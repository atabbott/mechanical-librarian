"""Closed offline adoption of reconstructible SQLite schema objects."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import struct
import sys
from pathlib import Path

from mechanical_librarian.adapters.persistence.binding import (
    _connect_bound,
    _LinuxDatabaseBinding,
    _reject_database_sidecars,
    _require_named_identity,
    _require_owner_only_database_parent,
    _validate_delete_mode_header,
)
from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.adapters.persistence.proposal_sets import (
    _validate_proposal_row,
)
from mechanical_librarian.adapters.persistence.schema import (
    _OWNER_SETTING_SECRETS_SQL,
    _OWNER_SETTINGS_SQL,
    _REVIEW_CASE_FIELD_FACTS_INSERT_SQL,
    _REVIEW_CASE_FIELD_FACTS_SQL,
    _REVIEW_CONTROL_SQL,
    _expected_schema_objects,
    _review_case_field_fact_values_sql,
    _validate_report_row,
    _validate_review_owner_decision_actions_predecessor_schema,
    _validate_schema,
    _validate_schema_contents,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.schema_adoption import (
    OWNER_SETTINGS_V1,
    REVIEW_OWNER_DECISION_ACTIONS_V1,
    SchemaAdoptionRecoveryRequired,
)
from mechanical_librarian.domain.automatic_decision import AutomaticSafetyState
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.review_case import ReviewCase

_CHUNK_BYTES = 1024 * 1024
_DERIVED_TABLES = {
    "owner_setting_secrets",
    "owner_settings",
    "review_case_field_facts",
}
_TARGET_SCHEMA_OBJECTS_SHA256 = (
    "5600d55d8f750351f13cf49251ba3f5064405cdf1af4241354e5b25d2ef8ae9b"
)
_PRE_HANDOVER_REVIEW_CONTROL_SQL = (
    "CREATE TABLE review_preparation_control ("
    "singleton INTEGER PRIMARY KEY NOT NULL CHECK(singleton=1), "
    "paused INTEGER NOT NULL CHECK(paused IN (0,1)), "
    "claim_sequence INTEGER NOT NULL CHECK(claim_sequence>=0), "
    "last_library TEXT CHECK(last_library IS NULL OR (typeof(last_library)='text' AND length(CAST(last_library AS BLOB)) BETWEEN 1 AND 64 AND last_library NOT GLOB '*[^a-z0-9_-]*' AND substr(last_library,1,1) GLOB '[a-z]')), circuit_failures INTEGER NOT NULL CHECK(circuit_failures BETWEEN 0 AND 3), "
    "circuit_open_until INTEGER NOT NULL CHECK(circuit_open_until>=0), updated_at INTEGER NOT NULL CHECK(updated_at>=0))"
)


def _validate_owner_settings_predecessor_schema(
    connection: sqlite3.Connection,
) -> tuple[bool, bool]:
    observed = connection.execute(
        "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
    ).fetchall()
    for missing_owner_settings, pre_handover_control in (
        (True, False),
        (False, True),
        (True, True),
    ):
        expected = []
        for value in _expected_schema_objects():
            if missing_owner_settings and value[1] in {
                "owner_settings",
                "owner_setting_secrets",
                "sqlite_autoindex_owner_setting_secrets_1",
            }:
                continue
            expected.append(
                (value[0], value[1], _PRE_HANDOVER_REVIEW_CONTROL_SQL)
                if pre_handover_control and value[1] == "review_preparation_control"
                else value
            )
        if observed == expected:
            _validate_schema_contents(connection)
            return missing_owner_settings, pre_handover_control
    raise MetadataReportError("database is unavailable or incompatible")


def _expected_schema_objects_digest() -> str:
    return hashlib.sha256(
        json.dumps(
            _expected_schema_objects(), ensure_ascii=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _descriptor_digest(descriptor: int) -> str:
    digest = hashlib.sha256()
    offset = 0
    while True:
        block = os.pread(descriptor, _CHUNK_BYTES, offset)
        if not block:
            return digest.hexdigest()
        digest.update(block)
        offset += len(block)


def _encode_value(value: object) -> bytes:
    if value is None:
        return b"n"
    if type(value) is int:
        return b"i" + str(value).encode("ascii")
    if type(value) is float:
        return b"f" + struct.pack(">d", value)
    if type(value) is str:
        return b"t" + value.encode("utf-8", "strict")
    if type(value) is bytes:
        return b"b" + value
    raise MetadataReportError("database is unavailable or incompatible")


def _application_table_digest(
    connection: sqlite3.Connection, *, exclude_review_control: bool = False
) -> str:
    """Digest every application value in stable physical table order."""
    digest = hashlib.sha256()
    table_names = sorted(
        value[1]
        for value in _expected_schema_objects()
        if value[0] == "table" and value[1] not in _DERIVED_TABLES
    )
    for table_name in table_names:
        if exclude_review_control and table_name == "review_preparation_control":
            continue
        encoded_name = table_name.encode("utf-8")
        digest.update(len(encoded_name).to_bytes(4, "big"))
        digest.update(encoded_name)
        quoted = '"' + table_name.replace('"', '""') + '"'
        for row in connection.execute(f"SELECT * FROM {quoted} NOT INDEXED"):
            digest.update(len(row).to_bytes(4, "big"))
            for value in row:
                encoded = _encode_value(value)
                digest.update(len(encoded).to_bytes(8, "big"))
                digest.update(encoded)
    return digest.hexdigest()


def _validate_canonical_documents(connection: sqlite3.Connection) -> None:
    """Validate every persisted JSON value and its owning domain envelope."""
    try:
        for table_name in sorted(
            value[1] for value in _expected_schema_objects() if value[0] == "table"
        ):
            columns = tuple(
                str(row[1])
                for row in connection.execute(f'PRAGMA table_info("{table_name}")')
            )
            if "document" not in columns:
                continue
            for (document,) in connection.execute(
                f'SELECT document FROM "{table_name}" NOT INDEXED'
            ):
                if (
                    type(document) is not str
                    or _canonical_json(json.loads(document)) != document
                ):
                    raise MetadataReportError("database is unavailable or incompatible")
        for row in connection.execute(
            "SELECT report_id,connection_name,library_name,item_id,file_identity,"
            "content_digest,schema_version,document_digest,document "
            "FROM metadata_reports"
        ):
            _validate_report_row(row)
        for row in connection.execute(
            "SELECT proposal_set_id,report_id,policy_version,document FROM proposal_sets"
        ):
            _validate_proposal_row(row)
        for review_case_id, proposal_set_id, report_id, document in connection.execute(
            "SELECT review_case_id,proposal_set_id,report_id,document FROM review_cases"
        ):
            case = ReviewCase.from_json(document)
            if (
                case.review_case_id,
                case.proposal_set_id,
                case.report_id,
            ) != (review_case_id, proposal_set_id, report_id):
                raise MetadataReportError("database is unavailable or incompatible")
        safety = connection.execute(
            "SELECT paused,global_suspension,category_suspensions_document "
            "FROM automatic_safety_state WHERE singleton=1"
        ).fetchone()
        if safety is None:
            raise MetadataReportError("database is unavailable or incompatible")
        categories = json.loads(safety[2])
        if type(categories) is not list:
            raise MetadataReportError("database is unavailable or incompatible")
        state = AutomaticSafetyState(
            bool(safety[0]),
            safety[1],
            tuple(tuple(value) for value in categories),
        )
        if (
            _canonical_json([list(value) for value in state.category_suspensions])
            != safety[2]
        ):
            raise MetadataReportError("database is unavailable or incompatible")
        for row in connection.execute(
            "SELECT decision_id,review_case_id,proposal_id,target_fingerprint,"
            "destination,actor,outcome,selected_value_document,idempotency_digest,"
            "request_digest,occurred_at,document FROM human_decisions"
        ):
            decision = HumanDecision.from_json(row[11])
            if (
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
            ) != row[:11]:
                raise MetadataReportError("database is unavailable or incompatible")
    except MetadataReportError:
        raise
    except (
        json.JSONDecodeError,
        sqlite3.Error,
        TypeError,
        UnicodeError,
        ValueError,
    ) as error:
        raise MetadataReportError("database is unavailable or incompatible") from error


def _require_source_current(
    database: Path,
    source_identity: tuple[int, int],
    parent_descriptor: int,
    binding: _LinuxDatabaseBinding | None,
) -> None:
    if binding is not None:
        binding.require_current()
        return
    current_parent = -1
    try:
        current_parent = os.open(
            database.parent,
            os.O_RDONLY
            | os.O_DIRECTORY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        expected_parent = os.fstat(parent_descriptor)
        observed_parent = os.fstat(current_parent)
        if (observed_parent.st_dev, observed_parent.st_ino) != (
            expected_parent.st_dev,
            expected_parent.st_ino,
        ):
            raise MetadataReportError("database pathname changed while opening")
        _require_named_identity(current_parent, database.name, source_identity)
        _reject_database_sidecars(database)
    except OSError as error:
        raise MetadataReportError("database pathname changed while opening") from error
    finally:
        if current_parent >= 0:
            os.close(current_parent)


def _copy_verified_backup(
    source_descriptor: int,
    parent_descriptor: int,
    backup_name: str,
) -> tuple[tuple[int, int], str]:
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    backup_descriptor = -1
    created_identity: tuple[int, int] | None = None
    succeeded = False
    try:
        backup_descriptor = os.open(backup_name, flags, 0o600, dir_fd=parent_descriptor)
        information = os.fstat(backup_descriptor)
        created_identity = (information.st_dev, information.st_ino)
        if (
            not stat.S_ISREG(information.st_mode)
            or information.st_uid != os.geteuid()
            or information.st_mode & 0o077
        ):
            raise MetadataReportError("schema adoption backup is unavailable")
        offset = 0
        while True:
            block = os.pread(source_descriptor, _CHUNK_BYTES, offset)
            if not block:
                break
            view = memoryview(block)
            while view:
                written = os.write(backup_descriptor, view)
                if written <= 0:
                    raise OSError("backup write made no progress")
                view = view[written:]
            offset += len(block)
        os.fsync(backup_descriptor)
        source_digest = _descriptor_digest(source_descriptor)
        if source_digest != _descriptor_digest(backup_descriptor):
            raise MetadataReportError("schema adoption backup is unavailable")
        os.fsync(parent_descriptor)
        succeeded = True
        return created_identity, source_digest
    except FileExistsError as error:
        raise MetadataReportError("schema adoption backup already exists") from error
    except MetadataReportError:
        raise
    except OSError as error:
        raise MetadataReportError("schema adoption backup is unavailable") from error
    finally:
        if backup_descriptor >= 0:
            os.close(backup_descriptor)
        if created_identity is not None:
            try:
                information = os.stat(
                    backup_name, dir_fd=parent_descriptor, follow_symlinks=False
                )
                if (information.st_dev, information.st_ino) != created_identity:
                    raise MetadataReportError("schema adoption backup identity changed")
                if not succeeded:
                    os.unlink(backup_name, dir_fd=parent_descriptor)
                    os.fsync(parent_descriptor)
            except OSError as error:
                raise MetadataReportError(
                    "schema adoption backup identity changed"
                ) from error


def _require_backup_current(
    parent_descriptor: int,
    backup_name: str,
    expected_identity: tuple[int, int],
    expected_digest: str,
) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            backup_name,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        information = os.fstat(descriptor)
        if (
            (information.st_dev, information.st_ino) != expected_identity
            or not stat.S_ISREG(information.st_mode)
            or information.st_uid != os.geteuid()
            or information.st_mode & 0o077
            or _descriptor_digest(descriptor) != expected_digest
        ):
            raise MetadataReportError("schema adoption backup identity changed")
    except OSError as error:
        raise MetadataReportError("schema adoption backup identity changed") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _close_adoption_resources(
    connection: sqlite3.Connection | None,
    parent_descriptor: int,
    binding: _LinuxDatabaseBinding | None,
    source_descriptor: int,
) -> None:
    """Attempt every cleanup and expose one directly testable cleanup contract."""
    failure: Exception | None = None
    if connection is not None:
        try:
            connection.close()
        except sqlite3.Error as error:
            failure = error
    if parent_descriptor >= 0:
        try:
            os.close(parent_descriptor)
        except OSError as error:
            failure = failure or error
    if binding is not None:
        try:
            binding.close()
        except MetadataReportError as error:
            failure = failure or error
    elif source_descriptor >= 0:
        try:
            os.close(source_descriptor)
        except OSError as error:
            failure = failure or error
    if failure is not None:
        raise MetadataReportError("schema adoption cleanup failed") from failure


def adopt_schema(database_path: Path, backup_path: Path, plan: str) -> dict[str, str]:
    """Apply one named exact-predecessor plan without changing application rows."""
    if plan not in {REVIEW_OWNER_DECISION_ACTIONS_V1, OWNER_SETTINGS_V1}:
        raise MetadataReportError("schema adoption plan is unsupported")
    # This check precedes pathname resolution and all filesystem access. The
    # literal is the reviewed plan result, never a dynamically stored target.
    if _expected_schema_objects_digest() != _TARGET_SCHEMA_OBJECTS_SHA256:
        raise MetadataReportError("schema adoption is unavailable for this build")
    database = database_path.absolute()
    backup = backup_path.absolute()
    if database.parent != backup.parent or database.name == backup.name:
        raise MetadataReportError("schema adoption backup path is invalid")

    descriptor = -1
    parent_descriptor = -1
    binding: _LinuxDatabaseBinding | None = None
    connection: sqlite3.Connection | None = None
    recovery_required = False
    missing_owner_settings = False
    pre_handover_control = False
    try:
        descriptor = os.open(
            database,
            os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        information = os.fstat(descriptor)
        source_identity = (information.st_dev, information.st_ino)
        if (
            not stat.S_ISREG(information.st_mode)
            or information.st_uid != os.geteuid()
            or information.st_mode & 0o077
        ):
            raise MetadataReportError("schema adoption database is not owner-only")
        _validate_delete_mode_header(descriptor)

        if sys.platform.startswith("linux"):
            binding = _LinuxDatabaseBinding.create(database, descriptor)
            descriptor = -1
            database_for_sqlite = binding.alias_path
            source_descriptor = binding.descriptor
            parent_descriptor = binding.directory_chain.open_current_parent()
        else:
            parent_descriptor = os.open(
                database.parent,
                os.O_RDONLY
                | os.O_DIRECTORY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            _require_owner_only_database_parent(os.fstat(parent_descriptor))
            _reject_database_sidecars(database)
            database_for_sqlite = database
            source_descriptor = descriptor

        connection = _connect_bound(
            database_for_sqlite,
            source_descriptor,
            binding.require_current if binding is not None else None,
        )
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA busy_timeout=0")
        connection.execute("BEGIN EXCLUSIVE")
        try:
            _validate_schema(connection)
        except MetadataReportError:
            if plan == OWNER_SETTINGS_V1:
                missing_owner_settings, pre_handover_control = (
                    _validate_owner_settings_predecessor_schema(connection)
                )
            else:
                _validate_review_owner_decision_actions_predecessor_schema(connection)
                pre_handover_control = True
        else:
            _validate_canonical_documents(connection)
            connection.rollback()
            _require_source_current(
                database, source_identity, parent_descriptor, binding
            )
            return {"plan": plan, "status": "already-current"}

        _validate_canonical_documents(connection)
        before = _application_table_digest(
            connection, exclude_review_control=pre_handover_control
        )
        review_control = (
            connection.execute(
                "SELECT singleton,paused,claim_sequence,last_library,circuit_failures,"
                "circuit_open_until,updated_at FROM review_preparation_control"
            ).fetchone()
            if pre_handover_control
            else None
        )
        backup_identity, backup_digest = _copy_verified_backup(
            source_descriptor, parent_descriptor, backup.name
        )
        if binding is not None:
            binding.require_current()
        if plan == OWNER_SETTINGS_V1 and missing_owner_settings:
            connection.execute(_OWNER_SETTINGS_SQL)
            connection.execute(_OWNER_SETTING_SECRETS_SQL)
        if pre_handover_control:
            connection.execute(
                "ALTER TABLE review_preparation_control RENAME TO review_preparation_control_pre_handover"
            )
            connection.execute(_REVIEW_CONTROL_SQL)
            connection.execute(
                "INSERT INTO review_preparation_control("
                "singleton,paused,claim_sequence,first_boot_acknowledged,last_library,"
                "circuit_failures,circuit_open_until,updated_at) "
                "SELECT singleton,paused,claim_sequence,0,last_library,circuit_failures,"
                "circuit_open_until,updated_at FROM review_preparation_control_pre_handover"
            )
            connection.execute("DROP TABLE review_preparation_control_pre_handover")
        if plan != OWNER_SETTINGS_V1:
            connection.execute(_OWNER_SETTINGS_SQL)
            connection.execute(_OWNER_SETTING_SECRETS_SQL)
            connection.execute("DROP TRIGGER review_cases_project_field_facts")
        if plan != OWNER_SETTINGS_V1:
            connection.execute("DROP TABLE review_case_field_facts")
        if plan != OWNER_SETTINGS_V1:
            connection.execute(_REVIEW_CASE_FIELD_FACTS_SQL)
        if plan != OWNER_SETTINGS_V1:
            connection.execute(
                "INSERT INTO review_case_field_facts("
                "review_case_id,destination,unresolved_choice,manual_bibliographic_attention,has_owner_decision_actions) "
                "SELECT rc.review_case_id,"
                + _review_case_field_fact_values_sql("rc.document")
                + " "
                "FROM review_cases rc,json_each(rc.document,'$.results') result"
            )
            connection.execute(_REVIEW_CASE_FIELD_FACTS_INSERT_SQL)
        recovery_required = True
        connection.commit()
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise SchemaAdoptionRecoveryRequired
        if binding is not None:
            binding.require_current()
        _validate_schema(connection)
        _validate_canonical_documents(connection)
        if (
            _application_table_digest(
                connection, exclude_review_control=pre_handover_control
            )
            != before
        ):
            raise SchemaAdoptionRecoveryRequired
        if (
            pre_handover_control
            and connection.execute(
                "SELECT singleton,paused,claim_sequence,last_library,circuit_failures,"
                "circuit_open_until,updated_at FROM review_preparation_control"
            ).fetchone()
            != review_control
        ):
            raise SchemaAdoptionRecoveryRequired
        _require_backup_current(
            parent_descriptor, backup.name, backup_identity, backup_digest
        )
        _require_source_current(database, source_identity, parent_descriptor, binding)
        return {"plan": plan, "status": "adopted"}
    except BaseException as error:
        if connection is not None and not recovery_required:
            try:
                connection.rollback()
            except sqlite3.Error as rollback_error:
                raise MetadataReportError(
                    "schema adoption failed safely"
                ) from rollback_error
        if recovery_required:
            raise SchemaAdoptionRecoveryRequired from error
        if isinstance(error, MetadataReportError):
            raise
        if not isinstance(error, Exception):
            raise
        raise MetadataReportError("schema adoption failed safely") from error
    finally:
        try:
            _close_adoption_resources(
                connection, parent_descriptor, binding, descriptor
            )
        except BaseException as error:
            if recovery_required:
                raise SchemaAdoptionRecoveryRequired from error
            if isinstance(error, MetadataReportError):
                raise MetadataReportError("schema adoption failed safely") from error
            raise
