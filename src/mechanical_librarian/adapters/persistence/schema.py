"""The shared SQLite schema spine: DDL, filesystem binding, and open-time validation.

This module owns the exact submitted schema text, the filesystem-authority
binding that keeps one connection on one inode, and the total validation that
runs whenever a database is opened.

`sqlite_master` stores submitted DDL text verbatim, so no statement below may be
reflowed and none may be reordered: either silently changes the persisted
schema, and `_validate_schema` sorts before comparing, so it will not catch a
reordering. `E501` is deliberately unenforced here for that reason.

Aggregate modules import from this module; this module imports from no aggregate
module, so the spine stays acyclic.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.parse import quote

from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.adapters.persistence.field_effect_validation import (
    _validate_field_effect_state,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.metadata_policy import (
    MetadataPolicyError,
    MetadataPolicyProfile,
    ResolvedMetadataPolicy,
)
from mechanical_librarian.domain.proposal_set import POLICY_VERSION

if sys.platform.startswith("linux"):
    import fcntl
else:
    fcntl = None

if TYPE_CHECKING:
    from mechanical_librarian.adapters.persistence.metadata_reports import (
        SQLiteMetadataReportRepository,
        SQLiteShadowPreviewRepository,
    )


_REPORTS_SQL = (
    "CREATE TABLE metadata_reports ("
    "report_id TEXT PRIMARY KEY NOT NULL CHECK (report_id GLOB 'report_[0-9a-f]*' AND length(report_id) = 71), "
    "connection_name TEXT NOT NULL, library_name TEXT NOT NULL, item_id TEXT NOT NULL, "
    "file_identity TEXT NOT NULL CHECK (length(file_identity) = 64 AND file_identity NOT GLOB '*[^0-9a-f]*'), "
    "content_digest TEXT NOT NULL CHECK (length(content_digest) = 64 AND content_digest NOT GLOB '*[^0-9a-f]*'), "
    "schema_version TEXT NOT NULL CHECK (schema_version = 'metadata-report-v26'), "
    "document_digest TEXT NOT NULL CHECK (length(document_digest) = 64 AND document_digest NOT GLOB '*[^0-9a-f]*'), "
    "document TEXT NOT NULL CHECK (typeof(document) = 'text' AND length(CAST(document AS BLOB)) BETWEEN 2 AND 2097152), "
    "UNIQUE (connection_name, library_name, item_id, file_identity, document_digest))"
)
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
    "policy_version TEXT NOT NULL CHECK (policy_version = '"
    + POLICY_VERSION
    + "'), "
    "document TEXT NOT NULL CHECK (typeof(document) = 'text' AND length(CAST(document AS BLOB)) BETWEEN 2 AND 2097152))"
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
    "outcome TEXT NOT NULL CHECK(outcome IN ('report_only','admitted','superseded','no_case','retired')), "
    "CHECK ((outcome='report_only' AND proposal_set_id IS NULL AND review_case_id IS NULL AND checkpoint IS NULL) "
    "OR (outcome IN ('admitted','superseded') AND proposal_set_id IS NOT NULL AND review_case_id IS NOT NULL AND checkpoint IS NOT NULL) "
    "OR (outcome IN ('no_case','retired') AND proposal_set_id IS NOT NULL AND review_case_id IS NULL AND checkpoint IS NOT NULL)), "
    "PRIMARY KEY(connection_name,library_name,item_id))"
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
    "last_library TEXT CHECK(last_library IS NULL OR (typeof(last_library)='text' AND length(CAST(last_library AS BLOB)) BETWEEN 1 AND 64 AND last_library NOT GLOB '*[^a-z0-9_-]*' AND substr(last_library,1,1) GLOB '[a-z]')), circuit_failures INTEGER NOT NULL CHECK(circuit_failures BETWEEN 0 AND 3), "
    "circuit_open_until INTEGER NOT NULL CHECK(circuit_open_until>=0), updated_at INTEGER NOT NULL CHECK(updated_at>=0))"
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
    "state TEXT NOT NULL CHECK(state IN ('queued','running','complete','failed','stale')), attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt BETWEEN 0 AND 3), recovery_attempt INTEGER NOT NULL DEFAULT 0 CHECK(recovery_attempt BETWEEN 0 AND 3), retry_not_before INTEGER NOT NULL DEFAULT 0 CHECK(retry_not_before>=0), "
    "report_id TEXT REFERENCES metadata_reports(report_id), recovery_source_report_id TEXT REFERENCES metadata_reports(report_id), proposal_set_id TEXT REFERENCES proposal_sets(proposal_set_id), review_case_id TEXT REFERENCES review_cases(review_case_id), "
    "failure_code TEXT CHECK(failure_code IS NULL OR failure_code IN ('provider','configuration','authentication','stale','transient','projection','failed','currentness')), "
    "failure_root TEXT CHECK(failure_root IS NULL OR (failure_code='authentication' AND typeof(failure_root)='text' AND length(CAST(failure_root AS BLOB)) BETWEEN 1 AND 64 AND failure_root NOT GLOB '*[^a-z0-9_-]*' AND substr(failure_root,1,1) GLOB '[a-z]')), "
    "updated_at INTEGER NOT NULL CHECK(updated_at>=0), UNIQUE(scan_id,item_id))"
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
    "ON review_preparation_work(state,library_name,updated_at,ordinal)"
)
_REVIEW_WORK_RECENT_CLAIM_INDEX_SQL = (
    "CREATE INDEX review_preparation_work_recent_claim "
    "ON review_preparation_work(state,library_name,updated_at DESC,ordinal)"
)
_REVIEW_SESSIONS_SQL = (
    "CREATE TABLE review_sessions ("
    "session_digest TEXT PRIMARY KEY NOT NULL CHECK(length(session_digest)=64 AND session_digest NOT GLOB '*[^0-9a-f]*'), "
    "csrf_digest TEXT NOT NULL CHECK(length(csrf_digest)=64 AND csrf_digest NOT GLOB '*[^0-9a-f]*'), "
    "actor TEXT NOT NULL CHECK(actor='owner'), created_at INTEGER NOT NULL CHECK(created_at>=0), "
    "expires_at INTEGER NOT NULL CHECK(expires_at>created_at), revoked_at INTEGER CHECK(revoked_at IS NULL OR revoked_at>=created_at))"
)
_REVIEW_LOGIN_FAILURES_SQL = (
    "CREATE TABLE review_login_failures ("
    "failure_id TEXT PRIMARY KEY NOT NULL CHECK(length(failure_id)=64 AND failure_id NOT GLOB '*[^0-9a-f]*'), "
    "occurred_at INTEGER NOT NULL CHECK(occurred_at>=0))"
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
    "outcome TEXT NOT NULL CHECK(outcome IN ('accept','keep_current','reject')), "
    "selected_value_document TEXT NOT NULL CHECK(typeof(selected_value_document)='text' AND length(CAST(selected_value_document AS BLOB)) BETWEEN 4 AND 32768), "
    "idempotency_digest TEXT NOT NULL UNIQUE CHECK(length(idempotency_digest)=64 AND idempotency_digest NOT GLOB '*[^0-9a-f]*'), "
    "request_digest TEXT NOT NULL CHECK(length(request_digest)=64 AND request_digest NOT GLOB '*[^0-9a-f]*'), "
    "occurred_at INTEGER NOT NULL CHECK(occurred_at>=0), "
    "document TEXT NOT NULL CHECK(typeof(document)='text' AND length(CAST(document AS BLOB)) BETWEEN 2 AND 65536))"
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
    "document TEXT NOT NULL CHECK(typeof(document)='text' AND length(CAST(document AS BLOB)) BETWEEN 2 AND 12582912))"
)
_CURRENT_COVER_CONSIDERATIONS_SQL = (
    "CREATE TABLE current_cover_considerations ("
    "review_case_id TEXT PRIMARY KEY NOT NULL REFERENCES review_cases(review_case_id), "
    "consideration_id TEXT NOT NULL UNIQUE REFERENCES cover_considerations(consideration_id))"
)
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
_FIELD_EFFECT_NO_CHANGES_SQL = (
    "CREATE TABLE field_effect_no_changes ("
    "decision_id TEXT PRIMARY KEY NOT NULL REFERENCES automatic_decisions(decision_id), "
    "review_case_id TEXT NOT NULL REFERENCES review_cases(review_case_id), report_id TEXT NOT NULL REFERENCES metadata_reports(report_id), "
    "connection_name TEXT NOT NULL, library_name TEXT NOT NULL, work_id TEXT NOT NULL, destination TEXT NOT NULL, "
    "target_fingerprint TEXT NOT NULL, target_version TEXT NOT NULL, origin TEXT NOT NULL CHECK(origin='automatic_policy'), "
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
    "AND json_extract(document,'$.schema_version')='shadow-outcome-set-v3'))"
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


class _LinuxDirectoryChain:
    def __init__(
        self,
        names: tuple[str, ...],
        descriptors: tuple[int, ...],
        identities: tuple[tuple[int, int], ...],
    ) -> None:
        self.names = names
        self.descriptors = descriptors
        self.identities = identities
        self.closed = False

    @classmethod
    def open(cls, path: Path) -> "_LinuxDirectoryChain":
        absolute = path.absolute()
        if not absolute.is_absolute():
            raise MetadataReportError("database parent path is invalid")
        names = tuple(absolute.parts[1:])
        if any(name in ("", ".", "..") for name in names):
            raise MetadataReportError("database parent path is invalid")
        descriptors: list[int] = []
        identities: list[tuple[int, int]] = []
        try:
            descriptor = os.open(
                "/",
                os.O_RDONLY
                | os.O_DIRECTORY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            descriptors.append(descriptor)
            information = os.fstat(descriptor)
            identities.append((information.st_dev, information.st_ino))
            for name in names:
                descriptor = os.open(
                    name,
                    os.O_RDONLY
                    | os.O_DIRECTORY
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=descriptors[-1],
                )
                descriptors.append(descriptor)
                information = os.fstat(descriptor)
                if not stat.S_ISDIR(information.st_mode):
                    raise MetadataReportError(
                        "database parent path is invalid"
                    )
                _require_protected_directory_entry(
                    os.fstat(descriptors[-2]), information
                )
                identities.append((information.st_dev, information.st_ino))
            return cls(names, tuple(descriptors), tuple(identities))
        except MetadataReportError:
            raise
        except (OSError, ValueError) as error:
            raise MetadataReportError(
                "database parent path is unavailable"
            ) from error
        finally:
            if len(identities) != len(descriptors):
                for descriptor in reversed(descriptors):
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass

    @property
    def parent_descriptor(self) -> int:
        return self.descriptors[-1]

    def open_current_parent(self) -> int:
        if self.closed:
            raise MetadataReportError(
                "database parent path is unavailable"
            )
        current = -1
        try:
            current = os.open(
                "/",
                os.O_RDONLY
                | os.O_DIRECTORY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            root = os.fstat(current)
            if (root.st_dev, root.st_ino) != self.identities[0]:
                raise MetadataReportError("database parent path changed")
            for index, name in enumerate(self.names):
                descriptor = -1
                try:
                    descriptor = os.open(
                        name,
                        os.O_RDONLY
                        | os.O_DIRECTORY
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=current,
                    )
                    information = os.fstat(descriptor)
                    if (
                        not stat.S_ISDIR(information.st_mode)
                        or (information.st_dev, information.st_ino)
                        != self.identities[index + 1]
                    ):
                        raise MetadataReportError(
                            "database parent path changed"
                        )
                    _require_protected_directory_entry(
                        os.fstat(current), information
                    )
                    os.close(current)
                    current = descriptor
                    descriptor = -1
                finally:
                    if descriptor >= 0:
                        os.close(descriptor)
            result = current
            current = -1
            return result
        except MetadataReportError:
            raise
        except OSError as error:
            raise MetadataReportError(
                "database parent path changed"
            ) from error
        finally:
            if current >= 0:
                os.close(current)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        failure: OSError | None = None
        for descriptor in reversed(self.descriptors):
            try:
                os.close(descriptor)
            except OSError as error:
                failure = failure or error
        if failure is not None:
            raise MetadataReportError(
                "database parent binding cleanup failed"
            ) from failure


class _LinuxDatabaseBinding:
    def __init__(
        self,
        configured_name: str,
        descriptor: int,
        directory_chain: _LinuxDirectoryChain,
        alias_name: str,
        expected_identity: tuple[int, int],
        owns_lock: bool,
        authority: "_LinuxDatabaseBinding | None",
    ) -> None:
        self.configured_name = configured_name
        self.descriptor = descriptor
        self.directory_chain = directory_chain
        self.parent_descriptor = directory_chain.parent_descriptor
        self.alias_name = alias_name
        self.expected_identity = expected_identity
        self.owns_lock = owns_lock
        self.authority = authority
        self.alias_path = Path(
            f"/proc/self/fd/{self.parent_descriptor}/{alias_name}"
        )
        self.closed = False

    @classmethod
    def create(
        cls,
        path: Path,
        descriptor: int,
        authority: object | None = None,
    ) -> "_LinuxDatabaseBinding":
        if fcntl is None:
            raise MetadataReportError("database private binding is unavailable")
        absolute = path.absolute()
        if not absolute.name:
            raise MetadataReportError("database path is invalid")
        directory_chain: _LinuxDirectoryChain | None = None
        alias_name: str | None = None
        locked = False
        succeeded = False
        try:
            directory_chain = _LinuxDirectoryChain.open(absolute.parent)
            parent_descriptor = directory_chain.parent_descriptor
            _require_owner_only_database_parent(
                os.fstat(parent_descriptor)
            )
            expected = os.fstat(descriptor)
            expected_identity = (expected.st_dev, expected.st_ino)
            if authority is None:
                fcntl.flock(parent_descriptor, fcntl.LOCK_EX)
                locked = True
            elif type(authority) is not cls:
                raise MetadataReportError("database authority changed")
            else:
                authority.require_current()
                if (
                    authority.closed
                    or authority.configured_name != absolute.name
                    or authority.expected_identity != expected_identity
                    or authority.directory_chain.identities[-1]
                    != directory_chain.identities[-1]
                ):
                    raise MetadataReportError("database authority changed")
            _require_named_identity(
                parent_descriptor, absolute.name, expected_identity
            )
            _reject_database_sidecars_in_directory(
                parent_descriptor, absolute.name
            )
            for _ in range(8):
                candidate = (
                    f".metadata-report-binding-{secrets.token_hex(16)}.sqlite"
                )
                try:
                    os.link(
                        absolute.name,
                        candidate,
                        src_dir_fd=parent_descriptor,
                        dst_dir_fd=parent_descriptor,
                        follow_symlinks=False,
                    )
                except FileExistsError:
                    continue
                alias_name = candidate
                break
            if alias_name is None:
                raise MetadataReportError(
                    "database private binding is unavailable"
                )
            _require_named_identity(
                parent_descriptor, alias_name, expected_identity
            )
            binding = cls(
                absolute.name,
                descriptor,
                directory_chain,
                alias_name,
                expected_identity,
                locked,
                authority if type(authority) is cls else None,
            )
            succeeded = True
            return binding
        except MetadataReportError:
            raise
        except (OSError, ValueError) as error:
            raise MetadataReportError(
                "database private binding is unavailable"
            ) from error
        finally:
            if (
                alias_name is not None
                and directory_chain is not None
                and not succeeded
            ):
                try:
                    os.unlink(
                        alias_name,
                        dir_fd=directory_chain.parent_descriptor,
                    )
                except OSError:
                    pass
            if not succeeded:
                if locked and directory_chain is not None:
                    try:
                        fcntl.flock(
                            directory_chain.parent_descriptor,
                            fcntl.LOCK_UN,
                        )
                    except OSError:
                        pass
                if directory_chain is not None:
                    directory_chain.close()

    def require_current(self) -> None:
        if self.closed:
            raise MetadataReportError("database authority changed")
        if self.authority is not None:
            self.authority.require_current()
        current_parent = self.directory_chain.open_current_parent()
        try:
            _require_owner_only_database_parent(
                os.fstat(current_parent)
            )
            _require_named_identity(
                current_parent,
                self.configured_name,
                self.expected_identity,
            )
            _reject_database_sidecars_in_directory(
                current_parent, self.configured_name
            )
        finally:
            os.close(current_parent)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        failure: OSError | None = None
        for suffix in ("-journal", "-wal", "-shm"):
            name = f"{self.alias_name}{suffix}"
            try:
                information = os.stat(
                    name,
                    dir_fd=self.parent_descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                continue
            except OSError as error:
                failure = failure or error
                continue
            if (
                not stat.S_ISREG(information.st_mode)
                or information.st_uid != os.geteuid()
            ):
                failure = failure or OSError("unsafe private database artifact")
                continue
            try:
                os.unlink(name, dir_fd=self.parent_descriptor)
            except OSError as error:
                failure = failure or error
        try:
            _require_named_identity(
                self.parent_descriptor,
                self.alias_name,
                self.expected_identity,
            )
            os.unlink(self.alias_name, dir_fd=self.parent_descriptor)
        except (OSError, MetadataReportError) as error:
            failure = failure or OSError(str(error))
        try:
            os.close(self.descriptor)
        except OSError as error:
            failure = failure or error
        if self.owns_lock:
            try:
                fcntl.flock(self.parent_descriptor, fcntl.LOCK_UN)
            except OSError as error:
                failure = failure or error
        try:
            self.directory_chain.close()
        except MetadataReportError as error:
            failure = failure or OSError(str(error))
        if failure is not None:
            raise MetadataReportError(
                "database private binding cleanup failed"
            ) from failure


def open_shadow_preview_repository(path: Path) -> SQLiteShadowPreviewRepository:
    """Open an exact-current SQLite database without create or write authority."""
    # Deliberately local: the spine must stay importable without the repository
    # module, which imports the schema and binding from here. This inverts once
    # the composition root owns repository construction.
    from mechanical_librarian.adapters.persistence.metadata_reports import (
        SQLiteShadowPreviewRepository,
    )

    descriptor = -1
    connection: sqlite3.Connection | None = None
    try:
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        if not nofollow:
            raise MetadataReportError("database no-follow access is unavailable")
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | nofollow,
        )
        information = os.fstat(descriptor)
        if not stat.S_ISREG(information.st_mode):
            raise MetadataReportError("database path is invalid")
        identity = (
            information.st_dev,
            information.st_ino,
            information.st_size,
            information.st_mtime_ns,
            information.st_ctime_ns,
        )
        _validate_delete_mode_header(descriptor)
        _reject_database_sidecars(path)
        _validate_existing_read_only(path, descriptor)
        _reject_database_sidecars(path)
        absolute = str(path.absolute())
        uri = f"file:{quote(absolute, safe='/')}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True, isolation_level=None)
        _validate_connection_binding(connection, path, descriptor)
        connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA query_only").fetchone() != (1,):
            raise MetadataReportError("database query-only mode is unavailable")
        if connection.execute("PRAGMA database_list").fetchall() == []:
            raise MetadataReportError("database connection identity is unavailable")
        _validate_schema(connection)
        _reject_database_sidecars(path)
        current = path.lstat()
        if (
            not stat.S_ISREG(current.st_mode)
            or (
                current.st_dev,
                current.st_ino,
                current.st_size,
                current.st_mtime_ns,
                current.st_ctime_ns,
            )
            != identity
        ):
            raise MetadataReportError("database authority changed")
        repository = SQLiteShadowPreviewRepository(
            connection, descriptor, path, identity
        )
        connection = None
        descriptor = -1
        return repository
    except MetadataReportError:
        raise
    except (OSError, sqlite3.Error, ValueError) as error:
        raise MetadataReportError("database is unavailable or incompatible") from error
    finally:
        if connection is not None:
            connection.close()
        if descriptor >= 0:
            os.close(descriptor)


def open_repository(path: Path, *, create: bool = True, expected_identity: tuple[int, int] | None = None, cross_thread: bool = False, authority: object | None = None) -> SQLiteMetadataReportRepository:
    # Deliberately local: see open_shadow_preview_repository.
    from mechanical_librarian.adapters.persistence.metadata_reports import (
        SQLiteMetadataReportRepository,
    )

    created = False
    descriptor = -1
    created_identity: tuple[int, int] | None = None
    connection: sqlite3.Connection | None = None
    binding: _LinuxDatabaseBinding | None = None
    try:
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        if not nofollow:
            raise MetadataReportError("database no-follow access is unavailable")
        flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | nofollow
        if os.path.lexists(path):
            descriptor = os.open(path, flags)
            information = os.fstat(descriptor)
            if expected_identity is not None and (information.st_dev, information.st_ino) != expected_identity:
                raise MetadataReportError("database authority changed")
            if not stat.S_ISREG(information.st_mode):
                raise MetadataReportError("database path is invalid")
            _validate_delete_mode_header(descriptor)
            if sys.platform.startswith("linux"):
                binding = _LinuxDatabaseBinding.create(path, descriptor, authority)
                descriptor = -1
                _validate_existing_read_only(
                    binding.alias_path, binding.descriptor
                )
                binding.require_current()
            else:
                _reject_database_sidecars(path)
                _validate_existing_read_only(path, descriptor)
                _reject_database_sidecars(path)
        elif expected_identity is not None:
            raise MetadataReportError("database authority changed")
        elif create:
            descriptor = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
            information = os.fstat(descriptor)
            if not stat.S_ISREG(information.st_mode):
                raise MetadataReportError("database path is invalid")
            created = True
            created_identity = (information.st_dev, information.st_ino)
            if sys.platform.startswith("linux"):
                binding = _LinuxDatabaseBinding.create(path, descriptor, authority)
                descriptor = -1
        else:
            raise MetadataReportError("database is unavailable")
        database_path = binding.alias_path if binding is not None else path
        database_descriptor = (
            binding.descriptor if binding is not None else descriptor
        )
        connection = _connect_bound(
            database_path,
            database_descriptor,
            binding.require_current if binding is not None else None,
            cross_thread=cross_thread,
        )
        connection.create_function(
            "ml_casefold",
            1,
            _review_casefold,
            deterministic=True,
        )
        if binding is not None:
            binding.require_current()
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA busy_timeout=5000")
        if created:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(_REPORTS_SQL)
            connection.execute(_CURRENT_SQL)
            connection.execute(_CURRENT_LINEAGE_SQL)
            connection.execute(_COVER_REFERENCES_SQL)
            connection.execute(_COVER_ARTIFACT_INDEX_SQL)
            connection.execute(_COVER_PREVIEW_INDEX_SQL)
            connection.execute(_PROPOSAL_SETS_SQL)
            connection.execute(_METADATA_POLICY_DRAFTS_SQL)
            builtin_policy = MetadataPolicyProfile.builtin()
            connection.execute(
                "INSERT INTO metadata_policy_drafts(policy_id,document) VALUES(?,?)",
                (builtin_policy.policy_id, builtin_policy.canonical_json),
            )
            connection.execute(_METADATA_POLICY_STATE_SQL)
            connection.execute(
                "INSERT INTO metadata_policy_state(singleton,active_policy_id,generation,activated_at,posture) VALUES(1,?,1,0,'automatic')",
                (builtin_policy.policy_id,),
            )
            connection.execute(_METADATA_POLICY_RESOLUTIONS_SQL)
            connection.execute(_METADATA_POLICY_TRANSITIONS_SQL)
            bootstrap_transition_id = "policy_transition_" + hashlib.sha256(
                ("automatic-bootstrap-v1:" + builtin_policy.policy_id).encode("utf-8")
            ).hexdigest()
            connection.execute(
                "INSERT INTO metadata_policy_transitions(transition_id,from_policy_id,to_policy_id,generation,state,total,acknowledged,created_at,updated_at) VALUES(?,?,?,1,'completed',0,0,0,0)",
                (bootstrap_transition_id, builtin_policy.policy_id, builtin_policy.policy_id),
            )
            connection.execute(_REVIEW_CASES_SQL)
            connection.execute(_REVIEW_POINTERS_SQL)
            connection.execute(_CURRENT_REVIEW_PACKAGES_SQL)
            connection.execute(_PROVIDER_RECOVERY_OUTBOX_SQL)
            connection.execute(_REVIEW_TRANSITIONS_SQL)
            connection.execute(_REVIEW_SCANS_SQL)
            connection.execute(_REVIEW_CONTROL_SQL)
            connection.execute(
                "INSERT INTO review_preparation_control(singleton,paused,claim_sequence,last_library,circuit_failures,circuit_open_until,updated_at) VALUES(1,0,0,NULL,0,0,0)"
            )
            connection.execute(_REVIEW_CONNECTION_CONDITIONS_SQL)
            connection.execute(_REVIEW_WORK_SQL)
            connection.execute(_METADATA_POLICY_TRANSITION_ITEMS_SQL)
            connection.execute(_REVIEW_ABSENCES_SQL)
            connection.execute(_REVIEW_WORK_AGED_CLAIM_INDEX_SQL)
            connection.execute(_REVIEW_WORK_RECENT_CLAIM_INDEX_SQL)
            connection.execute(_REVIEW_SESSIONS_SQL)
            connection.execute(_REVIEW_LOGIN_FAILURES_SQL)
            connection.execute(_HUMAN_DECISIONS_SQL)
            connection.execute(_HUMAN_DECISION_EVENTS_SQL)
            connection.execute(_COVER_CONSIDERATIONS_SQL)
            connection.execute(_CURRENT_COVER_CONSIDERATIONS_SQL)
            connection.execute(_COVER_CONSIDERATION_EVENTS_SQL)
            connection.execute(_AUTOMATIC_DECISIONS_SQL)
            connection.execute(_AUTOMATIC_SAFETY_SQL)
            connection.execute("INSERT INTO automatic_safety_state(singleton,paused,global_suspension,category_suspensions_document,updated_at) VALUES(1,0,NULL,'[]',0)")
            connection.execute(_FIELD_EFFECT_CONTROL_SQL)
            connection.execute("INSERT INTO field_effect_control(singleton,paused,updated_at) VALUES(1,0,0)")
            connection.execute(_FIELD_EFFECT_SUSPENSIONS_SQL)
            connection.execute(_FIELD_EFFECT_AUTHORITIES_SQL)
            connection.execute(_FIELD_EFFECT_CONSENTS_SQL)
            connection.execute(_FIELD_EFFECT_MANUAL_HOLDS_SQL)
            connection.execute(_FIELD_EFFECTS_SQL)
            connection.execute(_FIELD_EFFECT_EVENTS_SQL)
            connection.execute(_FIELD_EFFECT_NO_CHANGES_SQL)
            connection.execute(_FIELD_EFFECT_ACTIONS_SQL)
            connection.execute(_FIELD_EFFECT_CLEANUPS_SQL)
            connection.execute(_SHADOW_DECISION_OUTCOMES_SQL)
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
            connection.commit()
        _validate_schema(connection)
        if binding is None:
            os.close(descriptor)
            descriptor = -1
        repository = SQLiteMetadataReportRepository(connection, binding, (information.st_dev, information.st_ino))
        connection = None
        binding = None
        return repository
    except MetadataReportError:
        if connection is not None:
            connection.close()
        if binding is not None:
            try:
                binding.close()
            except MetadataReportError:
                pass
        if created and created_identity is not None:
            _unlink_created(path, created_identity)
        raise
    except (OSError, sqlite3.Error) as error:
        if connection is not None:
            connection.close()
        if binding is not None:
            try:
                binding.close()
            except MetadataReportError:
                pass
        if created and created_identity is not None:
            _unlink_created(path, created_identity)
        raise MetadataReportError("database is unavailable or incompatible") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _validate_existing_read_only(path: Path, descriptor: int) -> None:
    absolute = str(path.absolute())
    uri = f"file:{quote(absolute, safe='/')}?mode=ro"
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(uri, uri=True, isolation_level=None)
        _validate_connection_binding(connection, path, descriptor)
        connection.execute("PRAGMA schema_version").fetchone()
        _validate_schema(connection)
    finally:
        if connection is not None:
            connection.close()


def _connect_bound(
    path: Path,
    descriptor: int,
    preconnect: Callable[[], None] | None = None,
    *,
    cross_thread: bool = False,
) -> sqlite3.Connection:
    absolute = str(path.absolute())
    uri = f"file:{quote(absolute, safe='/')}?mode=rw"
    _reject_database_sidecars(path)
    if preconnect is not None:
        preconnect()
    connection = sqlite3.connect(
        uri,
        uri=True,
        isolation_level=None,
        check_same_thread=not cross_thread,
    )
    try:
        _validate_connection_binding(connection, path, descriptor)
        return connection
    except Exception:
        connection.close()
        raise


def _reject_database_sidecars(path: Path) -> None:
    for suffix in ("-journal", "-wal", "-shm"):
        sidecar = Path(f"{path}{suffix}")
        try:
            sidecar.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise MetadataReportError(
                "database recovery state is unavailable"
            ) from error
        raise MetadataReportError("database recovery state is unsupported")


def _reject_database_sidecars_in_directory(
    parent_descriptor: int, basename: str
) -> None:
    for suffix in ("-journal", "-wal", "-shm"):
        try:
            os.stat(
                f"{basename}{suffix}",
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            continue
        except OSError as error:
            raise MetadataReportError(
                "database recovery state is unavailable"
            ) from error
        raise MetadataReportError("database recovery state is unsupported")


def _require_named_identity(
    parent_descriptor: int,
    name: str,
    expected_identity: tuple[int, int],
) -> None:
    try:
        information = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except OSError as error:
        raise MetadataReportError(
            "database pathname changed while opening"
        ) from error
    if not stat.S_ISREG(information.st_mode) or (
        information.st_dev,
        information.st_ino,
    ) != expected_identity:
        raise MetadataReportError("database pathname changed while opening")


def _require_protected_directory_entry(
    parent: os.stat_result, entry: os.stat_result
) -> None:
    effective_user = os.geteuid()
    if parent.st_uid not in (0, effective_user):
        raise MetadataReportError(
            "database parent path is not protected"
        )
    writable_by_others = parent.st_mode & 0o022
    sticky = parent.st_mode & stat.S_ISVTX
    if writable_by_others and (
        not sticky or entry.st_uid != effective_user
    ):
        raise MetadataReportError(
            "database parent path is not protected"
        )


def _require_owner_only_database_parent(
    information: os.stat_result,
) -> None:
    if (
        not stat.S_ISDIR(information.st_mode)
        or information.st_uid != os.geteuid()
        or information.st_mode & 0o077
    ):
        raise MetadataReportError(
            "database parent directory is not owner-only"
        )


def _validate_delete_mode_header(descriptor: int) -> None:
    try:
        header = os.pread(descriptor, 20, 0)
    except OSError as error:
        raise MetadataReportError("database header is unavailable") from error
    if (
        len(header) != 20
        or header[:16] != b"SQLite format 3\x00"
        or header[18:20] != b"\x01\x01"
    ):
        raise MetadataReportError("database recovery mode is unsupported")


def _validate_connection_binding(
    connection: sqlite3.Connection,
    path: Path,
    descriptor: int,
) -> None:
    expected = os.fstat(descriptor)
    expected_identity = (expected.st_dev, expected.st_ino)
    rows = connection.execute("PRAGMA database_list").fetchall()
    if (
        len(rows) != 1
        or len(rows[0]) != 3
        or rows[0][0] != 0
        or rows[0][1] != "main"
        or type(rows[0][2]) is not str
        or not rows[0][2]
        or not os.path.isabs(rows[0][2])
    ):
        raise MetadataReportError("database connection identity is unavailable")
    verification = -1
    try:
        verification = os.open(
            rows[0][2],
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        connected = os.fstat(verification)
        if not stat.S_ISREG(connected.st_mode) or (
            connected.st_dev,
            connected.st_ino,
        ) != expected_identity:
            raise MetadataReportError("database pathname changed while opening")
    except OSError as error:
        raise MetadataReportError("database pathname changed while opening") from error
    finally:
        if verification >= 0:
            os.close(verification)
    try:
        requested = path.lstat()
    except OSError as error:
        raise MetadataReportError("database pathname changed while opening") from error
    if not stat.S_ISREG(requested.st_mode) or (
        requested.st_dev,
        requested.st_ino,
    ) != expected_identity:
        raise MetadataReportError("database pathname changed while opening")
def _unlink_created(path: Path, expected: tuple[int, int]) -> None:
    try:
        information = path.lstat()
        if stat.S_ISREG(information.st_mode) and (
            information.st_dev,
            information.st_ino,
        ) == expected:
            path.unlink()
    except OSError:
        pass


def _validate_schema(connection: sqlite3.Connection) -> None:
    objects = connection.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
    expected_objects = sorted((
        ("index", "sqlite_autoindex_current_metadata_reports_1", None),
        ("index", "sqlite_autoindex_current_metadata_report_lineages_1", None),
        ("index", "sqlite_autoindex_current_review_case_lineages_1", None),
        ("index", "sqlite_autoindex_current_review_packages_1", None),
        ("index", "sqlite_autoindex_provider_recovery_outbox_1", None),
        ("index", "sqlite_autoindex_metadata_report_cover_references_1", None),
        ("index", "metadata_report_cover_references_artifact_id", _COVER_ARTIFACT_INDEX_SQL),
        ("index", "metadata_report_cover_references_preview_digest", _COVER_PREVIEW_INDEX_SQL),
        ("index", "sqlite_autoindex_human_decision_events_1", None),
        ("index", "sqlite_autoindex_human_decision_events_2", None),
        ("index", "sqlite_autoindex_human_decisions_1", None),
        ("index", "sqlite_autoindex_human_decisions_2", None),
        ("index", "sqlite_autoindex_cover_considerations_1", None),
        ("index", "sqlite_autoindex_current_cover_considerations_1", None),
        ("index", "sqlite_autoindex_current_cover_considerations_2", None),
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
        ("index", "sqlite_autoindex_metadata_reports_1", None),
        ("index", "sqlite_autoindex_metadata_reports_2", None),
        ("index", "sqlite_autoindex_metadata_policy_drafts_1", None),
        ("index", "sqlite_autoindex_metadata_policy_resolutions_1", None),
        ("index", "sqlite_autoindex_metadata_policy_transitions_1", None),
        ("index", "sqlite_autoindex_metadata_policy_transitions_2", None),
        ("index", "sqlite_autoindex_metadata_policy_transition_items_1", None),
        ("index", "sqlite_autoindex_proposal_sets_1", None),
        ("index", "sqlite_autoindex_review_cases_1", None),
        ("index", "sqlite_autoindex_review_case_transitions_1", None),
        ("index", "sqlite_autoindex_review_case_transitions_2", None),
        ("index", "sqlite_autoindex_review_preparation_absences_1", None),
        ("index", "sqlite_autoindex_review_preparation_connection_conditions_1", None),
        ("index", "sqlite_autoindex_review_preparation_scans_1", None),
        ("index", "sqlite_autoindex_review_preparation_work_1", None),
        ("index", "sqlite_autoindex_review_preparation_work_2", None),
        (
            "index",
            "review_preparation_work_aged_claim",
            _REVIEW_WORK_AGED_CLAIM_INDEX_SQL,
        ),
        (
            "index",
            "review_preparation_work_recent_claim",
            _REVIEW_WORK_RECENT_CLAIM_INDEX_SQL,
        ),
        ("index", "sqlite_autoindex_review_login_failures_1", None),
        ("index", "sqlite_autoindex_review_sessions_1", None),
        ("index", "sqlite_autoindex_shadow_decision_outcomes_1", None),
        ("table", "current_metadata_reports", _CURRENT_SQL),
        ("table", "current_metadata_report_lineages", _CURRENT_LINEAGE_SQL),
        ("table", "current_review_case_lineages", _REVIEW_POINTERS_SQL),
        ("table", "current_review_packages", _CURRENT_REVIEW_PACKAGES_SQL),
        ("table", "provider_recovery_outbox", _PROVIDER_RECOVERY_OUTBOX_SQL),
        ("table", "cover_considerations", _COVER_CONSIDERATIONS_SQL),
        ("table", "current_cover_considerations", _CURRENT_COVER_CONSIDERATIONS_SQL),
        ("table", "cover_consideration_events", _COVER_CONSIDERATION_EVENTS_SQL),
        ("table", "human_decision_events", _HUMAN_DECISION_EVENTS_SQL),
        ("table", "human_decisions", _HUMAN_DECISIONS_SQL),
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
        ("table", "metadata_policy_drafts", _METADATA_POLICY_DRAFTS_SQL),
        ("table", "metadata_policy_resolutions", _METADATA_POLICY_RESOLUTIONS_SQL),
        ("table", "metadata_policy_state", _METADATA_POLICY_STATE_SQL),
        ("table", "metadata_policy_transitions", _METADATA_POLICY_TRANSITIONS_SQL),
        ("table", "metadata_policy_transition_items", _METADATA_POLICY_TRANSITION_ITEMS_SQL),
        ("table", "proposal_sets", _PROPOSAL_SETS_SQL),
        ("table", "review_case_transitions", _REVIEW_TRANSITIONS_SQL),
        ("table", "review_cases", _REVIEW_CASES_SQL),
        ("table", "review_preparation_control", _REVIEW_CONTROL_SQL),
        ("table", "review_preparation_connection_conditions", _REVIEW_CONNECTION_CONDITIONS_SQL),
        ("table", "review_preparation_scans", _REVIEW_SCANS_SQL),
        ("table", "review_preparation_absences", _REVIEW_ABSENCES_SQL),
        ("table", "review_preparation_work", _REVIEW_WORK_SQL),
        ("table", "review_login_failures", _REVIEW_LOGIN_FAILURES_SQL),
        ("table", "review_sessions", _REVIEW_SESSIONS_SQL),
        ("table", "shadow_decision_outcomes", _SHADOW_DECISION_OUTCOMES_SQL),
        ("trigger", "metadata_report_cover_references_no_delete", _COVER_NO_DELETE_SQL),
        ("trigger", "metadata_report_cover_references_no_update", _COVER_NO_UPDATE_SQL),
        ("trigger", "metadata_reports_no_delete", _NO_DELETE_SQL),
        ("trigger", "metadata_reports_no_update", _NO_UPDATE_SQL),
        ("trigger", "metadata_policy_drafts_no_delete", _METADATA_POLICY_DRAFT_NO_DELETE_SQL),
        ("trigger", "metadata_policy_drafts_no_update", _METADATA_POLICY_DRAFT_NO_UPDATE_SQL),
        ("trigger", "human_decision_events_no_delete", _HUMAN_DECISION_EVENT_NO_DELETE_SQL),
        ("trigger", "human_decision_events_no_update", _HUMAN_DECISION_EVENT_NO_UPDATE_SQL),
        ("trigger", "human_decisions_no_delete", _HUMAN_DECISION_NO_DELETE_SQL),
        ("trigger", "human_decisions_no_update", _HUMAN_DECISION_NO_UPDATE_SQL),
        ("trigger", "cover_considerations_no_delete", _COVER_CONSIDERATION_NO_DELETE_SQL),
        ("trigger", "cover_considerations_no_update", _COVER_CONSIDERATION_NO_UPDATE_SQL),
        ("trigger", "cover_consideration_events_no_delete", _COVER_CONSIDERATION_EVENT_NO_DELETE_SQL),
        ("trigger", "cover_consideration_events_no_update", _COVER_CONSIDERATION_EVENT_NO_UPDATE_SQL),
        ("trigger", "automatic_decisions_no_delete", _AUTOMATIC_DECISION_NO_DELETE_SQL),
        ("trigger", "automatic_decisions_no_update", _AUTOMATIC_DECISION_NO_UPDATE_SQL),
        ("trigger", "field_effect_events_no_delete", _FIELD_EFFECT_EVENT_NO_DELETE_SQL),
        ("trigger", "field_effect_events_no_update", _FIELD_EFFECT_EVENT_NO_UPDATE_SQL),
        ("trigger", "field_effect_no_changes_no_delete", _FIELD_EFFECT_NO_CHANGE_NO_DELETE_SQL),
        ("trigger", "field_effect_no_changes_no_update", _FIELD_EFFECT_NO_CHANGE_NO_UPDATE_SQL),
        ("trigger", "proposal_sets_no_delete", _PROPOSAL_NO_DELETE_SQL),
        ("trigger", "proposal_sets_no_update", _PROPOSAL_NO_UPDATE_SQL),
        ("trigger", "review_case_transitions_no_delete", _REVIEW_TRANSITION_NO_DELETE_SQL),
        ("trigger", "review_case_transitions_no_update", _REVIEW_TRANSITION_NO_UPDATE_SQL),
        ("trigger", "review_cases_no_delete", _REVIEW_NO_DELETE_SQL),
        ("trigger", "review_cases_no_update", _REVIEW_NO_UPDATE_SQL),
        ("trigger", "shadow_decision_outcomes_no_delete", _SHADOW_DECISION_OUTCOME_NO_DELETE_SQL),
        ("trigger", "shadow_decision_outcomes_no_update", _SHADOW_DECISION_OUTCOME_NO_UPDATE_SQL),
    ))
    if objects != expected_objects or connection.execute("PRAGMA foreign_key_check").fetchall() or connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
        raise MetadataReportError("database is unavailable or incompatible")
    _validate_metadata_policy_documents(connection)
    _validate_field_effect_state(connection)
    for row in connection.execute(
        "SELECT outbox_id,connection_name,library_name,item_id,proposal_set_id,"
        "report_id,review_case_id,checkpoint,outcome,created_at "
        "FROM provider_recovery_outbox"
    ):
        _validate_provider_recovery_outbox_row(row)


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
            if (
                profile.policy_id != policy_id
                or profile.canonical_json != document
            ):
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
                raise MetadataPolicyError(
                    "metadata policy resolution is invalid"
                )
    except (
        json.JSONDecodeError,
        MetadataPolicyError,
        TypeError,
        ValueError,
        UnicodeError,
        sqlite3.Error,
    ) as error:
        raise MetadataReportError(
            "database is unavailable or incompatible"
        ) from error


def _provider_recovery_outbox_id(
    connection: str, library: str, item: str, proposal_id: str | None,
    report_id: str, case_id: str | None, checkpoint: int | None, outcome: str,
) -> str:
    document = _canonical_json({
        "case_id": case_id, "checkpoint": checkpoint, "connection": connection,
        "item": item, "library": library, "outcome": outcome,
        "proposal_id": proposal_id, "report_id": report_id,
    })
    return "recovery_outbox_" + hashlib.sha256(document.encode("utf-8")).hexdigest()


def _validate_provider_recovery_outbox_row(row: tuple[object, ...]) -> None:
    if (
        len(row) != 10
        or not all(type(row[index]) is str and row[index] for index in (0, 1, 2, 3, 5, 8))
        or (row[4] is not None and (type(row[4]) is not str or not row[4]))
        or (row[6] is not None and (type(row[6]) is not str or not row[6]))
        or (row[7] is not None and (type(row[7]) is not int or row[7] < 1))
        or type(row[9]) is not int or row[9] < 0
        # Short-circuit evaluation: reaching this disjunct means every column
        # test above passed, fixing each column's exact type.
        or row[0] != _provider_recovery_outbox_id(
            cast(str, row[1]), cast(str, row[2]), cast(str, row[3]),
            row[4], cast(str, row[5]), row[6], row[7], cast(str, row[8]),
        )
    ):
        raise MetadataReportError("provider recovery outbox is invalid")


def _review_casefold(value: object) -> str:
    if type(value) is not str:
        raise ValueError("review search value is invalid")
    return value.casefold()
