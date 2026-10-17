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
import json
import sqlite3
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import nullcontext
from pathlib import Path
from typing import cast
from unittest.mock import Mock, patch

import pytest

from mechanical_librarian.adapters.persistence import field_effect_activity
from mechanical_librarian.adapters.persistence.activity_documents import (
    ACTIVITY_DOCUMENT_CHARACTERS,
    ActivityDocuments,
)
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
from mechanical_librarian.bootstrap import composition
from mechanical_librarian.bootstrap.composition import (
    open_field_effect_activity_readers,
)
from mechanical_librarian.domain.field_effect import FieldEffectError
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    ReportValueError,
)
from mechanical_librarian.domain.proposal_set import ProposalSet
from mechanical_librarian.domain.review_case import ReviewCase, ReviewCaseValueError
from mechanical_librarian.domain.shadow_outcome import ShadowOutcomeSet
from mechanical_librarian.infrastructure.first_boot import FirstBootMode
from mechanical_librarian.infrastructure.settings_authority import ReviewBootstrap
from tests.support.automatic_decisions import _automatic_decisions
from tests.support.automatic_no_change import _automatic_no_change_inputs
from tests.support.field_effects import _EffectEnvironment, effects  # noqa: F401
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
    _outcome,
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
    reader = cast("SQLiteMetadataReportRepository", history._repository)
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
    borrower.configure_activity_page_reader(ActivityFactStore())
    halt = _projecting(borrower)
    try:
        assert _settled(borrower)
        assert borrower._field_effect_activity._activity_facts is None
        assert _pages(borrower) == expected
    finally:
        halt()
        borrower.close()


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


@pytest.mark.parametrize(
    ("column", "tamper"),
    [
        pytest.param("document_digest", repr("0" * 64), id="digest"),
        pytest.param("item_id", "item_id || '-moved'", id="envelope"),
    ],
)
def test_a_kept_report_still_answers_to_its_row(
    ledger: _Ledger,  # noqa: F811
    column: str,
    tamper: str,
) -> None:
    """A row whose digest or envelope no longer matches its bytes refuses alike."""
    reader = ledger.reader
    assert reader._field_effect_activity._reused_report is not None
    before = _pages(reader)
    connection = ledger.root._connection
    connection.execute("DROP TRIGGER metadata_reports_no_update")
    connection.execute(f"UPDATE metadata_reports SET {column}={tamper}")
    connection.commit()
    expected = _pages(ledger.canonical)
    refused = [page for page in expected if type(page) is tuple]
    assert len(refused) > sum(type(page) is tuple for page in before)
    assert _pages(reader) == expected


def test_a_report_is_reused_only_for_its_own_bytes(
    ledger: _Ledger,  # noqa: F811
) -> None:
    rows = ledger.root._connection.execute(
        "SELECT document FROM metadata_reports ORDER BY report_id LIMIT 2"
    ).fetchall()
    first, second = (row[0] for row in rows)
    documents = ActivityDocuments()
    known = documents.report(first)
    assert documents.report(first) is known
    reparsed = documents.report(second)
    assert reparsed is not known
    assert reparsed == MetadataReport.from_json(second)
    assert reparsed.to_json() == second


def test_the_document_store_keeps_its_character_bound(
    ledger: _Ledger,  # noqa: F811
) -> None:
    rows = [
        row[0]
        for row in ledger.root._connection.execute(
            "SELECT document FROM metadata_reports ORDER BY report_id LIMIT 3"
        )
    ]
    assert len(rows) == 3
    bound = len(rows[0]) + max(len(rows[1]), len(rows[2]))
    documents = ActivityDocuments(bound)
    held = [documents.report(document) for document in rows[:2]]
    assert [documents.report(document) for document in rows[:2]] == held
    assert all(
        documents.report(document) is value
        for document, value in zip(rows[:2], held, strict=True)
    )
    documents.report(rows[0])
    documents.report(rows[2])
    assert documents._characters <= bound
    # The least recently used entry went first.
    assert documents.report(rows[0]) is held[0]
    assert documents.report(rows[1]) is not held[1]
    # A document over the whole bound is not kept, and evicts nothing.
    small, large = sorted(rows, key=len)[:: len(rows) - 1]
    assert len(small) < len(large)
    narrow = ActivityDocuments(len(small))
    kept = narrow.report(small)
    assert narrow.report(large) is not narrow.report(large)
    assert narrow.report(small) is kept
    assert narrow._characters == len(small)
    assert ACTIVITY_DOCUMENT_CHARACTERS == 48 * 1024 * 1024


def test_a_case_hands_out_the_proposal_set_it_validated(tmp_path: Path) -> None:
    """Activity takes a case's proposal set from the case, not a second parse."""
    root = tmp_path / "owner"
    root.mkdir(mode=0o700)
    fixture = OwnerResponsivenessFixture(root, 2, mixed=True)
    try:
        (document,) = fixture.repository._connection.execute(
            "SELECT document FROM review_cases LIMIT 1"
        ).fetchone()
    finally:
        fixture.close()
        fixture.repository.close()
    with patch.object(ProposalSet, "from_json", wraps=ProposalSet.from_json) as parse:
        case = ReviewCase.from_json(document)
        assert case.proposal_set is case.proposal_set
        assert case.proposals == case.proposals
    assert parse.call_count == 1
    assert case.proposal_set == ProposalSet.from_json(case.proposal_document)
    assert _immutable(case.proposal_set, set())
    value = json.loads(document)
    proposal = json.loads(value["proposal_document"])
    proposal["schema_version"] = "unknown"
    value["proposal_document"] = json.dumps(proposal, sort_keys=True)
    for tampered in (json.dumps(value, sort_keys=True), document[:-1]):
        for _ in range(2):
            with pytest.raises(ReviewCaseValueError):
                ReviewCase.from_json(tampered)


def _immutable(value: object, seen: set[int]) -> bool:
    if id(value) in seen or value is None:
        return True
    seen.add(id(value))
    if isinstance(value, (str, int, float, bytes, enum.Enum)):
        return True
    if type(value) in {tuple, frozenset}:
        return all(_immutable(item, seen) for item in value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return type(value).__dataclass_params__.frozen and all(
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


@pytest.mark.parametrize("history", ["covers", "decisions"])
def test_restart_parses_each_document_once_and_projects_as_a_cold_reader(
    history: str,
    tmp_path: Path,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What validation parsed, Activity reuses, and reuse changes nothing (#1231).

    Validation parses the reports cover considerations name; Activity parses
    the cases, proposals and reports of owner decisions.
    """
    _poll(monkeypatch)
    if history == "covers":
        root = tmp_path / "owner"
        root.mkdir(mode=0o700)
        fixture = OwnerResponsivenessFixture(root, 8, mixed=True)
        configuration, path = fixture.configuration, fixture.database
        fixture.close()
        fixture.repository.close()
        expected = {"MetadataReport"}
    else:
        environment = cast("_EffectEnvironment", request.getfixturevalue("effects"))
        report, category, option, _ = _automatic_no_change_inputs("exact_title")
        assert _automatic_decisions(
            environment, report, category, option, qualified=True
        )
        configuration, path = _configuration(), environment.database_path
        environment.repository.close()
        expected = {"MetadataReport", "ReviewCase", "ProposalSet"}
    parsed: Counter[tuple[str, str]] = Counter()
    for kind in (MetadataReport, ReviewCase, ProposalSet, ShadowOutcomeSet):
        parse = kind.from_json

        def counted(
            document: str, *options: object, _kind: str = kind.__name__, _parse=parse
        ) -> object:
            parsed[(_kind, document)] += 1
            return _parse(document, *options)

        monkeypatch.setattr(kind, "from_json", counted)
    documents = ActivityDocuments()
    facts = ActivityFactStore()
    owner = open_repository(
        path,
        cross_thread=True,
        admission=OperationAdmission(),
        activity_documents=documents,
    )
    try:
        validated = set(parsed)
        assert bool(validated) is (history == "covers")
        readers, _, close = open_field_effect_activity_readers(
            configuration, path, owner.authority, None, facts, documents
        )
        try:
            reader = cast("SQLiteMetadataReportRepository", readers._repository)
            assert _settled(reader)
            handed = _pages(reader)
        finally:
            close()
        state = facts.snapshot()
        assert state is not None and state.facts and not state.pending
        assert expected <= {kind for kind, _ in parsed}
        assert set(parsed.values()) == {1}
        # The oracle: a cold reader holding nothing projects the same pages.
        parsed.clear()
        cold = ActivityFactStore()
        control = open_repository(path, create=False, authority=owner.authority)
        try:
            control.configure_activity_page_reader(cold)
            assert _pages(control) == handed
            rebuilt = cold.snapshot()
            assert rebuilt is not None
            assert rebuilt.facts == state.facts and rebuilt.ordered == state.ordered
            assert expected <= {kind for kind, _ in parsed}
            assert validated <= set(parsed)
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
        borrower = cast("SQLiteMetadataReportRepository", second._repository)
        assert _settled(borrower)
        _assert_exact(borrower, ledger)
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
    repository = cast("SQLiteMetadataReportRepository", history._repository)
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
    assert admission is not None
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


def test_readiness_never_waits_for_a_request_holding_the_history_reader(
    ledger: _Ledger,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The projector never holds the window while it waits for the cache lock.

    Before the fix it took the window first, so readiness waited out the rest
    of whatever history request held the cache lock.
    """
    _poll(monkeypatch)
    history, permissions, close = open_field_effect_activity_readers(
        _configuration(), ledger.path, ledger.root.authority, None, ActivityFactStore()
    )
    reader = cast("SQLiteMetadataReportRepository", history._repository)
    checker = cast("SQLiteMetadataReportRepository", permissions._repository)
    try:
        assert _settled(reader)
        held, release = threading.Event(), threading.Event()

        def request() -> None:
            with reader._field_effect_activity._activity_page_cache_lock:
                held.set()
                release.wait(10)

        holder = threading.Thread(target=request)
        holder.start()
        try:
            assert held.wait(10)
            ledger.commit()
            time.sleep(0.2)
            start = time.monotonic()
            assert _readiness(checker) == _readiness(ledger.canonical)
            assert time.monotonic() - start < 1.5
            assert holder.is_alive()
        finally:
            release.set()
            holder.join(10)
    finally:
        close()


def test_both_readers_of_a_generation_share_one_window_and_one_store(
    ledger: _Ledger,  # noqa: F811
) -> None:
    history, permissions, close = open_field_effect_activity_readers(
        _configuration(), ledger.path, ledger.root.authority, None, ActivityFactStore()
    )
    try:
        first = history._repository._field_effect_activity
        second = permissions._repository._field_effect_activity
        assert first._readiness_window is second._readiness_window
        assert first._reused_report.__self__ is second._reused_report.__self__
    finally:
        close()
    _, other, close_other = open_field_effect_activity_readers(
        _configuration(), ledger.path, ledger.root.authority, None, ActivityFactStore()
    )
    try:
        third = other._repository._field_effect_activity
        assert third._readiness_window is not second._readiness_window
    finally:
        close_other()


def test_the_projector_steps_as_background_work(
    ledger: _Ledger,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _poll(monkeypatch)
    history, _, close = open_field_effect_activity_readers(
        _configuration(), ledger.path, ledger.root.authority, None, ActivityFactStore()
    )
    repository = cast("SQLiteMetadataReportRepository", history._repository)
    activity = repository._field_effect_activity
    admission = repository.operation_admission
    assert admission is not None
    step = activity._activity_fact_step
    classes: list[object] = []

    def observed() -> None:
        classes.append(admission._classification.get(None))
        step()

    monkeypatch.setattr(activity, "_activity_fact_step", observed)
    try:
        ledger.commit()
        assert _settled(repository)
        assert classes and set(classes) == {"background"}
    finally:
        close()


def _events(captured: str) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in captured.splitlines()
        if line.startswith("{") and '"activity_projector"' in line
    ]


def test_an_unexpected_projector_failure_is_logged_without_its_values(
    ledger: _Ledger,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _poll(monkeypatch)
    reader = _reader(ledger)
    activity = reader._field_effect_activity

    def failing() -> None:
        raise RuntimeError("/private/library/path secret")

    monkeypatch.setattr(activity, "_activity_fact_step", failing)
    ledger.commit()
    stop = threading.Event()
    thread = threading.Thread(target=reader.run_activity_fact_projector, args=(stop,))
    try:
        thread.start()
        thread.join(10)
        assert not thread.is_alive()
        captured = capsys.readouterr().err
        assert _events(captured) == [
            {
                "code": "unexpected",
                "error": "RuntimeError",
                "event": "activity_projector",
                "level": "warning",
            }
        ]
        assert "secret" not in captured and "Traceback" not in captured
        # Requests still catch up themselves.
        assert _pages(reader) == _pages(ledger.canonical)
    finally:
        stop.set()
        thread.join(10)
        reader.close()


def test_a_stopped_projector_logs_its_exit(
    ledger: _Ledger,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _poll(monkeypatch)
    reader = _reader(ledger)
    try:
        _projecting(reader)()
        assert _events(capsys.readouterr().err) == [
            {"code": "stopped", "event": "activity_projector", "level": "info"}
        ]
    finally:
        reader.close()


def test_a_projector_outliving_its_join_bound_is_logged(
    ledger: _Ledger,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _poll(monkeypatch)
    monkeypatch.setattr(composition, "_ACTIVITY_PROJECTOR_JOIN_SECONDS", 0.1)
    history, _, close = open_field_effect_activity_readers(
        _configuration(), ledger.path, ledger.root.authority, None, ActivityFactStore()
    )
    repository = cast("SQLiteMetadataReportRepository", history._repository)
    activity = repository._field_effect_activity
    assert _settled(repository)
    entered, release = threading.Event(), threading.Event()

    def blocking() -> None:
        entered.set()
        release.wait(10)

    monkeypatch.setattr(activity, "_activity_fact_step", blocking)
    ledger.commit()
    assert entered.wait(10)
    capsys.readouterr()
    try:
        start = time.monotonic()
        close()
        assert time.monotonic() - start < 5
        assert {"code": "join_timeout", "event": "activity_projector"}.items() <= (
            _events(capsys.readouterr().err)[0].items()
        )
    finally:
        release.set()
        deadline = time.monotonic() + 10
        while any(
            thread.name == "activity-fact-projector" and thread.is_alive()
            for thread in threading.enumerate()
        ):
            assert time.monotonic() < deadline
            time.sleep(0.01)


def test_every_readiness_check_is_fresh(
    ledger: _Ledger,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = _reader(ledger)
    activity = reader._field_effect_activity
    check = activity._fresh_database_quick_check
    checks: list[None] = []

    def counted() -> tuple[object, ...] | None:
        checks.append(None)
        return check()

    monkeypatch.setattr(activity, "_fresh_database_quick_check", counted)
    try:
        for expected in range(1, 4):
            assert _readiness(reader) == _readiness(ledger.canonical)
            assert len(checks) == expected
    finally:
        reader.close()


def test_the_projector_coalesces_commits_and_backs_off_a_stalled_catch_up(
    ledger: _Ledger,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bound(monkeypatch, 1)
    monkeypatch.setattr(field_effect_activity, "_ACTIVITY_PROJECTOR_POLL_SECONDS", 0.1)
    monkeypatch.setattr(
        field_effect_activity, "_ACTIVITY_PROJECTOR_COALESCE_SECONDS", 0.5
    )
    reader = _reader(ledger)
    activity = reader._field_effect_activity
    step = activity._activity_fact_step
    steps: list[float] = []

    def counted() -> None:
        steps.append(time.monotonic())
        step()

    monkeypatch.setattr(activity, "_activity_fact_step", counted)
    halt = _projecting(reader)
    try:
        assert _settled(reader)
        # Commits every 20 ms for 1.5 s: at most one refresh per coalescing
        # period, where a refresh per commit would be about 75.
        steps.clear()
        end = time.monotonic() + 1.5
        while time.monotonic() < end:
            ledger.commit()
            time.sleep(0.02)
        assert len(steps) <= 4
        # Once commits pause, one quiet poll brings it current.
        assert _settled(reader, 2)
        _assert_exact(reader, ledger)
        # A catch-up whose steps stop shrinking what is pending backs off to
        # the poll interval instead of stepping every gap.
        monkeypatch.setattr(activity, "_activity_fact_step", lambda: steps.append(0))
        activity._activity_facts._snapshot = dataclasses.replace(  # type: ignore[union-attr]
            ledger.snapshot(), pending=frozenset({cast("Candidate", ("stuck",))})
        )
        steps.clear()
        time.sleep(1.0)
        assert 4 <= len(steps) <= 4 + 12
    finally:
        halt()
        reader.close()


@pytest.mark.parametrize("mode", [None, FirstBootMode.INITIALIZE_SETUP])
def test_a_store_root_hands_what_it_validated_to_its_activity_readers(
    tmp_path: Path, mode: FirstBootMode | None
) -> None:
    """An ordinary store root validates into the store its generations reuse."""

    class Stop(Exception):
        pass

    seen: dict[str, object] = {}

    def opened(*_args: object, **kwargs: object) -> object:
        seen["root"] = kwargs["activity_documents"]
        if mode is not None:
            raise Stop
        return Mock()

    def activation(*_args: object, **kwargs: object) -> object:
        seen["activation"] = kwargs["activity_documents"]
        return Mock()

    bootstrap = ReviewBootstrap(
        schema_version="review-bootstrap-v2",
        tls_certificate_file="/synthetic/certificate",
        tls_private_key_file="/synthetic/private-key",
        canonical_origin="https://library.invalid",
    )
    with (
        patch.object(
            composition, "load_configuration_file", side_effect=FileNotFoundError
        ),
        patch.object(composition, "load_review_bootstrap", return_value=bootstrap),
        patch.object(
            composition,
            "open_review_tls_identity",
            return_value=nullcontext(None),
        ),
        patch.object(composition, "open_repository", side_effect=opened),
        patch.object(composition, "_StoreRuntimeActivation", side_effect=activation),
        patch.object(composition, "compose_store_runtime_generation", side_effect=Stop),
        pytest.raises(Stop),
    ):
        composition.run_review_server(
            tmp_path / "config.json",
            tmp_path / "application.sqlite3",
            tmp_path / "catalogue.sqlite3",
            tmp_path / "artifacts",
            "127.0.0.1",
            18444,
            first_boot_mode=mode,
        )
    if mode is None:
        assert type(seen["root"]) is ActivityDocuments
        assert seen["activation"] is seen["root"]
    else:
        assert seen == {"root": None}


def _whole_history(repository: SQLiteMetadataReportRepository) -> object:
    return _outcome(
        lambda: repository.field_effect_activity_page(
            search=None, event_type="all", occurred_after=0, page=1, page_size=25
        )
    )


def test_a_proposal_row_unlike_its_cases_bytes_is_parsed_on_its_own(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A case's proposal set stands in only for that case's exact bytes."""
    environment = cast("_EffectEnvironment", request.getfixturevalue("effects"))
    report, category, option, _ = _automatic_no_change_inputs("exact_title")
    assert _automatic_decisions(environment, report, category, option, qualified=True)
    owner = environment.repository
    proposal_set_id, document = owner._connection.execute(
        "SELECT proposal.proposal_set_id,proposal.document FROM automatic_decisions "
        "decision JOIN review_cases review USING(review_case_id) "
        "JOIN proposal_sets proposal USING(proposal_set_id) LIMIT 1"
    ).fetchone()
    tampered = document + " "
    owner._connection.execute("DROP TRIGGER proposal_sets_no_update")
    owner._connection.execute(
        "UPDATE proposal_sets SET document=? WHERE proposal_set_id=?",
        (tampered, proposal_set_id),
    )
    owner._connection.commit()
    parsed: list[str] = []
    parse = ProposalSet.from_json

    def counted(value: str) -> ProposalSet:
        parsed.append(value)
        return parse(value)

    monkeypatch.setattr(ProposalSet, "from_json", counted)
    reader = open_repository(
        environment.database_path, create=False, authority=owner.authority
    )
    try:
        reader.configure_activity_page_reader(ActivityFactStore())
        refused = _whole_history(reader)
    finally:
        reader.close()
    assert tampered in parsed
    # It refuses exactly as the cold canonical path does.
    assert refused == _whole_history(owner)
    assert refused == ("refused", FieldEffectError, "persistence")
