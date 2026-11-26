"""The frozen synthetic v1 ledger (ADR-0108, "Ledger format and migrations").

`tests/fixtures/ledger/v1.sqlite3` is what `tests/support/ledger_fixture.py`
generates: a ledger written through the product's own writers, then
`regenerate-derived --retire-pending`, so it sits at epoch 1 beside an empty
derived partition. A later build must open it (after a regeneration where its
derived partition differs), decode every ledger document in it and encode each
back byte for byte, and the generator must still write the same ledger. C6's
first migration starts from this file.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence import schema
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.metadata_report import (
    DerivedRegenerationRequired,
)
from tests.support.ledger_documents import DOCUMENTS, missing_documents
from tests.support.ledger_fixture import FIXTURE, generate, ledger_dump
from tests.support.ledger_partition import _derived_setup, regenerate, rewrite

MUTATED = Path(__file__).parents[1] / "fixtures" / "derived" / "pre-cutover-mutated.sql"
# Ledger tables no store of this build can hold a row of: their documents are
# registered and pinned, but no fixture row proves they re-encode. Each one is
# written only for an automatic cover switch over a populated cover, which
# needs a replacement qualification record, and this build has none
# (`cover_qualification_policy`: every `*_REPLACEMENT_QUALIFICATION_SHA256` is
# None); a fill over a known-empty cover keeps no original.
EMPTY = frozenset(
    {
        # Bound when such a switch keeps the cover it replaces.
        "retained_cover_bindings",
        # Listed when such a switch is verified.
        "retained_cover_listings",
        # Owed when a bound switch ends unverified, is kept or is put back.
        "retained_cover_removals",
    }
)
# Ledger rows a regeneration without the flag writes (its record and every
# current authority revoked); every other ledger row stays.
_WRITTEN_BY_A_RUN = frozenset(
    {"ledger_meta", "derived_regenerations", "field_effect_authorities"}
)


def _copy(root: Path) -> Path:
    """A copy of the fixture in a private folder, as `/data` is."""
    root.mkdir(mode=0o700)
    path = root / "application.sqlite3"
    shutil.copyfile(FIXTURE, path)
    path.chmod(0o600)
    return path


def _opened(path: Path) -> None:
    """Open the store as a start does; a build whose derived partition
    differs regenerates first, as its startup asks."""
    try:
        open_repository(path, create=False).close()
    except DerivedRegenerationRequired:
        regenerate(path)
        open_repository(path, create=False).close()


def _not_byte_for_byte(path: Path) -> list[tuple[str, str]]:
    """The column of each registered document in the store at ``path`` that
    does not decode, or does not encode back to its stored bytes."""
    failed: list[tuple[str, str]] = []
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        for (table, column), document in sorted(DOCUMENTS.items()):
            for (stored,) in connection.execute(
                f'SELECT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL'
            ):
                try:
                    if document.encode(document.decode(stored)) != stored:
                        failed.append((table, column))
                except ValueError:
                    failed.append((table, column))
    finally:
        connection.close()
    return failed


def test_the_fixture_is_this_format_at_epoch_one_with_an_empty_derived_partition() -> (
    None
):
    connection = sqlite3.connect(f"file:{FIXTURE}?mode=ro", uri=True)
    try:
        assert connection.execute(
            "SELECT ledger_format_version,derived_epoch FROM ledger_meta"
        ).fetchone() == (schema.LEDGER_FORMAT_VERSION, 1)
        assert connection.execute(
            "SELECT retire_pending FROM derived_regenerations"
        ).fetchall() == [(1,)]
        assert not missing_documents(connection)
        filled = {
            table
            for table in schema.LEDGER_TABLES
            if connection.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone()
        }
        assert schema.LEDGER_TABLES - filled == EMPTY
        (digest,) = connection.execute(
            "SELECT derived_schema_digest FROM ledger_meta"
        ).fetchone()
        # Beside the derived partition of the build that wrote it, empty; a
        # later build's differs and is regenerated (see `_opened`).
        if digest == schema.build_partition().derived_schema_digest:
            assert {
                table: connection.execute(f'SELECT * FROM "{table}"').fetchall()
                for table in _derived_setup()
            } == _derived_setup()
    finally:
        connection.close()


def test_a_copy_of_the_fixture_opens_through_the_real_open(tmp_path: Path) -> None:
    path = _copy(tmp_path / "data")
    before = ledger_dump(path)
    _opened(path)
    # Opening reads the ledger; it never rewrites it.
    after = ledger_dump(path)
    assert {t: r for t, r in after.items() if t not in _WRITTEN_BY_A_RUN} == {
        t: r for t, r in before.items() if t not in _WRITTEN_BY_A_RUN
    }


def test_every_ledger_document_re_encodes_byte_for_byte() -> None:
    connection = sqlite3.connect(f"file:{FIXTURE}?mode=ro", uri=True)
    try:
        reached = {
            (table, column)
            for table, column in DOCUMENTS
            if connection.execute(
                f'SELECT 1 FROM "{table}" WHERE "{column}" IS NOT NULL LIMIT 1'
            ).fetchone()
        }
    finally:
        connection.close()
    assert _not_byte_for_byte(FIXTURE) == []
    # Every registered column of a filled table holds a document here, so
    # each one was decoded and encoded, the non-ASCII name included.
    assert {
        (table, column) for table, column in DOCUMENTS if table not in EMPTY
    } == reached


@pytest.mark.parametrize(
    ("table", "column", "change"),
    [
        ("human_decisions", "selected_value_document", "' '||selected_value_document"),
        ("field_effect_events", "document", "replace(document,'\":','\": ')"),
        ("owner_settings", "document", "replace(document,'\"','\"\"')"),
    ],
    ids=["leading_space", "spaced_separator", "unparseable"],
)
def test_a_document_that_does_not_re_encode_is_found(
    tmp_path: Path, table: str, column: str, change: str
) -> None:
    """Negative control: one document whose bytes are not the writer's."""
    path = _copy(tmp_path / "data")
    (rowid,) = (
        sqlite3.connect(path).execute(f'SELECT min(rowid) FROM "{table}"').fetchone()
    )
    assert (
        rewrite(
            path, f'UPDATE "{table}" SET "{column}"={change} WHERE rowid=?', (rowid,)
        )
        == 1
    )
    assert _not_byte_for_byte(path) == [(table, column)]


def test_the_command_regenerates_the_fixture_under_an_earlier_derived_partition(
    tmp_path: Path,
) -> None:
    """The fixture beside the frozen earlier derived partition: startup asks
    for a regeneration, and the command keeps every ledger row but its own."""
    path = _copy(tmp_path / "data")
    before = ledger_dump(path)
    connection = sqlite3.connect(path)
    try:
        connection.executescript(MUTATED.read_text(encoding="utf-8"))
    finally:
        connection.close()
    with pytest.raises(DerivedRegenerationRequired):
        open_repository(path, create=False)
    regenerate(path, at=2_000)
    open_repository(path, create=False).close()
    after = ledger_dump(path)
    assert {t: r for t, r in after.items() if t not in _WRITTEN_BY_A_RUN} == {
        t: r for t, r in before.items() if t not in _WRITTEN_BY_A_RUN
    }
    assert [row[2] for row in after["derived_regenerations"]] == [1, 2]


def test_the_generator_writes_the_frozen_ledger(tmp_path: Path) -> None:
    """The generator, run again at this build, writes the fixture's ledger:
    a writer change that moves a ledger row shows here."""
    root = tmp_path / "store"
    root.mkdir(mode=0o700)
    assert ledger_dump(generate(root)) == ledger_dump(FIXTURE)
