"""Bounded Activity catch-up: a writer waits behind one step, never all of it.

ADR-0082 as amended by #1225 projects at most ``_PROJECT_CANDIDATES`` affected
candidates in one read transaction and leaves the rest pending, and a state
with pending candidates is never served. Each oracle here compares the
borrower's pages with the canonical projection, and its admitted state with a
full witness and an unbounded rebuild, across bounded steps, writes committed
between them and the refusals a step can meet.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence import activity_facts
from mechanical_librarian.adapters.persistence.activity_facts import (
    ActivityFact,
    ActivityFactStore,
    Candidate,
)
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.domain.field_effect import FieldEffectError
from tests.support.field_effects import (  # noqa: F401
    _EffectEnvironment,
    effects,
)
from tests.unit.test_activity_fact_witness import (  # noqa: F401
    _blob_write,
    _full_witness,
    _Ledger,
    _outcome,
    _pages,
    _record_decision,
    ledger,
)
from tests.unit.test_activity_reprojection import (
    test_an_invalid_isbn_outcome_refuses_only_the_reads_that_select_it as _isbn,
)

TOUCH = (
    "UPDATE metadata_policy_transitions SET updated_at=updated_at+1 "
    "WHERE generation=(SELECT min(generation) FROM metadata_policy_transitions)"
)
TOUCHED = (
    "SELECT updated_at FROM metadata_policy_transitions "
    "WHERE generation=(SELECT min(generation) FROM metadata_policy_transitions)"
)


def _bound(monkeypatch: pytest.MonkeyPatch, candidates: int) -> None:
    # raising=False: the same test must fail, not error, before the bound exists.
    monkeypatch.setattr(
        activity_facts, "_PROJECT_CANDIDATES", candidates, raising=False
    )


def _borrower(value: _Ledger) -> SQLiteMetadataReportRepository:
    borrower = open_repository(value.path, create=False, authority=value.root.authority)
    borrower.configure_activity_page_reader(ActivityFactStore())
    return borrower


def _page(repository: SQLiteMetadataReportRepository) -> object:
    return _outcome(
        lambda: repository.field_effect_activity_page(
            search=None, event_type="all", occurred_after=0, page=1, page_size=25
        )
    )


def _snapshot_page(repository: SQLiteMetadataReportRepository) -> object:
    """The page as the composed application reads it: inside one snapshot."""
    return _outcome(
        lambda: repository.field_effect_activity_snapshot(
            lambda: {
                "summary": repository.field_effect_activity_summary(),
                **repository.field_effect_activity_page(
                    search=None,
                    event_type="all",
                    occurred_after=0,
                    page=1,
                    page_size=25,
                ),
            }
        )
    )


def _pending(value: _Ledger) -> frozenset[Candidate]:
    return value.snapshot().pending


def _locked(path: Path) -> bool:
    """Whether a new reader is refused SHARED, as it is while PENDING is held."""
    probe = sqlite3.connect(path, timeout=0)
    try:
        probe.execute("SELECT count(*) FROM sqlite_schema").fetchone()
        return False
    except sqlite3.OperationalError:
        return True
    finally:
        probe.close()


@pytest.mark.parametrize("route", ("page", "snapshot"))
def test_a_waiting_writer_waits_behind_one_bounded_step_at_most(
    ledger: _Ledger,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    route: str,
) -> None:
    """A COMMIT under the product busy timeout is never starved by catch-up.

    The writer starts its COMMIT inside the first projection of a fresh
    borrower, and the read transaction it finds then projects at most one
    bounded step: a waiting writer holds PENDING, so the next step cannot
    start until the writer has committed. Before the bound the one read
    transaction projected every candidate while the writer waited (#1225).
    The composed application nests its page in one snapshot, which must end
    before any step runs and retry as a whole.
    """
    bound = 2
    _bound(monkeypatch, bound)
    borrower = _borrower(ledger)
    writer = open_repository(
        ledger.path, create=False, authority=ledger.root.authority, cross_thread=True
    )
    connection = writer._connection
    assert connection.execute("PRAGMA busy_timeout").fetchone() == (5000,)
    written = connection.execute(TOUCHED).fetchone()[0] + 1
    outcome: list[str] = []

    def write() -> None:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(TOUCH)
        try:
            connection.execute("COMMIT")
            outcome.append("committed")
        except sqlite3.OperationalError as error:
            connection.execute("ROLLBACK")
            outcome.append(str(error))

    thread = threading.Thread(target=write)
    projected: list[int] = []
    waited: list[int] = []
    refresh = ActivityFactStore.refresh

    def counted(
        store: ActivityFactStore,
        reader: sqlite3.Connection,
        project: Callable[[set[Candidate] | None, int], dict[Candidate, ActivityFact]],
        **options: object,
    ) -> object:
        projected.append(0)

        def observed(
            candidates: set[Candidate] | None, budget: int
        ) -> dict[Candidate, ActivityFact]:
            if waited and len(projected) - 1 > waited[0]:
                # A later step reads only once the writer has committed.
                assert reader.execute(TOUCHED).fetchone() == (written,)
            elif not waited:
                waited.append(len(projected) - 1)
                thread.start()
                deadline = time.monotonic() + 10
                while not _locked(ledger.path):
                    assert time.monotonic() < deadline, "the writer never waited"
                    time.sleep(0.001)
            facts = project(candidates, budget)
            projected[-1] += len(facts)
            return facts

        return refresh(store, reader, observed, **options)  # type: ignore[arg-type]

    monkeypatch.setattr(ActivityFactStore, "refresh", counted)
    try:
        served = _page(borrower) if route == "page" else _snapshot_page(borrower)
        thread.join(10)
        assert outcome == ["committed"]
        total = len(ledger.snapshot().facts)
        assert total > 5 * bound
        assert waited and projected[waited[0]] <= bound
        assert sum(projected) >= total
        monkeypatch.setattr(ActivityFactStore, "refresh", refresh)
        assert type(served) is dict
        # The read itself finished the catch-up and was served from the store.
        store = borrower._field_effect_activity._activity_facts
        assert store is not None
        state = store.snapshot()
        assert state is not None and not state.pending
        assert _pages(borrower) == _pages(ledger.canonical)
    finally:
        borrower.close()
        writer.close()


@pytest.mark.parametrize("bound", (1, 4, 26))
def test_bounded_construction_admits_exactly_one_unbounded_projection(
    ledger: _Ledger,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    bound: int,
) -> None:
    unbounded = ledger.snapshot()
    _bound(monkeypatch, bound)
    borrower = _borrower(ledger)
    try:
        assert len(unbounded.facts) > bound
        assert _pages(borrower) == _pages(ledger.canonical)
        store = borrower._field_effect_activity._activity_facts
        assert store is not None
        admitted = store.snapshot()
        assert admitted is not None and not admitted.pending
        assert admitted.facts == unbounded.facts
        assert admitted.ordered == unbounded.ordered
        assert admitted.witnesses == _full_witness(ledger.path).witnesses
    finally:
        borrower.close()


def test_a_pending_state_holds_exact_facts_and_is_never_served(
    ledger: _Ledger,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One step admits exact facts for what it projected, and nothing is served
    from it: the first two attempts retry, and the last serves canonical."""
    unbounded = ledger.snapshot()
    _bound(monkeypatch, 3)
    borrower = _borrower(ledger)
    activity = borrower._field_effect_activity
    try:
        activity._activity_fact_step()
        store = activity._activity_facts
        assert store is not None
        state = store.snapshot()
        assert state is not None and len(state.pending) == len(unbounded.facts) - 3
        assert set(state.facts).isdisjoint(state.pending)
        assert state.facts == {key: unbounded.facts[key] for key in state.facts}
        assert state.witnesses == _full_witness(ledger.path).witnesses
        admissions: list[object] = []
        admit = activity._admit_activity_facts

        def counted(store: ActivityFactStore) -> object:
            admitted = admit(store)
            admissions.append(admitted)
            return admitted

        monkeypatch.setattr(activity, "_catch_up_activity_facts", lambda: None)
        monkeypatch.setattr(activity, "_admit_activity_facts", counted)
        assert _page(borrower) == _page(ledger.canonical)
        assert len(admissions) == 3
        assert activity._activity_facts is store
        current = store.snapshot()
        assert current is not None and current.pending
        monkeypatch.undo()
        _bound(monkeypatch, 3)
        assert _pages(borrower) == _pages(ledger.canonical)
        current = store.snapshot()
        assert current is not None and not current.pending
        assert current.facts == unbounded.facts
        assert current.ordered == unbounded.ordered
    finally:
        borrower.close()


def test_writes_committed_between_bounded_steps_stay_exact(
    ledger: _Ledger,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bound(monkeypatch, 2)
    activity = ledger.reader._field_effect_activity
    step = activity._activity_fact_step
    writes: list[Callable[[], None]] = [
        ledger.commit,
        lambda: _record_decision(ledger.root, 90),
        ledger.commit,
        lambda: _record_decision(ledger.root, 91),
    ]
    steps: list[int] = []

    def interleaved() -> None:
        step()
        steps.append(len(_pending(ledger)))
        if writes:
            writes.pop(0)()

    monkeypatch.setattr(activity, "_activity_fact_step", interleaved)
    for index in range(12):
        _record_decision(ledger.root, index)
    ledger.read()
    assert not writes and len(steps) > 4
    ledger.read()
    assert not _pending(ledger)
    ledger.assert_exact()


def test_a_busy_step_refuses_exactly_as_the_canonical_read(
    ledger: _Ledger,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bound(monkeypatch, 2)
    for index in range(3):
        _record_decision(ledger.root, index)
    lock = sqlite3.connect(ledger.path, timeout=0, isolation_level=None)
    try:
        lock.execute("BEGIN EXCLUSIVE")
        refused = _page(ledger.canonical)
        assert type(refused) is tuple and refused[0] == "refused"
        assert _page(ledger.reader) == refused
    finally:
        lock.execute("ROLLBACK")
        lock.close()
    ledger.read()
    assert not _pending(ledger)
    ledger.assert_exact()


def test_an_unreadable_document_refuses_exactly_across_bounded_steps(
    ledger: _Ledger,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bound(monkeypatch, 1)
    rowid = ledger.root._connection.execute(
        "SELECT min(rowid) FROM field_effect_events"
    ).fetchone()[0]
    _blob_write(ledger, rowid)
    expected = _pages(ledger.canonical)
    assert any(type(page) is tuple for page in expected)
    borrower = _borrower(ledger)
    try:
        assert _pages(borrower) == expected
    finally:
        borrower.close()


@pytest.mark.parametrize("fresh", (False, True), ids=("reprojected", "constructed"))
@pytest.mark.parametrize(
    "identifier", ("\x00z", ""), ids=("nul_identifier", "mismatched_identifier")
)
def test_an_invalid_isbn_outcome_refuses_exactly_one_candidate_per_step(
    effects: _EffectEnvironment,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    identifier: str,
    fresh: bool,
) -> None:
    _bound(monkeypatch, 1)
    _isbn(effects, identifier, fresh)


def test_witness_encoding_is_injective_where_lossy_encodings_collide() -> None:
    """Each pair collides under a text, JSON or repr-by-string encoding."""
    rows: list[list[object]] = [
        [b"1"],
        ["b'1'"],
        [None],
        ["None"],
        ["null"],
        [float("inf")],
        ["inf"],
        [2**63 - 1],
        [float(2**63 - 1)],
        [""],
        [b""],
        ["a,b"],
        ["a", "b"],
        ["é"],
        ["\\xe9"],
        [[1], [2]],
        [[1, 2]],
        ["1", None],
        ["1, None"],
    ]
    assert len({activity_facts._encoded(row) for row in rows}) == len(rows)


def test_witness_bytes_count_every_value_and_binding(
    ledger: _Ledger,  # noqa: F811
) -> None:
    """The 64 MiB bound counts each witness's key, value and bindings."""
    state = ledger.snapshot()
    for table, rows in state.witnesses.items():
        assert state.witness_bytes[table] == sum(
            len(key[0])
            + len(key[1])
            + len(witness.value)
            + sum(len(k[0]) + len(k[1]) for k in witness.dependencies | witness.aliases)
            + sum(len(kind) + len(identity) for kind, identity in witness.candidates)
            for key, witness in rows.items()
        )


def test_a_step_whose_file_moves_during_its_refresh_refuses_and_keeps_its_stamp(
    ledger: _Ledger,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A step publishes no stamp for a file it did not read throughout."""
    _bound(monkeypatch, 3)
    borrower = _borrower(ledger)
    activity = borrower._field_effect_activity
    try:
        activity._activity_fact_step()
        stamp = activity._activity_fact_stamp
        assert stamp is not None
        identity = activity._activity_database_identity
        sampled: list[object] = []

        def moving() -> object:
            sampled.append(identity())
            return sampled[-1] if len(sampled) == 1 else ("moved", sampled[-1])

        monkeypatch.setattr(activity, "_activity_database_identity", moving)
        with pytest.raises(FieldEffectError) as refused:
            activity._activity_fact_step()
        assert refused.value.code == "persistence"
        assert activity._activity_fact_stamp is stamp
        monkeypatch.setattr(activity, "_activity_database_identity", identity)
        assert _pages(borrower) == _pages(ledger.canonical)
    finally:
        borrower.close()
