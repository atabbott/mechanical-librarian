"""Closed offline adoption of #1232's persisted attention facts (ADR-0013).

`review-attention-facts-v1` accepts only the exact schema #1232 succeeds. It
adds the attention facts table, its refresh view and triggers, and three read
indexes, then derives every fact from the stored rows; it changes no other row.
The plan backs the source up beside itself, verifies the copy, and proves every
predecessor table's values unchanged and the result exact-current before it
reports success.
"""

from __future__ import annotations

import hashlib
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
    _require_owner_only_database_parent,
    _validate_delete_mode_header,
)
from mechanical_librarian.adapters.persistence.offline_backup import (
    _copy_verified_backup,
    _expected_schema_objects_digest,
    _require_backup_current,
    _require_source_current,
    _schema_objects_digest,
)
from mechanical_librarian.adapters.persistence.schema import (
    _create_attention_facts,
    _register_casefold,
    _validate_schema,
    _validate_schema_contents_without_attention_facts,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.schema_adoption import (
    REVIEW_ATTENTION_FACTS_V1,
    SchemaAdoptionRecoveryRequired,
)

_SUBJECT = "schema adoption"
# The reviewed plan's result and its exact predecessor, the schema at #1232's
# base. Both are literals: a later schema change fails the target check before
# any filesystem access, and needs its own reviewed plan or a clean rebuild.
_TARGET_SCHEMA_OBJECTS_SHA256 = (
    "70b931d7b27d807bb3761cee88ab9a6d66d50e2719edb45cd69a4d01a36e07e3"
)
_PREDECESSOR_SCHEMA_OBJECTS_SHA256 = (
    "0b5809bf9da236afc78310eee994552d6fffa8b91d6e145736238245f3ca76ca"
)


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


def _predecessor_table_digest(connection: sqlite3.Connection) -> str:
    """Digest every value of every table the predecessor already had."""
    digest = hashlib.sha256()
    for (table_name,) in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name<>'review_attention_facts' ORDER BY name"
    ).fetchall():
        encoded_name = str(table_name).encode("utf-8")
        digest.update(len(encoded_name).to_bytes(4, "big"))
        digest.update(encoded_name)
        quoted = '"' + str(table_name).replace('"', '""') + '"'
        for row in connection.execute(f"SELECT * FROM {quoted} NOT INDEXED"):
            digest.update(len(row).to_bytes(4, "big"))
            for value in row:
                encoded = _encode_value(value)
                digest.update(len(encoded).to_bytes(8, "big"))
                digest.update(encoded)
    return digest.hexdigest()


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
    """Apply the one named exact-predecessor plan without changing stored rows."""
    if plan != REVIEW_ATTENTION_FACTS_V1:
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
        # The functions every open registers, so the predecessor's content
        # checks and the derivation read exactly what ordinary startup reads.
        _register_casefold(connection)
        connection.create_function(
            "ml_cover_consideration_current", 2, lambda _document, _replay: 0
        )
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA busy_timeout=0")
        connection.execute("BEGIN EXCLUSIVE")
        observed = _schema_objects_digest(connection)
        if observed == _TARGET_SCHEMA_OBJECTS_SHA256:
            _validate_schema(connection)
            connection.rollback()
            _require_source_current(
                database, source_identity, parent_descriptor, binding, subject=_SUBJECT
            )
            return {"plan": plan, "status": "already-current"}
        if observed != _PREDECESSOR_SCHEMA_OBJECTS_SHA256:
            raise MetadataReportError("database is unavailable or incompatible")
        _validate_schema_contents_without_attention_facts(connection)
        before = _predecessor_table_digest(connection)
        backup_identity, backup_digest = _copy_verified_backup(
            source_descriptor,
            parent_descriptor,
            backup.name,
            subject=_SUBJECT,
            recovery=SchemaAdoptionRecoveryRequired,
        )
        if binding is not None:
            binding.require_current()
        _create_attention_facts(connection)
        recovery_required = True
        connection.commit()
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise SchemaAdoptionRecoveryRequired
        if binding is not None:
            binding.require_current()
        _validate_schema(connection)
        if _predecessor_table_digest(connection) != before:
            raise SchemaAdoptionRecoveryRequired
        _require_backup_current(
            parent_descriptor,
            backup.name,
            backup_digest,
            backup_identity,
            subject=_SUBJECT,
        )
        _require_source_current(
            database, source_identity, parent_descriptor, binding, subject=_SUBJECT
        )
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
            if isinstance(error, SchemaAdoptionRecoveryRequired):
                raise
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


__all__ = ["adopt_schema"]
