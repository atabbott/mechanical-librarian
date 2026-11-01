"""Immutable process-local operational evidence owned by the application."""

from dataclasses import dataclass
from enum import Enum


class ProviderIncompleteCause(str, Enum):
    """Closed, process-local reasons that prevent a provider dispatch."""

    DEADLINE = "deadline"
    AGGREGATE_BYTES = "aggregate_bytes"
    DISPATCH_COUNT = "dispatch_count"


@dataclass(frozen=True, slots=True)
class ProviderIncompleteCauseRecord:
    """Aggregate-safe evidence for one undispatched provider request."""

    cause: ProviderIncompleteCause


@dataclass(frozen=True, slots=True)
class ProviderAttemptRecord:
    """Aggregate-safe, process-local evidence for one provider dispatch."""

    provider: str
    ordinal: int
    category: str
    delay_seconds: float | None
    elapsed_seconds: float
    outcome: str


@dataclass(frozen=True, slots=True)
class ProviderOperationalEvidence:
    accessed_at: str | None = None
    attempt_records: tuple[ProviderAttemptRecord, ...] | None = ()
    incomplete_causes: tuple[ProviderIncompleteCauseRecord, ...] | None = ()
    # The closed class of a fault the adapter itself raised (#1498), named only
    # in the process-local event and never persisted.
    adapter_fault: str | None = None
