"""Encode the invalidation channel as one authenticated server-sent stream.

One stream per tab, multiplexed over every topic, riding the session cookie the
rest of the API already rides. It is a long HTTP response and nothing more: the
existing origin check refuses a cross-origin subscribe, the existing security
headers reach it as they reach any other response and make it ``no-store,
private`` -- asserted on the streamed response itself rather than assumed, in
``test_the_stream_is_private_uncached_and_unbuffered`` -- and the graceful-shutdown
contract ends it by closing the channel, so there is no separate
connection-lifecycle machinery to operate (#641).

The wire carries invalidation only. Every frame's ``data`` is a closed object of
exactly ``sequence``, ``subject`` and ``topic``; ``subject`` is either null or a
review case identity the reader already holds through the HTTP surface. There is
nowhere in this encoding to put a count, a work record, a status string, a path,
a credential, or a provider payload, which is how #641's "no state on the wire"
acceptance is kept structurally rather than by review.

The stream parks on an ``asyncio.Event`` that publishing threads set through
the running loop, and touches nothing that blocks. Holding a bounded worker
thread for its whole life would let a few idle tabs starve ordinary requests.
Library and Attention also use asynchronous transport handlers to observe
disconnects, while their bounded reads still execute in worker threads.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable

from mechanical_librarian.application.live_updates import (
    LiveUpdate,
    LiveUpdateSubscription,
)

__all__ = [
    "HEARTBEAT_SECONDS",
    "MAXIMUM_STREAM_SECONDS",
    "RECONNECT_MILLISECONDS",
    "frame",
    "last_event_position",
    "stream_frames",
]

# Long enough that an idle library costs almost nothing, short enough that a
# proxy or a sleeping laptop silently holding a dead stream is noticed and
# reconnected well inside a browsing session.
HEARTBEAT_SECONDS = 15.0
# What the browser waits before its native reconnect, presented once at the top
# of the stream so a flapping process cannot provoke a reconnect storm.
RECONNECT_MILLISECONDS = 3_000
# An owner session outlives a stream by design: the response is bounded so a
# session that lapses cannot keep being read through a connection opened before
# it did. The browser reconnects on its own and re-authenticates.
MAXIMUM_STREAM_SECONDS = 600.0


def last_event_position(header: str | None, epoch: str) -> int | None:
    """Read the browser's ``Last-Event-ID`` as a resume position in this order.

    Event ids are ``<epoch>-<sequence>``. A position from another epoch belongs
    to another process's total order and is no position here; so is anything
    unparseable. Both are answered with a resync rather than a refusal, because
    a reader whose position cannot be honoured still deserves a stream -- it
    just has to look at everything once.
    """
    if header is None or not 1 <= len(header) <= 64:
        return None
    prefix, separator, sequence = header.partition("-")
    if not separator or prefix != epoch or not 1 <= len(sequence) <= 19:
        return None
    if not sequence.isdigit():
        return None
    return int(sequence)


def frame(update: LiveUpdate, epoch: str) -> bytes:
    """One server-sent frame carrying exactly the three invalidation fields."""
    payload = json.dumps(
        {
            "sequence": update.sequence,
            "subject": update.subject,
            "topic": update.topic,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return (
        f"id: {epoch}-{update.sequence}\nevent: {update.topic}\ndata: {payload}\n\n"
    ).encode()


async def stream_frames(
    subscription: LiveUpdateSubscription,
    epoch: str,
    *,
    still_authenticated: Callable[[], bool],
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
    maximum_seconds: float = MAXIMUM_STREAM_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
) -> AsyncIterator[bytes]:
    """Yield frames until the channel closes, the session lapses, or time is up."""
    signal = asyncio.Event()
    loop = asyncio.get_running_loop()

    def wake() -> None:
        loop.call_soon_threadsafe(signal.set)

    deadline = monotonic() + maximum_seconds
    subscription.wake_with(wake)
    try:
        yield f"retry: {RECONNECT_MILLISECONDS}\n\n".encode()
        while True:
            if subscription.closed:
                # Flush before ending. An invalidation already queued for this
                # reader is one the store has already made true, and dropping it
                # would make the reader's `Last-Event-ID` claim a position it
                # never actually saw.
                for update in subscription.take():
                    yield frame(update, epoch)
                return
            if not still_authenticated():
                return
            remaining = deadline - monotonic()
            if remaining <= 0:
                return
            try:
                await asyncio.wait_for(signal.wait(), min(heartbeat_seconds, remaining))
            except TimeoutError:
                pass
            signal.clear()
            batch = subscription.take()
            if not batch:
                # A comment frame carries no id, so a heartbeat never moves the
                # reader's resume position. It is also the only thing that
                # notices a client that vanished without a close: the write
                # fails and this response ends.
                yield b": heartbeat\n\n"
                continue
            for update in batch:
                yield frame(update, epoch)
    finally:
        subscription.close()
