"""The shared SQLite schema: the exact DDL text and its open-time validation.

This module owns the exact submitted schema text and the total validation that
runs whenever a database is opened. Filesystem authority is a separate concern
and lives in `binding.py`.

`sqlite_master` stores submitted DDL text verbatim, so no statement below may be
reflowed and none may be reordered: either silently changes the persisted
schema, and `_validate_schema` sorts before comparing, so it will not catch a
reordering. `ruff format` cannot reflow them because they are string literals
and it never rewrites string contents; `E501` is unenforced across the whole
backend rather than waived for this module, for the reasons `pyproject.toml`
records beside it.

Aggregate modules import from this module; this module imports from no aggregate
module, so the spine stays acyclic.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
import unicodedata
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.parse import quote

from mechanical_librarian.adapters.persistence.activity_documents import (
    ActivityDocuments,
)
from mechanical_librarian.adapters.persistence.binding import (
    _validate_connection_binding,
)
from mechanical_librarian.adapters.persistence.cover_consideration_documents import (
    expand_cover_consideration,
)
from mechanical_librarian.adapters.persistence.description_advice import (
    validate_description_history,
)
from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.adapters.persistence.field_effect_validation import (
    _validate_field_effect_state,
)
from mechanical_librarian.adapters.persistence.owner_settings import (
    _validate_owner_settings_content,
)
from mechanical_librarian.adapters.persistence.preparation_presentation import (
    validate_preparation_presentation,
)
from mechanical_librarian.adapters.persistence.review_sql import (
    _ATTENTION_FACT_SOURCE_SQL,
    _CURRENT_LIBRARY_FROM_SQL,
    _CURRENT_REVIEW_FROM_SQL,
)
from mechanical_librarian.application.cover_consideration import (
    cover_consideration_currentness_document,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.automatic_decision import AutomaticDecisionError
from mechanical_librarian.domain.cover_automatic_decision import (
    CoverAutomaticDecision,
)
from mechanical_librarian.domain.human_decision import (
    HumanDecision,
    HumanDecisionError,
    manual_cover_eligible,
    validate_cover_confirmation,
)
from mechanical_librarian.domain.metadata_policy import (
    MetadataPolicyError,
    MetadataPolicyProfile,
    ResolvedMetadataPolicy,
)
from mechanical_librarian.domain.metadata_report import (
    REPORT_SCHEMA,
    MetadataReport,
    ReportValueError,
)
from mechanical_librarian.domain.owner_settings import OwnerSettingsError
from mechanical_librarian.domain.proposal_set import (
    POLICY_VERSION,
    ProposalSet,
    evaluate_report,
)
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.shadow_decision import (
    DECISION_POLICY_SHA256,
    DECISION_POLICY_VERSION,
)

if sys.platform.startswith("linux"):
    import fcntl
else:
    fcntl = None

if TYPE_CHECKING:
    pass


_REPORTS_SQL = (
    "CREATE TABLE metadata_reports ("
    "report_id TEXT PRIMARY KEY NOT NULL CHECK (report_id GLOB 'report_[0-9a-f]*' AND length(report_id) = 71), "
    "connection_name TEXT NOT NULL, library_name TEXT NOT NULL, item_id TEXT NOT NULL, "
    "file_identity TEXT NOT NULL CHECK (length(file_identity) = 64 AND file_identity NOT GLOB '*[^0-9a-f]*'), "
    "content_digest TEXT NOT NULL CHECK (length(content_digest) = 64 AND content_digest NOT GLOB '*[^0-9a-f]*'), "
    "schema_version TEXT NOT NULL CHECK (schema_version = 'metadata-report-v67'), "
    "document_digest TEXT NOT NULL CHECK (length(document_digest) = 64 AND document_digest NOT GLOB '*[^0-9a-f]*'), "
    "document TEXT NOT NULL CHECK (typeof(document) = 'text' AND length(CAST(document AS BLOB)) BETWEEN 2 AND 2097152), "
    "UNIQUE (connection_name, library_name, item_id, file_identity, document_digest))"
)
# #1199: what the Library, Attention, search and count statements read from a
# report, derived once at insert instead of on every request. `title_key` is
# both the search value and the order key; `format` keeps the raw value the
# attention predicates compare. Authors are one row per distinct folded value,
# because a pattern may contain any separator and must never match across two.
_REPORT_BROWSE_FACTS_SQL = (
    "CREATE TABLE metadata_report_browse_facts ("
    "report_id TEXT PRIMARY KEY NOT NULL REFERENCES metadata_reports(report_id), "
    "title_key TEXT NOT NULL, format TEXT, format_key TEXT NOT NULL, "
    "provider_limited INTEGER NOT NULL CHECK(provider_limited IN (0,1)), "
    "provider_match_state TEXT NOT NULL CHECK(provider_match_state IN "
    "('matched','refused','unavailable','adapter_fault','no_match'))) WITHOUT ROWID"
)
_REPORT_BROWSE_AUTHORS_SQL = (
    "CREATE TABLE metadata_report_browse_authors ("
    "report_id TEXT NOT NULL REFERENCES metadata_reports(report_id), "
    "author_key TEXT NOT NULL, PRIMARY KEY(report_id,author_key)) WITHOUT ROWID"
)


# The lane statuses that leave a lookup without usable evidence for a reason a
# later attempt can change: what `provider_limited` flags, and what an
# unmatched work's `unavailable` state is.
_LIMITING_PROVIDER_STATUSES_SQL = (
    "'authentication','authorization','rate_limited','unavailable',"
    "'incomplete','malformed','oversized','candidate_limit_exceeded',"
    "'query_limit_exceeded'"
)


def _provider_lanes_sql(document: str) -> tuple[str, str]:
    # Provider lanes are current evidence, not a second workflow state.
    # Aggregate-only observations have no lanes, so the LEFT JOIN falls back to
    # their own status.
    lanes = (
        "FROM json_each(" + document + ",'$.providers') provider "
        "LEFT JOIN json_each(provider.value,'$.lanes') lane ON 1 WHERE "
    )
    status = "COALESCE(json_extract(lane.value,'$.status'),json_extract(provider.value,'$.status'))"
    return lanes, status


# A lane the adapter itself failed: its own diagnostics, or an aggregate-only
# observation's, name an adapter fault (#1498). No later attempt changes it,
# so it never limits a lookup.
_LANE_ADAPTER_FAULT_SQL = (
    "EXISTS(SELECT 1 FROM json_each(COALESCE(lane.value,provider.value),"
    "'$.diagnostics') diagnostic WHERE "
    "json_extract(diagnostic.value,'$.category')='adapter_fault')"
)


def _provider_limited_sql(document: str) -> str:
    lanes, status = _provider_lanes_sql(document)
    return (
        "EXISTS(SELECT 1 "
        + lanes
        + status
        + " IN ("
        + _LIMITING_PROVIDER_STATUSES_SQL
        + ") AND NOT "
        + _LANE_ADAPTER_FAULT_SQL
        + ")"
    )


def _report_provider_match_state_sql(document: str) -> str:
    """#1407: whether any provider returned candidates, and if none did, why.

    The owner's precedence: a lane whose candidates work-identity admission
    refused, then a lookup a later attempt can change, then a lane the adapter
    itself failed (#1498), then no match. `empty_or_refused` cannot say which,
    so it is no match.
    """
    lanes, status = _provider_lanes_sql(document)
    return (
        "CASE WHEN EXISTS(SELECT 1 "
        + lanes
        + status
        + "='candidates') THEN 'matched' WHEN EXISTS(SELECT 1 "
        + lanes
        + "json_extract(lane.value,'$.status')='no_match' AND "
        "(json_extract(lane.value,'$.dispatch.outcome')='refused' OR EXISTS("
        "SELECT 1 FROM json_each(lane.value,'$.diagnostics') diagnostic WHERE "
        "json_extract(diagnostic.value,'$.category')='work_identity_refused'))) "
        "THEN 'refused' WHEN "
        + _provider_limited_sql(document)
        + " THEN 'unavailable' WHEN EXISTS(SELECT 1 "
        + lanes
        + _LANE_ADAPTER_FAULT_SQL
        + ") THEN 'adapter_fault' ELSE 'no_match' END"
    )


def _report_browse_fact_values_sql(document: str) -> str:
    return (
        "ml_casefold(COALESCE(json_extract("
        + document
        + ",'$.target.fields[0].value'),'')),json_extract("
        + document
        + ",'$.file.format'),ml_casefold(COALESCE(json_extract("
        + document
        + ",'$.file.format'),'')),CASE WHEN "
        + _provider_limited_sql(document)
        + " THEN 1 ELSE 0 END,"
        + _report_provider_match_state_sql(document)
    )


def _report_browse_author_values_sql(source: str, document: str) -> str:
    return (
        "ml_casefold(COALESCE(author.value,'')) FROM "
        + source
        + "json_each("
        + document
        + ",'$.target.fields') author_field,"
        "json_each(author_field.value,'$.value') author "
        "WHERE json_extract(author_field.value,'$.destination')='authors'"
    )


_REPORT_BROWSE_FACTS_INSERT_SQL = (
    "CREATE TRIGGER metadata_reports_project_browse_facts AFTER INSERT ON metadata_reports "
    "BEGIN INSERT INTO metadata_report_browse_facts("
    "report_id,title_key,format,format_key,provider_limited,provider_match_state) "
    "SELECT NEW.report_id,"
    + _report_browse_fact_values_sql("NEW.document")
    + "; INSERT INTO metadata_report_browse_authors(report_id,author_key) "
    "SELECT DISTINCT NEW.report_id,"
    + _report_browse_author_values_sql("", "NEW.document")
    + "; END"
)


# The fold is `str.casefold`, whose mapping is the interpreter's Unicode
# database, so the facts are bound to the version they were folded under. Open
# refuses any other version: a clean rebuild re-derives them (#1199).
_BROWSE_FACT_IDENTITY_SQL = (
    "CREATE TABLE browse_fact_identity (singleton INTEGER PRIMARY KEY NOT NULL "
    "CHECK(singleton=1), unicode_version TEXT NOT NULL "
    "CHECK(length(unicode_version) BETWEEN 1 AND 32))"
)


def _review_casefold(value: object) -> str:
    if type(value) is not str:
        raise ValueError("review search value is invalid")
    return value.casefold()


def _register_casefold(connection: sqlite3.Connection) -> None:
    """Give a connection the one deterministic fold its facts were derived by.

    Every connection that inserts a report fires the browse-fact trigger, and
    every connection that validates facts recomputes them, through this
    function. One that lacks it fails with `no such function` and writes or
    accepts nothing.
    """
    connection.create_function("ml_casefold", 1, _review_casefold, deterministic=True)


_CURRENT_SQL = (
    "CREATE TABLE current_metadata_reports ("
    "connection_name TEXT NOT NULL, library_name TEXT NOT NULL, item_id TEXT NOT NULL, file_identity TEXT NOT NULL, "
    "report_id TEXT NOT NULL REFERENCES metadata_reports(report_id), "
    "PRIMARY KEY (connection_name, library_name, item_id, file_identity))"
)
_CURRENT_LINEAGE_SQL = (
    "CREATE TABLE current_metadata_report_lineages ("
    "connection_name TEXT NOT NULL, library_name TEXT NOT NULL, item_id TEXT NOT NULL, "
    "report_id TEXT NOT NULL REFERENCES metadata_reports(report_id), "
    "PRIMARY KEY (connection_name, library_name, item_id))"
)
_COVER_REFERENCES_SQL = (
    "CREATE TABLE metadata_report_cover_references ("
    "report_id TEXT NOT NULL REFERENCES metadata_reports(report_id), "
    "source_id TEXT NOT NULL, artifact_id TEXT, preview_digest TEXT, preview_variant TEXT, "
    "PRIMARY KEY (report_id, source_id), "
    "CHECK ((artifact_id IS NULL AND preview_digest IS NULL AND preview_variant IS NULL) OR "
    "(length(artifact_id) = 70 AND artifact_id GLOB 'cover_[0-9a-f]*' "
    "AND length(preview_digest) = 64 AND preview_digest NOT GLOB '*[^0-9a-f]*' AND preview_variant = 'preview-320x480-rgba-png-v1')))"
)
_COVER_ARTIFACT_INDEX_SQL = (
    "CREATE INDEX metadata_report_cover_references_artifact_id "
    "ON metadata_report_cover_references(artifact_id) WHERE artifact_id IS NOT NULL"
)
_COVER_PREVIEW_INDEX_SQL = (
    "CREATE INDEX metadata_report_cover_references_preview_digest "
    "ON metadata_report_cover_references(preview_digest) WHERE preview_digest IS NOT NULL"
)
_PROPOSAL_SETS_SQL = (
    "CREATE TABLE proposal_sets ("
    "proposal_set_id TEXT PRIMARY KEY NOT NULL CHECK (proposal_set_id GLOB 'proposal_set_[0-9a-f]*' AND length(proposal_set_id) = 77), "
    "report_id TEXT NOT NULL REFERENCES metadata_reports(report_id), "
    "policy_version TEXT NOT NULL CHECK (policy_version = '" + POLICY_VERSION + "'), "
    "document TEXT NOT NULL CHECK (typeof(document) = 'text' AND length(CAST(document AS BLOB)) BETWEEN 2 AND 2097152))"
)
# #1199: the policy-ask attention clause joins each candidate's proposal to its
# library's resolved policy; reading that id here spares a proposal document
# parse per candidate. A missing id stays NULL and joins nothing, as before.
_PROPOSAL_BROWSE_FACTS_SQL = (
    "CREATE TABLE proposal_set_browse_facts ("
    "proposal_set_id TEXT PRIMARY KEY NOT NULL REFERENCES proposal_sets(proposal_set_id), "
    "resolved_policy_id TEXT) WITHOUT ROWID"
)


def _proposal_browse_fact_value_sql(document: str) -> str:
    return "json_extract(" + document + ",'$.resolved_policy_id')"


_PROPOSAL_BROWSE_FACTS_INSERT_SQL = (
    "CREATE TRIGGER proposal_sets_project_browse_facts AFTER INSERT ON proposal_sets "
    "BEGIN INSERT INTO proposal_set_browse_facts(proposal_set_id,resolved_policy_id) "
    "VALUES(NEW.proposal_set_id,"
    + _proposal_browse_fact_value_sql("NEW.document")
    + "); END"
)
_METADATA_POLICY_DRAFTS_SQL = (
    "CREATE TABLE metadata_policy_drafts ("
    "policy_id TEXT PRIMARY KEY NOT NULL CHECK (policy_id GLOB 'policy_[0-9a-f]*' AND length(policy_id)=71), "
    "document TEXT NOT NULL CHECK (typeof(document)='text' AND length(CAST(document AS BLOB)) BETWEEN 2 AND 16384))"
)
_METADATA_POLICY_DRAFT_NO_UPDATE_SQL = (
    "CREATE TRIGGER metadata_policy_drafts_no_update BEFORE UPDATE ON metadata_policy_drafts "
    "BEGIN SELECT RAISE(ABORT,'immutable metadata policy draft'); END"
)
_METADATA_POLICY_DRAFT_NO_DELETE_SQL = (
    "CREATE TRIGGER metadata_policy_drafts_no_delete BEFORE DELETE ON metadata_policy_drafts "
    "BEGIN SELECT RAISE(ABORT,'immutable metadata policy draft'); END"
)
_METADATA_POLICY_STATE_SQL = (
    "CREATE TABLE metadata_policy_state ("
    "singleton INTEGER PRIMARY KEY NOT NULL CHECK(singleton=1), "
    "active_policy_id TEXT NOT NULL REFERENCES metadata_policy_drafts(policy_id), "
    "generation INTEGER NOT NULL CHECK(generation>=1), activated_at INTEGER NOT NULL CHECK(activated_at>=0), "
    "posture TEXT NOT NULL CHECK(posture IN ('review','preview','automatic')))"
)
_METADATA_POLICY_RESOLUTIONS_SQL = (
    "CREATE TABLE metadata_policy_resolutions ("
    "generation INTEGER NOT NULL, library_name TEXT NOT NULL, "
    "resolved_policy_id TEXT NOT NULL CHECK(resolved_policy_id GLOB 'resolved_policy_[0-9a-f]*' AND length(resolved_policy_id)=80), "
    "document TEXT NOT NULL CHECK(typeof(document)='text' AND length(CAST(document AS BLOB)) BETWEEN 2 AND 16384), "
    "PRIMARY KEY(generation,library_name))"
)
# Browse probes a proposal's resolution by library and resolved id per field;
# the primary key leads with generation, so history made that a scan (#1202).
_METADATA_POLICY_RESOLUTIONS_RESOLVED_INDEX_SQL = (
    "CREATE INDEX metadata_policy_resolutions_resolved "
    "ON metadata_policy_resolutions(library_name,resolved_policy_id,generation)"
)
_METADATA_POLICY_TRANSITIONS_SQL = (
    "CREATE TABLE metadata_policy_transitions ("
    "transition_id TEXT PRIMARY KEY NOT NULL CHECK(transition_id GLOB 'policy_transition_[0-9a-f]*' AND length(transition_id)=82), "
    "from_policy_id TEXT NOT NULL REFERENCES metadata_policy_drafts(policy_id), "
    "to_policy_id TEXT NOT NULL REFERENCES metadata_policy_drafts(policy_id), "
    "generation INTEGER NOT NULL UNIQUE CHECK(generation>=1), "
    "state TEXT NOT NULL CHECK(state IN ('processing','completed','completed_with_failures')), "
    "total INTEGER NOT NULL CHECK(total>=0), acknowledged INTEGER NOT NULL DEFAULT 0 CHECK(acknowledged IN (0,1)), "
    "created_at INTEGER NOT NULL CHECK(created_at>=0), updated_at INTEGER NOT NULL CHECK(updated_at>=created_at))"
)
_METADATA_POLICY_TRANSITION_ITEMS_SQL = (
    "CREATE TABLE metadata_policy_transition_items ("
    "transition_id TEXT NOT NULL REFERENCES metadata_policy_transitions(transition_id), "
    "connection_name TEXT NOT NULL, library_name TEXT NOT NULL, item_id TEXT NOT NULL, "
    "report_id TEXT NOT NULL REFERENCES metadata_reports(report_id), "
    "work_id TEXT REFERENCES review_preparation_work(work_id), "
    "state TEXT NOT NULL CHECK(state IN ('pending','complete','failed')), "
    "failure_code TEXT CHECK(failure_code IS NULL OR failure_code IN ('pipeline_unavailable','stale','failed')), "
    "updated_at INTEGER NOT NULL CHECK(updated_at>=0), "
    "PRIMARY KEY(transition_id,connection_name,library_name,item_id))"
)
_REVIEW_CASES_SQL = (
    "CREATE TABLE review_cases ("
    "review_case_id TEXT PRIMARY KEY NOT NULL CHECK (review_case_id GLOB 'review_case_[0-9a-f]*' AND length(review_case_id)=76), "
    "proposal_set_id TEXT NOT NULL REFERENCES proposal_sets(proposal_set_id), report_id TEXT NOT NULL REFERENCES metadata_reports(report_id), "
    "document TEXT NOT NULL CHECK (typeof(document)='text' AND length(CAST(document AS BLOB)) BETWEEN 2 AND 2097152))"
)
_REVIEW_CASE_FIELD_FACTS_SQL = (
    "CREATE TABLE review_case_field_facts ("
    "review_case_id TEXT NOT NULL REFERENCES review_cases(review_case_id), "
    "destination TEXT NOT NULL, unresolved_choice INTEGER NOT NULL "
    "CHECK(unresolved_choice IN (0,1)), "
    "manual_bibliographic_attention INTEGER NOT NULL "
    "CHECK(manual_bibliographic_attention IN (0,1)), "
    "has_owner_decision_actions INTEGER NOT NULL "
    "CHECK(has_owner_decision_actions IN (0,1)), "
    "PRIMARY KEY(review_case_id,destination)) WITHOUT ROWID"
)


def _review_case_field_fact_values_sql(case_document: str) -> str:
    return (
        "json_extract(result.value,'$.destination'),"
        "CASE WHEN json_extract(result.value,'$.outcome')='unresolved_choice' "
        "AND NOT EXISTS (SELECT 1 FROM json_each(result.value,'$.reasons') reason "
        "WHERE reason.value='work_alternative_immaterial') "
        "THEN 1 ELSE 0 END,CASE WHEN "
        "json_extract(result.value,'$.destination') IN ('title','subtitle','series') "
        "AND EXISTS (SELECT 1 FROM json_each("
        + case_document
        + ",'$.results') sibling "
        "WHERE json_extract(sibling.value,'$.destination') IN ('title','subtitle','series') "
        "AND json_array_length(json_extract(sibling.value,'$.reasons'))=1 "
        "AND json_extract(sibling.value,'$.reasons[0]')="
        "'no_safe_bibliographic_combination') THEN 1 ELSE 0 END,CASE WHEN ("
        "json_extract(result.value,'$.outcome') IN "
        "('propose_change','acceptable_choice','unresolved_choice') OR ("
        "json_extract(result.value,'$.destination') IN ('title','subtitle','series') "
        "AND EXISTS (SELECT 1 FROM json_each("
        + case_document
        + ",'$.results') sibling "
        "WHERE json_extract(sibling.value,'$.destination') IN ('title','subtitle','series') "
        "AND json_array_length(json_extract(sibling.value,'$.reasons'))=1 "
        "AND json_extract(sibling.value,'$.reasons[0]')='no_safe_bibliographic_combination'))) "
        "AND EXISTS (SELECT 1 FROM json_each(result.value,'$.alternatives') alternative "
        "WHERE json_extract(alternative.value,'$.assessment')<>'preserves_target') "
        "THEN 1 ELSE 0 END"
    )


_REVIEW_CASE_FIELD_FACT_VALUES_SQL = _review_case_field_fact_values_sql("NEW.document")
_REVIEW_CASE_FIELD_FACTS_INSERT_SQL = (
    "CREATE TRIGGER review_cases_project_field_facts AFTER INSERT ON review_cases "
    "BEGIN INSERT INTO review_case_field_facts("
    "review_case_id,destination,unresolved_choice,manual_bibliographic_attention,has_owner_decision_actions) "
    "SELECT NEW.review_case_id," + _REVIEW_CASE_FIELD_FACT_VALUES_SQL + " "
    "FROM json_each(NEW.document,'$.results') result; END"
)
_REVIEW_POINTERS_SQL = (
    "CREATE TABLE current_review_case_lineages ("
    "connection_name TEXT NOT NULL, library_name TEXT NOT NULL, item_id TEXT NOT NULL, "
    "proposal_set_id TEXT NOT NULL REFERENCES proposal_sets(proposal_set_id), report_id TEXT NOT NULL REFERENCES metadata_reports(report_id), review_case_id TEXT REFERENCES review_cases(review_case_id), "
    "transition_id TEXT NOT NULL REFERENCES review_case_transitions(transition_id), checkpoint INTEGER NOT NULL CHECK(checkpoint > 0), outcome TEXT NOT NULL CHECK(outcome IN ('admitted','superseded','no_case','retired','tombstoned')), "
    "CHECK ((outcome IN ('admitted','superseded') AND review_case_id IS NOT NULL) OR (outcome IN ('no_case','retired','tombstoned') AND review_case_id IS NULL)), "
    "PRIMARY KEY (connection_name,library_name,item_id))"
)
_CURRENT_REVIEW_PACKAGES_SQL = (
    "CREATE TABLE current_review_packages ("
    "connection_name TEXT NOT NULL, library_name TEXT NOT NULL, item_id TEXT NOT NULL, "
    "proposal_set_id TEXT REFERENCES proposal_sets(proposal_set_id), "
    "report_id TEXT NOT NULL REFERENCES metadata_reports(report_id), "
    "review_case_id TEXT REFERENCES review_cases(review_case_id), "
    "checkpoint INTEGER CHECK(checkpoint IS NULL OR checkpoint>=1), "
    "outcome TEXT NOT NULL CHECK(outcome IN ('report_only','evidence_only','admitted','superseded','no_case','retired')), "
    "CHECK ((outcome IN ('report_only','evidence_only') AND proposal_set_id IS NULL AND review_case_id IS NULL AND checkpoint IS NULL) "
    "OR (outcome IN ('admitted','superseded') AND proposal_set_id IS NOT NULL AND review_case_id IS NOT NULL AND checkpoint IS NOT NULL) "
    "OR (outcome IN ('no_case','retired') AND proposal_set_id IS NOT NULL AND review_case_id IS NULL AND checkpoint IS NOT NULL)), "
    "PRIMARY KEY(connection_name,library_name,item_id))"
)
# Library, Attention and detail reach a case's current lineage and package by
# its case id. Without these each visible row walked every lineage and package;
# newer SQLite skipped the walk only while field_effect_suspensions was empty
# (#1211).
_CURRENT_REVIEW_CASE_LINEAGES_CASE_INDEX_SQL = (
    "CREATE INDEX current_review_case_lineages_case "
    "ON current_review_case_lineages(review_case_id)"
)
_CURRENT_REVIEW_PACKAGES_CASE_INDEX_SQL = (
    "CREATE INDEX current_review_packages_case "
    "ON current_review_packages(review_case_id)"
)
_PROVIDER_RECOVERY_OUTBOX_SQL = (
    "CREATE TABLE provider_recovery_outbox ("
    "outbox_id TEXT PRIMARY KEY NOT NULL CHECK(outbox_id GLOB 'recovery_outbox_[0-9a-f]*' AND length(outbox_id)=80), "
    "connection_name TEXT NOT NULL, library_name TEXT NOT NULL, item_id TEXT NOT NULL, "
    "proposal_set_id TEXT REFERENCES proposal_sets(proposal_set_id), "
    "report_id TEXT NOT NULL REFERENCES metadata_reports(report_id), "
    "review_case_id TEXT REFERENCES review_cases(review_case_id), "
    "checkpoint INTEGER CHECK(checkpoint IS NULL OR checkpoint>=1), "
    "outcome TEXT NOT NULL CHECK(outcome IN ('report_only','admitted','superseded','no_case','retired')), "
    "created_at INTEGER NOT NULL CHECK(created_at>=0), "
    "delivered_at INTEGER CHECK(delivered_at IS NULL OR delivered_at>=created_at), "
    "CHECK ((outcome='report_only' AND proposal_set_id IS NULL AND review_case_id IS NULL AND checkpoint IS NULL) "
    "OR (outcome IN ('admitted','superseded') AND proposal_set_id IS NOT NULL AND review_case_id IS NOT NULL AND checkpoint IS NOT NULL) "
    "OR (outcome IN ('no_case','retired') AND proposal_set_id IS NOT NULL AND review_case_id IS NULL AND checkpoint IS NOT NULL)))"
)
_REVIEW_TRANSITIONS_SQL = (
    "CREATE TABLE review_case_transitions ("
    "transition_id TEXT PRIMARY KEY NOT NULL CHECK(transition_id GLOB 'review_transition_[0-9a-f]*' AND length(transition_id)=82), "
    "connection_name TEXT NOT NULL, library_name TEXT NOT NULL, item_id TEXT NOT NULL, checkpoint INTEGER NOT NULL CHECK(checkpoint > 0), actor TEXT NOT NULL CHECK(actor IN ('review-admission-v1','review-preparation-v1','policy-activation-v1')), "
    "from_review_case_id TEXT REFERENCES review_cases(review_case_id), to_review_case_id TEXT REFERENCES review_cases(review_case_id), proposal_set_id TEXT NOT NULL REFERENCES proposal_sets(proposal_set_id), report_id TEXT NOT NULL REFERENCES metadata_reports(report_id), kind TEXT NOT NULL CHECK(kind IN ('admitted','superseded','no_case','retired','tombstoned')), reason TEXT NOT NULL CHECK(reason IN ('actionable','nonactionable','absent','ineligible','policy_changed')), "
    "CHECK ((kind IN ('admitted','superseded') AND actor='review-admission-v1' AND reason='actionable' AND to_review_case_id IS NOT NULL) OR (kind IN ('no_case','retired') AND actor='review-admission-v1' AND reason='nonactionable' AND to_review_case_id IS NULL) OR (kind='tombstoned' AND actor='review-preparation-v1' AND reason='absent' AND from_review_case_id IS NOT NULL AND to_review_case_id IS NULL) OR (kind='retired' AND actor='review-preparation-v1' AND reason='ineligible' AND from_review_case_id IS NOT NULL AND to_review_case_id IS NULL) OR (kind='retired' AND actor='policy-activation-v1' AND reason='policy_changed' AND from_review_case_id IS NOT NULL AND to_review_case_id IS NULL)), "
    "CHECK ((kind IN ('admitted','no_case') AND from_review_case_id IS NULL) OR (kind IN ('superseded','retired','tombstoned') AND from_review_case_id IS NOT NULL)), "
    "UNIQUE(connection_name,library_name,item_id,checkpoint) )"
)
_REVIEW_SCANS_SQL = (
    "CREATE TABLE review_preparation_scans ("
    "scan_id TEXT PRIMARY KEY NOT NULL CHECK(scan_id GLOB 'review_scan_[0-9a-f]*' AND length(scan_id)=76), "
    "connection_name TEXT NOT NULL, library_name TEXT NOT NULL, library_id TEXT NOT NULL, "
    "producer_fingerprint TEXT NOT NULL CHECK(length(producer_fingerprint)=64 AND producer_fingerprint NOT GLOB '*[^0-9a-f]*'), "
    "state TEXT NOT NULL CHECK(state IN ('queued','discovering','processing','completed','completed_with_failures')), "
    "requested_at INTEGER NOT NULL CHECK(requested_at>=0), updated_at INTEGER NOT NULL CHECK(updated_at>=requested_at), "
    "catalogue_generation INTEGER CHECK(catalogue_generation IS NULL OR catalogue_generation>0), catalogue_fingerprint TEXT CHECK(catalogue_fingerprint IS NULL OR (length(catalogue_fingerprint)=64 AND catalogue_fingerprint NOT GLOB '*[^0-9a-f]*')), qualification_epoch INTEGER CHECK(qualification_epoch IS NULL OR qualification_epoch>=0), "
    "total INTEGER NOT NULL DEFAULT 0 CHECK(total>=0), refresh_count INTEGER NOT NULL DEFAULT 0 CHECK(refresh_count BETWEEN 0 AND 3), rescan_requested INTEGER NOT NULL DEFAULT 0 CHECK(rescan_requested IN (0,1)))"
)
_REVIEW_CONTROL_SQL = (
    "CREATE TABLE review_preparation_control ("
    "singleton INTEGER PRIMARY KEY NOT NULL CHECK(singleton=1), "
    "paused INTEGER NOT NULL CHECK(paused IN (0,1)), "
    "claim_sequence INTEGER NOT NULL CHECK(claim_sequence>=0), "
    "first_boot_acknowledged INTEGER NOT NULL CHECK(first_boot_acknowledged IN (0,1)), "
    "last_library TEXT CHECK(last_library IS NULL OR (typeof(last_library)='text' AND length(CAST(last_library AS BLOB)) BETWEEN 1 AND 64 AND last_library NOT GLOB '*[^a-z0-9_-]*' AND substr(last_library,1,1) GLOB '[a-z]')), circuit_failures INTEGER NOT NULL CHECK(circuit_failures BETWEEN 0 AND 3), "
    "circuit_open_until INTEGER NOT NULL CHECK(circuit_open_until>=0), updated_at INTEGER NOT NULL CHECK(updated_at>=0))"
)
_REVIEW_PREPARATION_PRESENTATION_WORK_SHAPE_SQL = "(row_kind='work' AND row_id='work:'||work_id AND scan_id IS NOT NULL AND connection_name IS NULL AND library_name IS NULL AND cycle_id IS NOT NULL AND pending_cycle_id IS NULL AND cycle_state IS NULL AND work_id IS NOT NULL AND ordinal IS NOT NULL AND trigger_ordinal IS NULL AND enrollment_ordinal IS NOT NULL AND display_name IS NOT NULL AND work_stage IS NOT NULL AND current_work_state IS NOT NULL AND ((work_outcome IS NULL AND completion_sequence IS NULL) OR (work_outcome IS NOT NULL AND completion_sequence IS NOT NULL)) AND ((recovery_high_water_stage IS NULL AND recovery_high_water_outcome IS NULL) OR recovery_high_water_stage IS NOT NULL) AND completion_count IS NULL AND trigger IS NULL AND accepted_count IS NULL AND accepted_at IS NULL AND settings_revision IS NULL AND first_accepted_at IS NULL AND last_accepted_at IS NULL AND state IS NULL AND started_at IS NULL AND completed_at IS NULL AND total IS NULL AND active_count IS NULL AND read IS NULL AND searches_finished IS NULL AND compared IS NULL AND checked IS NULL AND ready IS NULL AND failed IS NULL)"
_REVIEW_PREPARATION_PRESENTATION_SQL = (
    "CREATE TABLE review_preparation_presentation ("
    "row_id TEXT PRIMARY KEY NOT NULL, row_kind TEXT NOT NULL CHECK(row_kind IN ('run','trigger','enrollment','deferred_enrollment','work')), "
    "run_id TEXT NOT NULL CHECK(run_id GLOB 'preparation_run_[0-9a-f]*' AND length(run_id)=80), "
    "state TEXT CHECK(state IS NULL OR state IN ('starting','running','blocked','completed_clean','completed_with_failures','dismissed')), "
    "trigger TEXT CHECK(trigger IS NULL OR trigger IN ('initial_setup','manual_scan','library_added','title_recovery')), "
    "scan_id TEXT, connection_name TEXT, library_name TEXT, cycle_id TEXT CHECK(cycle_id IS NULL OR (substr(cycle_id,1,18)='preparation_cycle_' AND length(cycle_id)=82 AND substr(cycle_id,19) NOT GLOB '*[^0-9a-f]*')), pending_cycle_id TEXT CHECK(pending_cycle_id IS NULL OR (substr(pending_cycle_id,1,18)='preparation_cycle_' AND length(pending_cycle_id)=82 AND substr(pending_cycle_id,19) NOT GLOB '*[^0-9a-f]*')), cycle_state TEXT CHECK(cycle_state IS NULL OR cycle_state IN ('queued','discovering','processing','completed','completed_with_failures')), work_id TEXT, ordinal INTEGER CHECK(ordinal IS NULL OR ordinal>=0), trigger_ordinal INTEGER CHECK(trigger_ordinal IS NULL OR trigger_ordinal BETWEEN 0 AND 3), enrollment_ordinal INTEGER CHECK(enrollment_ordinal IS NULL OR enrollment_ordinal>=0), "
    "display_name TEXT CHECK(display_name IS NULL OR (typeof(display_name)='text' AND length(CAST(display_name AS BLOB)) BETWEEN 1 AND 2048)), "
    "work_stage TEXT CHECK(work_stage IS NULL OR work_stage IN ('extraction','provider','proposal','admission','projection')), "
    "current_work_state TEXT CHECK(current_work_state IS NULL OR current_work_state IN ('queued','delayed','running','complete','failed','stale')), work_outcome TEXT CHECK(work_outcome IS NULL OR work_outcome IN ('ready','failed')), recovery_high_water_stage TEXT CHECK(recovery_high_water_stage IS NULL OR recovery_high_water_stage IN ('extraction','provider','proposal','admission','projection')), recovery_high_water_outcome TEXT CHECK(recovery_high_water_outcome IS NULL OR recovery_high_water_outcome IN ('ready','failed')), completion_sequence INTEGER CHECK(completion_sequence IS NULL OR completion_sequence>0), completion_count INTEGER CHECK(completion_count IS NULL OR completion_count>=0), "
    "accepted_count INTEGER CHECK(accepted_count IS NULL OR accepted_count>0), accepted_at INTEGER CHECK(accepted_at IS NULL OR accepted_at>=0), settings_revision INTEGER CHECK(settings_revision IS NULL OR settings_revision>0), first_accepted_at INTEGER CHECK(first_accepted_at IS NULL OR first_accepted_at>=0), last_accepted_at INTEGER CHECK(last_accepted_at IS NULL OR last_accepted_at>=0), "
    "started_at INTEGER CHECK(started_at IS NULL OR started_at>=0), updated_at INTEGER NOT NULL CHECK(updated_at>=0), completed_at INTEGER CHECK(completed_at IS NULL OR completed_at>=0), "
    "total INTEGER CHECK(total IS NULL OR total>=0), active_count INTEGER CHECK(active_count IS NULL OR active_count>=0), read INTEGER CHECK(read IS NULL OR read>=0), searches_finished INTEGER CHECK(searches_finished IS NULL OR searches_finished>=0), compared INTEGER CHECK(compared IS NULL OR compared>=0), checked INTEGER CHECK(checked IS NULL OR checked>=0), ready INTEGER CHECK(ready IS NULL OR ready>=0), failed INTEGER CHECK(failed IS NULL OR failed>=0), "
    "CHECK(" + _REVIEW_PREPARATION_PRESENTATION_WORK_SHAPE_SQL + " OR "
    "(row_kind='run' AND row_id='run' AND state IS NOT NULL AND started_at IS NOT NULL AND updated_at>=started_at AND ((state IN ('completed_clean','completed_with_failures','dismissed') AND completed_at IS NOT NULL AND completed_at>=started_at) OR (state NOT IN ('completed_clean','completed_with_failures','dismissed') AND completed_at IS NULL)) AND total IS NOT NULL AND active_count IS NOT NULL AND read IS NOT NULL AND searches_finished IS NOT NULL AND compared IS NOT NULL AND checked IS NOT NULL AND ready IS NOT NULL AND failed IS NOT NULL AND completion_count IS NOT NULL AND trigger IS NULL AND scan_id IS NULL AND connection_name IS NULL AND library_name IS NULL AND cycle_id IS NULL AND pending_cycle_id IS NULL AND cycle_state IS NULL AND work_id IS NULL AND ordinal IS NULL AND trigger_ordinal IS NULL AND enrollment_ordinal IS NULL AND display_name IS NULL AND work_stage IS NULL AND current_work_state IS NULL AND work_outcome IS NULL AND recovery_high_water_stage IS NULL AND recovery_high_water_outcome IS NULL AND completion_sequence IS NULL AND accepted_count IS NULL AND accepted_at IS NULL AND settings_revision IS NULL AND first_accepted_at IS NULL AND last_accepted_at IS NULL) OR "
    "(row_kind='trigger' AND row_id='trigger:'||trigger AND trigger IS NOT NULL AND trigger_ordinal IS NOT NULL AND accepted_count IS NOT NULL AND first_accepted_at IS NOT NULL AND last_accepted_at IS NOT NULL AND last_accepted_at>=first_accepted_at AND state IS NULL AND scan_id IS NULL AND connection_name IS NULL AND library_name IS NULL AND cycle_id IS NULL AND pending_cycle_id IS NULL AND cycle_state IS NULL AND work_id IS NULL AND ordinal IS NULL AND enrollment_ordinal IS NULL AND display_name IS NULL AND work_stage IS NULL AND current_work_state IS NULL AND work_outcome IS NULL AND recovery_high_water_stage IS NULL AND recovery_high_water_outcome IS NULL AND completion_sequence IS NULL AND completion_count IS NULL AND accepted_at IS NULL AND settings_revision IS NULL AND started_at IS NULL AND completed_at IS NULL AND total IS NULL AND active_count IS NULL AND read IS NULL AND searches_finished IS NULL AND compared IS NULL AND checked IS NULL AND ready IS NULL AND failed IS NULL) OR "
    "(row_kind='enrollment' AND trigger IS NULL AND scan_id IS NOT NULL AND connection_name IS NOT NULL AND library_name IS NOT NULL AND (cycle_id IS NOT NULL OR pending_cycle_id IS NOT NULL) AND (cycle_id IS NULL OR pending_cycle_id IS NULL OR cycle_id<>pending_cycle_id) AND cycle_state IS NOT NULL AND (cycle_id IS NOT NULL OR cycle_state='queued') AND (cycle_state NOT IN ('completed','completed_with_failures') OR pending_cycle_id IS NULL) AND work_id IS NULL AND ordinal IS NOT NULL AND trigger_ordinal IS NULL AND enrollment_ordinal IS NULL AND display_name IS NULL AND work_stage IS NULL AND current_work_state IS NULL AND work_outcome IS NULL AND recovery_high_water_stage IS NULL AND recovery_high_water_outcome IS NULL AND completion_sequence IS NULL AND completion_count IS NULL AND accepted_count IS NULL AND accepted_at IS NULL AND settings_revision IS NULL AND first_accepted_at IS NULL AND last_accepted_at IS NULL AND state IS NULL AND started_at IS NULL AND completed_at IS NULL AND total IS NULL AND active_count IS NULL AND read IS NULL AND searches_finished IS NULL AND compared IS NULL AND checked IS NULL AND ready IS NULL AND failed IS NULL) OR "
    "(row_kind='deferred_enrollment' AND trigger IS NULL AND scan_id IS NULL AND connection_name IS NOT NULL AND library_name IS NOT NULL AND cycle_id IS NULL AND pending_cycle_id IS NULL AND cycle_state IS NULL AND work_id IS NULL AND ordinal IS NOT NULL AND trigger_ordinal IS NULL AND enrollment_ordinal IS NULL AND display_name IS NULL AND work_stage IS NULL AND current_work_state IS NULL AND work_outcome IS NULL AND recovery_high_water_stage IS NULL AND recovery_high_water_outcome IS NULL AND completion_sequence IS NULL AND completion_count IS NULL AND accepted_count IS NULL AND accepted_at IS NOT NULL AND settings_revision IS NOT NULL AND first_accepted_at IS NULL AND last_accepted_at IS NULL AND state IS NULL AND started_at IS NULL AND completed_at IS NULL AND total IS NULL AND active_count IS NULL AND read IS NULL AND searches_finished IS NULL AND compared IS NULL AND checked IS NULL AND ready IS NULL AND failed IS NULL)))"
)
_REVIEW_PREPARATION_PRESENTATION_RUN_INDEX_SQL = (
    "CREATE INDEX review_preparation_presentation_run "
    "ON review_preparation_presentation(run_id,row_kind,ordinal)"
)
_REVIEW_PREPARATION_PRESENTATION_ACTIVE_INDEX_SQL = (
    "CREATE INDEX review_preparation_presentation_active ON review_preparation_presentation("
    "run_id,current_work_state,enrollment_ordinal,ordinal,work_id) WHERE row_kind='work'"
)
_REVIEW_PREPARATION_PRESENTATION_RECENT_INDEX_SQL = (
    "CREATE UNIQUE INDEX review_preparation_presentation_recent ON review_preparation_presentation("
    "run_id,completion_sequence DESC) WHERE row_kind='work' AND work_outcome IS NOT NULL"
)
_REVIEW_PREPARATION_PRESENTATION_COMPLETION_INTEGRITY_INDEX_SQL = (
    "CREATE INDEX review_preparation_presentation_completion_integrity ON review_preparation_presentation("
    "run_id,completion_sequence) WHERE row_kind='work' AND completion_sequence IS NOT NULL"
)
_REVIEW_PREPARATION_PRESENTATION_THROUGHPUT_INDEX_SQL = (
    "CREATE INDEX review_preparation_presentation_throughput ON review_preparation_presentation("
    "run_id,updated_at) WHERE row_kind='work' AND work_outcome IS NOT NULL"
)
_REVIEW_CONNECTION_CONDITIONS_SQL = (
    "CREATE TABLE review_preparation_connection_conditions ("
    "connection_name TEXT PRIMARY KEY NOT NULL CHECK(typeof(connection_name)='text' AND length(CAST(connection_name AS BLOB)) BETWEEN 1 AND 64 AND connection_name NOT GLOB '*[^a-z0-9_-]*' AND substr(connection_name,1,1) GLOB '[a-z]'), "
    "code TEXT NOT NULL CHECK(code IN ('tls','authentication','authorization','unavailable')), "
    "state TEXT NOT NULL CHECK(state IN ('paused','checking','recovered')), "
    "epoch INTEGER NOT NULL CHECK(epoch>=1), affected INTEGER NOT NULL CHECK(affected>=0), "
    "updated_at INTEGER NOT NULL CHECK(updated_at>=0))"
)
_REVIEW_WORK_SQL = (
    "CREATE TABLE review_preparation_work ("
    "work_id TEXT PRIMARY KEY NOT NULL CHECK(work_id GLOB 'review_work_[0-9a-f]*' AND length(work_id)=76), "
    "scan_id TEXT NOT NULL REFERENCES review_preparation_scans(scan_id) ON DELETE CASCADE, ordinal INTEGER NOT NULL CHECK(ordinal>=0), "
    "connection_name TEXT NOT NULL, library_name TEXT NOT NULL, library_id TEXT NOT NULL, item_id TEXT NOT NULL, "
    "file_identity TEXT NOT NULL CHECK(length(file_identity)=64 AND file_identity NOT GLOB '*[^0-9a-f]*'), source_revision INTEGER NOT NULL CHECK(source_revision>=0), source_fingerprint TEXT NOT NULL CHECK(length(source_fingerprint)=64 AND source_fingerprint NOT GLOB '*[^0-9a-f]*'), "
    "producer_fingerprint TEXT NOT NULL CHECK(length(producer_fingerprint)=64 AND producer_fingerprint NOT GLOB '*[^0-9a-f]*'), "
    "catalogue_generation INTEGER NOT NULL CHECK(catalogue_generation>0), catalogue_fingerprint TEXT NOT NULL CHECK(length(catalogue_fingerprint)=64 AND catalogue_fingerprint NOT GLOB '*[^0-9a-f]*'), "
    "stage TEXT NOT NULL CHECK(stage IN ('extraction','provider','proposal','admission','projection','complete')), "
    "state TEXT NOT NULL CHECK(state IN ('queued','delayed','running','complete','failed','stale')), attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt BETWEEN 0 AND 3), recovery_attempt INTEGER NOT NULL DEFAULT 0 CHECK(recovery_attempt BETWEEN 0 AND 3), retry_not_before INTEGER NOT NULL DEFAULT 0 CHECK(retry_not_before>=0), "
    "report_id TEXT REFERENCES metadata_reports(report_id), recovery_source_report_id TEXT REFERENCES metadata_reports(report_id), proposal_set_id TEXT REFERENCES proposal_sets(proposal_set_id), review_case_id TEXT REFERENCES review_cases(review_case_id), "
    "outcome TEXT CHECK(outcome IS NULL OR outcome IN ('case','no_case','evidence_only')), "
    "failure_code TEXT CHECK(failure_code IS NULL OR failure_code IN ('provider','configuration','authentication','stale','transient','projection','failed','currentness','persistence','unsupported_media','unreadable_media','parser_refused','multi_file_budget_exceeded','missing_required_input','malformed_retained_data','internal_failure','unknown')), "
    "failure_root TEXT CHECK(failure_root IS NULL OR (typeof(failure_root)='text' AND length(CAST(failure_root AS BLOB)) BETWEEN 1 AND 64 AND ((failure_code='authentication' AND failure_root NOT GLOB '*[^a-z0-9_-]*' AND substr(failure_root,1,1) GLOB '[a-z]') OR (failure_code IN ('unsupported_media','unreadable_media','parser_refused','multi_file_budget_exceeded') AND (failure_root GLOB 'track_[1-9]' OR failure_root GLOB 'track_[1-9][0-9]' OR failure_root GLOB 'track_[1-9][0-9][0-9]'))))), "
    "presentation_recovery_cycle_id TEXT CHECK(presentation_recovery_cycle_id IS NULL OR (presentation_recovery_cycle_id GLOB 'preparation_cycle_[0-9a-f]*' AND length(presentation_recovery_cycle_id)=82)), "
    "presentation_recovery_stage TEXT CHECK(presentation_recovery_stage IS NULL OR presentation_recovery_stage IN ('extraction','provider','proposal','admission','projection')), "
    "presentation_recovery_outcome TEXT CHECK(presentation_recovery_outcome IS NULL OR presentation_recovery_outcome IN ('ready','failed')), "
    # #1451: when an audiobook whose report admitted no recording is searched
    # again; NULL for every other item.
    "research_not_before INTEGER CHECK(research_not_before IS NULL OR research_not_before>=0), "
    "updated_at INTEGER NOT NULL CHECK(updated_at>=0), "
    "CHECK((presentation_recovery_cycle_id IS NULL AND presentation_recovery_stage IS NULL AND presentation_recovery_outcome IS NULL) OR (presentation_recovery_cycle_id IS NOT NULL AND presentation_recovery_stage IS NOT NULL)), "
    "CHECK((state='complete')=(outcome IS NOT NULL)), "
    "CHECK(outcome IS NULL OR (outcome='case' AND review_case_id IS NOT NULL) OR (outcome='no_case' AND proposal_set_id IS NOT NULL AND review_case_id IS NULL) OR (outcome='evidence_only' AND report_id IS NOT NULL AND proposal_set_id IS NULL AND review_case_id IS NULL)), "
    "UNIQUE(scan_id,item_id))"
)
_REVIEW_ABSENCES_SQL = (
    "CREATE TABLE review_preparation_absences ("
    "scan_id TEXT NOT NULL REFERENCES review_preparation_scans(scan_id) ON DELETE CASCADE, "
    "item_id TEXT NOT NULL, proposal_set_id TEXT NOT NULL REFERENCES proposal_sets(proposal_set_id), "
    "report_id TEXT NOT NULL REFERENCES metadata_reports(report_id), review_case_id TEXT NOT NULL REFERENCES review_cases(review_case_id), "
    "checkpoint INTEGER NOT NULL CHECK(checkpoint>0), kind TEXT NOT NULL CHECK(kind IN ('tombstoned','retired')), "
    "reason TEXT NOT NULL CHECK((kind='tombstoned' AND reason='absent') OR (kind='retired' AND reason='ineligible')), "
    "PRIMARY KEY(scan_id,item_id))"
)
_REVIEW_WORK_AGED_CLAIM_INDEX_SQL = (
    "CREATE INDEX review_preparation_work_aged_claim "
    "ON review_preparation_work(state,library_name,ordinal)"
)
_REVIEW_WORK_DUE_PROMOTION_INDEX_SQL = (
    "CREATE INDEX review_preparation_work_due_promotion "
    "ON review_preparation_work(state,retry_not_before)"
)
_REVIEW_WORK_RECENT_CLAIM_INDEX_SQL = (
    "CREATE INDEX review_preparation_work_recent_claim "
    "ON review_preparation_work(state,library_name,updated_at DESC,ordinal)"
)
_REVIEW_WORK_LINEAGE_HISTORY_INDEX_SQL = (
    "CREATE INDEX review_preparation_work_lineage_history "
    "ON review_preparation_work(connection_name,library_name,item_id,updated_at DESC,work_id DESC)"
)
_HUMAN_DECISIONS_SQL = (
    "CREATE TABLE human_decisions ("
    "ordinal INTEGER PRIMARY KEY NOT NULL, "
    "decision_id TEXT NOT NULL UNIQUE CHECK(decision_id GLOB 'human_decision_[0-9a-f]*' AND length(decision_id)=79), "
    "review_case_id TEXT NOT NULL REFERENCES review_cases(review_case_id), "
    "proposal_id TEXT NOT NULL CHECK(proposal_id GLOB 'review_proposal_[0-9a-f]*' AND length(proposal_id)=80), "
    "target_fingerprint TEXT NOT NULL CHECK(length(target_fingerprint)=64 AND target_fingerprint NOT GLOB '*[^0-9a-f]*'), "
    "destination TEXT NOT NULL CHECK(length(destination) BETWEEN 1 AND 64), "
    "actor TEXT NOT NULL CHECK(actor='owner'), "
    "outcome TEXT NOT NULL CHECK(outcome IN ('accept','keep_current','reject','owner_entered','withdraw')), "
    "selected_value_document TEXT NOT NULL CHECK(typeof(selected_value_document)='text' AND length(CAST(selected_value_document AS BLOB)) BETWEEN 4 AND 32768), "
    "idempotency_digest TEXT NOT NULL UNIQUE CHECK(length(idempotency_digest)=64 AND idempotency_digest NOT GLOB '*[^0-9a-f]*'), "
    "request_digest TEXT NOT NULL CHECK(length(request_digest)=64 AND request_digest NOT GLOB '*[^0-9a-f]*'), "
    "occurred_at INTEGER NOT NULL CHECK(occurred_at>=0), "
    "document TEXT NOT NULL CHECK(typeof(document)='text' AND length(CAST(document AS BLOB)) BETWEEN 2 AND 65536))"
)
# Browse, counts and detail ask for a case's decisions per destination, and the
# latest by ordinal (the rowid, so the index carries it); without this every
# candidate scanned the whole decision history (#1202).
_HUMAN_DECISIONS_CASE_DESTINATION_INDEX_SQL = (
    "CREATE INDEX human_decisions_case_destination "
    "ON human_decisions(review_case_id,destination)"
)
# A case admitted again after the work left it starts a new episode (#1358).
# Its owner decisions up to `decision_floor` and the writes planned up to
# `effect_floor`, the latest ordinal and field effect rowid when it was
# admitted again, are history. Only re-admission writes a row.
_REVIEW_CASE_EPISODES_SQL = (
    "CREATE TABLE review_case_episodes ("
    "review_case_id TEXT PRIMARY KEY NOT NULL REFERENCES review_cases(review_case_id), "
    "decision_floor INTEGER NOT NULL CHECK(typeof(decision_floor)='integer' AND decision_floor>=0), "
    "effect_floor INTEGER NOT NULL CHECK(typeof(effect_floor)='integer' AND effect_floor>=0)) "
    "WITHOUT ROWID"
)
_HUMAN_DECISION_EVENTS_SQL = (
    "CREATE TABLE human_decision_events ("
    "event_id TEXT PRIMARY KEY NOT NULL CHECK(event_id GLOB 'human_decision_event_[0-9a-f]*' AND length(event_id)=85), "
    "decision_id TEXT NOT NULL UNIQUE REFERENCES human_decisions(decision_id), "
    "review_case_id TEXT NOT NULL REFERENCES review_cases(review_case_id), "
    "destination TEXT NOT NULL CHECK(length(destination) BETWEEN 1 AND 64), "
    "actor TEXT NOT NULL CHECK(actor='owner'), kind TEXT NOT NULL CHECK(kind='human_decision_recorded'), "
    "occurred_at INTEGER NOT NULL CHECK(occurred_at>=0), "
    "document TEXT NOT NULL CHECK(typeof(document)='text' AND length(CAST(document AS BLOB)) BETWEEN 2 AND 65536))"
)
_COVER_CONSIDERATIONS_SQL = (
    "CREATE TABLE cover_considerations ("
    "consideration_id TEXT PRIMARY KEY NOT NULL CHECK(consideration_id GLOB 'cover_consideration_[0-9a-f]*' AND length(consideration_id)=84), "
    "review_case_id TEXT NOT NULL REFERENCES review_cases(review_case_id), "
    "report_id TEXT NOT NULL REFERENCES metadata_reports(report_id), "
    "replay_digest TEXT NOT NULL CHECK(length(replay_digest)=64 AND replay_digest NOT GLOB '*[^0-9a-f]*'), "
    "document_digest TEXT NOT NULL CHECK(length(document_digest)=64 AND document_digest NOT GLOB '*[^0-9a-f]*'), "
    "supersedes_id TEXT REFERENCES cover_considerations(consideration_id), "
    "occurred_at INTEGER NOT NULL CHECK(occurred_at>=0), "
    "currentness_document TEXT NOT NULL CHECK(typeof(currentness_document)='text' AND length(CAST(currentness_document AS BLOB)) BETWEEN 4 AND 4096), "
    "document TEXT NOT NULL CHECK(typeof(document)='text' AND length(CAST(document AS BLOB)) BETWEEN 2 AND 12582912 AND json_valid(document) AND json_type(document,'$.report_document') IS NULL AND json_type(document,'$.report_document_digest') IS 'text' AND length(json_extract(document,'$.report_document_digest'))=64 AND json_extract(document,'$.report_document_digest') NOT GLOB '*[^0-9a-f]*'))"
)
_CURRENT_COVER_CONSIDERATIONS_SQL = (
    "CREATE TABLE current_cover_considerations ("
    "review_case_id TEXT PRIMARY KEY NOT NULL REFERENCES review_cases(review_case_id), "
    "consideration_id TEXT NOT NULL UNIQUE REFERENCES cover_considerations(consideration_id))"
)
_COVER_SELECTION_AUTHORITIES_SQL = (
    "CREATE TABLE cover_selection_authorities ("
    "selection_id TEXT PRIMARY KEY NOT NULL CHECK(typeof(selection_id)='text' AND selection_id GLOB 'cover_selection_[0-9a-f]*' AND length(selection_id)=80), "
    "connection_name TEXT NOT NULL CHECK(typeof(connection_name)='text' AND length(CAST(connection_name AS BLOB)) BETWEEN 1 AND 128), "
    "library_name TEXT NOT NULL CHECK(typeof(library_name)='text' AND length(CAST(library_name AS BLOB)) BETWEEN 1 AND 128), "
    "item_id TEXT NOT NULL CHECK(typeof(item_id)='text' AND length(CAST(item_id AS BLOB)) BETWEEN 1 AND 512), "
    "kind TEXT NOT NULL CHECK(kind IN ('automatic','owner')), "
    "decision_id TEXT NOT NULL UNIQUE CHECK((kind='automatic' AND decision_id GLOB 'automatic_decision_[0-9a-f]*' AND length(decision_id)=83) OR (kind='owner' AND decision_id GLOB 'human_decision_[0-9a-f]*' AND length(decision_id)=79)), "
    "consideration_id TEXT NOT NULL REFERENCES cover_considerations(consideration_id), "
    "supersedes_selection_id TEXT REFERENCES cover_selection_authorities(selection_id), "
    "occurred_at INTEGER NOT NULL CHECK(typeof(occurred_at)='integer' AND occurred_at>=0), "
    "document TEXT NOT NULL CHECK(typeof(document)='text' AND length(CAST(document AS BLOB)) BETWEEN 2 AND 131072), "
    "CHECK(kind<>'owner' OR (CASE WHEN json_valid(document) THEN json_extract(document,'$.schema_version')='cover-selection-authority-v2' ELSE 0 END) IS 1), "
    "CHECK(kind<>'automatic' OR (CASE WHEN json_valid(document) "
    "AND json_type(document,'$.decision_document')='text' "
    "THEN CASE WHEN json_valid(json_extract(document,'$.decision_document')) "
    "THEN json_extract(json_extract(document,'$.decision_document'),'$.schema_version')='cover-automatic-decision-v3' "
    "ELSE 0 END ELSE 0 END) IS 1), "
    "CHECK(supersedes_selection_id IS NULL OR supersedes_selection_id<>selection_id))"
)
_CURRENT_COVER_SELECTION_AUTHORITIES_SQL = (
    "CREATE TABLE current_cover_selection_authorities ("
    "connection_name TEXT NOT NULL CHECK(typeof(connection_name)='text' AND length(CAST(connection_name AS BLOB)) BETWEEN 1 AND 128), "
    "library_name TEXT NOT NULL CHECK(typeof(library_name)='text' AND length(CAST(library_name AS BLOB)) BETWEEN 1 AND 128), "
    "item_id TEXT NOT NULL CHECK(typeof(item_id)='text' AND length(CAST(item_id AS BLOB)) BETWEEN 1 AND 512), "
    "selection_id TEXT NOT NULL UNIQUE REFERENCES cover_selection_authorities(selection_id), "
    "PRIMARY KEY(connection_name,library_name,item_id))"
)
_COVER_SELECTION_AUTHORITY_NO_UPDATE_SQL = "CREATE TRIGGER cover_selection_authorities_no_update BEFORE UPDATE ON cover_selection_authorities BEGIN SELECT RAISE(ABORT, 'cover selection authorities are append-only'); END"
_COVER_SELECTION_AUTHORITY_NO_DELETE_SQL = "CREATE TRIGGER cover_selection_authorities_no_delete BEFORE DELETE ON cover_selection_authorities BEGIN SELECT RAISE(ABORT, 'cover selection authorities are append-only'); END"
_CURRENT_COVER_SELECTION_AUTHORITY_INSERT_SQL = "CREATE TRIGGER current_cover_selection_authorities_validate_insert BEFORE INSERT ON current_cover_selection_authorities WHEN NOT EXISTS (SELECT 1 FROM cover_selection_authorities selection WHERE selection.selection_id=NEW.selection_id AND selection.connection_name=NEW.connection_name AND selection.library_name=NEW.library_name AND selection.item_id=NEW.item_id) BEGIN SELECT RAISE(ABORT, 'cover selection authority target disagrees'); END"
_CURRENT_COVER_SELECTION_AUTHORITY_UPDATE_SQL = "CREATE TRIGGER current_cover_selection_authorities_validate_update BEFORE UPDATE ON current_cover_selection_authorities WHEN NOT EXISTS (SELECT 1 FROM cover_selection_authorities selection WHERE selection.selection_id=NEW.selection_id AND selection.connection_name=NEW.connection_name AND selection.library_name=NEW.library_name AND selection.item_id=NEW.item_id) BEGIN SELECT RAISE(ABORT, 'cover selection authority target disagrees'); END"
_COVER_CONSIDERATION_EVENTS_SQL = (
    "CREATE TABLE cover_consideration_events ("
    "event_id TEXT PRIMARY KEY NOT NULL CHECK(event_id GLOB 'cover_consideration_event_[0-9a-f]*' AND length(event_id)=90), "
    "consideration_id TEXT NOT NULL REFERENCES cover_considerations(consideration_id), "
    "review_case_id TEXT NOT NULL REFERENCES review_cases(review_case_id), "
    "kind TEXT NOT NULL CHECK(kind IN ('automatic_prepared','owner_retry_prepared','superseded','selected','kept_incumbent','no_action','stale')), "
    "decision_id TEXT, occurred_at INTEGER NOT NULL CHECK(occurred_at>=0), "
    "document TEXT NOT NULL CHECK(typeof(document)='text' AND length(CAST(document AS BLOB)) BETWEEN 2 AND 4096))"
)
_COVER_CONSIDERATION_NO_UPDATE_SQL = "CREATE TRIGGER cover_considerations_no_update BEFORE UPDATE ON cover_considerations BEGIN SELECT RAISE(ABORT, 'cover considerations are append-only'); END"
_COVER_CONSIDERATION_NO_DELETE_SQL = "CREATE TRIGGER cover_considerations_no_delete BEFORE DELETE ON cover_considerations BEGIN SELECT RAISE(ABORT, 'cover considerations are append-only'); END"
_COVER_CONSIDERATION_EVENT_NO_UPDATE_SQL = "CREATE TRIGGER cover_consideration_events_no_update BEFORE UPDATE ON cover_consideration_events BEGIN SELECT RAISE(ABORT, 'cover consideration events are append-only'); END"
_COVER_CONSIDERATION_EVENT_NO_DELETE_SQL = "CREATE TRIGGER cover_consideration_events_no_delete BEFORE DELETE ON cover_consideration_events BEGIN SELECT RAISE(ABORT, 'cover consideration events are append-only'); END"
_AUTOMATIC_DECISIONS_SQL = (
    "CREATE TABLE automatic_decisions ("
    "decision_id TEXT PRIMARY KEY NOT NULL CHECK(decision_id GLOB 'automatic_decision_[0-9a-f]*' AND length(decision_id)=83), "
    "review_case_id TEXT NOT NULL REFERENCES review_cases(review_case_id), "
    "destination TEXT NOT NULL CHECK(length(destination) BETWEEN 1 AND 64), "
    "idempotency_digest TEXT NOT NULL UNIQUE CHECK(length(idempotency_digest)=64 AND idempotency_digest NOT GLOB '*[^0-9a-f]*'), "
    "occurred_at INTEGER NOT NULL CHECK(occurred_at>=0), "
    "document TEXT NOT NULL CHECK(typeof(document)='text' AND length(CAST(document AS BLOB)) BETWEEN 2 AND 65536), "
    "UNIQUE(review_case_id,destination))"
)
_AUTOMATIC_SAFETY_SQL = (
    "CREATE TABLE automatic_safety_state ("
    "singleton INTEGER PRIMARY KEY NOT NULL CHECK(singleton=1), "
    "paused INTEGER NOT NULL CHECK(paused IN (0,1)), "
    "global_suspension TEXT, category_suspensions_document TEXT NOT NULL, "
    "updated_at INTEGER NOT NULL CHECK(updated_at>=0))"
)
_FIELD_EFFECT_CONTROL_SQL = (
    "CREATE TABLE field_effect_control (singleton INTEGER PRIMARY KEY NOT NULL CHECK(singleton=1), "
    "paused INTEGER NOT NULL CHECK(paused IN (0,1)), updated_at INTEGER NOT NULL CHECK(updated_at>=0))"
)
_FIELD_EFFECT_SUSPENSIONS_SQL = (
    "CREATE TABLE field_effect_suspensions ("
    "scope TEXT NOT NULL CHECK(scope IN ('connection','destination')), "
    "connection_name TEXT NOT NULL CHECK(length(connection_name) BETWEEN 1 AND 128), "
    "destination TEXT NOT NULL CHECK(length(destination)<=64 AND "
    "((scope='connection' AND destination='') OR (scope='destination' AND length(destination)>=1))), "
    "reason TEXT NOT NULL CHECK(reason IN ('connection_unavailable','insufficient_permission',"
    "'field_unavailable','unexpected_effect_behavior')), "
    "updated_at INTEGER NOT NULL CHECK(updated_at>=0), "
    "document TEXT NOT NULL CHECK(typeof(document)='text' AND "
    "length(CAST(document AS BLOB)) BETWEEN 2 AND 4096), "
    "PRIMARY KEY(scope,connection_name,destination))"
)
_FIELD_EFFECT_AUTHORITIES_SQL = (
    "CREATE TABLE field_effect_authorities ("
    "decision_id TEXT PRIMARY KEY NOT NULL, current INTEGER NOT NULL CHECK(current IN (0,1)), "
    "updated_at INTEGER NOT NULL CHECK(updated_at>=0), document TEXT NOT NULL "
    "CHECK(typeof(document)='text' AND length(CAST(document AS BLOB)) BETWEEN 2 AND 4096))"
)
_FIELD_EFFECT_CONSENTS_SQL = (
    "CREATE TABLE field_effect_consents ("
    "consent_id TEXT PRIMARY KEY NOT NULL CHECK(consent_id GLOB 'effect_consent_[0-9a-f]*' AND length(consent_id)=79), "
    "current INTEGER NOT NULL CHECK(current IN (0,1)), updated_at INTEGER NOT NULL CHECK(updated_at>=0), "
    "document TEXT NOT NULL CHECK(typeof(document)='text' AND length(CAST(document AS BLOB)) BETWEEN 2 AND 4096))"
)
_FIELD_EFFECT_MANUAL_HOLDS_SQL = (
    "CREATE TABLE field_effect_manual_holds ("
    "review_case_id TEXT NOT NULL REFERENCES review_cases(review_case_id), "
    "destination TEXT NOT NULL CHECK(length(destination) BETWEEN 1 AND 64), "
    "active INTEGER NOT NULL CHECK(active IN (0,1)), "
    "updated_at INTEGER NOT NULL CHECK(updated_at>=0), "
    "document TEXT NOT NULL CHECK(typeof(document)='text' AND "
    "length(CAST(document AS BLOB)) BETWEEN 2 AND 4096), "
    "PRIMARY KEY(review_case_id,destination))"
)
_FIELD_EFFECTS_SQL = (
    "CREATE TABLE field_effects ("
    "plan_id TEXT PRIMARY KEY NOT NULL CHECK(plan_id GLOB 'effect_plan_[0-9a-f]*' AND length(plan_id)=76), "
    "decision_id TEXT NOT NULL, connection_name TEXT NOT NULL, library_name TEXT NOT NULL, "
    "work_id TEXT NOT NULL, destination TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN "
    "('planned','claimed','applying','reconciling','retryable','verified','stale','superseded','abandoned')), "
    "attempt INTEGER NOT NULL CHECK(attempt BETWEEN 0 AND 3), updated_at INTEGER NOT NULL CHECK(updated_at>=0), "
    "document TEXT NOT NULL CHECK(typeof(document)='text' AND length(CAST(document AS BLOB)) BETWEEN 2 AND 131072))"
)
# Pending, revocation, replacement, history and override reads ask which effects
# name a decision as their own authority or as their subtitle companion's;
# without these each such read scanned every effect (#1204). The companion arm
# compares this CAST, which a statement must use exactly for the planner to
# choose the index. Against a TEXT-affinity column it is the conversion a bare
# json_extract already received; against a bound parameter (revocation,
# replacement) it differs only for the text of a number a companion holds (a
# boolean extracts as 1 or 0), which no decision id can be. Every authority id
# carries the human_decision_ or automatic_decision_ prefix: the human,
# automatic and description decision domain types derive and validate it, and
# the human_decisions, automatic_decisions and cover_selection_authorities GLOB
# checks enforce it.
_FIELD_EFFECT_COMPANION_DECISION_SQL = "CAST(json_extract(document,'$.plan.subtitle_companion.authority.decision_id') AS TEXT)"
_FIELD_EFFECTS_DECISION_INDEX_SQL = (
    "CREATE INDEX field_effects_decision ON field_effects(decision_id)"
)
_FIELD_EFFECTS_COMPANION_DECISION_INDEX_SQL = (
    "CREATE INDEX field_effects_companion_decision ON field_effects("
    + _FIELD_EFFECT_COMPANION_DECISION_SQL
    + ") WHERE "
    + _FIELD_EFFECT_COMPANION_DECISION_SQL
    + " IS NOT NULL"
)
# The scheduler's claim, the Activity page's waiting effect and description
# advice's target check read only live or one target's effects, but scanned
# every effect ever recorded. A partial index serves only a statement whose
# state list is written exactly as its WHERE clause's, so each statement uses
# the list below; a plain state index would not do, as without statistics the
# planner prefers it for the verified authority lookups and walks every
# verified effect (#1204).
_FIELD_EFFECT_READY_STATES_SQL = "('planned','retryable')"
_FIELD_EFFECT_WAITING_STATES_SQL = (
    "('planned','claimed','applying','reconciling','retryable')"
)
_FIELD_EFFECTS_READY_INDEX_SQL = (
    "CREATE INDEX field_effects_ready ON field_effects(updated_at,plan_id) "
    "WHERE state IN " + _FIELD_EFFECT_READY_STATES_SQL
)
_FIELD_EFFECTS_WAITING_INDEX_SQL = (
    "CREATE INDEX field_effects_waiting ON field_effects(updated_at,plan_id) "
    "WHERE state IN " + _FIELD_EFFECT_WAITING_STATES_SQL
)
_FIELD_EFFECTS_TARGET_INDEX_SQL = (
    "CREATE INDEX field_effects_target ON field_effects("
    "connection_name,library_name,work_id,destination)"
)
_FIELD_EFFECT_NO_CHANGES_SQL = (
    "CREATE TABLE field_effect_no_changes ("
    "decision_id TEXT PRIMARY KEY NOT NULL, "
    "review_case_id TEXT NOT NULL REFERENCES review_cases(review_case_id), report_id TEXT NOT NULL REFERENCES metadata_reports(report_id), "
    "connection_name TEXT NOT NULL, library_name TEXT NOT NULL, work_id TEXT NOT NULL, destination TEXT NOT NULL, "
    "target_fingerprint TEXT NOT NULL, target_version TEXT NOT NULL, origin TEXT NOT NULL CHECK(origin IN ('human','automatic_policy')), "
    "occurred_at INTEGER NOT NULL CHECK(occurred_at>=0), event_id TEXT NOT NULL UNIQUE) WITHOUT ROWID"
)
_FIELD_EFFECT_ACTIONS_SQL = (
    "CREATE TABLE field_effect_actions ("
    "action_id TEXT PRIMARY KEY NOT NULL CHECK(action_id GLOB 'effect_action_[0-9a-f]*' AND length(action_id)=78), "
    "plan_id TEXT NOT NULL REFERENCES field_effects(plan_id), "
    "kind TEXT NOT NULL CHECK(kind IN ('targeted_read','forward_reevaluation')), "
    "state TEXT NOT NULL CHECK(state IN ('pending','claimed','completed')), "
    "attempt INTEGER NOT NULL CHECK(attempt BETWEEN 0 AND 3), updated_at INTEGER NOT NULL CHECK(updated_at>=0), "
    "document TEXT NOT NULL CHECK(typeof(document)='text' AND length(CAST(document AS BLOB)) BETWEEN 2 AND 8192), "
    "UNIQUE(plan_id,kind))"
)
_FIELD_EFFECT_CLEANUPS_SQL = (
    "CREATE TABLE field_effect_cleanups ("
    "plan_id TEXT PRIMARY KEY NOT NULL REFERENCES field_effects(plan_id), "
    "state TEXT NOT NULL CHECK(state IN ('pending','completed')), "
    "attempt INTEGER NOT NULL CHECK(attempt BETWEEN 0 AND 3), updated_at INTEGER NOT NULL CHECK(updated_at>=0), "
    "document TEXT NOT NULL CHECK(typeof(document)='text' AND length(CAST(document AS BLOB)) BETWEEN 2 AND 4096))"
)
_FIELD_EFFECT_EVENTS_SQL = (
    "CREATE TABLE field_effect_events ("
    "event_id TEXT PRIMARY KEY NOT NULL CHECK(event_id GLOB 'effect_event_[0-9a-f]*' AND length(event_id)=77), "
    "plan_id TEXT NOT NULL REFERENCES field_effects(plan_id), sequence INTEGER NOT NULL CHECK(sequence>=1), "
    "kind TEXT NOT NULL CHECK(kind IN ('planned','claimed','applying','reconciling','retryable','verified','stale','superseded','abandoned')), "
    "occurred_at INTEGER NOT NULL CHECK(occurred_at>=0), "
    "document TEXT NOT NULL CHECK(typeof(document)='text' AND length(CAST(document AS BLOB)) BETWEEN 2 AND 131072), "
    "UNIQUE(plan_id,sequence))"
)
_FIELD_EFFECT_EVENT_NO_UPDATE_SQL = "CREATE TRIGGER field_effect_events_no_update BEFORE UPDATE ON field_effect_events BEGIN SELECT RAISE(ABORT, 'field effect events are append-only'); END"
_FIELD_EFFECT_EVENT_NO_DELETE_SQL = "CREATE TRIGGER field_effect_events_no_delete BEFORE DELETE ON field_effect_events BEGIN SELECT RAISE(ABORT, 'field effect events are append-only'); END"
_FIELD_EFFECT_NO_CHANGE_NO_UPDATE_SQL = "CREATE TRIGGER field_effect_no_changes_no_update BEFORE UPDATE ON field_effect_no_changes BEGIN SELECT RAISE(ABORT, 'field effect no-change facts are append-only'); END"
_FIELD_EFFECT_NO_CHANGE_NO_DELETE_SQL = "CREATE TRIGGER field_effect_no_changes_no_delete BEFORE DELETE ON field_effect_no_changes BEGIN SELECT RAISE(ABORT, 'field effect no-change facts are append-only'); END"


_SHADOW_DECISION_OUTCOMES_SQL = (
    "CREATE TABLE shadow_decision_outcomes ("
    "outcome_set_id TEXT PRIMARY KEY NOT NULL CHECK(outcome_set_id GLOB 'shadow_outcome_set_[0-9a-f]*' AND length(outcome_set_id)=83), "
    "review_case_id TEXT NOT NULL REFERENCES review_cases(review_case_id), "
    "report_id TEXT NOT NULL REFERENCES metadata_reports(report_id), "
    "proposal_set_id TEXT NOT NULL REFERENCES proposal_sets(proposal_set_id), "
    "resolved_policy_id TEXT NOT NULL CHECK(resolved_policy_id GLOB 'resolved_policy_[0-9a-f]*' AND length(resolved_policy_id)=80), "
    "source_decision_set_id TEXT NOT NULL, evaluated_at INTEGER NOT NULL CHECK(evaluated_at>=0), "
    "document TEXT NOT NULL CHECK(typeof(document)='text' AND length(CAST(document AS BLOB)) BETWEEN 2 AND 1048576 "
    "AND json_extract(document,'$.schema_version')='shadow-outcome-set-v11' "
    f"AND json_extract(document,'$.decision_policy_version') IS '{DECISION_POLICY_VERSION}' "
    f"AND json_extract(document,'$.decision_policy_sha256') IS '{DECISION_POLICY_SHA256}'))"
)
_SHADOW_OUTCOME_FIELD_FACTS_SQL = (
    "CREATE TABLE shadow_outcome_field_facts ("
    "outcome_set_id TEXT NOT NULL REFERENCES shadow_decision_outcomes(outcome_set_id), "
    "review_case_id TEXT NOT NULL REFERENCES review_cases(review_case_id), "
    "destination TEXT NOT NULL, disposition TEXT NOT NULL, "
    "automatic_destination INTEGER NOT NULL "
    "CHECK(automatic_destination IN (0,1)), policy_category TEXT, "
    "PRIMARY KEY(outcome_set_id,destination)) WITHOUT ROWID"
)
_SHADOW_OUTCOME_FIELD_FACTS_CASE_INDEX_SQL = (
    "CREATE INDEX shadow_outcome_field_facts_case "
    "ON shadow_outcome_field_facts(review_case_id,destination,disposition)"
)
_SHADOW_OUTCOME_FIELD_FACT_VALUES_SQL = (
    "json_extract(outcome.value,'$.destination'),"
    "json_extract(outcome.value,'$.disposition'),"
    "CASE WHEN json_extract(outcome.value,'$.policy_destination')='automatic' "
    "THEN 1 ELSE 0 END,json_extract(outcome.value,'$.policy_category')"
)
_SHADOW_OUTCOME_FIELD_FACTS_INSERT_SQL = (
    "CREATE TRIGGER shadow_outcomes_project_field_facts AFTER INSERT ON shadow_decision_outcomes "
    "BEGIN INSERT INTO shadow_outcome_field_facts("
    "outcome_set_id,review_case_id,destination,disposition,"
    "automatic_destination,policy_category) SELECT NEW.outcome_set_id,NEW.review_case_id,"
    + _SHADOW_OUTCOME_FIELD_FACT_VALUES_SQL
    + " "
    "FROM json_each(NEW.document,'$.outcomes') outcome; END"
)
_SHADOW_DECISION_OUTCOMES_CASE_HISTORY_INDEX_SQL = (
    "CREATE INDEX shadow_decision_outcomes_case_history "
    "ON shadow_decision_outcomes(review_case_id,evaluated_at DESC,outcome_set_id DESC)"
)
_NO_UPDATE_SQL = (
    "CREATE TRIGGER metadata_reports_no_update BEFORE UPDATE ON metadata_reports "
    "BEGIN SELECT RAISE(ABORT, 'metadata reports are append-only'); END"
)
_NO_DELETE_SQL = (
    "CREATE TRIGGER metadata_reports_no_delete BEFORE DELETE ON metadata_reports "
    "BEGIN SELECT RAISE(ABORT, 'metadata reports are append-only'); END"
)
_COVER_NO_UPDATE_SQL = (
    "CREATE TRIGGER metadata_report_cover_references_no_update BEFORE UPDATE ON metadata_report_cover_references "
    "BEGIN SELECT RAISE(ABORT, 'metadata report cover references are append-only'); END"
)
_COVER_NO_DELETE_SQL = (
    "CREATE TRIGGER metadata_report_cover_references_no_delete BEFORE DELETE ON metadata_report_cover_references "
    "BEGIN SELECT RAISE(ABORT, 'metadata report cover references are append-only'); END"
)
_PROPOSAL_NO_UPDATE_SQL = (
    "CREATE TRIGGER proposal_sets_no_update BEFORE UPDATE ON proposal_sets "
    "BEGIN SELECT RAISE(ABORT, 'proposal sets are append-only'); END"
)
_PROPOSAL_NO_DELETE_SQL = (
    "CREATE TRIGGER proposal_sets_no_delete BEFORE DELETE ON proposal_sets "
    "BEGIN SELECT RAISE(ABORT, 'proposal sets are append-only'); END"
)
_REVIEW_NO_UPDATE_SQL = "CREATE TRIGGER review_cases_no_update BEFORE UPDATE ON review_cases BEGIN SELECT RAISE(ABORT, 'review cases are append-only'); END"
_REVIEW_NO_DELETE_SQL = "CREATE TRIGGER review_cases_no_delete BEFORE DELETE ON review_cases BEGIN SELECT RAISE(ABORT, 'review cases are append-only'); END"
_REVIEW_TRANSITION_NO_UPDATE_SQL = "CREATE TRIGGER review_case_transitions_no_update BEFORE UPDATE ON review_case_transitions BEGIN SELECT RAISE(ABORT, 'review transitions are append-only'); END"
_REVIEW_TRANSITION_NO_DELETE_SQL = "CREATE TRIGGER review_case_transitions_no_delete BEFORE DELETE ON review_case_transitions BEGIN SELECT RAISE(ABORT, 'review transitions are append-only'); END"
_HUMAN_DECISION_NO_UPDATE_SQL = "CREATE TRIGGER human_decisions_no_update BEFORE UPDATE ON human_decisions BEGIN SELECT RAISE(ABORT, 'human decisions are append-only'); END"
_HUMAN_DECISION_NO_DELETE_SQL = "CREATE TRIGGER human_decisions_no_delete BEFORE DELETE ON human_decisions BEGIN SELECT RAISE(ABORT, 'human decisions are append-only'); END"
_HUMAN_DECISION_EVENT_NO_UPDATE_SQL = "CREATE TRIGGER human_decision_events_no_update BEFORE UPDATE ON human_decision_events BEGIN SELECT RAISE(ABORT, 'human decision events are append-only'); END"
_HUMAN_DECISION_EVENT_NO_DELETE_SQL = "CREATE TRIGGER human_decision_events_no_delete BEFORE DELETE ON human_decision_events BEGIN SELECT RAISE(ABORT, 'human decision events are append-only'); END"
_AUTOMATIC_DECISION_NO_UPDATE_SQL = "CREATE TRIGGER automatic_decisions_no_update BEFORE UPDATE ON automatic_decisions BEGIN SELECT RAISE(ABORT, 'automatic decisions are append-only'); END"
_AUTOMATIC_DECISION_NO_DELETE_SQL = "CREATE TRIGGER automatic_decisions_no_delete BEFORE DELETE ON automatic_decisions BEGIN SELECT RAISE(ABORT, 'automatic decisions are append-only'); END"
_SHADOW_DECISION_OUTCOME_NO_UPDATE_SQL = "CREATE TRIGGER shadow_decision_outcomes_no_update BEFORE UPDATE ON shadow_decision_outcomes BEGIN SELECT RAISE(ABORT, 'shadow decision outcomes are append-only'); END"
_SHADOW_DECISION_OUTCOME_NO_DELETE_SQL = "CREATE TRIGGER shadow_decision_outcomes_no_delete BEFORE DELETE ON shadow_decision_outcomes BEGIN SELECT RAISE(ABORT, 'shadow decision outcomes are append-only'); END"
_OWNER_SETTINGS_SQL = "CREATE TABLE owner_settings (singleton INTEGER PRIMARY KEY NOT NULL CHECK(singleton=1),revision INTEGER NOT NULL CHECK(revision>=1),document TEXT NOT NULL CHECK(typeof(document)='text' AND length(CAST(document AS BLOB)) BETWEEN 2 AND 262144))"
_OWNER_SETTING_SECRETS_SQL = "CREATE TABLE owner_setting_secrets (field_id TEXT PRIMARY KEY NOT NULL CHECK(length(field_id) BETWEEN 1 AND 128),value TEXT NOT NULL CHECK(typeof(value)='text' AND length(CAST(value AS BLOB)) BETWEEN 1 AND 2048))"
_REVIEWER_ACCESS_SQL = "CREATE TABLE reviewer_access (singleton INTEGER PRIMARY KEY NOT NULL CHECK(singleton=1),revision INTEGER NOT NULL CHECK(revision>=1),document TEXT NOT NULL CHECK(typeof(document)='text' AND length(CAST(document AS BLOB)) BETWEEN 2 AND 262144 AND json_extract(document,'$.schema_version')='reviewer-access-v3'))"
_REVIEWER_ACCESS_SECRETS_SQL = "CREATE TABLE reviewer_access_secrets (field_id TEXT PRIMARY KEY NOT NULL CHECK(field_id='openrouter.api_key'),value TEXT NOT NULL CHECK(typeof(value)='text' AND length(CAST(value AS BLOB)) BETWEEN 1 AND 2048))"
_BUILTIN_POLICY = MetadataPolicyProfile.builtin()
_SETUP_POLICY_DRAFT_ROW = (_BUILTIN_POLICY.policy_id, _BUILTIN_POLICY.canonical_json)
_SETUP_POLICY_STATE_ROW = (1, _BUILTIN_POLICY.policy_id, 1, 0, "automatic")
_SETUP_POLICY_TRANSITION_ROW = (
    "policy_transition_"
    + hashlib.sha256(
        ("automatic-bootstrap-v1:" + _BUILTIN_POLICY.policy_id).encode("utf-8")
    ).hexdigest(),
    _BUILTIN_POLICY.policy_id,
    _BUILTIN_POLICY.policy_id,
    1,
    "completed",
    0,
    0,
    0,
    0,
)
_SETUP_REVIEW_CONTROL_ROW = (1, 0, 0, 0, None, 0, 0, 0)
_SETUP_AUTOMATIC_SAFETY_ROW = (1, 0, None, "[]", 0)
_SETUP_FIELD_EFFECT_CONTROL_ROW = (1, 0, 0)
_SETUP_BROWSE_FACT_IDENTITY_ROW = (1, unicodedata.unidata_version)


_DESCRIPTION_ADVICE_TABLES = {
    "description_advice_attempts": "CREATE TABLE description_advice_attempts (attempt_id TEXT PRIMARY KEY NOT NULL, request_identity TEXT NOT NULL CHECK(length(request_identity)=64), binding_document TEXT NOT NULL CHECK(length(CAST(binding_document AS BLOB)) BETWEEN 2 AND 4096), engine TEXT NOT NULL CHECK(engine IN ('codex','openrouter')), model TEXT NOT NULL CHECK(length(model) BETWEEN 1 AND 256), state TEXT NOT NULL CHECK(state IN ('requested','running','succeeded','failed','interrupted')), failure TEXT CHECK(failure IN ('unavailable','invalid_output','interrupted')), created_at INTEGER NOT NULL CHECK(created_at>=0), updated_at INTEGER NOT NULL CHECK(updated_at>=created_at))",
    "description_advice_candidates": "CREATE TABLE description_advice_candidates (candidate_id TEXT PRIMARY KEY NOT NULL, attempt_id TEXT NOT NULL REFERENCES description_advice_attempts(attempt_id), request_identity TEXT NOT NULL CHECK(length(request_identity)=64), document TEXT NOT NULL CHECK(length(CAST(document AS BLOB)) BETWEEN 2 AND 131072))",
    "current_description_advice": "CREATE TABLE current_description_advice (review_case_id TEXT PRIMARY KEY NOT NULL REFERENCES review_cases(review_case_id), attempt_id TEXT NOT NULL REFERENCES description_advice_attempts(attempt_id), candidate_id TEXT REFERENCES description_advice_candidates(candidate_id), binding_document TEXT NOT NULL CHECK(length(CAST(binding_document AS BLOB)) BETWEEN 2 AND 4096))",
    "description_decisions": "CREATE TABLE description_decisions (decision_id TEXT PRIMARY KEY NOT NULL, review_case_id TEXT NOT NULL REFERENCES review_cases(review_case_id), candidate_id TEXT NOT NULL REFERENCES description_advice_candidates(candidate_id), idempotency_digest TEXT NOT NULL UNIQUE CHECK(length(idempotency_digest)=64), document TEXT NOT NULL CHECK(length(CAST(document AS BLOB)) BETWEEN 2 AND 16384))",
    "current_description_decisions": "CREATE TABLE current_description_decisions (review_case_id TEXT PRIMARY KEY NOT NULL REFERENCES review_cases(review_case_id), decision_id TEXT NOT NULL REFERENCES description_decisions(decision_id))",
    "description_preparation_pending": "CREATE TABLE description_preparation_pending (review_case_id TEXT PRIMARY KEY NOT NULL REFERENCES review_cases(review_case_id), retry INTEGER NOT NULL CHECK(retry IN (0,1)), requested_at INTEGER NOT NULL CHECK(requested_at>=0))",
}
_DESCRIPTION_ADVICE_TRIGGERS = {
    f"{table}_no_{operation}": f"CREATE TRIGGER {table}_no_{operation} BEFORE {operation.upper()} ON {table} BEGIN SELECT RAISE(ABORT, 'description history is immutable'); END"
    for table in ("description_advice_candidates", "description_decisions")
    for operation in ("update", "delete")
}
# A corrective keep_current's predecessor and an authored description's both
# find a case's description decisions; without this the pending statements
# built an automatic index over field_effects per call (#1204).
_DESCRIPTION_DECISIONS_CASE_INDEX_SQL = (
    "CREATE INDEX description_decisions_case ON description_decisions(review_case_id)"
)


# #1232: Library, search, the attention lens and Attention read which fields
# ask for the owner from these facts instead of re-evaluating the attention
# predicate over every field per request. `_ATTENTION_FACT_SOURCE_SQL` is the
# one definition; open compares the table with it. The untyped
# `cover_policy_reference` keeps the stored JSON value's own type, so the live
# comparison is the one `_CURRENT_COVER_ATTENTION_AUTHORITY_SQL` makes.
_ATTENTION_FACTS_SQL = (
    "CREATE TABLE review_attention_facts ("
    "connection_name TEXT NOT NULL, library_name TEXT NOT NULL, item_id TEXT NOT NULL, "
    "report_id TEXT NOT NULL, destination TEXT NOT NULL, cover_consideration_id TEXT, "
    "cover_policy_reference, cover_refresh_admits INTEGER, "
    "PRIMARY KEY(connection_name,library_name,item_id,report_id,destination)) WITHOUT ROWID"
)
# Every maintenance trigger names the packages and fields a changed row can
# reach by inserting into this view; its one INSTEAD OF trigger recomputes
# exactly those facts from current state in the writer's own statement.
_ATTENTION_REFRESH_VIEW_SQL = (
    "CREATE VIEW review_attention_refresh(connection_name,library_name,item_id,destination) "
    "AS SELECT NULL,NULL,NULL,NULL WHERE 0"
)
_ATTENTION_REFRESH_SQL = (
    "CREATE TRIGGER review_attention_refresh_apply INSTEAD OF INSERT ON review_attention_refresh "
    "BEGIN DELETE FROM review_attention_facts WHERE connection_name=NEW.connection_name "
    "AND library_name=NEW.library_name AND item_id=NEW.item_id "
    "AND (NEW.destination IS NULL OR destination=NEW.destination); "
    "INSERT INTO review_attention_facts(connection_name,library_name,item_id,report_id,"
    "destination,cover_consideration_id,cover_policy_reference,cover_refresh_admits) "
    + _ATTENTION_FACT_SOURCE_SQL
    + " AND c.connection_name=NEW.connection_name AND c.library_name=NEW.library_name "
    "AND c.item_id=NEW.item_id AND (NEW.destination IS NULL OR result.destination=NEW.destination); END"
)
# Maintenance reaches a changed report or proposal through these.
_CURRENT_REVIEW_PACKAGES_REPORT_INDEX_SQL = (
    "CREATE INDEX current_review_packages_report ON current_review_packages(report_id)"
)
_REVIEW_CASES_REPORT_INDEX_SQL = (
    "CREATE INDEX review_cases_report ON review_cases(report_id)"
)
_REVIEW_CASES_PROPOSAL_INDEX_SQL = (
    "CREATE INDEX review_cases_proposal ON review_cases(proposal_set_id)"
)


def _packages_of_case(destination: str) -> str:
    return (
        "SELECT c.connection_name,c.library_name,c.item_id," + destination + " "
        "FROM current_review_packages c WHERE c.review_case_id={row}.review_case_id"
    )


# Each input of `_ATTENTION_FACT_SOURCE_SQL`, the operations that can change
# it, and the facts a changed row (`{row}`, NEW or OLD) can reach. An
# operation an append-only table refuses has no trigger, because it cannot
# happen. The source reaches its four append-only parents -- review cases,
# reports, cover considerations and selections -- only through a child row
# whose immediate foreign key names them, so inserting one reaches no fact and
# none of the four is listed; `test_review_attention_facts` holds this map to
# the source's tables and those keys.
_ATTENTION_INPUTS: dict[str, tuple[tuple[str, ...], str]] = {
    "current_review_packages": (
        ("insert", "update", "delete"),
        "SELECT {row}.connection_name,{row}.library_name,{row}.item_id,NULL",
    ),
    "review_case_field_facts": (
        ("insert", "update", "delete"),
        _packages_of_case("{row}.destination"),
    ),
    "metadata_report_browse_facts": (
        ("insert", "update", "delete"),
        "SELECT c.connection_name,c.library_name,c.item_id,NULL "
        "FROM current_review_packages c WHERE c.report_id={row}.report_id UNION "
        "SELECT c.connection_name,c.library_name,c.item_id,NULL FROM review_cases rc "
        "JOIN current_review_packages c ON c.review_case_id=rc.review_case_id "
        "WHERE rc.report_id={row}.report_id",
    ),
    "proposal_set_browse_facts": (
        ("insert", "update", "delete"),
        "SELECT c.connection_name,c.library_name,c.item_id,NULL FROM review_cases rc "
        "JOIN current_review_packages c ON c.review_case_id=rc.review_case_id "
        "WHERE rc.proposal_set_id={row}.proposal_set_id",
    ),
    "metadata_policy_resolutions": (
        ("insert", "update", "delete"),
        "SELECT c.connection_name,c.library_name,c.item_id,NULL "
        "FROM proposal_set_browse_facts policy JOIN review_cases rc "
        "ON rc.proposal_set_id=policy.proposal_set_id JOIN current_review_packages c "
        "ON c.review_case_id=rc.review_case_id "
        "WHERE policy.resolved_policy_id={row}.resolved_policy_id",
    ),
    "shadow_outcome_field_facts": (
        ("insert", "update", "delete"),
        _packages_of_case("{row}.destination"),
    ),
    "automatic_safety_state": (
        ("insert", "update", "delete"),
        "SELECT DISTINCT c.connection_name,c.library_name,c.item_id,outcome.destination "
        "FROM shadow_outcome_field_facts outcome JOIN current_review_packages c "
        "ON c.review_case_id=outcome.review_case_id WHERE outcome.automatic_destination=1",
    ),
    "human_decisions": (
        ("insert",),
        _packages_of_case("{row}.destination"),
    ),
    "review_case_episodes": (
        ("insert", "update", "delete"),
        _packages_of_case("NULL"),
    ),
    "automatic_decisions": (
        ("insert",),
        _packages_of_case("{row}.destination"),
    ),
    "current_description_decisions": (
        ("insert", "update", "delete"),
        _packages_of_case("'description'"),
    ),
    "current_cover_considerations": (
        ("insert", "update", "delete"),
        _packages_of_case("'cover'"),
    ),
    "current_cover_selection_authorities": (
        ("insert", "update", "delete"),
        "SELECT {row}.connection_name,{row}.library_name,{row}.item_id,'cover'",
    ),
    "field_effect_suspensions": (
        ("insert", "update", "delete"),
        "SELECT c.connection_name,c.library_name,c.item_id,"
        "CASE WHEN {row}.scope='destination' THEN {row}.destination END "
        "FROM current_review_packages c WHERE c.connection_name={row}.connection_name",
    ),
}


# The columns of each updatable input that the source, or the facts a row
# reaches, read. An update refreshes only when one of them changes value, so a
# write of anything else -- a safety state's time, a suspension's reason, a
# package's checkpoint -- refreshes nothing. `test_review_attention_facts`
# derives these from the source statement through SQLite's authorizer.
_ATTENTION_UPDATED_COLUMNS: dict[str, tuple[str, ...]] = {
    "current_review_packages": (
        "connection_name",
        "library_name",
        "item_id",
        "report_id",
        "review_case_id",
    ),
    "review_case_field_facts": (
        "review_case_id",
        "destination",
        "unresolved_choice",
        "manual_bibliographic_attention",
        "has_owner_decision_actions",
    ),
    "metadata_report_browse_facts": ("report_id", "format"),
    "proposal_set_browse_facts": ("proposal_set_id", "resolved_policy_id"),
    "metadata_policy_resolutions": ("resolved_policy_id", "library_name", "document"),
    "shadow_outcome_field_facts": (
        "review_case_id",
        "destination",
        "disposition",
        "policy_category",
        "automatic_destination",
    ),
    "automatic_safety_state": (
        "paused",
        "global_suspension",
        "category_suspensions_document",
    ),
    "review_case_episodes": ("review_case_id", "decision_floor"),
    "current_description_decisions": ("review_case_id",),
    "current_cover_considerations": ("review_case_id", "consideration_id"),
    "current_cover_selection_authorities": (
        "connection_name",
        "library_name",
        "item_id",
        "selection_id",
    ),
    "field_effect_suspensions": ("scope", "connection_name", "destination"),
}


def _attention_input_trigger_sql(table: str, operation: str, scope: str) -> str:
    rows = {"insert": ("NEW",), "update": ("OLD", "NEW"), "delete": ("OLD",)}
    target = f" ON {table}"
    if operation == "update":
        columns = _ATTENTION_UPDATED_COLUMNS[table]
        target = f" OF {','.join(columns)} ON {table} WHEN " + " OR ".join(
            f"OLD.{name} IS NOT NEW.{name} COLLATE BINARY" for name in columns
        )
    return (
        f"CREATE TRIGGER {table}_refresh_attention_{operation} AFTER "
        f"{operation.upper()}{target} BEGIN INSERT INTO review_attention_refresh"
        "(connection_name,library_name,item_id,destination) "
        + " UNION ".join(scope.format(row=row) for row in rows[operation])
        + "; END"
    )


_ATTENTION_INPUT_TRIGGERS = {
    f"{table}_refresh_attention_{operation}": _attention_input_trigger_sql(
        table, operation, scope
    )
    for table, (operations, scope) in _ATTENTION_INPUTS.items()
    for operation in operations
}
# The exact objects #1232 adds to its predecessor, in creation order.
_ATTENTION_FACT_OBJECTS: tuple[tuple[str, str, str], ...] = (
    ("table", "review_attention_facts", _ATTENTION_FACTS_SQL),
    ("view", "review_attention_refresh", _ATTENTION_REFRESH_VIEW_SQL),
    ("trigger", "review_attention_refresh_apply", _ATTENTION_REFRESH_SQL),
    (
        "index",
        "current_review_packages_report",
        _CURRENT_REVIEW_PACKAGES_REPORT_INDEX_SQL,
    ),
    ("index", "review_cases_report", _REVIEW_CASES_REPORT_INDEX_SQL),
    ("index", "review_cases_proposal", _REVIEW_CASES_PROPOSAL_INDEX_SQL),
    *(("trigger", name, sql) for name, sql in _ATTENTION_INPUT_TRIGGERS.items()),
)


def _create_attention_facts(connection: sqlite3.Connection) -> None:
    """Create #1232's objects and derive every fact from the current rows."""
    for _kind, _name, statement in _ATTENTION_FACT_OBJECTS:
        connection.execute(statement)
    connection.execute(
        "INSERT INTO review_attention_refresh(connection_name,library_name,item_id,"
        "destination) SELECT connection_name,library_name,item_id,NULL "
        "FROM current_review_packages"
    )


def _create_schema(connection: sqlite3.Connection) -> None:
    for statement in (
        *_DESCRIPTION_ADVICE_TABLES.values(),
        *_DESCRIPTION_ADVICE_TRIGGERS.values(),
    ):
        connection.execute(statement)
    """Build the exact schema and seed its singleton rows.

    The statement order below is load-bearing. `sqlite_master` row order
    follows creation order and `rootpage` follows allocation order, and
    `_validate_schema` sorts before comparing, so it will not catch a
    reordering -- only a whole-file digest will. Do not reorder or reflow.
    """
    connection.execute("BEGIN IMMEDIATE")
    connection.execute(_REPORTS_SQL)
    connection.execute(_REPORT_BROWSE_FACTS_SQL)
    connection.execute(_REPORT_BROWSE_AUTHORS_SQL)
    connection.execute(_REPORT_BROWSE_FACTS_INSERT_SQL)
    connection.execute(_CURRENT_SQL)
    connection.execute(_CURRENT_LINEAGE_SQL)
    connection.execute(_COVER_REFERENCES_SQL)
    connection.execute(_COVER_ARTIFACT_INDEX_SQL)
    connection.execute(_COVER_PREVIEW_INDEX_SQL)
    connection.execute(_PROPOSAL_SETS_SQL)
    connection.execute(_PROPOSAL_BROWSE_FACTS_SQL)
    connection.execute(_PROPOSAL_BROWSE_FACTS_INSERT_SQL)
    connection.execute(_BROWSE_FACT_IDENTITY_SQL)
    connection.execute(
        "INSERT INTO browse_fact_identity(singleton,unicode_version) VALUES(?,?)",
        _SETUP_BROWSE_FACT_IDENTITY_ROW,
    )
    connection.execute(_METADATA_POLICY_DRAFTS_SQL)
    connection.execute(
        "INSERT INTO metadata_policy_drafts(policy_id,document) VALUES(?,?)",
        _SETUP_POLICY_DRAFT_ROW,
    )
    connection.execute(_METADATA_POLICY_STATE_SQL)
    connection.execute(
        "INSERT INTO metadata_policy_state(singleton,active_policy_id,generation,activated_at,posture) VALUES(?,?,?,?,?)",
        _SETUP_POLICY_STATE_ROW,
    )
    connection.execute(_METADATA_POLICY_RESOLUTIONS_SQL)
    connection.execute(_METADATA_POLICY_RESOLUTIONS_RESOLVED_INDEX_SQL)
    connection.execute(_METADATA_POLICY_TRANSITIONS_SQL)
    connection.execute(
        "INSERT INTO metadata_policy_transitions(transition_id,from_policy_id,to_policy_id,generation,state,total,acknowledged,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        _SETUP_POLICY_TRANSITION_ROW,
    )
    connection.execute(_REVIEW_CASES_SQL)
    connection.execute(_REVIEW_CASE_FIELD_FACTS_SQL)
    connection.execute(_REVIEW_CASE_FIELD_FACTS_INSERT_SQL)
    connection.execute(_REVIEW_POINTERS_SQL)
    connection.execute(_CURRENT_REVIEW_CASE_LINEAGES_CASE_INDEX_SQL)
    connection.execute(_CURRENT_REVIEW_PACKAGES_SQL)
    connection.execute(_CURRENT_REVIEW_PACKAGES_CASE_INDEX_SQL)
    connection.execute(_PROVIDER_RECOVERY_OUTBOX_SQL)
    connection.execute(_REVIEW_TRANSITIONS_SQL)
    connection.execute(_REVIEW_SCANS_SQL)
    connection.execute(_REVIEW_CONTROL_SQL)
    connection.execute(
        "INSERT INTO review_preparation_control(singleton,paused,claim_sequence,first_boot_acknowledged,last_library,circuit_failures,circuit_open_until,updated_at) VALUES(?,?,?,?,?,?,?,?)",
        _SETUP_REVIEW_CONTROL_ROW,
    )
    connection.execute(_REVIEW_PREPARATION_PRESENTATION_SQL)
    connection.execute(_REVIEW_PREPARATION_PRESENTATION_ACTIVE_INDEX_SQL)
    connection.execute(_REVIEW_PREPARATION_PRESENTATION_RECENT_INDEX_SQL)
    connection.execute(_REVIEW_PREPARATION_PRESENTATION_COMPLETION_INTEGRITY_INDEX_SQL)
    connection.execute(_REVIEW_PREPARATION_PRESENTATION_RUN_INDEX_SQL)
    connection.execute(_REVIEW_PREPARATION_PRESENTATION_THROUGHPUT_INDEX_SQL)
    connection.execute(_REVIEW_CONNECTION_CONDITIONS_SQL)
    connection.execute(_REVIEW_WORK_SQL)
    connection.execute(_METADATA_POLICY_TRANSITION_ITEMS_SQL)
    connection.execute(_REVIEW_ABSENCES_SQL)
    connection.execute(_REVIEW_WORK_AGED_CLAIM_INDEX_SQL)
    connection.execute(_REVIEW_WORK_DUE_PROMOTION_INDEX_SQL)
    connection.execute(_REVIEW_WORK_RECENT_CLAIM_INDEX_SQL)
    connection.execute(_REVIEW_WORK_LINEAGE_HISTORY_INDEX_SQL)
    connection.execute(_HUMAN_DECISIONS_SQL)
    connection.execute(_HUMAN_DECISIONS_CASE_DESTINATION_INDEX_SQL)
    connection.execute(_HUMAN_DECISION_EVENTS_SQL)
    connection.execute(_REVIEW_CASE_EPISODES_SQL)
    connection.execute(_COVER_CONSIDERATIONS_SQL)
    connection.execute(_CURRENT_COVER_CONSIDERATIONS_SQL)
    connection.execute(_COVER_SELECTION_AUTHORITIES_SQL)
    connection.execute(_CURRENT_COVER_SELECTION_AUTHORITIES_SQL)
    connection.execute(_COVER_CONSIDERATION_EVENTS_SQL)
    connection.execute(_AUTOMATIC_DECISIONS_SQL)
    connection.execute(_AUTOMATIC_SAFETY_SQL)
    connection.execute(
        "INSERT INTO automatic_safety_state(singleton,paused,global_suspension,category_suspensions_document,updated_at) VALUES(?,?,?,?,?)",
        _SETUP_AUTOMATIC_SAFETY_ROW,
    )
    connection.execute(_FIELD_EFFECT_CONTROL_SQL)
    connection.execute(
        "INSERT INTO field_effect_control(singleton,paused,updated_at) VALUES(?,?,?)",
        _SETUP_FIELD_EFFECT_CONTROL_ROW,
    )
    connection.execute(_FIELD_EFFECT_SUSPENSIONS_SQL)
    connection.execute(_FIELD_EFFECT_AUTHORITIES_SQL)
    connection.execute(_FIELD_EFFECT_CONSENTS_SQL)
    connection.execute(_FIELD_EFFECT_MANUAL_HOLDS_SQL)
    connection.execute(_FIELD_EFFECTS_SQL)
    connection.execute(_FIELD_EFFECTS_DECISION_INDEX_SQL)
    connection.execute(_FIELD_EFFECTS_COMPANION_DECISION_INDEX_SQL)
    connection.execute(_FIELD_EFFECTS_READY_INDEX_SQL)
    connection.execute(_FIELD_EFFECTS_WAITING_INDEX_SQL)
    connection.execute(_FIELD_EFFECTS_TARGET_INDEX_SQL)
    connection.execute(_DESCRIPTION_DECISIONS_CASE_INDEX_SQL)
    connection.execute(_FIELD_EFFECT_EVENTS_SQL)
    connection.execute(_FIELD_EFFECT_NO_CHANGES_SQL)
    connection.execute(_FIELD_EFFECT_ACTIONS_SQL)
    connection.execute(_FIELD_EFFECT_CLEANUPS_SQL)
    connection.execute(_OWNER_SETTINGS_SQL)
    connection.execute(_OWNER_SETTING_SECRETS_SQL)
    connection.execute(_REVIEWER_ACCESS_SQL)
    connection.execute(_REVIEWER_ACCESS_SECRETS_SQL)
    connection.execute(_SHADOW_DECISION_OUTCOMES_SQL)
    connection.execute(_SHADOW_DECISION_OUTCOMES_CASE_HISTORY_INDEX_SQL)
    connection.execute(_SHADOW_OUTCOME_FIELD_FACTS_SQL)
    connection.execute(_SHADOW_OUTCOME_FIELD_FACTS_CASE_INDEX_SQL)
    connection.execute(_SHADOW_OUTCOME_FIELD_FACTS_INSERT_SQL)
    connection.execute(_NO_UPDATE_SQL)
    connection.execute(_COVER_NO_UPDATE_SQL)
    connection.execute(_COVER_NO_DELETE_SQL)
    connection.execute(_PROPOSAL_NO_UPDATE_SQL)
    connection.execute(_PROPOSAL_NO_DELETE_SQL)
    connection.execute(_METADATA_POLICY_DRAFT_NO_UPDATE_SQL)
    connection.execute(_METADATA_POLICY_DRAFT_NO_DELETE_SQL)
    connection.execute(_REVIEW_NO_UPDATE_SQL)
    connection.execute(_REVIEW_NO_DELETE_SQL)
    connection.execute(_REVIEW_TRANSITION_NO_UPDATE_SQL)
    connection.execute(_REVIEW_TRANSITION_NO_DELETE_SQL)
    connection.execute(_HUMAN_DECISION_NO_UPDATE_SQL)
    connection.execute(_HUMAN_DECISION_NO_DELETE_SQL)
    connection.execute(_HUMAN_DECISION_EVENT_NO_UPDATE_SQL)
    connection.execute(_HUMAN_DECISION_EVENT_NO_DELETE_SQL)
    connection.execute(_COVER_CONSIDERATION_NO_UPDATE_SQL)
    connection.execute(_COVER_CONSIDERATION_NO_DELETE_SQL)
    connection.execute(_COVER_SELECTION_AUTHORITY_NO_UPDATE_SQL)
    connection.execute(_COVER_SELECTION_AUTHORITY_NO_DELETE_SQL)
    connection.execute(_CURRENT_COVER_SELECTION_AUTHORITY_INSERT_SQL)
    connection.execute(_CURRENT_COVER_SELECTION_AUTHORITY_UPDATE_SQL)
    connection.execute(_COVER_CONSIDERATION_EVENT_NO_UPDATE_SQL)
    connection.execute(_COVER_CONSIDERATION_EVENT_NO_DELETE_SQL)
    connection.execute(_AUTOMATIC_DECISION_NO_UPDATE_SQL)
    connection.execute(_AUTOMATIC_DECISION_NO_DELETE_SQL)
    connection.execute(_FIELD_EFFECT_EVENT_NO_UPDATE_SQL)
    connection.execute(_FIELD_EFFECT_EVENT_NO_DELETE_SQL)
    connection.execute(_FIELD_EFFECT_NO_CHANGE_NO_UPDATE_SQL)
    connection.execute(_FIELD_EFFECT_NO_CHANGE_NO_DELETE_SQL)
    connection.execute(_SHADOW_DECISION_OUTCOME_NO_UPDATE_SQL)
    connection.execute(_SHADOW_DECISION_OUTCOME_NO_DELETE_SQL)
    connection.execute(_NO_DELETE_SQL)
    _create_attention_facts(connection)
    connection.commit()


def _validate_existing_read_only(
    path: Path, descriptor: int, documents: ActivityDocuments | None = None
) -> None:
    absolute = str(path.absolute())
    uri = f"file:{quote(absolute, safe='/')}?mode=ro"
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(uri, uri=True, isolation_level=None)
        _validate_connection_binding(connection, path, descriptor)
        _register_casefold(connection)
        # One read transaction for the whole validation. The cross-table checks
        # below compare rows a live sibling commits together, so reading them
        # statement by statement would compare a row from before a commit with
        # one from after it and reject a database that was never inconsistent.
        connection.execute("BEGIN")
        connection.execute("PRAGMA schema_version").fetchone()
        _validate_schema(connection, documents, read_only=True)
    finally:
        if connection is not None:
            try:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
            finally:
                connection.close()


def _expected_schema_objects() -> list[tuple[str, str, str | None]]:
    """Return the one exact-current SQLite object contract."""
    return sorted(
        (
            *(
                ("table", name, statement)
                for name, statement in _DESCRIPTION_ADVICE_TABLES.items()
            ),
            *(
                ("index", f"sqlite_autoindex_{name}_1", None)
                for name in _DESCRIPTION_ADVICE_TABLES
            ),
            ("index", "sqlite_autoindex_description_decisions_2", None),
            (
                "index",
                "description_decisions_case",
                _DESCRIPTION_DECISIONS_CASE_INDEX_SQL,
            ),
            *(
                ("trigger", name, statement)
                for name, statement in _DESCRIPTION_ADVICE_TRIGGERS.items()
            ),
            ("index", "sqlite_autoindex_current_metadata_reports_1", None),
            ("index", "sqlite_autoindex_current_metadata_report_lineages_1", None),
            ("index", "sqlite_autoindex_current_review_case_lineages_1", None),
            ("index", "sqlite_autoindex_current_review_packages_1", None),
            (
                "index",
                "current_review_case_lineages_case",
                _CURRENT_REVIEW_CASE_LINEAGES_CASE_INDEX_SQL,
            ),
            (
                "index",
                "current_review_packages_case",
                _CURRENT_REVIEW_PACKAGES_CASE_INDEX_SQL,
            ),
            ("index", "sqlite_autoindex_provider_recovery_outbox_1", None),
            ("index", "sqlite_autoindex_metadata_report_cover_references_1", None),
            (
                "index",
                "metadata_report_cover_references_artifact_id",
                _COVER_ARTIFACT_INDEX_SQL,
            ),
            (
                "index",
                "metadata_report_cover_references_preview_digest",
                _COVER_PREVIEW_INDEX_SQL,
            ),
            ("index", "sqlite_autoindex_human_decision_events_1", None),
            ("index", "sqlite_autoindex_human_decision_events_2", None),
            ("index", "sqlite_autoindex_human_decisions_1", None),
            ("index", "sqlite_autoindex_human_decisions_2", None),
            (
                "index",
                "human_decisions_case_destination",
                _HUMAN_DECISIONS_CASE_DESTINATION_INDEX_SQL,
            ),
            ("index", "sqlite_autoindex_cover_considerations_1", None),
            ("index", "sqlite_autoindex_current_cover_considerations_1", None),
            ("index", "sqlite_autoindex_current_cover_considerations_2", None),
            ("index", "sqlite_autoindex_cover_selection_authorities_1", None),
            ("index", "sqlite_autoindex_cover_selection_authorities_2", None),
            (
                "index",
                "sqlite_autoindex_current_cover_selection_authorities_1",
                None,
            ),
            (
                "index",
                "sqlite_autoindex_current_cover_selection_authorities_2",
                None,
            ),
            ("index", "sqlite_autoindex_cover_consideration_events_1", None),
            ("index", "sqlite_autoindex_automatic_decisions_1", None),
            ("index", "sqlite_autoindex_automatic_decisions_2", None),
            ("index", "sqlite_autoindex_automatic_decisions_3", None),
            ("index", "sqlite_autoindex_field_effect_events_1", None),
            ("index", "sqlite_autoindex_field_effect_events_2", None),
            # WITHOUT ROWID is deliberate: only the explicit unique event identity
            # has an autoindex; the primary key does not.
            ("index", "sqlite_autoindex_field_effect_no_changes_2", None),
            ("index", "sqlite_autoindex_field_effect_suspensions_1", None),
            ("index", "sqlite_autoindex_field_effect_actions_1", None),
            ("index", "sqlite_autoindex_field_effect_actions_2", None),
            ("index", "sqlite_autoindex_field_effect_authorities_1", None),
            ("index", "sqlite_autoindex_field_effect_cleanups_1", None),
            ("index", "sqlite_autoindex_field_effect_consents_1", None),
            ("index", "sqlite_autoindex_field_effect_manual_holds_1", None),
            ("index", "sqlite_autoindex_field_effects_1", None),
            ("index", "field_effects_decision", _FIELD_EFFECTS_DECISION_INDEX_SQL),
            (
                "index",
                "field_effects_companion_decision",
                _FIELD_EFFECTS_COMPANION_DECISION_INDEX_SQL,
            ),
            ("index", "field_effects_ready", _FIELD_EFFECTS_READY_INDEX_SQL),
            ("index", "field_effects_waiting", _FIELD_EFFECTS_WAITING_INDEX_SQL),
            ("index", "field_effects_target", _FIELD_EFFECTS_TARGET_INDEX_SQL),
            ("index", "sqlite_autoindex_metadata_reports_1", None),
            ("index", "sqlite_autoindex_metadata_reports_2", None),
            ("index", "sqlite_autoindex_metadata_policy_drafts_1", None),
            ("index", "sqlite_autoindex_metadata_policy_resolutions_1", None),
            (
                "index",
                "metadata_policy_resolutions_resolved",
                _METADATA_POLICY_RESOLUTIONS_RESOLVED_INDEX_SQL,
            ),
            ("index", "sqlite_autoindex_metadata_policy_transitions_1", None),
            ("index", "sqlite_autoindex_metadata_policy_transitions_2", None),
            ("index", "sqlite_autoindex_metadata_policy_transition_items_1", None),
            ("index", "sqlite_autoindex_owner_setting_secrets_1", None),
            ("index", "sqlite_autoindex_reviewer_access_secrets_1", None),
            ("index", "sqlite_autoindex_proposal_sets_1", None),
            ("index", "sqlite_autoindex_review_cases_1", None),
            ("index", "sqlite_autoindex_review_case_transitions_1", None),
            ("index", "sqlite_autoindex_review_case_transitions_2", None),
            ("index", "sqlite_autoindex_review_preparation_absences_1", None),
            (
                "index",
                "sqlite_autoindex_review_preparation_connection_conditions_1",
                None,
            ),
            ("index", "sqlite_autoindex_review_preparation_scans_1", None),
            ("index", "sqlite_autoindex_review_preparation_presentation_1", None),
            ("index", "sqlite_autoindex_review_preparation_work_1", None),
            ("index", "sqlite_autoindex_review_preparation_work_2", None),
            (
                "index",
                "review_preparation_presentation_active",
                _REVIEW_PREPARATION_PRESENTATION_ACTIVE_INDEX_SQL,
            ),
            (
                "index",
                "review_preparation_presentation_completion_integrity",
                _REVIEW_PREPARATION_PRESENTATION_COMPLETION_INTEGRITY_INDEX_SQL,
            ),
            (
                "index",
                "review_preparation_presentation_recent",
                _REVIEW_PREPARATION_PRESENTATION_RECENT_INDEX_SQL,
            ),
            (
                "index",
                "review_preparation_presentation_run",
                _REVIEW_PREPARATION_PRESENTATION_RUN_INDEX_SQL,
            ),
            (
                "index",
                "review_preparation_presentation_throughput",
                _REVIEW_PREPARATION_PRESENTATION_THROUGHPUT_INDEX_SQL,
            ),
            (
                "index",
                "review_preparation_work_aged_claim",
                _REVIEW_WORK_AGED_CLAIM_INDEX_SQL,
            ),
            (
                "index",
                "review_preparation_work_due_promotion",
                _REVIEW_WORK_DUE_PROMOTION_INDEX_SQL,
            ),
            (
                "index",
                "review_preparation_work_recent_claim",
                _REVIEW_WORK_RECENT_CLAIM_INDEX_SQL,
            ),
            (
                "index",
                "review_preparation_work_lineage_history",
                _REVIEW_WORK_LINEAGE_HISTORY_INDEX_SQL,
            ),
            ("index", "sqlite_autoindex_shadow_decision_outcomes_1", None),
            (
                "index",
                "shadow_decision_outcomes_case_history",
                _SHADOW_DECISION_OUTCOMES_CASE_HISTORY_INDEX_SQL,
            ),
            (
                "index",
                "shadow_outcome_field_facts_case",
                _SHADOW_OUTCOME_FIELD_FACTS_CASE_INDEX_SQL,
            ),
            ("table", "current_metadata_reports", _CURRENT_SQL),
            ("table", "current_metadata_report_lineages", _CURRENT_LINEAGE_SQL),
            ("table", "current_review_case_lineages", _REVIEW_POINTERS_SQL),
            ("table", "current_review_packages", _CURRENT_REVIEW_PACKAGES_SQL),
            ("table", "provider_recovery_outbox", _PROVIDER_RECOVERY_OUTBOX_SQL),
            ("table", "cover_considerations", _COVER_CONSIDERATIONS_SQL),
            (
                "table",
                "current_cover_considerations",
                _CURRENT_COVER_CONSIDERATIONS_SQL,
            ),
            ("table", "cover_selection_authorities", _COVER_SELECTION_AUTHORITIES_SQL),
            (
                "table",
                "current_cover_selection_authorities",
                _CURRENT_COVER_SELECTION_AUTHORITIES_SQL,
            ),
            ("table", "cover_consideration_events", _COVER_CONSIDERATION_EVENTS_SQL),
            ("table", "human_decision_events", _HUMAN_DECISION_EVENTS_SQL),
            ("table", "human_decisions", _HUMAN_DECISIONS_SQL),
            ("table", "review_case_episodes", _REVIEW_CASE_EPISODES_SQL),
            ("table", "automatic_decisions", _AUTOMATIC_DECISIONS_SQL),
            ("table", "automatic_safety_state", _AUTOMATIC_SAFETY_SQL),
            ("table", "field_effect_control", _FIELD_EFFECT_CONTROL_SQL),
            ("table", "field_effect_suspensions", _FIELD_EFFECT_SUSPENSIONS_SQL),
            ("table", "field_effect_actions", _FIELD_EFFECT_ACTIONS_SQL),
            ("table", "field_effect_authorities", _FIELD_EFFECT_AUTHORITIES_SQL),
            ("table", "field_effect_cleanups", _FIELD_EFFECT_CLEANUPS_SQL),
            ("table", "field_effect_consents", _FIELD_EFFECT_CONSENTS_SQL),
            ("table", "field_effect_manual_holds", _FIELD_EFFECT_MANUAL_HOLDS_SQL),
            ("table", "field_effect_events", _FIELD_EFFECT_EVENTS_SQL),
            ("table", "field_effect_no_changes", _FIELD_EFFECT_NO_CHANGES_SQL),
            ("table", "field_effects", _FIELD_EFFECTS_SQL),
            ("table", "metadata_report_cover_references", _COVER_REFERENCES_SQL),
            ("table", "metadata_reports", _REPORTS_SQL),
            ("table", "metadata_report_browse_facts", _REPORT_BROWSE_FACTS_SQL),
            ("table", "metadata_report_browse_authors", _REPORT_BROWSE_AUTHORS_SQL),
            ("table", "metadata_policy_drafts", _METADATA_POLICY_DRAFTS_SQL),
            ("table", "metadata_policy_resolutions", _METADATA_POLICY_RESOLUTIONS_SQL),
            ("table", "metadata_policy_state", _METADATA_POLICY_STATE_SQL),
            ("table", "metadata_policy_transitions", _METADATA_POLICY_TRANSITIONS_SQL),
            (
                "table",
                "metadata_policy_transition_items",
                _METADATA_POLICY_TRANSITION_ITEMS_SQL,
            ),
            ("table", "owner_setting_secrets", _OWNER_SETTING_SECRETS_SQL),
            ("table", "owner_settings", _OWNER_SETTINGS_SQL),
            ("table", "reviewer_access", _REVIEWER_ACCESS_SQL),
            ("table", "reviewer_access_secrets", _REVIEWER_ACCESS_SECRETS_SQL),
            ("table", "proposal_sets", _PROPOSAL_SETS_SQL),
            ("table", "proposal_set_browse_facts", _PROPOSAL_BROWSE_FACTS_SQL),
            ("table", "browse_fact_identity", _BROWSE_FACT_IDENTITY_SQL),
            ("table", "review_case_transitions", _REVIEW_TRANSITIONS_SQL),
            ("table", "review_cases", _REVIEW_CASES_SQL),
            ("table", "review_case_field_facts", _REVIEW_CASE_FIELD_FACTS_SQL),
            ("table", "review_preparation_control", _REVIEW_CONTROL_SQL),
            (
                "table",
                "review_preparation_presentation",
                _REVIEW_PREPARATION_PRESENTATION_SQL,
            ),
            (
                "table",
                "review_preparation_connection_conditions",
                _REVIEW_CONNECTION_CONDITIONS_SQL,
            ),
            ("table", "review_preparation_scans", _REVIEW_SCANS_SQL),
            ("table", "review_preparation_absences", _REVIEW_ABSENCES_SQL),
            ("table", "review_preparation_work", _REVIEW_WORK_SQL),
            ("table", "shadow_decision_outcomes", _SHADOW_DECISION_OUTCOMES_SQL),
            (
                "table",
                "shadow_outcome_field_facts",
                _SHADOW_OUTCOME_FIELD_FACTS_SQL,
            ),
            (
                "trigger",
                "metadata_report_cover_references_no_delete",
                _COVER_NO_DELETE_SQL,
            ),
            (
                "trigger",
                "metadata_report_cover_references_no_update",
                _COVER_NO_UPDATE_SQL,
            ),
            ("trigger", "metadata_reports_no_delete", _NO_DELETE_SQL),
            (
                "trigger",
                "metadata_reports_project_browse_facts",
                _REPORT_BROWSE_FACTS_INSERT_SQL,
            ),
            ("trigger", "metadata_reports_no_update", _NO_UPDATE_SQL),
            (
                "trigger",
                "metadata_policy_drafts_no_delete",
                _METADATA_POLICY_DRAFT_NO_DELETE_SQL,
            ),
            (
                "trigger",
                "metadata_policy_drafts_no_update",
                _METADATA_POLICY_DRAFT_NO_UPDATE_SQL,
            ),
            (
                "trigger",
                "human_decision_events_no_delete",
                _HUMAN_DECISION_EVENT_NO_DELETE_SQL,
            ),
            (
                "trigger",
                "human_decision_events_no_update",
                _HUMAN_DECISION_EVENT_NO_UPDATE_SQL,
            ),
            ("trigger", "human_decisions_no_delete", _HUMAN_DECISION_NO_DELETE_SQL),
            ("trigger", "human_decisions_no_update", _HUMAN_DECISION_NO_UPDATE_SQL),
            (
                "trigger",
                "cover_considerations_no_delete",
                _COVER_CONSIDERATION_NO_DELETE_SQL,
            ),
            (
                "trigger",
                "cover_considerations_no_update",
                _COVER_CONSIDERATION_NO_UPDATE_SQL,
            ),
            (
                "trigger",
                "cover_selection_authorities_no_delete",
                _COVER_SELECTION_AUTHORITY_NO_DELETE_SQL,
            ),
            (
                "trigger",
                "cover_selection_authorities_no_update",
                _COVER_SELECTION_AUTHORITY_NO_UPDATE_SQL,
            ),
            (
                "trigger",
                "current_cover_selection_authorities_validate_insert",
                _CURRENT_COVER_SELECTION_AUTHORITY_INSERT_SQL,
            ),
            (
                "trigger",
                "current_cover_selection_authorities_validate_update",
                _CURRENT_COVER_SELECTION_AUTHORITY_UPDATE_SQL,
            ),
            (
                "trigger",
                "cover_consideration_events_no_delete",
                _COVER_CONSIDERATION_EVENT_NO_DELETE_SQL,
            ),
            (
                "trigger",
                "cover_consideration_events_no_update",
                _COVER_CONSIDERATION_EVENT_NO_UPDATE_SQL,
            ),
            (
                "trigger",
                "automatic_decisions_no_delete",
                _AUTOMATIC_DECISION_NO_DELETE_SQL,
            ),
            (
                "trigger",
                "automatic_decisions_no_update",
                _AUTOMATIC_DECISION_NO_UPDATE_SQL,
            ),
            (
                "trigger",
                "field_effect_events_no_delete",
                _FIELD_EFFECT_EVENT_NO_DELETE_SQL,
            ),
            (
                "trigger",
                "field_effect_events_no_update",
                _FIELD_EFFECT_EVENT_NO_UPDATE_SQL,
            ),
            (
                "trigger",
                "field_effect_no_changes_no_delete",
                _FIELD_EFFECT_NO_CHANGE_NO_DELETE_SQL,
            ),
            (
                "trigger",
                "field_effect_no_changes_no_update",
                _FIELD_EFFECT_NO_CHANGE_NO_UPDATE_SQL,
            ),
            ("trigger", "proposal_sets_no_delete", _PROPOSAL_NO_DELETE_SQL),
            (
                "trigger",
                "proposal_sets_project_browse_facts",
                _PROPOSAL_BROWSE_FACTS_INSERT_SQL,
            ),
            ("trigger", "proposal_sets_no_update", _PROPOSAL_NO_UPDATE_SQL),
            (
                "trigger",
                "review_case_transitions_no_delete",
                _REVIEW_TRANSITION_NO_DELETE_SQL,
            ),
            (
                "trigger",
                "review_case_transitions_no_update",
                _REVIEW_TRANSITION_NO_UPDATE_SQL,
            ),
            ("trigger", "review_cases_no_delete", _REVIEW_NO_DELETE_SQL),
            ("trigger", "review_cases_no_update", _REVIEW_NO_UPDATE_SQL),
            (
                "trigger",
                "review_cases_project_field_facts",
                _REVIEW_CASE_FIELD_FACTS_INSERT_SQL,
            ),
            (
                "trigger",
                "shadow_decision_outcomes_no_delete",
                _SHADOW_DECISION_OUTCOME_NO_DELETE_SQL,
            ),
            (
                "trigger",
                "shadow_decision_outcomes_no_update",
                _SHADOW_DECISION_OUTCOME_NO_UPDATE_SQL,
            ),
            (
                "trigger",
                "shadow_outcomes_project_field_facts",
                _SHADOW_OUTCOME_FIELD_FACTS_INSERT_SQL,
            ),
            *_ATTENTION_FACT_OBJECTS,
        )
    )


_CHECK_TOKENS = re.compile(
    r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|(?<![\w$])CHECK(?![\w$])|[()]|--|/\*|['\"`\[]",
    re.IGNORECASE,
)


def _check_expressions(sql: str) -> tuple[str, ...] | None:
    """Return every CHECK expression in one CREATE TABLE, or None.

    The scanner skips quoted strings and identifiers and matches parentheses;
    a CHECK it cannot take whole -- one not opened by a parenthesis, one left
    unclosed, a nested one, or one beside a comment or a bracket or backtick
    identifier -- makes the whole table unextractable rather than skipped.
    """
    expressions: list[str] = []
    depth = 0
    opened: tuple[int, int] | None = None  # (depth outside, expression start)
    awaiting = -1  # end of a CHECK keyword still waiting for its parenthesis
    for token in _CHECK_TOKENS.finditer(sql):
        text = token.group()
        if awaiting >= 0:
            if text != "(" or sql[awaiting : token.start()].strip():
                return None
            awaiting = -1
            opened = (depth, token.end())
            depth += 1
        elif text.upper() == "CHECK":
            if opened is not None:
                return None
            awaiting = token.end()
        elif text == "(":
            depth += 1
        elif text == ")":
            depth -= 1
            if depth < 0:
                return None
            if opened is not None and depth == opened[0]:
                expressions.append(sql[opened[1] : token.start()])
                opened = None
        elif text[0] not in "'\"" or len(text) == 1:
            return None
    if depth or opened is not None or awaiting >= 0:
        return None
    return tuple(expressions)


def _check_constraint_violation(
    connection: sqlite3.Connection, objects: list[tuple[str, str, str | None]]
) -> str | None:
    """Name the first table holding a row that one of its CHECKs refuses.

    SQLite's `integrity_check` and `quick_check` skip CHECK constraints on a
    `mode=ro` connection, so every read-only open re-evaluates them here
    (#1401). `objects` is the schema the application defines, which the caller
    has just proved the store holds; an expression is never taken from the
    store unproved. Only a false result refuses: like SQLite's own enforcement,
    a CHECK whose expression is NULL passes. A table whose CHECKs cannot all be
    extracted is named as well, so an unreadable constraint refuses.
    """
    for kind, name, sql in objects:
        if kind != "table" or sql is None:
            continue
        expressions = _check_expressions(sql)
        if expressions is None:
            return name
        if (
            expressions
            and connection.execute(
                f'SELECT 1 FROM "{name}" WHERE NOT ('
                + ") OR NOT (".join(expressions)
                + ") LIMIT 1"
            ).fetchone()
            is not None
        ):
            return name
    return None


def _validate_schema_against(
    connection: sqlite3.Connection,
    expected_objects: list[tuple[str, str, str | None]],
    documents: ActivityDocuments | None = None,
    *,
    read_only: bool = False,
) -> None:
    objects = connection.execute(
        "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
    ).fetchall()
    if objects != expected_objects:
        raise MetadataReportError("database is unavailable or incompatible")
    if read_only and (
        table := _check_constraint_violation(connection, expected_objects)
    ):
        raise MetadataReportError(
            f"database is unavailable or incompatible: CHECK constraint failed in {table}"
        )
    _validate_schema_contents(connection, documents)


def _validate_schema_contents(
    connection: sqlite3.Connection, documents: ActivityDocuments | None = None
) -> None:
    _validate_schema_contents_without_attention_facts(connection, documents)
    _require_equal_facts(
        connection,
        (
            (
                _ATTENTION_FACT_SOURCE_SQL,
                "SELECT connection_name,library_name,item_id,report_id,destination,"
                "cover_consideration_id,cover_policy_reference,cover_refresh_admits "
                "FROM review_attention_facts",
            ),
        ),
    )


def _validate_schema_contents_without_attention_facts(
    connection: sqlite3.Connection, documents: ActivityDocuments | None = None
) -> None:
    """Validate every content rule #1232's exact predecessor also enforced.

    Each consideration's report is parsed once for both of its checks. Given a
    root's ``documents``, what validates is kept there by its exact bytes for
    the Activity reader (#1231).
    """
    _validate_schema_contents_without_review_field_facts(connection)
    _validate_review_field_facts(connection)
    parse = (
        lru_cache(maxsize=1)(MetadataReport.from_json)
        if documents is None
        else documents.report
    )
    for row in connection.execute(
        "SELECT consideration.document,report.document,report.document_digest,consideration.document_digest,consideration.report_id,consideration.currentness_document FROM cover_considerations consideration LEFT JOIN metadata_reports report ON report.report_id=consideration.report_id"
    ):
        document = expand_cover_consideration(*row[:5], parse_report=parse)
        if cover_consideration_currentness_document(document, parse) != row[5]:
            raise MetadataReportError("database is unavailable or incompatible")


def _validate_schema_contents_without_review_field_facts(
    connection: sqlite3.Connection,
) -> None:
    if connection.execute("PRAGMA foreign_key_check").fetchall() or connection.execute(
        "PRAGMA integrity_check"
    ).fetchone() != ("ok",):
        raise MetadataReportError("database is unavailable or incompatible")
    try:
        validate_description_history(connection)
    except ValueError as error:
        raise MetadataReportError("database is unavailable or incompatible") from error
    _validate_metadata_policy_documents(connection)
    _validate_cover_selection_authorities(connection)
    _validate_field_effect_state(connection)
    validate_preparation_presentation(connection)
    try:
        _validate_owner_settings_content(connection)
    except OwnerSettingsError as error:
        raise MetadataReportError("database is unavailable or incompatible") from error
    for row in connection.execute(
        "SELECT outbox_id,connection_name,library_name,item_id,proposal_set_id,"
        "report_id,review_case_id,checkpoint,outcome,created_at "
        "FROM provider_recovery_outbox"
    ):
        _validate_provider_recovery_outbox_row(row)


def _validate_cover_selection_authorities(connection: sqlite3.Connection) -> None:
    """Require append-only cover authority rows to equal their canonical facts."""
    try:
        selections: dict[str, tuple[str, str, str, dict[str, object]]] = {}
        selection_decision_ids: dict[str, str] = {}
        automatic_supersessions: dict[str, str | None] = {}
        for row in connection.execute(
            "SELECT selection_id,connection_name,library_name,item_id,kind,decision_id,"
            "consideration_id,supersedes_selection_id,occurred_at,document "
            "FROM cover_selection_authorities"
        ):
            document = json.loads(row[9])
            if (
                type(document) is not dict
                or _canonical_json(document) != row[9]
                or document.get("schema_version")
                != (
                    "cover-selection-authority-v2"
                    if row[4] == "owner"
                    else "cover-selection-authority-v1"
                )
                or document.get("selection_id") != row[0]
                or document.get("kind") != row[4]
                or document.get("decision_id") != row[5]
                or document.get("consideration_id") != row[6]
                or document.get("supersedes_selection_id") != row[7]
                or type(document.get("decision_document")) is not str
            ):
                raise ValueError
            decision_document = cast(str, document["decision_document"])
            consideration_row = connection.execute(
                "SELECT consideration.document,consideration.review_case_id,report.document,report.document_digest,consideration.document_digest,consideration.report_id FROM cover_considerations consideration LEFT JOIN metadata_reports report ON report.report_id=consideration.report_id "
                "WHERE consideration.consideration_id=?",
                (row[6],),
            ).fetchone()
            if consideration_row is None:
                raise ValueError
            expanded_document = expand_cover_consideration(
                consideration_row[0], *consideration_row[2:]
            )
            consideration = json.loads(expanded_document)
            case_row = connection.execute(
                "SELECT document FROM review_cases WHERE review_case_id=?",
                (consideration_row[1],),
            ).fetchone()
            if case_row is None:
                raise ValueError
            case = ReviewCase.from_json(case_row[0])
            target = case.target
            expected_artifact = None
            expected_rank = None
            if row[4] == "automatic":
                automatic_decision = CoverAutomaticDecision.from_json(decision_document)
                candidate = next(
                    (
                        value
                        for value in consideration["candidates"]
                        if value.get("reference")
                        == automatic_decision.candidate_reference
                    ),
                    None,
                )
                if (
                    automatic_decision.decision_id != row[5]
                    or automatic_decision.consideration_id != row[6]
                    or automatic_decision.review_case_id != consideration_row[1]
                    or automatic_decision.occurred_at != row[8]
                    or candidate is None
                    or candidate.get("artifact_id") != automatic_decision.artifact_id
                    or candidate.get("incumbent") is not False
                    or candidate.get("selectable") is not True
                    or candidate.get("deterministic_rank_key")
                    != list(automatic_decision.rank_key)
                ):
                    raise ValueError
                expected_artifact = automatic_decision.artifact_id
                expected_rank = list(automatic_decision.rank_key)
            else:
                human_decision = HumanDecision.from_json(decision_document)
                human = connection.execute(
                    "SELECT document FROM human_decisions WHERE decision_id=?",
                    (row[5],),
                ).fetchone()
                if (
                    human_decision.decision_id != row[5]
                    or human_decision.destination != "cover"
                    or human_decision.review_case_id != consideration_row[1]
                    or human_decision.occurred_at != row[8]
                    or human != (decision_document,)
                ):
                    raise ValueError
                candidate = next(
                    (
                        value
                        for value in consideration["candidates"]
                        if (
                            human_decision.outcome == "accept"
                            and human_decision.selected_value
                            == {"kind": "cover", "value": value.get("artifact_id")}
                        )
                        or (
                            human_decision.outcome == "keep_current"
                            and value.get("incumbent") is True
                        )
                    ),
                    None,
                )
                if human_decision.outcome == "reject":
                    if (
                        human_decision.selected_value is not None
                        or candidate is not None
                    ):
                        raise ValueError
                elif candidate is None:
                    raise ValueError
                if candidate is not None:
                    expected_artifact = candidate.get("artifact_id")
                    expected_rank = candidate.get("deterministic_rank_key")
            expected_selection_id = (
                "cover_selection_"
                + hashlib.sha256(decision_document.encode("utf-8")).hexdigest()
            )
            owner_facts = {}
            if row[4] == "owner":
                confirmation = validate_cover_confirmation(document.get("confirmation"))
                if confirmation["expected_selection_id"] != row[7]:
                    raise ValueError
                if human_decision.outcome == "accept" and (
                    candidate is None
                    or not manual_cover_eligible(candidate)
                    or (
                        candidate["match_state"] == "match_not_confirmed"
                        and confirmation["confirm_unverified_match"] is not True
                    )
                ):
                    raise ValueError
                owner_facts = {
                    "confirmation": confirmation,
                    "observed_identity": None
                    if candidate is None
                    else candidate["identity"],
                    "candidate_reference": None
                    if candidate is None
                    else candidate["reference"],
                    "original_sha256": None
                    if candidate is None
                    else candidate["original_sha256"],
                    "preview_sha256": None
                    if candidate is None
                    else candidate["preview_sha256"],
                    "consideration_digest": hashlib.sha256(
                        expanded_document.encode()
                    ).hexdigest(),
                    "target": dict(case.target),
                }
            expected_document = _canonical_json(
                {
                    **owner_facts,
                    "candidate_artifact_id": expected_artifact,
                    "consideration_id": row[6],
                    "decision_document": decision_document,
                    "decision_id": row[5],
                    "kind": row[4],
                    "rank_key": expected_rank,
                    "schema_version": "cover-selection-authority-v2"
                    if row[4] == "owner"
                    else "cover-selection-authority-v1",
                    "selection_id": expected_selection_id,
                    "supersedes_selection_id": row[7],
                }
            )
            if (
                row[0] != expected_selection_id
                or row[9] != expected_document
                or (row[1], row[2], row[3])
                != (target["connection"], target["library"], target["item"])
            ):
                raise ValueError
            selections[row[0]] = (row[1], row[2], row[3], document)
            selection_decision_ids[row[0]] = row[5]
            if row[4] == "automatic":
                automatic_supersessions[row[0]] = (
                    automatic_decision.supersedes_decision_id
                )
        for selection_id, (
            connection_name,
            library_name,
            item_id,
            document,
        ) in selections.items():
            predecessor = document.get("supersedes_selection_id")
            if predecessor is not None and (
                predecessor not in selections
                or selections[predecessor][:3]
                != (connection_name, library_name, item_id)
                or predecessor == selection_id
            ):
                raise ValueError
            expected_predecessor_decision_id = (
                None if predecessor is None else selection_decision_ids[predecessor]
            )
            if (
                selection_id in automatic_supersessions
                and automatic_supersessions[selection_id]
                != expected_predecessor_decision_id
            ):
                raise ValueError
        for row in connection.execute(
            "SELECT connection_name,library_name,item_id,selection_id "
            "FROM current_cover_selection_authorities"
        ):
            if row[3] not in selections or selections[row[3]][:3] != row[:3]:
                raise ValueError
    except (
        AutomaticDecisionError,
        HumanDecisionError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        raise MetadataReportError("database is unavailable or incompatible") from error


def _validate_review_field_facts(connection: sqlite3.Connection) -> None:
    """Require every reconstructible browse fact to equal its canonical JSON."""
    identity = connection.execute(
        "SELECT unicode_version FROM browse_fact_identity"
    ).fetchall()
    if len(identity) != 1:
        raise MetadataReportError("database is unavailable or incompatible")
    if identity[0][0] != unicodedata.unidata_version:
        raise MetadataReportError(
            "database browse facts were folded under a different Unicode version; "
            "a clean rebuild is required"
        )
    review_expected = (
        "SELECT rc.review_case_id,"
        + _review_case_field_fact_values_sql("rc.document")
        + " "
        "FROM review_cases rc,json_each(rc.document,'$.results') result"
    )
    review_actual = (
        "SELECT review_case_id,destination,unresolved_choice,"
        "manual_bibliographic_attention,has_owner_decision_actions FROM review_case_field_facts"
    )
    shadow_expected = (
        "SELECT shadow.outcome_set_id,shadow.review_case_id,"
        + _SHADOW_OUTCOME_FIELD_FACT_VALUES_SQL
        + " FROM "
        "shadow_decision_outcomes shadow,json_each(shadow.document,'$.outcomes') outcome"
    )
    shadow_actual = (
        "SELECT outcome_set_id,review_case_id,destination,disposition,"
        "automatic_destination,policy_category FROM shadow_outcome_field_facts"
    )
    # Browse facts are compared only for the rows Library, Attention and search
    # can select, through the statements' own FROM fragments, and for the
    # staged lineage pointers the preparation pipeline copies into a package
    # without a check. Unselected history stays isolated until a read selects
    # it (#1199). Each set is materialized once per statement, not once per side.
    library = _CURRENT_LIBRARY_FROM_SQL + " WHERE COALESCE(c.outcome,'')<>'tombstoned'"
    staged_case = (
        " FROM current_review_case_lineages staged "
        "JOIN review_cases rc ON rc.review_case_id=staged.review_case_id"
    )
    participating = (
        "WITH participating_reports(id) AS MATERIALIZED (SELECT mr.report_id "
        + library
        + " UNION SELECT mr.report_id "
        + _CURRENT_REVIEW_FROM_SQL
        + " UNION SELECT report_id FROM current_review_case_lineages"
        " UNION SELECT rc.report_id" + staged_case + " UNION SELECT report_id "
        "FROM current_metadata_report_lineages"
        "),participating_proposals(id) AS MATERIALIZED (SELECT rc.proposal_set_id "
        + library
        + " UNION SELECT rc.proposal_set_id "
        + _CURRENT_REVIEW_FROM_SQL
        + " UNION SELECT proposal_set_id FROM current_review_case_lineages"
        " UNION SELECT rc.proposal_set_id" + staged_case + ") "
    )
    _require_equal_facts(
        connection,
        (
            (review_expected, review_actual),
            (shadow_expected, shadow_actual),
            *_report_browse_fact_pairs(" IN (SELECT id FROM participating_reports)"),
            _proposal_browse_fact_pair(" IN (SELECT id FROM participating_proposals)"),
        ),
        participating,
    )


def _report_browse_fact_pairs(key: str) -> tuple[tuple[str, str], ...]:
    """A report's fact and author pairs, both sides restricted by `key`."""
    return (
        (
            "SELECT mr.report_id,"
            + _report_browse_fact_values_sql("mr.document")
            + " FROM metadata_reports mr WHERE mr.report_id"
            + key,
            "SELECT report_id,title_key,format,format_key,provider_limited,"
            "provider_match_state FROM metadata_report_browse_facts WHERE report_id"
            + key,
        ),
        (
            "SELECT mr.report_id,"
            + _report_browse_author_values_sql("metadata_reports mr,", "mr.document")
            + " AND mr.report_id"
            + key,
            "SELECT report_id,author_key FROM metadata_report_browse_authors "
            "WHERE report_id" + key,
        ),
    )


def _proposal_browse_fact_pair(key: str) -> tuple[str, str]:
    """A proposal's fact pair, both sides restricted by `key`."""
    return (
        "SELECT proposal_set_id,"
        + _proposal_browse_fact_value_sql("document")
        + " FROM proposal_sets WHERE proposal_set_id"
        + key,
        "SELECT proposal_set_id,resolved_policy_id FROM proposal_set_browse_facts "
        "WHERE proposal_set_id" + key,
    )


def _require_equal_facts(
    connection: sqlite3.Connection,
    pairs: tuple[tuple[str, str], ...],
    prefix: str = "",
    parameters: dict[str, str] | None = None,
) -> None:
    """Refuse unless each pair's two sides hold exactly the same rows.

    Open runs it over every participating row; publication and admission run
    it over the one report or proposal they make current again (#1199).
    """
    for expected, actual in pairs:
        mismatch = connection.execute(
            prefix + "SELECT 1 FROM (" + expected + " EXCEPT " + actual + ") "
            "UNION ALL SELECT 1 FROM (" + actual + " EXCEPT " + expected + ") LIMIT 1",
            parameters or {},
        ).fetchone()
        if mismatch is not None:
            raise MetadataReportError("database is unavailable or incompatible")


def _validate_schema(
    connection: sqlite3.Connection,
    documents: ActivityDocuments | None = None,
    *,
    read_only: bool = False,
) -> None:
    """Prove the exact current schema; `read_only` marks a `mode=ro` connection."""
    _validate_schema_against(
        connection, _expected_schema_objects(), documents, read_only=read_only
    )


def _validate_metadata_policy_documents(connection: sqlite3.Connection) -> None:
    """Accept only canonical documents for the exact current policy schema."""
    try:
        drafts = connection.execute(
            "SELECT policy_id,document FROM metadata_policy_drafts"
        ).fetchall()
        if not drafts:
            raise MetadataPolicyError("metadata policy state is empty")
        for policy_id, document in drafts:
            profile = MetadataPolicyProfile.from_data(json.loads(document))
            if profile.policy_id != policy_id or profile.canonical_json != document:
                raise MetadataPolicyError("metadata policy draft is invalid")
        for generation, library, resolved_id, document in connection.execute(
            "SELECT generation,library_name,resolved_policy_id,document "
            "FROM metadata_policy_resolutions"
        ):
            resolved = ResolvedMetadataPolicy.from_data(json.loads(document))
            if (
                type(generation) is not int
                or generation < 1
                or resolved.library != library
                or resolved.resolved_id != resolved_id
                or resolved.canonical_json != document
            ):
                raise MetadataPolicyError("metadata policy resolution is invalid")
    except (
        json.JSONDecodeError,
        MetadataPolicyError,
        TypeError,
        ValueError,
        UnicodeError,
        sqlite3.Error,
    ) as error:
        raise MetadataReportError("database is unavailable or incompatible") from error


def _validate_report_row(row: tuple[object, ...]) -> str:
    _validated_report_row(row)
    return cast(str, row[8])


def _validated_report_row(
    row: tuple[object, ...],
    parse_report: Callable[[str], MetadataReport] | None = None,
) -> MetadataReport:
    """Return the typed report only after the complete persisted-row contract."""
    if len(row) != 9:
        raise MetadataReportError("persisted report is invalid")
    (
        report_id,
        connection,
        library,
        item,
        file_identity,
        content_digest,
        schema,
        digest,
        document,
    ) = row
    if (
        type(report_id) is not str
        or type(document) is not str
        or schema != REPORT_SCHEMA
        or type(digest) is not str
        or hashlib.sha256(document.encode("utf-8")).hexdigest() != digest
    ):
        raise MetadataReportError("persisted report is invalid")
    try:
        report = (parse_report or MetadataReport.from_json)(document)
    except ReportValueError as error:
        raise MetadataReportError("persisted report is invalid") from error
    if (
        report.report_id,
        report.target.connection,
        report.target.library,
        report.target.item,
        report.file.identity,
        report.file.content_digest,
    ) != (report_id, connection, library, item, file_identity, content_digest):
        raise MetadataReportError("persisted report envelope disagrees")
    return report


def _validate_review_browse_row(
    row: object, policy_destinations: dict[str, str] | None = None
) -> tuple[str, str]:
    if (
        type(row) not in {tuple, sqlite3.Row}
        or len(row) != 2  # type: ignore[arg-type]
        or type(row[0]) is not str  # type: ignore[index]
        or type(row[1]) is not str  # type: ignore[index]
    ):
        raise MetadataReportError("review browse row is invalid")
    case = ReviewCase.from_json(row[0])  # type: ignore[index]
    report = MetadataReport.from_json(row[1])  # type: ignore[index]
    if (
        case.report_id != report.report_id
        or case.target != report.target.to_data()
        or case.target_fingerprint != report.target.fingerprint
        or case.file_identity != report.file.identity
        or tuple(
            value.to_data()
            for value in evaluate_report(
                report,
                ProposalSet.from_json(case.proposal_document).resolved_policy_id,
                policy_destinations,
            ).results
        )
        != case.results
    ):
        raise MetadataReportError("review browse row disagrees")
    return case.to_json(), report.to_json()


def _provider_recovery_outbox_id(
    connection: str,
    library: str,
    item: str,
    proposal_id: str | None,
    report_id: str,
    case_id: str | None,
    checkpoint: int | None,
    outcome: str,
) -> str:
    document = _canonical_json(
        {
            "case_id": case_id,
            "checkpoint": checkpoint,
            "connection": connection,
            "item": item,
            "library": library,
            "outcome": outcome,
            "proposal_id": proposal_id,
            "report_id": report_id,
        }
    )
    return "recovery_outbox_" + hashlib.sha256(document.encode("utf-8")).hexdigest()


def _validate_provider_recovery_outbox_row(row: tuple[object, ...]) -> None:
    if (
        len(row) != 10
        or not all(
            type(row[index]) is str and row[index] for index in (0, 1, 2, 3, 5, 8)
        )
        or (row[4] is not None and (type(row[4]) is not str or not row[4]))
        or (row[6] is not None and (type(row[6]) is not str or not row[6]))
        or (row[7] is not None and (type(row[7]) is not int or row[7] < 1))
        or type(row[9]) is not int
        or row[9] < 0
        # Short-circuit evaluation: reaching this disjunct means every column
        # test above passed, fixing each column's exact type.
        or row[0]
        != _provider_recovery_outbox_id(
            cast(str, row[1]),
            cast(str, row[2]),
            cast(str, row[3]),
            row[4],
            cast(str, row[5]),
            row[6],
            row[7],
            cast(str, row[8]),
        )
    ):
        raise MetadataReportError("provider recovery outbox is invalid")
