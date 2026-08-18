"""The invalidation channel and the stream that encodes it.

Every assertion here is about what the substrate refuses to do -- carry state,
grow without bound, answer a position it cannot honour exactly, or outlive the
shutdown that closed it -- because those are the properties #641 relies on and
the ones a later change would quietly remove.
"""

from __future__ import annotations

import asyncio
import json
import threading

import pytest

from mechanical_librarian.application.live_updates import (
    RESYNC,
    TOPICS,
    LiveUpdate,
    LiveUpdateCapacityError,
    LiveUpdateChannel,
)
from mechanical_librarian.interfaces.web import live_stream


def _topics(updates: tuple[LiveUpdate, ...]) -> list[str]:
    return [update.topic for update in updates]


def test_a_reader_receives_every_topic_in_one_total_order() -> None:
    channel = LiveUpdateChannel()
    subscription = channel.subscribe(0)

    first = channel.publish("preparation")
    second = channel.publish("library", "review_case_" + "a" * 64)
    third = channel.publish("activity")

    assert (first, second, third) == (1, 2, 3)
    taken = subscription.drain(0)
    assert [(update.sequence, update.topic) for update in taken] == [
        (1, "preparation"),
        (2, "library"),
        (3, "activity"),
    ]


def test_repeated_invalidation_of_one_identity_coalesces_to_its_latest_position() -> (
    None
):
    """A work advancing through six stages is one thing to look at, not six.

    This is what keeps a fifty-work publication from becoming fifty refetches,
    and it is only sound because an update carries no state: collapsing two
    state-carrying events would lose the earlier one's content.
    """
    channel = LiveUpdateChannel()
    subscription = channel.subscribe(0)

    for _ in range(200):
        channel.publish("preparation")
    last = channel.publish("preparation")

    taken = subscription.drain(0)
    assert len(taken) == 1
    assert taken[0].sequence == last


def test_a_reader_that_outgrows_its_bound_collapses_to_whole_topics() -> None:
    """The slow-consumer answer is more invalidation, never less."""
    channel = LiveUpdateChannel(pending_maximum=4)
    subscription = channel.subscribe(0)

    for index in range(64):
        channel.publish("case", f"review_case_{index:064x}")

    taken = subscription.drain(0)
    assert 1 <= len(taken) <= 4
    assert {update.topic for update in taken} == {"case"}
    assert any(update.subject is None for update in taken)
    assert max(update.sequence for update in taken) == channel.sequence


def test_a_resume_position_is_answered_with_exactly_what_followed_it() -> None:
    channel = LiveUpdateChannel()
    channel.publish("preparation")
    marker = channel.publish("activity")
    channel.publish("library", "review_case_" + "b" * 64)

    subscription = channel.subscribe(marker)

    taken = subscription.drain(0)
    assert [(update.sequence, update.topic) for update in taken] == [(3, "library")]


def test_a_reader_at_the_head_of_the_order_is_given_nothing() -> None:
    channel = LiveUpdateChannel()
    channel.publish("preparation")

    subscription = channel.subscribe(channel.sequence)

    assert subscription.drain(0) == ()


def test_a_position_the_retained_ring_has_passed_is_answered_conservatively() -> None:
    channel = LiveUpdateChannel(retention=4)
    for _ in range(10):
        channel.publish("preparation")

    subscription = channel.subscribe(1)

    assert _topics(subscription.drain(0)) == [RESYNC]


def test_a_position_from_a_previous_process_is_answered_conservatively() -> None:
    """A restarted channel is a different total order, not a rewound one."""
    channel = LiveUpdateChannel()
    channel.publish("preparation")

    subscription = channel.subscribe(4_096)

    assert _topics(subscription.drain(0)) == [RESYNC]


def test_a_first_connect_is_told_to_look_at_everything_once() -> None:
    channel = LiveUpdateChannel()
    subscription = channel.subscribe(None)

    assert _topics(subscription.drain(0)) == [RESYNC]


def test_an_unknown_topic_is_refused_rather_than_carried() -> None:
    channel = LiveUpdateChannel()
    with pytest.raises(ValueError):
        channel.publish("everything")


def test_closing_the_channel_ends_every_open_reader() -> None:
    channel = LiveUpdateChannel()
    first = channel.subscribe(0)
    second = channel.subscribe(0)

    channel.close()

    assert first.closed and second.closed
    assert channel.subscribers == 0
    assert channel.publish("preparation") == 0
    assert channel.subscribe(0).closed


def test_a_reader_beyond_the_subscriber_bound_is_refused() -> None:
    channel = LiveUpdateChannel(subscriber_maximum=2)
    channel.subscribe(0)
    channel.subscribe(0)

    with pytest.raises(LiveUpdateCapacityError):
        channel.subscribe(0)


def test_a_released_reader_frees_its_place() -> None:
    channel = LiveUpdateChannel(subscriber_maximum=1)
    subscription = channel.subscribe(0)

    subscription.close()

    assert channel.subscribers == 0
    assert not channel.subscribe(0).closed


def test_a_blocked_reader_is_woken_by_a_publisher_on_another_thread() -> None:
    channel = LiveUpdateChannel()
    subscription = channel.subscribe(0)
    taken: list[tuple[LiveUpdate, ...]] = []

    reader = threading.Thread(target=lambda: taken.append(subscription.drain(5)))
    reader.start()
    channel.publish("activity")
    reader.join(timeout=5)

    assert not reader.is_alive()
    assert _topics(taken[0]) == ["activity"]


def test_an_installed_wakeup_fires_for_work_that_arrived_before_it() -> None:
    """The stream installs its wakeup after subscribing, so the seed must survive."""
    channel = LiveUpdateChannel()
    subscription = channel.subscribe(None)
    woken = threading.Event()

    subscription.wake_with(woken.set)

    assert woken.is_set()


# --- the wire ---------------------------------------------------------------


def test_a_frame_carries_the_three_invalidation_fields_and_nothing_else() -> None:
    """The structural half of "events contain no state".

    There is no field on this wire that could hold a count, a work record, a
    status string, a path, or a provider payload, and this is what asserts it.
    """
    encoded = live_stream.frame(
        LiveUpdate(7, "case", "review_case_" + "c" * 64), "abc123"
    ).decode()

    lines = encoded.rstrip("\n").split("\n")
    assert lines[0] == "id: abc123-7"
    assert lines[1] == "event: case"
    payload = json.loads(lines[2].removeprefix("data: "))
    assert set(payload) == {"sequence", "subject", "topic"}
    assert payload == {
        "sequence": 7,
        "subject": "review_case_" + "c" * 64,
        "topic": "case",
    }


@pytest.mark.parametrize("topic", TOPICS)
def test_no_topic_widens_the_frame_beyond_those_three_fields(topic: str) -> None:
    encoded = live_stream.frame(LiveUpdate(1, topic), "epoch").decode()
    payload = json.loads(encoded.split("data: ", 1)[1].strip())
    assert set(payload) == {"sequence", "subject", "topic"}
    assert payload["subject"] is None


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "12",
        "other-12",
        "epoch-",
        "epoch-x",
        "epoch-" + "9" * 40,
        "epoch--1",
    ],
)
def test_an_unreadable_resume_position_is_no_position(header: str | None) -> None:
    assert live_stream.last_event_position(header, "epoch") is None


def test_a_resume_position_from_this_order_is_read_back() -> None:
    assert live_stream.last_event_position("epoch-41", "epoch") == 41


async def _collect(iterator, limit: int) -> list[bytes]:
    frames: list[bytes] = []
    async for value in iterator:
        frames.append(value)
        if len(frames) >= limit:
            break
    return frames


def test_the_stream_opens_with_its_reconnect_hint_then_carries_updates() -> None:
    async def exercise() -> list[bytes]:
        channel = LiveUpdateChannel()
        subscription = channel.subscribe(0)
        channel.publish("library", "review_case_" + "d" * 64)
        return await _collect(
            live_stream.stream_frames(
                subscription,
                channel.epoch,
                still_authenticated=lambda: True,
                heartbeat_seconds=0.01,
            ),
            2,
        )

    frames = asyncio.run(exercise())
    assert frames[0] == f"retry: {live_stream.RECONNECT_MILLISECONDS}\n\n".encode()
    assert b"event: library" in frames[1]


def test_an_idle_stream_writes_a_heartbeat_that_carries_no_position() -> None:
    async def exercise() -> list[bytes]:
        channel = LiveUpdateChannel()
        subscription = channel.subscribe(0)
        return await _collect(
            live_stream.stream_frames(
                subscription,
                channel.epoch,
                still_authenticated=lambda: True,
                heartbeat_seconds=0.01,
            ),
            2,
        )

    frames = asyncio.run(exercise())
    assert frames[1] == b": heartbeat\n\n"
    assert b"id:" not in frames[1]


def test_a_lapsed_session_ends_the_stream_it_opened_under() -> None:
    async def exercise() -> list[bytes]:
        channel = LiveUpdateChannel()
        subscription = channel.subscribe(0)
        return [
            value
            async for value in live_stream.stream_frames(
                subscription,
                channel.epoch,
                still_authenticated=lambda: False,
                heartbeat_seconds=0.01,
                # Bounded so that a stream which stops re-checking the session
                # ends by its own lifetime instead of hanging this test: the
                # frames it emits meanwhile are what fails the assertion.
                maximum_seconds=0.05,
            )
        ]

    assert asyncio.run(exercise()) == [
        f"retry: {live_stream.RECONNECT_MILLISECONDS}\n\n".encode()
    ]


def test_a_stream_is_bounded_by_its_own_lifetime() -> None:
    async def exercise() -> list[bytes]:
        channel = LiveUpdateChannel()
        subscription = channel.subscribe(0)
        clock = iter((0.0, 61.0))
        return [
            value
            async for value in live_stream.stream_frames(
                subscription,
                channel.epoch,
                still_authenticated=lambda: True,
                heartbeat_seconds=0.01,
                maximum_seconds=60.0,
                monotonic=lambda: next(clock),
            )
        ]

    assert asyncio.run(exercise()) == [
        f"retry: {live_stream.RECONNECT_MILLISECONDS}\n\n".encode()
    ]


def test_a_closed_channel_ends_the_stream_and_releases_its_reader() -> None:
    async def exercise() -> tuple[list[bytes], int]:
        channel = LiveUpdateChannel()
        subscription = channel.subscribe(0)
        frames: list[bytes] = []
        stream = live_stream.stream_frames(
            subscription,
            channel.epoch,
            still_authenticated=lambda: True,
            heartbeat_seconds=0.01,
        )
        frames.append(await anext(stream))
        channel.close()
        async for value in stream:
            frames.append(value)
        return frames, channel.subscribers

    frames, subscribers = asyncio.run(exercise())
    assert frames == [f"retry: {live_stream.RECONNECT_MILLISECONDS}\n\n".encode()]
    assert subscribers == 0
