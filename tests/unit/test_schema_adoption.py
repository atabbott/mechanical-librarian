"""The offline `review-attention-facts-v1` adoption (#1232, ADR-0013)."""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest

import mechanical_librarian.adapters.persistence.schema_adoption as adoption
from mechanical_librarian.adapters.persistence import schema
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.persistence.offline_backup import (
    _expected_schema_objects_digest,
    _schema_objects_digest,
)
from mechanical_librarian.adapters.persistence.review_sql import (
    _ATTENTION_FACT_SOURCE_SQL,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.schema_adoption import (
    REVIEW_ATTENTION_FACTS_V1,
    SchemaAdoptionRecoveryRequired,
)
from mechanical_librarian.interfaces.cli import main
from tests.support.owner_responsiveness import OwnerResponsivenessFixture

_DROP_ATTENTION_FACTS_SQL = "".join(
    f"DROP {kind.upper()} {name};"
    for kind, name, _ in reversed(schema._ATTENTION_FACT_OBJECTS)
)


@pytest.fixture(scope="module")
def _current(tmp_path_factory: pytest.TempPathFactory) -> Iterator[bytes]:
    """An exact-current owner store whose fields ask for the owner."""
    root = tmp_path_factory.mktemp("schema-adoption")
    root.chmod(0o700)
    (root / "owner").mkdir(mode=0o700)
    fixture = OwnerResponsivenessFixture(root / "owner", 30, mixed=True)
    path = root / "current.sqlite3"
    try:
        fixture.repository._connection.execute("VACUUM INTO ?", (str(path),))
    finally:
        fixture.close()
    yield path.read_bytes()


def _private(tmp_path: Path) -> Path:
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    return directory


def _write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    path.chmod(0o600)
    return path


def _predecessor(current: bytes, path: Path) -> Path:
    _write(path, current)
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(_DROP_ATTENTION_FACTS_SQL)
    path.chmod(0o600)
    return path


def _rows(path: Path) -> dict[str, list[tuple[object, ...]]]:
    with closing(sqlite3.connect(path)) as connection:
        return {
            str(name): connection.execute(f'SELECT * FROM "{name}"').fetchall()
            for (name,) in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name<>'review_attention_facts' ORDER BY name"
            ).fetchall()
        }


def test_plan_literals_name_this_build_and_its_exact_predecessor(
    _current: bytes, tmp_path: Path
) -> None:
    assert _expected_schema_objects_digest() == adoption._TARGET_SCHEMA_OBJECTS_SHA256
    path = _predecessor(_current, _private(tmp_path) / "application.sqlite3")
    with closing(sqlite3.connect(path)) as connection:
        assert (
            _schema_objects_digest(connection)
            == adoption._PREDECESSOR_SCHEMA_OBJECTS_SHA256
        )


def test_adoption_derives_the_facts_and_changes_no_stored_row(
    _current: bytes, tmp_path: Path
) -> None:
    directory = _private(tmp_path)
    database = _predecessor(_current, directory / "application.sqlite3")
    backup = directory / "application.backup.sqlite3"
    source = database.read_bytes()
    rows = _rows(database)

    assert adoption.adopt_schema(database, backup, REVIEW_ATTENTION_FACTS_V1) == {
        "plan": REVIEW_ATTENTION_FACTS_V1,
        "status": "adopted",
    }

    assert backup.read_bytes() == source
    assert backup.stat().st_mode & 0o777 == 0o600
    assert _rows(database) == rows
    with closing(open_repository(database, create=False)) as repository:
        connection = repository._connection
        facts = connection.execute(
            "SELECT * FROM review_attention_facts ORDER BY 1,2,3,4,5"
        ).fetchall()
        assert facts
        assert facts == sorted(
            connection.execute(_ATTENTION_FACT_SOURCE_SQL).fetchall(),
            key=lambda row: row[:5],
        )
    # A second run finds the exact-current result and touches nothing.
    adopted = database.read_bytes()
    assert adoption.adopt_schema(
        database, directory / "unused.sqlite3", REVIEW_ATTENTION_FACTS_V1
    ) == {"plan": REVIEW_ATTENTION_FACTS_V1, "status": "already-current"}
    assert database.read_bytes() == adopted
    assert not (directory / "unused.sqlite3").exists()


def test_the_build_and_plan_are_checked_before_any_filesystem_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "absent" / "application.sqlite3"
    with pytest.raises(
        MetadataReportError, match="^schema adoption plan is unsupported$"
    ):
        adoption.adopt_schema(missing, missing.with_name("b"), "owner-settings-v1")
    monkeypatch.setattr(adoption, "_TARGET_SCHEMA_OBJECTS_SHA256", "0" * 64)
    with pytest.raises(
        MetadataReportError, match="^schema adoption is unavailable for this build$"
    ):
        adoption.adopt_schema(
            missing, missing.with_name("b"), REVIEW_ATTENTION_FACTS_V1
        )
    assert not missing.parent.exists()


@pytest.mark.parametrize(
    ("backup", "message"),
    (
        (
            "elsewhere/application.backup.sqlite3",
            "schema adoption backup path is invalid",
        ),
        ("application.sqlite3", "schema adoption backup path is invalid"),
    ),
)
def test_backup_beside_the_source_under_another_name_is_required(
    _current: bytes, tmp_path: Path, backup: str, message: str
) -> None:
    directory = _private(tmp_path)
    database = _predecessor(_current, directory / "application.sqlite3")
    before = database.read_bytes()
    with pytest.raises(MetadataReportError, match=f"^{message}$"):
        adoption.adopt_schema(database, directory / backup, REVIEW_ATTENTION_FACTS_V1)
    assert database.read_bytes() == before


def _refused_without_mutation(database: Path, backup: Path, message: str) -> None:
    before = database.read_bytes()
    with pytest.raises(MetadataReportError, match=f"^{message}$"):
        adoption.adopt_schema(database, backup, REVIEW_ATTENTION_FACTS_V1)
    assert database.read_bytes() == before


def test_any_other_schema_is_refused_without_mutation(
    _current: bytes, tmp_path: Path
) -> None:
    directory = _private(tmp_path)
    database = _predecessor(_current, directory / "application.sqlite3")
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("DROP INDEX current_review_packages_case")
    _refused_without_mutation(
        database,
        directory / "backup.sqlite3",
        "database is unavailable or incompatible",
    )
    assert not (directory / "backup.sqlite3").exists()


def test_a_predecessor_with_invalid_contents_is_refused_without_mutation(
    _current: bytes, tmp_path: Path
) -> None:
    directory = _private(tmp_path)
    database = _predecessor(_current, directory / "application.sqlite3")
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "UPDATE metadata_report_browse_facts SET title_key=title_key||'x'"
        )
        connection.commit()
    _refused_without_mutation(
        database,
        directory / "backup.sqlite3",
        "database is unavailable or incompatible",
    )
    assert not (directory / "backup.sqlite3").exists()


def test_a_shared_database_or_an_existing_backup_is_refused(
    _current: bytes, tmp_path: Path
) -> None:
    directory = _private(tmp_path)
    database = _predecessor(_current, directory / "application.sqlite3")
    database.chmod(0o640)
    _refused_without_mutation(
        database,
        directory / "backup.sqlite3",
        "schema adoption database is not owner-only",
    )
    database.chmod(0o600)
    existing = _write(directory / "backup.sqlite3", b"kept")
    _refused_without_mutation(
        database, existing, "schema adoption backup already exists"
    )
    assert existing.read_bytes() == b"kept"


def test_a_failed_check_after_commit_requires_recovery_from_the_backup(
    _current: bytes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = _private(tmp_path)
    database = _predecessor(_current, directory / "application.sqlite3")
    backup = directory / "backup.sqlite3"
    source = database.read_bytes()

    def refuse(connection: sqlite3.Connection) -> None:
        raise MetadataReportError("database is unavailable or incompatible")

    monkeypatch.setattr(adoption, "_validate_schema", refuse)
    with pytest.raises(SchemaAdoptionRecoveryRequired):
        adoption.adopt_schema(database, backup, REVIEW_ATTENTION_FACTS_V1)
    assert backup.read_bytes() == source
    assert hashlib.sha256(backup.read_bytes()).hexdigest() == (
        hashlib.sha256(source).hexdigest()
    )


def test_the_offline_command_reports_each_outcome(
    _current: bytes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = _private(tmp_path)
    database = _predecessor(_current, directory / "application.sqlite3")
    arguments = [
        "schema-adopt",
        REVIEW_ATTENTION_FACTS_V1,
        "--database",
        str(database),
        "--backup",
        str(directory / "backup.sqlite3"),
    ]
    output, errors = io.StringIO(), io.StringIO()
    assert (
        main(
            arguments,
            schema_adoption_runner=adoption.adopt_schema,
            stdout=output,
            stderr=errors,
        )
        == 0
    )
    assert json.loads(output.getvalue()) == {
        "plan": REVIEW_ATTENTION_FACTS_V1,
        "status": "adopted",
    }
    assert errors.getvalue() == ""

    def recovery(*_arguments: object) -> dict[str, str]:
        raise SchemaAdoptionRecoveryRequired

    def failure(*_arguments: object) -> dict[str, str]:
        raise MetadataReportError("schema adoption backup already exists")

    for runner, message in (
        (recovery, "Schema adoption requires backup recovery.\n"),
        (failure, "Schema adoption failed safely.\n"),
    ):
        errors = io.StringIO()
        assert (
            main(
                arguments,
                schema_adoption_runner=runner,
                stdout=io.StringIO(),
                stderr=errors,
            )
            == 1
        )
        assert errors.getvalue() == message
    errors = io.StringIO()
    assert (
        main([*arguments[:1], "review-field-facts-v1", *arguments[2:]], stderr=errors)
        == 2
    )
    assert errors.getvalue() == "Review server failed: invalid invocation.\n"
