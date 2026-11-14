"""Taste profile builds, versions, cadence and the daily ceiling (#1372 A2, A7, A8).

Eligible decisions are seeded as rows of the real schema (foreign keys off,
so no report or case is needed); the end-to-end test drives them through the
real decision path instead.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence.cover_taste import (
    _ELIGIBLE,
    SQLiteCoverTasteRepository,
)
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.application.cover_taste_build import CoverTasteBuildService
from mechanical_librarian.application.cover_taste_contract import (
    TasteBuildReply,
    TasteChargeUnknownError,
)
from mechanical_librarian.application.cover_taste_inputs import (
    TasteCandidateSource,
    TasteDecisionSource,
)
from mechanical_librarian.application.reviewer_access import (
    ReviewerAccessApplicationError,
)
from mechanical_librarian.domain.cover_automatic_decision import (
    SCHEMA_VERSION as COVER_AUTOMATIC_DECISION_SCHEMA,
)
from mechanical_librarian.domain.cover_taste import TasteProfile
from mechanical_librarian.domain.human_decision import COVER_SELECTION_AUTHORITY_SCHEMA
from tests.support.cover_taste import canonical, stored_preview

DAY = 86_400
NOW = 10 * DAY + 100
LIMIT = 8
RECORD = {
    "corrections": 0,
    "dropped": 0,
    "eligible": 25,
    "images": 50,
    "included": 25,
    "input_sha256": "0" * 64,
    "truncated": False,
}
PROFILE = TasteProfile.from_output(
    {
        "sections": {
            "drawn_to": ["Bold art."],
            "avoid": [],
            "trade_offs": [],
            "keep_or_switch": [],
        }
    }
)


def _hex(value: str, width: int) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:width].ljust(width, "0")


class _Seed:
    def __init__(self, repository: SQLiteMetadataReportRepository) -> None:
        self.connection = repository._connection
        self.count = 0

    def decision(
        self,
        item: str,
        *,
        arts: int | None = 2,
        preview: bool = True,
        candidate_kind: str = "candidate",
        kind: str = "owner",
    ) -> str:
        self.count += 1
        tag = f"{item}-{self.count}"
        consideration = "cover_consideration_" + _hex(tag, 64)
        selection = "cover_selection_" + _hex(tag, 64)
        stage = (
            None if arts is None else {"arts": arts, "outcome": "keep", "reason": None}
        )
        self.connection.execute("PRAGMA foreign_keys=OFF")
        self.connection.execute(
            "INSERT INTO cover_considerations VALUES(?,?,?,?,?,NULL,?,?,?)",
            (
                consideration,
                "review_case_" + _hex(item, 64),
                "report_" + _hex(item, 64),
                "1" * 64,
                "2" * 64,
                1,
                json.dumps({"cover_stage": stage}),
                json.dumps({"report_document_digest": "3" * 64}),
            ),
        )
        previous = self.connection.execute(
            "SELECT selection_id FROM current_cover_selection_authorities WHERE item_id=?",
            (item,),
        ).fetchone()
        document = {
            "schema_version": COVER_SELECTION_AUTHORITY_SCHEMA,
            "candidate_kind": candidate_kind,
            "preview_sha256": "4" * 64 if preview else None,
        }
        decision = (
            "human_decision_" + _hex(tag, 64)
            if kind == "owner"
            else "automatic_decision_" + _hex(tag, 64)
        )
        if kind != "owner":
            document = {
                "decision_document": json.dumps(
                    {"schema_version": COVER_AUTOMATIC_DECISION_SCHEMA}
                )
            }
        self.connection.execute(
            "INSERT INTO cover_selection_authorities VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                selection,
                "primary",
                "books",
                item,
                kind,
                decision,
                consideration,
                None if previous is None else previous[0],
                1,
                json.dumps(document),
            ),
        )
        self.connection.execute(
            "INSERT INTO current_cover_selection_authorities VALUES(?,?,?,?) "
            "ON CONFLICT(connection_name,library_name,item_id) "
            "DO UPDATE SET selection_id=excluded.selection_id",
            ("primary", "books", item, selection),
        )
        self.connection.commit()
        return selection

    def many(self, count: int, start: int = 0) -> None:
        for index in range(start, start + count):
            self.decision(f"item-{index}")


@pytest.fixture
def repository(tmp_path: Path):
    value = open_repository(tmp_path / "reports.sqlite")
    try:
        yield value
    finally:
        value.close()


def _store(repository: SQLiteMetadataReportRepository) -> SQLiteCoverTasteRepository:
    return repository.cover_taste_store()


def _state(
    repository: SQLiteMetadataReportRepository,
) -> list[tuple[str, str, str | None]]:
    return repository._connection.execute(
        "SELECT trigger,state,failure FROM cover_taste_builds ORDER BY rowid"
    ).fetchall()


def test_24_decisions_build_nothing_and_25_request_one(repository) -> None:
    seed = _Seed(repository)
    store = _store(repository)
    seed.many(24)
    store.schedule(NOW, True, LIMIT)
    assert _state(repository) == []
    seed.decision("item-24")
    store.schedule(NOW, False, LIMIT)
    assert _state(repository) == []
    store.schedule(NOW, True, LIMIT)
    assert _state(repository) == [("cadence", "requested", None)]
    assert store.read()["eligible"] == 25


def test_eligibility_excludes_one_art_retained_preview_less_and_automatic(
    repository,
) -> None:
    seed = _Seed(repository)
    seed.many(20)
    seed.decision("one-art", arts=1)
    seed.decision("no-stage", arts=None)
    seed.decision("retained", candidate_kind="retained")
    # A keep with no readable incumbent records no preview.
    seed.decision("keep-no-incumbent", preview=False)
    seed.decision("automatic", kind="automatic")
    # A superseded owner selection counts once, as the item's newest.
    seed.decision("item-0")
    seed.decision("item-0", arts=1)
    assert _store(repository).eligible_count() == 19


def test_eligible_count_reads_considerations_by_key_only(repository) -> None:
    """R9: the count on each read searches considerations by key, never scans them."""
    plan = " ".join(
        row[-1]
        for row in repository._connection.execute(
            f"EXPLAIN QUERY PLAN SELECT count(*) {_ELIGIBLE}", (2,)
        )
    )
    assert "SCAN consideration" not in plan
    assert "SEARCH consideration USING" in plan


def test_versions_are_append_only(repository) -> None:
    seed = _Seed(repository)
    store = _store(repository)
    seed.many(25)
    store.schedule(NOW, True, LIMIT)
    build = store.pending()
    assert build is not None
    assert store.claim(build, NOW, "example/vision", LIMIT) is None
    assert store.succeed(build, NOW, PROFILE, RECORD) == 1
    connection = repository._connection
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        connection.execute("UPDATE cover_taste_profile_versions SET created_at=0")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        connection.execute("DELETE FROM cover_taste_profile_versions")
    connection.rollback()
    versions = store.read()["versions"]
    assert [(version, created) for version, created, _document in versions] == [
        (1, NOW)
    ]
    assert versions[0][2]["rules"] == PROFILE.rules_data()
    assert versions[0][2]["model"] == "example/vision"


def test_one_build_in_flight_and_at_most_four_scheduled_a_day(repository) -> None:
    seed = _Seed(repository)
    store = _store(repository)
    seed.many(25)
    store.schedule(NOW, True, LIMIT)
    store.schedule(NOW, True, LIMIT)
    assert len(_state(repository)) == 1
    for round_ in range(5):
        build = store.pending()
        assert build is not None
        assert store.claim(build, NOW, "example/vision", LIMIT) is None
        store.schedule(NOW, True, LIMIT)
        assert len(_state(repository)) == round_ + 1
        store.succeed(build, NOW, PROFILE, RECORD)
        seed.many(10, start=25 + 10 * round_)
        store.schedule(NOW, True, LIMIT)
        if round_ >= 3:
            break
    # Four cadence builds today; the fifth waits for the next UTC day.
    assert [row[0] for row in _state(repository)] == ["cadence"] * 4
    assert store.pending() is None
    store.schedule(NOW + DAY, True, LIMIT)
    assert store.pending() is not None


def test_rebuild_waits_for_ten_new_decisions(repository) -> None:
    seed = _Seed(repository)
    store = _store(repository)
    seed.many(25)
    store.schedule(NOW, True, LIMIT)
    build = store.pending()
    assert build is not None and store.claim(build, NOW, "m/x", LIMIT) is None
    store.succeed(build, NOW, PROFILE, RECORD)
    seed.many(9, start=25)
    store.schedule(NOW, True, LIMIT)
    assert store.pending() is None
    seed.decision("item-34")
    store.schedule(NOW, True, LIMIT)
    assert store.pending() is not None


def test_ceiling_counts_before_dispatch_including_failures(repository) -> None:
    seed = _Seed(repository)
    store = _store(repository)
    seed.many(25)
    store.schedule(NOW, True, 2)
    first = store.pending()
    assert first is not None and store.claim(first, NOW, "m/x", 2) is None
    requests = lambda: repository._connection.execute(  # noqa: E731
        "SELECT requests,stopped FROM reviewer_task_requests"
    ).fetchall()
    assert requests() == [(1, None)]
    store.fail(first, NOW, "unavailable")
    retry = store.request_retry(first, NOW, 2)
    assert retry is not None
    # Idempotent on the failed build.
    assert store.request_retry(first, NOW, 2) == retry
    assert store.claim(retry, NOW, "m/x", 2) is None
    assert requests() == [(2, None)]
    store.fail(retry, NOW, "invalid_output")
    third = store.request_retry(retry, NOW, 2)
    assert third is not None
    assert _state(repository)[-1] == ("retry", "failed", "ceiling")
    build = store.read()["build"]
    assert build == {"build_id": third, "failure": "ceiling", "retry_after": 11 * DAY}
    assert requests() == [(2, None)]


def test_a_retry_names_only_the_latest_failed_build(repository) -> None:
    seed = _Seed(repository)
    store = _store(repository)
    seed.many(25)
    store.schedule(NOW, True, LIMIT)
    build = store.pending()
    assert build is not None
    assert store.request_retry(build, NOW, LIMIT) is None
    assert store.request_retry("taste_build_" + "0" * 32, NOW, LIMIT) is None


def test_unknown_charge_stops_the_task_for_the_day(repository) -> None:
    seed = _Seed(repository)
    store = _store(repository)
    seed.many(25)
    store.schedule(NOW, True, LIMIT)
    build = store.pending()
    assert build is not None and store.claim(build, NOW, "m/x", LIMIT) is None
    store.fail(build, NOW, "unknown_charge", stop_day=True)
    retry = store.request_retry(build, NOW, LIMIT)
    assert _state(repository)[-1] == ("retry", "failed", "unknown_charge")
    assert retry is not None
    # The next UTC day starts a fresh count.
    again = store.request_retry(retry, NOW + DAY, LIMIT)
    assert again is not None and store.claim(again, NOW + DAY, "m/x", LIMIT) is None


def test_restart_ends_a_running_build_interrupted(repository) -> None:
    seed = _Seed(repository)
    store = _store(repository)
    seed.many(25)
    store.schedule(NOW, True, LIMIT)
    build = store.pending()
    assert build is not None and store.claim(build, NOW, "m/x", LIMIT) is None
    store.interrupt_recovered(NOW + 5)
    assert _state(repository) == [("cadence", "failed", "interrupted")]
    assert store.pending() is None
    # A late answer for the interrupted build writes nothing.
    assert store.succeed(build, NOW + 6, PROFILE, RECORD) is None
    assert store.read()["versions"] == []


class _Access:
    def __init__(self, client: object) -> None:
        self.client = client

    def current_taste_build_model(self) -> str | None:
        return "example/vision"

    def open_taste_build(self):
        if isinstance(self.client, Exception):
            raise self.client
        return 1, "example/vision", b"profile", self.client


class _Client:
    def __init__(self, answer: object, check=lambda: None) -> None:
        self.answer = answer
        self.check = check
        self.closed = False

    def run_taste_build(self, _profile: bytes, _request: object) -> TasteBuildReply:
        self.check()
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer  # type: ignore[return-value]

    def close(self) -> None:
        self.closed = True


_PAYLOAD = stored_preview(1)
_PREVIEW = canonical(_PAYLOAD)


def _sources() -> tuple[TasteDecisionSource, ...]:
    candidate = TasteCandidateSource(
        "ref-1",
        hashlib.sha256(_PAYLOAD).hexdigest(),
        _PREVIEW.digest,
        600,
        900,
        False,
        1,
        0,
        False,
        "ref-1",
    )
    return (TasteDecisionSource("selection-1", "ebook", "ref-1", (candidate,)),)


def _service(repository, client: object, now: int = NOW) -> CoverTasteBuildService:
    store = _store(repository)
    store.decisions = _sources  # type: ignore[method-assign]
    return CoverTasteBuildService(
        lambda: store,
        _Access(client),  # type: ignore[arg-type]
        lambda _digest: _PAYLOAD,
        daily_limit=LIMIT,
        clock=lambda: now,
    )


_VALID = TasteBuildReply(
    {
        "sections": {
            "drawn_to": ["Bold art."],
            "avoid": [],
            "trade_offs": [],
            "keep_or_switch": [],
        }
    },
    True,
)


def test_invalid_output_keeps_version_n_in_use(repository) -> None:
    _Seed(repository).many(25)
    _service(repository, _Client(_VALID)).wake(NOW)
    assert [row[0] for row in _store(repository).read()["versions"]] == [1]
    _Seed(repository).many(10, start=25)
    too_long = TasteBuildReply(
        {
            "sections": {
                "drawn_to": ["x" * 401],
                "avoid": [],
                "trade_offs": [],
                "keep_or_switch": [],
            }
        },
        True,
    )
    _service(repository, _Client(too_long), NOW + 61).wake(NOW + 61)
    assert _state(repository)[-1] == ("cadence", "failed", "invalid_output")
    assert [row[0] for row in _store(repository).read()["versions"]] == [1]


@pytest.mark.parametrize(
    ("answer", "failure", "stopped"),
    [
        (TasteChargeUnknownError(), "unknown_charge", "unknown_charge"),
        (RuntimeError("provider said something private"), "unavailable", None),
        (TasteBuildReply({"sections": {}}, False), "invalid_output", "unknown_charge"),
    ],
    ids=["timed-out-after-send", "refused", "no-usage-invalid"],
)
def test_build_failures_are_closed_codes(repository, answer, failure, stopped) -> None:
    _Seed(repository).many(25)
    client = _Client(answer)
    _service(repository, client).wake(NOW)
    assert _state(repository) == [("cadence", "failed", failure)]
    assert client.closed
    assert repository._connection.execute(
        "SELECT requests,stopped FROM reviewer_task_requests"
    ).fetchall() == [(1, stopped)]


def test_unconfigured_access_fails_without_counting(repository) -> None:
    _Seed(repository).many(25)
    _service(repository, ReviewerAccessApplicationError("not_configured")).wake(NOW)
    assert _state(repository) == [("cadence", "failed", "unconfigured")]
    assert repository._connection.execute(
        "SELECT count(*) FROM reviewer_task_requests"
    ).fetchone() == (0,)


def test_no_write_transaction_is_open_during_the_transport_call(
    repository, tmp_path
) -> None:
    _Seed(repository).many(25)
    seen: list[bool] = []

    def check() -> None:
        seen.append(repository._connection.in_transaction)
        # Another connection can write while the request is in flight.
        other = sqlite3.connect(tmp_path / "reports.sqlite", timeout=0.5)
        try:
            other.execute("BEGIN IMMEDIATE")
            other.rollback()
        finally:
            other.close()
        seen.append(threading.current_thread() is threading.main_thread())

    _service(repository, _Client(_VALID, check)).wake(NOW)
    assert seen == [False, True]
    assert _state(repository) == [("cadence", "succeeded", None)]


def test_the_first_wake_after_a_restart_ends_a_left_running_build(repository) -> None:
    _Seed(repository).many(25)
    store = _store(repository)
    store.schedule(NOW, True, LIMIT)
    build = store.pending()
    assert build is not None and store.claim(build, NOW, "m/x", LIMIT) is None
    # A new process: its first wake ends the build, and no request is resent.
    client = _Client(_VALID)
    service = _service(repository, client, NOW + 30)
    service.wake(NOW + 30)
    assert _state(repository) == [("cadence", "failed", "interrupted")]
    assert client.closed is False
    assert repository._connection.execute(
        "SELECT requests FROM reviewer_task_requests"
    ).fetchall() == [(1,)]
