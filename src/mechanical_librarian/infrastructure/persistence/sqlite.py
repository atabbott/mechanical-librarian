"""SQLite connection, migration, and transaction foundation."""

from __future__ import annotations

import hashlib
import os
import re
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
        source = self.checksum_source if self.checksum_source is not None else "\n".join(self.statements)
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
    "canonical_input TEXT NOT NULL CHECK (typeof(canonical_input) = 'text' AND length(canonical_input) BETWEEN 2 AND 524288), "
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
    if len(known) != len(MIGRATIONS) or tuple(sorted(known)) != tuple(range(1, len(known) + 1)):
        raise SchemaError("application migrations are not a contiguous ordered sequence")
    return known


def _expected_tables(known: dict[int, Migration], versions: set[int]) -> set[str]:
    return set().union(*(known[version].expected_tables for version in versions)) if versions else set()


def _expected_schema_objects(known: dict[int, Migration], versions: set[int]) -> set[tuple[str, str]]:
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


def _expected_non_table_definitions(known: dict[int, Migration], versions: set[int]) -> dict[tuple[str, str], str]:
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
                if closing != "]" and position < len(without_idempotency_clause) and without_idempotency_clause[position] == closing:
                    canonical.append(closing)
                    position += 1
                    continue
                break
            continue
        canonical.append(character.casefold())
        position += 1
    return "".join(canonical).rstrip().removesuffix(";").rstrip()


def _expected_table_definitions(known: dict[int, Migration], versions: set[int]) -> dict[str, str]:
    definitions: dict[str, str] = {}
    for version in sorted(versions):
        migration = known[version]
        declared = migration.expected_table_definitions
        if declared is not None:
            definitions.update({name: _normalise_schema_sql(sql) for name, sql in declared.items()})
            continue
        for statement in migration.statements:
            match = re.match(r"\s*CREATE\s+TABLE\s+([A-Za-z_][A-Za-z0-9_]*)\b", statement, re.IGNORECASE)
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
    operation = statement.lstrip().split(maxsplit=1)[0].upper() if statement.strip() else ""
    if operation not in {"ALTER", "CREATE", "DELETE", "DROP", "INSERT", "UPDATE", "WITH"}:
        raise SchemaError("migration contains a transaction-control or unsupported statement")
    if re.match(r"\s*CREATE\s+(?:TEMP|TEMPORARY)\b", statement, re.IGNORECASE):
        raise SchemaError("migration contains an unsupported temporary schema declaration")
    quoted_name = r'(?:[A-Za-z_][A-Za-z0-9_]*|"(?:[^"]|"")*"|`(?:[^`]|``)*`|\[[^\]]*\])'
    qualified = re.match(
        rf"\s*(?:CREATE|DROP)\s+(?:UNIQUE\s+)?(?:INDEX|TRIGGER|VIEW)\s+"
        rf"(?:IF\s+(?:NOT\s+)?EXISTS\s+)?{quoted_name}\s*\.",
        statement,
        re.IGNORECASE,
    )
    if qualified is not None:
        raise SchemaError("migration contains an unsupported schema-qualified declaration")


def _validate_existing_schema(connection: sqlite3.Connection, known: dict[int, Migration]) -> dict[int, str]:
    attached_schemas = {
        name
        for _, name, _ in connection.execute("PRAGMA database_list")
        if name not in {"main", "temp"}
    }
    if attached_schemas:
        raise SchemaError("migration connection contains an unsupported attached schema")
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
                raise SchemaError("database schema is partially applied; restore from a verified backup")
            raise SchemaError("database schema contains unrecognized objects; inspect it before retrying")
        return {}

    applied = dict(connection.execute("SELECT version, checksum FROM schema_migrations"))
    versions = set(applied)
    if versions - set(known):
        raise SchemaError("database schema is newer than this application")
    if versions != set(range(1, len(versions) + 1)):
        raise SchemaError("database schema is partially applied; restore from a verified backup")
    if not versions and objects:
        raise SchemaError("database schema is partially applied; restore from a verified backup")
    for version, checksum in applied.items():
        if known[version].checksum != checksum:
            raise SchemaError("database migration checksum does not match")

    expected = _expected_schema_objects(known, versions)
    if not expected.issubset(objects):
        raise SchemaError("database schema is partially applied; restore from a verified backup")
    if objects - expected:
        raise SchemaError("database schema contains unrecognized objects; inspect it before retrying")
    expected_definitions = _expected_table_definitions(known, versions)
    actual_definitions = {
        name: _normalise_schema_sql(sql)
        for name, sql in connection.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table'")
        if name in expected_definitions and sql is not None
    }
    if actual_definitions != expected_definitions:
        raise SchemaError("database schema definition does not match its recorded migration")
    expected_non_table = _expected_non_table_definitions(known, versions)
    actual_non_table = {
        (kind, name): _normalise_schema_sql(sql)
        for kind, name, sql in connection.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE type IN ('index', 'trigger', 'view')"
        )
        if (kind, name) in expected_non_table and sql is not None
    }
    if actual_non_table != expected_non_table:
        raise SchemaError("database schema object definition does not match its recorded migration")
    return applied


_STATE_SUFFIXES = ("", "-journal", "-wal", "-shm")
_RECOVERY_COPY_SUFFIXES = ("", "-journal", "-wal")
_STAGED_PREFIX = ".mechanical-librarian-state-"


def path_entry_exists(path: Path) -> bool:
    """Return whether a directory entry exists without following symlinks."""

    return os.path.lexists(path)


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
    if any(path_entry_exists(Path(f"{staged}{suffix}")) for suffix in _STATE_SUFFIXES[1:]):
        raise SchemaError("prepared state still requires recovery sidecars")

    _fsync_file(staged)
    target_before = _state_snapshot(target)
    if "" in target_before:
        return False
    if target_before:
        raise SchemaError("recovery state exists without a database")
    try:
        os.link(staged, target, follow_symlinks=False)
    except FileExistsError:
        # A competing initializer may have linked its complete inode but not yet
        # removed its private staging name. Wait briefly for that final ctime
        # change so the loser's subsequent stable-copy preflight cannot race it.
        # A crashed publisher is still safe: the linked target is complete, and
        # this bounded wait never treats staging-name liveness as authority.
        for _ in range(100):
            try:
                if target.lstat().st_nlink == 1:
                    break
            except OSError:
                break
            time.sleep(0.001)
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
                raise SchemaError("existing database has no recorded application schema")
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
    if journal_mode != ("wal",):
        raise SchemaError("database journal mode could not be enabled")


def migrate(connection: sqlite3.Connection) -> int:
    """Apply ordered migrations atomically, rejecting tampered or newer schemas."""

    known = _validated_migrations(connection)
    _enable_wal(connection)
    try:
        connection.execute("BEGIN EXCLUSIVE")
        applied = _validate_existing_schema(connection, known)
        for migration in MIGRATIONS:
            if migration.version not in applied:
                for statement in migration.statements:
                    _validate_statement(statement)
                    connection.execute(statement)
                connection.execute("INSERT INTO schema_migrations(version, checksum) VALUES (?, ?)", (migration.version, migration.checksum))
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
    return max(known)


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
