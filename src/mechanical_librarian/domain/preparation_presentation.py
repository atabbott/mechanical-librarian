"""Closed vocabulary for the owner-visible preparation run.

This is deliberately separate from the review pipeline's work claims.  It
records only the bounded presentation envelope and never participates in
claiming, ordering, retry, or provider work.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PreparationRunState(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    BLOCKED = "blocked"
    COMPLETED_CLEAN = "completed_clean"
    COMPLETED_WITH_FAILURES = "completed_with_failures"
    DISMISSED = "dismissed"


class PreparationTrigger(StrEnum):
    INITIAL_SETUP = "initial_setup"
    MANUAL_SCAN = "manual_scan"
    LIBRARY_ADDED = "library_added"


TERMINAL_PREPARATION_STATES = frozenset(
    {
        PreparationRunState.COMPLETED_CLEAN,
        PreparationRunState.COMPLETED_WITH_FAILURES,
        PreparationRunState.DISMISSED,
    }
)

PREPARATION_PASSAGE_FIELDS = (
    "read",
    "searches_finished",
    "compared",
    "checked",
    "ready",
    "failed",
)


@dataclass(frozen=True, slots=True)
class PreparationPassage:
    """Monotonic owner-facing stage passage, not instantaneous occupancy."""

    read: int
    searches_finished: int
    compared: int
    checked: int
    ready: int
    failed: int

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value < 0 for value in (self.ready, self.failed)
        ):
            raise ValueError("preparation passage is invalid")
        values = (
            self.read,
            self.searches_finished,
            self.compared,
            self.checked,
            self.ready + self.failed,
        )
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError("preparation passage is invalid")
        if any(values[index + 1] > values[index] for index in range(len(values) - 1)):
            raise ValueError("preparation passage is unordered")

    def to_data(self) -> dict[str, int]:
        return {
            "read": self.read,
            "searches_finished": self.searches_finished,
            "compared": self.compared,
            "checked": self.checked,
            "ready": self.ready,
            "failed": self.failed,
        }


@dataclass(frozen=True, slots=True)
class PreparationTriggerRecord:
    trigger: PreparationTrigger
    connection: str
    library: str
    scan_id: str
    accepted_at: int

    def __post_init__(self) -> None:
        if (
            type(self.trigger) is not PreparationTrigger
            or any(
                type(value) is not str or not value
                for value in (self.connection, self.library, self.scan_id)
            )
            or type(self.accepted_at) is not int
            or self.accepted_at < 0
        ):
            raise ValueError("preparation trigger is invalid")

    def to_data(self) -> dict[str, object]:
        return {
            "trigger": self.trigger.value,
            "connection": self.connection,
            "library": self.library,
            "scan_id": self.scan_id,
            "accepted_at": self.accepted_at,
        }


@dataclass(frozen=True, slots=True)
class PreparationActiveBook:
    name: str
    stage: str

    def __post_init__(self) -> None:
        if (
            type(self.name) is not str
            or not self.name
            or type(self.stage) is not str
            or not self.stage
        ):
            raise ValueError("active preparation book is invalid")

    def to_data(self) -> dict[str, str]:
        return {"name": self.name, "stage": self.stage}


@dataclass(frozen=True, slots=True)
class PreparationCompletion:
    name: str
    outcome: str
    completed_at: int

    def __post_init__(self) -> None:
        if (
            type(self.name) is not str
            or not self.name
            or self.outcome not in {"ready", "failed"}
            or type(self.completed_at) is not int
            or self.completed_at < 0
        ):
            raise ValueError("preparation completion is invalid")

    def to_data(self) -> dict[str, object]:
        return {
            "name": self.name,
            "outcome": self.outcome,
            "completed_at": self.completed_at,
        }


@dataclass(frozen=True, slots=True)
class PreparationPresentation:
    run_id: str
    state: PreparationRunState
    triggers: tuple[PreparationTriggerRecord, ...]
    passage: PreparationPassage
    active_books: tuple[PreparationActiveBook, ...]
    active_overflow: int
    recent_completions: tuple[PreparationCompletion, ...]
    throughput_per_minute: float
    started_at: int
    updated_at: int
    completed_at: int | None
    total: int

    def __post_init__(self) -> None:
        if (
            type(self.run_id) is not str
            or not self.run_id.startswith("preparation_run_")
            or len(self.run_id) != len("preparation_run_") + 64
            or any(
                character not in "0123456789abcdef"
                for character in self.run_id.removeprefix("preparation_run_")
            )
            or type(self.state) is not PreparationRunState
            or not self.triggers
            or len(self.active_books) > 5
            or len(self.recent_completions) > 8
            or type(self.active_overflow) is not int
            or self.active_overflow < 0
            or type(self.throughput_per_minute) is not float
            or self.throughput_per_minute < 0
            or any(
                type(value) is not int or value < 0
                for value in (self.started_at, self.updated_at, self.total)
            )
            or self.updated_at < self.started_at
            or (
                self.completed_at is not None
                and (
                    type(self.completed_at) is not int
                    or self.completed_at < self.started_at
                )
            )
            or (self.state in TERMINAL_PREPARATION_STATES and self.completed_at is None)
            or (
                self.state not in TERMINAL_PREPARATION_STATES
                and self.completed_at is not None
            )
            or self.passage.read > self.total
        ):
            raise ValueError("preparation presentation is invalid")

    @property
    def dismissible(self) -> bool:
        return self.state in {
            PreparationRunState.COMPLETED_CLEAN,
            PreparationRunState.COMPLETED_WITH_FAILURES,
        }

    def to_data(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "state": self.state.value,
            "triggers": [item.to_data() for item in self.triggers],
            "passage": self.passage.to_data(),
            "active_books": [item.to_data() for item in self.active_books],
            "active_overflow": self.active_overflow,
            "recent_completions": [item.to_data() for item in self.recent_completions],
            "throughput_per_minute": self.throughput_per_minute,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "total": self.total,
            "dismissible": self.dismissible,
        }
