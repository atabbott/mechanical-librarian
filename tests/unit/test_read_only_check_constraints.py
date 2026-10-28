"""A `mode=ro` open re-evaluates the schema's CHECK constraints (#1401).

SQLite's `integrity_check` and `quick_check` skip CHECK constraints on a
read-only connection, so each read-only open sweeps them itself. Each store
below is built valid, given one row written past its CHECKs, and opened the
way production opens it.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest
from scripts import qualification_state_bridge as bridge

from mechanical_librarian.adapters.persistence import catalogue, metadata_reports
from mechanical_librarian.adapters.persistence import provider_recovery as recovery
from mechanical_librarian.adapters.persistence.catalogue import (
    CatalogueReadAdmission,
    open_catalogue,
)
from mechanical_librarian.adapters.persistence.metadata_reports import (
    open_repository,
    open_shadow_preview_repository,
)
from mechanical_librarian.adapters.persistence.provider_recovery import (
    ProviderRecoveryStoreError,
    SQLiteProviderRecoveryStore,
)
from mechanical_librarian.adapters.persistence.schema import (
    _check_constraint_violation,
    _check_expressions,
    _create_schema,
    _expected_schema_objects,
)
from mechanical_librarian.application.catalogue import CatalogueEntry, CatalogueError
from mechanical_librarian.application.metadata_report import MetadataReportError

_REPORT_REFUSAL = (
    "^database is unavailable or incompatible: "
    "CHECK constraint failed in review_preparation_control$"
)


def _tables(statements: tuple[str, ...]) -> list[tuple[str, str, str | None]]:
    return [
        ("table", sql.split()[2].split("(")[0], sql)
        for sql in statements
        if sql.startswith("CREATE TABLE ")
    ]


_STORES = (
    (_create_schema, _expected_schema_objects),
    (
        lambda connection: [connection.execute(sql) for sql in catalogue._SQL],
        lambda: _tables(catalogue._SQL),
    ),
    (
        lambda connection: [connection.execute(sql) for sql in recovery._CREATE],
        lambda: _tables(recovery._CREATE),
    ),
)
_STORE_IDS = ("reports", "catalogue", "provider-recovery")


@pytest.mark.parametrize(("create", "objects"), _STORES, ids=_STORE_IDS)
def test_extraction_takes_every_check_the_code_defined_ddl_states(
    create, objects
) -> None:
    tables = [sql for kind, _, sql in objects() if kind == "table" and sql]
    extracted = [_check_expressions(sql) for sql in tables]
    assert None not in extracted
    count = sum(len(expressions or ()) for expressions in extracted)
    assert count > 0
    assert count == sum(
        len(re.findall(r"\bCHECK\s*\(", sql, re.IGNORECASE)) for sql in tables
    )


@pytest.mark.parametrize(("create", "objects"), _STORES, ids=_STORE_IDS)
def test_every_extracted_expression_runs_against_its_valid_store(
    create, objects
) -> None:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    create(connection)
    assert _check_constraint_violation(connection, objects()) is None
    connection.close()


@pytest.mark.parametrize(
    "sql",
    (
        "CREATE TABLE t(x INTEGER CHECK x>0)",
        "CREATE TABLE t(x INTEGER CHECK(x>0)",
        "CREATE TABLE t(x INTEGER CHECK(x>0 AND CHECK(x<9)))",
        "CREATE TABLE t(x INTEGER CHECK(x>0) -- CHECK(x<0)\n)",
        "CREATE TABLE t(x INTEGER CHECK(x>0) /* CHECK(x<0) */)",
        "CREATE TABLE t([x] INTEGER CHECK([x]>0))",
        "CREATE TABLE t(`x` INTEGER CHECK(`x`>0))",
        "CREATE TABLE t(x TEXT CHECK(x='a))",
        "CREATE TABLE t(x INTEGER CHECK(x>0)))(",
    ),
    ids=(
        "no-parenthesis",
        "unclosed",
        "nested",
        "line-comment",
        "block-comment",
        "bracket-identifier",
        "backtick-identifier",
        "unterminated-string",
        "unbalanced-close",
    ),
)
def test_an_unextractable_check_refuses_rather_than_skips(sql: str) -> None:
    connection = sqlite3.connect(":memory:")
    assert _check_expressions(sql) is None
    assert _check_constraint_violation(connection, [("table", "t", sql)]) == "t"
    connection.close()


def test_extraction_skips_quoted_text_and_identifiers() -> None:
    assert _check_expressions(
        "CREATE TABLE t(checked TEXT CHECK (checked != 'it''s (CHECK(' "
        'AND "check" IN (\')\',"x")), "check" TEXT, CONSTRAINT c CHECK(1))'
    ) == ("checked != 'it''s (CHECK(' AND \"check\" IN (')',\"x\")", "1")


def test_a_null_check_result_passes_and_only_a_false_one_refuses() -> None:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    sql = "CREATE TABLE t(x INTEGER CHECK(x>=0), y TEXT, CHECK(y IN ('a','b')))"
    objects: list[tuple[str, str, str | None]] = [("table", "t", sql)]
    connection.execute(sql)
    # SQLite admits this row itself: both expressions are NULL, not false.
    connection.execute("INSERT INTO t VALUES(NULL,NULL)")
    assert _check_constraint_violation(connection, objects) is None
    connection.execute("PRAGMA ignore_check_constraints=1")
    connection.execute("INSERT INTO t VALUES(NULL,'c')")
    assert _check_constraint_violation(connection, objects) == "t"
    connection.execute("DELETE FROM t WHERE y='c'")
    connection.execute("INSERT INTO t VALUES(-1,NULL)")
    assert _check_constraint_violation(connection, objects) == "t"
    connection.close()


def _write_past_checks(path: Path, statement: str) -> None:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA ignore_check_constraints=1")
    connection.execute(statement)
    connection.commit()
    connection.close()


def _report_store(tmp_path: Path) -> Path:
    tmp_path.chmod(0o700)
    path = tmp_path / "reports.sqlite"
    open_repository(path).close()
    _write_past_checks(path, "UPDATE review_preparation_control SET circuit_failures=9")
    return path


def test_report_store_read_only_open_refuses_a_check_violation(
    tmp_path: Path,
) -> None:
    path = _report_store(tmp_path)
    before = path.read_bytes()
    with pytest.raises(MetadataReportError, match=_REPORT_REFUSAL):
        open_repository(path, create=False)
    assert path.read_bytes() == before


def test_shadow_preview_refuses_a_check_violation_on_each_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _report_store(tmp_path)
    with pytest.raises(MetadataReportError, match=_REPORT_REFUSAL):
        open_shadow_preview_repository(path)
    # The `mode=ro&immutable=1` connection sweeps on its own as well.
    monkeypatch.setattr(
        metadata_reports, "_validate_existing_read_only", lambda *_: None
    )
    with pytest.raises(MetadataReportError, match=_REPORT_REFUSAL):
        open_shadow_preview_repository(path)


def test_bridge_output_validation_refuses_a_check_violation(tmp_path: Path) -> None:
    path = _report_store(tmp_path)
    with pytest.raises(MetadataReportError, match=_REPORT_REFUSAL):
        bridge._validate_output(path, bridge._BridgeData((), (), (), 0))


def _catalogue_store(tmp_path: Path) -> Path:
    tmp_path.chmod(0o700)
    path = tmp_path / "catalogue.sqlite"
    writer = open_catalogue(path, create=True)
    writer.acquire_refresh()
    writer.publish(
        "a" * 64,
        (
            CatalogueEntry(
                "primary",
                "books",
                "library-id",
                "item-1",
                1,
                "Title",
                ("Author",),
                True,
                False,
                "singular-supported",
                "a" * 64,
                "book.epub",
                "epub",
                "books",
            ),
        ),
    )
    writer.close()
    _write_past_checks(path, "UPDATE catalogue_entries SET has_ebook=7")
    return path


_CATALOGUE_REFUSAL = (
    "^catalogue schema is unsupported: CHECK constraint failed in catalogue_entries$"
)


def test_catalogue_open_refuses_a_check_violation_read_only_first(
    tmp_path: Path,
) -> None:
    path = _catalogue_store(tmp_path)
    with pytest.raises(CatalogueError, match=_CATALOGUE_REFUSAL):
        open_catalogue(path)


def test_catalogue_library_admission_refuses_a_check_violation(
    tmp_path: Path,
) -> None:
    path = _catalogue_store(tmp_path)
    connection = sqlite3.connect(":memory:", uri=True, isolation_level=None)
    admission = CatalogueReadAdmission(path, "a" * 64)
    try:
        with pytest.raises(CatalogueError, match=_CATALOGUE_REFUSAL):
            with admission.attach(connection):
                pytest.fail("a CHECK-violating catalogue was attached")
        assert connection.execute("PRAGMA database_list").fetchall() == [
            (0, "main", "")
        ]
    finally:
        admission.close()
        connection.close()


def test_provider_recovery_read_only_open_refuses_a_check_violation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "recovery.sqlite"
    SQLiteProviderRecoveryStore.open(path).close()
    _write_past_checks(
        path, "INSERT INTO provider_recovery_members VALUES('','adapter','scope')"
    )
    with pytest.raises(
        ProviderRecoveryStoreError,
        match="^provider recovery database is unavailable or incompatible: "
        "CHECK constraint failed in provider_recovery_members$",
    ):
        SQLiteProviderRecoveryStore.open(path, read_only=True)
