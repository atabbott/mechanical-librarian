"""What the composed application announces, and what the stream endpoint admits.

The channel's own behaviour is proven in `test_live_updates`. This cohort proves
the two things that connect it to the product: that every place an owner surface
would otherwise have had to poll now announces, and that the endpoint carrying
those announcements is authenticated, same-origin, uncached and value-free.
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.application.live_updates import RESYNC, LiveUpdateChannel
from mechanical_librarian.application.review_application import ReviewApplication
from mechanical_librarian.application.review_pipeline import (
    PipelineAction,
    ReviewPipelineError,
    ReviewPreparationPipeline,
)
from mechanical_librarian.interfaces.web import live_stream
from tests.support.review_web_doubles import (
    FakeActivityApplication,
    FakeReviewRepository,
    ReviewWebContext,
    review_web_context,
)
from tests.unit.review_app_fixture import create_transport_test_app

_PRODUCER = "f" * 64
_CASE = "review_case_" + "a" * 64


@pytest.fixture
def web() -> Iterator[ReviewWebContext[FakeReviewRepository]]:
    yield from review_web_context(FakeReviewRepository)


@pytest.fixture
def writes(
    web: ReviewWebContext[FakeReviewRepository],
) -> ReviewWebContext[FakeReviewRepository]:
    """The same application with the guarded write lane composed into it."""
    web.client.close()
    web.client = TestClient(
        create_transport_test_app(
            web.repository,
            ReviewWebConfiguration(
                canonical_origin="https://review.test",
                owner_password_hash_file=str(web.hash_path),
                session_lifetime_seconds=600,
            ),
            clock=lambda: web.now,
            field_application=FakeActivityApplication(),
        ),
        base_url="https://review.test",
    )
    web.cleanup.callback(web.client.close)
    return web


def _frames(text: str) -> list[dict[str, object]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in text.splitlines()
        if line.startswith("data: ")
    ]


# --- the endpoint -----------------------------------------------------------


def test_an_unauthenticated_subscribe_is_refused(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    response = web.client.get("/api/v1/updates")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_a_cross_origin_subscribe_is_refused(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    """A stream is the one read whose response keeps arriving after admission."""
    web.login()
    # Closed first so an admitted subscribe would end at once rather than hold
    # this test open: a refusal that regressed has to fail, not hang.
    _channel(web).close()

    response = web.client.get(
        "/api/v1/updates", headers={"Origin": "https://attacker.test"}
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "origin"


def _application(web: ReviewWebContext[FakeReviewRepository]) -> ReviewApplication:
    return cast("ReviewApplication", web.client.app.state.review_application)


def _channel(web: ReviewWebContext[FakeReviewRepository]) -> LiveUpdateChannel:
    return _application(web).updates


def _subscribe(
    web: ReviewWebContext[FakeReviewRepository],
    headers: dict[str, str],
    once_reading: Callable[[], None] = lambda: None,
) -> str:
    """Read one whole stream by ending it, rather than by waiting on a clock.

    A stream has no last frame, so a test that read until it expected to stop
    would be a sleep with an assertion attached. Closing the channel the moment
    the reader appears is the same event the graceful-shutdown contract uses,
    and it makes the response finite and its content exact.
    """
    channel = _channel(web)
    ready = threading.Event()

    def close_once_reading() -> None:
        while not ready.is_set():
            if channel.subscribers:
                once_reading()
                channel.close()
                return
            time.sleep(0.001)

    closer = threading.Thread(target=close_once_reading)
    closer.start()
    try:
        response = web.client.get("/api/v1/updates", headers=headers)
    finally:
        ready.set()
        closer.join(timeout=5)
    assert response.status_code == 200, response.text
    return response.text


def test_a_same_origin_subscribe_is_private_uncached_and_unbuffered(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    web.login()
    channel = _channel(web)
    channel.close()

    response = web.client.get("/api/v1/updates", headers=web.origin)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.text == f"retry: {live_stream.RECONNECT_MILLISECONDS}\n\n"


def test_a_first_connect_is_told_to_look_at_everything_then_carries_a_change(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    web.login()
    channel = _channel(web)

    def publish_once_reading() -> None:
        # The published position is the channel's business; a wake callback
        # hands off a signal and returns nothing, because it runs on the
        # publishing thread while the channel lock is held.
        channel.publish("library", _CASE)

    payloads = _frames(_subscribe(web, web.origin, publish_once_reading))

    assert payloads[0]["topic"] == RESYNC
    assert payloads[-1] == {"sequence": 1, "subject": _CASE, "topic": "library"}
    assert all(set(payload) == {"sequence", "subject", "topic"} for payload in payloads)


def test_a_resume_position_is_honoured_rather_than_replayed_from_the_start(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    web.login()
    channel = _channel(web)
    first = channel.publish("preparation")
    channel.publish("activity")

    payloads = _frames(
        _subscribe(web, {**web.origin, "Last-Event-ID": f"{channel.epoch}-{first}"})
    )

    assert [payload["topic"] for payload in payloads] == ["activity"]


# --- what the application announces -----------------------------------------


def test_recording_a_decision_announces_the_case_its_row_and_the_write_queue(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    """Nothing here waits for a poll, and nothing here carries the decision."""
    login = web.login()
    detail = web.client.get(f"/api/v1/review-cases/{web.case.review_case_id}").json()
    field = detail["fields"][0]
    channel = _channel(web)
    subscription = channel.subscribe(channel.sequence)

    recorded = web.client.post(
        f"/api/v1/review-cases/{web.case.review_case_id}/decisions",
        headers={
            **web.origin,
            "X-CSRF-Token": str(login["csrf_token"]),
            "Idempotency-Key": "announced-decision-0001",
        },
        json={
            "destination": field["destination"],
            "outcome": "accept",
            "proposal_id": field["proposal_id"],
            "selected_value": field["proposed_values"][0],
            "target_fingerprint": field["target_fingerprint"],
        },
    )

    assert recorded.status_code == 200, recorded.text
    announced = subscription.drain(0)
    assert {(update.topic, update.subject) for update in announced} == {
        ("case", web.case.review_case_id),
        ("library", web.case.review_case_id),
        ("activity", None),
    }
    # The response tells the tab that recorded it exactly which positions its
    # own decision caused, so that tab can recognise its own echo instead of
    # rebuilding the Library from it (#635, #641). Correlation by identity is
    # only worth anything if these are the same numbers the channel assigned:
    # a token that names positions nothing was published at would leave the
    # mechanism silently inert, refetching on every decision again.
    assert sorted(recorded.json()["invalidation_sequences"]) == sorted(
        update.sequence for update in announced
    )
    # The Library row's position is the one the client acts on, and it must
    # name the case, because a subjectless `library` frame is what the channel
    # emits when a subscription collapses and may stand for many other works.
    row = next(update for update in announced if update.topic == "library")
    assert row.subject == web.case.review_case_id
    assert row.sequence in recorded.json()["invalidation_sequences"]


def test_enabling_and_revoking_write_consent_announce_the_write_queue(
    writes: ReviewWebContext[FakeReviewRepository],
) -> None:
    application = _application(writes)
    subscription = application.updates.subscribe(application.updates.sequence)

    application.enable_activity((("fiction", ("title",)),))
    granted = [update.topic for update in subscription.drain(0)]
    application.revoke_activity("primary", "fiction")
    revoked = [update.topic for update in subscription.drain(0)]

    # Drained between the two, because coalescing would otherwise let one
    # announcement stand in for both and leave the other unproven.
    assert (granted, revoked) == (["activity"], ["activity"])


def test_shutting_down_ends_every_open_reader(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    application = _application(web)
    subscription = application.updates.subscribe(0)

    application.stop()

    assert subscription.closed


# --- what the preparation pipeline announces ---------------------------------


@pytest.fixture
def application_path() -> Iterator[Path]:
    """A created, closed application database in a directory of its own."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "application.sqlite3"
        open_repository(path).close()
        yield path


def _action(stage: str, *, review_case_id: str | None = None) -> PipelineAction:
    return PipelineAction(
        action_id="review_action_" + "b" * 64,
        scan_id="review_scan_" + "c" * 64,
        stage=stage,
        attempt=1,
        connection="primary",
        library="fiction",
        library_id="lib-1",
        producer_fingerprint=_PRODUCER,
        catalogue_generation=1,
        catalogue_fingerprint="d" * 64,
        item_id="item-1",
        file_identity="e" * 64,
        source_revision=1,
        source_fingerprint="a" * 64,
        review_case_id=review_case_id,
    )


class _AnnouncingRepository:
    """Only the two transitions the announcement contract is written against."""

    def advance_review_pipeline_action(self, *_arguments: object) -> None:
        return None

    def fail_review_pipeline_action(self, *_arguments: object) -> None:
        return None


def _pipeline(announced: list[tuple[str, str | None]]) -> ReviewPreparationPipeline:
    return ReviewPreparationPipeline(
        repository_factory=lambda: (_ for _ in ()).throw(
            ReviewPipelineError("persistence")
        ),
        libraries=(("primary", "fiction", "lib-1", _PRODUCER),),
        discover=lambda *_: (1, "d" * 64, ()),
        extract=lambda *_: ("report", False),
        providers=lambda *_: (),
        propose=lambda *_: "proposal",
        admit=lambda *_: ("case", None),
        project=lambda *_: True,
        announce=lambda topic, subject: announced.append((topic, subject)),
    )


def test_a_mid_pipeline_stage_announces_preparation_and_nothing_else() -> None:
    """An extraction finishing changes the snapshot, not the published set."""
    announced: list[tuple[str, str | None]] = []
    pipeline = _pipeline(announced)

    pipeline._advance(
        _AnnouncingRepository(),  # type: ignore[arg-type]  # reason: only the two transitions the announcement is written against
        _action("extraction"),
        "report-1",
        claim_next=False,
    )

    assert announced == [("preparation", None)]


def test_a_committed_package_cutover_announces_the_row_and_the_work_it_published() -> (
    None
):
    announced: list[tuple[str, str | None]] = []
    pipeline = _pipeline(announced)

    pipeline._advance(
        _AnnouncingRepository(),  # type: ignore[arg-type]  # reason: only the two transitions the announcement is written against
        _action("projection", review_case_id=_CASE),
        None,
        claim_next=False,
    )

    assert announced == [
        ("preparation", None),
        ("library", _CASE),
        ("case", _CASE),
    ]


def test_a_terminal_failure_announces_the_work_an_owner_can_now_see() -> None:
    announced: list[tuple[str, str | None]] = []
    pipeline = _pipeline(announced)

    pipeline._fail(
        _AnnouncingRepository(),  # type: ignore[arg-type]  # reason: only the two transitions the announcement is written against
        _action("provider"),
        "provider",
        False,
    )

    assert announced == [("preparation", None), ("library", None)]


# --- stage counts ------------------------------------------------------------


def test_the_snapshot_counts_works_at_every_stage_an_owner_reads(
    application_path: Path,
) -> None:
    """`checked/total` answers "how far"; only these answer "what is it doing"."""
    repository = open_repository(application_path)
    progress = repository.request_review_scan(
        "primary", "fiction", "lib-1", _PRODUCER, 10
    )
    assert dict(progress.stages) == {
        "queued": 0,
        "extracting": 0,
        "providers": 0,
        "proposals": 0,
        "admission": 0,
        "projection": 0,
        "ready": 0,
        "failed": 0,
    }
    repository.close()


def test_every_stage_bucket_is_named_in_owner_reading_order(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    progress = repository.request_review_scan(
        "primary", "fiction", "lib-1", _PRODUCER, 10
    )
    repository.close()

    assert [stage for stage, _count in progress.stages] == [
        "queued",
        "extracting",
        "providers",
        "proposals",
        "admission",
        "projection",
        "ready",
        "failed",
    ]


# --- waiting reasons ---------------------------------------------------------


def test_waiting_is_split_into_the_reasons_an_owner_can_act_on(
    writes: ReviewWebContext[FakeReviewRepository],
) -> None:
    """`46 Waiting safely` is four different next steps, and this separates them."""
    writes.login()

    waiting = writes.client.get("/api/v1/activity").json()["waiting"]

    assert set(waiting) == {
        "applying",
        "awaiting_permission",
        "held_for_recovery",
        "retrying",
        "scheduled",
    }
    assert waiting["awaiting_permission"] == 3
    assert waiting["scheduled"] == 0


def test_consent_moves_prepared_work_out_of_awaiting_permission(
    writes: ReviewWebContext[FakeReviewRepository],
) -> None:
    session = writes.login()
    before = writes.client.get("/api/v1/activity").json()["waiting"]
    assert before["awaiting_permission"] == 3

    writes.client.post(
        "/api/v1/activity/consent",
        headers={**writes.origin, "x-csrf-token": str(session["csrf_token"])},
        json={"grants": [{"library": "fiction", "destinations": ["title"]}]},
    )

    waiting = writes.client.get("/api/v1/activity").json()["waiting"]
    assert waiting["awaiting_permission"] == 0
    assert waiting["scheduled"] == 3


def test_a_destination_reports_the_prepared_work_its_permission_would_carry(
    writes: ReviewWebContext[FakeReviewRepository],
) -> None:
    writes.login()

    capabilities = writes.client.get("/api/v1/activity").json()["capabilities"]

    assert [
        (item["library"], destination["destination"], destination["prepared"])
        for item in capabilities
        for destination in item["destinations"]
    ] == [("fiction", "title", 2), ("history", "title", 1)]
