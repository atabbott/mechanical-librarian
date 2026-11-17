"""Taste judgements: storage, the daily ceiling, charge, cadence and order
(#1372 PR 4, J5-J7).

Fields are prepared by the real producers (`tests/unit/test_cover_taste_judge.py`);
the engine is a fake whose answers each test chooses.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from mechanical_librarian.adapters.persistence.cover_taste_judge import _DUE_SQL
from mechanical_librarian.application.contained_reviewer_contract import (
    ContainedReviewerError,
    canonical_json,
)
from mechanical_librarian.application.cover_taste_contract import (
    TasteChargeUnknownError,
)
from mechanical_librarian.application.cover_taste_judge import (
    CoverTasteBackground,
    CoverTasteJudgeService,
)
from mechanical_librarian.application.cover_taste_judge_contract import (
    AdmittedTasteJudgeRequest,
    TasteJudgeReply,
)
from mechanical_librarian.application.reviewer_access import (
    ReviewerAccessApplicationError,
)
from mechanical_librarian.domain.cover_taste import (
    TASTE_JUDGE_NOTICE_SECONDS,
    TASTE_JUDGE_OUTAGE_PAUSE_SECONDS,
    TASTE_JUDGE_SETTLE_SECONDS,
    TASTE_MIN_ARTS,
)
from tests.unit.test_cover_taste_judge import WORKS, _Library, _library, _version

NOW = 1_000_000
DAY = 86_400
Answer = Callable[[str, AdmittedTasteJudgeRequest], object]


def _verdict(request: AdmittedTasteJudgeRequest) -> TasteJudgeReply:
    """keep_current when a current cover was sent, else choose the first."""
    if request.current is not None:
        value: dict[str, object] = {
            "candidate": None,
            "confidence": 90,
            "decision": "keep_current",
            "rules": [1],
        }
    else:
        value = {
            "candidate": request.images[0][0],
            "confidence": 70,
            "decision": "choose",
            "rules": [2],
        }
    return TasteJudgeReply(canonical_json(value), True)


class _Client:
    def __init__(self, library: _Library, answer: Answer | None = None) -> None:
        self.library = library
        self.answer = answer
        self.items: list[str] = []
        self.check: Callable[[], None] = lambda: None

    def item(self, request: AdmittedTasteJudgeRequest) -> str:
        real = {reference for _opaque, reference in request.references}
        return next(
            item
            for item, consideration in self.library.considerations.items()
            if real
            <= {candidate["reference"] for candidate in consideration["candidates"]}
        )

    def run_taste_judge(
        self, _profile: bytes, request: AdmittedTasteJudgeRequest
    ) -> TasteJudgeReply:
        item = self.item(request)
        self.items.append(item)
        self.check()
        answer = (
            _verdict(request) if self.answer is None else self.answer(item, request)
        )
        if isinstance(answer, BaseException):
            raise answer
        return cast(TasteJudgeReply, answer)

    def close(self) -> None:
        pass


class _Access:
    def __init__(self, client: _Client) -> None:
        self.client = client
        self.engine = "openrouter"
        self.out = False

    def current_taste_judge_model(self) -> str | None:
        return "example/vision"

    def open_taste_judge(self):
        if self.out:
            raise ReviewerAccessApplicationError("unreachable")
        return self.engine, "example/vision", b"profile", self.client


class _Clock:
    def __init__(self, now: int = NOW) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now


def _service(
    library: _Library, client: _Client, clock: _Clock, limit: int = 50
) -> tuple[CoverTasteJudgeService, _Access]:
    store = library.repository.cover_taste_judge_store()
    access = _Access(client)
    service = CoverTasteJudgeService(
        lambda: store,
        access,  # type: ignore[arg-type]  # reason: the two methods the service reads
        library.artifacts.read_preview_digest,
        daily_limit=limit,
        clock=clock,
    )
    return service, access


def _works(*names: str) -> dict[str, dict[str, object]]:
    return {name: WORKS[name] for name in names}


def _rows(library: _Library) -> list[tuple[str, int, str, str | None]]:
    return [
        (library.item(consideration_id), version, state, failure)
        for consideration_id, version, state, failure in library.repository._connection.execute(
            "SELECT consideration_id,version,state,failure FROM cover_taste_judgements "
            "ORDER BY rowid"
        )
    ]


def _count(library: _Library) -> list[tuple[int, str | None]]:
    return library.repository._connection.execute(
        "SELECT requests,stopped FROM reviewer_task_requests "
        "WHERE task='cover_taste_judge'"
    ).fetchall()


@pytest.fixture
def library(tmp_path: Path):
    value = _library(tmp_path, _works("keep", "wrong_shape", "unreadable"))
    try:
        yield value
    finally:
        value.repository.close()


def test_verdicts_are_append_only_and_a_pair_is_attempted_once(library) -> None:
    _version(library.repository)
    client = _Client(library)
    service, _access = _service(library, client, _Clock())
    assert service.wake(NOW)
    connection = library.repository._connection
    for statement in (
        "UPDATE cover_taste_verdicts SET created_at=created_at+1",
        "DELETE FROM cover_taste_verdicts",
    ):
        with pytest.raises(
            sqlite3.IntegrityError, match="taste verdicts are append-only"
        ):
            connection.execute(statement)
    [(consideration_id, version)] = connection.execute(
        "SELECT consideration_id,version FROM cover_taste_judgements"
    ).fetchall()
    store = library.repository.cover_taste_judge_store()
    # The same cadence pair cannot be claimed or recorded again.
    assert store.claim(consideration_id, version, NOW, "example/vision", 50) is None
    with pytest.raises(sqlite3.IntegrityError):
        store.record(consideration_id, version, NOW)
    assert _count(library) == [(1, None)]
    document = json.loads(
        connection.execute("SELECT document FROM cover_taste_verdicts").fetchone()[0]
    )
    assert document["current_marker"] is True
    assert document["consideration_id"] == consideration_id


def test_the_due_statement_and_verdict_reads_search_their_indexes(library) -> None:
    policy, replay = library.repository._live_cover_authority()
    plan = " ".join(
        row[-1]
        for row in library.repository._connection.execute(
            "EXPLAIN QUERY PLAN " + _DUE_SQL, (policy, replay, TASTE_MIN_ARTS, 1)
        )
    )
    assert "SEARCH attempted USING COVERING INDEX cover_taste_judgements_pair" in plan
    assert "SEARCH verdict USING INDEX cover_taste_verdicts_consideration" in plan
    assert "SCAN attempted" not in plan and "SCAN verdict" not in plan
    assert "SCAN consideration" not in plan


def test_a_judgement_is_counted_before_dispatch_with_no_transaction_open(
    library, tmp_path: Path
) -> None:
    _version(library.repository)
    client = _Client(library)
    seen: list[object] = []

    def check() -> None:
        connection = library.repository._connection
        seen.append(connection.in_transaction)
        seen.append(_count(library))
        seen.append(
            connection.execute("SELECT state FROM cover_taste_judgements").fetchall()
        )
        other = sqlite3.connect(tmp_path / "application.sqlite3", timeout=0.5)
        try:
            other.execute("BEGIN IMMEDIATE")
            other.rollback()
        finally:
            other.close()

    client.check = check
    service, _access = _service(library, client, _Clock())
    assert service.wake(NOW)
    assert seen == [False, [(1, None)], [("running",)]]


def test_the_ceiling_stops_dispatch_and_counts_failures(library) -> None:
    _version(library.repository)
    client = _Client(library, lambda _item, _request: TasteJudgeReply("not json", True))
    clock = _Clock()
    service, _access = _service(library, client, clock, limit=2)
    assert service.wake(NOW) and service.wake(NOW)
    assert service.wake(NOW) is False
    assert len(client.items) == 2
    assert [row[2:] for row in _rows(library)] == [("failed", "invalid_output")] * 2
    assert _count(library) == [(2, None)]
    # The next UTC day has its own count.
    clock.now = NOW + DAY
    assert service.wake(clock.now)
    assert len(client.items) == 3
    assert sorted(_count(library)) == [(1, None), (2, None)]


@pytest.mark.parametrize(
    ("answer", "failure", "stopped"),
    (
        (TasteChargeUnknownError("taste outcome unknown"), "unknown_charge", True),
        (TasteJudgeReply(None, False), "unknown_charge", True),
        (TasteJudgeReply("[]", True), "invalid_output", False),
    ),
)
def test_an_unknown_charge_stops_the_day(library, answer, failure, stopped) -> None:
    _version(library.repository)
    client = _Client(library, lambda _item, _request: answer)
    service, _access = _service(library, client, _Clock())
    assert service.wake(NOW)
    assert [row[2:] for row in _rows(library)] == [("failed", failure)]
    assert _count(library) == [(1, "unknown_charge" if stopped else None)]
    assert service.wake(NOW) is (not stopped)
    assert len(client.items) == (1 if stopped else 2)


def test_a_codex_failure_pauses_the_judge_without_stopping_the_day(library) -> None:
    """A Codex run that fails is `unavailable`: counted, never a stopped day,
    and judging pauses 600 seconds so an outage costs a few requests an hour."""
    _version(library.repository)
    client = _Client(library, lambda _item, _request: ContainedReviewerError("timeout"))
    clock = _Clock()
    service, access = _service(library, client, clock)
    access.engine = "codex"
    assert service.wake(NOW)
    assert [row[2:] for row in _rows(library)] == [("failed", "unavailable")]
    assert _count(library) == [(1, None)]
    assert service.wake(NOW + TASTE_JUDGE_OUTAGE_PAUSE_SECONDS - 1) is False
    client.answer = None
    clock.now = NOW + TASTE_JUDGE_OUTAGE_PAUSE_SECONDS
    assert service.wake(clock.now)
    # The failed pair is not re-sent under the same version.
    assert len(client.items) == 2 and client.items[0] != client.items[1]


def test_an_engine_that_cannot_open_pauses_without_recording(library) -> None:
    _version(library.repository)
    client = _Client(library)
    service, access = _service(library, client, _Clock())
    access.out = True
    service.wake(NOW)
    assert _rows(library) == [] and _count(library) == []
    access.out = False
    assert service.wake(NOW + TASTE_JUDGE_OUTAGE_PAUSE_SECONDS - 1) is False
    assert service.wake(NOW + TASTE_JUDGE_OUTAGE_PAUSE_SECONDS)


def test_a_missing_preview_ends_the_attempt_unavailable_uncounted(library) -> None:
    _version(library.repository)
    client = _Client(library)
    service, _access = _service(library, client, _Clock())

    def missing(_digest: str) -> bytes:
        raise OSError("gone")

    service._read_preview = missing
    assert service.wake(NOW)
    assert client.items == []
    assert [row[2:] for row in _rows(library)] == [("failed", "unavailable")]
    assert _count(library) == []


def test_a_restart_ends_a_running_judgement_interrupted(library) -> None:
    version = _version(library.repository)
    store = library.repository.cover_taste_judge_store()
    due = store.next_due(version)
    assert due is not None
    assert store.claim(due.consideration_id, version, NOW, "example/vision", 50)
    client = _Client(library)
    service, _access = _service(library, client, _Clock())
    assert service.wake(NOW + 5)
    rows = _rows(library)
    assert rows[0][2:] == ("failed", "interrupted")
    assert rows[0][0] not in client.items
    assert _count(library) == [(2, None)]


def test_a_version_settles_before_it_is_judged(library) -> None:
    _version(library.repository, created_at=NOW - TASTE_JUDGE_SETTLE_SECONDS + 1)
    client = _Client(library)
    service, _access = _service(library, client, _Clock())
    assert service.wake(NOW) is False
    assert service.wake(NOW + 1)


def test_a_new_version_rejudges_pending_suggestions_first(library) -> None:
    """S22: an older version's `choose` comes first, then fields whose last
    attempt failed; `keep_current` and `abstain` are not judged again."""
    _version(library.repository)
    client = _Client(
        library,
        lambda item, request: (
            TasteJudgeReply("{}", True) if item == "wrong_shape" else _verdict(request)
        ),
    )
    service, _access = _service(library, client, _Clock())
    while service.wake(NOW):
        pass
    assert sorted(client.items) == ["keep", "unreadable", "wrong_shape"]
    _version(library.repository, created_at=NOW)
    client.items.clear()
    client.answer = None
    later = NOW + TASTE_JUDGE_SETTLE_SECONDS + 60
    while service.wake(later):
        pass
    assert client.items == ["unreadable", "wrong_shape"]


def test_changed_is_called_at_most_every_ten_seconds_and_when_a_pass_ends(
    tmp_path: Path,
) -> None:
    library = _library(tmp_path, _works("keep", "wrong_shape", "unreadable"))
    _version(library.repository)
    client = _Client(library)
    service, _access = _service(library, client, _Clock())
    calls: list[int] = []
    for now in (NOW, NOW + 1, NOW + TASTE_JUDGE_NOTICE_SECONDS, NOW + 11):

        def changed(at: int = now) -> None:
            calls.append(at)

        service.wake(now, changed)
    assert calls == [NOW, NOW + TASTE_JUDGE_NOTICE_SECONDS, NOW + 11]
    assert len(client.items) == 3
    library.repository.close()


def test_the_background_lane_runs_a_due_build_before_any_judgement() -> None:
    calls: list[str] = []

    class _Lane:
        def __init__(self, name: str, ran: bool) -> None:
            self.name, self.ran = name, ran

        def wake(self, _now: int, _changed: object) -> bool:
            calls.append(self.name)
            return self.ran

    assert CoverTasteBackground(_Lane("build", True), _Lane("judge", True)).wake(1)  # type: ignore[arg-type]  # reason: two wake-only lanes
    assert calls == ["build"]
    assert CoverTasteBackground(_Lane("build", False), _Lane("judge", True)).wake(1)  # type: ignore[arg-type]  # reason: as above
    assert calls == ["build", "build", "judge"]
