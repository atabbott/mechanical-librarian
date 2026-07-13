"""Bounded structured logging and counters for inventory poll lifecycle events."""

from __future__ import annotations

import logging
from collections import Counter
from threading import Lock

from mechanical_librarian.application.inventory_polling import InventoryPollEvent


class LoggingInventoryPollTelemetry:
    """Record fixed-schema events without titles, paths, endpoints, or credentials."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("mechanical_librarian.inventory_poll")
        self._counters: Counter[tuple[str, str, str]] = Counter()
        self._lock = Lock()

    def record(self, event: InventoryPollEvent) -> None:
        failure = (
            event.failure.value
            if hasattr(event.failure, "value")
            else event.failure or "none"
        )
        labels = (event.adapter.value, event.stage.value, failure)
        with self._lock:
            self._counters[labels] += 1
            self._counters[(event.adapter.value, "pages", event.stage.value)] += (
                event.usage.pages
            )
            self._counters[(event.adapter.value, "items", event.stage.value)] += (
                event.usage.items
            )
            self._counters[(event.adapter.value, "bytes", event.stage.value)] += (
                event.usage.response_bytes
            )
        self._logger.info(
            "inventory_poll",
            extra={
                "inventory_poll": {
                    "adapter": event.adapter.value,
                    "poll_id": event.poll_id,
                    "stage": event.stage.value,
                    "attempt": event.attempt,
                    "pages": event.usage.pages,
                    "items": event.usage.items,
                    "response_bytes": event.usage.response_bytes,
                    "failure_kind": failure,
                    "compatibility": event.compatibility,
                }
            },
        )

    def counters(self) -> dict[tuple[str, str, str], int]:
        with self._lock:
            return dict(self._counters)


__all__ = ["LoggingInventoryPollTelemetry"]
