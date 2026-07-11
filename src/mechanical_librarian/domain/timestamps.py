"""Canonical timestamp values shared across persistence boundaries."""

from __future__ import annotations

import re
from datetime import datetime, timezone


_SUPPORTED_TIMESTAMP = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})T(?P<time>\d{2}:\d{2}:\d{2})"
    r"(?P<fraction>\.\d{1,6})?(?P<zone>Z|[+-](?:(?:0\d|1[0-3]):[0-5]\d|14:00))$"
)


def canonical_utc_timestamp(value: object) -> str | None:
    """Return a fixed-width UTC timestamp for one supported ISO spelling."""

    if type(value) is not str or _SUPPORTED_TIMESTAMP.fullmatch(value) is None:
        return None
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        utc_timestamp = timestamp.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        return None
    return utc_timestamp.isoformat(timespec="microseconds").replace("+00:00", "Z")
