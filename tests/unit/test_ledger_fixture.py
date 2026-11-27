"""The frozen synthetic ledgers (ADR-0108, "Ledger format and migrations").

`tests/fixtures/ledger/v<n>.sqlite3` is, for each ledger format `n` up to this
build's, what `tests/support/ledger_fixture.py` generated at that format: a
ledger written through the product's own writers, then
`regenerate-derived --retire-pending`, so it sits at epoch 1 beside an empty
derived partition. Each file's bytes are pinned. This build must open the
current one (after a regeneration where its derived partition differs),
decode every ledger document in it and encode each back byte for byte, and
the generator must still write a ledger of the same shape
(`ledger_projection`); every earlier one must migrate through the command to
a ledger of this build whose documents re-encode.
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence import ledger_migrations, schema
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.metadata_report import (
    DerivedRegenerationRequired,
    LedgerMigrationRequired,
)
from tests.support.ledger_chain import chain, identity
from tests.support.ledger_documents import DOCUMENTS, missing_documents
from tests.support.ledger_fixture import (
    FIXTURE,
    generate,
    ledger_dump,
    ledger_projection,
)
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
# {ledger format: `tests/fixtures/ledger/v<format>.sqlite3`, as written by
# `write_fixture` at that format}
FIXTURE_SHA256 = {
    1: "1b939510faf6b9661a980d8f867a92df1121096c1390ac4d4775085a2ba6869f",
}
# Ledger rows a regeneration without the flag writes (its record and every
# current authority revoked); every other ledger row stays.
_WRITTEN_BY_A_RUN = frozenset(
    {"ledger_meta", "derived_regenerations", "field_effect_authorities"}
)


def _fixture(version: int) -> Path:
    return FIXTURE.parent / f"v{version}.sqlite3"


def _copy(root: Path, fixture: Path = FIXTURE) -> Path:
    """A copy of a fixture in a private folder, as `/data` is."""
    root.mkdir(mode=0o700)
    path = root / "application.sqlite3"
    shutil.copyfile(fixture, path)
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
            stored = {
                table: connection.execute(f'SELECT * FROM "{table}"').fetchall()
                for table in _derived_setup()
            }
            # Except the one seed that names the Unicode version of the
            # Python that wrote it: a build running under another regenerates
            # on open (see `_opened`).
            expected = _derived_setup()
            ((singleton, _, rules),) = expected["browse_fact_identity"]
            ((_, written_under, _),) = stored["browse_fact_identity"]
            expected["browse_fact_identity"] = [(singleton, written_under, rules)]
            assert stored == expected
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
        # Canonical JSON still, but a rule no build or edit writes.
        (
            "cover_taste_profile_versions",
            "document",
            'replace(document,\'"provenance":"model"\',\'"provenance":"other"\')',
        ),
    ],
    ids=["leading_space", "spaced_separator", "unparseable", "taste_rule_unwritable"],
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


def test_every_ledger_version_has_its_frozen_fixture() -> None:
    """One fixture per format up to this build's, each the frozen file: it
    never changes for its format (a later ledger is a migration from it), so
    a rewrite is deliberate."""
    versions = range(1, schema.LEDGER_FORMAT_VERSION + 1)
    assert sorted(FIXTURE.parent.glob("v*.sqlite3")) == sorted(
        _fixture(version) for version in versions
    )
    assert {
        version: hashlib.sha256(_fixture(version).read_bytes()).hexdigest()
        for version in versions
    } == FIXTURE_SHA256


def _migrates_through_the_command(fixture: Path, root: Path) -> None:
    """A copy of an earlier fixture, through the real command: startup names
    it, the run migrates it to a ledger holding exactly this build's ledger
    objects, every document re-encodes byte for byte, and it opens."""
    path = _copy(root, fixture)
    with pytest.raises(LedgerMigrationRequired):
        open_repository(path, create=False)
    regenerate(path, at=2_000)
    connection = sqlite3.connect(path)
    try:
        ledger, _ = schema._partition(schema._creation_order(connection))
        assert set(ledger) == set(schema.build_partition().ledger_objects)
        assert connection.execute(
            "SELECT ledger_format_version FROM ledger_meta"
        ).fetchone() == (schema.LEDGER_FORMAT_VERSION,)
    finally:
        connection.close()
    assert _not_byte_for_byte(path) == []
    open_repository(path, create=False).close()


def test_each_older_fixture_migrates_through_the_command(tmp_path: Path) -> None:
    """Every older fixture the chain reaches; one behind a **Clean rebuild
    required** version has no step and is refused at startup instead."""
    for version in range(1, schema.LEDGER_FORMAT_VERSION):
        if ledger_migrations.steps_from(version, schema.LEDGER_FORMAT_VERSION):
            _migrates_through_the_command(_fixture(version), tmp_path / f"v{version}")


def test_an_older_fixture_migrates_through_a_synthetic_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The generic test's own proof, before any real step exists: this
    build as format 2 with an identity step, and v1 as the older fixture."""
    chain(monkeypatch, identity(1), current=2)
    _migrates_through_the_command(FIXTURE, tmp_path / "v1")


def test_the_generator_writes_a_ledger_of_the_frozen_shape(tmp_path: Path) -> None:
    """The generator, run again at this build, writes as many rows of each
    ledger table, and documents of the same schema labels and kinds, as the
    fixture holds: a writer change that adds, drops or relabels a ledger row
    shows here. Ledger ids derive from derived content, so the rows
    themselves are not compared: a derived-only change (a policy version)
    moves them and leaves the fixture as it is."""
    root = tmp_path / "store"
    root.mkdir(mode=0o700)
    assert ledger_projection(generate(root)) == ledger_projection(FIXTURE)
