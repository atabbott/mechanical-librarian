"""Exact logical privacy boundary for private Hardcover SQLite state."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3

from mechanical_librarian.domain.hardcover_identity_work import (
    IdentityMatchOutcome,
    IdentityWorkObservation,
)
from mechanical_librarian.infrastructure.hardcover_identity_gate import (
    CommittedSQLiteBindingRecovery,
)
from tests.support.hardcover_c1_privacy import PrivacyEvidenceError


_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RECOVERY_KEY = "hardcover-identity-binding-recovery-v1"
_OBSERVATION_DOMAIN = (
    b"mechanical-librarian:hardcover-matched-observation:v3\x00"
)
_WORK_KEY_TABLES = {
    "hardcover_identity_generations",
    "hardcover_identity_candidates",
    "hardcover_identity_claims",
    "hardcover_identity_attempts",
    "hardcover_identity_retry_schedules",
    "hardcover_identity_egress_policies",
    "hardcover_identity_events",
    "hardcover_identity_results",
    "hardcover_identity_observations",
    "current_hardcover_identity",
}


def logical_private_persistence_records(
    connection: sqlite3.Connection,
    *,
    recovery_observation: CommittedSQLiteBindingRecovery | None = None,
) -> list[str]:
    """Render every non-exempt cell after validating each exact private row."""

    observations = _validated_observations(connection)
    records: list[str] = []
    for table in _tables(connection):
        columns = _columns(connection, table)
        for row in connection.execute(f'SELECT * FROM "{table}"'):
            values = dict(zip(columns, row, strict=True))
            recovery_row_is_valid = _valid_recovery_row(
                connection,
                table,
                values,
                recovery_observation,
            )
            for column, value in values.items():
                if _is_allowed_private_cell(
                    table,
                    column,
                    value,
                    values,
                    observations,
                    recovery_row_is_valid,
                ):
                    continue
                records.append(_logical_record(table, column, value))
    return records


def _validated_observations(
    connection: sqlite3.Connection,
) -> dict[str, tuple[str, int, str]]:
    if "hardcover_identity_observations" not in _tables(connection):
        return {}
    result: dict[str, tuple[str, int, str]] = {}
    rows = connection.execute(
        "SELECT observation_id,work_key,generation,canonical_observation "
        "FROM hardcover_identity_observations"
    ).fetchall()
    for observation_id, work_key, generation, canonical in rows:
        if (
            type(observation_id) is not str
            or not _HEX_64.fullmatch(observation_id)
            or type(work_key) is not str
            or not _HEX_64.fullmatch(work_key)
            or type(generation) is not int
            or not 1 <= generation <= 2_147_483_647
            or type(canonical) is not str
        ):
            raise PrivacyEvidenceError("private matched observation is malformed")
        try:
            observation = IdentityWorkObservation.from_canonical_json(canonical)
        except (UnicodeError, ValueError):
            raise PrivacyEvidenceError(
                "private matched observation is malformed"
            ) from None
        if (
            observation.outcome is not IdentityMatchOutcome.MATCHED
            or not observation.reusable
        ):
            raise PrivacyEvidenceError("private matched observation is malformed")
        try:
            canonical_bytes = canonical.encode("ascii", "strict")
        except UnicodeEncodeError:
            raise PrivacyEvidenceError(
                "private matched observation is not canonical"
            ) from None
        digest = hashlib.sha256()
        for part in (
            _OBSERVATION_DOMAIN,
            work_key.encode("ascii"),
            b"\x00",
            str(generation).encode("ascii"),
            b"\x00",
            canonical_bytes,
        ):
            digest.update(part)
        if digest.hexdigest() != observation_id:
            raise PrivacyEvidenceError(
                "private matched observation content binding is invalid"
            )
        result[observation_id] = (work_key, generation, canonical)
    return result


def _is_allowed_private_cell(
    table: str,
    column: str,
    value: object,
    row: dict[str, object],
    observations: dict[str, tuple[str, int, str]],
    recovery_row_is_valid: bool,
) -> bool:
    if column == "work_key" and table in _WORK_KEY_TABLES:
        if type(value) is not str or not _HEX_64.fullmatch(value):
            raise PrivacyEvidenceError("private work key is malformed")
        return True
    if table == "hardcover_identity_observations" and column in {
        "observation_id",
        "canonical_observation",
    }:
        observation_id = row.get("observation_id")
        return type(observation_id) is str and observation_id in observations
    if table == "current_hardcover_identity" and column == "observation_id":
        if type(value) is not str or value not in observations:
            raise PrivacyEvidenceError("private current observation is malformed")
        expected_work_key, expected_generation, _ = observations[value]
        if (
            row.get("work_key") != expected_work_key
            or row.get("generation") != expected_generation
        ):
            raise PrivacyEvidenceError("private current observation is malformed")
        return True
    if recovery_row_is_valid:
        return column in {"key", "value"}
    return False


def _valid_recovery_row(
    connection: sqlite3.Connection,
    table: str,
    row: dict[str, object],
    recovery_observation: CommittedSQLiteBindingRecovery | None,
) -> bool:
    if (
        table != "instance_metadata"
        or set(row) != {"key", "value"}
        or row.get("key") != _RECOVERY_KEY
        or type(recovery_observation) is not CommittedSQLiteBindingRecovery
        or recovery_observation.connection is not connection
        or row.get("value") != recovery_observation.canonical
    ):
        return False
    current = connection.execute("PRAGMA application_id").fetchone()
    return current == (recovery_observation.marker,)


def _tables(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    tables = {row[0] for row in rows if type(row[0]) is str}
    if any(_IDENTIFIER.fullmatch(table) is None for table in tables):
        raise PrivacyEvidenceError("private persistence table name is malformed")
    return tables


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
    if not columns or any(
        type(column) is not str or _IDENTIFIER.fullmatch(column) is None
        for column in columns
    ):
        raise PrivacyEvidenceError("private persistence column name is malformed")
    return columns


def _logical_record(table: str, column: str, value: object) -> str:
    if type(value) is str:
        rendered = value
    elif type(value) is int or value is None:
        rendered = json.dumps(value, ensure_ascii=True, allow_nan=False)
    elif type(value) is float:
        rendered = repr(value)
    if type(value) is bytes:
        rendered = value.hex()
    elif type(value) not in {str, int, float} and value is not None:
        rendered = type(value).__name__
    return f"table={table}\ncolumn={column}\nvalue-begin\n{rendered}\nvalue-end"


__all__ = ["logical_private_persistence_records"]
