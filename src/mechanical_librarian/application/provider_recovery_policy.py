"""One recovery classification for initial reports and retry observations."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from mechanical_librarian.domain.failure_diagnostics import FailureCategory
from mechanical_librarian.domain.metadata_report import (
    ProviderObservation,
    ProviderStatus,
)
from mechanical_librarian.domain.provider_recovery import MAX_TIMESTAMP

_PACIFIC = ZoneInfo("America/Los_Angeles")
_BURST_DELAYS = {"google_books": 100, "hardcover": 60}
_DAILY_RESET_ZONES: dict[str, timezone | ZoneInfo] = {
    "google_books": _PACIFIC,
    "hardcover": timezone.utc,
}


def provider_recovery_decision(
    observation: ProviderObservation,
    *,
    now: int,
    google_burst_remaining: Callable[[], int | None] | None = None,
) -> tuple[str, int | None] | None:
    """Return one closed failure and delay from durable provider evidence.

    A lane the adapter's own fault failed is no evidence about the provider and
    is never retried, since it fails the same way each time (#1498).
    """
    if type(now) is not int or not 0 <= now <= MAX_TIMESTAMP:
        raise ValueError("provider recovery observation is invalid")
    lanes = tuple(lane for lane in observation.lanes if not adapter_faulted(lane))
    statuses = (
        *(
            ()
            if observation.lanes and adapter_faulted(observation)
            else (observation.status,)
        ),
        *(lane.status for lane in lanes),
    )
    if ProviderStatus.AUTHENTICATION in statuses:
        return "authentication", None
    if ProviderStatus.AUTHORIZATION in statuses:
        return "authorization", None
    if ProviderStatus.RATE_LIMITED in statuses:
        categories = {
            diagnostic.category
            for diagnostic in getattr(observation, "diagnostics", ())
        } | {
            diagnostic.category
            for lane in observation.lanes
            for diagnostic in getattr(lane, "diagnostics", ())
        }
        specific = categories & {
            FailureCategory.BURST_THROTTLE,
            FailureCategory.DAILY_QUOTA_EXHAUSTED,
        }
        generic = FailureCategory.QUOTA_RATE_LIMIT in categories
        authority = getattr(observation, "authority", None)
        # The reader belongs to the same composed Google pacer. A generic
        # *physical* 429 must keep its precedence; an entirely undispatched
        # observation may inherit only that trusted process-local window.
        if authority == "google_books" and google_burst_remaining is not None:
            accounting = observation.request_accounting
            inherited = (
                not specific
                and accounting.logical_requests > 0
                and accounting.initial_dispatches == 0
                and accounting.retry_dispatches == 0
                and accounting.undispatched_incomplete > 0
                and all(status is ProviderStatus.RATE_LIMITED for status in statuses)
            )
            if (
                inherited
                or not generic
                and specific == {FailureCategory.BURST_THROTTLE}
            ):
                remaining = google_burst_remaining()
                if remaining is not None:
                    return "burst_throttled", max(1, remaining)
        if not generic and specific == {FailureCategory.BURST_THROTTLE}:
            delay = _BURST_DELAYS.get(authority or "")
            return (
                ("burst_throttled", delay)
                if delay is not None
                else (
                    "rate_limited",
                    None,
                )
            )
        if not generic and specific == {FailureCategory.DAILY_QUOTA_EXHAUSTED}:
            zone = _DAILY_RESET_ZONES.get(authority or "")
            return (
                ("quota_exhausted", _next_midnight(now, zone))
                if zone
                else (
                    "rate_limited",
                    None,
                )
            )
        return "rate_limited", None
    if ProviderStatus.UNAVAILABLE in statuses or ProviderStatus.INCOMPLETE in statuses:
        return "transient", None
    return None


def adapter_faulted(value: object) -> bool:
    """Whether an observation or lane carries the adapter-fault diagnostic."""
    return any(
        diagnostic.category is FailureCategory.ADAPTER_FAULT
        for diagnostic in getattr(value, "diagnostics", ())
    )


def _next_midnight(now: int, zone: timezone | ZoneInfo) -> int:
    local = datetime.fromtimestamp(now, tz=zone)
    reset = datetime.combine(local.date() + timedelta(days=1), time.min, tzinfo=zone)
    delay = int(reset.timestamp()) - now
    if not 1 <= delay <= 90_000:
        raise ValueError("provider recovery reset is invalid")
    return delay


__all__ = ["adapter_faulted", "provider_recovery_decision"]
