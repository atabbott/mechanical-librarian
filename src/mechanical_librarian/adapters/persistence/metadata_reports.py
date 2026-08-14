"""Clean-state SQLite repository for immutable reports and ProposalSets."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
import sys
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import quote

from mechanical_librarian.application.catalogue import CatalogueEntry
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.metadata_policy import PolicyTransition
from mechanical_librarian.application.review_pipeline import (
    PipelineAction,
    PipelineProgress,
    ReviewPipelineError,
    pipeline_scan_id,
)
from mechanical_librarian.application.review_web import (
    CurrentReviewCaseSnapshot,
    ReviewAttentionSnapshot,
    ReviewLibrarySnapshot,
)
from mechanical_librarian.domain.failure_diagnostics import (
    MAX_DIAGNOSTIC_COUNT,
    FailureCategory,
    FailureComponent,
    FailureDiagnostic,
    FailureStage,
)
from mechanical_librarian.domain.human_decision import (
    HumanDecision,
    HumanDecisionError,
)
from mechanical_librarian.domain.field_effect import (
    EffectAuthority,
    EffectConsent,
    FIELD_EFFECT_FAILURES,
    SUPPORTED_DESTINATIONS,
    FieldEffectError,
    FieldEffectOperation,
    authority_to_data,
    canonical_json as canonical_effect_json,
    field_value_version,
)
from mechanical_librarian.domain.automatic_decision import (
    AutomaticDecision,
    AutomaticDecisionError,
    AutomaticSafetyState,
    QUALIFIED_POLICY_OPTIONS,
    qualification_manifest_sha256,
)
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    ProviderStatus,
    REPORT_SCHEMA,
    ReportValueError,
)
from mechanical_librarian.domain.metadata_policy import (
    MetadataPolicyError,
    MetadataPolicyProfile,
    ResolvedMetadataPolicy,
    human_review_destinations,
    resolve_policy,
)
from mechanical_librarian.domain.proposal_set import (
    POLICY_VERSION,
    ProposalSet,
    ProposalValueError,
    evaluate_report,
)
from mechanical_librarian.domain.review_case import (
    ReviewAdmissionResult,
    ReviewAdmissionIncompatibleError,
    ReviewAdmissionStaleError,
    ReviewCase,
    ReviewCaseValueError,
    ReviewTransition,
    is_actionable,
)
from mechanical_librarian.domain.rich_metadata_preview import (
    RichMetadataPreviewError,
    project_f20_automatic_decision_value,
    select_isbn_representation,
)
from mechanical_librarian.domain.shadow_review import ShadowReviewPacket
from mechanical_librarian.domain.shadow_decision import decide_shadow_packet
from mechanical_librarian.domain.shadow_outcome import (
    ShadowOutcomeError,
    ShadowOutcomeSet,
)

if sys.platform.startswith("linux"):
    import fcntl
else:
    fcntl = None


_REPORTS_SQL = (
    "CREATE TABLE metadata_reports ("
    "report_id TEXT PRIMARY KEY NOT NULL CHECK (report_id GLOB 'report_[0-9a-f]*' AND length(report_id) = 71), "
    "connection_name TEXT NOT NULL, library_name TEXT NOT NULL, item_id TEXT NOT NULL, "
    "file_identity TEXT NOT NULL CHECK (length(file_identity) = 64 AND file_identity NOT GLOB '*[^0-9a-f]*'), "
    "content_digest TEXT NOT NULL CHECK (length(content_digest) = 64 AND content_digest NOT GLOB '*[^0-9a-f]*'), "
    "schema_version TEXT NOT NULL CHECK (schema_version = 'metadata-report-v24'), "
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
    "catalogue_generation INTEGER CHECK(catalogue_generation IS NULL OR catalogue_generation>0), catalogue_fingerprint TEXT CHECK(catalogue_fingerprint IS NULL OR (length(catalogue_fingerprint)=64 AND catalogue_fingerprint NOT GLOB '*[^0-9a-f]*')), "
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
    "report_id TEXT REFERENCES metadata_reports(report_id), proposal_set_id TEXT REFERENCES proposal_sets(proposal_set_id), review_case_id TEXT REFERENCES review_cases(review_case_id), "
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
    "kind TEXT NOT NULL CHECK(kind IN ('considered','superseded','selected','kept_incumbent','no_action','stale')), "
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
_CURRENT_REVIEW_FROM_SQL = (
    "FROM current_review_packages c "
    "JOIN current_review_packages ml ON ml.connection_name=c.connection_name "
    "AND ml.library_name=c.library_name AND ml.item_id=c.item_id "
    "JOIN review_cases rc ON rc.review_case_id=c.review_case_id "
    "JOIN metadata_reports mr ON mr.report_id=rc.report_id "
)
_CURRENT_LIBRARY_FROM_SQL = (
    "FROM current_review_packages ml "
    "JOIN metadata_reports mr ON mr.report_id=ml.report_id "
    "LEFT JOIN current_review_packages c "
    "ON c.connection_name=ml.connection_name "
    "AND c.library_name=ml.library_name AND c.item_id=ml.item_id "
    "LEFT JOIN review_cases rc ON rc.review_case_id=ml.review_case_id"
)
_REVIEW_MANUAL_BIBLIOGRAPHIC_SQL = (
    "EXISTS (SELECT 1 FROM json_each(rc.document,'$.results') manual_result "
    "WHERE json_extract(manual_result.value,'$.destination') "
    "IN ('title','subtitle','series') "
    "AND json_array_length(json_extract(manual_result.value,'$.reasons'))=1 "
    "AND json_extract(manual_result.value,'$.reasons[0]')="
    "'no_safe_bibliographic_combination')"
)
_AUTOMATIC_SAFETY_ATTENTION_SQL = (
    "EXISTS (SELECT 1 FROM shadow_decision_outcomes shadow,"
    " json_each(shadow.document,'$.outcomes') outcome,automatic_safety_state safety "
    "WHERE shadow.review_case_id=rc.review_case_id "
    "AND json_extract(outcome.value,'$.destination')=json_extract(result.value,'$.destination') "
    "AND json_extract(outcome.value,'$.policy_destination')='automatic' AND ("
    "safety.paused=1 OR safety.global_suspension IS NOT NULL OR EXISTS("
    "SELECT 1 FROM json_each(safety.category_suspensions_document) category "
    "WHERE json_extract(category.value,'$[0]')=json_extract(outcome.value,'$.policy_category'))))"
)
_REVIEW_FIELD_ATTENTION_SQL = (
    "(json_extract(result.value,'$.outcome')='unresolved_choice' "
    "OR (json_extract(result.value,'$.destination') "
    "IN ('title','subtitle','series') AND "
    + _REVIEW_MANUAL_BIBLIOGRAPHIC_SQL
    + ") OR " + _AUTOMATIC_SAFETY_ATTENTION_SQL + ") AND NOT EXISTS (SELECT 1 FROM human_decisions decision "
    "WHERE decision.review_case_id=rc.review_case_id "
    "AND decision.destination=json_extract(result.value,'$.destination')) "
    "AND NOT EXISTS (SELECT 1 FROM automatic_decisions decision "
    "WHERE decision.review_case_id=rc.review_case_id "
    "AND decision.destination=json_extract(result.value,'$.destination')) "
    "AND NOT EXISTS (SELECT 1 FROM field_effect_suspensions suspension "
    "WHERE suspension.connection_name=c.connection_name AND ("
    "(suspension.scope='connection' AND suspension.destination='') OR "
    "(suspension.scope='destination' AND suspension.destination="
    "json_extract(result.value,'$.destination'))))"
)
_REVIEW_ATTENTION_SQL = (
    "EXISTS (SELECT 1 FROM json_each(rc.document,'$.results') result WHERE "
    + _REVIEW_FIELD_ATTENTION_SQL
    + ")"
)
_REVIEW_SEARCH_SQL = (
    " AND (? IS NULL OR "
    "ml_casefold(COALESCE(json_extract(mr.document,'$.target.fields[0].value'),'')) "
    "LIKE ? ESCAPE '\\' OR ml_casefold(ml.item_id) LIKE ? ESCAPE '\\' "
    "OR ml_casefold(ml.library_name) LIKE ? ESCAPE '\\' "
    "OR EXISTS (SELECT 1 FROM json_each(mr.document,'$.target.fields') author_field, "
    "json_each(author_field.value,'$.value') author WHERE "
    "json_extract(author_field.value,'$.destination')='authors' "
    "AND ml_casefold(COALESCE(author.value,'')) LIKE ? ESCAPE '\\') "
    "OR ml_casefold(COALESCE(json_extract(mr.document,'$.file.format'),'')) "
    "LIKE ? ESCAPE '\\')"
)
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


class SQLiteMetadataReportRepository:
    def __init__(
        self,
        connection: sqlite3.Connection,
        binding: _LinuxDatabaseBinding | None = None,
        identity: tuple[int, int] | None = None,
    ) -> None:
        self._connection = connection
        self._binding = binding
        self.identity = identity
        self._review_pipeline_claim: PipelineAction | None = None
        self._current_cover_policy_reference: str | None = None
        self._current_cover_replay_reference: str | None = None

    def bind_current_cover_policy(self, reference: str) -> None:
        """Bind guarded cover effects to this process's validated policy."""
        if re.fullmatch(r"cover_policy_[0-9a-f]{64}", reference) is None:
            raise ValueError("cover policy reference is invalid")
        self._current_cover_policy_reference = reference

    def bind_current_cover_replay(self, reference: str) -> None:
        """Bind guarded cover effects to the current observer/contract profile."""
        if re.fullmatch(r"cover_replay_[0-9a-f]{64}", reference) is None:
            raise ValueError("cover replay reference is invalid")
        self._current_cover_replay_reference = reference

    def field_effect_authority_source(
        self, decision: HumanDecision | AutomaticDecision
    ) -> dict[str, object]:
        """Resolve one exact persisted decision against its current case lineage."""
        table = "human_decisions" if type(decision) is HumanDecision else "automatic_decisions"
        row = self._connection.execute(
            f"SELECT document FROM {table} WHERE decision_id=?", (decision.decision_id,)
        ).fetchone()
        if row is None or row[0] != decision.to_json():
            raise FieldEffectError("authority_binding")
        envelope = json.loads(self.get_review_case(decision.review_case_id))
        if (type(envelope) is not dict or envelope.get("current") is not True
                or type(envelope.get("case")) is not dict):
            raise FieldEffectError("authority_binding")
        case = ReviewCase.from_json(_canonical_json(envelope["case"]))
        if type(decision) is HumanDecision:
            decision.validate_for(case)
            value = decision.selected_value
        else:
            if (decision.review_case_id != case.review_case_id
                    or decision.report_id != case.report_id
                    or decision.proposal_set_id != case.proposal_set_id
                    or decision.target_fingerprint != case.target_fingerprint):
                raise FieldEffectError("authority_binding")
            proposal = next((item for item in case.proposals
                             if item["proposal_id"] == decision.proposal_id
                             and item["destination"] == decision.destination), None)
            alternatives = None if proposal is None else proposal["result"].get("alternatives")
            selected = next((item for item in alternatives or ()
                             if item.get("alternative_id") == decision.candidate_id), None)
            if selected is None:
                raise FieldEffectError("authority_binding")
            value = selected["value"]
            if decision.policy_category == "isbn_form":
                report_row = self._connection.execute(
                    "SELECT document FROM metadata_reports WHERE report_id=?",
                    (decision.report_id,),
                ).fetchone()
                proposal_row = self._connection.execute(
                    "SELECT document FROM proposal_sets WHERE proposal_set_id=?",
                    (decision.proposal_set_id,),
                ).fetchone()
                if report_row is None or proposal_row is None:
                    raise FieldEffectError("authority_binding")
                try:
                    report = MetadataReport.from_json(report_row[0])
                    proposal_set = ProposalSet.from_json(proposal_row[0])
                    result = next(
                        item for item in proposal_set.results
                        if item.destination.value == decision.destination
                    )
                    candidate = next(
                        item for item in result.alternatives
                        if item.alternative_id == decision.candidate_id
                    )
                    exact_value = select_isbn_representation(
                        report, candidate, decision.policy_option
                    )
                except (
                    ReportValueError, ProposalValueError, StopIteration,
                    RichMetadataPreviewError,
                ) as error:
                    raise FieldEffectError("authority_binding") from error
                if exact_value is None:
                    raise FieldEffectError("authority_binding")
                value = {"kind": "text", "value": exact_value}
        lineage = self._connection.execute(
            "SELECT connection_name,library_name,item_id FROM current_review_case_lineages "
            "WHERE review_case_id=?", (decision.review_case_id,),
        ).fetchone()
        if lineage is None:
            raise FieldEffectError("authority_binding")
        target_fields = case.target.get("fields")
        target_field = next(
            (
                item for item in target_fields or ()
                if type(item) is dict
                and item.get("destination") == decision.destination
            ),
            None,
        )
        if (type(target_fields) is not list or type(target_field) is not dict
                or target_field.get("state") not in {
                    "value", "null", "empty", "missing",
                }):
            raise FieldEffectError("authority_binding")
        pre_value = (
            target_field.get("value")
            if target_field["state"] == "value"
            else None
        )
        if type(decision) is AutomaticDecision and decision.policy_category in {
            "series", "genres", "tags", "description_fill", "isbn_form",
        }:
            try:
                value = project_f20_automatic_decision_value(
                    decision.policy_category,
                    decision.policy_option,
                    value,
                    pre_value,
                )
            except RichMetadataPreviewError as error:
                raise FieldEffectError("authority_binding") from error
        dependency_digests = (decision.target_fingerprint,)
        if type(decision) is HumanDecision and decision.destination == "cover":
            consideration_row = self._connection.execute(
                "SELECT consideration.document FROM current_cover_considerations current "
                "JOIN cover_considerations consideration ON consideration.consideration_id=current.consideration_id "
                "JOIN cover_consideration_events event ON event.consideration_id=consideration.consideration_id "
                "WHERE current.review_case_id=? AND event.kind='selected' AND event.decision_id=?",
                (decision.review_case_id, decision.decision_id),
            ).fetchone()
            if consideration_row is None:
                raise FieldEffectError("authority_binding")
            consideration = json.loads(consideration_row[0])
            selected_artifact = (
                value.get("value") if type(value) is dict else None
            )
            selected_candidate = next(
                (
                    candidate
                    for candidate in consideration.get("candidates", ())
                    if type(candidate) is dict
                    and candidate.get("artifact_id") == selected_artifact
                    and candidate.get("selectable") is True
                    and candidate.get("incumbent") is False
                ),
                None,
            )
            incumbent_digest = consideration.get("incumbent_digest")
            if (
                selected_candidate is None
                or consideration.get("target_fingerprint") != decision.target_fingerprint
                or consideration.get("policy_reference")
                != self._current_cover_policy_reference
                or consideration.get("replay_reference")
                != self._current_cover_replay_reference
                or (
                    incumbent_digest is not None
                    and pre_value != "cover_" + str(incumbent_digest)
                )
            ):
                raise FieldEffectError("authority_binding")
            dependency_digests = (
                decision.target_fingerprint,
                hashlib.sha256(consideration_row[0].encode("utf-8")).hexdigest(),
            )
        return {"connection": lineage[0], "library": lineage[1], "work_id": lineage[2],
                "destination": decision.destination,
                "review_case_id": decision.review_case_id,
                "target_fingerprint": decision.target_fingerprint,
                "target_version": field_value_version(decision.destination, pre_value),
                "decision_value": value, "source": "persisted_review_case",
                "pre_value": pre_value,
                "dependency_digests": dependency_digests}

    def field_effect_authority_is_current(self, authority: EffectAuthority) -> bool:
        current = (self._connection.execute(
            "SELECT current FROM field_effect_authorities WHERE decision_id=?",
            (authority.decision_id,),
        ).fetchone() == (1,) and self._connection.execute(
            "SELECT 1 FROM current_review_case_lineages WHERE review_case_id=?",
            (authority.review_case_id,),
        ).fetchone() == (1,))
        if not current or authority.origin != "automatic_policy":
            return current
        lineage = self._connection.execute(
            "SELECT library_name FROM current_review_case_lineages "
            "WHERE review_case_id=?", (authority.review_case_id,),
        ).fetchone()
        if lineage is None:
            return False
        if self._connection.execute(
            "SELECT 1 FROM automatic_decisions automatic "
            "JOIN field_effect_manual_holds hold "
            "ON hold.review_case_id=automatic.review_case_id "
            "AND hold.destination=automatic.destination AND hold.active=1 "
            "WHERE automatic.decision_id=? LIMIT 1", (authority.decision_id,),
        ).fetchone() == (1,):
            return False
        try:
            return self.current_resolved_policy_id(lineage[0]) == authority.policy_identity
        except MetadataReportError:
            return False

    def grant_field_effect_consents(
        self, connection: str, libraries: tuple[str, ...],
        destinations: tuple[str, ...], granted_at: int,
    ) -> tuple[EffectConsent, ...]:
        """Grant one explicit library-scoped envelope as field-local consents."""
        if (type(connection) is not str or not connection
                or type(libraries) is not tuple or not libraries
                or type(destinations) is not tuple or not destinations
                or any(type(value) is not str or not value for value in libraries)
                or len(set(libraries)) != len(libraries)
                or len(set(destinations)) != len(destinations)
                or type(granted_at) is not int or granted_at < 0):
            raise FieldEffectError("invalid_consent")
        granted: list[EffectConsent] = []
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            for library in libraries:
                envelope_identity = self.field_effect_consent_envelope_identity(library)
                for destination in destinations:
                    row = self._connection.execute(
                        "SELECT document FROM field_effect_consents WHERE current=1 "
                        "AND json_extract(document,'$.consent.connection')=? "
                        "AND json_extract(document,'$.consent.library')=? "
                        "AND json_extract(document,'$.consent.destination')=? "
                        "ORDER BY updated_at DESC,consent_id LIMIT 1",
                        (connection, library, destination),
                    ).fetchone()
                    if row is not None:
                        raw = json.loads(row[0])
                        consent = EffectConsent.from_data(raw["consent"])
                        if raw.get("current") is not True:
                            raise FieldEffectError("invalid_consent")
                        if consent.envelope_identity == envelope_identity:
                            granted.append(consent)
                            continue
                        revoked = canonical_effect_json({
                            "consent": consent.to_data(), "current": False,
                            "schema_version": "field-effect-consent-state-v1",
                            "updated_at": granted_at,
                        })
                        self._connection.execute(
                            "UPDATE field_effect_consents SET current=0,updated_at=?,document=? "
                            "WHERE consent_id=? AND current=1",
                            (granted_at, revoked, consent.consent_id),
                        )
                    consent = EffectConsent.create(
                        connection=connection, library=library,
                        destination=destination,
                        envelope_identity=envelope_identity,
                        granted_at=granted_at,
                    )
                    document = canonical_effect_json({
                        "consent": consent.to_data(), "current": True,
                        "schema_version": "field-effect-consent-state-v1",
                        "updated_at": granted_at,
                    })
                    self._connection.execute(
                        "INSERT INTO field_effect_consents(consent_id,current,updated_at,document) "
                        "VALUES(?,1,?,?)",
                        (consent.consent_id, granted_at, document),
                    )
                    granted.append(consent)
            self._connection.commit()
            return tuple(granted)
        except (json.JSONDecodeError, KeyError, sqlite3.Error, MetadataReportError,
                FieldEffectError, TypeError, ValueError) as error:
            self._connection.rollback()
            if isinstance(error, FieldEffectError):
                raise
            raise FieldEffectError("persistence") from error

    def grant_field_effect_consent_matrix(
        self, connection: str,
        grants: tuple[tuple[str, tuple[str, ...]], ...], granted_at: int,
    ) -> tuple[EffectConsent, ...]:
        """Atomically persist only the capability-proven library destinations."""
        if (type(grants) is not tuple or not grants
                or any(type(library) is not str or not library
                       or type(destinations) is not tuple or not destinations
                       or any(type(destination) is not str or not destination
                              for destination in destinations)
                       or len(set(destinations)) != len(destinations)
                       for library, destinations in grants)
                or len({library for library, _ in grants}) != len(grants)):
            raise FieldEffectError("invalid_consent")
        granted: list[EffectConsent] = []
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            for library, destinations in grants:
                envelope_identity = self.field_effect_consent_envelope_identity(library)
                eligible_destinations = set(destinations)
                current_rows = self._connection.execute(
                    "SELECT document FROM field_effect_consents WHERE current=1 "
                    "AND json_extract(document,'$.consent.connection')=? "
                    "AND json_extract(document,'$.consent.library')=?",
                    (connection, library),
                ).fetchall()
                for (document,) in current_rows:
                    raw = json.loads(document)
                    consent = EffectConsent.from_data(raw["consent"])
                    if raw.get("current") is not True:
                        raise FieldEffectError("invalid_consent")
                    if consent.destination in eligible_destinations:
                        continue
                    self._connection.execute(
                        "UPDATE field_effect_consents SET current=0,updated_at=?,document=? "
                        "WHERE consent_id=? AND current=1",
                        (granted_at, canonical_effect_json({
                            "consent": consent.to_data(), "current": False,
                            "schema_version": "field-effect-consent-state-v1",
                            "updated_at": granted_at,
                        }), consent.consent_id),
                    )
                for destination in destinations:
                    row = self._connection.execute(
                        "SELECT document FROM field_effect_consents WHERE current=1 "
                        "AND json_extract(document,'$.consent.connection')=? "
                        "AND json_extract(document,'$.consent.library')=? "
                        "AND json_extract(document,'$.consent.destination')=? "
                        "ORDER BY updated_at DESC,consent_id LIMIT 1",
                        (connection, library, destination),
                    ).fetchone()
                    if row is not None:
                        raw = json.loads(row[0])
                        consent = EffectConsent.from_data(raw["consent"])
                        if raw.get("current") is not True:
                            raise FieldEffectError("invalid_consent")
                        if consent.envelope_identity == envelope_identity:
                            granted.append(consent)
                            continue
                        self._connection.execute(
                            "UPDATE field_effect_consents SET current=0,updated_at=?,document=? "
                            "WHERE consent_id=? AND current=1",
                            (granted_at, canonical_effect_json({
                                "consent": consent.to_data(), "current": False,
                                "schema_version": "field-effect-consent-state-v1",
                                "updated_at": granted_at,
                            }), consent.consent_id),
                        )
                    consent = EffectConsent.create(
                        connection=connection, library=library,
                        destination=destination, envelope_identity=envelope_identity,
                        granted_at=granted_at,
                    )
                    self._connection.execute(
                        "INSERT INTO field_effect_consents(consent_id,current,updated_at,document) "
                        "VALUES(?,1,?,?)",
                        (consent.consent_id, granted_at, canonical_effect_json({
                            "consent": consent.to_data(), "current": True,
                            "schema_version": "field-effect-consent-state-v1",
                            "updated_at": granted_at,
                        })),
                    )
                    granted.append(consent)
            self._connection.commit()
            return tuple(granted)
        except (json.JSONDecodeError, KeyError, sqlite3.Error, MetadataReportError,
                FieldEffectError, TypeError, ValueError) as error:
            self._connection.rollback()
            if isinstance(error, FieldEffectError):
                raise
            raise FieldEffectError("persistence") from error

    def current_field_effect_consent(
        self, connection: str, library: str, destination: str,
    ) -> EffectConsent:
        try:
            row = self._connection.execute(
                "SELECT document FROM field_effect_consents WHERE current=1 "
                "AND json_extract(document,'$.consent.connection')=? "
                "AND json_extract(document,'$.consent.library')=? "
                "AND json_extract(document,'$.consent.destination')=? "
                "ORDER BY updated_at DESC,consent_id LIMIT 1",
                (connection, library, destination),
            ).fetchone()
            if row is None:
                raise FieldEffectError("consent")
            raw = json.loads(row[0])
            if raw.get("current") is not True:
                raise FieldEffectError("consent")
            consent = EffectConsent.from_data(raw["consent"])
            if consent.envelope_identity != self.field_effect_consent_envelope_identity(library):
                raise FieldEffectError("consent")
            return consent
        except FieldEffectError:
            raise
        except (json.JSONDecodeError, KeyError, sqlite3.Error,
                TypeError, ValueError) as error:
            raise FieldEffectError("persistence") from error

    def field_effect_consent_is_current(self, consent: EffectConsent) -> bool:
        persisted = self._connection.execute(
            "SELECT current FROM field_effect_consents WHERE consent_id=?",
            (consent.consent_id,),
        ).fetchone() == (1,)
        if not persisted:
            return False
        try:
            return consent.envelope_identity == self.field_effect_consent_envelope_identity(
                consent.library
            )
        except (FieldEffectError, MetadataReportError):
            return False

    def field_effect_consent_envelope_identity(self, library: str) -> str:
        """Bind consent to the exact current automatic policy semantics."""
        if type(library) is not str or not library:
            raise FieldEffectError("invalid_consent")
        return self._field_effect_consent_envelope_identity(
            library, self.current_resolved_policy_id(library)
        )

    @staticmethod
    def _field_effect_consent_envelope_identity(
        library: str, resolved_policy_id: str,
    ) -> str:
        document = canonical_effect_json({
            "action": "apply_field",
            "destinations": sorted(SUPPORTED_DESTINATIONS),
            "library": library,
            "resolved_policy_id": resolved_policy_id,
            "schema_version": "field-effect-consent-envelope-v1",
        })
        return hashlib.sha256(document.encode("utf-8")).hexdigest()

    def revoke_library_field_effect_consents(
        self, connection: str, library: str, changed_at: int,
    ) -> int:
        """Atomically stop every new claim for one configured library."""
        if (type(connection) is not str or not connection
                or type(library) is not str or not library
                or type(changed_at) is not int or changed_at < 0):
            raise FieldEffectError("invalid_consent")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            rows = self._connection.execute(
                "SELECT consent_id,document FROM field_effect_consents WHERE current=1 "
                "AND json_extract(document,'$.consent.connection')=? "
                "AND json_extract(document,'$.consent.library')=?",
                (connection, library),
            ).fetchall()
            for consent_id, document in rows:
                raw = json.loads(document)
                consent = EffectConsent.from_data(raw["consent"])
                revoked = canonical_effect_json({
                    "consent": consent.to_data(), "current": False,
                    "schema_version": "field-effect-consent-state-v1",
                    "updated_at": changed_at,
                })
                self._connection.execute(
                    "UPDATE field_effect_consents SET current=0,updated_at=?,document=? "
                    "WHERE consent_id=? AND current=1",
                    (changed_at, revoked, consent_id),
                )
            self._connection.commit()
            return len(rows)
        except (json.JSONDecodeError, KeyError, sqlite3.Error,
                FieldEffectError, TypeError, ValueError) as error:
            self._connection.rollback()
            if isinstance(error, FieldEffectError):
                raise
            raise FieldEffectError("persistence") from error

    def pending_field_effect_decision_documents(
        self, limit: int, after: tuple[int, str] | None = None,
    ) -> tuple[str, ...]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise FieldEffectError("invalid_limit")
        if (after is not None and (type(after) is not tuple or len(after) != 2
                or type(after[0]) is not int or after[0] < 0
                or type(after[1]) is not str or not after[1])):
            raise FieldEffectError("invalid_limit")
        try:
            cursor = ""
            parameters: tuple[object, ...] = ()
            if after is not None:
                cursor = ("AND (pending.occurred_at>? OR "
                          "(pending.occurred_at=? AND pending.decision_id>?)) ")
                parameters = (after[0], after[0], after[1])
            rows = self._connection.execute(
                "SELECT pending.document FROM ("
                "SELECT human.decision_id,human.occurred_at,human.document "
                "FROM human_decisions human WHERE human.outcome='accept' AND "
                "(EXISTS (SELECT 1 FROM current_review_case_lineages lineage "
                "WHERE lineage.review_case_id=human.review_case_id) OR ("
                "human.destination='cover' AND EXISTS (SELECT 1 FROM "
                "cover_consideration_events selected JOIN current_cover_considerations current "
                "ON current.consideration_id=selected.consideration_id "
                "WHERE selected.decision_id=human.decision_id AND selected.kind='selected' "
                "AND current.review_case_id=human.review_case_id) AND NOT EXISTS ("
                "SELECT 1 FROM cover_consideration_events stale WHERE "
                "stale.decision_id=human.decision_id AND stale.kind='stale'))) AND "
                "human.ordinal=(SELECT MAX(current.ordinal) FROM human_decisions current "
                "WHERE current.review_case_id=human.review_case_id AND "
                "current.destination=human.destination) AND NOT EXISTS ("
                "SELECT 1 FROM field_effect_manual_holds returned WHERE "
                "returned.review_case_id=human.review_case_id AND "
                "returned.destination=human.destination AND returned.active=0) UNION ALL "
                "SELECT automatic.decision_id,automatic.occurred_at,automatic.document "
                "FROM automatic_decisions automatic WHERE "
                "EXISTS (SELECT 1 FROM current_review_case_lineages lineage "
                "WHERE lineage.review_case_id=automatic.review_case_id) AND "
                "json_extract(automatic.document,'$.outcome')='candidate' AND NOT EXISTS ("
                "SELECT 1 FROM human_decisions human WHERE "
                "human.review_case_id=automatic.review_case_id AND "
                "human.destination=automatic.destination AND NOT EXISTS ("
                "SELECT 1 FROM field_effect_manual_holds returned WHERE "
                "returned.review_case_id=automatic.review_case_id AND "
                "returned.destination=automatic.destination AND returned.active=0)) AND NOT EXISTS ("
                "SELECT 1 FROM field_effect_manual_holds hold WHERE "
                "hold.review_case_id=automatic.review_case_id AND "
                "hold.destination=automatic.destination AND hold.active=1)"
                ") AS pending WHERE NOT EXISTS (SELECT 1 FROM field_effects effect "
                "WHERE effect.decision_id=pending.decision_id AND "
                "effect.state NOT IN ('superseded','abandoned')) " + cursor +
                "ORDER BY pending.occurred_at,pending.decision_id LIMIT ?",
                (*parameters, limit),
            ).fetchall()
            return tuple(row[0] for row in rows)
        except sqlite3.Error as error:
            raise FieldEffectError("persistence") from error

    def pending_field_effect_decision_counts(self) -> tuple[dict[str, object], ...]:
        """Return exact current prepared counts grouped by library and origin."""
        try:
            rows = self._connection.execute(
                "WITH pending AS ("
                "SELECT human.decision_id,human.review_case_id,'human' origin "
                "FROM human_decisions human WHERE human.outcome='accept' AND "
                "EXISTS (SELECT 1 FROM current_review_case_lineages lineage "
                "WHERE lineage.review_case_id=human.review_case_id) AND "
                "human.ordinal=(SELECT MAX(current.ordinal) FROM human_decisions current "
                "WHERE current.review_case_id=human.review_case_id AND "
                "current.destination=human.destination) AND NOT EXISTS ("
                "SELECT 1 FROM field_effect_manual_holds returned WHERE "
                "returned.review_case_id=human.review_case_id AND "
                "returned.destination=human.destination AND returned.active=0) UNION ALL "
                "SELECT automatic.decision_id,automatic.review_case_id,'automatic' origin "
                "FROM automatic_decisions automatic WHERE "
                "EXISTS (SELECT 1 FROM current_review_case_lineages lineage "
                "WHERE lineage.review_case_id=automatic.review_case_id) AND "
                "json_extract(automatic.document,'$.outcome')='candidate' AND NOT EXISTS ("
                "SELECT 1 FROM human_decisions human WHERE "
                "human.review_case_id=automatic.review_case_id AND "
                "human.destination=automatic.destination AND NOT EXISTS ("
                "SELECT 1 FROM field_effect_manual_holds returned WHERE "
                "returned.review_case_id=automatic.review_case_id AND "
                "returned.destination=automatic.destination AND returned.active=0)) AND NOT EXISTS ("
                "SELECT 1 FROM field_effect_manual_holds hold WHERE "
                "hold.review_case_id=automatic.review_case_id AND "
                "hold.destination=automatic.destination AND hold.active=1)) "
                "SELECT lineage.connection_name,lineage.library_name,pending.origin,count(*) "
                "FROM pending JOIN current_review_case_lineages lineage "
                "ON lineage.review_case_id=pending.review_case_id "
                "WHERE NOT EXISTS (SELECT 1 FROM field_effects effect "
                "WHERE effect.decision_id=pending.decision_id AND "
                "effect.state NOT IN ('superseded','abandoned')) "
                "GROUP BY lineage.connection_name,lineage.library_name,pending.origin "
                "ORDER BY lineage.connection_name,lineage.library_name,pending.origin"
            ).fetchall()
            return tuple({
                "connection": connection,
                "library": library,
                "origin": origin,
                "count": count,
            } for connection, library, origin, count in rows)
        except sqlite3.Error as error:
            raise FieldEffectError("persistence") from error

    def field_effect_manual_override(
        self, review_case_id: str, destination: str, changed_at: int,
    ) -> int:
        """Supersede only unclaimed automatic authority for a human override."""
        if (type(review_case_id) is not str or type(destination) is not str
                or type(changed_at) is not int or changed_at < 0):
            raise FieldEffectError("authority_binding")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            rows = self._connection.execute(
                "SELECT automatic.decision_id,human.decision_id FROM automatic_decisions automatic "
                "JOIN human_decisions human ON human.review_case_id=automatic.review_case_id "
                "AND human.destination=automatic.destination "
                "AND human.ordinal=(SELECT MAX(current.ordinal) FROM human_decisions current "
                "WHERE current.review_case_id=human.review_case_id AND "
                "current.destination=human.destination) "
                "WHERE automatic.review_case_id=? AND automatic.destination=?",
                (review_case_id, destination),
            ).fetchall()
            if rows:
                self._set_field_effect_manual_hold(
                    review_case_id, destination, changed_at, rows[0][1]
                )
            superseded = sum(
                self._revoke_field_effect_authority_in_transaction(
                    decision_id, changed_at, required=False
                )
                for decision_id, _ in rows
            )
            self._connection.commit()
            return superseded
        except (sqlite3.Error, FieldEffectError) as error:
            self._connection.rollback()
            if isinstance(error, FieldEffectError):
                raise
            raise FieldEffectError("persistence") from error

    def _set_field_effect_manual_hold(
        self, review_case_id: str, destination: str, changed_at: int,
        decision_id: str,
    ) -> None:
        document = canonical_effect_json({
            "active": True, "destination": destination,
            "decision_id": decision_id,
            "pending_return": None,
            "review_case_id": review_case_id,
            "schema_version": "field-effect-manual-hold-v2",
            "updated_at": changed_at,
        })
        self._connection.execute(
            "INSERT INTO field_effect_manual_holds(review_case_id,destination,active,updated_at,document) "
            "VALUES(?,?,1,?,?) ON CONFLICT(review_case_id,destination) DO UPDATE SET "
            "active=1,updated_at=excluded.updated_at,document=excluded.document",
            (review_case_id, destination, changed_at, document),
        )

    def field_effect_manual_hold_context(
        self, review_case_id: str, destination: str,
    ) -> tuple[str, str] | None:
        row = self._connection.execute(
            "SELECT lineage.connection_name,lineage.library_name "
            "FROM current_review_case_lineages lineage "
            "JOIN field_effect_manual_holds hold ON hold.review_case_id=lineage.review_case_id "
            "WHERE lineage.review_case_id=? AND hold.destination=? AND hold.active=1",
            (review_case_id, destination),
        ).fetchone()
        return None if row is None else (row[0], row[1])

    def field_effect_manual_hold_token(
        self, review_case_id: str, destination: str,
    ) -> str | None:
        row = self._connection.execute(
            "SELECT document FROM field_effect_manual_holds "
            "WHERE review_case_id=? AND destination=? AND active=1",
            (review_case_id, destination),
        ).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row[0])
            token = value["decision_id"]
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise FieldEffectError("persistence") from error
        return token if type(token) is str else None

    def request_field_effect_manual_return(
        self, review_case_id: str, destination: str, hold_token: str,
        scan_id: str, requested_at: int, changed_at: int,
    ) -> bool:
        """Persist an accepted return request against one exact human hold."""
        if (
            any(type(value) is not str or not value for value in
                (review_case_id, destination, hold_token, scan_id))
            or type(requested_at) is not int or requested_at < 0
            or type(changed_at) is not int or changed_at < 0
        ):
            raise FieldEffectError("invalid_manual_hold")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT document FROM field_effect_manual_holds "
                "WHERE review_case_id=? AND destination=? AND active=1",
                (review_case_id, destination),
            ).fetchone()
            if row is None:
                self._connection.rollback()
                return False
            active_document = row[0]
            raw = json.loads(active_document)
            if raw.get("decision_id") != hold_token:
                self._connection.rollback()
                return False
            scan = self._connection.execute(
                "SELECT scan.state FROM review_preparation_scans scan "
                "JOIN current_review_case_lineages lineage "
                "ON lineage.review_case_id=? "
                "AND lineage.connection_name=scan.connection_name "
                "AND lineage.library_name=scan.library_name "
                "WHERE scan.scan_id=?",
                (review_case_id, scan_id),
            ).fetchone()
            if scan is None:
                self._connection.rollback()
                return False
            raw["pending_return"] = {
                "requested_at": requested_at,
                "scan_id": scan_id,
            }
            raw["updated_at"] = changed_at
            document = canonical_effect_json(raw)
            updated = self._connection.execute(
                "UPDATE field_effect_manual_holds SET updated_at=?,document=? "
                "WHERE review_case_id=? AND destination=? AND active=1 AND document=?",
                (changed_at, document, review_case_id, destination, active_document),
            ).rowcount
            if updated != 1:
                self._connection.rollback()
                return False
            if scan[0] == "completed_with_failures":
                requeued = self._connection.execute(
                    "UPDATE review_preparation_scans SET state='queued',"
                    "requested_at=?,updated_at=MAX(?,?),"
                    "catalogue_generation=NULL,catalogue_fingerprint=NULL,total=0,"
                    "refresh_count=0,rescan_requested=0 WHERE scan_id=? "
                    "AND state='completed_with_failures'",
                    (requested_at, requested_at, changed_at, scan_id),
                ).rowcount
                if requeued != 1:
                    self._connection.rollback()
                    return False
            self._connection.commit()
            return True
        except (json.JSONDecodeError, KeyError, sqlite3.Error, TypeError) as error:
            self._connection.rollback()
            raise FieldEffectError("persistence") from error

    def pending_field_effect_manual_returns(
        self, limit: int, after: tuple[int, str, str] | None = None,
    ) -> tuple[tuple[str, str, str, str, str, str, int, int], ...]:
        if (
            type(limit) is not int or not 1 <= limit <= 100
            or (
                after is not None
                and (
                    type(after) is not tuple
                    or len(after) != 3
                    or type(after[0]) is not int
                    or after[0] < 0
                    or any(type(value) is not str or not value for value in after[1:])
                )
            )
        ):
            raise FieldEffectError("invalid_manual_hold")
        try:
            cursor_clause = ""
            parameters: tuple[object, ...] = ()
            if after is not None:
                cursor_clause = (
                    "AND (hold.updated_at>? OR "
                    "(hold.updated_at=? AND hold.review_case_id>?) OR "
                    "(hold.updated_at=? AND hold.review_case_id=? "
                    "AND hold.destination>?)) "
                )
                parameters = (
                    after[0], after[0], after[1],
                    after[0], after[1], after[2],
                )
            rows = self._connection.execute(
                "SELECT hold.review_case_id,hold.destination,lineage.connection_name,"
                "lineage.library_name,hold.document,hold.updated_at "
                "FROM field_effect_manual_holds hold "
                "JOIN current_review_case_lineages lineage "
                "ON lineage.review_case_id=hold.review_case_id "
                "WHERE hold.active=1 AND "
                "json_type(hold.document,'$.pending_return')='object' "
                + cursor_clause +
                "ORDER BY hold.updated_at,hold.review_case_id,hold.destination LIMIT ?",
                (*parameters, limit),
            ).fetchall()
            pending = []
            for (
                review_case_id, destination, connection, library, document, updated_at
            ) in rows:
                raw = json.loads(document)
                request = raw["pending_return"]
                pending.append((
                    review_case_id, destination, connection, library,
                    raw["decision_id"], request["scan_id"], request["requested_at"],
                    updated_at,
                ))
            return tuple(pending)
        except (json.JSONDecodeError, KeyError, sqlite3.Error, TypeError) as error:
            raise FieldEffectError("persistence") from error

    def _has_pending_field_effect_manual_return(self, scan_id: str) -> bool:
        return bool(self._connection.execute(
            "SELECT EXISTS(SELECT 1 FROM field_effect_manual_holds "
            "WHERE active=1 AND "
            "json_extract(document,'$.pending_return.scan_id')=?)",
            (scan_id,),
        ).fetchone()[0])

    def clear_field_effect_manual_hold(
        self, review_case_id: str, destination: str, changed_at: int,
        expected_hold_token: str | None = None,
    ) -> tuple[str, str] | None:
        """Atomically clear an active hold and return its rescan lineage."""
        context = self.field_effect_manual_hold_context(review_case_id, destination)
        if context is None:
            return None
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            hold = self._connection.execute(
                "SELECT document FROM field_effect_manual_holds "
                "WHERE review_case_id=? AND destination=? AND active=1",
                (review_case_id, destination),
            ).fetchone()
            if hold is None:
                self._connection.rollback()
                return None
            active_document = hold[0]
            raw = json.loads(active_document)
            if expected_hold_token is not None and raw.get("decision_id") != expected_hold_token:
                self._connection.rollback()
                return None
            document = canonical_effect_json({
                "active": False, "decision_id": raw["decision_id"],
                "destination": destination, "review_case_id": review_case_id,
                "pending_return": None,
                "schema_version": "field-effect-manual-hold-v2",
                "updated_at": changed_at,
            })
            updated = self._connection.execute(
                "UPDATE field_effect_manual_holds SET active=0,updated_at=?,document=? "
                "WHERE review_case_id=? AND destination=? AND active=1 AND document=?",
                (changed_at, document, review_case_id, destination, active_document),
            ).rowcount
            automatic = self._connection.execute(
                "SELECT decision_id FROM automatic_decisions "
                "WHERE review_case_id=? AND destination=?",
                (review_case_id, destination),
            ).fetchone()
            if updated == 1 and automatic is not None:
                authority = self._connection.execute(
                    "SELECT document FROM field_effect_authorities WHERE decision_id=?",
                    (automatic[0],),
                ).fetchone()
                if authority is not None:
                    authority_document = json.loads(authority[0])
                    authority_document["current"] = True
                    authority_document["updated_at"] = changed_at
                    self._connection.execute(
                        "UPDATE field_effect_authorities SET current=1,updated_at=?,document=? "
                        "WHERE decision_id=?",
                        (changed_at, canonical_effect_json(authority_document), automatic[0]),
                    )
            self._connection.commit()
            return context if updated == 1 else None
        except (json.JSONDecodeError, KeyError, sqlite3.Error, TypeError) as error:
            self._connection.rollback()
            raise FieldEffectError("persistence") from error

    def field_effect_dependencies_current(self, digests: tuple[str, ...]) -> bool:
        if (type(digests) is not tuple or not digests
                or any(type(value) is not str or len(value) != 64 for value in digests)):
            return False
        try:
            return all(
                self._connection.execute(
                    "SELECT 1 FROM current_review_case_lineages lineage "
                    "JOIN review_cases review ON review.review_case_id=lineage.review_case_id "
                    "WHERE json_extract(review.document,'$.target_fingerprint')=? LIMIT 1",
                    (digest,),
                ).fetchone()
                == (1,)
                or self._connection.execute(
                    "SELECT 1 FROM current_cover_considerations current "
                    "JOIN cover_considerations consideration ON consideration.consideration_id=current.consideration_id "
                    "JOIN current_review_case_lineages lineage ON lineage.review_case_id=current.review_case_id "
                    "WHERE consideration.document_digest=? "
                    "AND json_extract(consideration.document,'$.policy_reference')=? "
                    "AND json_extract(consideration.document,'$.replay_reference')=? LIMIT 1",
                    (
                        digest,
                        self._current_cover_policy_reference,
                        self._current_cover_replay_reference,
                    ),
                ).fetchone()
                == (1,)
                for digest in digests
            )
        except sqlite3.Error:
            return False

    def field_effect_backup_ready(self) -> bool:
        """Prove the exact-current durable store can form a consistent checkpoint."""
        try:
            row = self._connection.execute("PRAGMA quick_check").fetchone()
            return row == ("ok",) and not self._connection.in_transaction
        except sqlite3.Error:
            return False

    def next_field_effect_plan_id(
        self, last_library: str | None,
    ) -> tuple[str, str] | None:
        """Choose a durable ready plan while alternating when another library waits."""
        if self.field_effect_paused():
            return None
        try:
            parameters: tuple[object, ...] = ()
            preference = ""
            if last_library is not None:
                preference = "CASE WHEN library_name=? THEN 1 ELSE 0 END,"
                parameters = (last_library,)
            row = self._connection.execute(
                "SELECT plan_id,library_name FROM field_effects WHERE state IN "
                "('planned','retryable') ORDER BY " + preference
                + "updated_at,plan_id LIMIT 1",
                parameters,
            ).fetchone()
            return None if row is None else (row[0], row[1])
        except sqlite3.Error as error:
            raise FieldEffectError("persistence") from error

    def pending_field_effect_action_plan_ids(self, limit: int) -> tuple[str, ...]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise FieldEffectError("invalid_limit")
        try:
            return tuple(row[0] for row in self._connection.execute(
                "SELECT DISTINCT plan_id FROM field_effect_actions WHERE state IN "
                "('pending','claimed') ORDER BY updated_at,plan_id LIMIT ?",
                (limit,),
            ).fetchall())
        except sqlite3.Error as error:
            raise FieldEffectError("persistence") from error

    def field_effect_activity_data(self, limit: int = 50) -> dict[str, object]:
        """Project a bounded value-free status and chronological audit ledger."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise FieldEffectError("invalid_limit")
        try:
            consent_rows = self._connection.execute(
                "SELECT c.consent_id,c.updated_at,c.document,s.generation,"
                "r.resolved_policy_id,r.document,d.document "
                "FROM field_effect_consents c JOIN metadata_policy_state s "
                "ON s.singleton=1 JOIN metadata_policy_drafts d "
                "ON d.policy_id=s.active_policy_id "
                "LEFT JOIN metadata_policy_resolutions r "
                "ON r.generation=s.generation AND r.library_name="
                "json_extract(c.document,'$.consent.library') "
                "WHERE c.current=1 ORDER BY "
                "json_extract(c.document,'$.consent.connection'),"
                "json_extract(c.document,'$.consent.library'),"
                "json_extract(c.document,'$.consent.destination')"
            ).fetchall()
            consented_destinations: list[tuple[str, str, str]] = []
            profile: MetadataPolicyProfile | None = None
            for consent_id, updated_at, document, generation, resolved_id, resolved_document, draft_document in consent_rows:
                raw = json.loads(document)
                if (
                    type(raw) is not dict
                    or set(raw) != {"consent", "current", "schema_version", "updated_at"}
                    or raw.get("current") is not True
                    or raw.get("schema_version") != "field-effect-consent-state-v1"
                    or raw.get("updated_at") != updated_at
                    or canonical_effect_json(raw) != document
                ):
                    raise FieldEffectError("persistence")
                consent = EffectConsent.from_data(raw["consent"])
                if consent.consent_id != consent_id:
                    raise FieldEffectError("persistence")
                if resolved_id is not None:
                    resolved = ResolvedMetadataPolicy.from_data(
                        json.loads(resolved_document)
                    )
                    if (
                        resolved.library != consent.library
                        or resolved.resolved_id != resolved_id
                        or resolved.canonical_json != resolved_document
                    ):
                        raise FieldEffectError("persistence")
                    current_resolved_id = resolved.resolved_id
                elif generation == 1:
                    if profile is None:
                        profile = MetadataPolicyProfile.from_data(
                            json.loads(draft_document)
                        )
                    current_resolved_id = resolve_policy(
                        profile, consent.library
                    ).resolved_id
                else:
                    continue
                if consent.envelope_identity == self._field_effect_consent_envelope_identity(
                    consent.library, current_resolved_id
                ):
                    consented_destinations.append((
                        consent.connection, consent.library, consent.destination
                    ))
            counts = {
                state: count for state, count in self._connection.execute(
                    "SELECT state,count(*) FROM field_effects GROUP BY state"
                ).fetchall()
            }
            cleanup = self._connection.execute(
                "SELECT count(*),"
                "coalesce(sum(CASE WHEN attempt>=3 THEN 1 ELSE 0 END),0) "
                "FROM field_effect_cleanups WHERE state='pending'"
            ).fetchone()
            event_rows = self._connection.execute(
                "SELECT effect.connection_name,effect.destination,json_extract(event.document,'$.failure'),"
                "effect.library_name,event.occurred_at,"
                "json_extract(event.document,'$.origin'),lineage.report_id,event.kind,effect.work_id "
                "FROM field_effect_events event JOIN field_effects effect "
                "ON effect.plan_id=event.plan_id LEFT JOIN current_metadata_report_lineages lineage "
                "ON lineage.connection_name=effect.connection_name AND lineage.library_name=effect.library_name AND lineage.item_id=effect.work_id "
                "ORDER BY event.occurred_at DESC,event.event_id DESC LIMIT ?", (limit,),
            ).fetchall()
            report_ids = sorted({row[6] for row in event_rows if row[6] is not None})
            report_documents = {} if not report_ids else dict(self._connection.execute(
                "SELECT report_id,document FROM metadata_reports WHERE report_id IN ("
                + ",".join("?" for _ in report_ids) + ")",
                report_ids,
            ).fetchall())
            events = [{
                "connection": connection,
                "destination": destination,
                "failure": failure,
                "library": library,
                "occurred_at": occurred_at,
                "origin": origin,
                "report_id": report_id,
                "state": state,
                "work_id": work_id,
            } for connection, destination, failure, library, occurred_at, origin, report_id, state, work_id
                in event_rows]
            suspensions = [{
                "connection": connection,
                "destination": destination or None,
                "reason": reason,
                "scope": scope,
                "updated_at": updated_at,
            } for scope, connection, destination, reason, updated_at
                in self._connection.execute(
                    "SELECT scope,connection_name,destination,reason,updated_at "
                    "FROM field_effect_suspensions ORDER BY updated_at DESC,scope,"
                    "connection_name,destination LIMIT 64"
                ).fetchall()]
            return {
                "consented_libraries": [
                    {"connection": connection, "library": library}
                    for connection, library in sorted({
                        (connection, library)
                        for connection, library, _ in consented_destinations
                    })
                ],
                "consented_destinations": [
                    {"connection": connection, "library": library,
                     "destination": destination}
                    for connection, library, destination
                    in consented_destinations
                ],
                "counts": counts,
                "cleanup": {"pending": cleanup[0], "exhausted": cleanup[1]},
                "events": events,
                "report_documents": report_documents,
                "paused": self.field_effect_paused(),
                "suspensions": suspensions,
            }
        except (json.JSONDecodeError, MetadataPolicyError, sqlite3.Error,
                TypeError, ValueError) as error:
            raise FieldEffectError("persistence") from error

    def record_field_effect(self, operation: FieldEffectOperation) -> FieldEffectOperation:
        """Persist a deterministic plan and its first audit fact atomically."""
        try:
            if (operation.state != "planned" or operation.attempt != 0
                    or operation.failure is not None
                    or operation.claim_token is not None
                    or operation.updated_at != operation.plan.created_at):
                raise FieldEffectError("invalid_initial_operation")
            document = canonical_effect_json(operation.to_data())
            self._connection.execute("BEGIN IMMEDIATE")
            existing = self._connection.execute(
                "SELECT document FROM field_effects WHERE plan_id=?",
                (operation.plan.plan_id,),
            ).fetchone()
            if existing is not None:
                persisted = FieldEffectOperation.from_json(existing[0])
                persisted_identity = persisted.plan.to_data()
                attempted_identity = operation.plan.to_data()
                persisted_identity.pop("created_at")
                attempted_identity.pop("created_at")
                if persisted_identity != attempted_identity:
                    raise FieldEffectError("idempotency_conflict")
                if (persisted.state in {"superseded", "abandoned"}
                        and operation.plan.authority.origin == "automatic_policy"
                        and self._connection.execute(
                            "SELECT current FROM field_effect_authorities WHERE decision_id=?",
                            (operation.plan.authority.decision_id,),
                        ).fetchone() == (1,)):
                    self._connection.execute(
                        "UPDATE field_effects SET state='planned',attempt=0,updated_at=?,document=? "
                        "WHERE plan_id=? AND state IN ('superseded','abandoned')",
                        (operation.updated_at, document, operation.plan.plan_id),
                    )
                    self._record_field_effect_event(
                        operation, "planned", operation.updated_at
                    )
                    self._connection.commit()
                    return operation
                self._connection.commit()
                return persisted
            authority_document = canonical_effect_json({
                "authority": operation.plan.to_data()["authority"], "current": True,
                "schema_version": "field-effect-authority-state-v1",
                "updated_at": operation.updated_at,
            })
            authority_existing = self._connection.execute(
                "SELECT document FROM field_effect_authorities WHERE decision_id=?",
                (operation.plan.authority.decision_id,),
            ).fetchone()
            if authority_existing is None:
                self._connection.execute(
                    "INSERT INTO field_effect_authorities(decision_id,current,updated_at,document) VALUES(?,1,?,?)",
                    (operation.plan.authority.decision_id, operation.updated_at, authority_document),
                )
            else:
                persisted_authority = json.loads(authority_existing[0])
                if (persisted_authority.get("authority")
                        != operation.plan.to_data()["authority"]):
                    raise FieldEffectError("authority_conflict")
                if persisted_authority.get("current") is not True:
                    self._connection.execute(
                        "UPDATE field_effect_authorities SET current=1,updated_at=?,document=? "
                        "WHERE decision_id=?",
                        (operation.updated_at, authority_document,
                         operation.plan.authority.decision_id),
                    )
            consent_document = canonical_effect_json({
                "consent": operation.plan.consent.to_data(), "current": True,
                "schema_version": "field-effect-consent-state-v1",
                "updated_at": operation.plan.consent.granted_at,
            })
            consent_existing = self._connection.execute(
                "SELECT document FROM field_effect_consents WHERE consent_id=?",
                (operation.plan.consent.consent_id,),
            ).fetchone()
            if consent_existing is None:
                self._connection.execute(
                    "INSERT INTO field_effect_consents(consent_id,current,updated_at,document) VALUES(?,1,?,?)",
                    (operation.plan.consent.consent_id,
                     operation.plan.consent.granted_at, consent_document),
                )
            elif consent_existing[0] != consent_document:
                raise FieldEffectError("consent_conflict")
            self._connection.execute(
                "INSERT INTO field_effects(plan_id,decision_id,connection_name,library_name,work_id,destination,state,attempt,updated_at,document) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (operation.plan.plan_id, operation.plan.authority.decision_id,
                 operation.plan.connection, operation.plan.library, operation.plan.work_id,
                 operation.plan.destination, operation.state, operation.attempt,
                 operation.updated_at, document),
            )
            self._record_field_effect_event(operation, "planned", operation.updated_at)
            self._connection.commit()
            return operation
        except (sqlite3.Error, FieldEffectError, TypeError, ValueError) as error:
            self._connection.rollback()
            if isinstance(error, FieldEffectError):
                raise
            raise FieldEffectError("persistence") from error

    def get_field_effect(self, plan_id: str) -> FieldEffectOperation:
        try:
            row = self._connection.execute(
                "SELECT document FROM field_effects WHERE plan_id=?", (plan_id,)
            ).fetchone()
            if row is None:
                raise FieldEffectError("not_found")
            return FieldEffectOperation.from_json(row[0])
        except sqlite3.Error as error:
            raise FieldEffectError("persistence") from error

    def field_effect_event_documents(self, plan_id: str) -> tuple[str, ...]:
        """Return one bounded, canonical, gap-free persisted audit sequence."""
        try:
            operation = self.get_field_effect(plan_id)
            rows = self._connection.execute(
                "SELECT event_id,sequence,kind,occurred_at,document "
                "FROM field_effect_events WHERE plan_id=? ORDER BY sequence",
                (plan_id,),
            ).fetchall()
            if not rows or len(rows) > 100:
                raise FieldEffectError("invalid_event")
            documents: list[str] = []
            event_keys = {"attempt", "decision_id", "desired_value", "destination",
                          "event_id", "failure", "kind", "occurred_at", "origin",
                          "plan_id", "pre_value", "schema_version", "sequence", "state"}
            for expected_sequence, (event_id, sequence, kind, occurred_at, document) in enumerate(rows, 1):
                raw = json.loads(document)
                if (type(raw) is not dict or set(raw) != event_keys
                        or raw["schema_version"] != "field-effect-event-v1"
                        or type(raw["sequence"]) is not int or raw["sequence"] < 1
                        or type(raw["attempt"]) is not int or not 0 <= raw["attempt"] <= 3
                        or type(raw["occurred_at"]) is not int or raw["occurred_at"] < 0
                        or raw["failure"] not in FIELD_EFFECT_FAILURES
                        or raw.get("plan_id") != plan_id
                        or raw.get("event_id") != event_id
                        or raw.get("sequence") != sequence
                        or raw.get("kind") != kind
                        or raw.get("occurred_at") != occurred_at
                        or sequence != expected_sequence
                        or raw.get("state") != kind
                        or raw["decision_id"] != operation.plan.authority.decision_id
                        or raw["origin"] != operation.plan.authority.origin
                        or raw["destination"] != operation.plan.destination
                        or raw["pre_value"] != operation.plan.pre_value
                        or raw["desired_value"] != operation.plan.desired_value
                        or canonical_effect_json(raw) != document
                        or event_id != "effect_event_" + hashlib.sha256(
                            canonical_effect_json({key: value for key, value in raw.items()
                                                   if key != "event_id"}).encode()
                        ).hexdigest()):
                    raise FieldEffectError("invalid_event")
                documents.append(document)
            last = json.loads(documents[-1])
            if ((last["state"], last["attempt"], last["failure"])
                    != (operation.state, operation.attempt, operation.failure)):
                raise FieldEffectError("invalid_event")
            return tuple(documents)
        except (sqlite3.Error, json.JSONDecodeError) as error:
            raise FieldEffectError("persistence") from error

    def suspend_field_effect_capability(self, *, scope: str, connection: str,
                                        destination: str, reason: str,
                                        changed_at: int) -> None:
        reasons = {"connection_unavailable", "insufficient_permission",
                   "field_unavailable", "unexpected_effect_behavior"}
        if (scope not in {"connection", "destination"}
                or type(connection) is not str or not 1 <= len(connection) <= 128
                or type(destination) is not str or not 1 <= len(destination) <= 64
                or reason not in reasons or type(changed_at) is not int
                or changed_at < 0):
            raise FieldEffectError("invalid_suspension")
        stored_destination = "" if scope == "connection" else destination
        document = canonical_effect_json({
            "connection": connection,
            "destination": stored_destination,
            "reason": reason,
            "schema_version": "field-effect-suspension-v1",
            "scope": scope,
            "updated_at": changed_at,
        })
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            existing = self._connection.execute(
                "SELECT updated_at FROM field_effect_suspensions "
                "WHERE scope=? AND connection_name=? AND destination=?",
                (scope, connection, stored_destination),
            ).fetchone()
            if existing is not None and existing[0] > changed_at:
                raise FieldEffectError("invalid_suspension")
            self._connection.execute(
                "INSERT INTO field_effect_suspensions(scope,connection_name,destination,reason,updated_at,document) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(scope,connection_name,destination) DO UPDATE SET "
                "reason=excluded.reason,updated_at=excluded.updated_at,document=excluded.document "
                "WHERE field_effect_suspensions.reason!='unexpected_effect_behavior' "
                "OR excluded.reason='unexpected_effect_behavior'",
                (scope, connection, stored_destination, reason, changed_at, document),
            )
            self._connection.commit()
        except (sqlite3.Error, FieldEffectError) as error:
            self._connection.rollback()
            if isinstance(error, FieldEffectError):
                raise
            raise FieldEffectError("persistence") from error

    def field_effect_capability_suspension(self, connection: str,
                                           destination: str) -> str | None:
        try:
            row = self._connection.execute(
                "SELECT reason FROM field_effect_suspensions WHERE "
                "connection_name=? AND ((scope='connection' AND destination='') "
                "OR (scope='destination' AND destination=?)) "
                "ORDER BY CASE scope WHEN 'connection' THEN 0 ELSE 1 END LIMIT 1",
                (connection, destination),
            ).fetchone()
            return None if row is None else str(row[0])
        except sqlite3.Error as error:
            raise FieldEffectError("persistence") from error

    def clear_field_effect_capability_suspension(
        self, *, scope: str, connection: str, destination: str, reason: str,
        observed_at: int,
    ) -> None:
        reasons = {"connection_unavailable", "insufficient_permission",
                   "field_unavailable", "unexpected_effect_behavior"}
        if (scope not in {"connection", "destination"}
                or type(connection) is not str or not connection
                or (scope == "connection" and destination != "")
                or (scope == "destination" and (type(destination) is not str
                                                 or not destination))
                or reason not in reasons
                or type(observed_at) is not int or observed_at < 0):
            raise FieldEffectError("invalid_suspension")
        try:
            self._connection.execute(
                "DELETE FROM field_effect_suspensions "
                "WHERE scope=? AND connection_name=? AND destination=? "
                "AND reason=? AND updated_at<?",
                (scope, connection, destination, reason, observed_at),
            )
            self._connection.commit()
        except sqlite3.Error as error:
            self._connection.rollback()
            raise FieldEffectError("persistence") from error

    def claim_field_effect(self, plan_id: str, expected_state: str,
                           claim_token: str, claimed_at: int) -> FieldEffectOperation:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            if self._connection.execute(
                "SELECT paused FROM field_effect_control WHERE singleton=1"
            ).fetchone() != (0,):
                raise FieldEffectError("paused")
            row = self._connection.execute(
                "SELECT document,state FROM field_effects WHERE plan_id=?", (plan_id,)
            ).fetchone()
            if row is None or row[1] != expected_state:
                raise FieldEffectError("claim_conflict")
            persisted = FieldEffectOperation.from_json(row[0])
            if self.field_effect_capability_suspension(
                    persisted.plan.connection, persisted.plan.destination) is not None:
                raise FieldEffectError("capability")
            if self._connection.execute(
                "SELECT current FROM field_effect_authorities WHERE decision_id=?",
                (persisted.plan.authority.decision_id,),
            ).fetchone() != (1,):
                raise FieldEffectError("authority")
            if self._connection.execute(
                "SELECT current FROM field_effect_consents WHERE consent_id=?",
                (persisted.plan.consent.consent_id,),
            ).fetchone() != (1,):
                raise FieldEffectError("consent")
            if (
                persisted.plan.consent.envelope_identity
                != self.field_effect_consent_envelope_identity(
                    persisted.plan.library
                )
            ):
                raise FieldEffectError("consent")
            authority = persisted.plan.authority
            if authority.source == "persisted_review_case":
                try:
                    case_envelope = json.loads(
                        self.get_review_case(authority.review_case_id)
                    )
                    case = case_envelope["case"]
                    target = case["target"]
                except (MetadataReportError, KeyError, TypeError,
                        json.JSONDecodeError) as error:
                    raise FieldEffectError("authority") from error
                if (case_envelope.get("current") is not True
                        or case.get("target_fingerprint")
                        != authority.target_fingerprint
                        or (target.get("connection"), target.get("library"),
                            target.get("item"))
                        != (persisted.plan.connection, persisted.plan.library,
                            persisted.plan.work_id)):
                    raise FieldEffectError("authority")
                table = ("human_decisions" if authority.origin == "human"
                         else "automatic_decisions")
                decision = self._connection.execute(
                    f"SELECT document FROM {table} WHERE decision_id=? "
                    "AND review_case_id=? AND destination=?",
                    (authority.decision_id, authority.review_case_id,
                     persisted.plan.destination),
                ).fetchone()
                if (decision is None
                        or json.loads(decision[0]).get("target_fingerprint")
                        != authority.target_fingerprint):
                    raise FieldEffectError("authority")
                if authority.origin == "automatic_policy":
                    automatic = json.loads(decision[0])
                    safety = self._connection.execute(
                        "SELECT paused,global_suspension,category_suspensions_document "
                        "FROM automatic_safety_state WHERE singleton=1"
                    ).fetchone()
                    suspended = {} if safety is None else dict(json.loads(safety[2]))
                    if (safety is None or safety[0] != 0 or safety[1] is not None
                            or automatic["policy_category"] in suspended):
                        raise FieldEffectError("authority")
            operation = persisted.transition(
                "claimed", at=claimed_at, claim_token=claim_token
            )
            self._update_field_effect(operation, expected_state)
            self._record_field_effect_event(operation, "claimed", claimed_at)
            self._connection.commit()
            return operation
        except (sqlite3.Error, FieldEffectError) as error:
            self._connection.rollback()
            if isinstance(error, FieldEffectError):
                raise
            raise FieldEffectError("persistence") from error

    def transition_field_effect(self, plan_id: str, expected_state: str,
                                state: str, changed_at: int,
                                failure: str | None = None) -> FieldEffectOperation:
        if state == "reconciling":
            return self.schedule_field_effect_reconciliation(
                plan_id, expected_state, changed_at,
                "ambiguous" if failure is None else failure,
            )
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT document,state FROM field_effects WHERE plan_id=?", (plan_id,)
            ).fetchone()
            if row is None or row[1] != expected_state:
                raise FieldEffectError("transition_conflict")
            operation = FieldEffectOperation.from_json(row[0]).transition(
                state, at=changed_at, failure=failure
            )
            self._update_field_effect(operation, expected_state)
            self._record_field_effect_event(operation, state, changed_at)
            self._retire_stale_cover_consideration(operation, state, changed_at)
            self._schedule_field_effect_cleanup(operation, changed_at)
            self._connection.commit()
            return operation
        except (sqlite3.Error, FieldEffectError) as error:
            self._connection.rollback()
            if isinstance(error, FieldEffectError):
                raise
            raise FieldEffectError("persistence") from error

    def supersede_field_effect(self, decision_id: str, superseded_at: int) -> int:
        return self.revoke_field_effect_authority(decision_id, superseded_at)

    def revoke_field_effect_authority(self, decision_id: str, changed_at: int) -> int:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            count = self._revoke_field_effect_authority_in_transaction(
                decision_id, changed_at, required=True
            )
            self._connection.commit()
            return count
        except (sqlite3.Error, FieldEffectError) as error:
            self._connection.rollback()
            if isinstance(error, FieldEffectError):
                raise
            raise FieldEffectError("persistence") from error

    def _revoke_field_effect_authority_in_transaction(
        self, decision_id: str, changed_at: int, *, required: bool,
    ) -> int:
        row = self._connection.execute(
            "SELECT document,current FROM field_effect_authorities WHERE decision_id=?",
            (decision_id,),
        ).fetchone()
        if row is None:
            if required:
                raise FieldEffectError("authority")
            return 0
        if row[1] == 1:
            raw = json.loads(row[0])
            raw["current"] = False
            raw["updated_at"] = changed_at
            self._connection.execute(
                "UPDATE field_effect_authorities SET current=0,updated_at=?,document=? WHERE decision_id=?",
                (changed_at, canonical_effect_json(raw), decision_id),
            )
        rows = self._connection.execute(
            "SELECT document,state FROM field_effects WHERE decision_id=? AND state IN ('planned','retryable')",
            (decision_id,),
        ).fetchall()
        for document, state in rows:
            operation = FieldEffectOperation.from_json(document).transition(
                "superseded", at=changed_at, failure="authority_superseded"
            )
            self._update_field_effect(operation, state)
            self._record_field_effect_event(operation, "superseded", changed_at)
            self._schedule_field_effect_cleanup(operation, changed_at)
        return len(rows)

    def revoke_field_effect_consent(self, consent_id: str, changed_at: int) -> int:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT document FROM field_effect_consents WHERE consent_id=?",
                (consent_id,),
            ).fetchone()
            if row is None:
                raise FieldEffectError("consent")
            raw = json.loads(row[0])
            raw["current"] = False
            raw["updated_at"] = changed_at
            self._connection.execute(
                "UPDATE field_effect_consents SET current=0,updated_at=?,document=? WHERE consent_id=?",
                (changed_at, canonical_effect_json(raw), consent_id),
            )
            rows = self._connection.execute(
                "SELECT document,state FROM field_effects WHERE json_extract(document,'$.plan.consent.consent_id')=? AND state IN ('planned','retryable')",
                (consent_id,),
            ).fetchall()
            for document, state in rows:
                operation = FieldEffectOperation.from_json(document).transition(
                    "superseded", at=changed_at, failure="consent_revoked"
                )
                self._update_field_effect(operation, state)
                self._record_field_effect_event(operation, "superseded", changed_at)
                self._schedule_field_effect_cleanup(operation, changed_at)
            self._connection.commit()
            return len(rows)
        except (sqlite3.Error, FieldEffectError, json.JSONDecodeError) as error:
            self._connection.rollback()
            if isinstance(error, FieldEffectError):
                raise
            raise FieldEffectError("persistence") from error

    def schedule_field_effect_reconciliation(self, plan_id: str,
                                             expected_state: str,
                                             changed_at: int,
                                             failure: str = "ambiguous") -> FieldEffectOperation:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT document,state FROM field_effects WHERE plan_id=?", (plan_id,)
            ).fetchone()
            if row is None or row[1] != expected_state:
                raise FieldEffectError("transition_conflict")
            operation = FieldEffectOperation.from_json(row[0]).transition(
                "reconciling", at=changed_at, failure=failure
            )
            self._update_field_effect(operation, expected_state)
            self._record_field_effect_event(operation, "reconciling", changed_at)
            self._upsert_field_effect_action(operation, "targeted_read", "pending", 0,
                                             changed_at)
            self._connection.commit()
            return operation
        except (sqlite3.Error, FieldEffectError) as error:
            self._connection.rollback()
            if isinstance(error, FieldEffectError):
                raise
            raise FieldEffectError("persistence") from error

    def complete_field_effect_reconciliation(self, plan_id: str, state: str,
                                             failure: str | None, changed_at: int,
                                             reevaluate: bool = False) -> FieldEffectOperation:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT document,state FROM field_effects WHERE plan_id=?", (plan_id,)
            ).fetchone()
            if row is None or row[1] != "reconciling":
                raise FieldEffectError("transition_conflict")
            operation = FieldEffectOperation.from_json(row[0]).transition(
                state, at=changed_at, failure=failure
            )
            self._update_field_effect(operation, "reconciling")
            self._record_field_effect_event(operation, state, changed_at)
            self._retire_stale_cover_consideration(operation, state, changed_at)
            action = self._connection.execute(
                "SELECT attempt FROM field_effect_actions WHERE plan_id=? "
                "AND kind='targeted_read' AND state='claimed'",
                (plan_id,),
            ).fetchone()
            if action is None:
                raise FieldEffectError("action")
            self._upsert_field_effect_action(operation, "targeted_read", "completed",
                                             action[0], changed_at)
            if reevaluate:
                self._upsert_field_effect_action(operation, "forward_reevaluation",
                                                 "pending", 0, changed_at)
            self._schedule_field_effect_cleanup(operation, changed_at)
            self._connection.commit()
            return operation
        except (sqlite3.Error, FieldEffectError) as error:
            self._connection.rollback()
            if isinstance(error, FieldEffectError):
                raise
            raise FieldEffectError("persistence") from error

    def _retire_stale_cover_consideration(
        self,
        operation: FieldEffectOperation,
        state: str,
        changed_at: int,
    ) -> None:
        if state != "stale" or operation.plan.destination != "cover":
            return
        row = self._connection.execute(
            "SELECT event.consideration_id,event.review_case_id "
            "FROM cover_consideration_events event "
            "JOIN current_cover_considerations current "
            "ON current.consideration_id=event.consideration_id "
            "WHERE event.decision_id=? AND event.kind='selected' "
            "AND current.review_case_id=event.review_case_id",
            (operation.plan.authority.decision_id,),
        ).fetchone()
        if row is None:
            return
        consideration_id, review_case_id = str(row[0]), str(row[1])
        self._record_cover_consideration_event(
            consideration_id,
            review_case_id,
            "stale",
            changed_at,
            operation.plan.authority.decision_id,
        )

    def retire_stale_cover_decision(
        self, decision_id: str, changed_at: int
    ) -> bool:
        """Retire a selected cover whose case ceased to be current before planning."""
        with self._connection:
            row = self._connection.execute(
                "SELECT event.consideration_id,event.review_case_id "
                "FROM cover_consideration_events event "
                "JOIN current_cover_considerations current "
                "ON current.consideration_id=event.consideration_id "
                "WHERE event.decision_id=? AND event.kind='selected' "
                "AND current.review_case_id=event.review_case_id "
                "AND NOT EXISTS (SELECT 1 FROM cover_consideration_events stale "
                "WHERE stale.consideration_id=event.consideration_id "
                "AND stale.kind='stale' AND stale.decision_id=event.decision_id)",
                (decision_id,),
            ).fetchone()
            if row is None:
                return False
            self._record_cover_consideration_event(
                str(row[0]), str(row[1]), "stale", changed_at, decision_id
            )
        return True

    def claim_next_field_effect_action(self, changed_at: int,
                                       plan_id: str | None = None) -> dict[str, object] | None:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            parameters: tuple[object, ...] = () if plan_id is None else (plan_id,)
            where = "state IN ('pending','claimed')"
            if plan_id is not None:
                where += " AND plan_id=?"
            row = self._connection.execute(
                "SELECT action_id,plan_id,kind,state,attempt FROM field_effect_actions "
                f"WHERE {where} ORDER BY CASE kind WHEN 'targeted_read' THEN 0 ELSE 1 END,updated_at,action_id LIMIT 1",
                parameters,
            ).fetchone()
            if row is None:
                self._connection.commit()
                return None
            operation = self.get_field_effect(row[1])
            attempt = min(row[4] + (1 if row[3] == "pending" else 0), 3)
            self._upsert_field_effect_action(
                operation, row[2], "claimed", attempt, changed_at
            )
            self._connection.commit()
            return {"action_id": row[0], "plan_id": row[1],
                    "kind": row[2], "attempt": attempt}
        except (sqlite3.Error, FieldEffectError) as error:
            self._connection.rollback()
            if isinstance(error, FieldEffectError):
                raise
            raise FieldEffectError("persistence") from error

    def complete_forward_reevaluation(self, action_id: str, changed_at: int) -> None:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT plan_id,attempt FROM field_effect_actions "
                "WHERE action_id=? AND kind='forward_reevaluation' AND state='claimed'",
                (action_id,),
            ).fetchone()
            if row is None:
                raise FieldEffectError("action")
            operation = self.get_field_effect(row[0])
            self._upsert_field_effect_action(
                operation, "forward_reevaluation", "completed", row[1], changed_at
            )
            self._connection.commit()
        except (sqlite3.Error, FieldEffectError) as error:
            self._connection.rollback()
            if isinstance(error, FieldEffectError):
                raise
            raise FieldEffectError("persistence") from error

    def field_effect_cleanup_pending(self, plan_id: str) -> bool:
        row = self._connection.execute(
            "SELECT state FROM field_effect_cleanups WHERE plan_id=?", (plan_id,)
        ).fetchone()
        return row == ("pending",)

    def pending_field_effect_cleanups(self, limit: int) -> tuple[str, ...]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise FieldEffectError("invalid_limit")
        return tuple(row[0] for row in self._connection.execute(
            "SELECT plan_id FROM field_effect_cleanups WHERE state='pending' AND attempt<3 "
            "ORDER BY updated_at,plan_id LIMIT ?", (limit,),
        ).fetchall())

    def pending_field_effect_plan_ids(self, limit: int) -> tuple[str, ...]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise FieldEffectError("invalid_limit")
        return tuple(row[0] for row in self._connection.execute(
            "SELECT plan_id FROM field_effects "
            "WHERE state IN ('claimed','applying','reconciling') "
            "ORDER BY updated_at,plan_id LIMIT ?", (limit,),
        ).fetchall())

    def complete_field_effect_cleanup(self, plan_id: str, changed_at: int) -> None:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT attempt FROM field_effect_cleanups WHERE plan_id=? AND state='pending'",
                (plan_id,),
            ).fetchone()
            if row is None:
                self._connection.commit()
                return
            self._upsert_field_effect_cleanup(
                plan_id, "completed", min(row[0] + 1, 3), changed_at
            )
            self._connection.commit()
        except (sqlite3.Error, FieldEffectError) as error:
            self._connection.rollback()
            raise FieldEffectError("persistence") from error

    def fail_field_effect_cleanup(self, plan_id: str, changed_at: int) -> None:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT attempt FROM field_effect_cleanups WHERE plan_id=? AND state='pending'",
                (plan_id,),
            ).fetchone()
            if row is None:
                raise FieldEffectError("cleanup")
            self._upsert_field_effect_cleanup(
                plan_id, "pending", min(row[0] + 1, 3), changed_at
            )
            self._connection.commit()
        except (sqlite3.Error, FieldEffectError) as error:
            self._connection.rollback()
            if isinstance(error, FieldEffectError):
                raise
            raise FieldEffectError("persistence") from error

    def set_field_effect_paused(self, paused: bool, changed_at: int) -> None:
        if type(paused) is not bool or type(changed_at) is not int or changed_at < 0:
            raise FieldEffectError("invalid_control")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute(
                "UPDATE field_effect_control SET paused=?,updated_at=? WHERE singleton=1",
                (int(paused), changed_at),
            )
            self._connection.commit()
        except sqlite3.Error as error:
            self._connection.rollback()
            raise FieldEffectError("persistence") from error

    def field_effect_paused(self) -> bool:
        try:
            return self._connection.execute(
                "SELECT paused FROM field_effect_control WHERE singleton=1"
            ).fetchone() == (1,)
        except sqlite3.Error as error:
            raise FieldEffectError("persistence") from error

    def operational_snapshot(self) -> dict[str, object]:
        """Return only bounded codes and scopes; never persisted effect values."""
        try:
            signals: list[dict[str, object]] = []
            safety = self._connection.execute(
                "SELECT paused,global_suspension,category_suspensions_document "
                "FROM automatic_safety_state WHERE singleton=1"
            ).fetchone()
            if safety is None:
                raise FieldEffectError("persistence")
            if safety[0]:
                signals.append(_operational_signal(
                    "field_suspended", "owner_pause", "runtime"
                ))
            if safety[1] is not None:
                signals.append(_operational_signal(
                    "field_suspended", "automatic_suspension", "runtime"
                ))
            categories = json.loads(safety[2])
            if type(categories) is not list:
                raise FieldEffectError("persistence")
            for entry in categories[:64]:
                if (type(entry) is not list or len(entry) != 2
                        or type(entry[0]) is not str):
                    raise FieldEffectError("persistence")
                category = entry[0]
                signals.append(_operational_signal(
                    "category_suspended", "automatic_suspension", "category",
                    category=category,
                ))
            if self.field_effect_paused():
                signals.append(_operational_signal(
                    "field_suspended", "owner_pause", "runtime"
                ))
            authentication = self._connection.execute(
                "SELECT work.failure_root,work.report_id,report.report_id,"
                "report.document FROM review_preparation_work AS work "
                "LEFT JOIN metadata_reports AS report "
                "ON report.report_id=work.report_id "
                "WHERE work.state='failed' "
                "AND work.failure_code='authentication' "
                "ORDER BY work.updated_at DESC,work.work_id LIMIT 256"
            ).fetchall()
            authentication_roots: set[str] = set()
            for failure_root, work_report_id, report_id, document in authentication:
                if work_report_id is None:
                    if failure_root is None:
                        raise FieldEffectError("persistence")
                    authentication_roots.add(failure_root)
                    continue
                if report_id != work_report_id or type(document) is not str:
                    raise FieldEffectError("persistence")
                try:
                    report = MetadataReport.from_json(document)
                except (ReportValueError, TypeError, ValueError) as error:
                    raise FieldEffectError("persistence") from error
                if report.report_id != report_id:
                    raise FieldEffectError("persistence")
                report_roots = {
                    provider.provider
                    for provider in report.providers
                    if any(
                        status in {
                            ProviderStatus.AUTHENTICATION,
                            ProviderStatus.AUTHORIZATION,
                        }
                        for status in (
                            provider.status,
                            *(lane.status for lane in provider.lanes),
                        )
                    )
                }
                if not report_roots:
                    raise FieldEffectError("persistence")
                authentication_roots.update(report_roots)
            for provider_root in sorted(authentication_roots)[:64]:
                signals.append(_operational_signal(
                    "authentication_failure", "provider_authentication",
                    "connection", connection=provider_root,
                ))
            rows = self._connection.execute(
                "SELECT connection_name,library_name,work_id,destination,state,document "
                "FROM field_effects WHERE state NOT IN ('verified','superseded','abandoned') "
                "ORDER BY updated_at DESC,plan_id LIMIT 256"
            ).fetchall()
            for connection, library, work_id, destination, state, document in rows:
                operation = FieldEffectOperation.from_json(document)
                failure = operation.failure
                if failure in {"target_currentness", "third_value", "readback"}:
                    kind, cause = "owner_action_required", "currentness_conflict"
                    scope = "field"
                    signal_library, signal_work, signal_field = (
                        library, work_id, destination
                    )
                elif failure == "backup":
                    kind, cause = "backup_failure", "backup"
                    scope = "library"
                    signal_library, signal_work, signal_field = library, None, None
                elif failure in {"capability", "target"}:
                    kind, cause = "service_incompatibility", "service_incompatibility"
                    scope = "connection"
                    signal_library, signal_work, signal_field = None, None, None
                elif failure in {"authority", "consent", "consent_revoked"}:
                    kind, cause = "field_suspended", "authority_unavailable"
                    scope = "field"
                    signal_library, signal_work, signal_field = (
                        library, work_id, destination
                    )
                elif state in {"claimed", "applying", "reconciling", "retryable"}:
                    kind, cause = "automatic_recovery", (
                        "reconciliation" if state == "reconciling" else "in_progress"
                    )
                    scope = "field"
                    signal_library, signal_work, signal_field = (
                        library, work_id, destination
                    )
                else:
                    kind, cause = "healthy_activity", "planned"
                    scope = "field"
                    signal_library, signal_work, signal_field = (
                        library, work_id, destination
                    )
                signals.append(_operational_signal(
                    kind, cause, scope, connection=connection,
                    library=signal_library, work_id=signal_work,
                    field=signal_field,
                ))
            return {"signals": signals}
        except (json.JSONDecodeError, sqlite3.Error, FieldEffectError) as error:
            raise FieldEffectError("persistence") from error

    def _upsert_field_effect_action(self, operation: FieldEffectOperation,
                                    kind: str, state: str, attempt: int,
                                    changed_at: int) -> None:
        action_id = "effect_action_" + hashlib.sha256(
            f"{operation.plan.plan_id}:{kind}".encode()
        ).hexdigest()
        document = canonical_effect_json({
            "action_id": action_id, "attempt": attempt, "kind": kind,
            "plan_id": operation.plan.plan_id,
            "schema_version": "field-effect-action-v1", "state": state,
            "updated_at": changed_at,
        })
        self._connection.execute(
            "INSERT INTO field_effect_actions(action_id,plan_id,kind,state,attempt,updated_at,document) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(plan_id,kind) DO UPDATE SET "
            "state=excluded.state,attempt=excluded.attempt,updated_at=excluded.updated_at,document=excluded.document",
            (action_id, operation.plan.plan_id, kind, state, attempt, changed_at, document),
        )

    def _schedule_field_effect_cleanup(self, operation: FieldEffectOperation,
                                       changed_at: int) -> None:
        if (operation.plan.destination == "cover"
                and operation.state in {"verified", "stale", "superseded", "abandoned"}):
            self._upsert_field_effect_cleanup(
                operation.plan.plan_id, "pending", 0, changed_at
            )

    def _upsert_field_effect_cleanup(self, plan_id: str, state: str,
                                     attempt: int, changed_at: int) -> None:
        document = canonical_effect_json({
            "attempt": attempt, "plan_id": plan_id,
            "schema_version": "field-effect-cleanup-v1", "state": state,
            "updated_at": changed_at,
        })
        self._connection.execute(
            "INSERT INTO field_effect_cleanups(plan_id,state,attempt,updated_at,document) "
            "VALUES(?,?,?,?,?) ON CONFLICT(plan_id) DO UPDATE SET "
            "state=excluded.state,attempt=excluded.attempt,updated_at=excluded.updated_at,document=excluded.document",
            (plan_id, state, attempt, changed_at, document),
        )

    def _update_field_effect(self, operation: FieldEffectOperation,
                             expected_state: str) -> None:
        cursor = self._connection.execute(
            "UPDATE field_effects SET state=?,attempt=?,updated_at=?,document=? WHERE plan_id=? AND state=?",
            (operation.state, operation.attempt, operation.updated_at,
             canonical_effect_json(operation.to_data()), operation.plan.plan_id,
             expected_state),
        )
        if cursor.rowcount != 1:
            raise FieldEffectError("transition_conflict")

    def _record_field_effect_event(self, operation: FieldEffectOperation,
                                   kind: str, occurred_at: int) -> None:
        sequence = self._connection.execute(
            "SELECT count(*)+1 FROM field_effect_events WHERE plan_id=?",
            (operation.plan.plan_id,),
        ).fetchone()[0]
        content = {
            "schema_version": "field-effect-event-v1", "plan_id": operation.plan.plan_id,
            "decision_id": operation.plan.authority.decision_id,
            "origin": operation.plan.authority.origin, "destination": operation.plan.destination,
            "pre_value": operation.plan.pre_value, "desired_value": operation.plan.desired_value,
            "state": operation.state, "attempt": operation.attempt, "failure": operation.failure,
            "occurred_at": occurred_at, "kind": kind, "sequence": sequence,
        }
        event_id = "effect_event_" + hashlib.sha256(
            canonical_effect_json(content).encode()
        ).hexdigest()
        document = canonical_effect_json({**content, "event_id": event_id})
        self._connection.execute(
            "INSERT INTO field_effect_events(event_id,plan_id,sequence,kind,occurred_at,document) VALUES(?,?,?,?,?,?)",
            (event_id, operation.plan.plan_id, sequence, kind, occurred_at, document),
        )

    @property
    def authority(self) -> object | None:
        return self._binding

    def request_review_scan(
        self,
        connection: str,
        library: str,
        library_id: str,
        producer_fingerprint: str,
        requested_at: int,
    ) -> PipelineProgress:
        if (
            any(type(value) is not str or not 1 <= len(value) <= 128 for value in (connection, library, library_id))
            or type(producer_fingerprint) is not str
            or len(producer_fingerprint) != 64
            or any(character not in "0123456789abcdef" for character in producer_fingerprint)
            or type(requested_at) is not int
            or requested_at < 0
        ):
            raise MetadataReportError("review preparation request is invalid")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            control = self._connection.execute(
                "SELECT paused FROM review_preparation_control WHERE singleton=1"
            ).fetchone()
            if control is None:
                raise MetadataReportError("review preparation control is unavailable")
            if bool(control[0]):
                raise ReviewPipelineError("paused")
            row = self._connection.execute(
                "SELECT scan_id,state,producer_fingerprint FROM review_preparation_scans "
                "WHERE connection_name=? AND library_name=? LIMIT 1",
                (connection, library),
            ).fetchone()
            if row is not None:
                if row[1] in {"queued", "discovering", "processing"}:
                    if row[2] != producer_fingerprint:
                        raise MetadataReportError(
                            "review preparation producer changed during active work"
                        )
                    if row[1] == "queued":
                        self._connection.execute(
                            "UPDATE review_preparation_scans SET requested_at=?,"
                            "updated_at=? WHERE scan_id=?",
                            (requested_at, requested_at, row[0]),
                        )
                    else:
                        self._connection.execute(
                            "UPDATE review_preparation_scans SET rescan_requested=1,updated_at=? WHERE scan_id=?",
                            (requested_at, row[0]),
                        )
                else:
                    self._connection.execute(
                        "UPDATE review_preparation_scans SET state='queued',"
                        "library_id=?,producer_fingerprint=?,requested_at=?,updated_at=?,"
                        "catalogue_generation=NULL,catalogue_fingerprint=NULL,total=0,"
                        "refresh_count=0,"
                        "rescan_requested=0 WHERE scan_id=?",
                        (
                            library_id,
                            producer_fingerprint,
                            requested_at,
                            requested_at,
                            row[0],
                        ),
                    )
                self._connection.commit()
                progress = self.review_pipeline_progress(connection, library)
                if progress is None:
                    raise MetadataReportError("review preparation state is invalid")
                return progress
            scan_id = pipeline_scan_id(connection, library)
            self._connection.execute(
                "INSERT INTO review_preparation_scans("
                "scan_id,connection_name,library_name,library_id,producer_fingerprint,"
                "state,requested_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    scan_id,
                    connection,
                    library,
                    library_id,
                    producer_fingerprint,
                    "queued",
                    requested_at,
                    requested_at,
                ),
            )
            self._connection.commit()
            return PipelineProgress(
                scan_id, connection, library, "queued", 0, 0, 0, 0, False, requested_at
            )
        except (sqlite3.Error, MetadataReportError, ReviewPipelineError) as error:
            if self._connection.in_transaction:
                self._connection.rollback()
            if isinstance(error, (MetadataReportError, ReviewPipelineError)):
                raise
            raise MetadataReportError("review preparation persistence failed") from error

    def review_pipeline_progress(
        self, connection: str, library: str
    ) -> PipelineProgress | None:
        try:
            row = self._connection.execute(
                "SELECT scan_id,state,total,rescan_requested,updated_at FROM review_preparation_scans "
                "WHERE connection_name=? AND library_name=? "
                "ORDER BY requested_at DESC LIMIT 1",
                (connection, library),
            ).fetchone()
            if row is None:
                return None
            counts = self._connection.execute(
                "SELECT "
                "COALESCE(SUM(CASE WHEN state IN ('complete','failed','stale') THEN 1 ELSE 0 END),0),"
                "COALESCE(SUM(CASE WHEN state='complete' AND review_case_id IS NOT NULL THEN 1 ELSE 0 END),0),"
                "COALESCE(SUM(CASE WHEN state IN ('failed','stale') THEN 1 ELSE 0 END),0) "
                "FROM review_preparation_work WHERE scan_id=?",
                (row[0],),
            ).fetchone()
            return PipelineProgress(
                row[0],
                connection,
                library,
                row[1],
                counts[0],
                row[2],
                counts[1],
                counts[2],
                bool(row[3]),
                row[4],
            )
        except sqlite3.Error as error:
            raise MetadataReportError("review preparation persistence failed") from error

    def review_pipeline_status(
        self, libraries: tuple[tuple[str, str], ...]
    ) -> tuple[tuple[PipelineProgress | None, ...], bool]:
        if (
            type(libraries) is not tuple
            or len(libraries) > 128
            or len(libraries) != len(set(libraries))
            or any(
                type(connection) is not str
                or type(library) is not str
                or not 1 <= len(connection) <= 64
                or not 1 <= len(library) <= 64
                for connection, library in libraries
            )
        ):
            raise MetadataReportError("review preparation scope is invalid")
        try:
            self._connection.execute("BEGIN")
            control = self._connection.execute(
                "SELECT paused FROM review_preparation_control WHERE singleton=1"
            ).fetchone()
            if control is None:
                raise MetadataReportError("review preparation control is unavailable")
            scope = " OR ".join(
                "(s.connection_name=? AND s.library_name=?)" for _ in libraries
            ) or "0"
            rows = self._connection.execute(
                "SELECT s.scan_id,s.connection_name,s.library_name,s.state,s.total,"
                "s.rescan_requested,s.updated_at,"
                "COALESCE(SUM(CASE WHEN w.state IN ('complete','failed','stale') THEN 1 ELSE 0 END),0),"
                "COALESCE(SUM(CASE WHEN w.state='complete' AND w.review_case_id IS NOT NULL THEN 1 ELSE 0 END),0),"
                "COALESCE(SUM(CASE WHEN w.state IN ('failed','stale') THEN 1 ELSE 0 END),0) "
                "FROM review_preparation_scans s "
                "LEFT JOIN review_preparation_work w ON w.scan_id=s.scan_id "
                f"WHERE ({scope}) AND s.scan_id=(SELECT selected.scan_id "
                "FROM review_preparation_scans selected "
                "WHERE selected.connection_name=s.connection_name "
                "AND selected.library_name=s.library_name "
                "ORDER BY selected.requested_at DESC,selected.scan_id DESC LIMIT 1) "
                "GROUP BY s.scan_id,s.connection_name,s.library_name,s.state,s.total,"
                "s.rescan_requested,s.updated_at",
                tuple(value for library in libraries for value in library),
            ).fetchall()
            self._connection.commit()
            progress = {
                (row[1], row[2]): PipelineProgress(
                    row[0], row[1], row[2], row[3], row[7], row[4], row[8], row[9],
                    bool(row[5]), row[6]
                )
                for row in rows
            }
            return tuple(progress.get(library) for library in libraries), bool(control[0])
        except (sqlite3.Error, MetadataReportError) as error:
            if self._connection.in_transaction:
                self._connection.rollback()
            if isinstance(error, MetadataReportError):
                raise
            raise MetadataReportError("review preparation persistence failed") from error

    def review_pipeline_paused(self) -> bool:
        try:
            row = self._connection.execute(
                "SELECT paused FROM review_preparation_control WHERE singleton=1"
            ).fetchone()
            if row is None:
                raise MetadataReportError("review preparation control is unavailable")
            return bool(row[0])
        except sqlite3.Error as error:
            raise MetadataReportError("review preparation persistence failed") from error

    def set_review_pipeline_paused(self, paused: bool, updated_at: int) -> None:
        if type(paused) is not bool or type(updated_at) is not int or updated_at < 0:
            raise MetadataReportError("review preparation control is invalid")
        try:
            self._connection.execute(
                "UPDATE review_preparation_control SET paused=?,updated_at=? WHERE singleton=1",
                (paused, updated_at),
            )
            self._connection.commit()
        except sqlite3.Error as error:
            raise MetadataReportError("review preparation persistence failed") from error

    def recover_review_pipeline(
        self, producers: tuple[tuple[str, str, str, str], ...]
    ) -> None:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute(
                "UPDATE review_preparation_scans SET state='queued' WHERE state='discovering'"
            )
            self._connection.execute(
                "UPDATE review_preparation_work SET state='failed',failure_code='failed',"
                "failure_root=NULL "
                "WHERE state='running' AND attempt>=3"
            )
            self._connection.execute(
                "UPDATE review_preparation_work SET state='queued' "
                "WHERE state='running' AND attempt<3"
            )
            for connection, library, library_id, producer_fingerprint in producers:
                row = self._connection.execute(
                    "SELECT scan_id,state,library_id,producer_fingerprint "
                    "FROM review_preparation_scans "
                    "WHERE connection_name=? AND library_name=?",
                    (connection, library),
                ).fetchone()
                if (
                    row is not None
                    and row[1] in {"queued", "discovering", "processing"}
                    and (row[2], row[3]) != (library_id, producer_fingerprint)
                ):
                    self._connection.execute(
                        "UPDATE review_preparation_scans SET state='queued',"
                        "library_id=?,producer_fingerprint=?,catalogue_generation=NULL,"
                        "catalogue_fingerprint=NULL,total=0,refresh_count=0,"
                        "rescan_requested=0 "
                        "WHERE scan_id=?",
                        (library_id, producer_fingerprint, row[0]),
                    )
                    self._connection.execute(
                        "UPDATE review_preparation_work SET stage='extraction',"
                        "state='queued',attempt=0,recovery_attempt=0,library_id=?,producer_fingerprint=?,"
                        "report_id=NULL,"
                        "proposal_set_id=NULL,review_case_id=NULL,failure_code=NULL,"
                        "failure_root=NULL "
                        "WHERE scan_id=?",
                        (library_id, producer_fingerprint, row[0]),
                    )
            self._connection.commit()
        except sqlite3.Error as error:
            self._connection.rollback()
            raise MetadataReportError("review preparation recovery failed") from error

    def claim_review_pipeline_action(self, claimed_at: int) -> PipelineAction | None:
        owns_transaction = not self._connection.in_transaction
        try:
            if owns_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            active = self._connection.execute(
                "SELECT (SELECT COUNT(*) FROM review_preparation_scans WHERE state='discovering')+"
                "(SELECT COUNT(*) FROM review_preparation_work WHERE state='running')"
            ).fetchone()[0]
            if active >= 2:
                if owns_transaction:
                    self._connection.commit()
                return None
            control = self._connection.execute(
                "SELECT claim_sequence,last_library,circuit_open_until "
                "FROM review_preparation_control WHERE singleton=1"
            ).fetchone()
            if control is None:
                raise MetadataReportError("review preparation control is unavailable")
            if control[1] is not None and (
                type(control[1]) is not str
                or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", control[1]) is None
            ):
                raise MetadataReportError("review preparation control is invalid")
            scan = self._connection.execute(
                "SELECT scan_id,connection_name,library_name,library_id,producer_fingerprint "
                "FROM review_preparation_scans WHERE state='queued' "
                "ORDER BY requested_at,scan_id LIMIT 1"
            ).fetchone()
            if scan is not None:
                self._connection.execute(
                    "UPDATE review_preparation_scans SET state='discovering',updated_at=? "
                    "WHERE scan_id=? AND state='queued'",
                    (claimed_at, scan[0]),
                )
                if owns_transaction:
                    self._connection.commit()
                return PipelineAction(
                    scan[0],
                    scan[0],
                    "discovery",
                    1,
                    scan[1],
                    scan[2],
                    scan[3],
                    scan[4],
                )
            library = self._connection.execute(
                "SELECT library_name FROM review_preparation_work WHERE state='queued' "
                "AND EXISTS(SELECT 1 FROM review_preparation_scans s "
                "WHERE s.scan_id=review_preparation_work.scan_id "
                "AND s.state='processing') "
                "AND retry_not_before<=? "
                "GROUP BY library_name "
                "ORDER BY CASE WHEN library_name>? THEN 0 ELSE 1 END,library_name "
                "LIMIT 1",
                (claimed_at, control[1] or ""),
            ).fetchone()
            ordering = (
                "updated_at DESC,ordinal"
                if control[0] % 4 == 0
                else "updated_at,ordinal"
            )
            work = None if library is None else self._connection.execute(
                "SELECT work_id,scan_id,stage,attempt,connection_name,library_name,library_id,"
                "producer_fingerprint,catalogue_generation,catalogue_fingerprint,"
                "item_id,file_identity,source_revision,"
                "report_id,proposal_set_id,review_case_id,source_fingerprint "
                "FROM review_preparation_work WHERE state='queued' AND library_name=? "
                "AND EXISTS(SELECT 1 FROM review_preparation_scans s "
                "WHERE s.scan_id=review_preparation_work.scan_id "
                "AND s.state='processing') "
                "AND retry_not_before<=? "
                f"ORDER BY {ordering} LIMIT 1",
                (
                    library[0],
                    claimed_at,
                ),
            ).fetchone()
            if work is not None:
                attempt = work[3] + 1
                updated = self._connection.execute(
                    "UPDATE review_preparation_work SET state='running',attempt=?,updated_at=? "
                    "WHERE work_id=? AND state='queued'",
                    (attempt, claimed_at, work[0]),
                ).rowcount
                if updated != 1:
                    raise MetadataReportError("review preparation claim changed")
                self._connection.execute(
                    "UPDATE review_preparation_control SET claim_sequence=claim_sequence+1,"
                    "last_library=?,updated_at=? WHERE singleton=1",
                    (work[5], claimed_at),
                )
                if owns_transaction:
                    self._connection.commit()
                return PipelineAction(
                    work[0],
                    work[1],
                    work[2],
                    attempt,
                    work[4],
                    work[5],
                    work[6],
                    work[7],
                    work[8],
                    work[9],
                    work[10],
                    work[11],
                    work[12],
                    work[13],
                    work[14],
                    work[15],
                    work[16],
                )
            scans = self._connection.execute(
                "SELECT scan_id,rescan_requested FROM review_preparation_scans "
                "WHERE state='processing' AND NOT EXISTS("
                "SELECT 1 FROM review_preparation_work w "
                "WHERE w.scan_id=review_preparation_scans.scan_id "
                "AND w.state IN ('queued','running'))"
            ).fetchall()
            for scan_id, rescan_requested in scans:
                pending_manual_return = (
                    self._has_pending_field_effect_manual_return(scan_id)
                )
                failures = self._connection.execute(
                    "SELECT COUNT(*) FROM review_preparation_work "
                    "WHERE scan_id=? AND state='failed'",
                    (scan_id,),
                ).fetchone()[0]
                stale = self._connection.execute(
                    "SELECT COUNT(*) FROM review_preparation_work "
                    "WHERE scan_id=? AND state='stale'",
                    (scan_id,),
                ).fetchone()[0]
                if rescan_requested and pending_manual_return:
                    if failures or stale:
                        self._connection.execute(
                            "DELETE FROM review_preparation_absences WHERE scan_id=?",
                            (scan_id,),
                        )
                    self._connection.execute(
                        "UPDATE review_preparation_scans SET state='queued',"
                        "catalogue_generation=NULL,catalogue_fingerprint=NULL,total=0,"
                        "refresh_count=0,rescan_requested=0,updated_at=? "
                        "WHERE scan_id=?",
                        (claimed_at, scan_id),
                    )
                elif failures:
                    self._connection.execute(
                        "DELETE FROM review_preparation_absences WHERE scan_id=?",
                        (scan_id,),
                    )
                    self._connection.execute(
                        "UPDATE review_preparation_scans SET state='completed_with_failures',"
                        "rescan_requested=0,updated_at=? WHERE scan_id=?",
                        (claimed_at, scan_id),
                    )
                elif rescan_requested:
                    self._connection.execute(
                        "UPDATE review_preparation_scans SET state='queued',"
                        "catalogue_generation=NULL,catalogue_fingerprint=NULL,total=0,"
                        "rescan_requested=0,updated_at=? WHERE scan_id=?",
                        (claimed_at, scan_id),
                    )
                else:
                    state = "completed_with_failures" if stale else "completed"
                    if stale:
                        self._connection.execute(
                            "DELETE FROM review_preparation_absences WHERE scan_id=?",
                            (scan_id,),
                        )
                    else:
                        self._commit_review_absences(scan_id)
                    self._connection.execute(
                        "UPDATE review_preparation_scans SET state=?,updated_at=? WHERE scan_id=?",
                        (state, claimed_at, scan_id),
                    )
            if owns_transaction:
                self._connection.commit()
            return None
        except (sqlite3.Error, MetadataReportError) as error:
            if self._connection.in_transaction:
                self._connection.rollback()
            if isinstance(error, MetadataReportError):
                raise
            raise MetadataReportError("review preparation claim failed") from error

    def complete_review_discovery(
        self,
        action: PipelineAction,
        generation: int,
        fingerprint: str,
        entries: tuple[CatalogueEntry, ...],
        completed_at: int,
    ) -> None:
        if action.stage != "discovery":
            raise MetadataReportError("review preparation state is invalid")
        selected = tuple(
            entry
            for entry in entries
            if entry.connection == action.connection
            and entry.library == action.library
            and entry.library_id == action.library_id
        )
        eligible = tuple(
            entry
            for entry in selected
            if entry.eligible and entry.file_identity is not None
        )
        present_items = {entry.item_id for entry in selected}
        eligible_items = {entry.item_id for entry in eligible}
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            current = self._connection.execute(
                "SELECT state FROM review_preparation_scans WHERE scan_id=?",
                (action.scan_id,),
            ).fetchone()
            if current != ("discovering",):
                raise MetadataReportError("review preparation state changed")
            previous_retirements = self._connection.execute(
                "SELECT item_id,proposal_set_id,report_id,review_case_id,checkpoint,kind,reason "
                "FROM review_preparation_absences WHERE scan_id=? ORDER BY item_id",
                (action.scan_id,),
            ).fetchall()
            pointers = self._connection.execute(
                "SELECT item_id,proposal_set_id,report_id,review_case_id,checkpoint "
                "FROM current_review_case_lineages "
                "WHERE connection_name=? AND library_name=? AND review_case_id IS NOT NULL",
                (action.connection, action.library),
            ).fetchall()
            self._connection.execute(
                "DELETE FROM review_preparation_absences WHERE scan_id=?",
                (action.scan_id,),
            )
            for item_id, proposal_id, report_id, case_id, checkpoint in pointers:
                if item_id in eligible_items:
                    continue
                kind = "tombstoned" if item_id not in present_items else "retired"
                reason = "absent" if kind == "tombstoned" else "ineligible"
                self._connection.execute(
                    "INSERT INTO review_preparation_absences("
                    "scan_id,item_id,proposal_set_id,report_id,review_case_id,checkpoint,kind,reason"
                    ") VALUES(?,?,?,?,?,?,?,?)",
                    (
                        action.scan_id,
                        item_id,
                        proposal_id,
                        report_id,
                        case_id,
                        checkpoint,
                        kind,
                        reason,
                    ),
                )
            staged_retirements = self._connection.execute(
                "SELECT item_id,proposal_set_id,report_id,review_case_id,checkpoint,kind,reason "
                "FROM review_preparation_absences WHERE scan_id=? ORDER BY item_id",
                (action.scan_id,),
            ).fetchall()
            confirmation_required = bool(staged_retirements) and not previous_retirements
            if previous_retirements:
                confirmed = set(previous_retirements).intersection(staged_retirements)
                for candidate in staged_retirements:
                    if candidate not in confirmed:
                        self._connection.execute(
                            "DELETE FROM review_preparation_absences "
                            "WHERE scan_id=? AND item_id=?",
                            (action.scan_id, candidate[0]),
                        )
            for ordinal, entry in enumerate(
                sorted(eligible, key=lambda value: value.item_id)
            ):
                assert entry.file_identity is not None
                material = (
                    f"review-preparation-work-v1\0{action.connection}\0"
                    f"{action.library}\0{entry.item_id}"
                )
                work_id = "review_work_" + hashlib.sha256(
                    material.encode("utf-8")
                ).hexdigest()
                existing = self._connection.execute(
                    "SELECT file_identity,source_fingerprint,producer_fingerprint,"
                    "stage,state,report_id,proposal_set_id,review_case_id "
                    "FROM review_preparation_work WHERE work_id=?",
                    (work_id,),
                ).fetchone()
                stage = "extraction"
                state = "queued"
                report_id = proposal_set_id = review_case_id = None
                if existing is not None and existing[:3] == (
                    entry.file_identity,
                    entry.source_fingerprint,
                    action.producer_fingerprint,
                ):
                    stage, previous_state = existing[3], existing[4]
                    report_id, proposal_set_id, review_case_id = existing[5:]
                    report_current = True
                    if report_id is not None:
                        report_current = self._connection.execute(
                            "SELECT report_id FROM current_metadata_reports "
                            "WHERE connection_name=? AND library_name=? AND item_id=? "
                            "AND file_identity=?",
                            (
                                action.connection,
                                action.library,
                                entry.item_id,
                                entry.file_identity,
                            ),
                        ).fetchone() == (report_id,)
                    if not report_current or previous_state == "stale":
                        stage = "extraction"
                        report_id = proposal_set_id = review_case_id = None
                    elif previous_state == "failed" and stage == "provider":
                        stage = "extraction"
                        report_id = proposal_set_id = review_case_id = None
                    elif previous_state == "complete":
                        pointer = self._connection.execute(
                            "SELECT proposal_set_id,report_id,review_case_id,outcome "
                            "FROM current_review_case_lineages WHERE connection_name=? "
                            "AND library_name=? AND item_id=?",
                            (action.connection, action.library, entry.item_id),
                        ).fetchone()
                        pointer_matches = (
                            pointer is not None
                            and pointer[:3]
                            == (proposal_set_id, report_id, review_case_id)
                            and (
                                pointer[3] in {"admitted", "superseded"}
                                if review_case_id is not None
                                else pointer[3] in {"no_case", "retired"}
                            )
                        )
                        if pointer_matches:
                            stage = "complete"
                            state = "complete"
                        else:
                            stage = "admission"
                            review_case_id = None
                self._connection.execute(
                    "INSERT INTO review_preparation_work("
                    "work_id,scan_id,ordinal,connection_name,library_name,library_id,"
                    "item_id,file_identity,source_revision,source_fingerprint,producer_fingerprint,"
                    "catalogue_generation,catalogue_fingerprint,stage,state,attempt,recovery_attempt,retry_not_before,"
                    "report_id,proposal_set_id,review_case_id,failure_code,failure_root,updated_at"
                    ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(work_id) DO UPDATE SET ordinal=excluded.ordinal,"
                    "library_id=excluded.library_id,file_identity=excluded.file_identity,"
                    "source_revision=excluded.source_revision,"
                    "source_fingerprint=excluded.source_fingerprint,"
                    "producer_fingerprint=excluded.producer_fingerprint,"
                    "catalogue_generation=excluded.catalogue_generation,"
                    "catalogue_fingerprint=excluded.catalogue_fingerprint,"
                    "stage=excluded.stage,state=excluded.state,attempt=0,recovery_attempt=0,retry_not_before=0,"
                    "report_id=excluded.report_id,proposal_set_id=excluded.proposal_set_id,"
                    "review_case_id=excluded.review_case_id,failure_code=NULL,failure_root=NULL,"
                    "updated_at=excluded.updated_at",
                    (
                        work_id,
                        action.scan_id,
                        ordinal,
                        action.connection,
                        action.library,
                        action.library_id,
                        entry.item_id,
                        entry.file_identity,
                        entry.updated_at,
                        entry.source_fingerprint,
                        action.producer_fingerprint,
                        generation,
                        fingerprint,
                        stage,
                        state,
                        0,
                        0,
                        0,
                        report_id,
                        proposal_set_id,
                        review_case_id,
                        None,
                        None,
                        completed_at,
                    ),
                )
            for work_id, item_id in self._connection.execute(
                "SELECT work_id,item_id FROM review_preparation_work WHERE scan_id=?",
                (action.scan_id,),
            ).fetchall():
                if item_id not in eligible_items:
                    self._connection.execute(
                        "DELETE FROM review_preparation_work WHERE work_id=?", (work_id,)
                    )
            self._connection.execute(
                "UPDATE review_preparation_scans SET state='processing',"
                "catalogue_generation=?,catalogue_fingerprint=?,total=?,"
                "refresh_count=refresh_count+1,rescan_requested="
                "CASE WHEN ? THEN 1 ELSE rescan_requested END,"
                "updated_at=? "
                "WHERE scan_id=?",
                (
                    generation,
                    fingerprint,
                    len(eligible),
                    confirmation_required,
                    completed_at,
                    action.scan_id,
                ),
            )
            self._connection.commit()
        except (sqlite3.Error, MetadataReportError, ReviewCaseValueError) as error:
            if self._connection.in_transaction:
                self._connection.rollback()
            if isinstance(error, MetadataReportError):
                raise
            raise MetadataReportError("review preparation discovery failed") from error

    def _commit_review_absences(self, scan_id: str) -> None:
        scan = self._connection.execute(
            "SELECT connection_name,library_name FROM review_preparation_scans "
            "WHERE scan_id=? AND state='processing'",
            (scan_id,),
        ).fetchone()
        if scan is None:
            raise MetadataReportError("review preparation state changed")
        connection, library = scan
        candidates = self._connection.execute(
            "SELECT item_id,proposal_set_id,report_id,review_case_id,checkpoint,kind,reason "
            "FROM review_preparation_absences WHERE scan_id=? ORDER BY item_id",
            (scan_id,),
        ).fetchall()
        for (
            item_id,
            proposal_id,
            report_id,
            case_id,
            checkpoint,
            kind,
            reason,
        ) in candidates:
            current = self._connection.execute(
                "SELECT proposal_set_id,report_id,review_case_id,checkpoint "
                "FROM current_review_case_lineages WHERE connection_name=? "
                "AND library_name=? AND item_id=?",
                (connection, library, item_id),
            ).fetchone()
            if current != (proposal_id, report_id, case_id, checkpoint):
                continue
            transition = ReviewTransition(
                connection,
                library,
                item_id,
                checkpoint + 1,
                proposal_id,
                report_id,
                kind,
                case_id,
                None,
                reason,
                "review-preparation-v1",
            )
            self._connection.execute(
                "INSERT INTO review_case_transitions("
                "transition_id,connection_name,library_name,item_id,checkpoint,actor,"
                "from_review_case_id,to_review_case_id,proposal_set_id,report_id,kind,reason"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    transition.transition_id,
                    connection,
                    library,
                    item_id,
                    checkpoint + 1,
                    transition.actor,
                    case_id,
                    None,
                    proposal_id,
                    report_id,
                    kind,
                    reason,
                ),
            )
            self._connection.execute(
                "UPDATE current_review_case_lineages SET review_case_id=NULL,"
                "transition_id=?,checkpoint=?,outcome=? "
                "WHERE connection_name=? AND library_name=? AND item_id=? "
                "AND review_case_id=? AND checkpoint=?",
                (
                    transition.transition_id,
                    checkpoint + 1,
                    kind,
                    connection,
                    library,
                    item_id,
                    case_id,
                    checkpoint,
                ),
            )
            if self._connection.execute(
                "DELETE FROM current_review_packages WHERE connection_name=? AND library_name=? AND item_id=? AND proposal_set_id=? AND report_id=? AND review_case_id=?",
                (connection, library, item_id, proposal_id, report_id, case_id),
            ).rowcount != 1:
                raise MetadataReportError("review preparation state changed")
        self._connection.execute(
            "DELETE FROM review_preparation_absences WHERE scan_id=?", (scan_id,)
        )

    def advance_review_pipeline_action(
        self,
        action: PipelineAction,
        output_id: str | None,
        completed_at: int,
        claim_next: bool = False,
    ) -> PipelineAction | None:
        if type(claim_next) is not bool:
            raise MetadataReportError("review preparation state is invalid")
        stages = {
            "extraction": ("provider", "report_id"),
            "provider": ("proposal", None),
            "proposal": ("admission", "proposal_set_id"),
            "admission": ("projection", "review_case_id"),
            "projection": ("complete", None),
        }
        target = stages.get(action.stage)
        if target is None:
            raise MetadataReportError("review preparation state is invalid")
        if action.stage == "admission" and output_id is None:
            target = ("complete", None)
        try:
            if not self._connection.in_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            values: list[object] = [target[0], completed_at]
            assignment = ""
            if target[1] is not None:
                assignment = f",{target[1]}=?"
                values.append(output_id)
            values.extend((action.action_id, action.stage, action.attempt))
            updated = self._connection.execute(
                "UPDATE review_preparation_work SET stage=?,attempt=0,state="
                + ("'complete'" if target[0] == "complete" else "'queued'")
                + ",updated_at=?"
                + assignment
                + " WHERE work_id=? AND stage=? AND state='running' AND attempt=?",
                tuple(values),
            ).rowcount
            if updated != 1:
                raise MetadataReportError("review preparation state changed")
            if action.stage == "extraction" and output_id is not None:
                transition_row = self._connection.execute(
                    "SELECT transition_id,report_id FROM metadata_policy_transition_items "
                    "WHERE work_id=? AND state='pending'",
                    (action.action_id,),
                ).fetchone()
                if transition_row is not None and output_id != transition_row[1]:
                    self._connection.execute(
                        "UPDATE metadata_policy_transition_items SET state='failed',failure_code='stale',updated_at=? "
                        "WHERE transition_id=? AND work_id=? AND state='pending'",
                        (completed_at, transition_row[0], action.action_id),
                    )
                    self._refresh_metadata_policy_transition(
                        transition_row[0], completed_at
                    )
            if (
                target[0] == "complete"
                and action.stage in {"admission", "projection"}
                and action.proposal_set_id is not None
                and action.report_id is not None
            ):
                pointer = self._connection.execute(
                    "SELECT proposal_set_id,report_id,review_case_id,checkpoint,outcome FROM current_review_case_lineages "
                    "WHERE connection_name=? AND library_name=? AND item_id=?",
                    (action.connection, action.library, action.item_id),
                ).fetchone()
                if (
                    pointer is None
                    or pointer[0] != action.proposal_set_id
                    or pointer[1] != action.report_id
                    or pointer[4] not in {"admitted", "superseded", "no_case", "retired"}
                    or (action.stage == "projection" and pointer[2] != action.review_case_id)
                ):
                    raise MetadataReportError("review preparation state changed")
                self._connection.execute(
                    "INSERT INTO current_review_packages(connection_name,library_name,item_id,proposal_set_id,report_id,review_case_id,checkpoint,outcome) "
                    "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(connection_name,library_name,item_id) DO UPDATE SET "
                    "proposal_set_id=excluded.proposal_set_id,report_id=excluded.report_id,"
                    "review_case_id=excluded.review_case_id,checkpoint=excluded.checkpoint,outcome=excluded.outcome",
                    (action.connection, action.library, action.item_id, *pointer),
                )
            if target[0] == "complete":
                transition_row = self._connection.execute(
                    "SELECT transition_id FROM metadata_policy_transition_items WHERE work_id=? AND state='pending'",
                    (action.action_id,),
                ).fetchone()
                if transition_row is not None:
                    self._connection.execute(
                        "UPDATE metadata_policy_transition_items SET state='complete',failure_code=NULL,updated_at=? WHERE transition_id=? AND work_id=? AND state='pending'",
                        (completed_at, transition_row[0], action.action_id),
                    )
                    self._refresh_metadata_policy_transition(transition_row[0], completed_at)
            if action.stage == "provider":
                self._connection.execute(
                    "UPDATE review_preparation_control SET circuit_failures="
                    "CASE WHEN circuit_open_until<=? THEN 0 ELSE circuit_failures END,"
                    "circuit_open_until=CASE WHEN circuit_open_until<=? THEN 0 "
                    "ELSE circuit_open_until END,updated_at=? WHERE singleton=1",
                    (completed_at, completed_at, completed_at),
                )
            next_action = (
                self.claim_review_pipeline_action(completed_at)
                if claim_next
                else None
            )
            self._connection.commit()
            return next_action
        except (sqlite3.Error, MetadataReportError) as error:
            if self._connection.in_transaction:
                self._connection.rollback()
            if isinstance(error, MetadataReportError):
                raise
            raise MetadataReportError("review preparation persistence failed") from error

    def begin_review_pipeline_output(self) -> None:
        try:
            if self._connection.in_transaction:
                raise MetadataReportError("review preparation state is invalid")
            self._connection.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as error:
            raise MetadataReportError("review preparation persistence failed") from error

    def take_review_pipeline_claim(self) -> PipelineAction | None:
        action = self._review_pipeline_claim
        self._review_pipeline_claim = None
        return action

    def rollback_review_pipeline_output(self) -> None:
        try:
            if self._connection.in_transaction:
                self._connection.rollback()
        except sqlite3.Error as error:
            raise MetadataReportError("review preparation persistence failed") from error

    def complete_review_extraction_publication(
        self,
        action: PipelineAction,
        report_id: str,
        completed_at: int,
        recovery_delay_seconds: int = 0,
        provider_failure_code: str | None = None,
        claim_next: bool = False,
        provider_failure_root: str | None = None,
    ) -> None:
        """Bind report publication and provider disposition atomically."""
        if (
            not self._connection.in_transaction
            or action.stage != "extraction"
            or type(recovery_delay_seconds) is not int
            or not 0 <= recovery_delay_seconds <= 86_400
            or provider_failure_code
            not in {None, "provider", "configuration", "authentication", "transient"}
            or type(claim_next) is not bool
            or (
                provider_failure_root is not None
                and (
                    provider_failure_code != "authentication"
                    or type(provider_failure_root) is not str
                    or re.fullmatch(
                        r"[a-z][a-z0-9_-]{0,63}", provider_failure_root
                    ) is None
                )
            )
            or (
                provider_failure_code == "authentication"
                and provider_failure_root is None
            )
        ):
            raise MetadataReportError("review preparation state is invalid")
        if provider_failure_code == "transient":
            updated = self._connection.execute(
                "UPDATE review_preparation_work SET "
                "stage=CASE WHEN recovery_attempt<2 THEN 'extraction' ELSE 'proposal' END,"
                "state='queued',attempt=0,recovery_attempt=recovery_attempt+1,"
                "retry_not_before=CASE WHEN recovery_attempt<2 THEN ? "
                "ELSE 0 END,report_id=?,proposal_set_id=NULL,review_case_id=NULL,"
                "failure_code='transient',failure_root=NULL,"
                "updated_at=? WHERE work_id=? AND stage='extraction' "
                "AND state='running' AND attempt=?",
                (
                    completed_at + max(
                        recovery_delay_seconds,
                        5 * (1 << min(3, action.attempt)),
                    ),
                    report_id,
                    completed_at,
                    action.action_id,
                    action.attempt,
                ),
            ).rowcount
        else:
            terminal_failure = provider_failure_code in {"authentication", "configuration"}
            updated = self._connection.execute(
                "UPDATE review_preparation_work SET stage=?,state=?,attempt=0,"
                "retry_not_before=0,report_id=?,failure_code=?,failure_root=?,updated_at=? WHERE work_id=? "
                "AND stage='extraction' AND state='running' AND attempt=?",
                (
                    "provider" if terminal_failure else "proposal",
                    "failed" if terminal_failure else "queued",
                    report_id,
                    provider_failure_code,
                    provider_failure_root,
                    completed_at,
                    action.action_id,
                    action.attempt,
                ),
            ).rowcount
        if updated != 1:
            raise MetadataReportError("review preparation state changed")
        transition_row = self._connection.execute(
            "SELECT transition_id,report_id FROM metadata_policy_transition_items "
            "WHERE work_id=? AND state='pending'",
            (action.action_id,),
        ).fetchone()
        if transition_row is not None:
            work_state = self._connection.execute(
                "SELECT state FROM review_preparation_work WHERE work_id=?",
                (action.action_id,),
            ).fetchone()
            failure_code = (
                "stale"
                if report_id != transition_row[1]
                else (
                    "failed"
                    if work_state is not None
                    and work_state[0] in {"failed", "stale"}
                    else None
                )
            )
            if failure_code is not None:
                self._connection.execute(
                    "UPDATE metadata_policy_transition_items SET state='failed',"
                    "failure_code=?,updated_at=? WHERE transition_id=? AND work_id=? "
                    "AND state='pending'",
                    (
                        failure_code,
                        completed_at,
                        transition_row[0],
                        action.action_id,
                    ),
                )
                self._refresh_metadata_policy_transition(
                    transition_row[0], completed_at
                )
        self._review_pipeline_claim = (
            self.claim_review_pipeline_action(completed_at) if claim_next else None
        )

    def fail_review_pipeline_action(
        self,
        action: PipelineAction,
        code: str,
        retryable: bool,
        completed_at: int,
        failure_root: str | None = None,
    ) -> None:
        if action.stage == "discovery":
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                pending_manual_return = (
                    self._has_pending_field_effect_manual_return(action.scan_id)
                )
                updated = self._connection.execute(
                    "UPDATE review_preparation_scans SET state="
                    "CASE WHEN rescan_requested=1 AND ? THEN 'queued' "
                    "ELSE 'completed_with_failures' END,"
                    "refresh_count=CASE WHEN rescan_requested=1 AND ? "
                    "THEN 0 ELSE refresh_count END,"
                    "rescan_requested=0,updated_at=? "
                    "WHERE scan_id=? AND state='discovering'",
                    (
                        pending_manual_return, pending_manual_return,
                        completed_at, action.scan_id,
                    ),
                ).rowcount
                if updated != 1:
                    raise MetadataReportError("review preparation state changed")
                self._connection.commit()
                return
            except (sqlite3.Error, MetadataReportError) as error:
                if self._connection.in_transaction:
                    self._connection.rollback()
                if isinstance(error, MetadataReportError):
                    raise
                raise MetadataReportError("review preparation persistence failed") from error
        if code not in {
            "provider",
            "configuration",
            "authentication",
            "stale",
            "transient",
            "projection",
            "failed",
            "currentness",
        }:
            code = "failed"
        if (failure_root is not None and (
                code != "authentication"
                or type(failure_root) is not str
                or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", failure_root) is None
        )):
            code = "failed"
            failure_root = None
        if code != "authentication":
            failure_root = None
        state = "queued" if retryable and action.attempt < 3 else (
            "stale" if code in {"stale", "currentness"} else "failed"
        )
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            if code == "transient" and retryable:
                updated = self._connection.execute(
                    "UPDATE review_preparation_work SET stage='extraction',"
                    "state=CASE WHEN recovery_attempt<2 THEN 'queued' ELSE 'failed' END,"
                    "attempt=0,recovery_attempt=recovery_attempt+1,"
                    "retry_not_before=CASE WHEN recovery_attempt<2 THEN ? ELSE 0 END,"
                    "report_id=NULL,proposal_set_id=NULL,review_case_id=NULL,"
                    "failure_code='transient',failure_root=NULL,updated_at=? WHERE work_id=? AND stage=? "
                    "AND state='running' AND attempt=?",
                    (
                        completed_at + 5 * (1 << min(3, action.attempt)),
                        completed_at,
                        action.action_id,
                        action.stage,
                        action.attempt,
                    ),
                ).rowcount
                if updated == 1:
                    state = self._connection.execute(
                        "SELECT state FROM review_preparation_work WHERE work_id=?",
                        (action.action_id,),
                    ).fetchone()[0]
            else:
                updated = self._connection.execute(
                    "UPDATE review_preparation_work SET state=?,failure_code=?,failure_root=?,updated_at=? "
                    "WHERE work_id=? AND stage=? AND state='running' AND attempt=?",
                    (
                        state,
                        code,
                        failure_root,
                        completed_at,
                        action.action_id,
                        action.stage,
                        action.attempt,
                    ),
                ).rowcount
            if updated != 1:
                raise MetadataReportError("review preparation state changed")
            if state == "stale":
                self._connection.execute(
                    "UPDATE review_preparation_scans SET rescan_requested="
                    "CASE WHEN refresh_count<3 THEN 1 ELSE 0 END,updated_at=? "
                    "WHERE scan_id=?",
                    (completed_at, action.scan_id),
                )
            if state in {"failed", "stale"}:
                transition_row = self._connection.execute(
                    "SELECT transition_id FROM metadata_policy_transition_items WHERE work_id=? AND state='pending'",
                    (action.action_id,),
                ).fetchone()
                if transition_row is not None:
                    self._connection.execute(
                        "UPDATE metadata_policy_transition_items SET state='failed',failure_code=?,updated_at=? WHERE transition_id=? AND work_id=? AND state='pending'",
                        ("stale" if state == "stale" else "failed", completed_at, transition_row[0], action.action_id),
                    )
                    self._refresh_metadata_policy_transition(transition_row[0], completed_at)
            self._connection.commit()
        except (sqlite3.Error, MetadataReportError) as error:
            if self._connection.in_transaction:
                self._connection.rollback()
            if isinstance(error, MetadataReportError):
                raise
            raise MetadataReportError("review preparation persistence failed") from error

    @contextmanager
    def _consistent_review_read(self) -> Iterator[None]:
        """Keep one review projection on one authoritative SQLite snapshot."""
        try:
            if self._binding is not None:
                self._binding.require_current()
            self._connection.execute("BEGIN")
            yield
            self._connection.execute("COMMIT")
            if self._binding is not None:
                self._binding.require_current()
        except MetadataReportError:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except Exception as error:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise MetadataReportError("review snapshot is unavailable") from error

    def publish(
        self,
        report: MetadataReport,
        currentness: Callable[[], bool],
        expected_current: str | None = None,
        after_publication: Callable[[object, MetadataReport], None] | None = None,
    ) -> str:
        if type(report) is not MetadataReport or not callable(currentness):
            raise MetadataReportError("report publication failed")
        document = report.to_json()
        digest = hashlib.sha256(document.encode("utf-8")).hexdigest()
        values = (
            report.report_id,
            report.target.connection,
            report.target.library,
            report.target.item,
            report.file.identity,
            report.file.content_digest,
            report.schema_version,
            digest,
            document,
        )
        try:
            if self._binding is not None:
                self._binding.require_current()
            if currentness() is not True:
                raise MetadataReportError("target or media changed")
            self._connection.execute("BEGIN IMMEDIATE")
            if self._binding is not None:
                self._binding.require_current()
            current = self._connection.execute(
                "SELECT r.report_id,r.connection_name,r.library_name,r.item_id,r.file_identity,r.content_digest,r.schema_version,r.document_digest,r.document FROM current_metadata_reports c JOIN metadata_reports r ON r.report_id=c.report_id WHERE c.connection_name=? AND c.library_name=? AND c.item_id=? AND c.file_identity=?",
                values[1:5],
            ).fetchone()
            if current is not None:
                document = _validate_report_row(current)
                persisted = MetadataReport.from_json(document)
                if (persisted.target.connection, persisted.target.library, persisted.target.item, persisted.file.identity) != values[1:5]:
                    raise MetadataReportError("persisted current report scope disagrees")
                if (
                    persisted.report_id != report.report_id
                    and persisted.report_id != expected_current
                ):
                    raise MetadataReportError("current report changed")
            elif expected_current is not None:
                raise MetadataReportError("current report changed")
            existing = self._connection.execute(
                "SELECT connection_name,library_name,item_id,file_identity,content_digest,schema_version,document_digest,document FROM metadata_reports WHERE report_id=?",
                (report.report_id,),
            ).fetchone()
            if existing is None:
                self._connection.execute(
                    "INSERT INTO metadata_reports(report_id,connection_name,library_name,item_id,file_identity,content_digest,schema_version,document_digest,document) VALUES(?,?,?,?,?,?,?,?,?)",
                    values,
                )
                for source in report.cover_sources:
                    self._connection.execute(
                    "INSERT INTO metadata_report_cover_references(report_id,source_id,artifact_id,preview_digest,preview_variant) VALUES(?,?,?,?,?)",
                        (
                            report.report_id,
                            source.source_id,
                            None if source.artifact is None else source.artifact.artifact_id,
                            None if source.artifact is None else source.artifact.preview_digest,
                            None if source.artifact is None else source.artifact.preview_variant,
                        ),
                    )
            else:
                if existing != values[1:]:
                    raise MetadataReportError("persisted report identity conflicts")
                expected_references = tuple(
                    (source.source_id, None if source.artifact is None else source.artifact.artifact_id, None if source.artifact is None else source.artifact.preview_digest, None if source.artifact is None else source.artifact.preview_variant)
                    for source in report.cover_sources
                )
                actual_references = tuple(self._connection.execute(
                    "SELECT source_id,artifact_id,preview_digest,preview_variant FROM metadata_report_cover_references WHERE report_id=? ORDER BY source_id",
                    (report.report_id,),
                ))
                if tuple(sorted(expected_references)) != actual_references:
                    raise MetadataReportError("persisted report cover references conflict")
            self._connection.execute(
                "INSERT INTO current_metadata_reports(connection_name,library_name,item_id,file_identity,report_id) VALUES(?,?,?,?,?) "
                "ON CONFLICT(connection_name,library_name,item_id,file_identity) DO UPDATE SET report_id=excluded.report_id",
                (*values[1:5], report.report_id),
            )
            self._connection.execute(
                "INSERT INTO current_metadata_report_lineages(connection_name,library_name,item_id,report_id) VALUES(?,?,?,?) "
                "ON CONFLICT(connection_name,library_name,item_id) DO UPDATE SET report_id=excluded.report_id",
                (report.target.connection, report.target.library, report.target.item, report.report_id),
            )
            if after_publication is None:
                self._connection.execute(
                    "INSERT INTO current_review_packages(connection_name,library_name,item_id,proposal_set_id,report_id,review_case_id,checkpoint,outcome) VALUES(?,?,?,NULL,?,NULL,NULL,'report_only') "
                    "ON CONFLICT(connection_name,library_name,item_id) DO UPDATE SET proposal_set_id=NULL,report_id=excluded.report_id,review_case_id=NULL,checkpoint=NULL,outcome='report_only'",
                    (report.target.connection, report.target.library, report.target.item, report.report_id),
                )
            if after_publication is not None:
                after_publication(self, report)
            if self._binding is not None:
                self._binding.require_current()
            self._connection.commit()
            return self.get(report.report_id)
        except MetadataReportError:
            self._connection.rollback()
            raise
        except sqlite3.Error as error:
            self._connection.rollback()
            raise MetadataReportError("report publication failed") from error
        except Exception as error:
            self._connection.rollback()
            raise MetadataReportError("report publication precondition failed") from error

    def get(self, report_id: str) -> str:
        try:
            row = self._connection.execute(
                "SELECT report_id,connection_name,library_name,item_id,file_identity,content_digest,schema_version,document_digest,document FROM metadata_reports WHERE report_id=?",
                (report_id,),
            ).fetchone()
            if row is None:
                raise MetadataReportError("persisted report is unavailable")
            document = _validate_report_row(row)
            return document
        except (sqlite3.Error, ReportValueError, UnicodeError) as error:
            if isinstance(error, MetadataReportError):
                raise
            raise MetadataReportError("persisted report is invalid") from error

    def publish_proposal(self, proposal_set: ProposalSet) -> str:
        """Insert one exact ProposalSet after transaction-local report recheck."""
        if type(proposal_set) is not ProposalSet:
            raise MetadataReportError("proposal publication failed")
        document = proposal_set.to_json()
        values = (
            proposal_set.proposal_set_id,
            proposal_set.report_id,
            proposal_set.policy_version,
            document,
        )
        owns_transaction = not self._connection.in_transaction
        try:
            if owns_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            if self._binding is not None:
                self._binding.require_current()
            existing = self._connection.execute(
                "SELECT report_id,policy_version,document "
                "FROM proposal_sets WHERE proposal_set_id=?",
                (proposal_set.proposal_set_id,),
            ).fetchone()
            expected = values[1:]
            if existing is not None and existing != expected:
                raise MetadataReportError("persisted proposal identity conflicts")
            row = self._connection.execute(
                "SELECT report_id,connection_name,library_name,item_id,file_identity,content_digest,schema_version,document_digest,document "
                "FROM metadata_reports WHERE report_id=?",
                (proposal_set.report_id,),
            ).fetchone()
            if row is None:
                raise MetadataReportError("proposal report is unavailable")
            report_document = _validate_report_row(row)
            report = MetadataReport.from_json(report_document)
            current = self._connection.execute(
                "SELECT report_id FROM current_metadata_reports "
                "WHERE connection_name=? AND library_name=? AND item_id=? AND file_identity=?",
                (
                    report.target.connection,
                    report.target.library,
                    report.target.item,
                    report.file.identity,
                ),
            ).fetchone()
            policy_generation = self._connection.execute(
                "SELECT generation FROM metadata_policy_state WHERE singleton=1"
            ).fetchone()
            current_policy = self.current_resolved_metadata_policy(
                report.target.library
            )
            policy_is_bound = current_policy is not None or (
                policy_generation is not None and policy_generation[0] > 1
            )
            if (
                report.report_id != proposal_set.report_id
                or report.target.fingerprint != proposal_set.target_fingerprint
                or current != (proposal_set.report_id,)
                or (
                    policy_is_bound
                    and (
                        current_policy is None
                        or proposal_set.resolved_policy_id != current_policy.resolved_id
                        or proposal_set.to_json()
                        != evaluate_report(
                            report,
                            current_policy.resolved_id,
                            human_review_destinations(current_policy),
                        ).to_json()
                    )
                )
            ):
                raise MetadataReportError("proposal report or policy changed")
            if existing is None:
                self._connection.execute(
                    "INSERT INTO proposal_sets(proposal_set_id,report_id,policy_version,document) VALUES(?,?,?,?)",
                    values,
                )
            if self._binding is not None:
                self._binding.require_current()
            if owns_transaction:
                self._connection.commit()
            return self.get_proposal(proposal_set.proposal_set_id)
        except MetadataReportError:
            self._connection.rollback()
            raise
        except (sqlite3.Error, ProposalValueError, ReportValueError) as error:
            self._connection.rollback()
            raise MetadataReportError("proposal publication failed") from error
        except Exception as error:
            self._connection.rollback()
            raise MetadataReportError(
                "proposal publication precondition failed"
            ) from error

    def get_proposal(self, proposal_set_id: str) -> str:
        try:
            row = self._connection.execute(
                "SELECT proposal_set_id,report_id,policy_version,document "
                "FROM proposal_sets WHERE proposal_set_id=?",
                (proposal_set_id,),
            ).fetchone()
            if row is None:
                raise MetadataReportError("persisted proposal is unavailable")
            return _validate_proposal_row(row)
        except MetadataReportError:
            raise
        except (sqlite3.Error, ProposalValueError, UnicodeError) as error:
            raise MetadataReportError("persisted proposal is invalid") from error

    def admit_review_case(self, proposal_set: ProposalSet) -> str:
        """Atomically bind one exact current ProposalSet to its target lineage."""
        if type(proposal_set) is not ProposalSet:
            raise MetadataReportError("review admission failed")
        owns_transaction = not self._connection.in_transaction
        try:
            if owns_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            if self._binding is not None:
                self._binding.require_current()
            proposal_row = self._connection.execute("SELECT proposal_set_id,report_id,policy_version,document FROM proposal_sets WHERE proposal_set_id=?", (proposal_set.proposal_set_id,)).fetchone()
            if proposal_row is None or _validate_proposal_row(proposal_row) != proposal_set.to_json():
                raise MetadataReportError("review proposal is unavailable")
            report_row = self._connection.execute("SELECT report_id,connection_name,library_name,item_id,file_identity,content_digest,schema_version,document_digest,document FROM metadata_reports WHERE report_id=?", (proposal_set.report_id,)).fetchone()
            if report_row is None:
                raise MetadataReportError("review report is unavailable")
            report = MetadataReport.from_json(_validate_report_row(report_row))
            lineage = (report.target.connection, report.target.library, report.target.item)
            generation = self._connection.execute(
                "SELECT generation FROM metadata_policy_state WHERE singleton=1"
            ).fetchone()
            current_policy = self.current_resolved_metadata_policy(
                report.target.library
            )
            if (
                proposal_set.report_id != report.report_id
                or proposal_set.target_fingerprint != report.target.fingerprint
                or proposal_set.to_json()
                != evaluate_report(
                    report,
                    proposal_set.resolved_policy_id,
                    None
                    if current_policy is None
                    else human_review_destinations(current_policy),
                ).to_json()
            ):
                raise ReviewAdmissionIncompatibleError
            if (
                (current_policy is not None or (generation is not None and generation[0] > 1))
                and (
                    current_policy is None
                    or proposal_set.resolved_policy_id != current_policy.resolved_id
                )
            ):
                raise ReviewAdmissionStaleError
            if self._connection.execute("SELECT report_id FROM current_metadata_report_lineages WHERE connection_name=? AND library_name=? AND item_id=?", lineage).fetchone() != (report.report_id,):
                raise ReviewAdmissionStaleError
            if self._connection.execute("SELECT report_id FROM current_metadata_reports WHERE connection_name=? AND library_name=? AND item_id=? AND file_identity=?", (*lineage, report.file.identity)).fetchone() != (report.report_id,):
                raise ReviewAdmissionStaleError
            validated = self._validated_review_pointer(lineage)
            prior = (
                None
                if validated is None
                else (
                    validated[0],
                    validated[1],
                    validated[2],
                    validated[4],
                    validated[5],
                )
            )
            prior_case_id = None if prior is None else prior[2]
            checkpoint = 1 if prior is None else prior[3] + 1
            if (
                prior is not None
                and prior[0] == proposal_set.proposal_set_id
                and self._connection.execute(
                    "SELECT actor FROM review_case_transitions "
                    "WHERE transition_id=?",
                    (validated[3],),
                ).fetchone()
                != ("review-preparation-v1",)
            ):
                if self._binding is not None:
                    self._binding.require_current()
                if owns_transaction:
                    self._connection.commit()
                replay_status = (
                    "replayed"
                    if validated[5] in {"admitted", "superseded"}
                    else validated[5]
                )
                return _admission_json(replay_status, validated[2])
            if not is_actionable(proposal_set):
                outcome = "retired" if prior_case_id is not None else "no_case"
                transition = ReviewTransition(*lineage, checkpoint, proposal_set.proposal_set_id, report.report_id, outcome, prior_case_id, None, "nonactionable")
                self._connection.execute("INSERT INTO review_case_transitions(transition_id,connection_name,library_name,item_id,checkpoint,actor,from_review_case_id,to_review_case_id,proposal_set_id,report_id,kind,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (transition.transition_id, *lineage, checkpoint, transition.actor, transition.from_review_case_id, None, proposal_set.proposal_set_id, report.report_id, outcome, "nonactionable"))
                self._connection.execute("INSERT INTO current_review_case_lineages(connection_name,library_name,item_id,proposal_set_id,report_id,review_case_id,transition_id,checkpoint,outcome) VALUES(?,?,?,?,?,?,?, ?,?) ON CONFLICT(connection_name,library_name,item_id) DO UPDATE SET proposal_set_id=excluded.proposal_set_id,report_id=excluded.report_id,review_case_id=NULL,transition_id=excluded.transition_id,checkpoint=excluded.checkpoint,outcome=excluded.outcome", (*lineage, proposal_set.proposal_set_id, report.report_id, None, transition.transition_id, checkpoint, outcome))
                if owns_transaction:
                    self._connection.execute(
                        "INSERT INTO current_review_packages(connection_name,library_name,item_id,proposal_set_id,report_id,review_case_id,checkpoint,outcome) VALUES(?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(connection_name,library_name,item_id) DO UPDATE SET proposal_set_id=excluded.proposal_set_id,report_id=excluded.report_id,review_case_id=excluded.review_case_id,checkpoint=excluded.checkpoint,outcome=excluded.outcome",
                        (*lineage, proposal_set.proposal_set_id, report.report_id, None, checkpoint, outcome),
                    )
                if self._binding is not None:
                    self._binding.require_current()
                if owns_transaction:
                    self._connection.commit()
                return _admission_json(outcome, None)
            case = ReviewCase.create(proposal_set, report)
            existing = self._connection.execute("SELECT document FROM review_cases WHERE review_case_id=?", (case.review_case_id,)).fetchone()
            if existing is not None and existing != (case.to_json(),):
                raise MetadataReportError("review case identity conflicts")
            if existing is None:
                self._connection.execute("INSERT INTO review_cases(review_case_id,proposal_set_id,report_id,document) VALUES(?,?,?,?)", (case.review_case_id, case.proposal_set_id, case.report_id, case.to_json()))
            outcome = "superseded" if prior_case_id is not None else "admitted"
            transition = ReviewTransition(*lineage, checkpoint, proposal_set.proposal_set_id, report.report_id, outcome, prior_case_id, case.review_case_id, "actionable")
            self._connection.execute("INSERT INTO review_case_transitions(transition_id,connection_name,library_name,item_id,checkpoint,actor,from_review_case_id,to_review_case_id,proposal_set_id,report_id,kind,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (transition.transition_id, *lineage, checkpoint, transition.actor, transition.from_review_case_id, case.review_case_id, proposal_set.proposal_set_id, report.report_id, outcome, "actionable"))
            self._connection.execute("INSERT INTO current_review_case_lineages(connection_name,library_name,item_id,proposal_set_id,report_id,review_case_id,transition_id,checkpoint,outcome) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(connection_name,library_name,item_id) DO UPDATE SET proposal_set_id=excluded.proposal_set_id,report_id=excluded.report_id,review_case_id=excluded.review_case_id,transition_id=excluded.transition_id,checkpoint=excluded.checkpoint,outcome=excluded.outcome", (*lineage, proposal_set.proposal_set_id, report.report_id, case.review_case_id, transition.transition_id, checkpoint, outcome))
            if owns_transaction:
                self._connection.execute(
                    "INSERT INTO current_review_packages(connection_name,library_name,item_id,proposal_set_id,report_id,review_case_id,checkpoint,outcome) VALUES(?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(connection_name,library_name,item_id) DO UPDATE SET proposal_set_id=excluded.proposal_set_id,report_id=excluded.report_id,review_case_id=excluded.review_case_id,checkpoint=excluded.checkpoint,outcome=excluded.outcome",
                    (*lineage, proposal_set.proposal_set_id, report.report_id, case.review_case_id, checkpoint, outcome),
                )
            if self._binding is not None:
                self._binding.require_current()
            if owns_transaction:
                self._connection.commit()
            return _admission_json(outcome, case.review_case_id)
        except (
            MetadataReportError,
            ReviewAdmissionIncompatibleError,
            ReviewAdmissionStaleError,
        ):
            self._connection.rollback(); raise
        except (sqlite3.Error, ProposalValueError, ReportValueError, ReviewCaseValueError) as error:
            self._connection.rollback(); raise MetadataReportError("review admission failed") from error

    def review_case_exists(self, review_case_id: str) -> bool:
        try:
            return (
                self._connection.execute(
                    "SELECT 1 FROM review_cases WHERE review_case_id=?",
                    (review_case_id,),
                ).fetchone()
                is not None
            )
        except sqlite3.Error as error:
            raise MetadataReportError(
                "review case availability is unavailable"
            ) from error

    def get_review_case(self, review_case_id: str) -> str:
        try:
            row = self._connection.execute("SELECT review_case_id,proposal_set_id,report_id,document FROM review_cases WHERE review_case_id=?", (review_case_id,)).fetchone()
            if row is None or len(row) != 4:
                raise MetadataReportError("review case is unavailable")
            case = ReviewCase.from_json(row[3])
            if (case.review_case_id, case.proposal_set_id, case.report_id) != row[:3]:
                raise MetadataReportError("review case is invalid")
            self._validated_review_pointer((case.target["connection"], case.target["library"], case.target["item"]))
            proposal_row = self._connection.execute(
                "SELECT proposal_set_id,report_id,policy_version,document FROM proposal_sets WHERE proposal_set_id=?",
                (case.proposal_set_id,),
            ).fetchone()
            report_row = self._connection.execute(
                "SELECT report_id,connection_name,library_name,item_id,file_identity,content_digest,schema_version,document_digest,document FROM metadata_reports WHERE report_id=?",
                (case.report_id,),
            ).fetchone()
            if proposal_row is None or report_row is None:
                raise MetadataReportError("review case source is unavailable")
            proposal = ProposalSet.from_json(_validate_proposal_row(proposal_row))
            report = MetadataReport.from_json(_validate_report_row(report_row))
            policy_destinations = self._policy_destinations_for(report, proposal)
            if (
                proposal.proposal_set_id != case.proposal_set_id
                or proposal.report_id != case.report_id
                or proposal.target_fingerprint != case.target_fingerprint
                or proposal.to_json()
                != evaluate_report(
                    report, proposal.resolved_policy_id, policy_destinations
                ).to_json()
                or report.target.to_data() != case.target
                or report.target.fingerprint != case.target_fingerprint
                or report.file.identity != case.file_identity
                or tuple(value.to_data() for value in proposal.results) != case.results
            ):
                raise MetadataReportError("review case source disagrees")
            package = self._connection.execute(
                "SELECT proposal_set_id,report_id,review_case_id FROM current_review_packages WHERE connection_name=? AND library_name=? AND item_id=?",
                (case.target["connection"], case.target["library"], case.target["item"]),
            ).fetchone()
            current = package == (case.proposal_set_id, case.report_id, case.review_case_id)
            return _canonical_json({"case": case.to_data(), "current": current, "schema_version": "review-case-get-v1"})
        except (sqlite3.Error, ReviewCaseValueError, KeyError, TypeError) as error:
            raise MetadataReportError("review case is unavailable") from error

    def get_review_case_report(self, review_case_id: str) -> str:
        try:
            return self._source_report_for_review_case(review_case_id).to_json()
        except (sqlite3.Error, ReviewCaseValueError, ReportValueError) as error:
            raise MetadataReportError("review case evidence is unavailable") from error

    def review_case_ready_at(self, review_case_id: str) -> int | None:
        """Return the exact preparation-complete checkpoint for advisory timing."""
        try:
            row = self._connection.execute(
                "SELECT updated_at FROM review_preparation_work "
                "WHERE review_case_id=? AND stage='complete' AND state='complete' "
                "ORDER BY updated_at DESC,work_id LIMIT 1",
                (review_case_id,),
            ).fetchone()
            if row is None:
                return None
            if len(row) != 1 or type(row[0]) is not int or row[0] < 0:
                raise MetadataReportError("review case readiness is unavailable")
            return row[0]
        except sqlite3.Error as error:
            raise MetadataReportError(
                "review case readiness is unavailable"
            ) from error

    def read_current_review_case(
        self, review_case_id: str, history_limit: int | None
    ) -> CurrentReviewCaseSnapshot:
        if history_limit is not None and (
            type(history_limit) is not int or not 1 <= history_limit <= 100
        ):
            raise MetadataReportError("review case snapshot is unavailable")
        with self._consistent_review_read():
            snapshot = CurrentReviewCaseSnapshot(
                case_document=self.get_review_case(review_case_id),
                report_document=self.get_review_case_report(review_case_id),
                decision_documents=self.get_review_case_decisions(review_case_id),
                history=(
                    ()
                    if history_limit is None
                    else self.get_review_case_history(review_case_id, history_limit)
                ),
                automatic_attention_destinations=self._automatic_attention_destinations(review_case_id),
                cover_consideration_document=(
                    self.current_cover_consideration_document(review_case_id)
                ),
                cover_history=(
                    ()
                    if history_limit is None
                    else self.cover_consideration_history(
                        review_case_id, history_limit
                    )
                ),
            )
        return snapshot

    def read_review_library(
        self,
        search: str | None,
        limit: int,
        offset: int,
        attention: bool | None,
    ) -> ReviewLibrarySnapshot:
        with self._consistent_review_read():
            rows = self.list_current_review_case_documents(
                search, limit, offset, attention
            )
            projected_rows = []
            for case_document, report_document in rows:
                if case_document is None and '"schema_version":"review-terminal-work-v1"' in report_document:
                    projected_rows.append((case_document, report_document, (), (), (), None))
                    continue
                report = MetadataReport.from_json(report_document)
                terminal = self._connection.execute(
                    "SELECT work_id,stage,attempt,recovery_attempt,failure_code,failure_root "
                    "FROM review_preparation_work WHERE connection_name=? AND library_name=? AND item_id=? "
                    "AND state='failed' AND failure_code IS NOT NULL AND (report_id IS NULL OR report_id<>?) "
                    "ORDER BY updated_at DESC,work_id DESC LIMIT 1",
                    (report.target.connection, report.target.library, report.target.item, report.report_id),
                ).fetchone()
                terminal_document = None if terminal is None else _canonical_json({
                    "attempt": terminal[2], "connection": report.target.connection,
                    "failure_root": terminal[5], "item": report.target.item,
                    "lane": terminal[1], "library": report.target.library,
                    "recovery_attempt": terminal[3], "schema_version": "review-terminal-work-v1",
                    "status": terminal[4], "work_id": terminal[0],
                })
                case_id = None if case_document is None else ReviewCase.from_json(case_document).review_case_id
                projected_rows.append((
                    case_document, report_document,
                    () if case_id is None else self.get_review_case_decisions(case_id),
                    () if case_id is None else self._automatic_attention_destinations(case_id),
                    () if case_id is None else self._field_effect_capability_suspended_destinations(case_id),
                    terminal_document,
                ))
            projected = tuple(projected_rows)
            total, attention_total = self.count_current_review_cases(search)
            snapshot = ReviewLibrarySnapshot(
                rows=projected,
                total=total,
                attention_total=attention_total,
            )
        return snapshot

    def _automatic_attention_destinations(self, review_case_id: str) -> tuple[str, ...]:
        rows = self._connection.execute(
            "SELECT DISTINCT json_extract(outcome.value,'$.destination') FROM shadow_decision_outcomes shadow, "
            "json_each(shadow.document,'$.outcomes') outcome, automatic_safety_state safety "
            "WHERE shadow.review_case_id=? AND json_extract(outcome.value,'$.policy_destination')='automatic' AND ("
            "safety.paused=1 OR safety.global_suspension IS NOT NULL OR EXISTS(SELECT 1 FROM json_each(safety.category_suspensions_document) category WHERE json_extract(category.value,'$[0]')=json_extract(outcome.value,'$.policy_category'))) ORDER BY 1",
            (review_case_id,),
        ).fetchall()
        if any(type(row[0]) is not str for row in rows):
            raise MetadataReportError("review attention is unavailable")
        return tuple(row[0] for row in rows)

    def _field_effect_capability_suspended_destinations(
        self, review_case_id: str,
    ) -> tuple[str, ...]:
        rows = self._connection.execute(
            "SELECT json_extract(result.value,'$.destination') "
            "FROM current_review_case_lineages c JOIN review_cases rc "
            "ON rc.review_case_id=c.review_case_id,"
            "json_each(rc.document,'$.results') result "
            "WHERE rc.review_case_id=? AND EXISTS(SELECT 1 "
            "FROM field_effect_suspensions suspension "
            "WHERE suspension.connection_name=c.connection_name AND ("
            "(suspension.scope='connection' AND suspension.destination='') OR "
            "(suspension.scope='destination' AND suspension.destination="
            "json_extract(result.value,'$.destination')))) ORDER BY 1",
            (review_case_id,),
        ).fetchall()
        if any(type(row[0]) is not str for row in rows):
            raise MetadataReportError("review attention is unavailable")
        return tuple(row[0] for row in rows)

    def read_review_attention(
        self, search: str | None, limit: int, offset: int
    ) -> ReviewAttentionSnapshot:
        with self._consistent_review_read():
            snapshot = ReviewAttentionSnapshot(
                rows=self.list_current_attention_documents(
                    search, limit, offset
                ),
                total=self.count_current_attention_fields(search),
            )
        return snapshot

    def list_current_review_case_documents(
        self,
        search: str | None,
        limit: int,
        offset: int,
        attention: bool | None,
    ) -> tuple[tuple[str | None, str], ...]:
        try:
            query, pattern = _review_search_pattern(search)
            condition = _REVIEW_ATTENTION_SQL
            if attention is False:
                condition = f"NOT ({condition})"
            elif attention is None:
                condition = "1"
            terminal_union = ""
            parameters: list[object] = [
                query,
                pattern,
                pattern,
                pattern,
                pattern,
                pattern,
            ]
            if attention is not True:
                terminal_union = (
                    " UNION ALL SELECT NULL,json_object("
                    "'attempt',attempt,'connection',connection_name,"
                    "'failure_root',failure_root,'item',item_id,'lane',stage,"
                    "'library',library_name,'recovery_attempt',recovery_attempt,"
                    "'schema_version','review-terminal-work-v1','status',failure_code,"
                    "'work_id',work_id),connection_name,library_name,item_id,NULL,"
                    "1,'preparation unavailable','' FROM review_preparation_work "
                    "WHERE state='failed' AND failure_code IS NOT NULL AND NOT EXISTS ("
                    "SELECT 1 FROM current_review_packages p WHERE "
                    "p.connection_name=review_preparation_work.connection_name AND "
                    "p.library_name=review_preparation_work.library_name AND "
                    "p.item_id=review_preparation_work.item_id) AND "
                    "(?='' OR ml_casefold(item_id) LIKE ? ESCAPE '\\' OR "
                    "ml_casefold(library_name) LIKE ? ESCAPE '\\')"
                )
                parameters.extend((query, pattern, pattern))
            parameters.extend((limit, offset))
            rows = self._connection.execute(
                "SELECT case_document,report_document,connection_name,library_name,"
                "item_id,outcome FROM (SELECT rc.document AS case_document,"
                "mr.document AS report_document,ml.connection_name AS connection_name,"
                "ml.library_name AS library_name,ml.item_id AS item_id,c.outcome AS outcome,"
                "CASE WHEN "
                + _REVIEW_ATTENTION_SQL
                + " THEN 0 ELSE 1 END AS attention_order,"
                "ml_casefold(COALESCE(json_extract(mr.document,'$.target.fields[0].value'),'')) AS title_order,"
                "COALESCE(rc.review_case_id,'') AS case_order "
                + _CURRENT_LIBRARY_FROM_SQL
                + " WHERE COALESCE(c.outcome,'')<>'tombstoned' AND "
                + condition
                + _REVIEW_SEARCH_SQL
                + terminal_union
                + ") ORDER BY attention_order,title_order,library_name,item_id,"
                "connection_name,case_order LIMIT ? OFFSET ?",
                tuple(parameters),
            ).fetchall()
            values: list[tuple[str | None, str]] = []
            for row in rows:
                if (
                    len(row) != 6
                    or type(row[1]) is not str
                    or any(type(value) is not str for value in row[2:5])
                    or (row[5] is not None and type(row[5]) is not str)
                ):
                    raise MetadataReportError("review browse row is invalid")
                if row[0] is None and row[5] is None:
                    terminal = json.loads(row[1])
                    if (
                        type(terminal) is dict
                        and terminal.get("schema_version")
                        == "review-terminal-work-v1"
                    ):
                        values.append((None, _canonical_json(terminal)))
                        continue
                lineage = (row[2], row[3], row[4])
                report = MetadataReport.from_json(row[1])
                if (
                    (
                        report.target.connection,
                        report.target.library,
                        report.target.item,
                    )
                    != lineage
                ):
                    raise MetadataReportError("review browse row disagrees")
                if row[5] is None:
                    if row[0] is not None:
                        raise MetadataReportError("review browse row disagrees")
                    values.append((None, report.to_json()))
                    continue
                if row[0] is None:
                    if row[5] not in {"report_only", "no_case", "retired"}:
                        raise MetadataReportError("review browse row disagrees")
                    values.append((None, report.to_json()))
                    continue
                case_document, report_document = _validate_review_browse_row(
                    row[:2],
                    self._policy_destinations_for(
                        report,
                        ProposalSet.from_json(
                            ReviewCase.from_json(row[0]).proposal_document
                        ),
                    ),
                )
                values.append((case_document, report_document))
            return tuple(values)
        except (
            sqlite3.Error,
            MetadataReportError,
            ReviewCaseValueError,
            ReportValueError,
            TypeError,
            ValueError,
        ) as error:
            raise MetadataReportError("review library is unavailable") from error

    def count_current_review_cases(
        self, search: str | None
    ) -> tuple[int, int]:
        try:
            query, pattern = _review_search_pattern(search)
            row = self._connection.execute(
                "SELECT count(*),COALESCE(sum(CASE WHEN "
                + _REVIEW_ATTENTION_SQL
                + " THEN 1 ELSE 0 END),0) "
                + _CURRENT_LIBRARY_FROM_SQL
                + " WHERE COALESCE(c.outcome,'')<>'tombstoned'"
                + _REVIEW_SEARCH_SQL,
                (query, pattern, pattern, pattern, pattern, pattern),
            ).fetchone()
            if (
                row is None
                or len(row) != 2
                or any(type(value) is not int or value < 0 for value in row)
                or row[1] > row[0]
            ):
                raise MetadataReportError("review library count is invalid")
            terminal = self._connection.execute(
                "SELECT count(*) FROM review_preparation_work WHERE state='failed' AND failure_code IS NOT NULL "
                "AND NOT EXISTS (SELECT 1 FROM current_review_packages p WHERE p.connection_name=review_preparation_work.connection_name AND p.library_name=review_preparation_work.library_name AND p.item_id=review_preparation_work.item_id) "
                "AND (?='' OR ml_casefold(item_id) LIKE ? ESCAPE '\\' OR ml_casefold(library_name) LIKE ? ESCAPE '\\')",
                (query, pattern, pattern),
            ).fetchone()
            if terminal is None or type(terminal[0]) is not int or terminal[0] < 0:
                raise MetadataReportError("review library count is invalid")
            return row[0] + terminal[0], row[1]
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise MetadataReportError("review library is unavailable") from error

    def list_current_attention_documents(
        self, search: str | None, limit: int, offset: int
    ) -> tuple[tuple[str, str, str, int], ...]:
        try:
            query, pattern = _review_search_pattern(search)
            rows = self._connection.execute(
                "SELECT rc.document,mr.document,"
                "json_extract(result.value,'$.destination'),"
                "(SELECT count(*) FROM json_each(rc.document,'$.results') result "
                "WHERE " + _REVIEW_FIELD_ATTENTION_SQL + ") "
                + _CURRENT_REVIEW_FROM_SQL
                + ",json_each(rc.document,'$.results') result "
                "WHERE "
                + _REVIEW_FIELD_ATTENTION_SQL
                + _REVIEW_SEARCH_SQL
                + " ORDER BY "
                "ml_casefold(COALESCE(json_extract(mr.document,'$.target.fields[0].value'),'')),"
                "c.library_name,c.item_id,"
                "json_extract(result.value,'$.destination'),rc.review_case_id "
                "LIMIT ? OFFSET ?",
                (query, pattern, pattern, pattern, pattern, pattern, limit, offset),
            ).fetchall()
            values = []
            for row in rows:
                case_document, report_document = _validate_review_browse_row(
                    row[:2],
                    self._policy_destinations_for(
                        MetadataReport.from_json(row[1]),
                        ProposalSet.from_json(
                            ReviewCase.from_json(row[0]).proposal_document
                        ),
                    ),
                )
                if (
                    type(row[2]) is not str
                    or type(row[3]) is not int
                    or row[3] < 1
                ):
                    raise MetadataReportError("review attention field is invalid")
                values.append((case_document, report_document, row[2], row[3]))
            return tuple(values)
        except (
            sqlite3.Error,
            MetadataReportError,
            ReviewCaseValueError,
            ReportValueError,
            TypeError,
            ValueError,
        ) as error:
            raise MetadataReportError("review attention is unavailable") from error

    def count_current_attention_fields(self, search: str | None) -> int:
        try:
            query, pattern = _review_search_pattern(search)
            row = self._connection.execute(
                "SELECT count(*) "
                + _CURRENT_REVIEW_FROM_SQL
                + ",json_each(rc.document,'$.results') result "
                "WHERE "
                + _REVIEW_FIELD_ATTENTION_SQL
                + _REVIEW_SEARCH_SQL,
                (query, pattern, pattern, pattern, pattern, pattern),
            ).fetchone()
            if (
                row is None
                or len(row) != 1
                or type(row[0]) is not int
                or row[0] < 0
            ):
                raise MetadataReportError("review attention count is invalid")
            return row[0]
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise MetadataReportError("review attention is unavailable") from error

    def begin_login(
        self, now: int, window_seconds: int, maximum_failures: int
    ) -> bool:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute(
                "DELETE FROM review_login_failures WHERE occurred_at<?",
                (now - window_seconds,),
            )
            self._connection.execute(
                "DELETE FROM review_sessions WHERE expires_at<=?", (now,)
            )
            count = self._connection.execute(
                "SELECT count(*) FROM review_login_failures"
            ).fetchone()
            if count is None:
                raise MetadataReportError("review login is unavailable")
            permitted = count[0] < maximum_failures
            if permitted:
                identity = hashlib.sha256(
                    f"review-login-attempt-v1:{now}:{secrets.token_hex(16)}".encode()
                ).hexdigest()
                self._connection.execute(
                    "INSERT INTO review_login_failures(failure_id,occurred_at) "
                    "VALUES(?,?)",
                    (identity, now),
                )
            self._connection.commit()
            return permitted
        except MetadataReportError:
            self._connection.rollback()
            raise
        except sqlite3.Error as error:
            self._connection.rollback()
            raise MetadataReportError("review login is unavailable") from error

    def shadow_posture(self) -> str:
        """Return the closed global posture without implying authority."""
        try:
            row = self._connection.execute(
                "SELECT posture FROM metadata_policy_state WHERE singleton=1"
            ).fetchone()
            if row is None or row[0] not in {"review", "preview", "automatic"}:
                raise MetadataReportError("shadow posture is unavailable")
            return row[0]
        except sqlite3.Error as error:
            raise MetadataReportError("shadow posture is unavailable") from error

    def enter_shadow_preview(self, updated_at: int) -> str:
        """Enter Preview and reconcile the exact-current cohort when leaving automatic."""
        if type(updated_at) is not int or updated_at < 0:
            raise MetadataReportError("shadow posture request is invalid")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT active_policy_id,generation,posture FROM metadata_policy_state WHERE singleton=1"
            ).fetchone()
            if row is None or row[2] not in {"review", "preview", "automatic"}:
                raise MetadataReportError("shadow posture is unavailable")
            # Automatic decisions must never survive a posture change without
            # re-projecting the exact-current cohort through the established
            # F09 transition/work protocol.  A fresh generation makes both the
            # bounded progress signal and the later confirmation check durable.
            if row[2] == "automatic":
                active_document = self._connection.execute(
                    "SELECT document FROM metadata_policy_drafts WHERE policy_id=?",
                    (row[0],),
                ).fetchone()
                if active_document is None or type(active_document[0]) is not str:
                    raise MetadataReportError("shadow posture reconciliation is unavailable")
                active_profile = MetadataPolicyProfile.from_data(json.loads(active_document[0]))
                # A policy with no automatic destination has no affected
                # automatic cohort.  Preserve the ordinary Preview workflow.
                affected = (
                    self._connection.execute("SELECT 1 FROM automatic_decisions LIMIT 1").fetchone()
                    if any(value == "automatic" for _, value in active_profile.destinations)
                    else None
                )
                # There is no prior automatic authority to reconcile until a
                # current cohort has actually received an automatic decision.
                # Initial Preview remains a harmless report-only entry.
                if affected is not None:
                    resolutions = tuple(self._connection.execute(
                        "SELECT library_name,resolved_policy_id,document FROM metadata_policy_resolutions WHERE generation=? ORDER BY library_name",
                        (row[1],),
                    ).fetchall())
                    if not resolutions:
                        profile = active_profile
                        libraries = self._connection.execute(
                            "SELECT DISTINCT c.library_name " + _CURRENT_REVIEW_FROM_SQL + " ORDER BY c.library_name"
                        ).fetchall()
                        resolutions = tuple(
                            (library[0], resolved.resolved_id, resolved.canonical_json)
                            for library in libraries
                            for resolved in (resolve_policy(profile, library[0]),)
                        )
                    self.activate_metadata_policy(
                        row[0], row[0], resolutions, updated_at, reconcile_current=True
                    )
            self._connection.execute(
                "UPDATE metadata_policy_state SET posture='preview' WHERE singleton=1"
            )
            self._connection.commit()
            return "preview"
        except (sqlite3.Error, MetadataReportError) as error:
            if self._connection.in_transaction:
                self._connection.rollback()
            if isinstance(error, MetadataReportError):
                raise
            raise MetadataReportError("shadow posture persistence failed") from error

    def activate_qualified_automatic(self, updated_at: int) -> str:
        """Confirm Preview only when every requested automatic option is sealed."""
        if type(updated_at) is not int or updated_at < 0:
            raise MetadataReportError("automatic posture request is invalid")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT s.posture,d.document FROM metadata_policy_state s JOIN metadata_policy_drafts d ON d.policy_id=s.active_policy_id WHERE s.singleton=1"
            ).fetchone()
            if row is None or row[0] != "preview":
                raise MetadataReportError("automatic posture confirmation is unavailable")
            policy = MetadataPolicyProfile.from_data(json.loads(row[1]))
            requested = {f"{field}:{option}" for field, option in policy.options if dict(policy.destinations)[field] == "automatic"}
            if not requested or not requested.issubset(QUALIFIED_POLICY_OPTIONS):
                raise MetadataReportError("automatic posture qualification is unavailable")
            transition = self.metadata_policy_transition()
            if (
                transition is None
                or transition.generation != self._connection.execute(
                    "SELECT generation FROM metadata_policy_state WHERE singleton=1"
                ).fetchone()[0]
                or transition.state != "completed"
                or transition.completed != transition.total
                or transition.failed != 0
            ):
                raise MetadataReportError("automatic posture confirmation is unavailable")
            self._connection.execute("UPDATE metadata_policy_state SET posture='automatic' WHERE singleton=1")
            self._connection.commit()
            return "automatic"
        except (sqlite3.Error, MetadataReportError, MetadataPolicyError, ValueError, TypeError) as error:
            if self._connection.in_transaction:
                self._connection.rollback()
            if isinstance(error, MetadataReportError):
                raise
            raise MetadataReportError("automatic posture persistence failed") from error

    def _schedule_current_shadow_work(
        self, case: ReviewCase, updated_at: int
    ) -> None:
        """Reuse an existing current pipeline row; never create a scheduler."""
        lineage = (
            case.target["connection"],
            case.target["library"],
            case.target["item"],
        )
        current = self._connection.execute(
            "SELECT l.report_id,f.report_id,c.report_id,c.review_case_id "
            "FROM current_metadata_report_lineages l "
            "JOIN metadata_reports m ON m.report_id=l.report_id "
            "LEFT JOIN current_metadata_reports f ON "
            "f.connection_name=l.connection_name AND "
            "f.library_name=l.library_name AND f.item_id=l.item_id AND "
            "f.file_identity=m.file_identity "
            "LEFT JOIN current_review_case_lineages c ON "
            "c.connection_name=l.connection_name AND "
            "c.library_name=l.library_name AND c.item_id=l.item_id "
            "WHERE l.connection_name=? AND l.library_name=? AND l.item_id=?",
            lineage,
        ).fetchone()
        if (
            current is None
            or type(current[0]) is not str
            or current[0] != current[1]
            or (current[2] is not None and type(current[2]) is not str)
            or (current[3] is not None and type(current[3]) is not str)
        ):
            raise MetadataReportError("shadow outcome currentness is unavailable")
        current_report_id, _, review_report_id, review_case_id = current
        if (
            current_report_id == case.report_id
            and review_report_id == case.report_id
            and review_case_id == case.review_case_id
        ):
            return
        row = self._connection.execute(
            "SELECT work_id,report_id,review_case_id FROM review_preparation_work "
            "WHERE connection_name=? AND library_name=? AND item_id=? "
            "ORDER BY updated_at DESC,work_id DESC LIMIT 1",
            lineage,
        ).fetchone()
        if row is None:
            return
        if (
            review_case_id is not None
            and review_report_id == current_report_id
            and row[2] == review_case_id
            and row[1] == current_report_id
        ):
            self._connection.execute(
                "UPDATE review_preparation_work SET stage='projection',state='queued',"
                "attempt=0,recovery_attempt=0,failure_code=NULL,failure_root=NULL,updated_at=? WHERE work_id=?",
                (updated_at, row[0]),
            )
        else:
            self._restart_metadata_policy_work(
                row[0], "proposal", current_report_id, updated_at
            )

    def _persisted_shadow_sources(
        self,
        review_case_id: str,
        *,
        include_policy_ignored: bool = False,
    ) -> tuple[ReviewCase, ShadowReviewPacket, MetadataReport, ProposalSet]:
        case_row = self._connection.execute(
            "SELECT document FROM review_cases WHERE review_case_id=?",
            (review_case_id,),
        ).fetchone()
        if case_row is None or type(case_row[0]) is not str:
            raise MetadataReportError("shadow outcome source is unavailable")
        case = ReviewCase.from_json(case_row[0])
        proposal_row = self._connection.execute(
            "SELECT proposal_set_id,report_id,policy_version,document FROM proposal_sets "
            "WHERE proposal_set_id=?",
            (case.proposal_set_id,),
        ).fetchone()
        report_row = self._connection.execute(
            "SELECT report_id,connection_name,library_name,item_id,file_identity,"
            "content_digest,schema_version,document_digest,document "
            "FROM metadata_reports WHERE report_id=?",
            (case.report_id,),
        ).fetchone()
        if proposal_row is None or report_row is None:
            raise MetadataReportError("shadow outcome source is unavailable")
        proposal = ProposalSet.from_json(_validate_proposal_row(proposal_row))
        report = MetadataReport.from_json(_validate_report_row(report_row))
        return (
            case,
            ShadowReviewPacket.create(
                case,
                proposal,
                report,
                include_policy_ignored=include_policy_ignored,
            ),
            report,
            proposal,
        )

    def _persisted_shadow_packet_source(
        self,
        review_case_id: str,
        *,
        include_policy_ignored: bool = False,
    ) -> tuple[ReviewCase, ShadowReviewPacket]:
        case, packet, _, _ = self._persisted_shadow_sources(
            review_case_id, include_policy_ignored=include_policy_ignored
        )
        return case, packet

    def _validate_shadow_outcome_row(
        self, row: tuple[object, ...] | sqlite3.Row
    ) -> tuple[str, int]:
        if len(row) != 8:
            raise MetadataReportError("shadow outcome persistence is invalid")
        outcome = ShadowOutcomeSet.from_json(row[7])
        if (
            type(row[6]) is not int
            or row[6] < 0
            or (
                outcome.outcome_set_id,
                outcome.review_case_id,
                outcome.report_id,
                outcome.proposal_set_id,
                outcome.resolved_policy_id,
                outcome.source_decision_set_id,
            )
            != row[:6]
        ):
            raise MetadataReportError("shadow outcome persistence is invalid")
        case, packet, report, proposal = self._persisted_shadow_sources(
            outcome.review_case_id, include_policy_ignored=True
        )
        library = case.target["library"]
        if type(library) is not str:
            raise MetadataReportError("shadow outcome persistence is invalid")
        resolution = self._connection.execute(
            "SELECT document FROM metadata_policy_resolutions "
            "WHERE generation=? AND library_name=?",
            (outcome.policy_generation, library),
        ).fetchone()
        if resolution is None and outcome.policy_generation == 1:
            draft = self._connection.execute(
                "SELECT document FROM metadata_policy_drafts WHERE policy_id=?",
                (outcome.policy_profile_id,),
            ).fetchone()
            if draft is None or type(draft[0]) is not str:
                raise MetadataReportError("shadow outcome persistence is invalid")
            policy = resolve_policy(
                MetadataPolicyProfile.from_data(json.loads(draft[0])), library
            )
        else:
            if resolution is None or type(resolution[0]) is not str:
                raise MetadataReportError("shadow outcome persistence is invalid")
            policy = ResolvedMetadataPolicy.from_data(json.loads(resolution[0]))
            if policy.canonical_json != resolution[0]:
                raise MetadataReportError("shadow outcome persistence is invalid")
        if (
            policy.profile_id != outcome.policy_profile_id
            or policy.resolved_id != outcome.resolved_policy_id
        ):
            raise MetadataReportError("shadow outcome persistence is invalid")
        expected = ShadowOutcomeSet.create(
            case,
            decide_shadow_packet(packet),
            policy,
            outcome.policy_generation,
            report,
            proposal,
        )
        if expected.to_json() != row[7]:
            raise MetadataReportError("shadow outcome persistence is invalid")
        return expected.to_json(), row[6]

    def persist_shadow_outcome(
        self, review_case_id: str, evaluated_at: int
    ) -> str:
        """Atomically publish or replay one exact Preview decision set."""
        if (
            type(review_case_id) is not str
            or re.fullmatch(r"review_case_[0-9a-f]{64}", review_case_id) is None
            or type(evaluated_at) is not int
            or evaluated_at < 0
        ):
            raise MetadataReportError("shadow outcome request is invalid")
        owns_transaction = not self._connection.in_transaction
        case_for_recovery: ReviewCase | None = None
        try:
            if owns_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            raw_case = self._connection.execute(
                "SELECT document FROM review_cases WHERE review_case_id=?",
                (review_case_id,),
            ).fetchone()
            if raw_case is None:
                raise MetadataReportError("shadow outcome source is unavailable")
            case_for_recovery = ReviewCase.from_json(raw_case[0])
            self._require_shadow_case_current(review_case_id)
            state = self._connection.execute(
                "SELECT s.generation,s.posture,d.document FROM metadata_policy_state s "
                "JOIN metadata_policy_drafts d ON d.policy_id=s.active_policy_id "
                "WHERE s.singleton=1"
            ).fetchone()
            if state is None or state[1] not in {"review", "preview", "automatic"}:
                raise MetadataReportError("shadow posture is unavailable")
            if state[1] == "review":
                if owns_transaction:
                    self._connection.commit()
                return "inactive"
            case, packet, report, proposal = self._persisted_shadow_sources(
                review_case_id, include_policy_ignored=True
            )
            library = case.target["library"]
            if type(library) is not str:
                raise MetadataReportError("shadow outcome source is invalid")
            policy = self.current_resolved_metadata_policy(library)
            if policy is None and state[0] == 1:
                policy = resolve_policy(
                    MetadataPolicyProfile.from_data(json.loads(state[2])), library
                )
            if policy is None:
                raise MetadataReportError("shadow policy is unavailable")
            outcome = ShadowOutcomeSet.create(
                case, decide_shadow_packet(packet), policy, state[0], report, proposal
            )
            serialized = outcome.to_json()
            existing = self._connection.execute(
                "SELECT outcome_set_id,review_case_id,report_id,proposal_set_id,"
                "resolved_policy_id,source_decision_set_id,evaluated_at,document "
                "FROM shadow_decision_outcomes WHERE outcome_set_id=?",
                (outcome.outcome_set_id,),
            ).fetchone()
            if existing is not None:
                stored, _ = self._validate_shadow_outcome_row(existing)
                if stored != serialized:
                    raise MetadataReportError("shadow outcome identity conflicts")
            if existing is None:
                self._connection.execute(
                    "INSERT INTO shadow_decision_outcomes(outcome_set_id,review_case_id,"
                    "report_id,proposal_set_id,resolved_policy_id,source_decision_set_id,"
                    "evaluated_at,document) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        outcome.outcome_set_id,
                        outcome.review_case_id,
                        outcome.report_id,
                        outcome.proposal_set_id,
                        outcome.resolved_policy_id,
                        outcome.source_decision_set_id,
                        evaluated_at,
                        serialized,
                    ),
                )
            # Preview remains an immutable observation even once it has become
            # the input to decision-only automatic handling.  Decisions are
            # derived inside this same exact-current transaction, never by a
            # later queue or apply worker.
            if state[1] == "automatic":
                attempted_categories: set[str] = set()
                while True:
                    self._connection.execute("SAVEPOINT automatic_admission")
                    try:
                        self._record_automatic_outcomes(outcome, evaluated_at)
                    except AutomaticDecisionError as error:
                        # Keep the outer report/case/outcome projection intact.
                        # A category defect is suspended, then unaffected
                        # categories are retried from the same exact outcome.
                        self._connection.execute("ROLLBACK TO automatic_admission")
                        self._connection.execute("RELEASE automatic_admission")
                        current = self.automatic_safety_state()
                        message = str(error)
                        category = (
                            message.removeprefix("category:")
                            if message.startswith("category:") else None
                        )
                        if category:
                            categories = dict(current.category_suspensions)
                            categories[category] = "automatic_admission_failed"
                            safety = AutomaticSafetyState(
                                current.paused, current.global_suspension,
                                tuple(sorted(categories.items())),
                            )
                            self._set_automatic_safety_state(safety, evaluated_at)
                            if category not in attempted_categories:
                                attempted_categories.add(category)
                                continue
                        else:
                            safety = AutomaticSafetyState(
                                current.paused, "automatic_authority_failed",
                                current.category_suspensions,
                            )
                            self._set_automatic_safety_state(safety, evaluated_at)
                        break
                    else:
                        self._connection.execute("RELEASE automatic_admission")
                        break
            if owns_transaction:
                self._connection.commit()
            return serialized
        except ReviewAdmissionStaleError:
            try:
                if case_for_recovery is not None:
                    self._schedule_current_shadow_work(
                        case_for_recovery, evaluated_at
                    )
                if owns_transaction:
                    self._connection.commit()
                return "stale"
            except (sqlite3.Error, MetadataReportError) as error:
                if self._connection.in_transaction:
                    self._connection.rollback()
                if isinstance(error, MetadataReportError):
                    raise
                raise MetadataReportError(
                    "shadow outcome persistence failed"
                ) from error
        except (
            sqlite3.Error,
            MetadataReportError,
            MetadataPolicyError,
            ReviewCaseValueError,
            ShadowOutcomeError,
        ) as error:
            if self._connection.in_transaction:
                self._connection.rollback()
            if isinstance(error, MetadataReportError):
                raise
            raise MetadataReportError("shadow outcome persistence failed") from error

    def _record_automatic_outcomes(
        self, outcome: ShadowOutcomeSet, occurred_at: int
    ) -> None:
        """Append eligible dispositions, leaving paused/suspended work human."""
        safety = self.automatic_safety_state()
        automatic_dispositions = {"candidate", "keep_current", "ignore"}
        required_bundles = {
            # Subtitle remains a closed witness for the selected title
            # combination. Independently qualified series decisions use their
            # own category and preserve the proposal's combination evidence.
            "title_layout": ("title",),
            "date_precision": ("publishedDate", "publishedYear"),
        }
        bundle_ids: dict[str, str] = {}
        for category, destinations in required_bundles.items():
            if not safety.permits(category):
                continue
            members = tuple(
                field for field in outcome.outcomes
                if field.policy_category == category and field.destination in destinations
            )
            all_automatic = bool(members) and all(
                field.disposition in automatic_dispositions for field in members
            )
            evidence_ids = tuple(
                field.combination_ids
                if category == "title_layout"
                else field.citation_ids
                for field in members
            )
            coherent = (
                len(members) == len(destinations)
                and {field.destination for field in members} == set(destinations)
                and all_automatic
                and len(set(evidence_ids)) == 1
                and bool(evidence_ids[0])
            )
            if not coherent:
                # A fully automatic but incoherent protected tuple is an
                # authority defect, unlike an ordinary abstention/conflict.
                if len(members) == len(destinations) and all_automatic:
                    raise AutomaticDecisionError("category:" + category)
                continue
            bundle_ids[category] = "automatic_bundle_" + hashlib.sha256(
                _canonical_json({
                    "category": category,
                    "evidence_ids": list(evidence_ids[0]),
                }).encode("utf-8")
            ).hexdigest()
        decisions: list[AutomaticDecision] = []
        for field in outcome.outcomes:
            if (
                field.disposition not in automatic_dispositions
                or field.policy_category is None
                or field.policy_option is None
                or field.policy_destination != "automatic"
                or not safety.permits(field.policy_category)
                or (field.policy_category in required_bundles and field.policy_category not in bundle_ids)
            ):
                continue
            try:
                decision = AutomaticDecision.create(
                    review_case_id=outcome.review_case_id,
                    report_id=outcome.report_id,
                    proposal_set_id=outcome.proposal_set_id,
                    proposal_id=field.proposal_id,
                    target_fingerprint=outcome.target_fingerprint,
                    destination=field.destination,
                    outcome=field.disposition,
                    candidate_id=field.candidate_id,
                    resolved_policy_id=outcome.resolved_policy_id,
                    policy_generation=outcome.policy_generation,
                    rule_id=field.rule_id,
                    policy_category=field.policy_category,
                    policy_option=field.policy_option,
                    qualification_manifest_sha256=qualification_manifest_sha256(
                        field.policy_category, field.policy_option
                    ),
                    source_outcome_id=field.outcome_id,
                    bundle_id=bundle_ids.get(field.policy_category),
                    occurred_at=occurred_at,
                )
            except AutomaticDecisionError as error:
                # Construction and identity/source/manifest binding defects
                # invalidate automatic authority as a whole.  Category
                # suspension is reserved for the explicit protected-bundle
                # coherence check above.
                raise AutomaticDecisionError("authority") from error
            decisions.append(decision)
        # A normal human winner or replay is not an authority failure.  Remove
        # its whole bundle before any insert, so a bundle is all-or-none.
        blocked_bundles: set[str] = set()
        for decision in decisions:
            conflict = self._connection.execute(
                "SELECT 1 FROM human_decisions WHERE review_case_id=? AND destination=? "
                "UNION ALL SELECT 1 FROM automatic_decisions WHERE review_case_id=? AND destination=?",
                (decision.review_case_id, decision.destination, decision.review_case_id, decision.destination),
            ).fetchone()
            if conflict is not None and decision.bundle_id is not None:
                blocked_bundles.add(decision.bundle_id)
        decisions = [decision for decision in decisions if decision.bundle_id not in blocked_bundles]
        # Complete validation precedes every insert: an authority defect cannot
        # leave a subset of a multi-field bundle durable.
        for decision in decisions:
            existing = self._connection.execute(
                "SELECT document FROM automatic_decisions WHERE idempotency_digest=?",
                (decision.idempotency_digest,),
            ).fetchone()
            if existing is not None:
                # The event timestamp is deliberately not part of the replay
                # key; a later projection retry must reproduce the immutable
                # authority fact rather than manufacture a second decision.
                continue
            conflict = self._connection.execute(
                "SELECT 1 FROM human_decisions WHERE review_case_id=? AND destination=? "
                "UNION ALL SELECT 1 FROM automatic_decisions WHERE review_case_id=? AND destination=?",
                (decision.review_case_id, decision.destination,
                 decision.review_case_id, decision.destination),
            ).fetchone()
            if conflict is not None:
                # Human wins and independent replay are ordinary fallback, not
                # a global safety event.  Bundles were removed as a unit above.
                continue
            self._connection.execute(
                "INSERT INTO automatic_decisions(decision_id,review_case_id,destination,idempotency_digest,occurred_at,document) VALUES(?,?,?,?,?,?)",
                (decision.decision_id, decision.review_case_id, decision.destination,
                 decision.idempotency_digest, decision.occurred_at, decision.to_json()),
            )

    def list_shadow_outcomes(
        self, limit: int
    ) -> tuple[tuple[str, int], ...]:
        if type(limit) is not int or not 1 <= limit <= 50:
            raise MetadataReportError("shadow outcome sample is unavailable")
        try:
            rows = self._connection.execute(
                "SELECT outcome_set_id,review_case_id,report_id,proposal_set_id,"
                "resolved_policy_id,source_decision_set_id,evaluated_at,document "
                "FROM shadow_decision_outcomes "
                "ORDER BY evaluated_at DESC,outcome_set_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return tuple(self._validate_shadow_outcome_row(row) for row in rows)
        except (
            sqlite3.Error,
            MetadataPolicyError,
            ReviewCaseValueError,
            ShadowOutcomeError,
            TypeError,
            ValueError,
        ) as error:
            raise MetadataReportError("shadow outcome sample is unavailable") from error

    def create_metadata_policy_draft(self, document: str) -> str:
        """Persist an exact canonical draft; there is intentionally no update."""
        try:
            profile = MetadataPolicyProfile.from_data(json.loads(document))
            if profile.canonical_json != document:
                raise MetadataPolicyError("policy document is not canonical")
            self._connection.execute("BEGIN IMMEDIATE")
            posture = self._connection.execute(
                "SELECT posture FROM metadata_policy_state WHERE singleton=1"
            ).fetchone()
            if posture is None or posture[0] not in {"review", "preview", "automatic"}:
                raise MetadataReportError("metadata policy posture is unavailable")
            requested_automatic = {
                f"{category}:{dict(profile.options)[category]}"
                for category, destination in profile.destinations
                if destination == "automatic"
            }
            if not requested_automatic.issubset(QUALIFIED_POLICY_OPTIONS):
                raise MetadataReportError(
                    "metadata policy automatic intent is unqualified"
                )
            self._connection.execute(
                "INSERT OR IGNORE INTO metadata_policy_drafts(policy_id,document) VALUES(?,?)",
                (profile.policy_id, document),
            )
            row = self._connection.execute(
                "SELECT document FROM metadata_policy_drafts WHERE policy_id=?",
                (profile.policy_id,),
            ).fetchone()
            if row != (document,):
                raise MetadataReportError("metadata policy draft is unavailable")
            self._connection.commit()
            return profile.policy_id
        except (
            MetadataReportError,
            MetadataPolicyError,
            ValueError,
            TypeError,
            sqlite3.Error,
        ) as error:
            if self._connection.in_transaction:
                self._connection.rollback()
            if isinstance(error, MetadataReportError):
                raise
            raise MetadataReportError("metadata policy draft is unavailable") from error

    def get_metadata_policy_draft(self, policy_id: str) -> str:
        try:
            row = self._connection.execute(
                "SELECT document FROM metadata_policy_drafts WHERE policy_id=?", (policy_id,)
            ).fetchone()
            if row is None or type(row[0]) is not str:
                raise MetadataReportError("metadata policy draft is unavailable")
            profile = MetadataPolicyProfile.from_data(json.loads(row[0]))
            if profile.policy_id != policy_id or profile.canonical_json != row[0]:
                raise MetadataReportError("metadata policy draft is unavailable")
            return row[0]
        except (MetadataPolicyError, ValueError, TypeError, sqlite3.Error) as error:
            raise MetadataReportError("metadata policy draft is unavailable") from error

    def active_metadata_policy(self) -> str:
        try:
            row = self._connection.execute(
                "SELECT d.document FROM metadata_policy_state s JOIN metadata_policy_drafts d "
                "ON d.policy_id=s.active_policy_id WHERE s.singleton=1"
            ).fetchone()
            if row is None or type(row[0]) is not str:
                raise MetadataReportError("active metadata policy is unavailable")
            profile = MetadataPolicyProfile.from_data(json.loads(row[0]))
            if profile.canonical_json != row[0]:
                raise MetadataReportError("active metadata policy is unavailable")
            return row[0]
        except (MetadataPolicyError, ValueError, TypeError, sqlite3.Error) as error:
            raise MetadataReportError("active metadata policy is unavailable") from error

    def current_resolved_policy_id(self, library: str) -> str:
        if type(library) is not str or not library:
            raise MetadataReportError("active metadata policy is unavailable")
        try:
            row = self._connection.execute(
                "SELECT s.generation,r.resolved_policy_id,d.document FROM metadata_policy_state s "
                "JOIN metadata_policy_drafts d ON d.policy_id=s.active_policy_id "
                "LEFT JOIN metadata_policy_resolutions r ON r.generation=s.generation AND r.library_name=? "
                "WHERE s.singleton=1",
                (library,),
            ).fetchone()
            if row is None:
                raise MetadataReportError("active metadata policy is unavailable")
            if row[1] is not None:
                resolved = self.current_resolved_metadata_policy(library)
                if resolved is None:
                    raise MetadataReportError("active metadata policy resolution is unavailable")
                return resolved.resolved_id
            if row[0] != 1:
                raise MetadataReportError("active metadata policy resolution is unavailable")
            profile = MetadataPolicyProfile.from_data(json.loads(row[2]))
            return resolve_policy(profile, library).resolved_id
        except (MetadataPolicyError, ValueError, TypeError, sqlite3.Error) as error:
            raise MetadataReportError("active metadata policy is unavailable") from error

    def current_resolved_metadata_policy(
        self, library: str
    ) -> ResolvedMetadataPolicy | None:
        return self._metadata_policy_resolution(library, allow_missing=False)

    def metadata_policy_resolution_for_preview(
        self, library: str
    ) -> ResolvedMetadataPolicy | None:
        return self._metadata_policy_resolution(library, allow_missing=True)

    def metadata_policy_preview_baseline(
        self, libraries: tuple[str, ...]
    ) -> tuple[str, tuple[tuple[str, ResolvedMetadataPolicy | None], ...]]:
        if (
            type(libraries) is not tuple
            or libraries != tuple(sorted(set(libraries)))
            or any(type(library) is not str or not library for library in libraries)
        ):
            raise MetadataReportError("active metadata policy is unavailable")
        try:
            join_filter = "0"
            parameters: tuple[str, ...] = ()
            if libraries:
                join_filter = "r.library_name IN (" + ",".join("?" for _ in libraries) + ")"
                parameters = libraries
            rows = self._connection.execute(
                "SELECT s.active_policy_id,d.document,r.library_name,r.resolved_policy_id,r.document "
                "FROM metadata_policy_state s JOIN metadata_policy_drafts d "
                "ON d.policy_id=s.active_policy_id LEFT JOIN metadata_policy_resolutions r "
                f"ON r.generation=s.generation AND {join_filter} "
                "WHERE s.singleton=1 ORDER BY r.library_name",
                parameters,
            ).fetchall()
            if not rows or type(rows[0][0]) is not str or type(rows[0][1]) is not str:
                raise MetadataReportError("active metadata policy is unavailable")
            profile = MetadataPolicyProfile.from_data(json.loads(rows[0][1]))
            if (
                profile.policy_id != rows[0][0]
                or profile.canonical_json != rows[0][1]
                or any(row[:2] != rows[0][:2] for row in rows)
            ):
                raise MetadataReportError("active metadata policy is unavailable")
            resolutions: dict[str, ResolvedMetadataPolicy | None] = {
                library: None for library in libraries
            }
            for _, _, library, resolved_id, document in rows:
                if library is None:
                    continue
                resolved = ResolvedMetadataPolicy.from_data(json.loads(document))
                if (
                    library not in resolutions
                    or resolved.profile_id != profile.policy_id
                    or resolved.library != library
                    or resolved.resolved_id != resolved_id
                    or resolved.canonical_json != document
                ):
                    raise MetadataReportError(
                        "active metadata policy resolution is unavailable"
                    )
                resolutions[library] = resolved
            return rows[0][1], tuple(sorted(resolutions.items()))
        except MetadataReportError:
            raise
        except (MetadataPolicyError, ValueError, TypeError, sqlite3.Error) as error:
            raise MetadataReportError("active metadata policy is unavailable") from error

    def _metadata_policy_resolution(
        self, library: str, *, allow_missing: bool
    ) -> ResolvedMetadataPolicy | None:
        if type(library) is not str or not library:
            raise MetadataReportError("active metadata policy is unavailable")
        try:
            row = self._connection.execute(
                "SELECT s.generation,r.resolved_policy_id,r.document "
                "FROM metadata_policy_state s LEFT JOIN metadata_policy_resolutions r "
                "ON r.generation=s.generation AND r.library_name=? WHERE s.singleton=1",
                (library,),
            ).fetchone()
            if row is None:
                raise MetadataReportError("active metadata policy is unavailable")
            if row[1] is None:
                if row[0] == 1 or allow_missing:
                    return None
                raise MetadataReportError("active metadata policy resolution is unavailable")
            resolved = ResolvedMetadataPolicy.from_data(json.loads(row[2]))
            if (
                resolved.library != library
                or resolved.resolved_id != row[1]
                or resolved.canonical_json != row[2]
            ):
                raise MetadataReportError("active metadata policy resolution is unavailable")
            return resolved
        except MetadataReportError:
            raise
        except (MetadataPolicyError, ValueError, TypeError, sqlite3.Error) as error:
            raise MetadataReportError("active metadata policy is unavailable") from error

    def bind_initial_metadata_policy_resolution(self, document: str) -> str:
        """Bind one exact configured resolution while generation one is active."""
        nested_transaction = self._connection.in_transaction
        savepoint = "bind_initial_metadata_policy_resolution"
        scope_started = False
        try:
            resolved = ResolvedMetadataPolicy.from_data(json.loads(document))
            if resolved.canonical_json != document:
                raise MetadataPolicyError("policy resolution is not canonical")
            self._connection.execute(
                f"SAVEPOINT {savepoint}" if nested_transaction else "BEGIN IMMEDIATE"
            )
            scope_started = True
            state = self._connection.execute(
                "SELECT s.generation,s.active_policy_id,d.document "
                "FROM metadata_policy_state s JOIN metadata_policy_drafts d "
                "ON d.policy_id=s.active_policy_id WHERE s.singleton=1"
            ).fetchone()
            if state is None or state[0] != 1 or resolved.profile_id != state[1]:
                raise MetadataReportError(
                    "active metadata policy resolution is unavailable"
                )
            profile = MetadataPolicyProfile.from_data(json.loads(state[2]))
            if profile.policy_id != resolved.profile_id:
                raise MetadataReportError(
                    "active metadata policy resolution is unavailable"
                )
            requested = {
                f"{category}:{dict(resolved.options)[category]}"
                for category, destination in resolved.destinations
                if destination == "automatic"
            }
            if state[0] == 1 and state[1] == profile.policy_id and requested and not requested.issubset(QUALIFIED_POLICY_OPTIONS):
                raise MetadataReportError("active metadata policy resolution is unqualified")
            self._connection.execute(
                "INSERT OR IGNORE INTO metadata_policy_resolutions("
                "generation,library_name,resolved_policy_id,document) VALUES(1,?,?,?)",
                (resolved.library, resolved.resolved_id, document),
            )
            stored = self._connection.execute(
                "SELECT resolved_policy_id,document FROM metadata_policy_resolutions "
                "WHERE generation=1 AND library_name=?",
                (resolved.library,),
            ).fetchone()
            if stored != (resolved.resolved_id, document):
                raise MetadataReportError(
                    "active metadata policy resolution is unavailable"
                )
            if nested_transaction:
                self._connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            else:
                self._connection.commit()
            scope_started = False
            return resolved.resolved_id
        except (MetadataReportError, MetadataPolicyError, sqlite3.Error, TypeError, ValueError) as error:
            if scope_started and nested_transaction:
                self._connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                self._connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            elif scope_started and self._connection.in_transaction:
                self._connection.rollback()
            if isinstance(error, MetadataReportError):
                raise
            raise MetadataReportError(
                "active metadata policy resolution is unavailable"
            ) from error

    def _restart_metadata_policy_work(
        self,
        work_id: str,
        work_stage: str,
        report_id: str,
        updated_at: int,
    ) -> None:
        restart_stage = "extraction" if work_stage == "extraction" else "proposal"
        self._connection.execute(
            "UPDATE review_preparation_work SET stage=?,state='queued',attempt=0,"
            "recovery_attempt=0,report_id=?,proposal_set_id=NULL,review_case_id=NULL,"
            "failure_code=NULL,failure_root=NULL,updated_at=? WHERE work_id=?",
            (
                restart_stage,
                None if restart_stage == "extraction" else report_id,
                updated_at,
                work_id,
            ),
        )

    def activate_metadata_policy(
        self,
        policy_id: str,
        expected_active_policy_id: str,
        resolutions: tuple[tuple[str, str, str], ...],
        activated_at: int,
        *,
        reconcile_current: bool = False,
    ) -> PolicyTransition | None:
        if (
            type(policy_id) is not str
            or type(expected_active_policy_id) is not str
            or type(resolutions) is not tuple
            or resolutions != tuple(sorted(resolutions))
            or len(resolutions) != len({library for library, _, _ in resolutions})
            or any(
                type(library) is not str
                or not library
                or re.fullmatch(r"resolved_policy_[0-9a-f]{64}", resolved_id) is None
                or type(document) is not str
                or not 2 <= len(document.encode("utf-8")) <= 16_384
                for library, resolved_id, document in resolutions
            )
            or type(activated_at) is not int
            or activated_at < 0
            or type(reconcile_current) is not bool
        ):
            raise MetadataReportError("metadata policy activation is invalid")
        owns_transaction = not self._connection.in_transaction
        try:
            if owns_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            state = self._connection.execute(
                "SELECT active_policy_id,generation,posture FROM metadata_policy_state WHERE singleton=1"
            ).fetchone()
            if state is None or state[2] not in {"review", "preview", "automatic"}:
                raise MetadataReportError("metadata policy activation is stale")
            if state[0] != expected_active_policy_id:
                replay = self._connection.execute(
                    "SELECT transition_id,from_policy_id,to_policy_id,generation,state,total,acknowledged "
                    "FROM metadata_policy_transitions WHERE generation=?",
                    (state[1],),
                ).fetchone()
                if (
                    state[0] == policy_id
                    and replay is not None
                    and replay[1] == expected_active_policy_id
                    and replay[2] == policy_id
                ):
                    result = self.metadata_policy_transition()
                    if result is None or result.transition_id != replay[0]:
                        raise MetadataReportError(
                            "metadata policy transition is unavailable"
                        )
                    if owns_transaction:
                        self._connection.commit()
                    return result
                raise MetadataReportError("metadata policy activation is stale")
            if policy_id == state[0] and not reconcile_current:
                if owns_transaction:
                    self._connection.commit()
                return None
            active_transition = self._connection.execute(
                "SELECT state,acknowledged FROM metadata_policy_transitions WHERE generation=?",
                (state[1],),
            ).fetchone()
            if active_transition is not None and (
                active_transition[0] == "processing"
                or (
                    active_transition[0] == "completed_with_failures"
                    and active_transition[1] == 0
                )
            ):
                raise MetadataReportError("metadata policy transition is processing")
            draft = self._connection.execute(
                "SELECT document FROM metadata_policy_drafts WHERE policy_id=?", (policy_id,)
            ).fetchone()
            if draft is None:
                raise MetadataReportError("metadata policy draft is unavailable")
            profile = MetadataPolicyProfile.from_data(json.loads(draft[0]))
            if profile.policy_id != policy_id or profile.canonical_json != draft[0]:
                raise MetadataReportError("metadata policy draft is unavailable")
            automatic_options = {
                f"{category}:{dict(profile.options)[category]}"
                for category, destination in profile.destinations
                if destination == "automatic"
            }
            if automatic_options and (
                state[2] not in {"preview", "automatic"}
                or not automatic_options.issubset(QUALIFIED_POLICY_OPTIONS)
            ):
                raise MetadataReportError(
                    "metadata policy automatic intent is unqualified outside preview"
                )
            generation = state[1] + 1
            material = f"metadata-policy-transition-v1\0{state[0]}\0{policy_id}\0{generation}"
            transition_id = "policy_transition_" + hashlib.sha256(material.encode()).hexdigest()
            cohort = self._connection.execute(
                "SELECT c.connection_name,c.library_name,c.item_id,c.proposal_set_id,c.report_id,"
                "c.review_case_id,c.checkpoint "
                + _CURRENT_REVIEW_FROM_SQL
                + " WHERE 1 ORDER BY c.connection_name,c.library_name,c.item_id"
            ).fetchall()
            parsed_resolutions: list[tuple[str, str, str]] = []
            for library, resolved_id, document in resolutions:
                resolved = ResolvedMetadataPolicy.from_data(json.loads(document))
                if (
                    resolved.profile_id != policy_id
                    or resolved.library != library
                    or resolved.resolved_id != resolved_id
                    or resolved.canonical_json != document
                    or (
                        any(
                            value == "automatic"
                            for _, value in resolved.destinations
                        )
                        and (
                            state[2] not in {"preview", "automatic"}
                            or not {
                                f"{category}:{dict(resolved.options)[category]}"
                                for category, destination in resolved.destinations
                                if destination == "automatic"
                            }.issubset(QUALIFIED_POLICY_OPTIONS)
                        )
                    )
                ):
                    raise MetadataReportError("metadata policy resolution is invalid")
                parsed_resolutions.append((library, resolved_id, document))
            configured = {library for library, _, _ in parsed_resolutions}
            if any(row[1] not in configured for row in cohort):
                raise MetadataReportError("metadata policy resolution scope changed")
            self._connection.execute(
                "INSERT INTO metadata_policy_transitions(transition_id,from_policy_id,to_policy_id,generation,state,total,acknowledged,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (transition_id, state[0], policy_id, generation, "processing", len(cohort), 0, activated_at, activated_at),
            )
            self._connection.executemany(
                "INSERT INTO metadata_policy_resolutions(generation,library_name,resolved_policy_id,document) VALUES(?,?,?,?)",
                (
                    (generation, library, resolved_id, document)
                    for library, resolved_id, document in parsed_resolutions
                ),
            )
            for connection, library, item, proposal_id, report_id, case_id, checkpoint in cohort:
                transition = ReviewTransition(
                    connection, library, item, checkpoint + 1, proposal_id, report_id,
                    "retired", case_id, None, "policy_changed", "policy-activation-v1",
                )
                self._connection.execute(
                    "INSERT INTO review_case_transitions(transition_id,connection_name,library_name,item_id,checkpoint,actor,from_review_case_id,to_review_case_id,proposal_set_id,report_id,kind,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (transition.transition_id, connection, library, item, checkpoint + 1, transition.actor, case_id, None, proposal_id, report_id, "retired", "policy_changed"),
                )
                updated = self._connection.execute(
                    "UPDATE current_review_case_lineages SET review_case_id=NULL,transition_id=?,checkpoint=?,outcome='retired' "
                    "WHERE connection_name=? AND library_name=? AND item_id=? AND review_case_id=? AND checkpoint=?",
                    (transition.transition_id, checkpoint + 1, connection, library, item, case_id, checkpoint),
                ).rowcount
                if updated != 1:
                    raise MetadataReportError("metadata policy cohort changed")
                work = self._connection.execute(
                    "SELECT work_id,scan_id,stage,state,report_id FROM review_preparation_work WHERE connection_name=? AND library_name=? AND item_id=?",
                    (connection, library, item),
                ).fetchone()
                item_state = "failed" if work is None else "pending"
                failure = "pipeline_unavailable" if work is None else None
                work_id = None if work is None else work[0]
                if work is not None:
                    if work[2] == "provider" and work[4] != report_id:
                        item_state = "failed"
                        failure = "stale"
                    elif work[2] not in {"extraction", "provider"} or work[3] in {
                        "failed",
                        "stale",
                    }:
                        if work[4] != report_id and not (
                            work[2] == "extraction" and work[4] is None
                        ):
                            raise MetadataReportError(
                                "metadata policy cohort evidence changed"
                            )
                        self._restart_metadata_policy_work(
                            work[0], work[2], report_id, activated_at
                        )
                    self._connection.execute(
                        "UPDATE review_preparation_scans SET state='processing',updated_at=? WHERE scan_id=?",
                        (activated_at, work[1]),
                    )
                self._connection.execute(
                    "INSERT INTO metadata_policy_transition_items(transition_id,connection_name,library_name,item_id,report_id,work_id,state,failure_code,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (transition_id, connection, library, item, report_id, work_id, item_state, failure, activated_at),
                )
            changed = self._connection.execute(
                "UPDATE metadata_policy_state SET active_policy_id=?,generation=?,activated_at=? WHERE singleton=1 AND active_policy_id=? AND generation=?",
                (policy_id, generation, activated_at, state[0], state[1]),
            ).rowcount
            if changed != 1:
                raise MetadataReportError("metadata policy activation is stale")
            self._refresh_metadata_policy_transition(transition_id, activated_at)
            result = self.metadata_policy_transition()
            if result is None or result.transition_id != transition_id:
                raise MetadataReportError("metadata policy transition is unavailable")
            if owns_transaction:
                self._connection.commit()
            return result
        except (MetadataReportError, MetadataPolicyError, ReviewCaseValueError, sqlite3.Error, ValueError, TypeError) as error:
            if owns_transaction and self._connection.in_transaction:
                self._connection.rollback()
            if isinstance(error, MetadataReportError):
                raise
            raise MetadataReportError("metadata policy activation is unavailable") from error

    def _refresh_metadata_policy_transition(self, transition_id: str, updated_at: int) -> None:
        counts = self._connection.execute(
            "SELECT COUNT(*),COALESCE(SUM(state='complete'),0),COALESCE(SUM(state='failed'),0) FROM metadata_policy_transition_items WHERE transition_id=?",
            (transition_id,),
        ).fetchone()
        state = "processing" if counts[1] + counts[2] < counts[0] else ("completed_with_failures" if counts[2] else "completed")
        self._connection.execute(
            "UPDATE metadata_policy_transitions SET state=?,updated_at=? WHERE transition_id=?",
            (state, updated_at, transition_id),
        )

    def metadata_policy_transition(self) -> PolicyTransition | None:
        try:
            row = self._connection.execute(
                "SELECT t.transition_id,t.from_policy_id,t.to_policy_id,t.generation,t.state,t.total,t.acknowledged,"
                "COALESCE(SUM(i.state='complete'),0),COALESCE(SUM(i.state='failed'),0) "
                "FROM metadata_policy_transitions t LEFT JOIN metadata_policy_transition_items i ON i.transition_id=t.transition_id "
                "GROUP BY t.transition_id ORDER BY t.generation DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            return PolicyTransition(row[0], row[1], row[2], row[3], row[4], row[7], row[5], row[8], bool(row[6]))
        except sqlite3.Error as error:
            raise MetadataReportError("metadata policy transition is unavailable") from error

    def retry_metadata_policy_transition(self, transition_id: str, retried_at: int) -> PolicyTransition:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT generation,state FROM metadata_policy_transitions WHERE transition_id=?", (transition_id,)
            ).fetchone()
            current = self._connection.execute("SELECT generation FROM metadata_policy_state WHERE singleton=1").fetchone()
            if row is None or current != (row[0],):
                raise MetadataReportError("metadata policy transition is stale")
            failed = self._connection.execute(
                "SELECT connection_name,library_name,item_id,report_id,work_id FROM metadata_policy_transition_items WHERE transition_id=? AND state='failed'",
                (transition_id,),
            ).fetchall()
            for connection, library, item, report_id, work_id in failed:
                work_stage = None
                work_state = None
                if work_id is None:
                    found = self._connection.execute(
                        "SELECT work_id,stage,state FROM review_preparation_work WHERE connection_name=? AND library_name=? AND item_id=? AND report_id=?",
                        (connection, library, item, report_id),
                    ).fetchone()
                    if found is not None:
                        work_id, work_stage, work_state = found
                else:
                    existing_work = self._connection.execute(
                        "SELECT stage,state,report_id FROM review_preparation_work WHERE work_id=?",
                        (work_id,),
                    ).fetchone()
                    if existing_work is None or existing_work[2] not in (
                        {None, report_id}
                        if existing_work[0] == "extraction"
                        else {report_id}
                    ):
                        work_id = None
                    else:
                        work_stage, work_state = existing_work[:2]
                if work_id is None:
                    continue
                if work_state in {"failed", "stale"} or work_stage not in {
                    "extraction",
                    "provider",
                }:
                    self._restart_metadata_policy_work(
                        work_id, work_stage, report_id, retried_at
                    )
                self._connection.execute(
                    "UPDATE metadata_policy_transition_items SET work_id=?,state='pending',failure_code=NULL,updated_at=? WHERE transition_id=? AND connection_name=? AND library_name=? AND item_id=?",
                    (work_id, retried_at, transition_id, connection, library, item),
                )
            self._refresh_metadata_policy_transition(transition_id, retried_at)
            result = self.metadata_policy_transition()
            if result is None or result.transition_id != transition_id:
                raise MetadataReportError("metadata policy transition is unavailable")
            self._connection.commit()
            return result
        except (sqlite3.Error, MetadataReportError) as error:
            if self._connection.in_transaction:
                self._connection.rollback()
            if isinstance(error, MetadataReportError):
                raise
            raise MetadataReportError("metadata policy transition is unavailable") from error

    def acknowledge_metadata_policy_transition(self, transition_id: str, acknowledged_at: int) -> PolicyTransition:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            updated = self._connection.execute(
                "UPDATE metadata_policy_transitions SET acknowledged=1,updated_at=? WHERE transition_id=? AND state IN ('completed','completed_with_failures')",
                (acknowledged_at, transition_id),
            ).rowcount
            if updated != 1:
                raise MetadataReportError("metadata policy transition acknowledgement is unavailable")
            result = self.metadata_policy_transition()
            if result is None or result.transition_id != transition_id:
                raise MetadataReportError("metadata policy transition is unavailable")
            self._connection.commit()
            return result
        except (sqlite3.Error, MetadataReportError) as error:
            if self._connection.in_transaction:
                self._connection.rollback()
            if isinstance(error, MetadataReportError):
                raise
            raise MetadataReportError("metadata policy transition is unavailable") from error

    def current_unapplied_count(self, library: str) -> int:
        """Count only current, undecided review cases for one configured library."""
        if type(library) is not str or not library:
            raise MetadataReportError("policy preview is unavailable")
        try:
            row = self._connection.execute(
                "SELECT count(*) " + _CURRENT_REVIEW_FROM_SQL +
                " WHERE c.library_name=?",
                (library,),
            ).fetchone()
            if row is None or type(row[0]) is not int or row[0] < 0:
                raise MetadataReportError("policy preview is unavailable")
            return row[0]
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise MetadataReportError("policy preview is unavailable") from error

    def current_policy_impact_pairs(
        self, library: str, limit: int
    ) -> tuple[int, tuple[tuple[MetadataReport, ProposalSet], ...]]:
        """Return bounded exact current review pairs for a no-effect policy preview."""
        if type(library) is not str or not library or type(limit) is not int or not 1 <= limit <= 20:
            raise MetadataReportError("policy preview is unavailable")
        try:
            self._connection.execute("BEGIN")
            total_row = self._connection.execute(
                "SELECT count(*) " + _CURRENT_REVIEW_FROM_SQL
                + " WHERE c.library_name=?",
                (library,),
            ).fetchone()
            if total_row is None or type(total_row[0]) is not int or total_row[0] < 0:
                raise MetadataReportError("policy preview is unavailable")
            rows = self._connection.execute(
                "SELECT rc.document,mr.document,p.proposal_set_id,p.report_id,"
                "p.policy_version,p.document " + _CURRENT_REVIEW_FROM_SQL
                + " JOIN proposal_sets p ON p.proposal_set_id=rc.proposal_set_id "
                "AND p.report_id=rc.report_id"
                + " WHERE c.library_name=? "
                "ORDER BY c.connection_name,c.item_id,c.review_case_id LIMIT ?",
                (library, limit),
            ).fetchall()
            pairs_list: list[tuple[MetadataReport, ProposalSet]] = []
            for row in rows:
                case = ReviewCase.from_json(row[0])
                report = MetadataReport.from_json(row[1])
                proposal = ProposalSet.from_json(case.proposal_document)
                case_document, report_document = _validate_review_browse_row(
                    row[:2], self._policy_destinations_for(report, proposal)
                )
                case = ReviewCase.from_json(case_document)
                report = MetadataReport.from_json(report_document)
                proposal = ProposalSet.from_json(case.proposal_document)
                persisted_proposal = ProposalSet.from_json(
                    _validate_proposal_row(row[2:])
                )
                if persisted_proposal.to_json() != proposal.to_json():
                    raise MetadataReportError("policy preview is unavailable")
                pairs_list.append((report, proposal))
            pairs = tuple(pairs_list)
            if len(pairs) > total_row[0]:
                raise MetadataReportError("policy preview is unavailable")
            self._connection.commit()
            return total_row[0], pairs
        except MetadataReportError:
            if self._connection.in_transaction:
                self._connection.rollback()
            raise
        except (sqlite3.Error, TypeError, ValueError) as error:
            if self._connection.in_transaction:
                self._connection.rollback()
            raise MetadataReportError("policy preview is unavailable") from error

    def create_review_session(
        self,
        session_digest: str,
        csrf_digest: str,
        created_at: int,
        expires_at: int,
    ) -> None:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute(
                "INSERT INTO review_sessions(session_digest,csrf_digest,actor,created_at,expires_at) VALUES(?,?,'owner',?,?)",
                (session_digest, csrf_digest, created_at, expires_at),
            )
            self._connection.execute("DELETE FROM review_login_failures")
            self._connection.commit()
        except sqlite3.Error as error:
            self._connection.rollback()
            raise MetadataReportError("review session is unavailable") from error

    def authenticate_review_session(
        self, session_digest: str, now: int
    ) -> tuple[str, str, int]:
        try:
            row = self._connection.execute(
                "SELECT actor,csrf_digest,expires_at FROM review_sessions WHERE session_digest=? AND revoked_at IS NULL AND expires_at>?",
                (session_digest, now),
            ).fetchone()
            if (
                row is None
                or len(row) != 3
                or row[0] != "owner"
                or type(row[1]) is not str
                or type(row[2]) is not int
            ):
                raise MetadataReportError("review session is unavailable")
            return row
        except sqlite3.Error as error:
            raise MetadataReportError("review session is unavailable") from error

    def rotate_review_session_csrf(
        self, session_digest: str, csrf_digest: str, now: int
    ) -> str:
        try:
            row = self._connection.execute(
                "UPDATE review_sessions SET csrf_digest=? "
                "WHERE session_digest=? AND revoked_at IS NULL AND expires_at>? "
                "RETURNING actor,expires_at",
                (csrf_digest, session_digest, now),
            ).fetchone()
            if row is None:
                self._connection.rollback()
                return "unauthorized"
            if (
                len(row) != 2
                or row[0] != "owner"
                or type(row[1]) is not int
            ):
                raise MetadataReportError("review session is unavailable")
            self._connection.commit()
            return "rotated"
        except MetadataReportError:
            self._connection.rollback()
            raise
        except sqlite3.Error as error:
            self._connection.rollback()
            raise MetadataReportError("review session is unavailable") from error

    def revoke_review_session(
        self, session_digest: str, csrf_digest: str, now: int
    ) -> str:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            cursor = self._connection.execute(
                "UPDATE review_sessions SET revoked_at=? WHERE session_digest=? AND csrf_digest=? AND revoked_at IS NULL AND expires_at>?",
                (now, session_digest, csrf_digest, now),
            )
            if cursor.rowcount != 1:
                active = self._connection.execute(
                    "SELECT 1 FROM review_sessions WHERE session_digest=? AND revoked_at IS NULL AND expires_at>?",
                    (session_digest, now),
                ).fetchone()
                self._connection.rollback()
                return "csrf" if active is not None else "unauthorized"
            self._connection.commit()
            return "revoked"
        except sqlite3.Error as error:
            self._connection.rollback()
            raise MetadataReportError("review session is unavailable") from error

    def get_review_case_decisions(
        self, review_case_id: str
    ) -> tuple[str, ...]:
        try:
            envelope = json.loads(self.get_review_case(review_case_id))
            if type(envelope) is not dict or type(envelope.get("case")) is not dict:
                raise MetadataReportError("human decision is unavailable")
            case = ReviewCase.create_from_data(envelope["case"])
            rows = self._connection.execute(
                "SELECT decision_id,proposal_id,target_fingerprint,destination,"
                "actor,outcome,selected_value_document,idempotency_digest,"
                "request_digest,occurred_at,document "
                "FROM human_decisions WHERE review_case_id=? "
                "ORDER BY ordinal",
                (review_case_id,),
            ).fetchall()
            documents = []
            for row in rows:
                decision = HumanDecision.from_json(row[10])
                decision.validate_for(case)
                if (
                    decision.decision_id,
                    decision.proposal_id,
                    decision.target_fingerprint,
                    decision.destination,
                    decision.actor,
                    decision.outcome,
                    _canonical_json(decision.selected_value),
                    decision.idempotency_digest,
                    decision.request_digest,
                    decision.occurred_at,
                ) != row[:10]:
                    raise MetadataReportError("human decision is invalid")
                documents.append(decision.to_json())
            automatic_rows = self._connection.execute(
                "SELECT document FROM automatic_decisions WHERE review_case_id=? "
                "ORDER BY occurred_at,decision_id",
                (review_case_id,),
            ).fetchall()
            for (document,) in automatic_rows:
                decision = json.loads(document)
                if (
                    type(decision) is not dict
                    or decision.get("schema_version") != "automatic-decision-v1"
                    or decision.get("origin") != "automatic_policy"
                    or decision.get("review_case_id") != review_case_id
                ):
                    raise MetadataReportError("automatic decision is invalid")
                documents.append(document)
            return tuple(documents)
        except (sqlite3.Error, HumanDecisionError, TypeError) as error:
            raise MetadataReportError("human decision is unavailable") from error

    def current_cover_consideration(
        self, review_case_id: str, replay_digest: str
    ) -> str | None:
        row = self._connection.execute(
            "SELECT consideration.document FROM current_cover_considerations current "
            "JOIN cover_considerations consideration ON consideration.consideration_id=current.consideration_id "
            "JOIN current_review_case_lineages lineage ON lineage.review_case_id=current.review_case_id "
            "WHERE current.review_case_id=? AND consideration.replay_digest=? "
            "AND NOT EXISTS (SELECT 1 FROM cover_consideration_events event "
            "WHERE event.consideration_id=consideration.consideration_id "
            "AND event.kind='stale')",
            (review_case_id, replay_digest),
        ).fetchone()
        return None if row is None else str(row[0])

    def current_cover_consideration_document(
        self, review_case_id: str
    ) -> str | None:
        row = self._connection.execute(
            "SELECT consideration.document FROM current_cover_considerations current "
            "JOIN cover_considerations consideration ON consideration.consideration_id=current.consideration_id "
            "JOIN current_review_case_lineages lineage ON lineage.review_case_id=current.review_case_id "
            "WHERE current.review_case_id=? "
            "AND NOT EXISTS (SELECT 1 FROM cover_consideration_events event "
            "WHERE event.consideration_id=consideration.consideration_id "
            "AND event.kind='stale')",
            (review_case_id,),
        ).fetchone()
        return None if row is None else str(row[0])

    def latest_cover_consideration_document(
        self, review_case_id: str
    ) -> str | None:
        row = self._connection.execute(
            "SELECT consideration.document FROM current_cover_considerations current "
            "JOIN cover_considerations consideration "
            "ON consideration.consideration_id=current.consideration_id "
            "JOIN current_review_case_lineages lineage "
            "ON lineage.review_case_id=current.review_case_id "
            "WHERE current.review_case_id=?",
            (review_case_id,),
        ).fetchone()
        return None if row is None else str(row[0])

    def _record_cover_consideration_event(
        self,
        consideration_id: str,
        review_case_id: str,
        kind: str,
        occurred_at: int,
        decision_id: str | None = None,
    ) -> None:
        if decision_id is not None and self._connection.execute(
            "SELECT 1 FROM cover_consideration_events WHERE consideration_id=? AND kind=? AND decision_id=?",
            (consideration_id, kind, decision_id),
        ).fetchone() == (1,):
            return
        document = _canonical_json(
            {
                "consideration_id": consideration_id,
                "decision_id": decision_id,
                "kind": kind,
                "occurred_at": occurred_at,
                "review_case_id": review_case_id,
                "schema_version": "cover-consideration-event-v1",
            }
        )
        event_id = "cover_consideration_event_" + hashlib.sha256(
            document.encode("utf-8")
        ).hexdigest()
        self._connection.execute(
            "INSERT OR IGNORE INTO cover_consideration_events(event_id,consideration_id,review_case_id,kind,decision_id,occurred_at,document) VALUES(?,?,?,?,?,?,?)",
            (
                event_id,
                consideration_id,
                review_case_id,
                kind,
                decision_id,
                occurred_at,
                document,
            ),
        )

    def record_cover_consideration(
        self,
        document: str,
        *,
        review_case_id: str,
        report_id: str,
        replay_digest: str,
        supersedes_id: str | None,
        occurred_at: int,
    ) -> str:
        try:
            value = json.loads(document)
            if (
                type(value) is not dict
                or value.get("schema_version") != "cover-consideration-v1"
                or value.get("review_case_id") != review_case_id
                or value.get("report_id") != report_id
                or value.get("replay_digest") != replay_digest
                or value.get("supersedes_id") != supersedes_id
                or _canonical_json(value) != document
            ):
                raise ValueError
            consideration_id = value["consideration_id"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise MetadataReportError("cover consideration is invalid") from error
        with self._connection:
            existing = self._connection.execute(
                "SELECT document FROM cover_considerations WHERE consideration_id=?",
                (consideration_id,),
            ).fetchone()
            if existing is not None:
                if existing != (document,):
                    raise MetadataReportError("cover consideration disagrees")
                return document
            current = self._connection.execute(
                "SELECT consideration_id FROM current_cover_considerations WHERE review_case_id=?",
                (review_case_id,),
            ).fetchone()
            actual_supersedes = None if current is None else current[0]
            if actual_supersedes != supersedes_id:
                raise MetadataReportError("cover consideration changed")
            self._connection.execute(
                "INSERT INTO cover_considerations(consideration_id,review_case_id,report_id,replay_digest,document_digest,supersedes_id,occurred_at,document) VALUES(?,?,?,?,?,?,?,?)",
                (
                    consideration_id,
                    review_case_id,
                    report_id,
                    replay_digest,
                    hashlib.sha256(document.encode("utf-8")).hexdigest(),
                    supersedes_id,
                    occurred_at,
                    document,
                ),
            )
            if supersedes_id is not None:
                self._record_cover_consideration_event(
                    supersedes_id, review_case_id, "superseded", occurred_at
                )
            self._connection.execute(
                "INSERT INTO current_cover_considerations(review_case_id,consideration_id) VALUES(?,?) "
                "ON CONFLICT(review_case_id) DO UPDATE SET consideration_id=excluded.consideration_id",
                (review_case_id, consideration_id),
            )
            self._record_cover_consideration_event(
                consideration_id, review_case_id, "considered", occurred_at
            )
        return document

    def record_cover_consideration_outcome(
        self,
        consideration_id: str,
        kind: str,
        occurred_at: int,
        decision_id: str | None = None,
    ) -> None:
        row = self._connection.execute(
            "SELECT review_case_id FROM cover_considerations WHERE consideration_id=?",
            (consideration_id,),
        ).fetchone()
        if row is None:
            raise MetadataReportError("cover consideration is unavailable")
        with self._connection:
            self._record_cover_consideration_event(
                consideration_id, str(row[0]), kind, occurred_at, decision_id
            )

    def cover_consideration_history(
        self, review_case_id: str, limit: int = 50
    ) -> tuple[dict[str, object], ...]:
        rows = self._connection.execute(
            "SELECT event.document,consideration.document FROM "
            "cover_consideration_events event JOIN cover_considerations consideration "
            "ON consideration.consideration_id=event.consideration_id "
            "WHERE event.review_case_id=? ORDER BY event.occurred_at DESC,event.event_id DESC LIMIT ?",
            (review_case_id, min(max(limit, 1), 50)),
        ).fetchall()
        items = []
        for event_document, consideration_document in rows:
            event = json.loads(event_document)
            event["consideration_document"] = consideration_document
            items.append(event)
        return tuple(items)

    def find_human_decision(self, idempotency_digest: str) -> str | None:
        try:
            row = self._connection.execute(
                "SELECT decision_id,review_case_id,proposal_id,"
                "target_fingerprint,destination,actor,outcome,"
                "selected_value_document,idempotency_digest,"
                "request_digest,occurred_at,document FROM human_decisions "
                "WHERE idempotency_digest=?",
                (idempotency_digest,),
            ).fetchone()
            if row is None:
                return None
            decision = HumanDecision.from_json(row[11])
            stored_columns = (
                decision.decision_id,
                decision.review_case_id,
                decision.proposal_id,
                decision.target_fingerprint,
                decision.destination,
                decision.actor,
                decision.outcome,
                _canonical_json(decision.selected_value),
                decision.idempotency_digest,
                decision.request_digest,
                decision.occurred_at,
            )
            if (
                stored_columns != row[:11]
                or decision.idempotency_digest != idempotency_digest
            ):
                raise MetadataReportError("human decision is unavailable")
            return decision.to_json()
        except (sqlite3.Error, HumanDecisionError, TypeError) as error:
            raise MetadataReportError("human decision is unavailable") from error

    def _record_cover_decision_outcome(
        self,
        decision: HumanDecision,
        consideration_id: str | None,
    ) -> None:
        if decision.destination != "cover":
            if consideration_id is not None:
                raise HumanDecisionError("invalid_choice")
            return
        if consideration_id is None:
            raise HumanDecisionError("stale")
        kind = {
            "accept": "selected",
            "keep_current": "kept_incumbent",
            "reject": "no_action",
        }.get(decision.outcome)
        if kind is None:
            raise HumanDecisionError("invalid_choice")
        existing = self._connection.execute(
            "SELECT consideration_id,kind FROM cover_consideration_events "
            "WHERE decision_id=? AND kind IN "
            "('selected','kept_incumbent','no_action')",
            (decision.decision_id,),
        ).fetchone()
        if existing is not None:
            if existing != (consideration_id, kind):
                raise HumanDecisionError("idempotency_conflict")
            return
        row = self._connection.execute(
            "SELECT consideration.document FROM current_cover_considerations current "
            "JOIN cover_considerations consideration "
            "ON consideration.consideration_id=current.consideration_id "
            "WHERE current.review_case_id=? AND current.consideration_id=?",
            (decision.review_case_id, consideration_id),
        ).fetchone()
        if row is None:
            raise HumanDecisionError("stale")
        try:
            consideration = json.loads(row[0])
            if (
                consideration["proposal_id"] != decision.proposal_id
                or consideration["target_fingerprint"] != decision.target_fingerprint
            ):
                raise HumanDecisionError("stale")
            if decision.outcome == "accept":
                selected = next(
                    (
                        candidate
                        for candidate in consideration["candidates"]
                        if decision.selected_value
                        == {"kind": "cover", "value": candidate["artifact_id"]}
                    ),
                    None,
                )
                if (
                    selected is None
                    or selected["incumbent"] is True
                    or selected["selectable"] is not True
                ):
                    raise HumanDecisionError("invalid_choice")
            elif decision.selected_value is not None:
                raise HumanDecisionError("invalid_choice")
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise MetadataReportError("cover consideration is unavailable") from error
        self._record_cover_consideration_event(
            consideration_id,
            decision.review_case_id,
            kind,
            decision.occurred_at,
            decision.decision_id,
        )

    def record_human_decision(
        self,
        decision: HumanDecision,
        *,
        cover_consideration_id: str | None = None,
    ) -> str:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            replay = self._connection.execute(
                "SELECT request_digest,document FROM human_decisions "
                "WHERE idempotency_digest=?",
                (decision.idempotency_digest,),
            ).fetchone()
            if replay is not None:
                if replay[0] != decision.request_digest:
                    raise HumanDecisionError("idempotency_conflict")
                try:
                    stored = HumanDecision.from_json(replay[1])
                except HumanDecisionError as error:
                    raise MetadataReportError(
                        "human decision persistence failed"
                    ) from error
                if (
                    stored.idempotency_digest != decision.idempotency_digest
                    or stored.request_digest != replay[0]
                ):
                    raise MetadataReportError("human decision persistence failed")
                self._record_cover_decision_outcome(
                    stored, cover_consideration_id
                )
                self._connection.commit()
                return stored.to_json()
            case_envelope = json.loads(self.get_review_case(decision.review_case_id))
            if (
                type(case_envelope) is not dict
                or case_envelope.get("current") is not True
                or type(case_envelope.get("case")) is not dict
            ):
                raise HumanDecisionError("stale")
            case = ReviewCase.create_from_data(case_envelope["case"])
            decision.validate_for(case)
            previous = self._connection.execute(
                "SELECT decision_id,'human' FROM human_decisions "
                "WHERE review_case_id=? AND destination=? ORDER BY ordinal DESC LIMIT 1",
                (decision.review_case_id, decision.destination),
            ).fetchone()
            if previous is None:
                previous = self._connection.execute(
                    "SELECT decision_id,'automatic' FROM automatic_decisions "
                    "WHERE review_case_id=? AND destination=?",
                    (decision.review_case_id, decision.destination),
                ).fetchone()
            automatic = self._connection.execute(
                "SELECT decision_id FROM automatic_decisions "
                "WHERE review_case_id=? AND destination=?",
                (decision.review_case_id, decision.destination),
            ).fetchone()
            if previous is not None:
                effect = self._connection.execute(
                    "SELECT 1 FROM field_effects WHERE decision_id=? AND state NOT IN "
                    "('planned','retryable','stale','superseded','abandoned') LIMIT 1",
                    (previous[0],),
                ).fetchone()
                if effect is not None:
                    raise HumanDecisionError("already_decided")
            selected = _canonical_json(decision.selected_value)
            self._connection.execute(
                "INSERT INTO human_decisions("
                "decision_id,review_case_id,proposal_id,target_fingerprint,"
                "destination,actor,outcome,selected_value_document,"
                "idempotency_digest,request_digest,occurred_at,document) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    decision.decision_id,
                    decision.review_case_id,
                    decision.proposal_id,
                    decision.target_fingerprint,
                    decision.destination,
                    decision.actor,
                    decision.outcome,
                    selected,
                    decision.idempotency_digest,
                    decision.request_digest,
                    decision.occurred_at,
                    decision.to_json(),
                ),
            )
            self._record_cover_decision_outcome(
                decision, cover_consideration_id
            )
            event_id = "human_decision_event_" + hashlib.sha256(
                ("human-decision-event-v1:" + decision.decision_id).encode("utf-8")
            ).hexdigest()
            event_document = _canonical_json(
                {
                    "actor": decision.actor,
                    "decision_id": decision.decision_id,
                    "destination": decision.destination,
                    "event_id": event_id,
                    "kind": "human_decision_recorded",
                    "occurred_at": decision.occurred_at,
                    "outcome": decision.outcome,
                    "review_case_id": decision.review_case_id,
                    "schema_version": "human-decision-event-v1",
                }
            )
            self._connection.execute(
                "INSERT INTO human_decision_events("
                "event_id,decision_id,review_case_id,destination,actor,kind,"
                "occurred_at,document) VALUES(?,?,?,?,?,'human_decision_recorded',?,?)",
                (
                    event_id,
                    decision.decision_id,
                    decision.review_case_id,
                    decision.destination,
                    decision.actor,
                    decision.occurred_at,
                    event_document,
                ),
            )
            if previous is not None:
                self._revoke_field_effect_authority_in_transaction(
                    previous[0], decision.occurred_at, required=False
                )
            if automatic is not None:
                if previous is None or automatic[0] != previous[0]:
                    self._revoke_field_effect_authority_in_transaction(
                        automatic[0], decision.occurred_at, required=False
                    )
                self._set_field_effect_manual_hold(
                    decision.review_case_id,
                    decision.destination,
                    decision.occurred_at,
                    decision.decision_id,
                )
            if self._binding is not None:
                self._binding.require_current()
            self._connection.commit()
            return decision.to_json()
        except HumanDecisionError:
            self._connection.rollback()
            raise
        except MetadataReportError:
            self._connection.rollback()
            raise
        except (
            sqlite3.Error,
            ReviewCaseValueError,
            TypeError,
            ValueError,
        ) as error:
            self._connection.rollback()
            raise MetadataReportError("human decision persistence failed") from error

    def _record_automatic_decision(self, decision: AutomaticDecision) -> str:
        """Legacy internal single-row helper; projection admission owns policy classification.

        It is intentionally not a repository command or web/API seam.  The
        only supported admission path is ``persist_shadow_outcome``, which
        validates a complete outcome and applies bundle/race classification
        before any durable insert.
        """
        if type(decision) is not AutomaticDecision:
            raise MetadataReportError("automatic decision persistence failed")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            replay = self._connection.execute(
                "SELECT document FROM automatic_decisions WHERE idempotency_digest=?",
                (decision.idempotency_digest,),
            ).fetchone()
            if replay is not None:
                if replay != (decision.to_json(),):
                    raise AutomaticDecisionError("idempotency_conflict")
                self._connection.commit()
                return decision.to_json()
            posture = self._connection.execute(
                "SELECT posture FROM metadata_policy_state WHERE singleton=1"
            ).fetchone()
            # The persistence seam is intentionally inert until the complete
            # policy-control/suspension composition admits active posture.
            if posture != ("automatic",):
                raise AutomaticDecisionError("unavailable")
            if not self.automatic_safety_state().permits(decision.policy_category):
                raise AutomaticDecisionError("suspended")
            case = json.loads(self.get_review_case(decision.review_case_id))
            if type(case) is not dict or case.get("current") is not True:
                raise AutomaticDecisionError("stale")
            existing = self._connection.execute(
                "SELECT 1 FROM human_decisions WHERE review_case_id=? AND destination=? "
                "UNION ALL SELECT 1 FROM automatic_decisions WHERE review_case_id=? AND destination=?",
                (decision.review_case_id, decision.destination, decision.review_case_id, decision.destination),
            ).fetchone()
            if existing is not None:
                raise AutomaticDecisionError("already_decided")
            outcome = self._connection.execute(
                "SELECT document FROM shadow_decision_outcomes WHERE review_case_id=? AND report_id=? "
                "AND proposal_set_id=? AND resolved_policy_id=?",
                (decision.review_case_id, decision.report_id, decision.proposal_set_id, decision.resolved_policy_id),
            ).fetchone()
            if outcome is None:
                raise AutomaticDecisionError("stale")
            source = ShadowOutcomeSet.from_json(outcome[0])
            field = next((value for value in source.outcomes if value.outcome_id == decision.source_outcome_id), None)
            if field is None or (field.disposition != decision.outcome) or any((field.proposal_id != decision.proposal_id, field.destination != decision.destination, field.rule_id != decision.rule_id, field.policy_category != decision.policy_category, field.policy_option != decision.policy_option)):
                raise AutomaticDecisionError("stale")
            if decision.policy_category in {"title_layout", "date_precision"}:
                required = ("title",) if decision.policy_category == "title_layout" else ("publishedDate", "publishedYear")
                members = tuple(
                    value for value in source.outcomes
                    if value.policy_category == decision.policy_category
                    and value.destination in required
                )
                expected_bundle = "automatic_bundle_" + hashlib.sha256(
                    _canonical_json({"category": decision.policy_category, "combination_ids": list(field.combination_ids)}).encode("utf-8")
                ).hexdigest()
                if (
                    len(members) != len(required)
                    or {value.destination for value in members} != set(required)
                    or len({value.combination_ids for value in members}) != 1
                    or not field.combination_ids
                    or decision.bundle_id != expected_bundle
                ):
                    raise AutomaticDecisionError("bundle_identity")
            if self.current_resolved_policy_id(json.loads(case["case"])["target"]["library"]) != decision.resolved_policy_id:
                raise AutomaticDecisionError("stale")
            self._connection.execute(
                "INSERT INTO automatic_decisions(decision_id,review_case_id,destination,idempotency_digest,occurred_at,document) VALUES(?,?,?,?,?,?)",
                (decision.decision_id, decision.review_case_id, decision.destination, decision.idempotency_digest, decision.occurred_at, decision.to_json()),
            )
            if self._binding is not None:
                self._binding.require_current()
            self._connection.commit()
            return decision.to_json()
        except AutomaticDecisionError:
            self._connection.rollback()
            raise
        except (sqlite3.Error, MetadataReportError, ShadowOutcomeError, TypeError, ValueError, json.JSONDecodeError) as error:
            self._connection.rollback()
            raise MetadataReportError("automatic decision persistence failed") from error

    def automatic_safety_state(self) -> AutomaticSafetyState:
        try:
            row = self._connection.execute("SELECT paused,global_suspension,category_suspensions_document FROM automatic_safety_state WHERE singleton=1").fetchone()
            if row is None:
                raise MetadataReportError("automatic safety state is unavailable")
            categories = json.loads(row[2])
            return AutomaticSafetyState(bool(row[0]), row[1], tuple(tuple(value) for value in categories))
        except (sqlite3.Error, TypeError, ValueError, json.JSONDecodeError, AutomaticDecisionError) as error:
            raise MetadataReportError("automatic safety state is unavailable") from error

    def set_automatic_safety_state(self, state: AutomaticSafetyState, updated_at: int) -> None:
        if type(state) is not AutomaticSafetyState or type(updated_at) is not int or updated_at < 0:
            raise MetadataReportError("automatic safety state is invalid")
        owns_transaction = not self._connection.in_transaction
        try:
            if owns_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            self._set_automatic_safety_state(state, updated_at)
            if owns_transaction:
                self._connection.commit()
        except (sqlite3.Error, MetadataReportError) as error:
            if owns_transaction and self._connection.in_transaction:
                self._connection.rollback()
            if isinstance(error, MetadataReportError):
                raise
            raise MetadataReportError("automatic safety state is unavailable") from error

    def _set_automatic_safety_state(self, state: AutomaticSafetyState, updated_at: int) -> None:
        """Update safety state inside a caller-owned transaction when present."""
        try:
            changed = self._connection.execute(
                "UPDATE automatic_safety_state SET paused=?,global_suspension=?,category_suspensions_document=?,updated_at=? WHERE singleton=1",
                (int(state.paused), state.global_suspension, _canonical_json([list(value) for value in state.category_suspensions]), updated_at),
            ).rowcount
            if changed != 1:
                raise MetadataReportError("automatic safety state is unavailable")
        except (sqlite3.Error, MetadataReportError) as error:
            if isinstance(error, MetadataReportError):
                raise
            raise MetadataReportError("automatic safety state is unavailable") from error

    def get_review_case_history(
        self, review_case_id: str, limit: int
    ) -> tuple[dict[str, object], ...]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise MetadataReportError("review history is unavailable")
        try:
            transition_rows = self._connection.execute(
                "SELECT transition_id,checkpoint,kind,reason,from_review_case_id,to_review_case_id "
                "FROM review_case_transitions WHERE from_review_case_id=? OR to_review_case_id=? "
                "ORDER BY checkpoint DESC,transition_id DESC LIMIT ?",
                (review_case_id, review_case_id, limit),
            ).fetchall()
            event_rows = self._connection.execute(
                "SELECT event_id,decision_id,review_case_id,destination,actor,"
                "kind,occurred_at,document FROM human_decision_events "
                "WHERE review_case_id=? ORDER BY occurred_at DESC,event_id DESC LIMIT ?",
                (review_case_id, limit),
            ).fetchall()
            shadow_rows = self._connection.execute(
                "SELECT outcome_set_id,review_case_id,report_id,proposal_set_id,"
                "resolved_policy_id,source_decision_set_id,evaluated_at,document "
                "FROM shadow_decision_outcomes "
                "WHERE review_case_id=? ORDER BY evaluated_at DESC,outcome_set_id DESC LIMIT ?",
                (review_case_id, limit),
            ).fetchall()
            automatic_rows = self._connection.execute(
                "SELECT decision_id,destination,occurred_at,document FROM automatic_decisions "
                "WHERE review_case_id=? ORDER BY occurred_at DESC,decision_id DESC LIMIT ?",
                (review_case_id, limit),
            ).fetchall()
            transitions = [
                {
                    "checkpoint": row[1],
                    "kind": row[2],
                    "reason": row[3],
                    "transition_id": row[0],
                }
                for row in transition_rows
            ]
            events: list[tuple[int, str, dict[str, object]]] = []
            for row in event_rows:
                value = json.loads(row[7])
                if type(value) is not dict or set(value) != {
                    "actor",
                    "decision_id",
                    "destination",
                    "event_id",
                    "kind",
                    "occurred_at",
                    "outcome",
                    "review_case_id",
                    "schema_version",
                } or value["schema_version"] != "human-decision-event-v1" or (
                    value["event_id"],
                    value["decision_id"],
                    value["review_case_id"],
                    value["destination"],
                    value["actor"],
                    value["kind"],
                    value["occurred_at"],
                ) != row[:7]:
                    raise MetadataReportError("review history is invalid")
                events.append((row[6], row[0], value))
            for row in shadow_rows:
                document, evaluated_at = self._validate_shadow_outcome_row(row)
                outcome = ShadowOutcomeSet.from_json(document)
                if (
                    outcome.outcome_set_id != row[0]
                    or outcome.review_case_id != review_case_id
                ):
                    raise MetadataReportError("review history is invalid")
                dispositions: dict[str, int] = {}
                for field in outcome.outcomes:
                    dispositions[field.disposition] = (
                        dispositions.get(field.disposition, 0) + 1
                    )
                events.append(
                    (
                        evaluated_at,
                        row[0],
                        {
                            "authority": "none",
                            "dispositions": dispositions,
                            "kind": "shadow_preview_recorded",
                            "occurred_at": evaluated_at,
                            "origin": "policy_preview",
                            "outcome_set_id": row[0],
                            "review_case_id": review_case_id,
                            "schema_version": "shadow-preview-history-event-v1",
                        },
                    )
                )
            for decision_id, destination, occurred_at, document in automatic_rows:
                value = json.loads(document)
                if (
                    type(value) is not dict
                    or value.get("schema_version") != "automatic-decision-v1"
                    or value.get("origin") != "automatic_policy"
                    or value.get("decision_id") != decision_id
                    or value.get("review_case_id") != review_case_id
                    or value.get("destination") != destination
                    or value.get("occurred_at") != occurred_at
                ):
                    raise MetadataReportError("review history is invalid")
                events.append(
                    (occurred_at, decision_id, {
                        "decision_id": decision_id,
                        "destination": destination,
                        "kind": "automatic_decision_recorded",
                        "occurred_at": occurred_at,
                        "origin": "automatic_policy",
                        "outcome": value["outcome"],
                        "review_case_id": review_case_id,
                        "schema_version": "automatic-decision-history-event-v1",
                    })
                )
            ordered_events = [
                value for _, _, value in sorted(events, reverse=True)
            ]
            return tuple((ordered_events + transitions)[:limit])
        except (
            sqlite3.Error,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            ShadowOutcomeError,
        ) as error:
            raise MetadataReportError("review history is unavailable") from error

    def preview_shadow_packet(self, review_case_id: str) -> ShadowReviewPacket:
        """Project the exact provider-independent packet without durable state."""
        try:
            return self._shadow_packet(review_case_id)
        except MetadataReportError:
            raise
        except (
            sqlite3.Error,
            ReviewCaseValueError,
            ShadowReviewValueError,
        ) as error:
            raise MetadataReportError("shadow review preview failed") from error

    def _shadow_packet(self, review_case_id: str) -> ShadowReviewPacket:
        return self._shadow_packet_source(review_case_id)[1]

    def _shadow_packet_source(
        self,
        review_case_id: str,
        *,
        include_policy_ignored: bool = False,
    ) -> tuple[ReviewCase, ShadowReviewPacket]:
        self._require_shadow_case_current(review_case_id)
        return self._persisted_shadow_packet_source(
            review_case_id,
            include_policy_ignored=include_policy_ignored,
        )

    def _require_shadow_case_current(self, review_case_id: str) -> None:
        reopened = json.loads(self.get_review_case(review_case_id))
        if (
            type(reopened) is not dict
            or reopened.get("current") is not True
            or type(reopened.get("case")) is not dict
        ):
            raise ReviewAdmissionStaleError

    def _policy_destinations_for(
        self, report: MetadataReport, proposal: ProposalSet
    ) -> dict[str, str] | None:
        rows = self._connection.execute(
            "SELECT document FROM metadata_policy_resolutions "
            "WHERE library_name=? AND resolved_policy_id=? ORDER BY generation",
            (report.target.library, proposal.resolved_policy_id),
        ).fetchall()
        if not rows:
            # Generation 1 did not persist resolutions and used the original
            # evaluator semantics, irrespective of its identity-only binding.
            return None
        if any(len(row) != 1 or type(row[0]) is not str for row in rows):
            raise MetadataReportError("proposal policy resolution is unavailable")
        documents = {row[0] for row in rows}
        if len(documents) != 1:
            raise MetadataReportError("proposal policy resolution is unavailable")
        resolved = ResolvedMetadataPolicy.from_data(json.loads(next(iter(documents))))
        if (
            resolved.library != report.target.library
            or resolved.resolved_id != proposal.resolved_policy_id
        ):
            raise MetadataReportError("proposal policy resolution is unavailable")
        return human_review_destinations(resolved)

    def _source_report_for_review_case(
        self, review_case_id: str
    ) -> MetadataReport:
        row = self._connection.execute(
            "SELECT r.report_id,r.connection_name,r.library_name,r.item_id,"
            "r.file_identity,r.content_digest,r.schema_version,"
            "r.document_digest,r.document "
            "FROM review_cases c JOIN metadata_reports r "
            "ON r.report_id=c.report_id WHERE c.review_case_id=?",
            (review_case_id,),
        ).fetchone()
        if row is None:
            raise MetadataReportError("shadow review source is unavailable")
        return MetadataReport.from_json(_validate_report_row(row))

    def _source_pair_for_review_case(
        self, review_case_id: str
    ) -> tuple[MetadataReport, ProposalSet]:
        row = self._connection.execute(
            "SELECT r.report_id,r.connection_name,r.library_name,r.item_id,"
            "r.file_identity,r.content_digest,r.schema_version,r.document_digest,"
            "r.document,p.proposal_set_id,p.report_id,p.policy_version,p.document "
            "FROM review_cases c JOIN metadata_reports r ON r.report_id=c.report_id "
            "JOIN proposal_sets p ON p.proposal_set_id=c.proposal_set_id "
            "WHERE c.review_case_id=?", (review_case_id,)
        ).fetchone()
        if row is None:
            raise MetadataReportError("shadow review source is unavailable")
        return (
            MetadataReport.from_json(_validate_report_row(row[:9])),
            ProposalSet.from_json(_validate_proposal_row(row[9:])),
        )

    def _validated_review_pointer(self, lineage: tuple[str, str, str]) -> tuple[object, ...] | None:
        pointer = self._connection.execute("SELECT proposal_set_id,report_id,review_case_id,transition_id,checkpoint,outcome FROM current_review_case_lineages WHERE connection_name=? AND library_name=? AND item_id=?", lineage).fetchone()
        if pointer is None:
            return None
        proposal_id, report_id, case_id, transition_id, checkpoint, outcome = pointer
        transition = self._connection.execute("SELECT connection_name,library_name,item_id,checkpoint,actor,from_review_case_id,to_review_case_id,proposal_set_id,report_id,kind,reason FROM review_case_transitions WHERE transition_id=?", (transition_id,)).fetchone()
        if transition is None:
            raise MetadataReportError("review pointer is invalid")
        try:
            value = ReviewTransition(
                transition[0],
                transition[1],
                transition[2],
                transition[3],
                transition[7],
                transition[8],
                transition[9],
                transition[5],
                transition[6],
                transition[10],
                transition[4],
            )
        except (TypeError, ReviewCaseValueError) as error:
            raise MetadataReportError("review pointer is invalid") from error
        if (
            value.transition_id != transition_id
            or transition[:4] != (*lineage, checkpoint)
            or (value.proposal_set_id, value.report_id, value.kind)
            != (proposal_id, report_id, outcome)
            or value.to_review_case_id != case_id
        ):
            raise MetadataReportError("review pointer is invalid")
        if case_id is not None:
            case_source = self._connection.execute(
                "SELECT proposal_set_id,report_id,document FROM review_cases WHERE review_case_id=?",
                (case_id,),
            ).fetchone()
            if case_source is None:
                raise MetadataReportError("review pointer is invalid")
            try:
                case = ReviewCase.from_json(case_source[2])
            except (TypeError, ReviewCaseValueError) as error:
                raise MetadataReportError("review pointer is invalid") from error
            if (
                case_source[:2] != (proposal_id, report_id)
                or (case.review_case_id, case.proposal_set_id, case.report_id)
                != (case_id, proposal_id, report_id)
                or (
                    case.target["connection"],
                    case.target["library"],
                    case.target["item"],
                )
                != lineage
            ):
                raise MetadataReportError("review pointer is invalid")
        elif outcome not in {"no_case", "retired", "tombstoned"}:
            raise MetadataReportError("review pointer is invalid")
        return pointer

    def current(self, connection: str, library: str, item: str, file_identity: str) -> str:
        document = self.current_optional(connection, library, item, file_identity)
        if document is None:
            raise MetadataReportError("persisted current report is unavailable")
        return document

    def current_optional(self, connection: str, library: str, item: str, file_identity: str) -> str | None:
        try:
            row = self._connection.execute(
                "SELECT c.report_id,r.schema_version,r.connection_name,r.library_name,r.item_id,r.file_identity FROM current_metadata_reports c JOIN metadata_reports r ON r.report_id=c.report_id WHERE c.connection_name=? AND c.library_name=? AND c.item_id=? AND c.file_identity=?",
                (connection, library, item, file_identity),
            ).fetchone()
        except sqlite3.Error as error:
            raise MetadataReportError("persisted current report is invalid") from error
        if row is None:
            return None
        if type(row[0]) is not str:
            raise MetadataReportError("persisted current report is invalid")
        document = self.get(row[0])
        try:
            report = MetadataReport.from_json(document)
        except ReportValueError as error:
            raise MetadataReportError("persisted current report is invalid") from error
        if (
            report.target.connection != connection
            or report.target.library != library
            or report.target.item != item
            or report.file.identity != file_identity
        ):
            raise MetadataReportError("persisted current report scope disagrees")
        return document

    def preview_digest(self, report_id: str, artifact_id: str, preview_variant: str) -> str:
        """Return only a preview explicitly referenced by one exact report."""
        if type(report_id) is not str or type(artifact_id) is not str or preview_variant != "preview-320x480-rgba-png-v1":
            raise MetadataReportError("persisted cover is unavailable")
        try:
            row = self._connection.execute(
                "SELECT c.preview_digest,r.document FROM metadata_report_cover_references c JOIN metadata_reports r ON r.report_id=c.report_id WHERE c.report_id=? AND c.artifact_id=? AND c.preview_variant=?",
                (report_id, artifact_id, preview_variant),
            ).fetchone()
            if row is None or len(row) != 2 or type(row[0]) is not str or type(row[1]) is not str:
                raise MetadataReportError("persisted cover is unavailable")
            report = MetadataReport.from_json(self.get(report_id))
            if not any(source.artifact is not None and source.artifact.artifact_id == artifact_id and source.artifact.preview_variant == preview_variant and source.artifact.preview_digest == row[0] for source in report.cover_sources):
                raise MetadataReportError("persisted cover is unavailable")
            return row[0]
        except MetadataReportError:
            raise
        except sqlite3.Error as error:
            raise MetadataReportError("persisted cover is unavailable") from error

    def preview_digest_for_artifact(self, artifact_id: str, preview_variant: str) -> str:
        """Resolve the one immutable preview identity shared by report references."""
        try:
            rows = self._connection.execute(
                "SELECT DISTINCT preview_digest FROM metadata_report_cover_references WHERE artifact_id=? AND preview_variant=?",
                (artifact_id, preview_variant),
            ).fetchall()
        except sqlite3.Error as error:
            raise MetadataReportError("persisted cover is unavailable") from error
        if len(rows) != 1 or type(rows[0][0]) is not str:
            raise MetadataReportError("persisted cover is unavailable")
        return rows[0][0]

    def artifact_is_referenced(self, digest: str) -> bool:
        """Check one cleanup candidate without semantically auditing history."""
        if (
            type(digest) is not str
            or len(digest) != 64
            or any(value not in "0123456789abcdef" for value in digest)
        ):
            raise MetadataReportError("persisted cover references are invalid")
        try:
            row = self._connection.execute(
                "SELECT 1 FROM metadata_report_cover_references WHERE artifact_id=? OR preview_digest=? LIMIT 1",
                ("cover_" + digest, digest),
            ).fetchone()
            if row is not None:
                return True
            documents = tuple(self._connection.execute(
                "SELECT report_id,document FROM metadata_reports WHERE document LIKE ? ORDER BY report_id",
                ("%" + digest + "%",),
            ))
        except sqlite3.Error as error:
            raise MetadataReportError("persisted cover references are invalid") from error
        for report_id, document in documents:
            if type(report_id) is not str or type(document) is not str:
                raise MetadataReportError("persisted cover references are invalid")
            try:
                report = MetadataReport.from_json(document)
            except (ReportValueError, TypeError) as error:
                raise MetadataReportError("persisted cover references are invalid") from error
            if report.report_id != report_id:
                raise MetadataReportError("persisted cover references are invalid")
            if any(
                source.artifact is not None
                and digest in {
                    source.artifact.original_digest,
                    source.artifact.preview_digest,
                }
                for source in report.cover_sources
            ):
                raise MetadataReportError("persisted cover references are invalid")
        return False

    def close(self) -> None:
        try:
            self._connection.close()
        finally:
            if self._binding is not None:
                self._binding.close()


class SQLiteShadowPreviewRepository:
    """Narrow packet reader backed by a no-create, immutable SQLite connection."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        descriptor: int,
        path: Path,
        identity: tuple[int, int, int, int, int],
    ) -> None:
        self._connection = connection
        self._descriptor = descriptor
        self._path = path
        self._identity = identity
        self._reader = SQLiteMetadataReportRepository(connection)
        self._closed = False

    def _require_current(self) -> None:
        try:
            named = self._path.lstat()
            opened = os.fstat(self._descriptor)
        except OSError as error:
            raise MetadataReportError("database authority changed") from error
        named_identity = (
            named.st_dev,
            named.st_ino,
            named.st_size,
            named.st_mtime_ns,
            named.st_ctime_ns,
        )
        opened_identity = (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        )
        if (
            not stat.S_ISREG(named.st_mode)
            or named_identity != self._identity
            or opened_identity != self._identity
        ):
            raise MetadataReportError("database authority changed")
        _reject_database_sidecars(self._path)

    def current_unapplied_count(self, library: str) -> int:
        self._require_current()
        value = self._reader.current_unapplied_count(library)
        self._require_current()
        return value

    def active_metadata_policy(self) -> str:
        self._require_current()
        value = self._reader.active_metadata_policy()
        self._require_current()
        return value

    def metadata_policy_resolution_for_preview(
        self, library: str
    ) -> ResolvedMetadataPolicy | None:
        self._require_current()
        value = self._reader.metadata_policy_resolution_for_preview(library)
        self._require_current()
        return value

    def metadata_policy_preview_baseline(
        self, libraries: tuple[str, ...]
    ) -> tuple[str, tuple[tuple[str, ResolvedMetadataPolicy | None], ...]]:
        self._require_current()
        value = self._reader.metadata_policy_preview_baseline(libraries)
        self._require_current()
        return value

    def current_policy_impact_pairs(
        self, library: str, limit: int
    ) -> tuple[int, tuple[tuple[MetadataReport, ProposalSet], ...]]:
        self._require_current()
        value = self._reader.current_policy_impact_pairs(library, limit)
        self._require_current()
        return value

    def preview_shadow_packet(self, review_case_id: str) -> ShadowReviewPacket:
        if self._closed:
            raise MetadataReportError("database is unavailable")
        self._require_current()
        try:
            self._connection.execute("BEGIN")
            packet = self._reader._shadow_packet(review_case_id)
            self._connection.execute("COMMIT")
            self._require_current()
            return packet
        except MetadataReportError:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except (
            sqlite3.Error,
            ReviewCaseValueError,
            ShadowReviewValueError,
        ) as error:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise MetadataReportError("shadow decision preview failed") from error

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._connection.close()
        finally:
            os.close(self._descriptor)


def open_shadow_preview_repository(path: Path) -> SQLiteShadowPreviewRepository:
    """Open an exact-current SQLite database without create or write authority."""
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
            connection.execute(_REVIEW_TRANSITIONS_SQL)
            connection.execute(_REVIEW_SCANS_SQL)
            connection.execute(_REVIEW_CONTROL_SQL)
            connection.execute(
                "INSERT INTO review_preparation_control(singleton,paused,claim_sequence,last_library,circuit_failures,circuit_open_until,updated_at) VALUES(1,0,0,NULL,0,0,0)"
            )
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


def _validate_field_effect_state(connection: sqlite3.Connection) -> None:
    """Fail closed on any non-canonical field-effect envelope or row mismatch."""
    try:
        control = connection.execute(
            "SELECT singleton,paused,updated_at FROM field_effect_control"
        ).fetchall()
        if (len(control) != 1 or control[0][0] != 1
                or control[0][1] not in (0, 1)
                or type(control[0][2]) is not int or control[0][2] < 0):
            raise FieldEffectError("invalid_control")

        suspension_keys = {"connection", "destination", "reason", "schema_version",
                           "scope", "updated_at"}
        for scope, connection_name, destination, reason, updated_at, document in connection.execute(
            "SELECT scope,connection_name,destination,reason,updated_at,document "
            "FROM field_effect_suspensions"
        ):
            raw = json.loads(document)
            if (type(raw) is not dict or set(raw) != suspension_keys
                    or raw["schema_version"] != "field-effect-suspension-v1"
                    or (raw["scope"], raw["connection"], raw["destination"],
                        raw["reason"], raw["updated_at"])
                    != (scope, connection_name, destination, reason, updated_at)
                    or canonical_effect_json(raw) != document):
                raise FieldEffectError("invalid_suspension")

        authorities: dict[str, tuple[EffectAuthority, bool, int]] = {}
        authority_keys = {"authority", "current", "schema_version", "updated_at"}
        nested_authority_keys = {"decision_id", "origin", "outcome", "policy_identity",
                                 "qualification_identity", "review_case_id",
                                 "source", "target_fingerprint"}
        for decision_id, current, updated_at, document in connection.execute(
            "SELECT decision_id,current,updated_at,document FROM field_effect_authorities"
        ):
            raw = json.loads(document)
            if (type(raw) is not dict or set(raw) != authority_keys
                    or raw["schema_version"] != "field-effect-authority-state-v1"
                    or type(raw["current"]) is not bool
                    or type(raw["updated_at"]) is not int or raw["updated_at"] < 0
                    or type(raw["authority"]) is not dict
                    or set(raw["authority"]) != nested_authority_keys
                    or (raw["current"], raw["updated_at"]) != (bool(current), updated_at)
                    or canonical_effect_json(raw) != document):
                raise FieldEffectError("invalid_authority")
            item = raw["authority"]
            authority = EffectAuthority(
                item["decision_id"], item["review_case_id"], item["target_fingerprint"],
                item["source"], item["origin"], item["outcome"], item["policy_identity"],
                item["qualification_identity"],
            )
            if decision_id != authority.decision_id or item != authority_to_data(authority):
                raise FieldEffectError("invalid_authority")
            authorities[decision_id] = (authority, bool(current), updated_at)

        consents: dict[str, tuple[EffectConsent, bool, int]] = {}
        consent_keys = {"consent", "current", "schema_version", "updated_at"}
        for consent_id, current, updated_at, document in connection.execute(
            "SELECT consent_id,current,updated_at,document FROM field_effect_consents"
        ):
            raw = json.loads(document)
            if (type(raw) is not dict or set(raw) != consent_keys
                    or raw["schema_version"] != "field-effect-consent-state-v1"
                    or type(raw["current"]) is not bool
                    or type(raw["updated_at"]) is not int or raw["updated_at"] < 0
                    or (raw["current"], raw["updated_at"]) != (bool(current), updated_at)
                    or canonical_effect_json(raw) != document):
                raise FieldEffectError("invalid_consent")
            consent = EffectConsent.from_data(raw["consent"])
            if consent_id != consent.consent_id:
                raise FieldEffectError("invalid_consent")
            consents[consent_id] = (consent, bool(current), updated_at)

        for review_case_id, destination, active, updated_at, document in connection.execute(
            "SELECT review_case_id,destination,active,updated_at,document "
            "FROM field_effect_manual_holds"
        ):
            raw = json.loads(document)
            if (type(raw) is not dict or set(raw) != {
                    "active", "decision_id", "destination", "pending_return",
                    "review_case_id", "schema_version", "updated_at"}
                or raw["schema_version"] != "field-effect-manual-hold-v2"
                or type(raw["active"]) is not bool
                or type(raw["decision_id"]) is not str
                or not re.fullmatch(r"human_decision_[0-9a-f]{64}", raw["decision_id"])
                or type(raw["updated_at"]) is not int or raw["updated_at"] < 0
                or (raw["review_case_id"], raw["destination"], raw["active"],
                    raw["updated_at"]) != (review_case_id, destination,
                                            bool(active), updated_at)
                or (
                    raw["pending_return"] is not None
                    and (
                        not raw["active"]
                        or type(raw["pending_return"]) is not dict
                        or set(raw["pending_return"]) != {"requested_at", "scan_id"}
                        or type(raw["pending_return"]["requested_at"]) is not int
                        or raw["pending_return"]["requested_at"] < 0
                        or type(raw["pending_return"]["scan_id"]) is not str
                        or not raw["pending_return"]["scan_id"]
                    )
                )
                or canonical_effect_json(raw) != document):
                raise FieldEffectError("invalid_manual_hold")

        operations: dict[str, FieldEffectOperation] = {}
        for row in connection.execute(
            "SELECT plan_id,decision_id,connection_name,library_name,work_id,destination,state,attempt,updated_at,document FROM field_effects"
        ):
            operation = FieldEffectOperation.from_json(row[9])
            plan = operation.plan
            expected = (plan.plan_id, plan.authority.decision_id, plan.connection,
                        plan.library, plan.work_id, plan.destination, operation.state,
                        operation.attempt, operation.updated_at)
            if (row[:9] != expected or canonical_effect_json(operation.to_data()) != row[9]
                    or plan.authority.decision_id not in authorities
                    or plan.consent.consent_id not in consents
                    or authorities[plan.authority.decision_id][0] != plan.authority
                    or consents[plan.consent.consent_id][0] != plan.consent):
                raise FieldEffectError("invalid_operation")
            operations[plan.plan_id] = operation

        event_keys = {"attempt", "decision_id", "desired_value", "destination",
                      "event_id", "failure", "kind", "occurred_at", "origin",
                      "plan_id", "pre_value", "schema_version", "sequence", "state"}
        next_sequence: dict[str, int] = {}
        last_events: dict[str, dict[str, object]] = {}
        for event_id, plan_id, sequence, kind, occurred_at, document in connection.execute(
            "SELECT event_id,plan_id,sequence,kind,occurred_at,document FROM field_effect_events ORDER BY plan_id,sequence"
        ):
            raw = json.loads(document)
            operation = operations.get(plan_id)
            expected_sequence = next_sequence.get(plan_id, 1)
            if (operation is None or type(raw) is not dict or set(raw) != event_keys
                    or raw["schema_version"] != "field-effect-event-v1"
                    or type(raw["sequence"]) is not int or raw["sequence"] < 1
                    or type(raw["attempt"]) is not int or not 0 <= raw["attempt"] <= 3
                    or type(raw["occurred_at"]) is not int or raw["occurred_at"] < 0
                    or type(raw["kind"]) is not str or type(raw["state"]) is not str
                    or raw["failure"] not in FIELD_EFFECT_FAILURES
                    or (raw["event_id"], raw["plan_id"], raw["sequence"], raw["kind"], raw["occurred_at"])
                    != (event_id, plan_id, sequence, kind, occurred_at)
                    or sequence != expected_sequence
                    or raw["decision_id"] != operation.plan.authority.decision_id
                    or raw["origin"] != operation.plan.authority.origin
                    or raw["destination"] != operation.plan.destination
                    or raw["pre_value"] != operation.plan.pre_value
                    or raw["desired_value"] != operation.plan.desired_value
                    or raw["state"] != kind
                    or canonical_effect_json(raw) != document
                    or event_id != "effect_event_" + hashlib.sha256(
                        canonical_effect_json({key: value for key, value in raw.items()
                                               if key != "event_id"}).encode()
                    ).hexdigest()):
                raise FieldEffectError("invalid_event")
            next_sequence[plan_id] = expected_sequence + 1
            last_events[plan_id] = raw
        if (set(next_sequence) != set(operations)
                or set(authorities) != {
                    value.plan.authority.decision_id for value in operations.values()
                }
                or not {
                    value.plan.consent.consent_id for value in operations.values()
                }.issubset(consents)):
            raise FieldEffectError("invalid_event")
        for plan_id, operation in operations.items():
            last = last_events[plan_id]
            if ((last["state"], last["attempt"], last["failure"])
                    != (operation.state, operation.attempt, operation.failure)):
                raise FieldEffectError("invalid_event")

        action_keys = {"action_id", "attempt", "kind", "plan_id",
                       "schema_version", "state", "updated_at"}
        actions: dict[tuple[str, str], tuple[str, int]] = {}
        for action_id, plan_id, kind, state, attempt, updated_at, document in connection.execute(
            "SELECT action_id,plan_id,kind,state,attempt,updated_at,document FROM field_effect_actions"
        ):
            raw = json.loads(document)
            if (plan_id not in operations or type(raw) is not dict or set(raw) != action_keys
                    or raw["schema_version"] != "field-effect-action-v1"
                    or type(raw["attempt"]) is not int or not 0 <= raw["attempt"] <= 3
                    or type(raw["updated_at"]) is not int or raw["updated_at"] < 0
                    or type(raw["kind"]) is not str or type(raw["state"]) is not str
                    or (raw["action_id"], raw["plan_id"], raw["kind"], raw["state"],
                        raw["attempt"], raw["updated_at"])
                    != (action_id, plan_id, kind, state, attempt, updated_at)
                    or canonical_effect_json(raw) != document
                    or action_id != "effect_action_" + hashlib.sha256(
                        f"{plan_id}:{kind}".encode()).hexdigest()):
                raise FieldEffectError("invalid_action")
            actions[(plan_id, kind)] = (state, attempt)
        for plan_id, operation in operations.items():
            targeted = actions.get((plan_id, "targeted_read"))
            if ((operation.state == "reconciling") != (targeted is not None and targeted[0] in {"pending", "claimed"})
                    or (targeted is not None and operation.state != "reconciling"
                        and targeted[0] != "completed")):
                raise FieldEffectError("invalid_action")
        for (plan_id, kind), (state, _) in actions.items():
            if (kind == "forward_reevaluation"
                    and (state not in {"pending", "claimed", "completed"}
                         or operations[plan_id].failure != "third_value")):
                raise FieldEffectError("invalid_action")

        cleanup_keys = {"attempt", "plan_id", "schema_version", "state", "updated_at"}
        cleanups: set[str] = set()
        for plan_id, state, attempt, updated_at, document in connection.execute(
            "SELECT plan_id,state,attempt,updated_at,document FROM field_effect_cleanups"
        ):
            raw = json.loads(document)
            operation = operations.get(plan_id)
            if (operation is None or operation.plan.destination != "cover"
                    or operation.state not in {"verified", "stale", "superseded", "abandoned"}
                    or type(raw) is not dict or set(raw) != cleanup_keys
                    or raw["schema_version"] != "field-effect-cleanup-v1"
                    or type(raw["attempt"]) is not int or not 0 <= raw["attempt"] <= 3
                    or type(raw["updated_at"]) is not int or raw["updated_at"] < 0
                    or type(raw["state"]) is not str
                    or (raw["plan_id"], raw["state"], raw["attempt"], raw["updated_at"])
                    != (plan_id, state, attempt, updated_at)
                    or canonical_effect_json(raw) != document):
                raise FieldEffectError("invalid_cleanup")
            cleanups.add(plan_id)
        expected_cleanups = {plan_id for plan_id, operation in operations.items()
                             if operation.plan.destination == "cover"
                             and operation.state in {"verified", "stale", "superseded", "abandoned"}}
        if cleanups != expected_cleanups:
            raise FieldEffectError("invalid_cleanup")
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, sqlite3.Error) as error:
        raise MetadataReportError("database is unavailable or incompatible") from error


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _operational_signal(kind: str, cause: str, scope: str, *,
                        connection: str | None = None,
                        library: str | None = None,
                        category: str | None = None,
                        work_id: str | None = None,
                        field: str | None = None) -> dict[str, object]:
    return {"category": category, "cause": cause, "connection": connection,
            "field": field, "kind": kind, "library": library,
            "scope": scope, "work_id": work_id}


def _admission_json(status: str, review_case_id: str | None) -> str:
    return ReviewAdmissionResult(status, review_case_id).to_json()


def _review_search_pattern(search: str | None) -> tuple[str | None, str]:
    if search is None:
        return None, "%"
    if (
        type(search) is not str
        or not search
        or len(search) > 128
        or search.strip() != search
    ):
        raise MetadataReportError("review search is invalid")
    escaped = (
        search.casefold()
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    return search, f"%{escaped}%"


def _review_casefold(value: object) -> str:
    if type(value) is not str:
        raise ValueError("review search value is invalid")
    return value.casefold()


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


def _validate_report_row(row: tuple[object, ...]) -> str:
    if len(row) != 9:
        raise MetadataReportError("persisted report is invalid")
    report_id, connection, library, item, file_identity, content_digest, schema, digest, document = row
    if type(report_id) is not str or type(document) is not str or schema != REPORT_SCHEMA or type(digest) is not str or hashlib.sha256(document.encode("utf-8")).hexdigest() != digest:
        raise MetadataReportError("persisted report is invalid")
    try:
        report = MetadataReport.from_json(document)
    except ReportValueError as error:
        raise MetadataReportError("persisted report is invalid") from error
    if (report.report_id, report.target.connection, report.target.library, report.target.item, report.file.identity, report.file.content_digest) != (report_id, connection, library, item, file_identity, content_digest):
        raise MetadataReportError("persisted report envelope disagrees")
    return document


def _validate_proposal_row(row: tuple[object, ...]) -> str:
    if len(row) != 4:
        raise MetadataReportError("persisted proposal is invalid")
    proposal_set_id, report_id, policy_version, document = row
    if (
        type(proposal_set_id) is not str
        or policy_version != POLICY_VERSION
        or type(document) is not str
    ):
        raise MetadataReportError("persisted proposal is invalid")
    try:
        proposal_set = ProposalSet.from_json(document)
    except ProposalValueError as error:
        raise MetadataReportError("persisted proposal is invalid") from error
    if (
        proposal_set.proposal_set_id,
        proposal_set.report_id,
        proposal_set.policy_version,
    ) != (
        proposal_set_id,
        report_id,
        policy_version,
    ):
        raise MetadataReportError("persisted proposal envelope disagrees")
    return document

__all__ = [
    "SQLiteMetadataReportRepository",
    "SQLiteShadowPreviewRepository",
    "open_repository",
    "open_shadow_preview_repository",
]
