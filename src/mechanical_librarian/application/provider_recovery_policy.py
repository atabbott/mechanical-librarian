"""One recovery classification for initial reports and retry observations."""

from __future__ import annotations

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
    observation: ProviderObservation, *, now: int
) -> tuple[str, int | None] | None:
    """Return one closed failure and delay from durable provider evidence."""
    if type(now) is not int or not 0 <= now <= MAX_TIMESTAMP:
        raise ValueError("provider recovery observation is invalid")
    statuses = (observation.status, *(lane.status for lane in observation.lanes))
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


def _next_midnight(now: int, zone: timezone | ZoneInfo) -> int:
    local = datetime.fromtimestamp(now, tz=zone)
    reset = datetime.combine(local.date() + timedelta(days=1), time.min, tzinfo=zone)
    delay = int(reset.timestamp()) - now
    if not 1 <= delay <= 90_000:
        raise ValueError("provider recovery reset is invalid")
    return delay


__all__ = ["provider_recovery_decision"]
