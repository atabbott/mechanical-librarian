"""One ordered invalidation channel every owner surface subscribes to.

The channel carries *invalidation*, never state. An update names the topic that
changed, optionally the one identity inside it that changed, and the sequence
number at which it changed. Nothing else fits in it: ``LiveUpdate`` has three
fields and no room for a count, a record, or a status string. A surface that
receives one refetches the authoritative answer over the existing HTTP surface,
so push decides *when* to look and HTTP stays the truth of *what is there*.
That is the property this shape exists to keep: an update that carries no state
cannot drift from the store it is announcing (#641).

Coalescing is what makes a slow consumer structurally impossible rather than a
condition to detect. A subscription holds a bounded map of pending identities
rather than a queue of updates, so a thousand advancements of one work collapse
into one pending entry, and a subscription that has outgrown its bound collapses
to topic-wide invalidation -- strictly more conservative than the per-identity
updates it replaces, because it makes the reader look at more, not less.

Resume is the same property read backwards. The channel retains a bounded ring
of recent updates; a reader presenting the last sequence it saw is given exactly
the updates after it. A reader whose position has fallen out of the ring cannot
be answered exactly, so it is answered conservatively with ``RESYNC``, which
names every topic at once.
"""

from __future__ import annotations

import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass

__all__ = [
    "LiveUpdate",
    "LiveUpdateCapacityError",
    "LiveUpdateChannel",
    "LiveUpdateSubscription",
    "RESYNC",
    "TOPICS",
]

# The invalidation vocabulary. Each name is a thing a surface can refetch on its
# own, which is what makes this a seam rather than a #641-shaped list: a later
# surface subscribes by naming a topic, and the substrate does not change.
#
#   `preparation` -- the preparation snapshot or a target-connection condition.
#   `library`     -- the published set of works, or one published row's content.
#   `case`        -- one work's current package and detail, named by its case.
#   `activity`    -- guarded write consent, readiness, queue state and ledger.
TOPICS = ("activity", "case", "library", "preparation")
# Not a topic a producer publishes: the control update the channel itself emits
# on first connect, and when it cannot answer a reader's position exactly.
RESYNC = "resync"

_DEFAULT_RETENTION = 512
_DEFAULT_PENDING = 64
_DEFAULT_SUBSCRIBERS = 32


class LiveUpdateCapacityError(RuntimeError):
    """More concurrent readers than one owner's browser can honestly need."""

    code = "capacity"


@dataclass(frozen=True, slots=True)
class LiveUpdate:
    """What changed, which identity inside it, and when in the total order.

    ``subject`` is an opaque identity the subscriber already holds through the
    HTTP surface -- a review case id -- and never a projection of the thing it
    names. It is present so one work's detail can be refetched without
    refetching the Library that contains it, which is the whole of #641's
    selected-work acceptance.
    """

    sequence: int
    topic: str
    subject: str | None = None


class LiveUpdateSubscription:
    """One reader's coalescing position in the channel.

    A reader waits either by blocking on ``drain`` or, when it runs on an event
    loop, by installing ``wake_with`` and calling ``take`` once woken. The second
    form is what the HTTP stream uses: every route in this adapter is a
    synchronous ``def`` served from a bounded worker threadpool, and a stream
    that parked one of those threads for its whole life would spend the pool on
    idle tabs.
    """

    def __init__(self, channel: LiveUpdateChannel, pending_maximum: int) -> None:
        self._channel = channel
        self._pending_maximum = pending_maximum
        self._pending: dict[tuple[str, str | None], int] = {}
        self._ready = threading.Event()
        self._wake: Callable[[], None] | None = None
        self._closed = False

    def wake_with(self, wake: Callable[[], None]) -> None:
        """Install a wakeup for a reader that cannot block on an Event.

        It is called from a publishing thread while the channel lock is held, so
        it must do no more than hand a signal to another thread -- in practice
        ``loop.call_soon_threadsafe``.
        """
        with self._channel.lock:
            self._wake = wake
        if self._pending or self._closed:
            wake()

    def offer(self, update: LiveUpdate) -> None:
        """Record one update. The caller holds the channel lock; never blocks."""
        self._pending[update.topic, update.subject] = update.sequence
        if len(self._pending) > self._pending_maximum:
            # Collapse to topic-wide invalidation rather than dropping a reader
            # or growing without bound. Each collapsed entry keeps the highest
            # sequence it replaces, so a resume position never moves backwards.
            collapsed: dict[tuple[str, str | None], int] = {}
            for (topic, _subject), sequence in self._pending.items():
                collapsed[topic, None] = max(collapsed.get((topic, None), 0), sequence)
            self._pending = collapsed
        self._ready.set()
        if self._wake is not None:
            self._wake()

    def take(self) -> tuple[LiveUpdate, ...]:
        """Take everything pending without waiting, in sequence order."""
        with self._channel.lock:
            self._ready.clear()
            taken = tuple(
                LiveUpdate(sequence, topic, subject)
                for (topic, subject), sequence in self._pending.items()
            )
            self._pending.clear()
        return tuple(sorted(taken, key=lambda update: update.sequence))

    def drain(self, timeout: float) -> tuple[LiveUpdate, ...]:
        """Wait up to ``timeout`` seconds, then take everything pending.

        An empty result means the wait elapsed with nothing pending, which is
        the caller's cue to write a heartbeat; ``closed`` distinguishes that
        from a channel that has shut down.
        """
        self._ready.wait(timeout)
        return self.take()

    @property
    def closed(self) -> bool:
        return self._closed

    def end(self) -> None:
        """Mark this reader finished and wake it. The channel calls this."""
        self._closed = True
        self._ready.set()
        if self._wake is not None:
            self._wake()

    def close(self) -> None:
        self._channel.release(self)


class LiveUpdateChannel:
    """The process-wide ordered invalidation log, bounded in both directions."""

    def __init__(
        self,
        *,
        retention: int = _DEFAULT_RETENTION,
        pending_maximum: int = _DEFAULT_PENDING,
        subscriber_maximum: int = _DEFAULT_SUBSCRIBERS,
    ) -> None:
        if retention < 1 or pending_maximum < 1 or subscriber_maximum < 1:
            raise ValueError("live update bounds must be positive")
        self.lock = threading.Lock()
        # The sequence is in-process by deliberate expansion boundary: no
        # durable event system, only the cursor the browser flow needs. That
        # makes a restarted process a different total order, and a reader
        # presenting a position from the previous one has to be told so rather
        # than silently matched against the new counter. The epoch says which
        # order a position belongs to; a position from any other order is
        # answered with a resync.
        self.epoch = secrets.token_hex(8)
        self._retention = retention
        self._pending_maximum = pending_maximum
        self._subscriber_maximum = subscriber_maximum
        self._sequence = 0
        self._retained: list[LiveUpdate] = []
        self._subscriptions: list[LiveUpdateSubscription] = []
        self._closed = False

    @property
    def sequence(self) -> int:
        with self.lock:
            return self._sequence

    @property
    def closed(self) -> bool:
        with self.lock:
            return self._closed

    @property
    def subscribers(self) -> int:
        with self.lock:
            return len(self._subscriptions)

    def publish(self, topic: str, subject: str | None = None) -> int:
        """Record one invalidation and fan it out; returns its sequence number.

        Publishing never fails a producer: the preparation pipeline and the
        guarded write lane call this from inside their own durable work, and a
        notification that could raise would turn a browser convenience into a
        way to fail a stage. An unknown topic is the one exception, because it
        is a programming error rather than a runtime condition.
        """
        if topic not in TOPICS:
            raise ValueError(f"unknown live update topic: {topic}")
        with self.lock:
            if self._closed:
                return self._sequence
            self._sequence += 1
            update = LiveUpdate(self._sequence, topic, subject)
            self._retained.append(update)
            if len(self._retained) > self._retention:
                del self._retained[: len(self._retained) - self._retention]
            for subscription in self._subscriptions:
                subscription.offer(update)
            return update.sequence

    def _since(self, last_sequence: int | None) -> tuple[LiveUpdate, ...]:
        """Updates after ``last_sequence``; the caller holds the lock."""
        if last_sequence is None or last_sequence < 0:
            return (LiveUpdate(self._sequence, RESYNC),)
        if last_sequence > self._sequence:
            # A position ahead of this order can only come from another one.
            return (LiveUpdate(self._sequence, RESYNC),)
        if last_sequence == self._sequence:
            return ()
        oldest = self._retained[0].sequence if self._retained else self._sequence + 1
        if last_sequence + 1 < oldest:
            return (LiveUpdate(self._sequence, RESYNC),)
        return tuple(
            update for update in self._retained if update.sequence > last_sequence
        )

    def subscribe(self, last_sequence: int | None = None) -> LiveUpdateSubscription:
        """Open one reader, seeded from the position it presents.

        A closed channel still answers, with a reader that is already closed, so
        a subscribe racing shutdown ends its response rather than holding a
        connection open past the point the owner asked it to stop.
        """
        subscription = LiveUpdateSubscription(self, self._pending_maximum)
        with self.lock:
            if self._closed:
                subscription.end()
                return subscription
            if len(self._subscriptions) >= self._subscriber_maximum:
                raise LiveUpdateCapacityError()
            for update in self._since(last_sequence):
                subscription.offer(update)
            self._subscriptions.append(subscription)
        return subscription

    def release(self, subscription: LiveUpdateSubscription) -> None:
        with self.lock:
            if subscription in self._subscriptions:
                self._subscriptions.remove(subscription)
        subscription.end()

    def close(self) -> None:
        """End every open reader. Part of the graceful-shutdown contract."""
        with self.lock:
            self._closed = True
            open_subscriptions = tuple(self._subscriptions)
            self._subscriptions.clear()
            self._retained.clear()
        for subscription in open_subscriptions:
            subscription.end()
