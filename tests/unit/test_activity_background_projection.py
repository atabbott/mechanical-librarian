"""Background Activity projection: requests find the store already caught up.

ADR-0082 as amended by #1231 runs the bounded steps a request would take on a
generation-owned thread, outside the readiness window, and leaves any
remainder to the request exactly as before. Each oracle compares the reader's
pages with the canonical projection, and its admitted state with a full
witness, across background catch-up, writes between its steps, refusals,
restart and a Settings cutover.
"""

from __future__ import annotations

import dataclasses
import enum
import hashlib
import json
import sqlite3
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence import field_effect_activity
from mechanical_librarian.adapters.persistence.activity_facts import (
    ActivityFact,
    ActivityFactStore,
    Candidate,
)
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    LibraryConfiguration,
    ReviewWebConfiguration,
    TargetConnectionConfiguration,
)
from mechanical_librarian.application.operation_admission import OperationAdmission
from mechanical_librarian.bootstrap.composition import (
    open_field_effect_activity_readers,
)
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    ReportValueError,
)
from mechanical_librarian.domain.proposal_set import ProposalSet
from mechanical_librarian.domain.shadow_outcome import ShadowOutcomeSet
from tests.support.owner_responsiveness import OwnerResponsivenessFixture
from tests.unit.test_activity_catch_up import (
    _bound,
    _page,
    _readiness,
)
from tests.unit.test_activity_fact_witness import (  # noqa: F401
    _blob_write,
    _full_witness,
    _Ledger,
    _pages,
    _record_decision,
    ledger,
)


def _configuration() -> ApplicationConfiguration:
    return ApplicationConfiguration(
        (TargetConnectionConfiguration("primary", "http://127.0.0.1:1", None),),
        (LibraryConfiguration("books", "primary", "Books", "ebook", None, ()),),
        (),
        (),
        review=ReviewWebConfiguration("http://127.0.0.1:1", "/synthetic/owner.hash"),
    )


def _poll(monkeypatch: pytest.MonkeyPatch) -> None:
    # raising=False: the regression below must fail, not error, before #1231.
    monkeypatch.setattr(
        field_effect_activity, "_ACTIVITY_PROJECTOR_POLL_SECONDS", 0.01, raising=False
    )


def _reader(value: _Ledger) -> SQLiteMetadataReportRepository:
    """A borrower of the ledger's store that a background thread may use."""
    reader = open_repository(
        value.path, create=False, authority=value.root.authority, cross_thread=True
    )
    reader.configure_activity_page_reader(value.store)
    return reader


def _assert_exact(reader: SQLiteMetadataReportRepository, value: _Ledger) -> None:
    """The background's state equals a full witness and an unbounded rebuild."""
    state = value.snapshot()
    full = _full_witness(value.path)
    assert not state.pending
    assert state.witnesses == full.witnesses
    assert state.marks == full.marks
    fresh = ActivityFactStore()
    rebuild = open_repository(value.path, create=False, authority=value.root.authority)
    try:
        rebuild.configure_activity_page_reader(fresh)
        assert _pages(rebuild) == _pages(value.canonical)
        rebuilt = fresh.snapshot()
        assert rebuilt is not None
        assert state.facts == rebuilt.facts
        assert state.ordered == rebuilt.ordered
    finally:
        rebuild.close()
    assert _pages(reader) == _pages(value.canonical)


def _projecting(repository: SQLiteMetadataReportRepository) -> Callable[[], None]:
    """Run the background projector on ``repository``; return its clean stop."""
    stop = threading.Event()
    thread = threading.Thread(
        target=repository.run_activity_fact_projector, args=(stop,), daemon=True
    )
    thread.start()

    def halt() -> None:
        stop.set()
        thread.join(10)
        assert not thread.is_alive()

    return halt


def _settled(repository: SQLiteMetadataReportRepository, timeout: float = 30) -> bool:
    """Wait until the store holds this revision complete, or the reader fell back."""
    activity = repository._field_effect_activity
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with activity._activity_page_cache_lock:
            store = activity._activity_facts
            if store is None:
                return True
            head = store.snapshot()
            stamp = activity._activity_fact_stamp
            if (
                head is not None
                and not head.pending
                and stamp is not None
                and stamp[2] == head.version
                and stamp[0] == activity._activity_projection_revision()
            ):
                return True
        time.sleep(0.005)
    return False


@pytest.fixture
def projected(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[int]]:
    """Count candidates projected on the test's own thread, the request path."""
    owner = threading.get_ident()
    counts: list[int] = []
    refresh = ActivityFactStore.refresh

    def counted(
        store: ActivityFactStore,
        reader: sqlite3.Connection,
        project: Callable[[set[Candidate] | None, int], dict[Candidate, ActivityFact]],
        **options: object,
    ) -> object:
        def observed(
            candidates: set[Candidate] | None, budget: int
        ) -> dict[Candidate, ActivityFact]:
            facts = project(candidates, budget)
            if threading.get_ident() == owner:
                counts.append(len(facts))
            return facts

        return refresh(store, reader, observed, **options)  # type: ignore[arg-type]

    monkeypatch.setattr(ActivityFactStore, "refresh", counted)
    yield counts


def test_the_first_read_after_a_large_change_performs_no_catch_up(
    ledger: _Ledger,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    projected: list[int],
) -> None:
    """Before #1231 the first request projected every changed candidate itself."""
    _bound(monkeypatch, 2)
    _poll(monkeypatch)
    facts = ActivityFactStore()
    history, _, close = open_field_effect_activity_readers(
        _configuration(), ledger.path, ledger.root.authority, None, facts
    )
    reader = history._repository
    try:
        assert _settled(reader)
        before = facts.snapshot()
        assert before is not None
        for index in range(12):
            _record_decision(ledger.root, index)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            head = facts.snapshot()
            if head is not None and head.version != before.version and _settled(reader):
                break
            time.sleep(0.01)
        projected.clear()
        assert type(_page(reader)) is dict
        assert sum(projected) == 0
        assert _pages(reader) == _pages(ledger.canonical)
    finally:
        close()


def test_background_steps_with_writes_between_them_stay_exact(
    ledger: _Ledger,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bound(monkeypatch, 2)
    _poll(monkeypatch)
    reader = _reader(ledger)
    activity = reader._field_effect_activity
    step = activity._activity_fact_step
    writes: list[Callable[[], None]] = [
        ledger.commit,
        lambda: _record_decision(ledger.root, 90),
        ledger.commit,
        lambda: _record_decision(ledger.root, 91),
    ]
    steps: list[threading.Thread] = []

    def interleaved() -> None:
        step()
        steps.append(threading.current_thread())
        if writes:
            writes.pop(0)()

    monkeypatch.setattr(activity, "_activity_fact_step", interleaved)
    for index in range(12):
        _record_decision(ledger.root, index)
    halt = _projecting(reader)
    try:
        assert _settled(reader)
        assert not writes and len(steps) > 4
        assert threading.current_thread() not in steps
        _assert_exact(reader, ledger)
    finally:
        halt()
        reader.close()


def test_a_busy_background_step_is_retried_and_reads_refuse_as_canonical(
    ledger: _Ledger,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bound(monkeypatch, 2)
    _poll(monkeypatch)
    for index in range(3):
        _record_decision(ledger.root, index)
    reader = _reader(ledger)
    activity = reader._field_effect_activity
    step = activity._activity_fact_step
    refusals: list[str] = []

    def observed() -> None:
        try:
            step()
        except Exception as error:
            refusals.append(type(error).__name__)
            raise

    monkeypatch.setattr(activity, "_activity_fact_step", observed)
    lock = sqlite3.connect(ledger.path, timeout=0, isolation_level=None)
    lock.execute("BEGIN EXCLUSIVE")
    halt = _projecting(reader)
    try:
        deadline = time.monotonic() + 10
        while not refusals:
            assert time.monotonic() < deadline, "the projector never met the lock"
            time.sleep(0.005)
        refused = _page(ledger.canonical)
        assert type(refused) is tuple and refused[0] == "refused"
        assert _page(reader) == refused
        lock.execute("ROLLBACK")
        assert _settled(reader)
        assert set(refusals) == {"FieldEffectError"}
        _assert_exact(reader, ledger)
    finally:
        if lock.in_transaction:
            lock.execute("ROLLBACK")
        lock.close()
        halt()
        reader.close()


def test_an_unreadable_document_stops_projection_and_refuses_as_canonical(
    ledger: _Ledger,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bound(monkeypatch, 1)
    _poll(monkeypatch)
    rowid = ledger.root._connection.execute(
        "SELECT min(rowid) FROM field_effect_events"
    ).fetchone()[0]
    _blob_write(ledger, rowid)
    expected = _pages(ledger.canonical)
    assert any(type(page) is tuple for page in expected)
    borrower = open_repository(
        ledger.path, create=False, authority=ledger.root.authority, cross_thread=True
    )
    (document,) = ledger.root._connection.execute(
        "SELECT document FROM metadata_reports LIMIT 1"
    ).fetchone()
    # A hand-off is released when projection stops short of completing.
    startup = {"unused": MetadataReport.from_json(document)}
    borrower.configure_activity_page_reader(
        ActivityFactStore(), startup_reports=startup
    )
    halt = _projecting(borrower)
    try:
        assert _settled(borrower)
        assert borrower._field_effect_activity._activity_facts is None
        assert _pages(borrower) == expected
    finally:
        halt()
        borrower.close()
    assert startup == {}


def test_a_tampered_report_refuses_and_no_refusal_is_retained(
    ledger: _Ledger,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reuse is keyed by exact bytes; a document that refused parses again."""
    reuse = ledger.reader._field_effect_activity._reused_report
    assert reuse is not None
    (document,) = ledger.root._connection.execute(
        "SELECT document FROM metadata_reports ORDER BY report_id DESC LIMIT 1"
    ).fetchone()
    parse = MetadataReport.from_json
    parsed: list[str] = []

    def counted(value: str) -> MetadataReport:
        parsed.append(value)
        return parse(value)

    monkeypatch.setattr(MetadataReport, "from_json", counted)
    value = json.loads(document)
    value["report_id"] = value["report_id"] + "\x00"
    tampered = (document[:-1], json.dumps(value), document + "\x00", document[1:])
    for candidate in tampered:
        for attempt in range(2):
            with pytest.raises(ReportValueError) as refused:
                reuse(candidate)
            with pytest.raises(ReportValueError) as canonical:
                parse(candidate)
            assert str(refused.value) == str(canonical.value)
            assert parsed.count(candidate) == attempt + 1
    first = reuse(document)
    assert reuse(document) is first
    assert first == parse(document)
    assert parsed.count(document) <= 1


def test_a_startup_report_is_reused_only_for_its_own_bytes(
    ledger: _Ledger,  # noqa: F811
) -> None:
    rows = ledger.root._connection.execute(
        "SELECT document FROM metadata_reports ORDER BY report_id LIMIT 2"
    ).fetchall()
    first, second = (row[0] for row in rows)
    known = MetadataReport.from_json(first)
    borrower = open_repository(
        ledger.path, create=False, authority=ledger.root.authority
    )
    try:
        # A digest naming different bytes is never trusted: it parses them.
        startup = {
            hashlib.sha256(first.encode()).hexdigest(): known,
            hashlib.sha256(second.encode()).hexdigest(): known,
        }
        borrower.configure_activity_page_reader(
            ActivityFactStore(), startup_reports=startup
        )
        reuse = borrower._field_effect_activity._reused_report
        assert reuse is not None
        assert reuse(first) is known
        reparsed = reuse(second)
        assert reparsed is not known
        assert reparsed == MetadataReport.from_json(second)
        assert reparsed.to_json() == second
    finally:
        borrower.close()


def _immutable(value: object, seen: set[int]) -> bool:
    if id(value) in seen or value is None:
        return True
    seen.add(id(value))
    if isinstance(value, (str, int, float, bytes, enum.Enum)):
        return True
    if type(value) in {tuple, frozenset}:
        return all(_immutable(item, seen) for item in value)  # type: ignore[attr-defined]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return type(value).__dataclass_params__.frozen and all(  # type: ignore[attr-defined]
            _immutable(getattr(value, field.name), seen)
            for field in dataclasses.fields(value)
        )
    return False


def test_reused_kinds_are_deeply_immutable(tmp_path: Path) -> None:
    """A retained object is handed out as is, so nothing in it may change."""
    root = tmp_path / "owner"
    root.mkdir(mode=0o700)
    fixture = OwnerResponsivenessFixture(root, 8, mixed=True)
    try:
        fixture.repository.enter_shadow_preview(1)
        for index, case in enumerate(fixture.cases):
            fixture.repository.persist_shadow_outcome(case.review_case_id, index + 2)
        connection = fixture.repository._connection
        for table, parse in (
            ("metadata_reports", MetadataReport.from_json),
            ("proposal_sets", ProposalSet.from_json),
            ("shadow_decision_outcomes", ShadowOutcomeSet.from_json),
        ):
            documents = connection.execute(f"SELECT document FROM {table}").fetchall()
            assert documents
            for (document,) in documents:
                assert _immutable(parse(document), set()), table
    finally:
        fixture.close()
        fixture.repository.close()


def test_restart_parses_each_report_once_and_releases_the_hand_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Validation parses each report once and hands it to Activity (#1231)."""
    _poll(monkeypatch)
    root = tmp_path / "owner"
    root.mkdir(mode=0o700)
    fixture = OwnerResponsivenessFixture(root, 8, mixed=True)
    configuration = fixture.configuration
    documents = {
        document
        for (document,) in fixture.repository._connection.execute(
            "SELECT document FROM metadata_reports"
        )
    }
    considered = fixture.repository._connection.execute(
        "SELECT count(DISTINCT report_id) FROM cover_considerations"
    ).fetchone()[0]
    fixture.close()
    fixture.repository.close()
    assert considered > 1
    parsed: Counter[str] = Counter()
    parse = MetadataReport.from_json

    def counted(document: str) -> MetadataReport:
        parsed[document] += 1
        return parse(document)

    monkeypatch.setattr(MetadataReport, "from_json", counted)
    startup: dict[str, MetadataReport] = {}
    facts = ActivityFactStore()
    owner = open_repository(
        fixture.database,
        cross_thread=True,
        admission=OperationAdmission(),
        validated_reports=startup,
    )
    try:
        assert len(startup) == considered
        assert set(parsed) <= documents and set(parsed.values()) == {1}
        history, _, close = open_field_effect_activity_readers(
            configuration, fixture.database, owner.authority, None, facts, startup
        )
        try:
            assert _settled(history._repository)
            state = facts.snapshot()
            assert state is not None and state.facts and not state.pending
            assert set(parsed.values()) == {1}
            assert startup == {}
        finally:
            close()
        # Without the hand-off the same projection parses reports again.
        parsed.clear()
        control = open_repository(
            fixture.database, create=False, authority=owner.authority
        )
        try:
            control.configure_activity_page_reader(ActivityFactStore())
            control.initialize_activity_facts()
            assert parsed and set(parsed) <= documents
        finally:
            control.close()
    finally:
        owner.close()


def test_readiness_runs_inside_a_window_no_background_step_enters(
    ledger: _Ledger,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _poll(monkeypatch)
    window = threading.Lock()
    history = open_repository(
        ledger.path, create=False, authority=ledger.root.authority, cross_thread=True
    )
    permissions = open_repository(
        ledger.path, create=False, authority=ledger.root.authority, cross_thread=True
    )
    history.configure_activity_page_reader(ActivityFactStore(), readiness_window=window)
    permissions.configure_activity_page_reader(readiness_window=window)
    activity = history._field_effect_activity
    step = activity._activity_fact_step
    steps: list[float] = []

    def timed() -> None:
        steps.append(time.monotonic())
        step()

    monkeypatch.setattr(activity, "_activity_fact_step", timed)
    entered, release = threading.Event(), threading.Event()
    held: list[tuple[float, float]] = []

    def check(ready: bool | None) -> dict[str, object]:
        start = time.monotonic()
        entered.set()
        assert release.wait(10)
        held.append((start, time.monotonic()))
        return {"backup_ready": ready}

    reading = threading.Thread(
        target=permissions.field_effect_permission_readiness_snapshot, args=(check,)
    )
    reading.start()
    halt: Callable[[], None] | None = None
    try:
        assert entered.wait(10)
        halt = _projecting(history)
        time.sleep(0.2)
        assert steps == []
        release.set()
        reading.join(10)
        assert _settled(history)
        assert steps and all(at > held[0][1] for at in steps)
        assert _readiness(permissions) == _readiness(ledger.canonical)
        assert _pages(history) == _pages(ledger.canonical)
    finally:
        release.set()
        reading.join(10)
        if halt is not None:
            halt()
        history.close()
        permissions.close()


def test_a_cutover_hands_the_store_to_the_next_generation_exactly(
    ledger: _Ledger,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two generations overlap on one store; the first stops mid-catch-up."""
    _bound(monkeypatch, 2)
    _poll(monkeypatch)
    configuration = _configuration()
    first, _, close_first = open_field_effect_activity_readers(
        configuration, ledger.path, ledger.root.authority, None, ledger.store
    )
    activity = first._repository._field_effect_activity
    step = activity._activity_fact_step
    stepped = threading.Event()

    def observed() -> None:
        step()
        stepped.set()

    monkeypatch.setattr(activity, "_activity_fact_step", observed)
    for index in range(12):
        _record_decision(ledger.root, index)
    assert stepped.wait(15)
    second, _, close_second = open_field_effect_activity_readers(
        configuration, ledger.path, ledger.root.authority, None, ledger.store
    )
    try:
        close_first()
        assert _settled(second._repository)
        _assert_exact(second._repository, ledger)
    finally:
        close_second()


def test_shutdown_stops_the_projector_before_its_admission_closes(
    ledger: _Ledger,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Close waits for the one step in progress, then nothing runs again."""
    _bound(monkeypatch, 2)
    _poll(monkeypatch)
    history, _, close = open_field_effect_activity_readers(
        _configuration(), ledger.path, ledger.root.authority, None, ActivityFactStore()
    )
    repository = history._repository
    activity = repository._field_effect_activity
    assert _settled(repository)
    step = activity._activity_fact_step
    entered, release = threading.Event(), threading.Event()
    calls: list[None] = []

    def blocking() -> None:
        calls.append(None)
        entered.set()
        assert release.wait(10)
        step()

    monkeypatch.setattr(activity, "_activity_fact_step", blocking)
    ledger.commit()
    assert entered.wait(10)
    admission = repository.operation_admission
    closing = threading.Thread(target=close)
    closing.start()
    time.sleep(0.1)
    assert closing.is_alive() and not admission._closed
    release.set()
    closing.join(10)
    assert not closing.is_alive() and admission._closed
    assert len(calls) == 1
    assert not any(
        thread.name == "activity-fact-projector" and thread.is_alive()
        for thread in threading.enumerate()
    )


def test_a_stop_requested_while_the_projector_waits_takes_no_further_step(
    ledger: _Ledger,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _poll(monkeypatch)
    reader = _reader(ledger)
    activity = reader._field_effect_activity
    steps: list[None] = []
    monkeypatch.setattr(activity, "_activity_fact_step", lambda: steps.append(None))
    ledger.commit()
    stop = threading.Event()
    thread = threading.Thread(
        target=reader.run_activity_fact_projector, args=(stop,), daemon=True
    )
    try:
        with activity._activity_page_cache_lock:
            thread.start()
            time.sleep(0.1)
            stop.set()
        thread.join(10)
        assert not thread.is_alive()
        assert steps == []
    finally:
        stop.set()
        thread.join(10)
        reader.close()
