"""Closed offline adoption of reconstructible SQLite schema objects."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import struct
import sys
import time
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
    _REVIEW_CASE_FIELD_FACT_VALUES_SQL,
    _REVIEW_CASE_FIELD_FACTS_INSERT_SQL,
    _REVIEW_CASE_FIELD_FACTS_SQL,
    _REVIEW_WORK_AGED_CLAIM_INDEX_SQL,
    _REVIEW_WORK_DUE_PROMOTION_INDEX_SQL,
    _REVIEW_WORK_LINEAGE_HISTORY_INDEX_SQL,
    _REVIEW_WORK_RECENT_CLAIM_INDEX_SQL,
    _REVIEW_WORK_SQL,
    _SHADOW_OUTCOME_FIELD_FACT_VALUES_SQL,
    _SHADOW_OUTCOME_FIELD_FACTS_CASE_INDEX_SQL,
    _SHADOW_OUTCOME_FIELD_FACTS_INSERT_SQL,
    _SHADOW_OUTCOME_FIELD_FACTS_SQL,
    _expected_schema_objects,
    _validate_report_row,
    _validate_review_field_facts_predecessor_schema,
    _validate_schema,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.schema_adoption import (
    REVIEW_FIELD_FACTS_V1,
    SchemaAdoptionRecoveryRequired,
)
from mechanical_librarian.domain.automatic_decision import AutomaticSafetyState
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.review_case import ReviewCase

_CHUNK_BYTES = 1024 * 1024
_DERIVED_TABLES = {"review_case_field_facts", "shadow_outcome_field_facts"}


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
    connection: sqlite3.Connection, adoption_boundary: int | None = None
) -> str:
    """Digest every application value in stable physical table order."""
    digest = hashlib.sha256()
    table_names = sorted(
        value[1]
        for value in _expected_schema_objects()
        if value[0] == "table" and value[1] not in _DERIVED_TABLES
    )
    for table_name in table_names:
        encoded_name = table_name.encode("utf-8")
        digest.update(len(encoded_name).to_bytes(4, "big"))
        digest.update(encoded_name)
        quoted = '"' + table_name.replace('"', '""') + '"'
        for row in connection.execute(f"SELECT * FROM {quoted} NOT INDEXED"):
            if (
                table_name == "review_preparation_work"
                and adoption_boundary is not None
                and row[14] == "queued"
                and row[17] > adoption_boundary
            ):
                row = (*row[:14], "delayed", *row[15:])
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
    if plan != REVIEW_FIELD_FACTS_V1:
        raise MetadataReportError("schema adoption plan is unsupported")
    database = database_path.absolute()
    backup = backup_path.absolute()
    if database.parent != backup.parent or database.name == backup.name:
        raise MetadataReportError("schema adoption backup path is invalid")

    adoption_boundary = -1
    descriptor = -1
    parent_descriptor = -1
    binding: _LinuxDatabaseBinding | None = None
    connection: sqlite3.Connection | None = None
    recovery_required = False
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
            binding = _LinuxDatabaseBinding.create(
                database, descriptor, wait_for_authority=False
            )
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
        # The one frozen predecessor has a narrower CHECK on preparation work.
        # Its adoption rebuilds that derived operational row in the same
        # exclusive transaction, so SQLite must not check child references
        # during the table swap; every reference is checked immediately after.
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA busy_timeout=0")
        connection.execute("BEGIN EXCLUSIVE")
        adoption_boundary = int(time.time())
        try:
            _validate_schema(connection)
        except MetadataReportError:
            _validate_review_field_facts_predecessor_schema(connection)
        else:
            _validate_canonical_documents(connection)
            connection.rollback()
            _require_source_current(
                database, source_identity, parent_descriptor, binding
            )
            return {"plan": plan, "status": "already-current"}

        _validate_canonical_documents(connection)
        # The predecessor stored retry backoff as queued work. The current
        # claimant only considers queued rows, so preserve a still-future
        # backoff at this one explicit adoption boundary.
        before = _application_table_digest(connection, adoption_boundary)
        backup_identity, backup_digest = _copy_verified_backup(
            source_descriptor, parent_descriptor, backup.name
        )
        if binding is not None:
            binding.require_current()
        connection.execute(_REVIEW_CASE_FIELD_FACTS_SQL)
        connection.execute(
            "INSERT INTO review_case_field_facts("
            "review_case_id,destination,unresolved_choice,manual_bibliographic_attention) "
            "SELECT rc.review_case_id," + _REVIEW_CASE_FIELD_FACT_VALUES_SQL + " "
            "FROM review_cases rc,json_each(rc.document,'$.results') result"
        )
        connection.execute(_REVIEW_CASE_FIELD_FACTS_INSERT_SQL)
        connection.execute(_SHADOW_OUTCOME_FIELD_FACTS_SQL)
        connection.execute(
            "INSERT INTO shadow_outcome_field_facts("
            "outcome_set_id,review_case_id,destination,disposition,"
            "automatic_destination,policy_category) SELECT shadow.outcome_set_id,"
            "shadow.review_case_id," + _SHADOW_OUTCOME_FIELD_FACT_VALUES_SQL + " FROM "
            "shadow_decision_outcomes shadow,json_each(shadow.document,'$.outcomes') outcome"
        )
        connection.execute(_SHADOW_OUTCOME_FIELD_FACTS_CASE_INDEX_SQL)
        connection.execute(_SHADOW_OUTCOME_FIELD_FACTS_INSERT_SQL)
        connection.execute("DROP INDEX review_preparation_work_aged_claim")
        connection.execute("DROP INDEX review_preparation_work_recent_claim")
        connection.execute("DROP INDEX review_preparation_work_lineage_history")
        connection.execute(
            _REVIEW_WORK_SQL.replace(
                "review_preparation_work (", "review_preparation_work_rebuilt (", 1
            )
        )
        columns = (
            "work_id,scan_id,ordinal,connection_name,library_name,library_id,item_id,"
            "file_identity,source_revision,source_fingerprint,producer_fingerprint,"
            "catalogue_generation,catalogue_fingerprint,stage,state,attempt,recovery_attempt,"
            "retry_not_before,report_id,recovery_source_report_id,proposal_set_id,"
            "review_case_id,failure_code,failure_root,updated_at"
        )
        connection.execute(
            "INSERT INTO review_preparation_work_rebuilt("
            + columns
            + ") SELECT "
            + columns.replace(
                "stage,state,attempt",
                "stage,CASE WHEN state='queued' AND retry_not_before>? THEN 'delayed' ELSE state END,attempt",
            )
            + " FROM review_preparation_work",
            (adoption_boundary,),
        )
        connection.execute("DROP TABLE review_preparation_work")
        connection.execute(_REVIEW_WORK_SQL)
        connection.execute(
            "INSERT INTO review_preparation_work("
            + columns
            + ") SELECT "
            + columns
            + " FROM review_preparation_work_rebuilt"
        )
        connection.execute("DROP TABLE review_preparation_work_rebuilt")
        connection.execute(_REVIEW_WORK_AGED_CLAIM_INDEX_SQL)
        connection.execute(_REVIEW_WORK_DUE_PROMOTION_INDEX_SQL)
        connection.execute(_REVIEW_WORK_RECENT_CLAIM_INDEX_SQL)
        connection.execute(_REVIEW_WORK_LINEAGE_HISTORY_INDEX_SQL)
        recovery_required = True
        connection.commit()
        connection.execute("PRAGMA foreign_keys=ON")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise SchemaAdoptionRecoveryRequired
        if binding is not None:
            binding.require_current()
        _validate_schema(connection)
        _validate_canonical_documents(connection)
        if _application_table_digest(connection) != before:
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
