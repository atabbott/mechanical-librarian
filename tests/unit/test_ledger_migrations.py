"""The ledger's migrations (ADR-0108, "Ledger format and migrations").

This build's chain is empty: no ledger change has ridden it yet. Each test
here registers synthetic steps inside the test only
(`tests.support.ledger_chain.chain`), never in the product registry, and runs
the real command over a store the product's own writers made, or over the
frozen v1 fixture.
"""

from __future__ import annotations

import dataclasses
import json
import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mechanical_librarian.adapters.persistence import (
    ledger_migrations,
    regeneration,
    schema,
)
from mechanical_librarian.adapters.persistence.ledger_migrations import (
    LedgerMigration,
)
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.metadata_policy import ResolvedMetadataPolicy
from mechanical_librarian.interfaces.cli import regenerate as cli
from tests.support.ledger_chain import chain, identity
from tests.support.ledger_fixture import ledger_dump
from tests.support.ledger_partition import opens, rewrite
from tests.support.owner_values import OwnerStore
from tests.unit.test_ledger_fixture import _WRITTEN_BY_A_RUN, _copy, _not_byte_for_byte
from tests.unit.test_regenerate_derived import (
    GENRES,
    _closed,
    _fresh,
    _no_scratch_left,
    _refuses_unchanged,
    _run,
    _scratch_entries,
    _sha,
    owner,  # noqa: F401  # reason: a fixture, used by name.
)

# --- The chain ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("sources", "version", "current", "expected"),
    [
        ((), 1, 1, None),
        ((1,), 1, 2, (1,)),
        ((1, 2), 1, 3, (1, 2)),
        ((1, 2), 2, 3, (2,)),
        ((2,), 1, 3, None),
        ((1,), 1, 3, None),
        ((1,), 2, 2, None),
        ((1,), 3, 2, None),
        ((1,), 0, 2, None),
    ],
    ids=[
        "empty",
        "one_step",
        "two_steps",
        "from_the_middle",
        "first_missing",
        "last_missing",
        "current",
        "newer",
        "before_the_first",
    ],
)
def test_steps_from_names_each_exact_predecessor_step_or_none(
    monkeypatch: pytest.MonkeyPatch,
    sources: tuple[int, ...],
    version: int,
    current: int,
    expected: tuple[int, ...] | None,
) -> None:
    monkeypatch.setattr(
        ledger_migrations,
        "LEDGER_MIGRATIONS",
        tuple(identity(source) for source in sources),
    )
    steps = ledger_migrations.steps_from(version, current)
    assert (None if steps is None else tuple(s.source for s in steps)) == expected


# --- Shared ------------------------------------------------------------------

_INCOMPATIBLE = "database is unavailable or incompatible"
# An index of the earlier format a synthetic step drops (S3, DP4).
_PLANTED = "CREATE INDEX planted_v1 ON human_decisions(occurred_at)"


def _audit(path: Path) -> list[tuple[int, int]]:
    connection = sqlite3.connect(path)
    try:
        return connection.execute(
            "SELECT ledger_format_version_before,ledger_format_version_after "
            "FROM derived_regenerations ORDER BY regeneration_id"
        ).fetchall()
    finally:
        connection.close()


def _format(path: Path) -> int:
    connection = sqlite3.connect(path)
    try:
        (version,) = connection.execute(
            "SELECT ledger_format_version FROM ledger_meta"
        ).fetchone()
        return int(version)
    finally:
        connection.close()


def _exact(path: Path) -> None:
    """Bring the fixture copy's derived partition to this build's, so only
    a pending migration is left to do."""
    assert _run(path)[2].kind in {"nothing_to_do", "regenerated"}
    assert _run(path)[2].kind == "nothing_to_do"


def _calls(steps: list[int]) -> Callable[[sqlite3.Connection], int]:
    """Count a step's calls: the dry run's is the first, the transaction's
    the second."""

    def counted(_connection: sqlite3.Connection) -> int:
        steps.append(len(steps) + 1)
        return len(steps)

    return counted


def _rewrite_resolution(connection: sqlite3.Connection) -> None:
    """A self-consistent rewrite of the one resolution (DP2): one option
    changes, so its canonical document and its id change together."""
    ((generation, library, document),) = connection.execute(
        "SELECT generation,library_name,document FROM metadata_policy_resolutions"
    ).fetchall()
    connection.execute(
        "UPDATE metadata_policy_resolutions SET resolved_policy_id=?,document=? "
        "WHERE generation=? AND library_name=?",
        (*_rewritten(document), generation, library),
    )


def _rewritten(document: str) -> tuple[str, str]:
    resolved = ResolvedMetadataPolicy.from_data(json.loads(document))
    assert dict(resolved.options)["tags"] == "off"
    moved = dataclasses.replace(
        resolved,
        options=tuple(
            (name, "fill_empty" if name == "tags" else value)
            for name, value in resolved.options
        ),
    )
    return moved.resolved_id, moved.canonical_json


# --- The chain in the command ------------------------------------------------


def test_the_chain_runs_in_order_after_the_drop_and_the_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each step runs once on admission's scratch copy, then once inside the
    transaction, after the derived drop and the verified restore point, in
    order, each seeing its own source format."""
    path = _fresh(tmp_path / "data")
    backup = path.parent / "regeneration-backup" / "epoch-0.sqlite3"
    seen: list[tuple[int, int, bool, bool, bool]] = []

    def step(source: int) -> LedgerMigration:
        def migrate(connection: sqlite3.Connection) -> None:
            (version,) = connection.execute(
                "SELECT ledger_format_version FROM ledger_meta"
            ).fetchone()
            derived = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='review_cases'"
            ).fetchone()
            seen.append(
                (
                    source,
                    version,
                    connection.in_transaction,
                    derived is not None,
                    backup.exists(),
                )
            )

        return LedgerMigration(source, migrate)

    chain(monkeypatch, step(1), step(2), current=3)
    status, lines, outcome = _run(path)
    assert (status, outcome.kind) == (0, "regenerated"), lines
    assert seen == [
        (1, 1, True, False, False),
        (2, 2, True, False, False),
        (1, 1, True, False, True),
        (2, 2, True, False, True),
    ]
    assert _format(path) == 3
    assert _audit(path) == [(1, 3)]
    assert opens(path)


def test_a_pending_migration_beside_exact_derived_state_is_not_nothing_to_do(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An additive change leaves the derived digest where it was: the run
    still migrates, records the formats it went from and to, and only then
    has nothing to do."""
    path = _copy(tmp_path / "data")
    _exact(path)
    chain(monkeypatch, identity(1), current=2)
    status, lines, outcome = _run(path)
    assert (status, outcome.kind) == (0, "regenerated"), lines
    assert _format(path) == 2
    assert _audit(path)[-1] == (1, 2)
    assert _run(path)[:2] == (0, [cli.NOTHING_TO_DO])


def test_the_frozen_v1_fixture_migrates_to_v2_and_matches_the_expected_dump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The C6 proof: the real command migrates the frozen v1 ledger through
    a step that rewrites the one resolution, and the ledger after the run is
    the ledger before it with exactly that edit, every decision, its order,
    the record, its events and every document kept."""
    path = _copy(tmp_path / "data")
    expected = ledger_dump(path)
    ((generation, library, _, document),) = expected["metadata_policy_resolutions"]
    expected["metadata_policy_resolutions"] = [
        (generation, library, *_rewritten(str(document)))
    ]
    chain(monkeypatch, LedgerMigration(1, _rewrite_resolution), current=2)
    status, lines, outcome = _run(path)
    assert (status, outcome.kind) == (0, "regenerated"), lines
    after = ledger_dump(path)
    assert {t: r for t, r in after.items() if t not in _WRITTEN_BY_A_RUN} == {
        t: r for t, r in expected.items() if t not in _WRITTEN_BY_A_RUN
    }
    assert _not_byte_for_byte(path) == []
    assert _audit(path)[-1] == (1, 2)
    assert opens(path)


def test_a_rewritten_resolution_lapses_that_librarys_consents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The step never touches a consent: each binds the resolution it was
    granted under, so after the rewrite Activity reports the library's
    consents lapsed, and the consent rows are byte for byte as they were."""
    path = _copy(tmp_path / "data")
    _exact(path)

    def activity() -> dict[str, Any]:
        repository = open_repository(path, create=False)
        try:
            return repository.field_effect_activity_summary()
        finally:
            repository.close()

    def consents() -> list[tuple[object, ...]]:
        connection = sqlite3.connect(path)
        try:
            return connection.execute(
                "SELECT * FROM field_effect_consents ORDER BY consent_id"
            ).fetchall()
        finally:
            connection.close()

    before, rows = activity(), consents()
    assert len(before["consented_destinations"]) == len(rows) == 4
    assert before["lapsed_consents"] == []
    chain(monkeypatch, LedgerMigration(1, _rewrite_resolution), current=2)
    assert _run(path)[2].kind == "regenerated"
    after = activity()
    assert consents() == rows
    assert after["consented_destinations"] == []
    assert [
        (lapsed["connection"], lapsed["library"]) for lapsed in after["lapsed_consents"]
    ] == [("primary", "ebooks")]


def test_an_inconsistent_resolution_rewrite_is_refused_by_the_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control: the same rewrite keeping the old id is refused on
    the scratch copy, before any restore point, and changes nothing."""

    def inconsistent(connection: sqlite3.Connection) -> None:
        (document,) = connection.execute(
            "SELECT document FROM metadata_policy_resolutions"
        ).fetchone()
        connection.execute(
            "UPDATE metadata_policy_resolutions SET document=?",
            (_rewritten(document)[1],),
        )

    path = _copy(tmp_path / "data")
    chain(monkeypatch, LedgerMigration(1, inconsistent), current=2)
    _, lines, outcome = _refuses_unchanged(path)
    assert (outcome.kind, lines) == ("failed", [cli.FAILED])
    assert not (path.parent / "regeneration-backup").exists()


# --- Admission of an earlier ledger -------------------------------------------


def _drop_planted(connection: sqlite3.Connection) -> None:
    connection.execute("DROP INDEX planted_v1")


@pytest.mark.parametrize("planted", [True, False], ids=["planted", "absent"])
def test_an_earlier_ledger_is_checked_against_its_own_formats_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, planted: bool
) -> None:
    """The step drops an index only the earlier format declares: admission
    accepts the earlier ledger that holds it, and the step's drop succeeds;
    without it the earlier ledger is altered (R10), and nothing changes."""
    path = _fresh(tmp_path / "data")
    if planted:
        rewrite(path, _PLANTED)
    chain(
        monkeypatch,
        LedgerMigration(
            1,
            _drop_planted,
            replaced=(("index", "planted_v1", "human_decisions", _PLANTED),),
        ),
        current=2,
    )
    if not planted:
        _, lines, outcome = _refuses_unchanged(path)
        assert outcome.refusal is regeneration.Refusal.LEDGER_ALTERED
        assert lines == [cli.REFUSALS[regeneration.Refusal.LEDGER_ALTERED]]
        return
    status, lines, outcome = _run(path)
    assert (status, outcome.kind) == (0, "regenerated"), lines
    connection = sqlite3.connect(path)
    try:
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='planted_v1'"
            ).fetchone()
            is None
        )
    finally:
        connection.close()
    assert opens(path)


def test_dropping_with_this_builds_ledger_list_would_lose_the_earlier_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DP4's negative control: a drop that kept only this build's ledger
    objects would drop the earlier format's index as derived, so the step
    meets no index and the transaction rolls back."""
    path = _fresh(tmp_path / "data")
    rewrite(path, _PLANTED)
    failures: list[str] = []

    def migrate(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("DROP INDEX planted_v1")
        except sqlite3.OperationalError as error:
            failures.append(str(error))
            raise

    chain(
        monkeypatch,
        LedgerMigration(
            1,
            migrate,
            replaced=(("index", "planted_v1", "human_decisions", _PLANTED),),
        ),
        current=2,
    )
    real = regeneration._drop_derived
    monkeypatch.setattr(
        regeneration,
        "_drop_derived",
        lambda connection, _objects: real(
            connection, schema.build_partition().ledger_objects
        ),
    )
    before = _sha(path)
    status, lines, outcome = _run(path)
    assert outcome.refusal is regeneration.Refusal.ROLLED_BACK, lines
    assert failures == ["no such index: planted_v1"]
    assert _sha(path) == before


def test_a_step_the_guard_refuses_on_the_copy_stops_before_the_restore_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dry run runs before the backup: a step that changes the record
    on the scratch copy is refused there (R9), with no restore point."""
    path = _copy(tmp_path / "data")
    calls: list[int] = []
    counted = _calls(calls)

    def migrate(connection: sqlite3.Connection) -> None:
        if counted(connection) == 1:
            connection.execute(
                "UPDATE owner_field_answers SET recorded_at=recorded_at+1"
            )

    chain(monkeypatch, LedgerMigration(1, migrate), current=2)
    _, lines, outcome = _refuses_unchanged(path)
    assert (outcome.kind, lines) == ("failed", [cli.FAILED])
    assert calls == [1]
    assert not (path.parent / "regeneration-backup").exists()


# --- What the chain keeps ------------------------------------------------------


def _swap_two_ordinals(connection: sqlite3.Connection) -> None:
    for (name,) in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' "
        "AND tbl_name='human_decisions'"
    ).fetchall():
        connection.execute(f'DROP TRIGGER "{name}"')
    first, second = (
        row[0]
        for row in connection.execute(
            "SELECT ordinal FROM human_decisions ORDER BY ordinal LIMIT 2"
        )
    )
    connection.execute(
        "UPDATE human_decisions SET ordinal=-1 WHERE ordinal=?", (first,)
    )
    connection.execute(
        "UPDATE human_decisions SET ordinal=? WHERE ordinal=?", (first, second)
    )
    connection.execute(
        "UPDATE human_decisions SET ordinal=? WHERE ordinal=-1", (second,)
    )


def _change_an_offer_digest(connection: sqlite3.Connection) -> None:
    assert (
        connection.execute(
            "UPDATE owner_field_answers SET offer_digest=? WHERE answer_id=("
            "SELECT min(answer_id) FROM owner_field_answers "
            "WHERE offer_digest IS NOT NULL)",
            ("0" * 64,),
        ).rowcount
        == 1
    )


def _add_an_owner_field_event(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT INTO owner_field_events(event_id,connection_name,library_name,"
        "item_id,destination,kind,request_id,over_ordinal,scan_id,answer_id,"
        "occurred_at) VALUES(?,'primary','ebooks','item-1','title',"
        "'resume_requested',NULL,0,'scan',NULL,1)",
        ("owner_field_event_" + "a" * 64,),
    )


def _add_an_authority(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT INTO field_effect_authorities(decision_id,current,updated_at,"
        "document) VALUES(?,0,1,'{}')",
        ("human_decision_" + "f" * 64,),
    )


def _make_an_authority_current(connection: sqlite3.Connection) -> None:
    assert (
        connection.execute(
            "UPDATE field_effect_authorities SET current=1 WHERE rowid=("
            "SELECT min(rowid) FROM field_effect_authorities WHERE current=0)"
        ).rowcount
        == 1
    )


@pytest.mark.parametrize(
    "violation",
    [
        _swap_two_ordinals,
        _change_an_offer_digest,
        _add_an_owner_field_event,
        _add_an_authority,
        _make_an_authority_current,
    ],
    ids=[
        "ordinals_swapped",
        "question_half_changed",
        "event_added",
        "authority_added",
        "authority_made_current",
    ],
)
def test_a_step_that_changes_what_the_chain_keeps_rolls_the_run_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    violation: Callable[[sqlite3.Connection], None],
) -> None:
    """Each change, made only by the transaction's call so the dry run
    admits the ledger, completes, and then the chain's guard refuses it:
    the run rolls back (R7), the database is unchanged and the restore
    point is kept."""
    path = _copy(tmp_path / "data")
    done: list[int] = []
    counted = _calls([])

    def migrate(connection: sqlite3.Connection) -> None:
        if counted(connection) == 2:
            violation(connection)
            done.append(2)

    chain(monkeypatch, LedgerMigration(1, migrate), current=2)
    refused: list[str] = []
    real = regeneration.migrate_ledger

    def spied(connection: sqlite3.Connection, version: int, current: int) -> None:
        try:
            real(connection, version, current)
        except MetadataReportError as error:
            refused.append(str(error))
            raise

    monkeypatch.setattr(regeneration, "migrate_ledger", spied)
    before = _sha(path)
    status, lines, outcome = _run(path)
    assert outcome.refusal is regeneration.Refusal.ROLLED_BACK, lines
    assert lines == [cli.REFUSALS[regeneration.Refusal.ROLLED_BACK]]
    assert (done, refused) == ([2], [_INCOMPATIBLE])
    assert _sha(path) == before
    assert os.listdir(path.parent / "regeneration-backup") == ["epoch-1.sqlite3"]


def test_invalidated_authority_stays_as_history_and_is_not_current(
    owner: OwnerStore,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A migrating run keeps every authority row, and the revocation that
    follows the chain leaves none current, each with its revoked document."""
    owner.write(owner.decide("accept", GENRES, destination="genres"))
    path = _closed(owner)
    connection = sqlite3.connect(path)
    try:
        before = connection.execute(
            "SELECT decision_id,current FROM field_effect_authorities ORDER BY rowid"
        ).fetchall()
    finally:
        connection.close()
    assert any(current for _, current in before)
    chain(monkeypatch, identity(1), current=2)
    assert _run(path)[2].kind == "regenerated"
    connection = sqlite3.connect(path)
    try:
        after = connection.execute(
            "SELECT decision_id,current,document FROM field_effect_authorities "
            "ORDER BY rowid"
        ).fetchall()
    finally:
        connection.close()
    assert [decision for decision, _, _ in after] == [d for d, _ in before]
    assert all(
        current == 0 and json.loads(document)["current"] is False
        for _, current, document in after
    )
    assert _audit(path)[-1] == (1, 2)


# --- The dry run's scratch copy (design ruling on Q1) ---------------------------


def test_the_reserve_counts_the_dry_runs_scratch_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scratch copy the dry run writes fits the one database size the
    reserve holds for it, so a volume short by the copy's size is refused
    at the gate, before the copy and the restore point, with nothing
    changed."""
    measured = _fresh(tmp_path / "measured")
    path = _fresh(tmp_path / "data")
    chain(monkeypatch, identity(1), current=2)
    sizes: list[int] = []
    real_scratch = regeneration._scratch

    @contextmanager
    def scratch(binding: Any) -> Iterator[tuple[int, Path]]:
        with real_scratch(binding) as (directory, path):
            yield directory, path
            sizes.append(
                sum(
                    os.stat(name, dir_fd=directory).st_size
                    for name in os.listdir(directory)
                )
            )

    with monkeypatch.context() as measuring_patch:
        measuring_patch.setattr(regeneration, "_scratch", scratch)
        assert _run(measured)[2].kind == "regenerated"
    size = path.stat().st_size
    # The dry run's copy, then the restore point's.
    copy, _ = sizes
    assert 0 < copy <= size
    needed = regeneration._RESERVE_FILE_SIZES * size
    monkeypatch.setattr(
        regeneration.os,
        "fstatvfs",
        lambda _descriptor: SimpleNamespace(f_bavail=needed - copy, f_frsize=1),
    )
    entered = _scratch_entries(monkeypatch)
    status, lines, outcome = _refuses_unchanged(path)
    assert (outcome.kind, outcome.free, outcome.needed) == (
        "no_space",
        needed - copy,
        needed,
    )
    assert entered == []
    assert not (path.parent / "regeneration-backup").exists()


def _raises_in_the_dry_run(_connection: sqlite3.Connection) -> None:
    raise ValueError("a step failed")


def _changes_the_record(connection: sqlite3.Connection) -> None:
    connection.execute("UPDATE owner_field_answers SET recorded_at=recorded_at+1")


@pytest.mark.parametrize(
    ("migrate", "kind"),
    [
        (lambda _connection: None, "regenerated"),
        (_changes_the_record, "failed"),
        (_raises_in_the_dry_run, None),
    ],
    ids=["success", "refused", "exception"],
)
def test_the_scratch_copy_is_removed_on_every_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    migrate: Callable[[sqlite3.Connection], None],
    kind: str | None,
) -> None:
    path = _copy(tmp_path / "data")
    chain(monkeypatch, LedgerMigration(1, migrate), current=2)
    entered = _scratch_entries(monkeypatch)
    if kind is None:
        before = _sha(path)
        with pytest.raises(ValueError, match="a step failed"):
            _run(path)
        assert _sha(path) == before
    else:
        assert _run(path)[2].kind == kind
    # The dry run's copy; a run that goes ahead takes its restore point
    # through a second one.
    assert entered == ["scratch"] * (2 if kind == "regenerated" else 1)
    assert _no_scratch_left(path)
