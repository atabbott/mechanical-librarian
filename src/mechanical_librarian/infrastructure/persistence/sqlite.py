"""SQLite connection, migration, and transaction foundation."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import shutil
import sqlite3
import stat
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping

from mechanical_librarian.domain.audit_values import SAFE_AUDIT_SUMMARIES


class SchemaError(RuntimeError):
    """Database schema is missing, incompatible, or has been modified."""


@dataclass(frozen=True)
class Migration:
    version: int
    statements: tuple[str, ...]
    expected_tables: frozenset[str]
    checksum_source: str | None = None
    expected_table_definitions: Mapping[str, str] | None = None

    @property
    def checksum(self) -> str:
        source = (
            self.checksum_source
            if self.checksum_source is not None
            else "\n".join(self.statements)
        )
        return hashlib.sha256(source.encode("utf-8")).hexdigest()


_SAFE_AUDIT_SUMMARIES_SQL = ", ".join(
    f"'{summary}'" for summary in sorted(SAFE_AUDIT_SUMMARIES)
)
_AUDIT_EVENTS_TABLE_SQL = (
    "CREATE TABLE audit_events ("
    "id TEXT PRIMARY KEY CHECK (typeof(id) = 'text' AND length(id) BETWEEN 1 AND 64 "
    "AND id GLOB '[a-z]*' AND id NOT GLOB '*[^a-z0-9_-]*'), "
    "kind TEXT NOT NULL CHECK (typeof(kind) = 'text' AND length(kind) BETWEEN 1 AND 64 "
    "AND kind GLOB '[a-z]*' AND kind NOT GLOB '*[^a-z0-9_-]*'), "
    "created_at TEXT NOT NULL CHECK (typeof(created_at) = 'text' AND length(created_at) = 27 "
    "AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' "
    "AND strftime('%Y-%m-%d', created_at) IS NOT NULL "
    "AND strftime('%Y-%m-%d', created_at) = substr(created_at, 1, 10) "
    "AND strftime('%H:%M:%S', created_at) IS NOT NULL "
    "AND strftime('%H:%M:%S', created_at) = substr(created_at, 12, 8) "
    "AND CAST(substr(created_at, 1, 4) AS INTEGER) BETWEEN 1 AND 9999 "
    "AND CAST(substr(created_at, 12, 2) AS INTEGER) BETWEEN 0 AND 23 "
    "AND CAST(substr(created_at, 15, 2) AS INTEGER) BETWEEN 0 AND 59 "
    "AND CAST(substr(created_at, 18, 2) AS INTEGER) BETWEEN 0 AND 59), "
    "summary TEXT NOT NULL CHECK (typeof(summary) = 'text' AND summary IN ("
    f"{_SAFE_AUDIT_SUMMARIES_SQL})))"
)

_UTC_TIMESTAMP_GLOB = (
    "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T"
    "[0-9][0-9]:[0-9][0-9]:[0-9][0-9]."
    "[0-9][0-9][0-9][0-9][0-9][0-9]Z"
)


def _timestamp_constraint(column: str, *, nullable: bool = False) -> str:
    required = (
        f"typeof({column}) = 'text' AND length({column}) = 27 "
        f"AND {column} GLOB '{_UTC_TIMESTAMP_GLOB}' "
        f"AND strftime('%Y-%m-%dT%H:%M:%S', {column}) IS NOT NULL "
        f"AND strftime('%Y-%m-%dT%H:%M:%S', {column}) = substr({column}, 1, 19)"
    )
    return f"{column} IS NULL OR ({required})" if nullable else required


_WORKFLOW_RUNS_TABLE_SQL = (
    "CREATE TABLE workflow_runs ("
    "run_id TEXT PRIMARY KEY CHECK (typeof(run_id) = 'text' AND length(run_id) BETWEEN 1 AND 64 "
    "AND run_id GLOB '[a-z]*' AND run_id NOT GLOB '*[^a-z0-9_-]*'), "
    "definition_version TEXT NOT NULL CHECK (typeof(definition_version) = 'text' AND length(definition_version) BETWEEN 1 AND 64), "
    "graph_digest TEXT NOT NULL CHECK (typeof(graph_digest) = 'text' AND length(graph_digest) = 64 AND graph_digest NOT GLOB '*[^0-9a-f]*'), "
    "state TEXT NOT NULL CHECK (state IN ('waiting','running','succeeded','failed_terminal','cancelled','superseded')), "
    "cancellation_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancellation_requested IN (0,1)), "
    "replanned_from TEXT UNIQUE REFERENCES workflow_runs(run_id), "
    "superseded_by TEXT UNIQUE REFERENCES workflow_runs(run_id), "
    f"created_at TEXT NOT NULL CHECK ({_timestamp_constraint('created_at')}), "
    f"updated_at TEXT NOT NULL CHECK ({_timestamp_constraint('updated_at')}), "
    "CHECK (replanned_from IS NULL OR replanned_from != run_id), "
    "CHECK (superseded_by IS NULL OR superseded_by != run_id))"
)
_WORKFLOW_STAGES_TABLE_SQL = (
    "CREATE TABLE workflow_stages ("
    "run_id TEXT NOT NULL REFERENCES workflow_runs(run_id), "
    "stage_id TEXT NOT NULL CHECK (typeof(stage_id) = 'text' AND length(stage_id) BETWEEN 1 AND 64 "
    "AND stage_id GLOB '[a-z]*' AND stage_id NOT GLOB '*[^a-z0-9_-]*'), "
    "position INTEGER NOT NULL CHECK (position BETWEEN 0 AND 31), "
    "kind TEXT NOT NULL CHECK (kind IN ('root','branch','join')), "
    "version TEXT NOT NULL CHECK (typeof(version) = 'text' AND length(version) BETWEEN 1 AND 64), "
    "expected_outcome TEXT NOT NULL CHECK (expected_outcome IN ('completed')), "
    "state TEXT NOT NULL CHECK (state IN ('waiting','ready','running','succeeded','failed_terminal','cancelled','dependency_failed','dependency_cancelled')), "
    "PRIMARY KEY (run_id, stage_id), UNIQUE (run_id, position))"
)
_WORKFLOW_DEPENDENCIES_TABLE_SQL = (
    "CREATE TABLE workflow_dependencies ("
    "run_id TEXT NOT NULL, dependent_stage_id TEXT NOT NULL, prerequisite_stage_id TEXT NOT NULL, "
    "required INTEGER NOT NULL DEFAULT 1 CHECK (required = 1), "
    "PRIMARY KEY (run_id, dependent_stage_id, prerequisite_stage_id), "
    "FOREIGN KEY (run_id, dependent_stage_id) REFERENCES workflow_stages(run_id, stage_id), "
    "FOREIGN KEY (run_id, prerequisite_stage_id) REFERENCES workflow_stages(run_id, stage_id), "
    "CHECK (dependent_stage_id != prerequisite_stage_id))"
)
_JOBS_TABLE_SQL = (
    "CREATE TABLE jobs ("
    "job_id TEXT PRIMARY KEY CHECK (typeof(job_id) = 'text' AND length(job_id) BETWEEN 1 AND 64 "
    "AND job_id GLOB '[a-z]*' AND job_id NOT GLOB '*[^a-z0-9_-]*'), "
    "dedup_key TEXT NOT NULL UNIQUE CHECK (typeof(dedup_key) = 'text' AND length(dedup_key) BETWEEN 1 AND 128), "
    "run_id TEXT NOT NULL, stage_id TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('root','branch','join')), "
    "version TEXT NOT NULL CHECK (typeof(version) = 'text' AND length(version) BETWEEN 1 AND 64), "
    "state TEXT NOT NULL CHECK (state IN ('pending','leased','running','retry_wait','succeeded','failed_terminal','cancelled')), "
    "attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt BETWEEN 0 AND 2), "
    "max_attempts INTEGER NOT NULL DEFAULT 2 CHECK (max_attempts = 2), "
    "lease_owner TEXT CHECK (lease_owner IS NULL OR (length(lease_owner) BETWEEN 1 AND 64 AND lease_owner GLOB '[a-z]*' AND lease_owner NOT GLOB '*[^a-z0-9_-]*')), "
    f"lease_expires_at TEXT CHECK ({_timestamp_constraint('lease_expires_at', nullable=True)}), "
    "fence INTEGER NOT NULL DEFAULT 0 CHECK (fence >= 0), "
    "cancellation_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancellation_requested IN (0,1)), "
    f"next_eligible_at TEXT CHECK ({_timestamp_constraint('next_eligible_at', nullable=True)}), "
    "terminal_outcome TEXT CHECK (terminal_outcome IS NULL OR terminal_outcome IN ('completed')), "
    "terminal_failure TEXT CHECK (terminal_failure IS NULL OR terminal_failure IN ('synthetic_retryable','synthetic_terminal','lease_expired','dependency_failed','dependency_cancelled','invalid_dependency_outcome')), "
    f"created_at TEXT NOT NULL CHECK ({_timestamp_constraint('created_at')}), "
    f"updated_at TEXT NOT NULL CHECK ({_timestamp_constraint('updated_at')}), "
    "FOREIGN KEY (run_id, stage_id) REFERENCES workflow_stages(run_id, stage_id), "
    "UNIQUE (run_id, stage_id))"
)
_JOB_ATTEMPTS_TABLE_SQL = (
    "CREATE TABLE job_attempts ("
    "job_id TEXT NOT NULL REFERENCES jobs(job_id), attempt INTEGER NOT NULL CHECK (attempt BETWEEN 1 AND 2), "
    "fence INTEGER NOT NULL CHECK (fence > 0), worker_id TEXT NOT NULL CHECK (length(worker_id) BETWEEN 1 AND 64), "
    f"started_at TEXT NOT NULL CHECK ({_timestamp_constraint('started_at')}), "
    f"finished_at TEXT CHECK ({_timestamp_constraint('finished_at', nullable=True)}), "
    "outcome TEXT CHECK (outcome IS NULL OR outcome IN ('completed')), "
    "failure TEXT CHECK (failure IS NULL OR failure IN ('synthetic_retryable','synthetic_terminal','lease_expired','dependency_failed','dependency_cancelled','invalid_dependency_outcome')), "
    "PRIMARY KEY (job_id, attempt), UNIQUE (job_id, fence))"
)
_STAGE_CHECKPOINTS_TABLE_SQL = (
    "CREATE TABLE stage_checkpoints ("
    "run_id TEXT NOT NULL, stage_id TEXT NOT NULL, source_run_id TEXT, source_stage_id TEXT, "
    "accepting_job_id TEXT REFERENCES jobs(job_id), accepting_fence INTEGER CHECK (accepting_fence IS NULL OR accepting_fence > 0), "
    "outcome TEXT NOT NULL CHECK (outcome IN ('completed')), "
    "reuse_eligible INTEGER NOT NULL CHECK (reuse_eligible IN (0,1)), "
    "current_applicable INTEGER NOT NULL CHECK (current_applicable IN (0,1)), "
    f"accepted_at TEXT NOT NULL CHECK ({_timestamp_constraint('accepted_at')}), "
    "PRIMARY KEY (run_id, stage_id), "
    "FOREIGN KEY (run_id, stage_id) REFERENCES workflow_stages(run_id, stage_id), "
    "FOREIGN KEY (source_run_id, source_stage_id) REFERENCES workflow_stages(run_id, stage_id), "
    "CHECK ((source_run_id IS NULL) = (source_stage_id IS NULL)), "
    "CHECK ((accepting_job_id IS NULL) = (accepting_fence IS NULL)), "
    "CHECK ((accepting_job_id IS NULL) = (source_run_id IS NOT NULL)))"
)
_WORKFLOW_TRANSITIONS_TABLE_SQL = (
    "CREATE TABLE workflow_transitions ("
    "transition_id TEXT PRIMARY KEY, "
    "run_id TEXT NOT NULL REFERENCES workflow_runs(run_id), "
    "sequence INTEGER NOT NULL CHECK (sequence > 0), "
    "entity_kind TEXT NOT NULL CHECK (entity_kind IN ('run','stage','job')), "
    "entity_id TEXT NOT NULL CHECK (typeof(entity_id) = 'text' AND length(entity_id) BETWEEN 1 AND 64), "
    "from_state TEXT NOT NULL CHECK (length(from_state) BETWEEN 1 AND 32), "
    "to_state TEXT NOT NULL CHECK (length(to_state) BETWEEN 1 AND 32), "
    "actor TEXT NOT NULL CHECK (actor = 'job repository'), "
    "reason TEXT NOT NULL CHECK (reason IN ('persist durable job transition','persist durable run transition','persist durable stage transition')), "
    f"occurred_at TEXT NOT NULL CHECK ({_timestamp_constraint('occurred_at')}), "
    "UNIQUE (run_id, sequence))"
)
_WORKFLOW_TRANSITIONS_NO_UPDATE_SQL = (
    "CREATE TRIGGER workflow_transitions_no_update BEFORE UPDATE ON workflow_transitions "
    "BEGIN SELECT RAISE(ABORT, 'workflow transitions are append-only'); END"
)
_WORKFLOW_TRANSITIONS_NO_DELETE_SQL = (
    "CREATE TRIGGER workflow_transitions_no_delete BEFORE DELETE ON workflow_transitions "
    "BEGIN SELECT RAISE(ABORT, 'workflow transitions are append-only'); END"
)

_INVENTORY_SNAPSHOTS_TABLE_SQL = (
    "CREATE TABLE inventory_snapshots ("
    "adapter_id TEXT NOT NULL CHECK (typeof(adapter_id) = 'text' AND length(adapter_id) BETWEEN 1 AND 64), "
    "connection_id TEXT NOT NULL CHECK (typeof(connection_id) = 'text' AND length(connection_id) BETWEEN 1 AND 64), "
    "library_id TEXT NOT NULL CHECK (typeof(library_id) = 'text' AND length(library_id) BETWEEN 1 AND 64), "
    "external_item TEXT NOT NULL CHECK (typeof(external_item) = 'text' AND length(external_item) BETWEEN 1 AND 512), "
    "snapshot_id TEXT NOT NULL CHECK (typeof(snapshot_id) = 'text' AND length(snapshot_id) BETWEEN 1 AND 64), "
    "idempotency_operation TEXT NOT NULL CHECK (idempotency_operation = 'snapshot-store'), "
    "idempotency_digest TEXT NOT NULL CHECK (length(idempotency_digest) = 64 AND idempotency_digest NOT GLOB '*[^0-9a-f]*'), "
    "fingerprint_algorithm TEXT NOT NULL CHECK (fingerprint_algorithm = 'sha256'), "
    "fingerprint_schema TEXT NOT NULL CHECK (fingerprint_schema = 'snapshot-v1'), "
    "fingerprint_digest TEXT NOT NULL CHECK (length(fingerprint_digest) = 64 AND fingerprint_digest NOT GLOB '*[^0-9a-f]*'), "
    "canonical_input TEXT NOT NULL CHECK (typeof(canonical_input) = 'text' AND length(CAST(canonical_input AS BLOB)) BETWEEN 2 AND 1048576), "
    f"observed_at TEXT NOT NULL CHECK ({_timestamp_constraint('observed_at')}), "
    "PRIMARY KEY (adapter_id, connection_id, library_id, external_item, snapshot_id), "
    "UNIQUE (adapter_id, connection_id, library_id, external_item, idempotency_operation, idempotency_digest), "
    "UNIQUE (adapter_id, connection_id, library_id, external_item, fingerprint_schema, fingerprint_digest))"
)
_CURRENT_INVENTORY_TABLE_SQL = (
    "CREATE TABLE current_inventory_observations ("
    "adapter_id TEXT NOT NULL, connection_id TEXT NOT NULL, library_id TEXT NOT NULL, "
    "external_item TEXT NOT NULL, snapshot_id TEXT NOT NULL, "
    "PRIMARY KEY (adapter_id, connection_id, library_id, external_item), "
    "FOREIGN KEY (adapter_id, connection_id, library_id, external_item, snapshot_id) "
    "REFERENCES inventory_snapshots(adapter_id, connection_id, library_id, external_item, snapshot_id))"
)
_INVENTORY_SNAPSHOTS_NO_UPDATE_SQL = (
    "CREATE TRIGGER inventory_snapshots_no_update BEFORE UPDATE ON inventory_snapshots "
    "BEGIN SELECT RAISE(ABORT, 'inventory snapshots are append-only'); END"
)
_INVENTORY_SNAPSHOTS_NO_DELETE_SQL = (
    "CREATE TRIGGER inventory_snapshots_no_delete BEFORE DELETE ON inventory_snapshots "
    "BEGIN SELECT RAISE(ABORT, 'inventory snapshots are append-only'); END"
)

_INVENTORY_V2_SNAPSHOTS_TABLE_SQL = (
    "CREATE TABLE inventory_v2_snapshots ("
    "adapter_id TEXT NOT NULL CHECK (typeof(adapter_id) = 'text' AND length(adapter_id) BETWEEN 1 AND 64), "
    "connection_id TEXT NOT NULL CHECK (typeof(connection_id) = 'text' AND length(connection_id) BETWEEN 1 AND 64), "
    "library_id TEXT NOT NULL CHECK (typeof(library_id) = 'text' AND length(library_id) BETWEEN 1 AND 64), "
    "external_item TEXT NOT NULL CHECK (typeof(external_item) = 'text' AND length(external_item) BETWEEN 1 AND 512), "
    "snapshot_id TEXT NOT NULL CHECK (typeof(snapshot_id) = 'text' AND length(snapshot_id) BETWEEN 1 AND 64), "
    "fingerprint_algorithm TEXT NOT NULL CHECK (fingerprint_algorithm = 'sha256'), "
    "fingerprint_schema TEXT NOT NULL CHECK (fingerprint_schema = 'inventory-snapshot-v2'), "
    "fingerprint_digest TEXT NOT NULL CHECK (length(fingerprint_digest) = 64 AND fingerprint_digest NOT GLOB '*[^0-9a-f]*'), "
    "canonical_observation TEXT NOT NULL CHECK (typeof(canonical_observation) = 'text' AND length(CAST(canonical_observation AS BLOB)) BETWEEN 2 AND 1048576), "
    f"observed_at TEXT NOT NULL CHECK ({_timestamp_constraint('observed_at')}), "
    "PRIMARY KEY (adapter_id, connection_id, library_id, external_item, snapshot_id), "
    "UNIQUE (adapter_id, connection_id, library_id, external_item, fingerprint_schema, fingerprint_digest))"
)
_CURRENT_INVENTORY_V2_TABLE_SQL = (
    "CREATE TABLE current_inventory_v2 ("
    "adapter_id TEXT NOT NULL, connection_id TEXT NOT NULL, library_id TEXT NOT NULL, "
    "external_item TEXT NOT NULL, snapshot_id TEXT NOT NULL, "
    "PRIMARY KEY (adapter_id, connection_id, library_id, external_item), "
    "FOREIGN KEY (adapter_id, connection_id, library_id, external_item, snapshot_id) "
    "REFERENCES inventory_v2_snapshots(adapter_id, connection_id, library_id, external_item, snapshot_id))"
)
_INVENTORY_SCANS_TABLE_SQL = (
    "CREATE TABLE inventory_scans ("
    "scan_id TEXT PRIMARY KEY CHECK (typeof(scan_id) = 'text' AND length(scan_id) BETWEEN 1 AND 64), "
    "owner_id TEXT NOT NULL CHECK (typeof(owner_id) = 'text' AND length(owner_id) BETWEEN 1 AND 64), "
    "cohort_id TEXT NOT NULL CHECK (typeof(cohort_id) = 'text' AND length(cohort_id) BETWEEN 1 AND 64), "
    "cohort_size INTEGER NOT NULL CHECK (cohort_size BETWEEN 1 AND 100000), "
    "adapter_id TEXT NOT NULL CHECK (typeof(adapter_id) = 'text' AND length(adapter_id) BETWEEN 1 AND 64), "
    "connection_id TEXT NOT NULL CHECK (typeof(connection_id) = 'text' AND length(connection_id) BETWEEN 1 AND 64), "
    "library_id TEXT NOT NULL CHECK (typeof(library_id) = 'text' AND length(library_id) BETWEEN 1 AND 64), "
    "state TEXT NOT NULL CHECK (state IN ('running','complete','incomplete','unstable')), "
    "failure_kind TEXT CHECK (failure_kind IS NULL OR failure_kind IN ('authentication','authorization','unavailable','rate_limited','timeout','cancelled','unsupported','incompatible','invalid_request','invalid_response','conflict','not_found','resource_exhausted','integrity_failure','interrupted')), "
    "base_digest TEXT NOT NULL CHECK (length(base_digest) = 64 AND base_digest NOT GLOB '*[^0-9a-f]*'), "
    "page_size INTEGER NOT NULL CHECK (page_size BETWEEN 1 AND 1000), "
    "max_pages INTEGER NOT NULL CHECK (max_pages BETWEEN 1 AND 10000), "
    "max_items INTEGER NOT NULL CHECK (max_items BETWEEN 1 AND 1000000), "
    "max_bytes INTEGER NOT NULL CHECK (max_bytes BETWEEN 1024 AND 1073741824), "
    "max_seconds INTEGER NOT NULL CHECK (max_seconds BETWEEN 1 AND 3600), "
    "stability_delay_seconds INTEGER NOT NULL CHECK (stability_delay_seconds BETWEEN 0 AND 86400), "
    "page_count INTEGER NOT NULL DEFAULT 0 CHECK (page_count >= 0), "
    "item_count INTEGER NOT NULL DEFAULT 0 CHECK (item_count >= 0), "
    "response_bytes INTEGER NOT NULL DEFAULT 0 CHECK (response_bytes >= 0), "
    f"started_at TEXT NOT NULL CHECK ({_timestamp_constraint('started_at')}), "
    f"finished_at TEXT CHECK ({_timestamp_constraint('finished_at', nullable=True)}), "
    "CHECK ((state = 'running') = (finished_at IS NULL)), "
    "CHECK ((state IN ('running','complete')) = (failure_kind IS NULL)))"
)
_INVENTORY_SCAN_ITEMS_TABLE_SQL = (
    "CREATE TABLE inventory_scan_items ("
    "scan_id TEXT NOT NULL REFERENCES inventory_scans(scan_id), "
    "adapter_id TEXT NOT NULL, connection_id TEXT NOT NULL, library_id TEXT NOT NULL, "
    "external_item TEXT NOT NULL, snapshot_id TEXT NOT NULL, "
    "PRIMARY KEY (scan_id, external_item), "
    "FOREIGN KEY (adapter_id, connection_id, library_id, external_item, snapshot_id) "
    "REFERENCES inventory_v2_snapshots(adapter_id, connection_id, library_id, external_item, snapshot_id))"
)
_INVENTORY_V2_SNAPSHOTS_NO_UPDATE_SQL = (
    "CREATE TRIGGER inventory_v2_snapshots_no_update BEFORE UPDATE ON inventory_v2_snapshots "
    "BEGIN SELECT RAISE(ABORT, 'inventory v2 snapshots are append-only'); END"
)
_INVENTORY_V2_SNAPSHOTS_NO_DELETE_SQL = (
    "CREATE TRIGGER inventory_v2_snapshots_no_delete BEFORE DELETE ON inventory_v2_snapshots "
    "BEGIN SELECT RAISE(ABORT, 'inventory v2 snapshots are append-only'); END"
)

_INVENTORY_POLLS_TABLE_SQL = (
    "CREATE TABLE inventory_polls ("
    "poll_id TEXT PRIMARY KEY CHECK (typeof(poll_id) = 'text' AND length(poll_id) BETWEEN 1 AND 64 AND poll_id GLOB '[a-z]*' AND poll_id NOT GLOB '*[^a-z0-9_-]*'), "
    "trigger_id TEXT NOT NULL CHECK (typeof(trigger_id) = 'text' AND length(trigger_id) BETWEEN 1 AND 64 AND trigger_id GLOB '[a-z]*' AND trigger_id NOT GLOB '*[^a-z0-9_-]*'), "
    "scope_digest TEXT NOT NULL CHECK (length(scope_digest) = 64 AND scope_digest NOT GLOB '*[^0-9a-f]*'), "
    "governing_digest TEXT NOT NULL CHECK (length(governing_digest) = 64 AND governing_digest NOT GLOB '*[^0-9a-f]*'), "
    "state TEXT NOT NULL CHECK (state IN ('pending','running','retry_wait','succeeded','blocked','failed_terminal','cancelled')), "
    "attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt BETWEEN 0 AND 16), "
    "max_attempts INTEGER NOT NULL CHECK (max_attempts BETWEEN 1 AND 16), "
    "max_elapsed_seconds INTEGER NOT NULL CHECK (max_elapsed_seconds BETWEEN 1 AND 86400), "
    "lease_seconds INTEGER NOT NULL CHECK (lease_seconds BETWEEN 1 AND 3600), "
    "initial_backoff_seconds INTEGER NOT NULL CHECK (initial_backoff_seconds BETWEEN 1 AND 3600), "
    "max_backoff_seconds INTEGER NOT NULL CHECK (max_backoff_seconds BETWEEN initial_backoff_seconds AND 86400), "
    "max_pages INTEGER NOT NULL CHECK (max_pages BETWEEN 1 AND 10240000), "
    "max_items INTEGER NOT NULL CHECK (max_items BETWEEN 1 AND 10240000), "
    "max_bytes INTEGER NOT NULL CHECK (max_bytes BETWEEN 1024 AND 68719476736), "
    "used_pages INTEGER NOT NULL DEFAULT 0 CHECK (used_pages BETWEEN 0 AND max_pages), "
    "used_items INTEGER NOT NULL DEFAULT 0 CHECK (used_items BETWEEN 0 AND max_items), "
    "used_bytes INTEGER NOT NULL DEFAULT 0 CHECK (used_bytes BETWEEN 0 AND max_bytes), "
    "lease_owner TEXT CHECK (lease_owner IS NULL OR (length(lease_owner) BETWEEN 1 AND 64 AND lease_owner GLOB '[a-z]*' AND lease_owner NOT GLOB '*[^a-z0-9_-]*')), "
    f"lease_expires_at TEXT CHECK ({_timestamp_constraint('lease_expires_at', nullable=True)}), "
    "fence INTEGER NOT NULL DEFAULT 0 CHECK (fence >= 0), "
    "cancellation_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancellation_requested IN (0,1)), "
    f"next_eligible_at TEXT CHECK ({_timestamp_constraint('next_eligible_at', nullable=True)}), "
    "failure_kind TEXT CHECK (failure_kind IS NULL OR (length(failure_kind) BETWEEN 1 AND 64 AND failure_kind NOT GLOB '*[^a-z0-9_]*')), "
    "compatibility TEXT CHECK (compatibility IS NULL OR compatibility IN ('supported','incompatible','unknown','contract_drift')), "
    f"created_at TEXT NOT NULL CHECK ({_timestamp_constraint('created_at')}), "
    f"elapsed_deadline TEXT NOT NULL CHECK ({_timestamp_constraint('elapsed_deadline')}), "
    f"updated_at TEXT NOT NULL CHECK ({_timestamp_constraint('updated_at')}), "
    "UNIQUE (trigger_id, governing_digest), "
    "CHECK ((state = 'running') = (lease_owner IS NOT NULL)), "
    "CHECK ((lease_owner IS NULL) = (lease_expires_at IS NULL)))"
)
_INVENTORY_POLL_ATTEMPTS_TABLE_SQL = (
    "CREATE TABLE inventory_poll_attempts ("
    "poll_id TEXT NOT NULL REFERENCES inventory_polls(poll_id), "
    "attempt INTEGER NOT NULL CHECK (attempt BETWEEN 1 AND 16), "
    "fence INTEGER NOT NULL CHECK (fence > 0), "
    "worker_id TEXT NOT NULL CHECK (length(worker_id) BETWEEN 1 AND 64), "
    "state TEXT NOT NULL CHECK (state IN ('running','retry_wait','succeeded','blocked','failed_terminal','cancelled','lease_expired')), "
    "page_count INTEGER NOT NULL DEFAULT 0 CHECK (page_count >= 0), "
    "item_count INTEGER NOT NULL DEFAULT 0 CHECK (item_count >= 0), "
    "response_bytes INTEGER NOT NULL DEFAULT 0 CHECK (response_bytes >= 0), "
    "failure_kind TEXT CHECK (failure_kind IS NULL OR (length(failure_kind) BETWEEN 1 AND 64 AND failure_kind NOT GLOB '*[^a-z0-9_]*')), "
    "compatibility TEXT CHECK (compatibility IS NULL OR compatibility IN ('supported','incompatible','unknown','contract_drift')), "
    f"started_at TEXT NOT NULL CHECK ({_timestamp_constraint('started_at')}), "
    f"finished_at TEXT CHECK ({_timestamp_constraint('finished_at', nullable=True)}), "
    "PRIMARY KEY (poll_id, attempt), UNIQUE (poll_id, fence), UNIQUE (poll_id, attempt, fence))"
)
_INVENTORY_SCAN_POLL_CLAIMS_TABLE_SQL = (
    "CREATE TABLE inventory_scan_poll_claims ("
    "scan_id TEXT PRIMARY KEY REFERENCES inventory_scans(scan_id), "
    "poll_id TEXT NOT NULL, attempt INTEGER NOT NULL, fence INTEGER NOT NULL CHECK (fence > 0), "
    "FOREIGN KEY (poll_id, attempt, fence) REFERENCES inventory_poll_attempts(poll_id, attempt, fence))"
)
_SYNTHETIC_TITLE_PROPOSAL_SETS_TABLE_SQL = (
    "CREATE TABLE synthetic_title_proposal_sets ("
    "set_id TEXT PRIMARY KEY CHECK (typeof(set_id) = 'text' AND length(set_id) BETWEEN 1 AND 64 "
    "AND set_id GLOB '[a-z]*' AND set_id NOT GLOB '*[^a-z0-9_-]*'), "
    "identity_digest TEXT NOT NULL UNIQUE CHECK (length(identity_digest) = 64 AND identity_digest NOT GLOB '*[^0-9a-f]*'), "
    "lineage TEXT NOT NULL CHECK (typeof(lineage) = 'text' AND length(lineage) BETWEEN 1 AND 64 "
    "AND lineage GLOB '[a-z]*' AND lineage NOT GLOB '*[^a-z0-9_-]*'), "
    "lineage_revision INTEGER NOT NULL CHECK (lineage_revision BETWEEN 1 AND 1000000), "
    "previous_set_id TEXT REFERENCES synthetic_title_proposal_sets(set_id), "
    "mode TEXT NOT NULL CHECK (mode = 'synthetic_preview'), "
    "canonical_set TEXT NOT NULL CHECK (typeof(canonical_set) = 'text' "
    "AND length(CAST(canonical_set AS BLOB)) BETWEEN 2 AND 131072), "
    "UNIQUE (lineage, lineage_revision), UNIQUE (lineage, set_id), "
    "CHECK (previous_set_id IS NULL OR previous_set_id != set_id))"
)
_CURRENT_SYNTHETIC_TITLE_PROPOSALS_TABLE_SQL = (
    "CREATE TABLE current_synthetic_title_proposals ("
    "lineage TEXT PRIMARY KEY, set_id TEXT NOT NULL UNIQUE, "
    "FOREIGN KEY (lineage, set_id) REFERENCES synthetic_title_proposal_sets(lineage, set_id))"
)
_SYNTHETIC_TITLE_PROPOSALS_NO_UPDATE_SQL = (
    "CREATE TRIGGER synthetic_title_proposal_sets_no_update BEFORE UPDATE ON synthetic_title_proposal_sets "
    "BEGIN SELECT RAISE(ABORT, 'synthetic title proposal history is append-only'); END"
)
_SYNTHETIC_TITLE_PROPOSALS_NO_DELETE_SQL = (
    "CREATE TRIGGER synthetic_title_proposal_sets_no_delete BEFORE DELETE ON synthetic_title_proposal_sets "
    "BEGIN SELECT RAISE(ABORT, 'synthetic title proposal history is append-only'); END"
)
_CURRENT_SYNTHETIC_TITLE_PROPOSALS_NO_DELETE_SQL = (
    "CREATE TRIGGER current_synthetic_title_proposals_no_delete BEFORE DELETE ON current_synthetic_title_proposals "
    "BEGIN SELECT RAISE(ABORT, 'synthetic title proposal current pointers cannot be deleted'); END"
)
_SYNTHETIC_TITLE_REVIEW_CASES_TABLE_SQL = (
    "CREATE TABLE synthetic_title_review_cases ("
    "case_id TEXT PRIMARY KEY CHECK (typeof(case_id) = 'text' AND length(case_id) BETWEEN 1 AND 64 "
    "AND case_id GLOB '[a-z]*' AND case_id NOT GLOB '*[^a-z0-9_-]*'), "
    "identity_digest TEXT NOT NULL UNIQUE CHECK (length(identity_digest) = 64 AND identity_digest NOT GLOB '*[^0-9a-f]*'), "
    "binding_version TEXT NOT NULL CHECK (binding_version = 'synthetic-title-review-binding-v1'), "
    "adapter_id TEXT NOT NULL CHECK (adapter_id = 'synthetic-target-service'), "
    "connection_id TEXT NOT NULL CHECK (connection_id = 'synthetic-connection'), "
    "library_id TEXT NOT NULL CHECK (library_id = 'synthetic-library'), "
    "external_item TEXT NOT NULL CHECK (typeof(external_item) = 'text' AND length(external_item) BETWEEN 1 AND 64 "
    "AND external_item GLOB '[a-z]*' AND external_item NOT GLOB '*[^a-z0-9_-]*'), "
    "target_digest TEXT NOT NULL CHECK (length(target_digest) = 64 AND target_digest NOT GLOB '*[^0-9a-f]*'), "
    "lineage TEXT NOT NULL, source_set_id TEXT NOT NULL, "
    "canonical_case TEXT NOT NULL CHECK (typeof(canonical_case) = 'text' "
    "AND length(CAST(canonical_case AS BLOB)) BETWEEN 2 AND 196608), "
    "UNIQUE (lineage, source_set_id), "
    "UNIQUE (binding_version, adapter_id, connection_id, library_id, external_item, lineage, case_id), "
    "FOREIGN KEY (lineage, source_set_id) REFERENCES synthetic_title_proposal_sets(lineage, set_id))"
)
_SYNTHETIC_TITLE_REVIEW_TRANSITIONS_TABLE_SQL = (
    "CREATE TABLE synthetic_title_review_case_transitions ("
    "transition_id TEXT PRIMARY KEY CHECK (typeof(transition_id) = 'text' AND length(transition_id) BETWEEN 1 AND 64 "
    "AND transition_id GLOB '[a-z]*' AND transition_id NOT GLOB '*[^a-z0-9_-]*'), "
    "binding_version TEXT NOT NULL CHECK (binding_version = 'synthetic-title-review-binding-v1'), "
    "adapter_id TEXT NOT NULL CHECK (adapter_id = 'synthetic-target-service'), "
    "connection_id TEXT NOT NULL CHECK (connection_id = 'synthetic-connection'), "
    "library_id TEXT NOT NULL CHECK (library_id = 'synthetic-library'), "
    "external_item TEXT NOT NULL, lineage TEXT NOT NULL, "
    "sequence INTEGER NOT NULL CHECK (sequence BETWEEN 1 AND 1000000), "
    "successor_set_id TEXT NOT NULL, prior_case_id TEXT, replacement_case_id TEXT, "
    "kind TEXT NOT NULL CHECK (kind IN ('created','superseded','retired','no_case')), "
    "actor TEXT NOT NULL CHECK (actor = 'synthetic review admission'), "
    "reason TEXT NOT NULL CHECK (reason IN ('admit current synthetic review case','supersede current synthetic review case','retire current synthetic review case','record current synthetic no-case result')), "
    f"occurred_at TEXT NOT NULL CHECK ({_timestamp_constraint('occurred_at')}), "
    "UNIQUE (binding_version, lineage, successor_set_id), "
    "UNIQUE (binding_version, adapter_id, connection_id, library_id, external_item, lineage, sequence), "
    "UNIQUE (binding_version, adapter_id, connection_id, library_id, external_item, lineage, transition_id), "
    "FOREIGN KEY (lineage, successor_set_id) REFERENCES synthetic_title_proposal_sets(lineage, set_id), "
    "FOREIGN KEY (binding_version, adapter_id, connection_id, library_id, external_item, lineage, prior_case_id) "
    "REFERENCES synthetic_title_review_cases(binding_version, adapter_id, connection_id, library_id, external_item, lineage, case_id), "
    "FOREIGN KEY (binding_version, adapter_id, connection_id, library_id, external_item, lineage, replacement_case_id) "
    "REFERENCES synthetic_title_review_cases(binding_version, adapter_id, connection_id, library_id, external_item, lineage, case_id), "
    "CHECK ((kind = 'created' AND prior_case_id IS NULL AND replacement_case_id IS NOT NULL) "
    "OR (kind = 'superseded' AND prior_case_id IS NOT NULL AND replacement_case_id IS NOT NULL AND prior_case_id != replacement_case_id) "
    "OR (kind = 'retired' AND prior_case_id IS NOT NULL AND replacement_case_id IS NULL) "
    "OR (kind = 'no_case' AND prior_case_id IS NULL AND replacement_case_id IS NULL)))"
)
_CURRENT_SYNTHETIC_TITLE_REVIEW_CASES_TABLE_SQL = (
    "CREATE TABLE current_synthetic_title_review_cases ("
    "binding_version TEXT NOT NULL CHECK (binding_version = 'synthetic-title-review-binding-v1'), "
    "adapter_id TEXT NOT NULL CHECK (adapter_id = 'synthetic-target-service'), "
    "connection_id TEXT NOT NULL CHECK (connection_id = 'synthetic-connection'), "
    "library_id TEXT NOT NULL CHECK (library_id = 'synthetic-library'), "
    "external_item TEXT NOT NULL, lineage TEXT NOT NULL, latest_set_id TEXT NOT NULL, "
    "current_case_id TEXT, sequence INTEGER NOT NULL CHECK (sequence BETWEEN 1 AND 1000000), "
    "last_transition_id TEXT NOT NULL, "
    "PRIMARY KEY (binding_version, adapter_id, connection_id, library_id, external_item, lineage), "
    "UNIQUE (binding_version, lineage), "
    "FOREIGN KEY (lineage, latest_set_id) REFERENCES synthetic_title_proposal_sets(lineage, set_id), "
    "FOREIGN KEY (binding_version, adapter_id, connection_id, library_id, external_item, lineage, current_case_id) "
    "REFERENCES synthetic_title_review_cases(binding_version, adapter_id, connection_id, library_id, external_item, lineage, case_id), "
    "FOREIGN KEY (binding_version, adapter_id, connection_id, library_id, external_item, lineage, last_transition_id) "
    "REFERENCES synthetic_title_review_case_transitions(binding_version, adapter_id, connection_id, library_id, external_item, lineage, transition_id))"
)
_SYNTHETIC_TITLE_REVIEW_CASES_NO_UPDATE_SQL = (
    "CREATE TRIGGER synthetic_title_review_cases_no_update BEFORE UPDATE ON synthetic_title_review_cases "
    "BEGIN SELECT RAISE(ABORT, 'synthetic title review case history is append-only'); END"
)
_SYNTHETIC_TITLE_REVIEW_CASES_NO_DELETE_SQL = (
    "CREATE TRIGGER synthetic_title_review_cases_no_delete BEFORE DELETE ON synthetic_title_review_cases "
    "BEGIN SELECT RAISE(ABORT, 'synthetic title review case history is append-only'); END"
)
_SYNTHETIC_TITLE_REVIEW_TRANSITIONS_NO_UPDATE_SQL = (
    "CREATE TRIGGER synthetic_title_review_case_transitions_no_update BEFORE UPDATE ON synthetic_title_review_case_transitions "
    "BEGIN SELECT RAISE(ABORT, 'synthetic title review transitions are append-only'); END"
)
_SYNTHETIC_TITLE_REVIEW_TRANSITIONS_NO_DELETE_SQL = (
    "CREATE TRIGGER synthetic_title_review_case_transitions_no_delete BEFORE DELETE ON synthetic_title_review_case_transitions "
    "BEGIN SELECT RAISE(ABORT, 'synthetic title review transitions are append-only'); END"
)
_CURRENT_SYNTHETIC_TITLE_REVIEW_CASES_NO_DELETE_SQL = (
    "CREATE TRIGGER current_synthetic_title_review_cases_no_delete BEFORE DELETE ON current_synthetic_title_review_cases "
    "BEGIN SELECT RAISE(ABORT, 'synthetic title review current state cannot be deleted'); END"
)
_INVENTORY_SNAPSHOT_FILE_BINDINGS_TABLE_SQL = (
    "CREATE TABLE inventory_snapshot_file_bindings ("
    "adapter_id TEXT NOT NULL, connection_id TEXT NOT NULL, library_id TEXT NOT NULL, "
    "external_item TEXT NOT NULL, snapshot_id TEXT NOT NULL, "
    "target_reference TEXT NOT NULL CHECK (target_reference GLOB 'target_[0-9a-f]*' AND length(target_reference) = 39), "
    "snapshot_reference TEXT NOT NULL CHECK (snapshot_reference GLOB 'snapshot_[0-9a-f]*' AND length(snapshot_reference) = 41), "
    "file_index INTEGER NOT NULL CHECK (file_index BETWEEN 0 AND 10000), "
    "file_identity TEXT NOT NULL CHECK (length(file_identity) = 64 AND file_identity NOT GLOB '*[^0-9a-f]*'), "
    "media_kind TEXT NOT NULL CHECK (media_kind = 'epub'), "
    "mount_configuration_digest TEXT NOT NULL CHECK (length(mount_configuration_digest) = 64 AND mount_configuration_digest NOT GLOB '*[^0-9a-f]*'), "
    "mount_name TEXT NOT NULL CHECK (length(mount_name) BETWEEN 1 AND 64 AND mount_name NOT GLOB '*/*'), "
    "relative_path TEXT NOT NULL CHECK (length(CAST(relative_path AS BLOB)) BETWEEN 1 AND 4096 AND substr(relative_path,1,1) != '/' AND instr(relative_path,char(0)) = 0), "
    "binding_digest TEXT NOT NULL CHECK (length(binding_digest) = 64 AND binding_digest NOT GLOB '*[^0-9a-f]*'), "
    "PRIMARY KEY (adapter_id,connection_id,library_id,external_item,snapshot_id,file_index,mount_configuration_digest), "
    "UNIQUE (binding_digest), "
    "UNIQUE (target_reference,snapshot_reference,file_index,mount_configuration_digest), "
    "FOREIGN KEY (adapter_id,connection_id,library_id,external_item,snapshot_id) REFERENCES inventory_v2_snapshots(adapter_id,connection_id,library_id,external_item,snapshot_id))"
)
_INVENTORY_SNAPSHOT_BINDING_SETS_TABLE_SQL = (
    "CREATE TABLE inventory_snapshot_binding_sets ("
    "binding_set_digest TEXT PRIMARY KEY CHECK (length(binding_set_digest)=64 AND binding_set_digest NOT GLOB '*[^0-9a-f]*'), "
    "adapter_id TEXT NOT NULL,connection_id TEXT NOT NULL,library_id TEXT NOT NULL,"
    "external_item TEXT NOT NULL,snapshot_id TEXT NOT NULL,"
    "binding_count INTEGER NOT NULL CHECK (binding_count BETWEEN 0 AND 10001),"
    "FOREIGN KEY (adapter_id,connection_id,library_id,external_item,snapshot_id) "
    "REFERENCES inventory_v2_snapshots(adapter_id,connection_id,library_id,external_item,snapshot_id))"
)
_INVENTORY_SNAPSHOT_BINDING_SET_MEMBERS_TABLE_SQL = (
    "CREATE TABLE inventory_snapshot_binding_set_members ("
    "binding_set_digest TEXT NOT NULL REFERENCES inventory_snapshot_binding_sets(binding_set_digest),"
    "binding_digest TEXT NOT NULL REFERENCES inventory_snapshot_file_bindings(binding_digest),"
    "file_index INTEGER NOT NULL CHECK (file_index BETWEEN 0 AND 10000),"
    "PRIMARY KEY (binding_set_digest,file_index),UNIQUE (binding_set_digest,binding_digest))"
)
_CURRENT_INVENTORY_SNAPSHOT_BINDING_SETS_TABLE_SQL = (
    "CREATE TABLE current_inventory_snapshot_binding_sets ("
    "adapter_id TEXT NOT NULL,connection_id TEXT NOT NULL,library_id TEXT NOT NULL,"
    "external_item TEXT NOT NULL,snapshot_id TEXT NOT NULL,"
    "binding_set_digest TEXT NOT NULL REFERENCES inventory_snapshot_binding_sets(binding_set_digest),"
    "PRIMARY KEY (adapter_id,connection_id,library_id,external_item))"
)
_INVENTORY_SNAPSHOT_LOCATOR_INDEX_TABLE_SQL = (
    "CREATE TABLE inventory_snapshot_locator_index ("
    "adapter_id TEXT NOT NULL,connection_id TEXT NOT NULL,library_id TEXT NOT NULL,"
    "external_item TEXT NOT NULL,snapshot_id TEXT NOT NULL,"
    "target_reference TEXT NOT NULL CHECK (target_reference GLOB 'target_[0-9a-f]*' AND length(target_reference)=39),"
    "snapshot_reference TEXT NOT NULL CHECK (snapshot_reference GLOB 'snapshot_[0-9a-f]*' AND length(snapshot_reference)=41),"
    "media_type TEXT NOT NULL CHECK (media_type IN ('ebook','audiobook')),"
    """lifecycle TEXT NOT NULL CHECK (lifecycle IN ('active','removed','unavailable')),"""
    "PRIMARY KEY (adapter_id,connection_id,library_id,external_item,snapshot_id),"
    "UNIQUE (target_reference,snapshot_reference),"
    "FOREIGN KEY (adapter_id,connection_id,library_id,external_item,snapshot_id) REFERENCES inventory_v2_snapshots(adapter_id,connection_id,library_id,external_item,snapshot_id))"
)
_EPUB_EVIDENCE_JOBS_TABLE_SQL = (
    "CREATE TABLE epub_evidence_jobs ("
    "work_id TEXT PRIMARY KEY CHECK (length(work_id) BETWEEN 1 AND 64), "
    "work_key TEXT NOT NULL UNIQUE CHECK (length(work_key) = 64 AND work_key NOT GLOB '*[^0-9a-f]*'), "
    "target_reference TEXT NOT NULL CHECK (target_reference GLOB 'target_[0-9a-f]*' AND length(target_reference) = 39), "
    "snapshot_reference TEXT NOT NULL CHECK (snapshot_reference GLOB 'snapshot_[0-9a-f]*' AND length(snapshot_reference) = 41), "
    "file_index INTEGER NOT NULL CHECK (file_index BETWEEN 0 AND 10000), "
    "binding_digest TEXT NOT NULL REFERENCES inventory_snapshot_file_bindings(binding_digest), "
    "source_fingerprint TEXT NOT NULL CHECK (length(source_fingerprint) = 64 AND source_fingerprint NOT GLOB '*[^0-9a-f]*'), "
    "governing_digest TEXT NOT NULL CHECK (length(governing_digest) = 64 AND governing_digest NOT GLOB '*[^0-9a-f]*'), "
    "initiating_authority TEXT NOT NULL CHECK (length(initiating_authority) BETWEEN 1 AND 64), "
    "lane_revision INTEGER NOT NULL CHECK (lane_revision BETWEEN 1 AND 1000000), "
    "state TEXT NOT NULL CHECK (state IN ('pending','running','retry_wait','succeeded','failed_terminal','cancelled')), "
    "attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt BETWEEN 0 AND 3), "
    "max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts = 3), "
    "lease_owner TEXT CHECK (lease_owner IS NULL OR length(lease_owner) BETWEEN 1 AND 64), "
    f"lease_expires_at TEXT CHECK ({_timestamp_constraint('lease_expires_at', nullable=True)}), "
    "fence INTEGER NOT NULL DEFAULT 0 CHECK (fence >= 0), "
    "cancellation_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancellation_requested IN (0,1)), "
    "terminal_outcome TEXT CHECK (terminal_outcome IS NULL OR terminal_outcome IN ('success','no_applicable_evidence','unsupported_format','unsupported_version','unsupported_compression','unsupported_multiple_renditions','encrypted','malformed','integrity_failure')), "
    "terminal_failure TEXT CHECK (terminal_failure IS NULL OR terminal_failure IN ('invalid_invocation','invalid_configuration','source_reference_unavailable','ambiguous_reference','wrong_target','stale_snapshot','file_index_out_of_range','non_epub','mount_mismatch','binding_integrity_failure','corrupt_state','source_changed','busy','cancelled','timeout','resource_limit','integrity_failure','retry_exhausted','persistence_failure','internal_failure','lease_expired')), evidence_id TEXT, "
    f"created_at TEXT NOT NULL CHECK ({_timestamp_constraint('created_at')}), "
    f"updated_at TEXT NOT NULL CHECK ({_timestamp_constraint('updated_at')}), "
    "CHECK ((state = 'running') = (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)), "
    "CHECK ((state = 'succeeded') = (evidence_id IS NOT NULL)))"
)
_EPUB_EVIDENCE_ATTEMPTS_TABLE_SQL = (
    "CREATE TABLE epub_evidence_attempts ("
    "work_id TEXT NOT NULL REFERENCES epub_evidence_jobs(work_id), "
    "attempt INTEGER NOT NULL CHECK (attempt BETWEEN 1 AND 3), "
    "fence INTEGER NOT NULL CHECK (fence > 0), "
    "worker_id TEXT NOT NULL CHECK (length(worker_id) BETWEEN 1 AND 64), "
    f"started_at TEXT NOT NULL CHECK ({_timestamp_constraint('started_at')}), "
    f"finished_at TEXT CHECK ({_timestamp_constraint('finished_at', nullable=True)}), "
    "outcome TEXT CHECK (outcome IS NULL OR outcome IN ('success','no_applicable_evidence','unsupported_format','unsupported_version','unsupported_compression','unsupported_multiple_renditions','encrypted','malformed','integrity_failure')), "
    "failure TEXT CHECK (failure IS NULL OR failure IN ('invalid_invocation','invalid_configuration','source_reference_unavailable','ambiguous_reference','wrong_target','stale_snapshot','file_index_out_of_range','non_epub','mount_mismatch','binding_integrity_failure','corrupt_state','source_changed','busy','cancelled','timeout','resource_limit','integrity_failure','persistence_failure','internal_failure','lease_expired')), PRIMARY KEY (work_id,attempt), UNIQUE (work_id,fence))"
)
_EPUB_OPENING_EVIDENCE_TABLE_SQL = (
    "CREATE TABLE epub_opening_evidence ("
    "evidence_id TEXT PRIMARY KEY CHECK (length(evidence_id) = 41 AND evidence_id GLOB 'evidence_[0-9a-f]*'), "
    "evidence_digest TEXT NOT NULL UNIQUE CHECK (length(evidence_digest) = 64 AND evidence_digest NOT GLOB '*[^0-9a-f]*'), "
    "work_key TEXT NOT NULL UNIQUE CHECK (length(work_key) = 64 AND work_key NOT GLOB '*[^0-9a-f]*'), "
    "target_reference TEXT NOT NULL, snapshot_reference TEXT NOT NULL, file_index INTEGER NOT NULL CHECK (file_index BETWEEN 0 AND 10000), "
    "binding_digest TEXT NOT NULL REFERENCES inventory_snapshot_file_bindings(binding_digest), "
    "source_fingerprint TEXT NOT NULL CHECK (length(source_fingerprint) = 64 AND source_fingerprint NOT GLOB '*[^0-9a-f]*'), "
    "governing_digest TEXT NOT NULL CHECK (length(governing_digest) = 64 AND governing_digest NOT GLOB '*[^0-9a-f]*'), "
    "outcome TEXT NOT NULL CHECK (outcome IN ('success','no_applicable_evidence','unsupported_format','unsupported_version','unsupported_compression','unsupported_multiple_renditions','encrypted','malformed','integrity_failure')), "
    "report_json TEXT NOT NULL CHECK (length(CAST(report_json AS BLOB)) BETWEEN 2 AND 524288), "
    "report_digest TEXT NOT NULL CHECK (length(report_digest) = 64 AND report_digest NOT GLOB '*[^0-9a-f]*'), "
    "claim_count INTEGER NOT NULL CHECK (claim_count >= 0), citation_count INTEGER NOT NULL CHECK (citation_count >= 0), selected_count INTEGER NOT NULL CHECK (selected_count >= 0), rejected_count INTEGER NOT NULL CHECK (rejected_count >= 0), warning_count INTEGER NOT NULL CHECK (warning_count >= 0), "
    "package_mode TEXT NOT NULL, parser_mode TEXT NOT NULL, "
    f"created_at TEXT NOT NULL CHECK ({_timestamp_constraint('created_at')}))"
)
_CURRENT_EPUB_OPENING_EVIDENCE_TABLE_SQL = (
    "CREATE TABLE current_epub_opening_evidence ("
    "target_reference TEXT NOT NULL, file_index INTEGER NOT NULL CHECK (file_index BETWEEN 0 AND 10000), "
    "evidence_id TEXT NOT NULL REFERENCES epub_opening_evidence(evidence_id), "
    "sequence INTEGER NOT NULL CHECK (sequence BETWEEN 1 AND 1000000), "
    "lane_revision INTEGER NOT NULL CHECK (lane_revision BETWEEN 1 AND 1000000), "
    f"updated_at TEXT NOT NULL CHECK ({_timestamp_constraint('updated_at')}), "
    "PRIMARY KEY (target_reference,file_index), UNIQUE (evidence_id))"
)
_EPUB_EVIDENCE_CHECKPOINTS_TABLE_SQL = (
    "CREATE TABLE epub_evidence_checkpoints ("
    "work_id TEXT PRIMARY KEY REFERENCES epub_evidence_jobs(work_id), "
    "attempt INTEGER NOT NULL, fence INTEGER NOT NULL, evidence_id TEXT NOT NULL REFERENCES epub_opening_evidence(evidence_id), "
    f"accepted_at TEXT NOT NULL CHECK ({_timestamp_constraint('accepted_at')}), "
    "FOREIGN KEY (work_id,attempt) REFERENCES epub_evidence_attempts(work_id,attempt))"
)
_EPUB_EVIDENCE_EVENTS_TABLE_SQL = (
    "CREATE TABLE epub_evidence_events ("
    "event_id TEXT PRIMARY KEY CHECK (length(event_id) BETWEEN 1 AND 64), "
    "work_id TEXT NOT NULL REFERENCES epub_evidence_jobs(work_id), "
    "kind TEXT NOT NULL CHECK (kind IN ('accepted','claimed','published','reused','failed','cancelled','retry_exhausted')), "
    "actor TEXT NOT NULL CHECK (length(actor) BETWEEN 1 AND 64), "
    "attempt INTEGER NOT NULL CHECK (attempt BETWEEN 0 AND 3), "
    "fence INTEGER NOT NULL CHECK (fence >= 0), "
    "from_state TEXT NOT NULL, to_state TEXT NOT NULL, "
    "sequence INTEGER NOT NULL CHECK (sequence BETWEEN 1 AND 1000000), "
    f"occurred_at TEXT NOT NULL CHECK ({_timestamp_constraint('occurred_at')}), "
    "UNIQUE (work_id,sequence))"
)
_INVENTORY_FILE_BINDINGS_NO_UPDATE_SQL = (
    "CREATE TRIGGER inventory_snapshot_file_bindings_no_update BEFORE UPDATE ON inventory_snapshot_file_bindings BEGIN SELECT RAISE(ABORT, 'inventory file bindings are append-only'); END"
)
_INVENTORY_SNAPSHOT_LOCATOR_NO_UPDATE_SQL = (
    "CREATE TRIGGER inventory_snapshot_locator_index_no_update BEFORE UPDATE ON inventory_snapshot_locator_index BEGIN SELECT RAISE(ABORT, 'inventory snapshot locator index is append-only'); END"
)
_INVENTORY_SNAPSHOT_LOCATOR_NO_DELETE_SQL = (
    "CREATE TRIGGER inventory_snapshot_locator_index_no_delete BEFORE DELETE ON inventory_snapshot_locator_index BEGIN SELECT RAISE(ABORT, 'inventory snapshot locator index is append-only'); END"
)
_INVENTORY_FILE_BINDINGS_NO_DELETE_SQL = (
    "CREATE TRIGGER inventory_snapshot_file_bindings_no_delete BEFORE DELETE ON inventory_snapshot_file_bindings BEGIN SELECT RAISE(ABORT, 'inventory file bindings are append-only'); END"
)
_INVENTORY_BINDING_SETS_NO_UPDATE_SQL = (
    "CREATE TRIGGER inventory_snapshot_binding_sets_no_update BEFORE UPDATE ON inventory_snapshot_binding_sets BEGIN SELECT RAISE(ABORT, 'inventory binding sets are append-only'); END"
)
_INVENTORY_BINDING_SETS_NO_DELETE_SQL = (
    "CREATE TRIGGER inventory_snapshot_binding_sets_no_delete BEFORE DELETE ON inventory_snapshot_binding_sets BEGIN SELECT RAISE(ABORT, 'inventory binding sets are append-only'); END"
)
_INVENTORY_BINDING_SET_MEMBERS_NO_UPDATE_SQL = (
    "CREATE TRIGGER inventory_snapshot_binding_set_members_no_update BEFORE UPDATE ON inventory_snapshot_binding_set_members BEGIN SELECT RAISE(ABORT, 'inventory binding set members are append-only'); END"
)
_INVENTORY_BINDING_SET_MEMBERS_NO_DELETE_SQL = (
    "CREATE TRIGGER inventory_snapshot_binding_set_members_no_delete BEFORE DELETE ON inventory_snapshot_binding_set_members BEGIN SELECT RAISE(ABORT, 'inventory binding set members are append-only'); END"
)
_EPUB_OPENING_EVIDENCE_NO_UPDATE_SQL = (
    "CREATE TRIGGER epub_opening_evidence_no_update BEFORE UPDATE ON epub_opening_evidence BEGIN SELECT RAISE(ABORT, 'epub evidence is append-only'); END"
)
_EPUB_OPENING_EVIDENCE_NO_DELETE_SQL = (
    "CREATE TRIGGER epub_opening_evidence_no_delete BEFORE DELETE ON epub_opening_evidence BEGIN SELECT RAISE(ABORT, 'epub evidence is append-only'); END"
)
_EPUB_EVIDENCE_ATTEMPTS_NO_DELETE_SQL = (
    "CREATE TRIGGER epub_evidence_attempts_no_delete BEFORE DELETE ON epub_evidence_attempts BEGIN SELECT RAISE(ABORT, 'epub evidence attempts are durable'); END"
)
_EPUB_EVIDENCE_ATTEMPTS_FINALIZE_ONLY_SQL = (
    "CREATE TRIGGER epub_evidence_attempts_finalize_only BEFORE UPDATE ON epub_evidence_attempts "
    "WHEN NOT (OLD.finished_at IS NULL AND OLD.outcome IS NULL AND OLD.failure IS NULL "
    "AND NEW.work_id=OLD.work_id AND NEW.attempt=OLD.attempt AND NEW.fence=OLD.fence "
    "AND NEW.worker_id=OLD.worker_id AND NEW.started_at=OLD.started_at "
    "AND NEW.finished_at IS NOT NULL AND NEW.failure IS NOT 'retry_exhausted' "
    "AND ((NEW.outcome IS NOT NULL AND NEW.failure IS NULL) "
    "OR (NEW.outcome IS NULL AND NEW.failure IS NOT NULL))) "
    "BEGIN SELECT RAISE(ABORT, 'epub evidence attempts are immutable after finalization'); END"
)
_EPUB_EVIDENCE_EVENTS_NO_UPDATE_SQL = (
    "CREATE TRIGGER epub_evidence_events_no_update BEFORE UPDATE ON epub_evidence_events BEGIN SELECT RAISE(ABORT, 'epub evidence events are append-only'); END"
)
_EPUB_EVIDENCE_EVENTS_NO_DELETE_SQL = (
    "CREATE TRIGGER epub_evidence_events_no_delete BEFORE DELETE ON epub_evidence_events BEGIN SELECT RAISE(ABORT, 'epub evidence events are append-only'); END"
)

_HARDCOVER_IDENTITY_GENERATIONS_TABLE_SQL = (
    "CREATE TABLE hardcover_identity_generations ("
    "work_key TEXT NOT NULL CHECK (typeof(work_key)='text' AND length(work_key)=64 AND work_key NOT GLOB '*[^0-9a-f]*'), "
    "generation INTEGER NOT NULL CHECK (typeof(generation)='integer' AND generation BETWEEN 1 AND 2147483647), "
    "state TEXT NOT NULL CHECK (state IN ('pending','claimed','retry_wait','succeeded','failed','cancelled')), "
    "fence INTEGER NOT NULL DEFAULT 0 CHECK (typeof(fence)='integer' AND fence BETWEEN 0 AND 2147483647), "
    "attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (typeof(attempt_count)='integer' AND attempt_count BETWEEN 0 AND 3), "
    "aggregate_started_at TEXT CHECK (" + _timestamp_constraint("aggregate_started_at", nullable=True) + "), "
    "aggregate_deadline TEXT CHECK (" + _timestamp_constraint("aggregate_deadline", nullable=True) + "), "
    "lease_owner TEXT CHECK (lease_owner IS NULL OR (typeof(lease_owner)='text' AND length(lease_owner) BETWEEN 1 AND 64 AND lease_owner NOT GLOB '*[^a-z0-9_-]*')), "
    "lease_expires_at TEXT CHECK (" + _timestamp_constraint("lease_expires_at", nullable=True) + "), "
    "created_at TEXT NOT NULL CHECK (" + _timestamp_constraint("created_at") + "), "
    "updated_at TEXT NOT NULL CHECK (" + _timestamp_constraint("updated_at") + "), "
    "PRIMARY KEY(work_key,generation), "
    "CHECK ((state='claimed')=(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)), "
    "CHECK ((aggregate_started_at IS NULL AND aggregate_deadline IS NULL) OR (aggregate_started_at IS NOT NULL AND aggregate_deadline IS NOT NULL AND aggregate_started_at<=aggregate_deadline)), "
    "CHECK (created_at<=updated_at))"
)
_HARDCOVER_IDENTITY_CANDIDATES_TABLE_SQL = (
    "CREATE TABLE hardcover_identity_candidates ("
    "work_key TEXT NOT NULL, generation INTEGER NOT NULL, candidate INTEGER NOT NULL CHECK (candidate BETWEEN 1 AND 3), "
    "created_at TEXT NOT NULL CHECK (" + _timestamp_constraint("created_at") + "), "
    "PRIMARY KEY(work_key,generation,candidate), FOREIGN KEY(work_key,generation) REFERENCES hardcover_identity_generations(work_key,generation))"
)
_HARDCOVER_IDENTITY_CLAIMS_TABLE_SQL = (
    "CREATE TABLE hardcover_identity_claims ("
    "work_key TEXT NOT NULL, generation INTEGER NOT NULL, fence INTEGER NOT NULL CHECK (fence BETWEEN 1 AND 2147483647), "
    "candidate INTEGER NOT NULL CHECK (candidate BETWEEN 1 AND 3), owner TEXT NOT NULL CHECK (length(owner) BETWEEN 1 AND 64 AND owner NOT GLOB '*[^a-z0-9_-]*'), "
    "claimed_at TEXT NOT NULL CHECK (" + _timestamp_constraint("claimed_at") + "), lease_expires_at TEXT NOT NULL CHECK (" + _timestamp_constraint("lease_expires_at") + "), "
    "PRIMARY KEY(work_key,generation,fence), UNIQUE(work_key,generation,fence,candidate), FOREIGN KEY(work_key,generation,candidate) REFERENCES hardcover_identity_candidates(work_key,generation,candidate))"
)
_HARDCOVER_IDENTITY_ATTEMPTS_TABLE_SQL = (
    "CREATE TABLE hardcover_identity_attempts ("
    "work_key TEXT NOT NULL, generation INTEGER NOT NULL, attempt INTEGER NOT NULL CHECK (attempt BETWEEN 1 AND 3), fence INTEGER NOT NULL, candidate INTEGER NOT NULL, "
    "started_at TEXT NOT NULL CHECK (" + _timestamp_constraint("started_at") + "), resolved_at TEXT CHECK (" + _timestamp_constraint("resolved_at", nullable=True) + "), "
    "outcome TEXT CHECK (outcome IS NULL OR outcome IN ('matched','interrupted','failed','cancelled')), "
    "PRIMARY KEY(work_key,generation,attempt), UNIQUE(work_key,generation,fence), "
    "FOREIGN KEY(work_key,generation,fence,candidate) REFERENCES hardcover_identity_claims(work_key,generation,fence,candidate), "
    "CHECK ((resolved_at IS NULL)=(outcome IS NULL) AND (resolved_at IS NULL OR started_at<=resolved_at)))"
)
_HARDCOVER_IDENTITY_RETRIES_TABLE_SQL = (
    "CREATE TABLE hardcover_identity_retry_schedules ("
    "work_key TEXT NOT NULL, generation INTEGER NOT NULL, fence INTEGER NOT NULL, candidate INTEGER NOT NULL CHECK (candidate BETWEEN 1 AND 3), "
    "due_at TEXT NOT NULL CHECK (" + _timestamp_constraint("due_at") + "), reason TEXT NOT NULL CHECK (reason IN ('interrupted','retryable')), "
    "created_at TEXT NOT NULL CHECK (" + _timestamp_constraint("created_at") + "), "
    "PRIMARY KEY(work_key,generation,fence), FOREIGN KEY(work_key,generation,candidate) REFERENCES hardcover_identity_candidates(work_key,generation,candidate))"
)
_HARDCOVER_IDENTITY_EGRESS_POLICIES_TABLE_SQL = (
    "CREATE TABLE hardcover_identity_egress_policies ("
    "work_key TEXT NOT NULL, generation INTEGER NOT NULL, "
    "max_starts INTEGER NOT NULL CHECK (typeof(max_starts)='integer' AND max_starts IN (1,3)), "
    "created_at TEXT NOT NULL CHECK (" + _timestamp_constraint("created_at") + "), "
    "PRIMARY KEY(work_key,generation), "
    "FOREIGN KEY(work_key,generation) REFERENCES hardcover_identity_generations(work_key,generation))"
)
_HARDCOVER_IDENTITY_EGRESS_POLICIES_NO_UPDATE_SQL = (
    "CREATE TRIGGER hardcover_identity_egress_policies_no_update "
    "BEFORE UPDATE ON hardcover_identity_egress_policies "
    "BEGIN SELECT RAISE(ABORT,'immutable hardcover identity record'); END"
)
_HARDCOVER_IDENTITY_EGRESS_POLICIES_NO_DELETE_SQL = (
    "CREATE TRIGGER hardcover_identity_egress_policies_no_delete "
    "BEFORE DELETE ON hardcover_identity_egress_policies "
    "BEGIN SELECT RAISE(ABORT,'immutable hardcover identity record'); END"
)
_HARDCOVER_IDENTITY_EVENTS_TABLE_SQL = (
    "CREATE TABLE hardcover_identity_events ("
    "work_key TEXT NOT NULL, generation INTEGER NOT NULL, sequence INTEGER NOT NULL CHECK (sequence BETWEEN 1 AND 2147483647), "
    "kind TEXT NOT NULL CHECK (kind IN ('accepted','claimed','started','recovered','retry_scheduled','published','failed','cancelled','expired')), "
    "from_state TEXT, to_state TEXT NOT NULL, fence INTEGER NOT NULL CHECK (fence BETWEEN 0 AND 2147483647), "
    "occurred_at TEXT NOT NULL CHECK (" + _timestamp_constraint("occurred_at") + "), "
    "PRIMARY KEY(work_key,generation,sequence), FOREIGN KEY(work_key,generation) REFERENCES hardcover_identity_generations(work_key,generation))"
)
_HARDCOVER_IDENTITY_RESULTS_TABLE_SQL = (
    "CREATE TABLE hardcover_identity_results ("
    "work_key TEXT NOT NULL, generation INTEGER NOT NULL, fence INTEGER NOT NULL, outcome TEXT NOT NULL CHECK (outcome IN ('matched','failed','cancelled')), "
    "published_at TEXT NOT NULL CHECK (" + _timestamp_constraint("published_at") + "), "
    "PRIMARY KEY(work_key,generation), FOREIGN KEY(work_key,generation) REFERENCES hardcover_identity_generations(work_key,generation))"
)
_HARDCOVER_IDENTITY_OBSERVATIONS_TABLE_SQL = (
    "CREATE TABLE hardcover_identity_observations ("
    "observation_id TEXT PRIMARY KEY CHECK (length(observation_id)=64 AND observation_id NOT GLOB '*[^0-9a-f]*'), "
    "work_key TEXT NOT NULL, generation INTEGER NOT NULL, canonical_observation TEXT NOT NULL CHECK (typeof(canonical_observation)='text' AND length(CAST(canonical_observation AS BLOB)) BETWEEN 2 AND 8192), "
    "created_at TEXT NOT NULL CHECK (" + _timestamp_constraint("created_at") + "), "
    "UNIQUE(work_key,generation), FOREIGN KEY(work_key,generation) REFERENCES hardcover_identity_results(work_key,generation))"
)
_CURRENT_HARDCOVER_IDENTITY_TABLE_SQL = (
    "CREATE TABLE current_hardcover_identity ("
    "work_key TEXT PRIMARY KEY, generation INTEGER NOT NULL, observation_id TEXT NOT NULL UNIQUE, status TEXT NOT NULL CHECK (status IN ('matched','expired')), "
    "current_since TEXT NOT NULL CHECK (" + _timestamp_constraint("current_since") + "), expires_at TEXT NOT NULL CHECK (" + _timestamp_constraint("expires_at") + "), "
    "FOREIGN KEY(work_key,generation) REFERENCES hardcover_identity_results(work_key,generation), FOREIGN KEY(observation_id) REFERENCES hardcover_identity_observations(observation_id), "
    "CHECK (current_since<=expires_at))"
)
_HARDCOVER_APPEND_ONLY_TRIGGERS = tuple(
    f"CREATE TRIGGER {table}_no_{operation} BEFORE {operation.upper()} ON {table} BEGIN SELECT RAISE(ABORT,'immutable hardcover identity record'); END"
    for table in (
        "hardcover_identity_candidates",
        "hardcover_identity_claims",
        "hardcover_identity_retry_schedules",
        "hardcover_identity_events",
        "hardcover_identity_results",
        "hardcover_identity_observations",
    )
    for operation in ("update", "delete")
)
_HARDCOVER_ATTEMPTS_FINALIZE_ONLY_SQL = (
    "CREATE TRIGGER hardcover_identity_attempts_finalize_only BEFORE UPDATE ON hardcover_identity_attempts "
    "WHEN OLD.resolved_at IS NOT NULL OR NEW.work_key!=OLD.work_key OR NEW.generation!=OLD.generation OR NEW.attempt!=OLD.attempt OR NEW.fence!=OLD.fence OR NEW.candidate!=OLD.candidate OR NEW.started_at!=OLD.started_at OR NEW.resolved_at IS NULL OR NEW.outcome IS NULL "
    "BEGIN SELECT RAISE(ABORT,'identity attempt update is not finalization'); END"
)
_HARDCOVER_ATTEMPTS_NO_DELETE_SQL = (
    "CREATE TRIGGER hardcover_identity_attempts_no_delete BEFORE DELETE ON hardcover_identity_attempts BEGIN SELECT RAISE(ABORT,'immutable hardcover identity attempt'); END"
)
MIGRATIONS = (
    Migration(
        1,
        (
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, checksum TEXT NOT NULL)",
            "CREATE TABLE instance_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
            _AUDIT_EVENTS_TABLE_SQL,
            "CREATE INDEX audit_events_by_created_at ON audit_events(created_at DESC, id DESC)",
        ),
        frozenset({"schema_migrations", "instance_metadata", "audit_events"}),
    ),
    Migration(
        2,
        (
            _WORKFLOW_RUNS_TABLE_SQL,
            _WORKFLOW_STAGES_TABLE_SQL,
            _WORKFLOW_DEPENDENCIES_TABLE_SQL,
            _JOBS_TABLE_SQL,
            _JOB_ATTEMPTS_TABLE_SQL,
            _STAGE_CHECKPOINTS_TABLE_SQL,
            _WORKFLOW_TRANSITIONS_TABLE_SQL,
            _WORKFLOW_TRANSITIONS_NO_UPDATE_SQL,
            _WORKFLOW_TRANSITIONS_NO_DELETE_SQL,
            "CREATE INDEX jobs_by_claim_eligibility ON jobs(state, next_eligible_at, created_at, job_id)",
            "CREATE INDEX jobs_by_lease_expiry ON jobs(state, lease_expires_at, job_id)",
            "CREATE INDEX jobs_by_run_stage ON jobs(run_id, stage_id)",
            "CREATE INDEX workflow_dependencies_by_prerequisite ON workflow_dependencies(run_id, prerequisite_stage_id, dependent_stage_id)",
            "CREATE INDEX workflow_runs_by_replan ON workflow_runs(replanned_from, superseded_by)",
            "CREATE INDEX workflow_transitions_by_run ON workflow_transitions(run_id, occurred_at, transition_id)",
        ),
        frozenset(
            {
                "workflow_runs",
                "workflow_stages",
                "workflow_dependencies",
                "jobs",
                "job_attempts",
                "stage_checkpoints",
                "workflow_transitions",
            }
        ),
    ),
    Migration(
        3,
        (
            _INVENTORY_SNAPSHOTS_TABLE_SQL,
            _CURRENT_INVENTORY_TABLE_SQL,
            _INVENTORY_SNAPSHOTS_NO_UPDATE_SQL,
            _INVENTORY_SNAPSHOTS_NO_DELETE_SQL,
            "CREATE INDEX inventory_snapshots_by_target ON inventory_snapshots(adapter_id, connection_id, library_id, external_item, observed_at, snapshot_id)",
        ),
        frozenset({"inventory_snapshots", "current_inventory_observations"}),
    ),
    Migration(
        4,
        (
            _INVENTORY_V2_SNAPSHOTS_TABLE_SQL,
            _CURRENT_INVENTORY_V2_TABLE_SQL,
            _INVENTORY_SCANS_TABLE_SQL,
            _INVENTORY_SCAN_ITEMS_TABLE_SQL,
            _INVENTORY_V2_SNAPSHOTS_NO_UPDATE_SQL,
            _INVENTORY_V2_SNAPSHOTS_NO_DELETE_SQL,
            "CREATE INDEX inventory_v2_snapshots_by_target ON inventory_v2_snapshots(adapter_id, connection_id, library_id, external_item, observed_at, snapshot_id)",
            "CREATE INDEX inventory_scans_by_scope ON inventory_scans(adapter_id, connection_id, library_id, started_at DESC, scan_id DESC)",
            "CREATE UNIQUE INDEX inventory_scans_one_running_scope ON inventory_scans(adapter_id, connection_id, library_id) WHERE state = 'running'",
        ),
        frozenset(
            {
                "inventory_v2_snapshots",
                "current_inventory_v2",
                "inventory_scans",
                "inventory_scan_items",
            }
        ),
    ),
    Migration(
        5,
        (
            _INVENTORY_POLLS_TABLE_SQL,
            _INVENTORY_POLL_ATTEMPTS_TABLE_SQL,
            _INVENTORY_SCAN_POLL_CLAIMS_TABLE_SQL,
            "CREATE INDEX inventory_polls_by_claim ON inventory_polls(state, next_eligible_at, created_at, poll_id)",
            "CREATE INDEX inventory_polls_by_scope ON inventory_polls(scope_digest, governing_digest, state, updated_at)",
            "CREATE INDEX inventory_poll_attempts_by_poll ON inventory_poll_attempts(poll_id, attempt, fence)",
        ),
        frozenset(
            {
                "inventory_polls",
                "inventory_poll_attempts",
                "inventory_scan_poll_claims",
            }
        ),
    ),
    Migration(
        6,
        (
            _SYNTHETIC_TITLE_PROPOSAL_SETS_TABLE_SQL,
            _CURRENT_SYNTHETIC_TITLE_PROPOSALS_TABLE_SQL,
            _SYNTHETIC_TITLE_PROPOSALS_NO_UPDATE_SQL,
            _SYNTHETIC_TITLE_PROPOSALS_NO_DELETE_SQL,
            _CURRENT_SYNTHETIC_TITLE_PROPOSALS_NO_DELETE_SQL,
            "CREATE INDEX synthetic_title_proposal_sets_by_lineage ON synthetic_title_proposal_sets(lineage, lineage_revision, set_id)",
        ),
        frozenset(
            {
                "synthetic_title_proposal_sets",
                "current_synthetic_title_proposals",
            }
        ),
    ),
    Migration(
        7,
        (
            _SYNTHETIC_TITLE_REVIEW_CASES_TABLE_SQL,
            _SYNTHETIC_TITLE_REVIEW_TRANSITIONS_TABLE_SQL,
            _CURRENT_SYNTHETIC_TITLE_REVIEW_CASES_TABLE_SQL,
            _SYNTHETIC_TITLE_REVIEW_CASES_NO_UPDATE_SQL,
            _SYNTHETIC_TITLE_REVIEW_CASES_NO_DELETE_SQL,
            _SYNTHETIC_TITLE_REVIEW_TRANSITIONS_NO_UPDATE_SQL,
            _SYNTHETIC_TITLE_REVIEW_TRANSITIONS_NO_DELETE_SQL,
            _CURRENT_SYNTHETIC_TITLE_REVIEW_CASES_NO_DELETE_SQL,
            "CREATE INDEX synthetic_title_review_cases_by_lineage ON synthetic_title_review_cases(lineage, source_set_id, case_id)",
            "CREATE INDEX synthetic_title_review_transitions_by_lineage ON synthetic_title_review_case_transitions(lineage, sequence, transition_id)",
        ),
        frozenset(
            {
                "synthetic_title_review_cases",
                "synthetic_title_review_case_transitions",
                "current_synthetic_title_review_cases",
            }
        ),
    ),
    Migration(
        8,
        (
            _INVENTORY_SNAPSHOT_LOCATOR_INDEX_TABLE_SQL,
            _INVENTORY_SNAPSHOT_FILE_BINDINGS_TABLE_SQL,
            _INVENTORY_SNAPSHOT_BINDING_SETS_TABLE_SQL,
            _INVENTORY_SNAPSHOT_BINDING_SET_MEMBERS_TABLE_SQL,
            _CURRENT_INVENTORY_SNAPSHOT_BINDING_SETS_TABLE_SQL,
            _EPUB_EVIDENCE_JOBS_TABLE_SQL,
            _EPUB_EVIDENCE_ATTEMPTS_TABLE_SQL,
            _EPUB_OPENING_EVIDENCE_TABLE_SQL,
            _CURRENT_EPUB_OPENING_EVIDENCE_TABLE_SQL,
            _EPUB_EVIDENCE_CHECKPOINTS_TABLE_SQL,
            _EPUB_EVIDENCE_EVENTS_TABLE_SQL,
            _INVENTORY_SNAPSHOT_LOCATOR_NO_UPDATE_SQL,
            _INVENTORY_SNAPSHOT_LOCATOR_NO_DELETE_SQL,
            _INVENTORY_FILE_BINDINGS_NO_UPDATE_SQL,
            _INVENTORY_FILE_BINDINGS_NO_DELETE_SQL,
            _INVENTORY_BINDING_SETS_NO_UPDATE_SQL,
            _INVENTORY_BINDING_SETS_NO_DELETE_SQL,
            _INVENTORY_BINDING_SET_MEMBERS_NO_UPDATE_SQL,
            _INVENTORY_BINDING_SET_MEMBERS_NO_DELETE_SQL,
            _EPUB_OPENING_EVIDENCE_NO_UPDATE_SQL,
            _EPUB_OPENING_EVIDENCE_NO_DELETE_SQL,
            _EPUB_EVIDENCE_ATTEMPTS_FINALIZE_ONLY_SQL,
            _EPUB_EVIDENCE_ATTEMPTS_NO_DELETE_SQL,
            _EPUB_EVIDENCE_EVENTS_NO_UPDATE_SQL,
            _EPUB_EVIDENCE_EVENTS_NO_DELETE_SQL,
            "CREATE INDEX inventory_file_bindings_by_snapshot ON inventory_snapshot_file_bindings(adapter_id,connection_id,library_id,external_item,snapshot_id,file_index)",
            "CREATE INDEX inventory_snapshot_locator_by_opaque_reference ON inventory_snapshot_locator_index(target_reference,snapshot_reference)",
            "CREATE INDEX epub_evidence_jobs_by_claim ON epub_evidence_jobs(state,lease_expires_at,updated_at,work_id)",
            "CREATE INDEX epub_opening_evidence_by_target ON epub_opening_evidence(target_reference,file_index,created_at,evidence_id)",
        ),
        frozenset(
            {
                "inventory_snapshot_file_bindings",
                "inventory_snapshot_binding_sets",
                "inventory_snapshot_binding_set_members",
                "current_inventory_snapshot_binding_sets",
                "inventory_snapshot_locator_index",
                "epub_evidence_jobs",
                "epub_evidence_attempts",
                "epub_opening_evidence",
                "current_epub_opening_evidence",
                "epub_evidence_checkpoints",
                "epub_evidence_events",
            }
        ),
    ),
    Migration(
        9,
        (
            _HARDCOVER_IDENTITY_GENERATIONS_TABLE_SQL,
            _HARDCOVER_IDENTITY_CANDIDATES_TABLE_SQL,
            _HARDCOVER_IDENTITY_CLAIMS_TABLE_SQL,
            _HARDCOVER_IDENTITY_ATTEMPTS_TABLE_SQL,
            _HARDCOVER_IDENTITY_RETRIES_TABLE_SQL,
            _HARDCOVER_IDENTITY_EVENTS_TABLE_SQL,
            _HARDCOVER_IDENTITY_RESULTS_TABLE_SQL,
            _HARDCOVER_IDENTITY_OBSERVATIONS_TABLE_SQL,
            _CURRENT_HARDCOVER_IDENTITY_TABLE_SQL,
            *_HARDCOVER_APPEND_ONLY_TRIGGERS,
            _HARDCOVER_ATTEMPTS_FINALIZE_ONLY_SQL,
            _HARDCOVER_ATTEMPTS_NO_DELETE_SQL,
            "CREATE UNIQUE INDEX hardcover_identity_one_active_generation ON hardcover_identity_generations(work_key) WHERE state IN ('pending','claimed','retry_wait')",
            "CREATE INDEX hardcover_identity_claimable ON hardcover_identity_generations(state,lease_expires_at,updated_at,work_key,generation)",
        ),
        frozenset(
            {
                "hardcover_identity_generations",
                "hardcover_identity_candidates",
                "hardcover_identity_claims",
                "hardcover_identity_attempts",
                "hardcover_identity_retry_schedules",
                "hardcover_identity_events",
                "hardcover_identity_results",
                "hardcover_identity_observations",
                "current_hardcover_identity",
            }
        ),
    ),
    Migration(
        10,
        (
            _HARDCOVER_IDENTITY_EGRESS_POLICIES_TABLE_SQL,
            "INSERT INTO hardcover_identity_egress_policies(work_key,generation,max_starts,created_at) "
            "SELECT work_key,generation,3,updated_at FROM hardcover_identity_generations "
            "WHERE attempt_count>0",
            _HARDCOVER_IDENTITY_EGRESS_POLICIES_NO_UPDATE_SQL,
            _HARDCOVER_IDENTITY_EGRESS_POLICIES_NO_DELETE_SQL,
        ),
        frozenset({"hardcover_identity_egress_policies"}),
    ),
    Migration(
        11,
        (
            "UPDATE current_hardcover_identity SET status='expired' WHERE status='matched'",
            "CREATE INDEX hardcover_identity_observations_by_work ON hardcover_identity_observations(work_key,generation)",
        ),
        frozenset(),
    ),
)


def connect(path: Path) -> sqlite3.Connection:
    """Open one application-owned connection without changing durable state."""

    connection: sqlite3.Connection | None = None
    try:
        if path == Path(":memory:"):
            raise SchemaError("durable state requires a filesystem database")
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, isolation_level=None, check_same_thread=True)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection
    except SchemaError:
        if connection is not None:
            connection.close()
        raise
    except (OSError, sqlite3.Error):
        if connection is not None:
            connection.close()
        raise SchemaError("database connection could not be opened") from None


def connect_existing(path: Path) -> sqlite3.Connection:
    """Open an existing regular database entry without creating a replacement."""

    connection: sqlite3.Connection | None = None
    try:
        status = path.lstat()
        if not stat.S_ISREG(status.st_mode):
            raise SchemaError("existing database entry is not a regular file")
        uri = f"{path.absolute().as_uri()}?mode=rw&nofollow=1"
        connection = sqlite3.connect(
            uri,
            uri=True,
            isolation_level=None,
            check_same_thread=True,
        )
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection
    except SchemaError:
        if connection is not None:
            connection.close()
        raise
    except (OSError, sqlite3.Error, ValueError):
        if connection is not None:
            connection.close()
        raise SchemaError("existing database could not be opened") from None


def _user_schema_objects(connection: sqlite3.Connection) -> set[tuple[str, str]]:
    return {
        (kind, name)
        for kind, name in connection.execute(
            "SELECT type, name FROM sqlite_master "
            "WHERE type IN ('table', 'index', 'trigger', 'view') AND name NOT LIKE 'sqlite_%'"
        )
    }


def _known_migrations() -> dict[int, Migration]:
    known = {migration.version: migration for migration in MIGRATIONS}
    if len(known) != len(MIGRATIONS) or tuple(sorted(known)) != tuple(
        range(1, len(known) + 1)
    ):
        raise SchemaError(
            "application migrations are not a contiguous ordered sequence"
        )
    return known


def _expected_tables(known: dict[int, Migration], versions: set[int]) -> set[str]:
    return (
        set().union(*(known[version].expected_tables for version in versions))
        if versions
        else set()
    )


def _expected_schema_objects(
    known: dict[int, Migration], versions: set[int]
) -> set[tuple[str, str]]:
    objects: set[tuple[str, str]] = set()
    for version in sorted(versions):
        migration = known[version]
        objects.update(("table", table) for table in migration.expected_tables)
        for statement in migration.statements:
            declaration = _schema_object_statement(statement)
            if declaration is None:
                continue
            operation, object_key = declaration
            if operation == "CREATE":
                objects.add(object_key)
            else:
                objects.discard(object_key)
    return objects


def _expected_non_table_definitions(
    known: dict[int, Migration], versions: set[int]
) -> dict[tuple[str, str], str]:
    definitions: dict[tuple[str, str], str] = {}
    for version in sorted(versions):
        for statement in known[version].statements:
            declaration = _schema_object_statement(statement)
            if declaration is None:
                continue
            operation, object_key = declaration
            if operation == "CREATE":
                definitions[object_key] = _normalise_schema_sql(statement)
            else:
                definitions.pop(object_key, None)
    return definitions


def _schema_object_statement(statement: str) -> tuple[str, tuple[str, str]] | None:
    quoted_name = r'(?:[A-Za-z_][A-Za-z0-9_]*|"(?:[^"]|"")*"|`(?:[^`]|``)*`|\[[^\]]*\])'
    match = re.match(
        rf"\s*(CREATE|DROP)\s+(?:UNIQUE\s+)?(INDEX|TRIGGER|VIEW)\s+"
        rf"(?:IF\s+(?:NOT\s+)?EXISTS\s+)?({quoted_name})(?=\s|\(|;|$)",
        statement,
        re.IGNORECASE,
    )
    if match is None:
        return None
    name = match.group(3)
    if name.startswith('"'):
        name = name[1:-1].replace('""', '"')
    elif name.startswith("`"):
        name = name[1:-1].replace("``", "`")
    elif name.startswith("["):
        name = name[1:-1]
    return match.group(1).upper(), (match.group(2).lower(), name)


def _normalise_schema_sql(sql: str) -> str:
    without_idempotency_clause = re.sub(
        r"^(\s*CREATE\s+(?:UNIQUE\s+)?(?:INDEX|TRIGGER|VIEW))\s+IF\s+NOT\s+EXISTS\s+",
        r"\1 ",
        sql,
        flags=re.IGNORECASE,
    )
    canonical: list[str] = []
    pending_space = False
    position = 0
    while position < len(without_idempotency_clause):
        character = without_idempotency_clause[position]
        if character.isspace():
            pending_space = bool(canonical)
            position += 1
            continue
        if pending_space:
            canonical.append(" ")
            pending_space = False
        if character in {"'", '"', "`", "["}:
            closing = "]" if character == "[" else character
            canonical.append(character)
            position += 1
            while position < len(without_idempotency_clause):
                quoted_character = without_idempotency_clause[position]
                canonical.append(quoted_character)
                position += 1
                if quoted_character != closing:
                    continue
                if (
                    closing != "]"
                    and position < len(without_idempotency_clause)
                    and without_idempotency_clause[position] == closing
                ):
                    canonical.append(closing)
                    position += 1
                    continue
                break
            continue
        canonical.append(character.casefold())
        position += 1
    return "".join(canonical).rstrip().removesuffix(";").rstrip()


def _expected_table_definitions(
    known: dict[int, Migration], versions: set[int]
) -> dict[str, str]:
    definitions: dict[str, str] = {}
    for version in sorted(versions):
        migration = known[version]
        declared = migration.expected_table_definitions
        if declared is not None:
            definitions.update(
                {name: _normalise_schema_sql(sql) for name, sql in declared.items()}
            )
            continue
        for statement in migration.statements:
            match = re.match(
                r"\s*CREATE\s+TABLE\s+([A-Za-z_][A-Za-z0-9_]*)\b",
                statement,
                re.IGNORECASE,
            )
            if match is not None:
                definitions[match.group(1)] = _normalise_schema_sql(statement)
    if set(definitions) != _expected_tables(known, versions):
        raise SchemaError("application migration does not declare every managed table")
    return definitions


def _contains_sql_comment(statement: str) -> bool:
    position = 0
    while position < len(statement):
        character = statement[position]
        if character in {"'", '"', "`", "["}:
            closing = "]" if character == "[" else character
            position += 1
            while position < len(statement):
                quoted_character = statement[position]
                position += 1
                if quoted_character != closing:
                    continue
                if (
                    closing != "]"
                    and position < len(statement)
                    and statement[position] == closing
                ):
                    position += 1
                    continue
                break
            continue
        if statement.startswith("--", position) or statement.startswith("/*", position):
            return True
        position += 1
    return False


def _validate_statement(statement: str) -> None:
    if _contains_sql_comment(statement):
        raise SchemaError("migration contains unsupported SQL comments")
    operation = (
        statement.lstrip().split(maxsplit=1)[0].upper() if statement.strip() else ""
    )
    if operation not in {
        "ALTER",
        "CREATE",
        "DELETE",
        "DROP",
        "INSERT",
        "UPDATE",
        "WITH",
    }:
        raise SchemaError(
            "migration contains a transaction-control or unsupported statement"
        )
    if re.match(r"\s*CREATE\s+(?:TEMP|TEMPORARY)\b", statement, re.IGNORECASE):
        raise SchemaError(
            "migration contains an unsupported temporary schema declaration"
        )
    quoted_name = r'(?:[A-Za-z_][A-Za-z0-9_]*|"(?:[^"]|"")*"|`(?:[^`]|``)*`|\[[^\]]*\])'
    qualified = re.match(
        rf"\s*(?:CREATE|DROP)\s+(?:UNIQUE\s+)?(?:INDEX|TRIGGER|VIEW)\s+"
        rf"(?:IF\s+(?:NOT\s+)?EXISTS\s+)?{quoted_name}\s*\.",
        statement,
        re.IGNORECASE,
    )
    if qualified is not None:
        raise SchemaError(
            "migration contains an unsupported schema-qualified declaration"
        )


def _validate_existing_schema(
    connection: sqlite3.Connection, known: dict[int, Migration]
) -> dict[int, str]:
    attached_schemas = {
        name
        for _, name, _ in connection.execute("PRAGMA database_list")
        if name not in {"main", "temp"}
    }
    if attached_schemas:
        raise SchemaError(
            "migration connection contains an unsupported attached schema"
        )
    temporary_objects = connection.execute(
        "SELECT 1 FROM sqlite_temp_master "
        "WHERE type IN ('table', 'index', 'trigger', 'view') "
        "AND name NOT LIKE 'sqlite_%' LIMIT 1"
    ).fetchone()
    if temporary_objects is not None:
        raise SchemaError("database schema contains an unsupported temporary object")
    objects = _user_schema_objects(connection)
    tables = {name for kind, name in objects if kind == "table"}
    known_objects = _expected_schema_objects(known, set(known))
    if "schema_migrations" not in tables:
        if objects:
            if objects & known_objects:
                raise SchemaError(
                    "database schema is partially applied; restore from a verified backup"
                )
            raise SchemaError(
                "database schema contains unrecognized objects; inspect it before retrying"
            )
        return {}

    applied = dict(
        connection.execute("SELECT version, checksum FROM schema_migrations")
    )
    versions = set(applied)
    if versions - set(known):
        raise SchemaError("database schema is newer than this application")
    if versions != set(range(1, len(versions) + 1)):
        raise SchemaError(
            "database schema is partially applied; restore from a verified backup"
        )
    if not versions and objects:
        raise SchemaError(
            "database schema is partially applied; restore from a verified backup"
        )
    for version, checksum in applied.items():
        if known[version].checksum != checksum:
            raise SchemaError("database migration checksum does not match")

    expected = _expected_schema_objects(known, versions)
    if not expected.issubset(objects):
        raise SchemaError(
            "database schema is partially applied; restore from a verified backup"
        )
    if objects - expected:
        raise SchemaError(
            "database schema contains unrecognized objects; inspect it before retrying"
        )
    expected_definitions = _expected_table_definitions(known, versions)
    actual_definitions = {
        name: _normalise_schema_sql(sql)
        for name, sql in connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'table'"
        )
        if name in expected_definitions and sql is not None
    }
    if actual_definitions != expected_definitions:
        raise SchemaError(
            "database schema definition does not match its recorded migration"
        )
    expected_non_table = _expected_non_table_definitions(known, versions)
    actual_non_table = {
        (kind, name): _normalise_schema_sql(sql)
        for kind, name, sql in connection.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE type IN ('index', 'trigger', 'view')"
        )
        if (kind, name) in expected_non_table and sql is not None
    }
    if actual_non_table != expected_non_table:
        raise SchemaError(
            "database schema object definition does not match its recorded migration"
        )
    return applied


_STATE_SUFFIXES = ("", "-journal", "-wal", "-shm")
_RECOVERY_COPY_SUFFIXES = ("", "-journal", "-wal")
_STAGED_PREFIX = ".mechanical-librarian-state-"
_PUBLICATION_SETTLE_TIMEOUT_SECONDS = 5.0
_PUBLICATION_SETTLE_POLL_SECONDS = 0.001
_BOUND_STAGED_MAX_BYTES = 64 * 1024 * 1024


def path_entry_exists(path: Path) -> bool:
    """Return whether a directory entry exists without following symlinks."""

    return os.path.lexists(path)


def _directory_identity(status: os.stat_result) -> tuple[int, int, int, int]:
    return (
        status.st_dev,
        status.st_ino,
        status.st_uid,
        stat.S_IMODE(status.st_mode),
    )


@dataclass(frozen=True)
class BoundStagedState:
    """One private staged database addressed only through a held directory."""

    parent: BoundDisposableState
    name: str
    identity: tuple[int, int]
    wal_snapshot: bool = False

    def _open(self, flags: int) -> int:
        self.parent._revalidate_directory()
        descriptor: int | None = None
        try:
            descriptor = os.open(
                self.name,
                flags | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=self.parent.directory_descriptor,
            )
            status = os.fstat(descriptor)
        except BaseException as error:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    error.add_note("prepared state descriptor cleanup failed")
            if isinstance(error, OSError):
                raise SchemaError("prepared state is unavailable") from None
            raise
        if (
            not stat.S_ISREG(status.st_mode)
            or status.st_nlink != 1
            or (status.st_dev, status.st_ino) != self.identity
            or stat.S_IMODE(status.st_mode) & 0o077
        ):
            os.close(descriptor)
            raise SchemaError("prepared state identity is inconsistent")
        return descriptor

    def read_bytes(self) -> bytes:
        descriptor = self._open(os.O_RDONLY)
        try:
            status = os.fstat(descriptor)
            if not 1 <= status.st_size <= _BOUND_STAGED_MAX_BYTES:
                raise SchemaError("prepared state size is invalid")
            chunks: list[bytes] = []
            remaining = status.st_size + 1
            while remaining:
                chunk = os.read(descriptor, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            value = b"".join(chunks)
            final_status = os.fstat(descriptor)
            if (
                len(value) != status.st_size
                or final_status.st_size != status.st_size
                or (final_status.st_dev, final_status.st_ino) != self.identity
            ):
                raise SchemaError("prepared state changed while reading")
            return value
        except OSError:
            raise SchemaError("prepared state could not be read") from None
        finally:
            os.close(descriptor)

    def write_bytes(self, value: bytes) -> None:
        if type(value) is not bytes or not 1 <= len(value) <= _BOUND_STAGED_MAX_BYTES:
            raise SchemaError("prepared state bytes are invalid")
        descriptor = self._open(os.O_WRONLY)
        try:
            os.ftruncate(descriptor, 0)
            position = 0
            while position < len(value):
                written = os.write(descriptor, value[position:])
                if written < 1:
                    raise OSError("short descriptor write")
                position += written
            os.fsync(descriptor)
        except OSError:
            raise SchemaError("prepared state could not be written") from None
        finally:
            os.close(descriptor)

    def connect(self) -> sqlite3.Connection:
        return _connect_serialized_state(None)

    def connect_existing(self) -> sqlite3.Connection:
        value = self.read_bytes()
        if self.wal_snapshot:
            if len(value) < 100 or value[:16] != b"SQLite format 3\x00":
                raise SchemaError("descriptor-bound database is malformed")
            if value[18:20] != b"\x02\x02":
                raise SchemaError(
                    "descriptor-bound database journal mode is invalid"
                )
            normalized = bytearray(value)
            normalized[18:20] = b"\x01\x01"
            value = bytes(normalized)
        return _connect_serialized_state(value)

    def persist(self, connection: sqlite3.Connection) -> None:
        if connection.in_transaction:
            raise SchemaError("prepared state still has an active transaction")
        _verify_integrity(connection)
        try:
            value = connection.serialize()
        except sqlite3.Error:
            raise SchemaError("prepared state could not be serialized") from None
        self.write_bytes(value)


def _connect_serialized_state(value: bytes | None) -> sqlite3.Connection:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            ":memory:", isolation_level=None, check_same_thread=True
        )
        if value is not None:
            connection.deserialize(value)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection
    except sqlite3.Error:
        if connection is not None:
            connection.close()
        raise SchemaError("descriptor-bound database could not be opened") from None


@dataclass(frozen=True)
class BoundDisposableState:
    """Descriptor-bound staging in one validated disposable directory."""

    target: Path
    directory_descriptor: int
    directory_identity: tuple[int, int, int, int]

    def _revalidate_directory(self) -> None:
        try:
            status = self.target.parent.lstat()
            descriptor_status = os.fstat(self.directory_descriptor)
        except OSError:
            raise SchemaError("durable work preview directory was replaced") from None
        if (
            not stat.S_ISDIR(status.st_mode)
            or not stat.S_ISDIR(descriptor_status.st_mode)
            or _directory_identity(status) != self.directory_identity
            or _directory_identity(descriptor_status) != self.directory_identity
        ):
            raise SchemaError("durable work preview directory was replaced")

    @contextmanager
    def staged_state(self) -> Iterator[BoundStagedState]:
        """Yield one staged database without changing process-global state."""

        self._revalidate_directory()
        name = f"{_STAGED_PREFIX}{secrets.token_hex(16)}.sqlite"
        descriptor: int | None = None
        identity: tuple[int, int] | None = None
        failure: BaseException | None = None
        cleanup_failed = False
        try:
            descriptor = os.open(
                name,
                os.O_RDWR
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=self.directory_descriptor,
            )
            os.fchmod(descriptor, 0o600)
            status = os.fstat(descriptor)
            identity = (status.st_dev, status.st_ino)
            os.close(descriptor)
            descriptor = None
            yield BoundStagedState(self, name, identity)
        except BaseException as error:
            failure = error
            raise
        finally:
            if descriptor is not None:
                os.close(descriptor)
            for suffix in _STATE_SUFFIXES:
                if suffix == "" and identity is not None:
                    try:
                        status = os.stat(
                            name,
                            dir_fd=self.directory_descriptor,
                            follow_symlinks=False,
                        )
                    except FileNotFoundError:
                        continue
                    except OSError:
                        cleanup_failed = True
                        continue
                    if (status.st_dev, status.st_ino) != identity:
                        continue
                try:
                    os.unlink(f"{name}{suffix}", dir_fd=self.directory_descriptor)
                except FileNotFoundError:
                    pass
                except OSError:
                    cleanup_failed = True
            if cleanup_failed:
                if failure is not None:
                    failure.add_note("private descriptor-bound staging cleanup failed")
                else:
                    raise SchemaError("private state staging could not be removed")

    def publish(self, staged: BoundStagedState) -> bool:
        """Publish by descriptor without reopening the caller-supplied parent."""

        if type(staged) is not BoundStagedState or staged.parent is not self:
            raise SchemaError("prepared state is not descriptor-bound")
        self._revalidate_directory()
        try:
            staged_status = os.stat(
                staged.name,
                dir_fd=self.directory_descriptor,
                follow_symlinks=False,
            )
        except OSError:
            raise SchemaError("prepared state is unavailable") from None
        if (
            not stat.S_ISREG(staged_status.st_mode)
            or staged_status.st_nlink != 1
            or (staged_status.st_dev, staged_status.st_ino) != staged.identity
        ):
            raise SchemaError("prepared state is not an exclusive regular file")
        if stat.S_IMODE(staged_status.st_mode) & 0o077:
            raise SchemaError("prepared state permissions are too broad")
        try:
            entries = set(os.listdir(self.directory_descriptor))
        except OSError:
            raise SchemaError("durable work preview directory is unavailable") from None
        expected_entries = {staged.name}
        if entries != expected_entries:
            if self.target.name in entries:
                return False
            raise SchemaError("prepared state still requires recovery sidecars")

        descriptor: int | None = None
        try:
            descriptor = staged._open(os.O_RDONLY)
            os.fsync(descriptor)
        except OSError:
            raise SchemaError("prepared state could not be synchronized") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

        self._revalidate_directory()
        try:
            os.link(
                staged.name,
                self.target.name,
                src_dir_fd=self.directory_descriptor,
                dst_dir_fd=self.directory_descriptor,
                follow_symlinks=False,
            )
        except FileExistsError:
            return False
        except OSError:
            raise SchemaError("prepared state could not be published") from None
        try:
            self._revalidate_directory()
        except SchemaError:
            try:
                published_status = os.stat(
                    self.target.name,
                    dir_fd=self.directory_descriptor,
                    follow_symlinks=False,
                )
                if (published_status.st_dev, published_status.st_ino) == staged.identity:
                    os.unlink(self.target.name, dir_fd=self.directory_descriptor)
            except OSError:
                pass
            raise
        published_descriptor: int | None = None
        try:
            published_descriptor = os.open(
                self.target.name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=self.directory_descriptor,
            )
            published_status = os.fstat(published_descriptor)
        except OSError:
            if published_descriptor is not None:
                os.close(published_descriptor)
            raise SchemaError("published state is unavailable") from None
        if (
            not stat.S_ISREG(published_status.st_mode)
            or published_status.st_nlink != 2
            or (published_status.st_dev, published_status.st_ino) != staged.identity
        ):
            os.close(published_descriptor)
            raise SchemaError("published state identity is inconsistent")
        final_descriptor: int | None = None
        try:
            os.fsync(published_descriptor)
            os.fsync(self.directory_descriptor)
            os.unlink(staged.name, dir_fd=self.directory_descriptor)
            os.fsync(self.directory_descriptor)
            remaining_status = os.fstat(published_descriptor)
            if (
                not stat.S_ISREG(remaining_status.st_mode)
                or remaining_status.st_nlink != 1
                or (remaining_status.st_dev, remaining_status.st_ino)
                != staged.identity
            ):
                raise SchemaError("published state identity is inconsistent")
            self._revalidate_directory()
            final_descriptor = os.open(
                self.target.name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=self.directory_descriptor,
            )
            final_status = os.fstat(final_descriptor)
            if (
                not stat.S_ISREG(final_status.st_mode)
                or final_status.st_nlink != 1
                or (final_status.st_dev, final_status.st_ino) != staged.identity
            ):
                raise SchemaError("published state identity is inconsistent")
        except OSError:
            raise SchemaError("published state could not be finalized") from None
        finally:
            if final_descriptor is not None:
                os.close(final_descriptor)
            os.close(published_descriptor)
        return True


@contextmanager
def bound_existing_state(target: Path) -> Iterator[BoundStagedState]:
    """Bind one existing regular database for descriptor-backed validation."""

    if (
        not target.is_absolute()
        or target.name in {"", ".", ".."}
        or any(part in {".", ".."} for part in target.parts)
    ):
        raise SchemaError("durable state path is invalid")
    descriptor: int | None = None
    failure: BaseException | None = None
    try:
        supplied_parent = target.parent
        canonical_parent = supplied_parent.resolve(strict=True)
        descriptor = os.open(
            canonical_parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        descriptor_status = os.fstat(descriptor)
        path_status = canonical_parent.lstat()
        directory_identity = _directory_identity(descriptor_status)
        target_status = os.stat(
            target.name,
            dir_fd=descriptor,
            follow_symlinks=False,
        )
        sidecars_present = False
        for suffix in _STATE_SUFFIXES[1:]:
            try:
                os.stat(
                    f"{target.name}{suffix}",
                    dir_fd=descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                continue
            else:
                sidecars_present = True
        target_identity = (target_status.st_dev, target_status.st_ino)
        if (
            not stat.S_ISDIR(descriptor_status.st_mode)
            or not stat.S_ISDIR(path_status.st_mode)
            or directory_identity != _directory_identity(path_status)
            or directory_identity[2] != os.geteuid()
            or directory_identity[3] & 0o077
            or not stat.S_ISREG(target_status.st_mode)
            or target_status.st_nlink != 1
            or target_status.st_uid != os.geteuid()
            or stat.S_IMODE(target_status.st_mode) & 0o077
            or sidecars_present
        ):
            raise SchemaError("durable state identity is invalid")
        if supplied_parent.resolve(strict=True) != canonical_parent:
            raise SchemaError("durable state identity is invalid")
        parent = BoundDisposableState(
            canonical_parent / target.name,
            descriptor,
            directory_identity,
        )
        bound = BoundStagedState(
            parent,
            target.name,
            target_identity,
            wal_snapshot=True,
        )
        yield bound
        if supplied_parent.resolve(strict=True) != canonical_parent:
            raise SchemaError("durable state identity changed")
        parent._revalidate_directory()
        final_status = os.stat(
            target.name,
            dir_fd=descriptor,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(final_status.st_mode)
            or final_status.st_nlink != 1
            or final_status.st_uid != os.geteuid()
            or (final_status.st_dev, final_status.st_ino) != target_identity
            or stat.S_IMODE(final_status.st_mode) & 0o077
        ):
            raise SchemaError("durable state identity changed")
        for suffix in _STATE_SUFFIXES[1:]:
            try:
                os.stat(
                    f"{target.name}{suffix}",
                    dir_fd=descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                continue
            raise SchemaError("durable state identity changed")
    except BaseException as error:
        failure = error
        if isinstance(error, SchemaError):
            raise
        if isinstance(error, OSError):
            raise SchemaError("durable state identity is unavailable") from None
        raise
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                if failure is not None:
                    failure.add_note("descriptor-bound state cleanup failed")
                else:
                    raise SchemaError(
                        "descriptor-bound state cleanup failed"
                    ) from None


@contextmanager
def bound_disposable_state(target: Path) -> Iterator[BoundDisposableState]:
    """Bind one absent target in a new, empty, private, euid-owned parent."""

    if not target.is_absolute() or target.name in {"", ".", ".."}:
        raise SchemaError("durable work preview path is invalid")
    descriptor: int | None = None
    failure: BaseException | None = None
    try:
        descriptor = os.open(
            target.parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        descriptor_status = os.fstat(descriptor)
        path_status = target.parent.lstat()
        identity = _directory_identity(descriptor_status)
        if (
            not stat.S_ISDIR(descriptor_status.st_mode)
            or not stat.S_ISDIR(path_status.st_mode)
            or identity != _directory_identity(path_status)
            or identity[2] != os.geteuid()
            or identity[3] != 0o700
            or os.listdir(descriptor)
        ):
            raise SchemaError("durable work preview path is invalid")
        try:
            os.stat(target.name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise SchemaError("durable work preview path is invalid")
        bound = BoundDisposableState(target, descriptor, identity)
        yield bound
        bound._revalidate_directory()
    except BaseException as error:
        failure = error
        if isinstance(error, SchemaError):
            raise
        if isinstance(error, OSError):
            raise SchemaError("durable work preview path is unavailable") from None
        raise
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                if failure is not None:
                    failure.add_note("durable work preview directory cleanup failed")
                else:
                    raise SchemaError(
                        "durable work preview directory cleanup failed"
                    ) from None


@contextmanager
def staged_state(target: Path) -> Iterator[Path]:
    """Yield one private same-filesystem path owned only by this invocation."""

    if target == Path(":memory:"):
        raise SchemaError("durable state requires a filesystem database")
    descriptor: int | None = None
    staged: Path | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            dir=target.parent,
            prefix=_STAGED_PREFIX,
            suffix=".sqlite",
        )
        os.fchmod(descriptor, 0o600)
        staged = Path(name)
        os.close(descriptor)
        descriptor = None
        yield staged
    except SchemaError:
        raise
    except OSError:
        raise SchemaError("private state staging could not be prepared") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if staged is not None:
            cleanup_failed = False
            for suffix in _STATE_SUFFIXES:
                try:
                    Path(f"{staged}{suffix}").unlink(missing_ok=True)
                except OSError:
                    cleanup_failed = True
            if cleanup_failed:
                raise SchemaError("private state staging could not be removed")


def _fsync_file(path: Path) -> None:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY)
        os.fsync(descriptor)
    except OSError:
        raise SchemaError("prepared state could not be synchronized") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        os.fsync(descriptor)
    except OSError:
        raise SchemaError("state directory could not be synchronized") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _wait_for_competing_publication(target: Path) -> None:
    """Wait boundedly for a complete linked target to lose its staging name."""

    deadline = time.monotonic() + _PUBLICATION_SETTLE_TIMEOUT_SECONDS
    while True:
        try:
            status = target.lstat()
        except OSError:
            raise SchemaError("competing state publication is unavailable") from None
        if not stat.S_ISREG(status.st_mode):
            raise SchemaError("competing state publication is not a regular file")
        if status.st_nlink == 1:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SchemaError("competing state publication did not settle")
        time.sleep(min(_PUBLICATION_SETTLE_POLL_SECONDS, remaining))


def publish_staged_state(staged: Path, target: Path) -> bool:
    """Atomically publish a complete staged database without replacing a winner."""

    try:
        staged_status = staged.lstat()
    except OSError:
        raise SchemaError("prepared state is unavailable") from None
    if not stat.S_ISREG(staged_status.st_mode) or staged_status.st_nlink != 1:
        raise SchemaError("prepared state is not an exclusive regular file")
    if stat.S_IMODE(staged_status.st_mode) & 0o077:
        raise SchemaError("prepared state permissions are too broad")
    if any(
        path_entry_exists(Path(f"{staged}{suffix}")) for suffix in _STATE_SUFFIXES[1:]
    ):
        raise SchemaError("prepared state still requires recovery sidecars")

    _fsync_file(staged)
    target_before = _state_snapshot(target)
    if "" in target_before:
        # The target may have appeared after this initializer's original
        # absence check but before its no-replace link attempt. Settle the
        # winner's staging unlink before the caller validates a stable copy,
        # just as the FileExistsError race path does below.
        _wait_for_competing_publication(target)
        return False
    if target_before:
        raise SchemaError("recovery state exists without a database")
    try:
        os.link(staged, target, follow_symlinks=False)
    except FileExistsError:
        # A competing initializer may have linked its complete inode but not yet
        # removed its private staging name. Wait boundedly for that final ctime
        # change so the loser's subsequent stable-copy preflight cannot race it.
        # A crashed publisher is still safe: the linked target is complete, and
        # timeout is a typed safe failure rather than authority to use a target
        # whose stable-copy signature can still change.
        _wait_for_competing_publication(target)
        return False
    except OSError:
        raise SchemaError("prepared state could not be published") from None

    # The target is complete once linked. Never remove it after this point: a
    # concurrent process may already be using the published inode.
    _fsync_directory(target.parent)
    try:
        staged.unlink()
    except OSError:
        raise SchemaError("private state staging could not be removed") from None
    _fsync_directory(target.parent)
    return True


def _state_signature(status: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        status.st_mode,
        status.st_dev,
        status.st_ino,
        status.st_size,
        status.st_mtime_ns,
        status.st_ctime_ns,
    )


def _state_snapshot(path: Path) -> dict[str, tuple[int, int, int, int, int, int]]:
    snapshot: dict[str, tuple[int, int, int, int, int, int]] = {}
    for suffix in _STATE_SUFFIXES:
        candidate = Path(f"{path}{suffix}")
        if not path_entry_exists(candidate):
            continue
        try:
            status = candidate.lstat()
        except OSError:
            raise SchemaError("existing state entry could not be inspected") from None
        if not stat.S_ISREG(status.st_mode):
            raise SchemaError("existing state entry is not a regular file")
        snapshot[suffix] = _state_signature(status)
    return snapshot


def _copy_regular_state(
    source: Path,
    destination: Path,
    expected: tuple[int, int, int, int, int, int],
) -> None:
    descriptor: int | None = None
    try:
        nofollow = getattr(os, "O_NOFOLLOW", None)
        nonblock = getattr(os, "O_NONBLOCK", None)
        if nofollow is None or nonblock is None:
            raise SchemaError("safe existing-state copying is unavailable")
        descriptor = os.open(
            source,
            os.O_RDONLY | nofollow | nonblock | getattr(os, "O_CLOEXEC", 0),
        )
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode) or _state_signature(status) != expected:
            raise SchemaError("database changed during validation")
        with os.fdopen(descriptor, "rb", closefd=False) as input_file:
            with destination.open("xb") as output_file:
                shutil.copyfileobj(input_file, output_file)
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode) or _state_signature(status) != expected:
            raise SchemaError("database changed during validation")
    except SchemaError:
        raise
    except OSError:
        raise SchemaError("existing state could not be copied safely") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _verify_integrity(connection: sqlite3.Connection) -> None:
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.Error:
        raise SchemaError("database integrity could not be verified") from None
    if integrity != [("ok",)]:
        raise SchemaError("database integrity could not be verified")


def preflight(path: Path) -> None:
    """Validate a stable copy before SQLite can recover or rewrite existing state."""

    if path == Path(":memory:"):
        raise SchemaError("durable state requires a filesystem database")
    before = _state_snapshot(path)
    if "" not in before:
        if before:
            raise SchemaError("recovery state exists without a database")
        return

    probe: sqlite3.Connection | None = None
    try:
        with tempfile.TemporaryDirectory() as directory:
            copied_path = Path(directory) / "state.sqlite"
            for suffix in _RECOVERY_COPY_SUFFIXES:
                if suffix in before:
                    _copy_regular_state(
                        Path(f"{path}{suffix}"),
                        Path(f"{copied_path}{suffix}"),
                        before[suffix],
                    )
            if before != _state_snapshot(path):
                raise SchemaError("database changed during validation")
            probe = sqlite3.connect(
                copied_path,
                isolation_level=None,
                check_same_thread=True,
            )
            try:
                applied = _validate_existing_schema(probe, _known_migrations())
                _verify_integrity(probe)
            finally:
                probe.close()
                probe = None
            if not applied:
                raise SchemaError(
                    "existing database has no recorded application schema"
                )
    except SchemaError:
        raise
    except (OSError, sqlite3.Error):
        raise SchemaError("existing database could not be validated") from None
    finally:
        if probe is not None:
            probe.close()


def _validated_migrations(connection: sqlite3.Connection) -> dict[int, Migration]:
    try:
        connection.execute("BEGIN EXCLUSIVE")
        known = _known_migrations()
        _validate_existing_schema(connection, known)
        connection.commit()
    except SchemaError:
        if connection.in_transaction:
            connection.rollback()
        raise
    except (sqlite3.Error, OSError):
        if connection.in_transaction:
            connection.rollback()
        raise SchemaError("database schema could not be validated") from None
    return known


def _enable_wal(connection: sqlite3.Connection) -> None:
    try:
        journal_mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()
    except sqlite3.Error:
        raise SchemaError("database journal mode could not be enabled") from None
    database = connection.execute("PRAGMA database_list").fetchone()
    descriptor_serialized_memory = (
        journal_mode == ("memory",)
        and database is not None
        and len(database) == 3
        and database[1:] == ("main", "")
    )
    if journal_mode != ("wal",) and not descriptor_serialized_memory:
        raise SchemaError("database journal mode could not be enabled")


def migrate_to_version(connection: sqlite3.Connection, target_version: int) -> int:
    """Apply a genuine supported schema prefix for upgrade qualification."""

    if type(target_version) is not int or not 1 <= target_version <= len(MIGRATIONS):
        raise SchemaError("migration target version is unsupported")

    known = _validated_migrations(connection)
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone() is not None:
        applied_max = connection.execute(
            "SELECT COALESCE(MAX(version),0) FROM schema_migrations"
        ).fetchone()
        if (
            applied_max is None
            or type(applied_max[0]) is not int
            or applied_max[0] > target_version
        ):
            raise SchemaError(
                "database schema is newer than the requested migration target"
            )
    _enable_wal(connection)
    try:
        connection.execute("BEGIN EXCLUSIVE")
        applied = _validate_existing_schema(connection, known)
        for migration in MIGRATIONS[:target_version]:
            if migration.version not in applied:
                for statement in migration.statements:
                    _validate_statement(statement)
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations(version, checksum) VALUES (?, ?)",
                    (migration.version, migration.checksum),
                )
                applied[migration.version] = migration.checksum
                _validate_existing_schema(connection, known)
        connection.commit()
    except SchemaError:
        if connection.in_transaction:
            connection.rollback()
        raise
    except (sqlite3.Error, OSError):
        if connection.in_transaction:
            connection.rollback()
        raise SchemaError("migration failed and was rolled back") from None
    return target_version


def migrate(connection: sqlite3.Connection) -> int:
    """Apply ordered migrations atomically, rejecting tampered or newer schemas."""

    return migrate_to_version(connection, len(MIGRATIONS))


def validate_current_schema(connection: sqlite3.Connection) -> None:
    """Validate one read-only snapshot against the complete current schema."""

    known = _validated_migrations(connection)
    applied = _validate_existing_schema(connection, known)
    if set(applied) != set(known):
        raise SchemaError("database schema requires migration")
    _verify_integrity(connection)


def finalize_staged_state(connection: sqlite3.Connection) -> None:
    """Checkpoint and verify a new database before no-clobber publication."""

    if connection.in_transaction:
        raise SchemaError("prepared state still has an active transaction")
    try:
        checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    except sqlite3.Error:
        raise SchemaError("prepared state could not be verified") from None
    if checkpoint != (0, 0, 0):
        raise SchemaError("prepared state could not be verified")
    _verify_integrity(connection)


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Provide exactly one explicit transaction; nested ownership is rejected."""

    if connection.in_transaction:
        raise RuntimeError("nested transactions are not supported")
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
