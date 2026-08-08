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

from mechanical_librarian.application.failure_diagnostics import (
    reviewer_diagnostic_is_admissible,
)
from mechanical_librarian.application.catalogue import CatalogueEntry
from mechanical_librarian.application.metadata_report import MetadataReportError
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
from mechanical_librarian.application.shadow_reviews import (
    ReviewerExecutionProvenance,
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
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    REPORT_SCHEMA,
    ReportValueError,
)
from mechanical_librarian.domain.metadata_policy import MetadataPolicyError, MetadataPolicyProfile
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
from mechanical_librarian.domain.shadow_review import (
    MAX_REQUEST_BYTES,
    QUALIFICATION_MODE,
    REQUEST_SCHEMA,
    ReviewerProfileIdentity,
    ShadowReviewPacket,
    ShadowReviewRequest,
    ShadowReviewResult,
    ShadowReviewValueError,
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
    "schema_version TEXT NOT NULL CHECK (schema_version = 'metadata-report-v22'), "
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
_REVIEW_TRANSITIONS_SQL = (
    "CREATE TABLE review_case_transitions ("
    "transition_id TEXT PRIMARY KEY NOT NULL CHECK(transition_id GLOB 'review_transition_[0-9a-f]*' AND length(transition_id)=82), "
    "connection_name TEXT NOT NULL, library_name TEXT NOT NULL, item_id TEXT NOT NULL, checkpoint INTEGER NOT NULL CHECK(checkpoint > 0), actor TEXT NOT NULL CHECK(actor IN ('review-admission-v1','review-preparation-v1')), "
    "from_review_case_id TEXT REFERENCES review_cases(review_case_id), to_review_case_id TEXT REFERENCES review_cases(review_case_id), proposal_set_id TEXT NOT NULL REFERENCES proposal_sets(proposal_set_id), report_id TEXT NOT NULL REFERENCES metadata_reports(report_id), kind TEXT NOT NULL CHECK(kind IN ('admitted','superseded','no_case','retired','tombstoned')), reason TEXT NOT NULL CHECK(reason IN ('actionable','nonactionable','absent','ineligible')), "
    "CHECK ((kind IN ('admitted','superseded') AND actor='review-admission-v1' AND reason='actionable' AND to_review_case_id IS NOT NULL) OR (kind IN ('no_case','retired') AND actor='review-admission-v1' AND reason='nonactionable' AND to_review_case_id IS NULL) OR (kind='tombstoned' AND actor='review-preparation-v1' AND reason='absent' AND from_review_case_id IS NOT NULL AND to_review_case_id IS NULL) OR (kind='retired' AND actor='review-preparation-v1' AND reason='ineligible' AND from_review_case_id IS NOT NULL AND to_review_case_id IS NULL)), "
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
    "state TEXT NOT NULL CHECK(state IN ('queued','running','complete','failed','stale')), attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt BETWEEN 0 AND 3), recovery_attempt INTEGER NOT NULL DEFAULT 0 CHECK(recovery_attempt BETWEEN 0 AND 3), "
    "report_id TEXT REFERENCES metadata_reports(report_id), proposal_set_id TEXT REFERENCES proposal_sets(proposal_set_id), review_case_id TEXT REFERENCES review_cases(review_case_id), "
    "failure_code TEXT CHECK(failure_code IS NULL OR failure_code IN ('provider','configuration','stale','transient','projection','failed','currentness')), "
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
    "decision_id TEXT PRIMARY KEY NOT NULL CHECK(decision_id GLOB 'human_decision_[0-9a-f]*' AND length(decision_id)=79), "
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
    "document TEXT NOT NULL CHECK(typeof(document)='text' AND length(CAST(document AS BLOB)) BETWEEN 2 AND 65536), "
    "UNIQUE(review_case_id,destination))"
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
_CURRENT_REVIEW_FROM_SQL = (
    "FROM current_review_case_lineages c "
    "JOIN review_cases rc ON rc.review_case_id=c.review_case_id "
    "JOIN metadata_reports mr ON mr.report_id=rc.report_id "
    "JOIN current_metadata_report_lineages ml "
    "ON ml.connection_name=c.connection_name "
    "AND ml.library_name=c.library_name AND ml.item_id=c.item_id "
    "AND ml.report_id=rc.report_id "
    "JOIN current_metadata_reports mf "
    "ON mf.connection_name=c.connection_name "
    "AND mf.library_name=c.library_name AND mf.item_id=c.item_id "
    "AND mf.file_identity=json_extract(rc.document,'$.file_identity') "
    "AND mf.report_id=rc.report_id"
)
_REVIEW_MANUAL_BIBLIOGRAPHIC_SQL = (
    "EXISTS (SELECT 1 FROM json_each(rc.document,'$.results') manual_result "
    "WHERE json_extract(manual_result.value,'$.destination') "
    "IN ('title','subtitle','series') "
    "AND json_array_length(json_extract(manual_result.value,'$.reasons'))=1 "
    "AND json_extract(manual_result.value,'$.reasons[0]')="
    "'no_safe_bibliographic_combination')"
)
_REVIEW_FIELD_ATTENTION_SQL = (
    "(json_extract(result.value,'$.outcome')='unresolved_choice' "
    "OR (json_extract(result.value,'$.destination') "
    "IN ('title','subtitle','series') AND "
    + _REVIEW_MANUAL_BIBLIOGRAPHIC_SQL
    + ")) AND NOT EXISTS (SELECT 1 FROM human_decisions decision "
    "WHERE decision.review_case_id=rc.review_case_id "
    "AND decision.destination=json_extract(result.value,'$.destination'))"
)
_REVIEW_ATTENTION_SQL = (
    "EXISTS (SELECT 1 FROM json_each(rc.document,'$.results') result WHERE "
    + _REVIEW_FIELD_ATTENTION_SQL
    + ")"
)
_REVIEW_SEARCH_SQL = (
    " AND (? IS NULL OR "
    "ml_casefold(COALESCE(json_extract(mr.document,'$.target.fields[0].value'),'')) "
    "LIKE ? ESCAPE '\\' OR ml_casefold(c.item_id) LIKE ? ESCAPE '\\' "
    "OR ml_casefold(c.library_name) LIKE ? ESCAPE '\\')"
)
_SHADOW_REQUESTS_SQL = (
    "CREATE TABLE shadow_review_requests ("
    "review_request_id TEXT PRIMARY KEY NOT NULL CHECK(review_request_id GLOB 'review_request_[0-9a-f]*' AND length(review_request_id)=79), "
    "review_case_id TEXT NOT NULL REFERENCES review_cases(review_case_id), "
    "request_schema TEXT NOT NULL CHECK(request_schema='"
    + REQUEST_SCHEMA
    + "'), "
    "profile_document TEXT NOT NULL CHECK(typeof(profile_document)='text' AND length(CAST(profile_document AS BLOB)) BETWEEN 2 AND 16384), "
    "packet_document TEXT NOT NULL CHECK(typeof(packet_document)='text' AND length(CAST(packet_document AS BLOB)) BETWEEN 2 AND 1048576), "
    "request_document TEXT NOT NULL CHECK(typeof(request_document)='text' AND length(CAST(request_document AS BLOB)) BETWEEN 2 AND "
    + str(MAX_REQUEST_BYTES)
    + "))"
)
_SHADOW_ATTEMPTS_SQL = (
    "CREATE TABLE shadow_review_attempts ("
    "attempt_id TEXT PRIMARY KEY NOT NULL CHECK(attempt_id GLOB 'review_attempt_[0-9a-f]*' AND length(attempt_id)=79), "
    "review_request_id TEXT NOT NULL REFERENCES shadow_review_requests(review_request_id), "
    "attempt_number INTEGER NOT NULL CHECK(attempt_number BETWEEN 1 AND 32), "
    "claim_pid INTEGER NOT NULL CHECK(claim_pid>1), "
    "claim_start_token TEXT NOT NULL CHECK(length(claim_start_token) BETWEEN 1 AND 32 AND claim_start_token NOT GLOB '*[^0-9]*'), "
    "UNIQUE(review_request_id,attempt_number))"
)
_SHADOW_DISPATCHES_SQL = (
    "CREATE TABLE shadow_review_dispatches ("
    "attempt_id TEXT PRIMARY KEY NOT NULL REFERENCES shadow_review_attempts(attempt_id), "
    "pid INTEGER NOT NULL CHECK(pid>1), pgid INTEGER NOT NULL CHECK(pgid>1), "
    "start_token TEXT NOT NULL CHECK(length(start_token) BETWEEN 1 AND 32 AND start_token NOT GLOB '*[^0-9]*'))"
)
_SHADOW_OUTCOMES_SQL = (
    "CREATE TABLE shadow_review_outcomes ("
    "attempt_id TEXT PRIMARY KEY NOT NULL REFERENCES shadow_review_attempts(attempt_id), "
    "kind TEXT NOT NULL CHECK(kind IN ('result','failure')), "
    "result_document TEXT CHECK(result_document IS NULL OR (typeof(result_document)='text' AND length(CAST(result_document AS BLOB)) BETWEEN 2 AND 262144)), "
    "failure_document TEXT CHECK(failure_document IS NULL OR (typeof(failure_document)='text' AND length(CAST(failure_document AS BLOB)) BETWEEN 2 AND 512)), "
    "execution_provenance_document TEXT NOT NULL CHECK(typeof(execution_provenance_document)='text' AND length(CAST(execution_provenance_document AS BLOB)) BETWEEN 2 AND 32768), "
    "CHECK((kind='result' AND result_document IS NOT NULL AND failure_document IS NULL) OR "
    "(kind='failure' AND result_document IS NULL AND failure_document IS NOT NULL)))"
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
_SHADOW_REQUEST_NO_UPDATE_SQL = "CREATE TRIGGER shadow_review_requests_no_update BEFORE UPDATE ON shadow_review_requests BEGIN SELECT RAISE(ABORT, 'shadow review requests are append-only'); END"
_SHADOW_REQUEST_NO_DELETE_SQL = "CREATE TRIGGER shadow_review_requests_no_delete BEFORE DELETE ON shadow_review_requests BEGIN SELECT RAISE(ABORT, 'shadow review requests are append-only'); END"
_SHADOW_ATTEMPT_NO_UPDATE_SQL = "CREATE TRIGGER shadow_review_attempts_no_update BEFORE UPDATE ON shadow_review_attempts BEGIN SELECT RAISE(ABORT, 'shadow review attempts are append-only'); END"
_SHADOW_ATTEMPT_NO_DELETE_SQL = "CREATE TRIGGER shadow_review_attempts_no_delete BEFORE DELETE ON shadow_review_attempts BEGIN SELECT RAISE(ABORT, 'shadow review attempts are append-only'); END"
_SHADOW_DISPATCH_NO_UPDATE_SQL = "CREATE TRIGGER shadow_review_dispatches_no_update BEFORE UPDATE ON shadow_review_dispatches BEGIN SELECT RAISE(ABORT, 'shadow review dispatches are append-only'); END"
_SHADOW_DISPATCH_NO_DELETE_SQL = "CREATE TRIGGER shadow_review_dispatches_no_delete BEFORE DELETE ON shadow_review_dispatches BEGIN SELECT RAISE(ABORT, 'shadow review dispatches are append-only'); END"
_SHADOW_OUTCOME_NO_UPDATE_SQL = "CREATE TRIGGER shadow_review_outcomes_no_update BEFORE UPDATE ON shadow_review_outcomes BEGIN SELECT RAISE(ABORT, 'shadow review outcomes are append-only'); END"
_SHADOW_OUTCOME_NO_DELETE_SQL = "CREATE TRIGGER shadow_review_outcomes_no_delete BEFORE DELETE ON shadow_review_outcomes BEGIN SELECT RAISE(ABORT, 'shadow review outcomes are append-only'); END"


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
                    if row[1] == "processing":
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
                "UPDATE review_preparation_work SET state='failed',failure_code='failed' "
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
                        "proposal_set_id=NULL,review_case_id=NULL,failure_code=NULL "
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
                "AND (stage!='extraction' OR ?<=?) "
                "GROUP BY library_name "
                "ORDER BY CASE WHEN library_name>? THEN 0 ELSE 1 END,library_name "
                "LIMIT 1",
                (control[2], claimed_at, control[1] or ""),
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
                "AND (stage!='extraction' OR ?<=?) "
                f"ORDER BY {ordering} LIMIT 1",
                (
                    library[0],
                    control[2],
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
                if failures:
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
                    "catalogue_generation,catalogue_fingerprint,stage,state,attempt,recovery_attempt,"
                    "report_id,proposal_set_id,review_case_id,failure_code,updated_at"
                    ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(work_id) DO UPDATE SET ordinal=excluded.ordinal,"
                    "library_id=excluded.library_id,file_identity=excluded.file_identity,"
                    "source_revision=excluded.source_revision,"
                    "source_fingerprint=excluded.source_fingerprint,"
                    "producer_fingerprint=excluded.producer_fingerprint,"
                    "catalogue_generation=excluded.catalogue_generation,"
                    "catalogue_fingerprint=excluded.catalogue_fingerprint,"
                    "stage=excluded.stage,state=excluded.state,attempt=0,recovery_attempt=0,"
                    "report_id=excluded.report_id,proposal_set_id=excluded.proposal_set_id,"
                    "review_case_id=excluded.review_case_id,failure_code=NULL,"
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
                        report_id,
                        proposal_set_id,
                        review_case_id,
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
    ) -> None:
        """Bind report publication and provider disposition atomically."""
        if (
            not self._connection.in_transaction
            or action.stage != "extraction"
            or type(recovery_delay_seconds) is not int
            or not 0 <= recovery_delay_seconds <= 86_400
            or provider_failure_code
            not in {None, "provider", "configuration", "transient"}
            or type(claim_next) is not bool
        ):
            raise MetadataReportError("review preparation state is invalid")
        if provider_failure_code == "transient":
            updated = self._connection.execute(
                "UPDATE review_preparation_work SET stage='extraction',"
                "state=CASE WHEN recovery_attempt<2 THEN 'queued' ELSE 'failed' END,"
                "attempt=0,recovery_attempt=recovery_attempt+1,report_id=NULL,"
                "proposal_set_id=NULL,review_case_id=NULL,failure_code='transient',"
                "updated_at=? WHERE work_id=? AND stage='extraction' "
                "AND state='running' AND attempt=?",
                (completed_at, action.action_id, action.attempt),
            ).rowcount
        else:
            state = "queued" if provider_failure_code is None else "failed"
            updated = self._connection.execute(
                "UPDATE review_preparation_work SET stage=?,state=?,attempt=0,"
                "report_id=?,failure_code=?,updated_at=? WHERE work_id=? "
                "AND stage='extraction' AND state='running' AND attempt=?",
                (
                    "proposal" if provider_failure_code is None else "provider",
                    state,
                    report_id,
                    provider_failure_code,
                    completed_at,
                    action.action_id,
                    action.attempt,
                ),
            ).rowcount
        if updated != 1:
            raise MetadataReportError("review preparation state changed")
        if provider_failure_code == "transient":
            self._connection.execute(
                "UPDATE review_preparation_control SET "
                "circuit_failures=MIN(3,circuit_failures+1),"
                "circuit_open_until=MAX(circuit_open_until,"
                "?+(5*(1 << MIN(3,circuit_failures+1)))),updated_at=? "
                "WHERE singleton=1",
                (completed_at, completed_at),
            )
        if recovery_delay_seconds:
            self._connection.execute(
                "UPDATE review_preparation_control SET circuit_open_until="
                "MAX(circuit_open_until,?+?),updated_at=? WHERE singleton=1",
                (completed_at, recovery_delay_seconds, completed_at),
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
    ) -> None:
        if action.stage == "discovery":
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                updated = self._connection.execute(
                    "UPDATE review_preparation_scans SET state='completed_with_failures',"
                    "updated_at=? WHERE scan_id=? AND state='discovering'",
                    (completed_at, action.scan_id),
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
            "stale",
            "transient",
            "projection",
            "failed",
            "currentness",
        }:
            code = "failed"
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
                    "report_id=NULL,proposal_set_id=NULL,review_case_id=NULL,"
                    "failure_code='transient',updated_at=? WHERE work_id=? AND stage=? "
                    "AND state='running' AND attempt=?",
                    (completed_at, action.action_id, action.stage, action.attempt),
                ).rowcount
                if updated == 1:
                    state = self._connection.execute(
                        "SELECT state FROM review_preparation_work WHERE work_id=?",
                        (action.action_id,),
                    ).fetchone()[0]
            else:
                updated = self._connection.execute(
                    "UPDATE review_preparation_work SET state=?,failure_code=?,updated_at=? "
                    "WHERE work_id=? AND stage=? AND state='running' AND attempt=?",
                    (
                        state,
                        code,
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
            if code == "transient":
                self._connection.execute(
                    "UPDATE review_preparation_control SET "
                    "circuit_failures=MIN(3,circuit_failures+1),"
                    "circuit_open_until=MAX(circuit_open_until,?+(5*(1 << MIN(3,circuit_failures+1)))),"
                    "updated_at=? WHERE singleton=1",
                    (completed_at, completed_at),
                )
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
            if (
                report.report_id != proposal_set.report_id
                or report.target.fingerprint != proposal_set.target_fingerprint
                or current != (proposal_set.report_id,)
            ):
                raise MetadataReportError("proposal report changed")
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
            if (
                proposal_set.report_id != report.report_id
                or proposal_set.target_fingerprint != report.target.fingerprint
                or proposal_set.to_json()
                != evaluate_report(report, proposal_set.resolved_policy_id).to_json()
            ):
                raise ReviewAdmissionIncompatibleError
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
            if (
                proposal.proposal_set_id != case.proposal_set_id
                or proposal.report_id != case.report_id
                or proposal.target_fingerprint != case.target_fingerprint
                or proposal.to_json()
                != evaluate_report(report, proposal.resolved_policy_id).to_json()
                or report.target.to_data() != case.target
                or report.target.fingerprint != case.target_fingerprint
                or report.file.identity != case.file_identity
                or tuple(value.to_data() for value in proposal.results) != case.results
            ):
                raise MetadataReportError("review case source disagrees")
            current = self._connection.execute("SELECT review_case_id FROM current_review_case_lineages WHERE connection_name=? AND library_name=? AND item_id=?", (case.target["connection"], case.target["library"], case.target["item"])).fetchone()
            if current is not None and current[0] is not None and (
                type(current[0]) is not str
                or not current[0].startswith("review_case_")
            ):
                raise MetadataReportError("review case pointer is invalid")
            source_current = self._connection.execute("SELECT report_id FROM current_metadata_report_lineages WHERE connection_name=? AND library_name=? AND item_id=?", (case.target["connection"], case.target["library"], case.target["item"])).fetchone()
            file_current = self._connection.execute("SELECT report_id FROM current_metadata_reports WHERE connection_name=? AND library_name=? AND item_id=? AND file_identity=?", (case.target["connection"], case.target["library"], case.target["item"], case.file_identity)).fetchone()
            return _canonical_json({"case": case.to_data(), "current": current == (case.review_case_id,) and source_current == (case.report_id,) and file_current == (case.report_id,), "schema_version": "review-case-get-v1"})
        except (sqlite3.Error, ReviewCaseValueError, KeyError, TypeError) as error:
            raise MetadataReportError("review case is unavailable") from error

    def get_review_case_report(self, review_case_id: str) -> str:
        try:
            return self._source_report_for_review_case(review_case_id).to_json()
        except (sqlite3.Error, ReviewCaseValueError, ReportValueError) as error:
            raise MetadataReportError("review case evidence is unavailable") from error

    def get_review_model_advice(self, review_case_id: str) -> dict[str, object]:
        try:
            row = self._connection.execute(
                "SELECT r.review_request_id,r.request_document,"
                "o.kind,o.result_document "
                "FROM shadow_review_requests r "
                "JOIN shadow_review_attempts a "
                "ON a.review_request_id=r.review_request_id "
                "LEFT JOIN shadow_review_outcomes o ON o.attempt_id=a.attempt_id "
                "WHERE r.review_case_id=? "
                "ORDER BY a.rowid DESC LIMIT 1",
                (review_case_id,),
            ).fetchone()
            if row is None:
                return {"results": [], "state": "not_requested"}
            request_id, request_document, kind, result_document = row
            if kind == "result":
                report = self._source_report_for_review_case(review_case_id)
                request = ShadowReviewRequest.from_json(request_document, report)
                result = ShadowReviewResult.from_json(result_document, request)
                if result.review_request_id != request_id:
                    raise MetadataReportError("review model advice is invalid")
                return {
                    "results": [
                        {
                            "recommendations": [
                                dict(value) for value in result.recommendations
                            ],
                            "review_request_id": result.review_request_id,
                        }
                    ],
                    "state": "available",
                }
            return {
                "results": [],
                "state": "unavailable" if kind == "failure" else "pending",
            }
        except (
            sqlite3.Error,
            ShadowReviewValueError,
            TypeError,
            ValueError,
        ) as error:
            raise MetadataReportError("review model advice is unavailable") from error

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
                model_advice=self.get_review_model_advice(review_case_id),
                decision_documents=self.get_review_case_decisions(review_case_id),
                history=(
                    ()
                    if history_limit is None
                    else self.get_review_case_history(review_case_id, history_limit)
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
            projected = tuple(
                (
                    case_document,
                    report_document,
                    self.get_review_case_decisions(
                        ReviewCase.from_json(case_document).review_case_id
                    ),
                )
                for case_document, report_document in rows
            )
            total, attention_total = self.count_current_review_cases(search)
            snapshot = ReviewLibrarySnapshot(
                rows=projected,
                total=total,
                attention_total=attention_total,
            )
        return snapshot

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
    ) -> tuple[tuple[str, str], ...]:
        try:
            query, pattern = _review_search_pattern(search)
            condition = _REVIEW_ATTENTION_SQL
            if attention is False:
                condition = f"NOT ({condition})"
            elif attention is None:
                condition = "1"
            rows = self._connection.execute(
                "SELECT rc.document,mr.document "
                + _CURRENT_REVIEW_FROM_SQL
                + " WHERE "
                + condition
                + _REVIEW_SEARCH_SQL
                + " ORDER BY CASE WHEN "
                + _REVIEW_ATTENTION_SQL
                + " THEN 0 ELSE 1 END,"
                "ml_casefold(COALESCE(json_extract(mr.document,'$.target.fields[0].value'),'')),"
                "c.library_name,c.item_id,rc.review_case_id LIMIT ? OFFSET ?",
                (query, pattern, pattern, pattern, limit, offset),
            ).fetchall()
            return tuple(_validate_review_browse_row(row) for row in rows)
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
                + _CURRENT_REVIEW_FROM_SQL
                + " WHERE 1"
                + _REVIEW_SEARCH_SQL,
                (query, pattern, pattern, pattern),
            ).fetchone()
            if (
                row is None
                or len(row) != 2
                or any(type(value) is not int or value < 0 for value in row)
                or row[1] > row[0]
            ):
                raise MetadataReportError("review library count is invalid")
            return row
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise MetadataReportError("review library is unavailable") from error

    def list_current_attention_documents(
        self, search: str | None, limit: int, offset: int
    ) -> tuple[tuple[str, str, str], ...]:
        try:
            query, pattern = _review_search_pattern(search)
            rows = self._connection.execute(
                "SELECT rc.document,mr.document,"
                "json_extract(result.value,'$.destination') "
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
                (query, pattern, pattern, pattern, limit, offset),
            ).fetchall()
            values = []
            for row in rows:
                case_document, report_document = _validate_review_browse_row(
                    row[:2]
                )
                if type(row[2]) is not str:
                    raise MetadataReportError("review attention field is invalid")
                values.append((case_document, report_document, row[2]))
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
                (query, pattern, pattern, pattern),
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

    def create_metadata_policy_draft(self, document: str) -> str:
        """Persist an exact canonical draft; there is intentionally no update."""
        try:
            profile = MetadataPolicyProfile.from_data(json.loads(document))
            if profile.canonical_json != document:
                raise MetadataPolicyError("policy document is not canonical")
            self._connection.execute("BEGIN IMMEDIATE")
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
        except (MetadataPolicyError, ValueError, TypeError, sqlite3.Error) as error:
            self._connection.rollback()
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

    def current_unapplied_count(self, library: str) -> int:
        """Count only current, undecided review cases for one configured library."""
        if type(library) is not str or not library:
            raise MetadataReportError("policy preview is unavailable")
        try:
            row = self._connection.execute(
                "SELECT count(*) " + _CURRENT_REVIEW_FROM_SQL +
                " WHERE c.library_name=? AND NOT EXISTS (SELECT 1 FROM human_decisions d WHERE d.review_case_id=rc.review_case_id)",
                (library,),
            ).fetchone()
            if row is None or type(row[0]) is not int or row[0] < 0:
                raise MetadataReportError("policy preview is unavailable")
            return row[0]
        except (sqlite3.Error, TypeError, ValueError) as error:
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
                "ORDER BY occurred_at,decision_id",
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
            return tuple(documents)
        except (sqlite3.Error, HumanDecisionError, TypeError) as error:
            raise MetadataReportError("human decision is unavailable") from error

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

    def record_human_decision(self, decision: HumanDecision) -> str:
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
            existing = self._connection.execute(
                "SELECT 1 FROM human_decisions "
                "WHERE review_case_id=? AND destination=?",
                (decision.review_case_id, decision.destination),
            ).fetchone()
            if existing is not None:
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
            transitions = [
                {
                    "checkpoint": row[1],
                    "kind": row[2],
                    "reason": row[3],
                    "transition_id": row[0],
                }
                for row in transition_rows
            ]
            events = []
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
                events.append(value)
            return tuple((events + transitions)[:limit])
        except (sqlite3.Error, TypeError, ValueError, json.JSONDecodeError) as error:
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

    def preview_shadow_request(
        self, review_case_id: str, profile_identity: str
    ) -> str:
        """Build the exact no-egress packet without changing durable state."""
        try:
            identity = ReviewerProfileIdentity.from_json(profile_identity)
            return self._shadow_request(review_case_id, identity).to_json()
        except MetadataReportError:
            raise
        except (
            sqlite3.Error,
            ReviewCaseValueError,
            ShadowReviewValueError,
        ) as error:
            raise MetadataReportError("shadow review preview failed") from error

    def validate_shadow_request(self, serialized: str) -> ShadowReviewRequest:
        """Decode a request against its immutable authoritative source report."""
        try:
            if (
                type(serialized) is not str
                or not serialized
                or len(serialized.encode("utf-8")) > MAX_REQUEST_BYTES
            ):
                raise ShadowReviewValueError("shadow-review request is invalid")
            raw = json.loads(serialized)
            if (
                type(raw) is not dict
                or type(raw.get("review_case_id")) is not str
            ):
                raise ShadowReviewValueError("shadow-review request is invalid")
            report = self._source_report_for_review_case(
                raw["review_case_id"]
            )
            return ShadowReviewRequest.from_json(serialized, report)
        except MetadataReportError:
            raise
        except (
            json.JSONDecodeError,
            RecursionError,
            ShadowReviewValueError,
            TypeError,
            UnicodeError,
            ValueError,
        ) as error:
            raise MetadataReportError("shadow review request is invalid") from error

    def prepare_shadow_request(
        self,
        exact_id: str,
        profile_identity: str,
        *,
        reevaluate: bool,
        claim_pid: int,
        claim_start_token: str,
        expected_prior_attempt_id: str | None = None,
    ) -> str:
        """Persist one request and attempt before any reviewer process starts."""
        try:
            identity = ReviewerProfileIdentity.from_json(profile_identity)
            if (
                type(claim_pid) is not int
                or claim_pid < 2
                or type(claim_start_token) is not str
                or not 1 <= len(claim_start_token) <= 32
                or not claim_start_token.isascii()
                or not claim_start_token.isdigit()
            ):
                raise MetadataReportError("shadow review claim is invalid")
            self._connection.execute("BEGIN IMMEDIATE")
            if self._binding is not None:
                self._binding.require_current()
            if reevaluate:
                request = self._validated_shadow_request(exact_id)
                if request.profile != identity:
                    raise MetadataReportError("shadow review profile changed")
                current = self._shadow_request(request.review_case_id, identity)
                if current.to_json() != request.to_json():
                    raise ReviewAdmissionStaleError
                previous = self._connection.execute(
                    "SELECT a.attempt_id,a.attempt_number,"
                    "d.pid,d.pgid,d.start_token,o.kind "
                    "FROM shadow_review_attempts a "
                    "LEFT JOIN shadow_review_dispatches d ON d.attempt_id=a.attempt_id "
                    "LEFT JOIN shadow_review_outcomes o ON o.attempt_id=a.attempt_id "
                    "WHERE a.review_request_id=? ORDER BY a.attempt_number DESC LIMIT 1",
                    (request.review_request_id,),
                ).fetchone()
                if (
                    previous is None
                    or expected_prior_attempt_id is None
                    or previous[0] != expected_prior_attempt_id
                ):
                    raise MetadataReportError(
                        "shadow review reevaluation claim changed"
                    )
                if not (
                    previous[5] is None
                    or (previous[5] == "failure" and previous[2] is not None)
                ):
                    raise MetadataReportError(
                        "shadow review latest attempt is terminal"
                    )
                attempt_number = previous[1] + 1
                run = True
            else:
                request = self._shadow_request(exact_id, identity)
                existing = self._connection.execute(
                    "SELECT request_schema,request_document "
                    "FROM shadow_review_requests "
                    "WHERE review_request_id=?",
                    (request.review_request_id,),
                ).fetchone()
                if existing is not None:
                    if existing != (REQUEST_SCHEMA, request.to_json()):
                        raise MetadataReportError("shadow review identity conflicts")
                    attempt = self._connection.execute(
                        "SELECT attempt_id FROM shadow_review_attempts "
                        "WHERE review_request_id=? ORDER BY attempt_number LIMIT 1",
                        (request.review_request_id,),
                    ).fetchone()
                    if attempt is None:
                        raise MetadataReportError("shadow review attempt is missing")
                    if self._binding is not None:
                        self._binding.require_current()
                    self._connection.commit()
                    return _canonical_json(
                        {
                            "attempt_id": attempt[0],
                            "request": request.to_data(),
                            "run": False,
                        }
                    )
                self._connection.execute(
                    "INSERT INTO shadow_review_requests("
                    "review_request_id,review_case_id,request_schema,"
                    "profile_document,packet_document,request_document) "
                    "VALUES(?,?,?,?,?,?)",
                    (
                        request.review_request_id,
                        request.review_case_id,
                        REQUEST_SCHEMA,
                        request.profile.to_json(),
                        request.packet.to_json(),
                        request.to_json(),
                    ),
                )
                attempt_number = 1
                run = True
            attempt_id = _shadow_attempt_id(
                request.review_request_id, attempt_number
            )
            self._connection.execute(
                "INSERT INTO shadow_review_attempts("
                "attempt_id,review_request_id,attempt_number,"
                "claim_pid,claim_start_token) VALUES(?,?,?,?,?)",
                (
                    attempt_id,
                    request.review_request_id,
                    attempt_number,
                    claim_pid,
                    claim_start_token,
                ),
            )
            if self._binding is not None:
                self._binding.require_current()
            self._connection.commit()
            return _canonical_json(
                {
                    "attempt_id": attempt_id,
                    "request": request.to_data(),
                    "run": run,
                }
            )
        except (
            MetadataReportError,
            ReviewAdmissionStaleError,
        ):
            self._connection.rollback()
            raise
        except (
            sqlite3.Error,
            ReviewCaseValueError,
            ShadowReviewValueError,
            TypeError,
            ValueError,
        ) as error:
            self._connection.rollback()
            raise MetadataReportError("shadow review preparation failed") from error

    def record_shadow_dispatch(
        self, attempt_id: str, pid: int, pgid: int, start_token: str
    ) -> None:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            if self._binding is not None:
                self._binding.require_current()
            latest = self._connection.execute(
                "SELECT latest.attempt_id "
                "FROM shadow_review_attempts selected "
                "JOIN shadow_review_attempts latest "
                "ON latest.review_request_id=selected.review_request_id "
                "WHERE selected.attempt_id=? "
                "ORDER BY latest.attempt_number DESC LIMIT 1",
                (attempt_id,),
            ).fetchone()
            if latest != (attempt_id,):
                raise MetadataReportError("shadow review dispatch fence changed")
            self._connection.execute(
                "INSERT INTO shadow_review_dispatches("
                "attempt_id,pid,pgid,start_token) VALUES(?,?,?,?)",
                (attempt_id, pid, pgid, start_token),
            )
            if self._binding is not None:
                self._binding.require_current()
            self._connection.commit()
        except (sqlite3.Error, TypeError, ValueError) as error:
            self._connection.rollback()
            raise MetadataReportError("shadow review dispatch failed") from error

    def record_shadow_result(
        self, attempt_id: str, result: str, execution_provenance: str
    ) -> None:
        try:
            request = self._request_for_attempt(attempt_id)
            validated = ShadowReviewResult.from_json(result, request)
            provenance = ReviewerExecutionProvenance.from_json(
                execution_provenance
            )
            if provenance.requested != request.profile:
                raise MetadataReportError(
                    "shadow review execution provenance changed"
                )
            self._connection.execute("BEGIN IMMEDIATE")
            if self._binding is not None:
                self._binding.require_current()
            dispatch = self._connection.execute(
                "SELECT 1 FROM shadow_review_dispatches WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
            if dispatch != (1,):
                raise MetadataReportError("shadow review was not dispatched")
            self._connection.execute(
                "INSERT INTO shadow_review_outcomes("
                "attempt_id,kind,result_document,failure_document,"
                "execution_provenance_document) "
                "VALUES(?,'result',?,NULL,?)",
                (attempt_id, validated.to_json(), provenance.to_json()),
            )
            if self._binding is not None:
                self._binding.require_current()
            self._connection.commit()
        except MetadataReportError:
            self._connection.rollback()
            raise
        except (sqlite3.Error, ShadowReviewValueError) as error:
            self._connection.rollback()
            raise MetadataReportError("shadow review result failed") from error

    def record_shadow_failure(
        self, attempt_id: str, diagnostic: str, execution_provenance: str
    ) -> None:
        try:
            parsed = FailureDiagnostic.from_json(diagnostic)
            request = self._request_for_attempt(attempt_id)
            provenance = ReviewerExecutionProvenance.from_json(
                execution_provenance
            )
            if provenance.requested != request.profile:
                raise MetadataReportError(
                    "shadow review execution provenance changed"
                )
            if not reviewer_diagnostic_is_admissible(parsed):
                raise MetadataReportError("shadow review failure is invalid")
            self._connection.execute("BEGIN IMMEDIATE")
            if self._binding is not None:
                self._binding.require_current()
            self._connection.execute(
                "INSERT INTO shadow_review_outcomes("
                "attempt_id,kind,result_document,failure_document,"
                "execution_provenance_document) "
                "VALUES(?,'failure',NULL,?,?)",
                (attempt_id, parsed.to_json(), provenance.to_json()),
            )
            if self._binding is not None:
                self._binding.require_current()
            self._connection.commit()
        except MetadataReportError:
            self._connection.rollback()
            raise
        except (sqlite3.Error, ShadowReviewValueError) as error:
            self._connection.rollback()
            raise MetadataReportError("shadow review failure recording failed") from error

    def inspect_shadow_request(
        self, review_request_id: str, profile_identity: str
    ) -> str:
        try:
            identity = ReviewerProfileIdentity.from_json(profile_identity)
            request = self._validated_shadow_request(review_request_id)
            if request.profile != identity:
                raise MetadataReportError("shadow review profile changed")
            try:
                current = (
                    self._shadow_request(request.review_case_id, identity).to_json()
                    == request.to_json()
                )
            except ReviewAdmissionStaleError:
                current = False
            rows = self._connection.execute(
                "SELECT d.pid,o.kind,o.result_document,o.failure_document,"
                "o.execution_provenance_document "
                "FROM shadow_review_attempts a "
                "LEFT JOIN shadow_review_dispatches d ON d.attempt_id=a.attempt_id "
                "LEFT JOIN shadow_review_outcomes o ON o.attempt_id=a.attempt_id "
                "WHERE a.review_request_id=? ORDER BY a.attempt_number",
                (review_request_id,),
            ).fetchall()
            if not rows:
                raise MetadataReportError("shadow review attempt is unavailable")
            diagnostics: dict[
                tuple[str, str, str, bool | None, bool | None],
                FailureDiagnostic,
            ] = {}

            def add_diagnostic(diagnostic: FailureDiagnostic) -> None:
                key = (
                    diagnostic.category.value,
                    diagnostic.component.value,
                    diagnostic.stage.value,
                    diagnostic.retryable,
                    diagnostic.current,
                )
                prior = diagnostics.get(key)
                diagnostics[key] = FailureDiagnostic(
                    diagnostic.category,
                    diagnostic.component,
                    diagnostic.stage,
                    diagnostic.retryable,
                    diagnostic.current,
                    min(
                        diagnostic.count
                        + (0 if prior is None else prior.count),
                        MAX_DIAGNOSTIC_COUNT,
                    ),
                )

            top_status = "pending"
            execution_provenance: list[dict[str, object]] = []
            for row in rows:
                (
                    pid,
                    kind,
                    result_document,
                    failure_document,
                    provenance_document,
                ) = row
                status = "pending"
                if kind is not None:
                    provenance = ReviewerExecutionProvenance.from_json(
                        provenance_document
                    )
                    if provenance.requested != request.profile:
                        raise MetadataReportError(
                            "shadow review execution provenance changed"
                        )
                    execution_provenance.append(
                        provenance.to_projection_data()
                    )
                if kind == "result":
                    result = ShadowReviewResult.from_json(
                        result_document, request
                    )
                    status = QUALIFICATION_MODE
                elif kind == "failure":
                    status = "unknown" if pid is not None else "failed"
                    diagnostic = FailureDiagnostic.from_json(failure_document)
                    if not reviewer_diagnostic_is_admissible(diagnostic):
                        raise MetadataReportError(
                            "shadow review failure is invalid"
                        )
                    add_diagnostic(diagnostic)
                elif pid is not None:
                    status = "unknown"
                    add_diagnostic(
                        FailureDiagnostic(
                            FailureCategory.UNKNOWN,
                            FailureComponent.REVIEWER,
                            FailureStage.EXECUTION,
                        )
                    )
                top_status = status
            return _canonical_json(
                {
                    "attempt_count": len(rows),
                    "current": current,
                    "diagnostics": [
                        value.to_data()
                        for value in sorted(
                            diagnostics.values(),
                            key=lambda value: (
                                value.category.value,
                                value.component.value,
                                value.stage.value,
                                -1
                                if value.retryable is None
                                else int(value.retryable),
                                -1
                                if value.current is None
                                else int(value.current),
                            ),
                        )
                    ],
                    "execution_provenance": execution_provenance,
                    "mode": QUALIFICATION_MODE,
                    "review_case_id": request.review_case_id,
                    "review_request_id": request.review_request_id,
                    "schema_version": "shadow-review-inspect-v3",
                    "status": top_status,
                }
            )
        except MetadataReportError:
            raise
        except (
            sqlite3.Error,
            ShadowReviewValueError,
            TypeError,
            ValueError,
        ) as error:
            raise MetadataReportError("shadow review inspection failed") from error

    def last_shadow_claim(
        self, review_request_id: str
    ) -> tuple[
        str,
        int,
        str,
        int | None,
        int | None,
        str | None,
    ]:
        try:
            self._validated_shadow_request(review_request_id)
            row = self._connection.execute(
                "SELECT a.attempt_id,a.claim_pid,a.claim_start_token,"
                "d.pid,d.pgid,d.start_token "
                "FROM shadow_review_attempts a "
                "LEFT JOIN shadow_review_dispatches d ON d.attempt_id=a.attempt_id "
                "WHERE a.review_request_id=? "
                "ORDER BY a.attempt_number DESC LIMIT 1",
                (review_request_id,),
            ).fetchone()
            if row is None:
                raise MetadataReportError("shadow review attempt is unavailable")
            return row
        except (sqlite3.Error, ShadowReviewValueError) as error:
            raise MetadataReportError("shadow review process is unavailable") from error

    def _shadow_request(
        self, review_case_id: str, identity: ReviewerProfileIdentity
    ) -> ShadowReviewRequest:
        case, packet = self._shadow_packet_source(review_case_id)
        return ShadowReviewRequest.create(case, identity, packet)

    def _shadow_packet(self, review_case_id: str) -> ShadowReviewPacket:
        return self._shadow_packet_source(review_case_id)[1]

    def _shadow_packet_source(
        self, review_case_id: str
    ) -> tuple[ReviewCase, ShadowReviewPacket]:
        reopened = json.loads(self.get_review_case(review_case_id))
        if (
            type(reopened) is not dict
            or reopened.get("current") is not True
            or type(reopened.get("case")) is not dict
        ):
            raise ReviewAdmissionStaleError
        case = ReviewCase.create_from_data(reopened["case"])
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
            raise MetadataReportError("shadow review source is unavailable")
        proposal = ProposalSet.from_json(_validate_proposal_row(proposal_row))
        report = MetadataReport.from_json(_validate_report_row(report_row))
        return case, ShadowReviewPacket.create(case, proposal, report)

    def _validated_shadow_request(
        self, review_request_id: str
    ) -> ShadowReviewRequest:
        row = self._connection.execute(
            "SELECT request_schema,review_case_id,profile_document,"
            "packet_document,request_document FROM shadow_review_requests "
            "WHERE review_request_id=?",
            (review_request_id,),
        ).fetchone()
        if row is None:
            raise MetadataReportError("shadow review request is unavailable")
        if row[0] != REQUEST_SCHEMA:
            raise MetadataReportError("shadow review request is invalid")
        request = ShadowReviewRequest.from_json(
            row[4], self._source_report_for_review_case(row[1])
        )
        if (
            request.schema_version != row[0]
            or request.review_request_id != review_request_id
            or request.review_case_id != row[1]
            or request.profile.to_json() != row[2]
            or request.packet.to_json() != row[3]
        ):
            raise MetadataReportError("shadow review request is invalid")
        return request

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

    def _request_for_attempt(self, attempt_id: str) -> ShadowReviewRequest:
        row = self._connection.execute(
            "SELECT review_request_id FROM shadow_review_attempts "
            "WHERE attempt_id=?",
            (attempt_id,),
        ).fetchone()
        if row is None:
            raise MetadataReportError("shadow review attempt is unavailable")
        return self._validated_shadow_request(row[0])

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
        before = _regular_descriptors(descriptor)
        connection = sqlite3.connect(uri, uri=True, isolation_level=None)
        _validate_connection_binding(connection, path, descriptor, before)
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
            connection.execute(_REVIEW_CASES_SQL)
            connection.execute(_REVIEW_POINTERS_SQL)
            connection.execute(_REVIEW_TRANSITIONS_SQL)
            connection.execute(_REVIEW_SCANS_SQL)
            connection.execute(_REVIEW_CONTROL_SQL)
            connection.execute(
                "INSERT INTO review_preparation_control(singleton,paused,claim_sequence,last_library,circuit_failures,circuit_open_until,updated_at) VALUES(1,0,0,NULL,0,0,0)"
            )
            connection.execute(_REVIEW_WORK_SQL)
            connection.execute(_REVIEW_ABSENCES_SQL)
            connection.execute(_REVIEW_WORK_AGED_CLAIM_INDEX_SQL)
            connection.execute(_REVIEW_WORK_RECENT_CLAIM_INDEX_SQL)
            connection.execute(_REVIEW_SESSIONS_SQL)
            connection.execute(_REVIEW_LOGIN_FAILURES_SQL)
            connection.execute(_HUMAN_DECISIONS_SQL)
            connection.execute(_HUMAN_DECISION_EVENTS_SQL)
            connection.execute(_SHADOW_REQUESTS_SQL)
            connection.execute(_SHADOW_ATTEMPTS_SQL)
            connection.execute(_SHADOW_DISPATCHES_SQL)
            connection.execute(_SHADOW_OUTCOMES_SQL)
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
            connection.execute(_SHADOW_REQUEST_NO_UPDATE_SQL)
            connection.execute(_SHADOW_REQUEST_NO_DELETE_SQL)
            connection.execute(_SHADOW_ATTEMPT_NO_UPDATE_SQL)
            connection.execute(_SHADOW_ATTEMPT_NO_DELETE_SQL)
            connection.execute(_SHADOW_DISPATCH_NO_UPDATE_SQL)
            connection.execute(_SHADOW_DISPATCH_NO_DELETE_SQL)
            connection.execute(_SHADOW_OUTCOME_NO_UPDATE_SQL)
            connection.execute(_SHADOW_OUTCOME_NO_DELETE_SQL)
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
        before = _regular_descriptors(descriptor)
        connection = sqlite3.connect(uri, uri=True, isolation_level=None)
        _validate_connection_binding(connection, path, descriptor, before)
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
    before = _regular_descriptors(descriptor)
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
        _validate_connection_binding(connection, path, descriptor, before)
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
    before: dict[int, tuple[int, int]],
) -> None:
    expected = os.fstat(descriptor)
    expected_identity = (expected.st_dev, expected.st_ino)
    if sys.platform.startswith("linux"):
        after = _regular_descriptors(descriptor)
        newly_opened = set(after) - set(before)
        if not any(after[value] == expected_identity for value in newly_opened):
            raise MetadataReportError("database connection inode is unavailable")
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


def _regular_descriptors(excluded: int) -> dict[int, tuple[int, int]]:
    if not sys.platform.startswith("linux"):
        return {}
    try:
        names = os.listdir("/proc/self/fd")
    except OSError as error:
        raise MetadataReportError("database descriptor binding is unavailable") from error
    result: dict[int, tuple[int, int]] = {}
    for name in names:
        if not name.isascii() or not name.isdigit():
            continue
        descriptor = int(name)
        if descriptor == excluded:
            continue
        try:
            information = os.fstat(descriptor)
        except OSError:
            continue
        if stat.S_ISREG(information.st_mode):
            result[descriptor] = (information.st_dev, information.st_ino)
    return result


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
        ("index", "sqlite_autoindex_metadata_report_cover_references_1", None),
        ("index", "metadata_report_cover_references_artifact_id", _COVER_ARTIFACT_INDEX_SQL),
        ("index", "metadata_report_cover_references_preview_digest", _COVER_PREVIEW_INDEX_SQL),
        ("index", "sqlite_autoindex_human_decision_events_1", None),
        ("index", "sqlite_autoindex_human_decision_events_2", None),
        ("index", "sqlite_autoindex_human_decisions_1", None),
        ("index", "sqlite_autoindex_human_decisions_2", None),
        ("index", "sqlite_autoindex_human_decisions_3", None),
        ("index", "sqlite_autoindex_metadata_reports_1", None),
        ("index", "sqlite_autoindex_metadata_reports_2", None),
        ("index", "sqlite_autoindex_metadata_policy_drafts_1", None),
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
        ("index", "sqlite_autoindex_shadow_review_attempts_1", None),
        ("index", "sqlite_autoindex_shadow_review_attempts_2", None),
        ("index", "sqlite_autoindex_shadow_review_dispatches_1", None),
        ("index", "sqlite_autoindex_shadow_review_outcomes_1", None),
        ("index", "sqlite_autoindex_shadow_review_requests_1", None),
        ("table", "current_metadata_reports", _CURRENT_SQL),
        ("table", "current_metadata_report_lineages", _CURRENT_LINEAGE_SQL),
        ("table", "current_review_case_lineages", _REVIEW_POINTERS_SQL),
        ("table", "human_decision_events", _HUMAN_DECISION_EVENTS_SQL),
        ("table", "human_decisions", _HUMAN_DECISIONS_SQL),
        ("table", "metadata_report_cover_references", _COVER_REFERENCES_SQL),
        ("table", "metadata_reports", _REPORTS_SQL),
        ("table", "metadata_policy_drafts", _METADATA_POLICY_DRAFTS_SQL),
        ("table", "proposal_sets", _PROPOSAL_SETS_SQL),
        ("table", "review_case_transitions", _REVIEW_TRANSITIONS_SQL),
        ("table", "review_cases", _REVIEW_CASES_SQL),
        ("table", "review_preparation_control", _REVIEW_CONTROL_SQL),
        ("table", "review_preparation_scans", _REVIEW_SCANS_SQL),
        ("table", "review_preparation_absences", _REVIEW_ABSENCES_SQL),
        ("table", "review_preparation_work", _REVIEW_WORK_SQL),
        ("table", "review_login_failures", _REVIEW_LOGIN_FAILURES_SQL),
        ("table", "review_sessions", _REVIEW_SESSIONS_SQL),
        ("table", "shadow_review_attempts", _SHADOW_ATTEMPTS_SQL),
        ("table", "shadow_review_dispatches", _SHADOW_DISPATCHES_SQL),
        ("table", "shadow_review_outcomes", _SHADOW_OUTCOMES_SQL),
        ("table", "shadow_review_requests", _SHADOW_REQUESTS_SQL),
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
        ("trigger", "proposal_sets_no_delete", _PROPOSAL_NO_DELETE_SQL),
        ("trigger", "proposal_sets_no_update", _PROPOSAL_NO_UPDATE_SQL),
        ("trigger", "review_case_transitions_no_delete", _REVIEW_TRANSITION_NO_DELETE_SQL),
        ("trigger", "review_case_transitions_no_update", _REVIEW_TRANSITION_NO_UPDATE_SQL),
        ("trigger", "review_cases_no_delete", _REVIEW_NO_DELETE_SQL),
        ("trigger", "review_cases_no_update", _REVIEW_NO_UPDATE_SQL),
        ("trigger", "shadow_review_attempts_no_delete", _SHADOW_ATTEMPT_NO_DELETE_SQL),
        ("trigger", "shadow_review_attempts_no_update", _SHADOW_ATTEMPT_NO_UPDATE_SQL),
        ("trigger", "shadow_review_dispatches_no_delete", _SHADOW_DISPATCH_NO_DELETE_SQL),
        ("trigger", "shadow_review_dispatches_no_update", _SHADOW_DISPATCH_NO_UPDATE_SQL),
        ("trigger", "shadow_review_outcomes_no_delete", _SHADOW_OUTCOME_NO_DELETE_SQL),
        ("trigger", "shadow_review_outcomes_no_update", _SHADOW_OUTCOME_NO_UPDATE_SQL),
        ("trigger", "shadow_review_requests_no_delete", _SHADOW_REQUEST_NO_DELETE_SQL),
        ("trigger", "shadow_review_requests_no_update", _SHADOW_REQUEST_NO_UPDATE_SQL),
    ))
    if objects != expected_objects or connection.execute("PRAGMA foreign_key_check").fetchall() or connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
        raise MetadataReportError("database is unavailable or incompatible")


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


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


def _validate_review_browse_row(row: object) -> tuple[str, str]:
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
                report, ProposalSet.from_json(case.proposal_document).resolved_policy_id
            ).results
        )
        != case.results
    ):
        raise MetadataReportError("review browse row disagrees")
    return case.to_json(), report.to_json()


def _shadow_attempt_id(review_request_id: str, attempt_number: int) -> str:
    return "review_attempt_" + hashlib.sha256(
        _canonical_json(
            {
                "attempt_number": attempt_number,
                "review_request_id": review_request_id,
                "schema_version": "shadow-review-attempt-v1",
            }
        ).encode("utf-8")
    ).hexdigest()


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
