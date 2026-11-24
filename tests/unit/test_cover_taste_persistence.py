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
from typing import Any, cast

import pytest

from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.adapters.persistence.cover_considerations import (
    _OWNER_SELECTION_INSERT_SQL,
)
from mechanical_librarian.adapters.persistence.cover_taste import (
    _ELIGIBLE,
    _INHERITED,
    SQLiteCoverTasteRepository,
    validate_cover_taste_versions,
)
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.adapters.persistence.schema import _COVER_TASTE_TABLES
from mechanical_librarian.application.contained_reviewer_contract import canonical_json
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
from mechanical_librarian.domain.cover_taste import (
    COVER_TASTE_PROFILE_SCHEMA,
    TasteEditError,
    TasteProfile,
    TasteRule,
)
from mechanical_librarian.domain.human_decision import COVER_SELECTION_AUTHORITY_SCHEMA
from tests.support.cover_selections import ALL_COVER_POINTERS, ALL_COVER_SELECTIONS
from tests.support.cover_taste import canonical, stored_preview
from tests.support.ledger_copies import assert_ledger_copies

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
        offered_kind: str = "candidate",
        file_format: str | None = "epub",
    ) -> str:
        self.count += 1
        tag = f"{item}-{self.count}"
        consideration = "cover_consideration_" + _hex(tag, 64)
        selection = "cover_selection_" + _hex(tag, 64)
        stage = (
            None if arts is None else {"arts": arts, "outcome": "keep", "reason": None}
        )
        report = "report_" + _hex(tag, 64)
        self.connection.execute("PRAGMA foreign_keys=OFF")
        # The chosen cover as the consideration offered it.
        offered = {
            "candidate_kind": offered_kind,
            "reference": "cover-a",
            "preview_sha256": "4" * 64,
            "transport_sha256": "5" * 64,
            "width": 600,
            "height": 900,
        }
        self.connection.execute(
            "INSERT INTO cover_considerations VALUES(?,?,?,?,?,NULL,?,?,?)",
            (
                consideration,
                "review_case_" + _hex(item, 64),
                report,
                "1" * 64,
                "2" * 64,
                1,
                json.dumps({"cover_stage": stage}),
                json.dumps(
                    {"report_document_digest": "3" * 64, "candidates": [offered]}
                ),
            ),
        )
        self.connection.execute(
            "INSERT INTO metadata_report_browse_facts VALUES(?,?,?,?,0,'matched')",
            (report, item, file_format, file_format or ""),
        )
        previous = self.connection.execute(
            "SELECT selection_id FROM " + ALL_COVER_POINTERS + " WHERE item_id=?",
            (item,),
        ).fetchone()
        document = {
            "schema_version": COVER_SELECTION_AUTHORITY_SCHEMA,
            "candidate_kind": candidate_kind,
            "candidate_reference": "cover-a",
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
        # An owner's choice copies its taste facts as the product writes
        # them, and takes the work from Automatic.
        self.connection.execute(
            _OWNER_SELECTION_INSERT_SQL
            if kind == "owner"
            else "INSERT INTO automatic_cover_selections VALUES(?,?,?,?,?,?,?,?,?)",
            (
                selection,
                "primary",
                "books",
                item,
                decision,
                consideration,
                None if previous is None else previous[0],
                1,
                json.dumps(document),
            ),
        )
        if kind == "owner":
            self.connection.execute(
                "DELETE FROM automatic_cover_pointers WHERE item_id=?", (item,)
            )
        self.connection.execute(
            f"INSERT INTO {kind}_cover_pointers VALUES(?,?,?,?) "
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


def test_each_owner_choice_copies_the_facts_the_profile_counts_it_by(
    repository,
) -> None:
    """An owner's cover choice is ledger: it keeps the chosen cover's digest
    and size, the comparison's art count and the report's format as the
    taste profile reads them when the owner chose (ADR-0108, #2038)."""
    seed = _Seed(repository)
    seed.many(3)
    seed.decision("one-art", arts=1)
    seed.decision("no-stage", arts=None)
    seed.decision("larger-copy", offered_kind="larger_copy")
    seed.decision("no-format", file_format=None)
    seed.decision("automatic", kind="automatic")
    checked = assert_ledger_copies(repository)
    assert (checked["taste_owner_selection"], checked["taste_eligible"]) == (7, 3)
    copies = dict(
        (row[0], row[1:])
        for row in repository._connection.execute(
            "SELECT item_id,transport_sha256,width,height,offered_arts,report_format "
            "FROM owner_cover_selections"
        )
    )
    assert copies == {
        **{f"item-{index}": ("5" * 64, 600, 900, 2, "epub") for index in range(3)},
        "one-art": ("5" * 64, 600, 900, 1, "epub"),
        "no-stage": ("5" * 64, 600, 900, None, "epub"),
        "larger-copy": (None, None, None, 2, "epub"),
        "no-format": ("5" * 64, 600, 900, 2, None),
    }


def test_count_and_thresholds_read_only_decisions_a_build_sends(repository) -> None:
    """A larger copy or a report with no file format is never sent, so it is
    not counted toward "You have chosen {n}" or the 25 threshold."""
    seed = _Seed(repository)
    store = _store(repository)
    seed.many(24)
    seed.decision("larger-copy", offered_kind="larger_copy")
    seed.decision("no-format", file_format=None)
    assert store.eligible_count() == 24
    store.schedule(NOW, True, LIMIT)
    assert _state(repository) == []
    seed.decision("item-24")
    store.schedule(NOW, True, LIMIT)
    assert _state(repository) == [("cadence", "requested", None)]


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
    assert store.succeed(build, NOW, PROFILE, RECORD, failure="invalid_output") == 1
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
        store.succeed(build, NOW, PROFILE, RECORD, failure="invalid_output")
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
    store.succeed(build, NOW, PROFILE, RECORD, failure="invalid_output")
    seed.many(9, start=25)
    store.schedule(NOW, True, LIMIT)
    assert store.pending() is None
    seed.decision("item-34")
    store.schedule(NOW, True, LIMIT)
    assert store.pending() is not None


def test_an_unconfigured_failure_does_not_consume_the_cadence_watermark(
    repository,
) -> None:
    """A build that failed only because no model was chosen sent nothing, so once
    a model is chosen again new choices still count from the last real build."""
    seed = _Seed(repository)
    store = _store(repository)
    seed.many(25)
    store.schedule(NOW, True, LIMIT)
    first = store.pending()
    assert first is not None
    store.fail(first, NOW, "unconfigured")
    store.schedule(NOW, True, LIMIT)
    second = store.pending()
    assert second is not None and second != first
    assert store.claim(second, NOW, "m/x", LIMIT) is None
    store.succeed(second, NOW, PROFILE, RECORD, failure="invalid_output")
    seed.many(10, start=25)
    store.schedule(NOW, True, LIMIT)
    third = store.pending()
    assert third is not None
    store.fail(third, NOW, "unconfigured")
    store.schedule(NOW, True, LIMIT)
    assert store.pending() is not None
    assert _state(repository) == [
        ("cadence", "failed", "unconfigured"),
        ("cadence", "succeeded", None),
        ("cadence", "failed", "unconfigured"),
        ("cadence", "requested", None),
    ]


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
    assert (
        store.succeed(build, NOW + 6, PROFILE, RECORD, failure="invalid_output") is None
    )
    assert store.read()["versions"] == []


class _Access:
    def __init__(self, client: object) -> None:
        self.client = client

    def current_taste_build_model(self) -> str | None:
        return "example/vision"

    def open_taste_build(self):
        if isinstance(self.client, Exception):
            raise self.client
        return "example/vision", b"profile", self.client


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


def test_a_wake_says_whether_it_scheduled_or_ran_a_build(repository) -> None:
    """#1372 K1: the runner announces only a wake that changed the build."""
    seed = _Seed(repository)
    seed.many(24)
    service = _service(repository, _Client(_VALID))
    assert service.wake(NOW) is False
    seed.decision("item-24")
    assert _service(repository, _Client(_VALID)).wake(NOW) is True
    assert _state(repository) == [("cadence", "succeeded", None)]
    assert _service(repository, _Client(_VALID)).wake(NOW) is False
    assert _store(repository).schedule(NOW, False, LIMIT) is False


def test_a_wake_announces_a_build_before_it_is_sent_and_again_when_it_ends(
    repository,
) -> None:
    """#1372 K1: an open Covers → Taste profile reads the build in flight, so
    it polls, and reads its end; a wake that changed nothing says nothing."""
    events: list[str] = []
    seed = _Seed(repository)
    seed.many(24)
    service = _service(repository, _Client(_VALID))
    service.wake(NOW, lambda: events.append("changed"))
    assert events == []
    seed.decision("item-24")

    def sent() -> None:
        events.append("sent")
        assert _state(repository) == [("cadence", "building", None)]

    _service(repository, _Client(_VALID, sent)).wake(
        NOW, lambda: events.append("changed")
    )
    assert events == ["changed", "sent", "changed"]


def test_a_build_that_raises_still_announces_its_end(repository) -> None:
    _Seed(repository).many(25)
    events: list[str] = []
    with pytest.raises(RuntimeError):
        _service(repository, RuntimeError("synthetic")).wake(
            NOW, lambda: events.append("changed")
        )
    assert events == ["changed", "changed"]
    assert _state(repository) == [("cadence", "failed", "unavailable")]


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
        (TasteBuildReply({"sections": {}}, True), "invalid_output", None),
        # A reply that was not one JSON object, such as content cut at the
        # output limit (R6), is an unusable profile; the day goes on.
        (TasteBuildReply(None, True), "invalid_output", None),
        # Without usage the charge is unknown, so the day stops (S9).
        (TasteBuildReply({"sections": {}}, False), "unknown_charge", "unknown_charge"),
        (TasteBuildReply(None, False), "unknown_charge", "unknown_charge"),
    ],
    ids=[
        "timed-out-after-send",
        "refused",
        "schema-invalid",
        "unparseable",
        "no-usage-invalid",
        "no-usage-unparseable",
    ],
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


def _real_sources(digests: list[str]) -> tuple[TasteDecisionSource, ...]:
    return tuple(
        TasteDecisionSource(
            f"selection-{index}",
            "ebook",
            "ref",
            (
                TasteCandidateSource(
                    "ref",
                    digest,
                    canonical(stored_preview(index)).digest,
                    600,
                    900,
                    False,
                    1,
                    0,
                    False,
                    "ref",
                ),
            ),
        )
        for index, digest in enumerate(digests)
    )


def test_a_missing_preview_in_the_real_store_drops_its_decision(
    repository, tmp_path
) -> None:
    """R3: the real store refuses a missing file with `MetadataReportError`;
    that decision is dropped and counted, and the build still runs."""
    _Seed(repository).many(25)
    root = tmp_path / "artifacts"
    artifacts = CoverArtifactStore(root)
    digests = []
    for index in range(2):
        payload = stored_preview(index)
        digest = hashlib.sha256(payload).hexdigest()
        (root / digest).write_bytes(payload)
        digests.append(digest)
    (root / digests[0]).unlink()
    store = _store(repository)
    store.decisions = lambda: _real_sources(digests)  # type: ignore[method-assign]
    client = _Client(_VALID)
    CoverTasteBuildService(
        lambda: store,
        _Access(client),  # type: ignore[arg-type]
        artifacts.read_preview_digest,
        daily_limit=LIMIT,
        clock=lambda: NOW,
    ).wake(NOW)
    assert _state(repository) == [("cadence", "succeeded", None)]
    [(_version, _created, document)] = cast(
        "list[tuple[int, int, dict[str, Any]]]", store.read()["versions"]
    )
    assert document["input"]["dropped"] == 1
    assert document["input"]["included"] == 1


def test_an_unexpected_fault_before_claim_ends_the_build(repository) -> None:
    """A build is never left `requested`, which would rerun it every wake."""
    _Seed(repository).many(25)
    service = _service(repository, _Client(_VALID))
    store = _store(repository)

    def broken() -> tuple[TasteDecisionSource, ...]:
        raise RuntimeError("synthetic")

    service._repository = lambda: store
    store.decisions = broken  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        service.wake(NOW)
    assert _state(repository) == [("cadence", "failed", "unavailable")]
    assert store.pending() is None
    assert repository._connection.execute(
        "SELECT count(*) FROM reviewer_task_requests"
    ).fetchone() == (0,)


def test_an_unexpected_fault_opening_the_client_ends_the_build(repository) -> None:
    _Seed(repository).many(25)
    with pytest.raises(RuntimeError):
        _service(repository, RuntimeError("synthetic")).wake(NOW)
    assert _state(repository) == [("cadence", "failed", "unavailable")]


class _FailingWrites:
    """The real store, whose next `succeed` or `fail` raises once."""

    def __init__(self, store: SQLiteCoverTasteRepository, method: str) -> None:
        self._store = store
        self._method = method

    def __getattr__(self, name: str) -> object:
        if name == self._method:
            self._method = ""

            def broken(*_args: object, **_kwargs: object) -> None:
                raise sqlite3.OperationalError("database is locked")

            return broken
        return getattr(self._store, name)


@pytest.mark.parametrize(
    ("answer", "method", "failure", "stopped"),
    [
        (_VALID, "succeed", "unavailable", None),
        (
            TasteBuildReply(_VALID.result, False),
            "succeed",
            "unknown_charge",
            "unknown_charge",
        ),
        (TasteChargeUnknownError(), "fail", "unknown_charge", "unknown_charge"),
        (TasteBuildReply(None, True), "fail", "invalid_output", None),
    ],
    ids=["succeed-usage", "succeed-no-usage", "fail-unknown", "fail-invalid"],
)
def test_a_lost_write_after_claim_never_leaves_the_build_running(
    repository, answer, method, failure, stopped
) -> None:
    """The request was counted at claim; the build ends at once, not at restart."""
    _Seed(repository).many(25)
    store = _store(repository)
    store.decisions = _sources  # type: ignore[method-assign]
    failing = _FailingWrites(store, method)
    service = CoverTasteBuildService(
        lambda: failing,  # type: ignore[arg-type,return-value]
        _Access(_Client(answer)),  # type: ignore[arg-type]
        lambda _digest: _PAYLOAD,
        daily_limit=LIMIT,
        clock=lambda: NOW,
    )
    with pytest.raises(sqlite3.OperationalError):
        service.wake(NOW)
    assert _state(repository) == [("cadence", "failed", failure)]
    assert store.read()["versions"] == []
    assert repository._connection.execute(
        "SELECT requests,stopped FROM reviewer_task_requests"
    ).fetchall() == [(1, stopped)]


def _built_version(repository, rules: dict[str, list[str]] | None = None) -> str:
    """Schedule, claim and write one build from 25 seeded decisions."""
    seen = repository._connection.execute(
        "SELECT count(*) FROM " + ALL_COVER_SELECTIONS
    ).fetchone()[0]
    _Seed(repository).many(25, start=1000 + seen)
    store = _store(repository)
    store.schedule(NOW, True, LIMIT)
    build = store.pending()
    assert build is not None and store.claim(build, NOW, "m/x", LIMIT) is None
    profile = PROFILE if rules is None else _output(**rules)
    assert isinstance(
        store.succeed(build, NOW, profile, RECORD, failure="invalid_output"), int
    )
    return build


def _output(**sections: list[str]) -> TasteProfile:
    return TasteProfile.from_output(
        {
            "sections": {
                name: sections.get(name, [])
                for name in ("drawn_to", "avoid", "trade_offs", "keep_or_switch")
            }
        }
    )


def _versions(repository) -> list[tuple[int, str | None, dict[str, Any]]]:
    return [
        (version, build_id, json.loads(document))
        for version, build_id, document in repository._connection.execute(
            "SELECT version,build_id,document FROM cover_taste_profile_versions "
            "ORDER BY version"
        )
    ]


def test_an_edit_appends_a_version_inheriting_the_builds_facts(repository) -> None:
    build = _built_version(repository)
    store = _store(repository)
    assert store.edit(1, "edit", 1, None, "Bright art.", NOW + 5) == 2
    assert store.edit(2, "add", None, "avoid", "Busy type.", NOW + 6) == 3
    assert store.edit(3, "remove", 1, None, None, NOW + 7) == 4
    [first, edited, added, removed] = _versions(repository)
    assert first[1] == build and first[2]["source"] == "build"
    assert first[2]["built_at"] == NOW and first[2]["left_out"] == 0
    for version, build_id, document in (edited, added, removed):
        assert build_id is None
        assert document["source"] == "edit"
        assert document["version"] == version
        assert {key: document[key] for key in _INHERITED} == {
            key: first[2][key] for key in _INHERITED
        }
    assert edited[2]["rules"] == [
        {
            "number": 1,
            "provenance": "edited",
            "section": "drawn_to",
            "text": "Bright art.",
        }
    ]
    assert edited[2]["removed"] == ["Bold art."]
    assert added[2]["rules"][-1] == {
        "number": 2,
        "provenance": "owner",
        "section": "avoid",
        "text": "Busy type.",
    }
    assert removed[2]["rules"] == [added[2]["rules"][-1]]
    assert removed[2]["removed"] == ["Bright art.", "Bold art."]
    # The unchanged text writes nothing.
    assert store.edit(4, "edit", 2, None, "Busy type.", NOW + 8) is None
    assert len(_versions(repository)) == 4
    validate_cover_taste_versions(repository._connection)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        repository._connection.execute(
            "UPDATE cover_taste_profile_versions SET version=9"
        )
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        repository._connection.execute("DELETE FROM cover_taste_profile_versions")
    repository._connection.rollback()


@pytest.mark.parametrize(
    ("args", "code"),
    [
        ((2, "edit", 1, None, "Bright art."), "stale"),
        ((1, "edit", 7, None, "Bright art."), "stale"),
        ((1, "remove", 7, None, None), "stale"),
        ((2, "edit", 1, None, ""), "rule_empty"),
    ],
    ids=["old-version", "missing-number", "remove-missing", "text-before-stale"],
)
def test_a_refused_edit_writes_no_version(repository, args, code) -> None:
    _built_version(repository)
    version, action, number, section, text = args
    with pytest.raises(TasteEditError) as raised:
        _store(repository).edit(version, action, number, section, text, NOW)
    assert raised.value.code == code
    assert len(_versions(repository)) == 1
    assert not repository._connection.in_transaction


def test_an_edit_with_no_profile_is_stale(repository) -> None:
    with pytest.raises(TasteEditError) as raised:
        _store(repository).edit(1, "add", None, "avoid", "Busy type.", NOW)
    assert raised.value.code == "stale"
    assert _versions(repository) == []


def test_add_at_40_rules_is_rule_limit(repository) -> None:
    _built_version(repository, {"drawn_to": [f"Rule {n}." for n in range(40)]})
    with pytest.raises(TasteEditError) as raised:
        _store(repository).edit(1, "add", None, "avoid", "Busy type.", NOW)
    assert raised.value.code == "rule_limit"
    assert len(_versions(repository)) == 1


def test_a_rebuild_keeps_edits_and_leaves_out_repeats(repository) -> None:
    _built_version(repository, {"drawn_to": ["Bold art."], "avoid": ["Busy type."]})
    store = _store(repository)
    store.edit(1, "edit", 1, None, "Bright art.", NOW)
    store.edit(2, "add", None, "avoid", "Thin type.", NOW)
    store.edit(3, "remove", 2, None, None, NOW)
    assert store.kept() == (
        (
            TasteRule(1, "drawn_to", "Bright art.", "edited"),
            TasteRule(3, "avoid", "Thin type.", "owner"),
        ),
        ("Busy type.", "Bold art."),
    )
    _built_version(
        repository,
        {
            "drawn_to": ["Bold art.", "Calm type."],
            "avoid": ["busy type", "THIN TYPE.", "Glossy finish."],
        },
    )
    [*_, (version, build_id, document)] = _versions(repository)
    assert version == 5 and build_id is not None
    assert [
        (rule["number"], rule["text"], rule["provenance"]) for rule in document["rules"]
    ] == [
        (1, "Bright art.", "edited"),
        (2, "Calm type.", "model"),
        (3, "Thin type.", "owner"),
        (4, "Glossy finish.", "model"),
    ]
    assert document["left_out"] == 3
    assert document["removed"] == ["Busy type.", "Bold art."]
    validate_cover_taste_versions(repository._connection)


def test_an_edit_during_a_build_is_merged_from_another_handle(tmp_path) -> None:
    """Dry proof c: the merge reads the newest version as `succeed` writes."""
    path = tmp_path / "edit-during-build.sqlite"
    builder = open_repository(path)
    owner = open_repository(path, authority=builder.authority)
    try:
        _built_version(builder, {"drawn_to": ["Bold art."]})
        seed = _Seed(builder)
        seed.many(10, start=5000)
        store = builder.cover_taste_store()
        store.schedule(NOW + DAY, True, LIMIT)
        build = store.pending()
        assert build is not None
        assert store.claim(build, NOW + DAY, "m/x", LIMIT) is None
        assert (
            owner.cover_taste_store().edit(
                1, "add", None, "avoid", "Busy type.", NOW + DAY
            )
            == 2
        )
        assert (
            store.succeed(
                build,
                NOW + DAY,
                _output(drawn_to=["Calm type."]),
                RECORD,
                failure="invalid_output",
            )
            == 3
        )
        rules = _versions(builder)[-1][2]["rules"]
        assert [(rule["text"], rule["provenance"]) for rule in rules] == [
            ("Calm type.", "model"),
            ("Busy type.", "owner"),
        ]
        # Negative control: an edit naming the pre-build version is stale.
        with pytest.raises(TasteEditError) as raised:
            owner.cover_taste_store().edit(2, "edit", 2, None, "Busy art.", NOW + DAY)
        assert raised.value.code == "stale"
    finally:
        owner.close()
        builder.close()


@pytest.mark.parametrize(
    ("usage", "failure", "stopped"),
    [(True, "invalid_output", None), (False, "unknown_charge", "unknown_charge")],
    ids=["usage", "no-usage"],
)
def test_a_merge_over_a_bound_fails_the_build_and_keeps_the_version(
    repository, usage, failure, stopped
) -> None:
    _built_version(repository)
    store = _store(repository)
    for index in range(20):
        store.edit(index + 1, "add", None, "avoid", f"Owner rule {index}.", NOW)
    _Seed(repository).many(10, start=7000)
    reply = TasteBuildReply(
        {
            "sections": {
                "drawn_to": [f"Model rule {n}." for n in range(21)],
                "avoid": [],
                "trade_offs": [],
                "keep_or_switch": [],
            }
        },
        usage,
    )
    _service(repository, _Client(reply), NOW + DAY).wake(NOW + DAY)
    assert _state(repository)[-1] == ("cadence", "failed", failure)
    assert len(_versions(repository)) == 21
    assert repository._connection.execute(
        "SELECT stopped FROM reviewer_task_requests WHERE day=11"
    ).fetchall() == [(stopped,)]


def test_kept_rules_that_leave_no_budget_schedule_no_cadence_build(
    repository,
) -> None:
    """S9: a build that could add nothing is not requested; Try again still is."""
    _built_version(repository)
    store = _store(repository)
    store.edit(1, "remove", 1, None, None, NOW)
    for index in range(40):
        store.edit(index + 2, "add", None, "avoid", f"Owner rule {index}.", NOW)
    _Seed(repository).many(10, start=8000)
    assert store.schedule(NOW + DAY, True, LIMIT) is False
    assert store.pending() is None
    store.edit(42, "remove", 1, None, None, NOW)
    assert store.schedule(NOW + DAY, True, LIMIT) is True


def test_the_version_table_admits_an_edit_row_only_without_a_build(
    repository,
) -> None:
    build = _built_version(repository)
    [(_version, _build, document)] = _versions(repository)
    connection = repository._connection
    edit = canonical_json({**document, "source": "edit", "version": 2})
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        connection.execute(
            "INSERT INTO cover_taste_profile_versions VALUES(2,?,?,?)",
            (build, NOW, edit),
        )
    build_row = canonical_json({**document, "version": 2})
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        connection.execute(
            "INSERT INTO cover_taste_profile_versions VALUES(2,NULL,?,?)",
            (NOW, build_row),
        )
    v1 = canonical_json(
        {**document, "schema_version": "cover-taste-profile-v1", "version": 2}
    )
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        connection.execute(
            "INSERT INTO cover_taste_profile_versions VALUES(2,NULL,?,?)",
            (NOW, v1),
        )
    connection.rollback()


def _worst_document(version: int, build_id: str) -> dict[str, object]:
    """Dry proof b: 40 rules of 8,192 four-byte code points and 100 removed
    texts of 16,384, under the closed v2 keys."""

    def astral(count: int, start: int) -> str:
        return "".join(
            chr(0x20000 + (start * 401 + index) % 42_720) for index in range(count)
        )

    sizes = [400] * 20 + [10] * 19 + [2]
    sections = ("drawn_to", "avoid", "trade_offs", "keep_or_switch")
    return {
        "build_id": build_id,
        "built_at": NOW,
        "input": RECORD,
        "left_out": 0,
        "model": "m/x",
        "removed": [
            astral(163 if index < 16 else 164, 100 + index) for index in range(100)
        ],
        "rules": [
            {
                "number": index + 1,
                "provenance": "owner",
                "section": sections[index // 10],
                "text": astral(size, index),
            }
            for index, size in enumerate(sizes)
        ],
        "schema_version": COVER_TASTE_PROFILE_SCHEMA,
        "source": "build",
        "version": version,
    }


def test_the_worst_version_document_fits_and_would_not_at_the_old_cap(
    repository,
) -> None:
    build = _built_version(repository)
    connection = repository._connection
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute(
        "INSERT INTO cover_taste_builds SELECT 'taste_build_' || "
        "printf('%032x', 1),trigger,NULL,state,failure,model,day+1,"
        "selection_watermark,requested_at,updated_at FROM cover_taste_builds "
        "WHERE build_id=?",
        (build,),
    )
    other = "taste_build_" + "%032x" % 1
    document = canonical_json(_worst_document(2, other))
    assert 65_536 < len(document.encode()) <= 262_144
    connection.execute(
        "INSERT INTO cover_taste_profile_versions VALUES(2,?,?,?)",
        (other, NOW, document),
    )
    validate_cover_taste_versions(connection)
    # Negative control: the same document breaks the old 65,536-byte cap.
    old = (
        _COVER_TASTE_TABLES["cover_taste_profile_versions"]
        .replace("cover_taste_profile_versions", "old_versions", 1)
        .replace("262144", "65536")
    )
    connection.execute(old)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        connection.execute(
            "INSERT INTO old_versions VALUES(2,?,?,?)", (other, NOW, document)
        )
    connection.rollback()


def _rewrite(repository, version: int, change: dict[str, object]) -> None:
    """Replace one stored document, as only a direct database edit could."""
    connection = repository._connection
    rows = connection.execute(
        "SELECT version,build_id,created_at,document FROM cover_taste_profile_versions"
    ).fetchall()
    for name in ("update", "delete"):
        connection.execute(f"DROP TRIGGER cover_taste_profile_versions_no_{name}")
    connection.execute("DELETE FROM cover_taste_profile_versions")
    for number, build_id, created_at, document in rows:
        value = json.loads(document)
        if number == version:
            value = {**value, **change}
            for key in [key for key, item in change.items() if item is None]:
                del value[key]
        connection.execute(
            "INSERT INTO cover_taste_profile_versions VALUES(?,?,?,?)",
            (number, build_id, created_at, canonical_json(value)),
        )


@pytest.mark.parametrize(
    ("version", "change"),
    [
        (2, {"model": "other/model"}),
        (2, {"left_out": 4}),
        (2, {"built_at": NOW + 1}),
        (1, {"notes": "x"}),
        (1, {"removed": [f"Rule {index}." for index in range(101)]}),
        (1, {"removed": ["Bold art.", "bold art"]}),
        (1, {"built_at": NOW + 1}),
        (
            1,
            {
                "rules": [
                    {
                        "number": 2,
                        "provenance": "model",
                        "section": "drawn_to",
                        "text": "Bold art.",
                    }
                ]
            },
        ),
        (1, {"source": "owner"}),
    ],
    ids=[
        "edit-model-differs",
        "edit-left-out-differs",
        "edit-built-at-differs",
        "extra-key",
        "removed-over-100",
        "removed-repeat",
        "build-built-at",
        "build-not-numbered-from-1",
        "source",
    ],
)
def test_validation_refuses_a_version_no_build_or_edit_wrote(
    repository, version, change
) -> None:
    _built_version(repository)
    _store(repository).edit(1, "edit", 1, None, "Bright art.", NOW)
    _rewrite(repository, version, change)
    with pytest.raises(ValueError):
        validate_cover_taste_versions(repository._connection)
    repository._connection.rollback()


def test_validation_refuses_an_edit_at_version_1(repository) -> None:
    _built_version(repository)
    _store(repository).edit(1, "edit", 1, None, "Bright art.", NOW)
    connection = repository._connection
    rows = connection.execute(
        "SELECT created_at,document FROM cover_taste_profile_versions WHERE version=2"
    ).fetchone()
    for name in ("update", "delete"):
        connection.execute(f"DROP TRIGGER cover_taste_profile_versions_no_{name}")
    connection.execute("DELETE FROM cover_taste_profile_versions")
    connection.execute(
        "INSERT INTO cover_taste_profile_versions VALUES(1,NULL,?,?)",
        (rows[0], canonical_json({**json.loads(rows[1]), "version": 1})),
    )
    with pytest.raises(ValueError):
        validate_cover_taste_versions(connection)
    connection.rollback()
