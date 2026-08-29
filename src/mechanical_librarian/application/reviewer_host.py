"""Conservative host-admission check for one contained Codex reviewer run."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

REVIEWER_HEADROOM_BYTES = 1_073_741_824


@dataclass(frozen=True, slots=True)
class ReviewerHostStatus:
    available: bool
    reason: (
        Literal[
            "host_evidence_unavailable",
            "cgroup_limit_below_1_gib",
            "headroom_below_1_gib",
        ]
        | None
    )
    headroom_bytes: int | None


def reviewer_host_status(
    read_text: Callable[[Path], str | None] | None = None,
) -> ReviewerHostStatus:
    reader = _read_optional if read_text is None else read_text
    try:
        meminfo = reader(Path("/proc/meminfo"))
        if meminfo is None:
            raise ValueError
        available = _mem_available(meminfo)
        cgroup = _cgroup_headroom(reader)
    except (OSError, UnicodeError, ValueError):
        return ReviewerHostStatus(False, "host_evidence_unavailable", None)
    headroom = available if cgroup is None else min(available, cgroup[1])
    if cgroup is not None and cgroup[0] < REVIEWER_HEADROOM_BYTES:
        return ReviewerHostStatus(False, "cgroup_limit_below_1_gib", headroom)
    if headroom < REVIEWER_HEADROOM_BYTES:
        return ReviewerHostStatus(False, "headroom_below_1_gib", headroom)
    return ReviewerHostStatus(True, None, headroom)


def _read_optional(path: Path) -> str | None:
    try:
        return path.read_text(encoding="ascii")
    except FileNotFoundError:
        return None


def _mem_available(document: str) -> int:
    matches = [
        line for line in document.splitlines() if line.startswith("MemAvailable:")
    ]
    if len(matches) != 1:
        raise ValueError
    fields = matches[0].split()
    if (
        len(fields) != 3
        or fields[2] != "kB"
        or not fields[1].isascii()
        or not fields[1].isdecimal()
    ):
        raise ValueError
    return int(fields[1]) * 1024


def _cgroup_headroom(
    reader: Callable[[Path], str | None],
) -> tuple[int, int] | None:
    v2_limit = reader(Path("/sys/fs/cgroup/memory.max"))
    v2_current = reader(Path("/sys/fs/cgroup/memory.current"))
    if v2_limit is not None or v2_current is not None:
        if v2_limit is None or v2_current is None:
            raise ValueError
        if v2_limit.strip() == "max":
            _unsigned(v2_current)
            return None
        limit = _unsigned(v2_limit)
        current = _unsigned(v2_current)
        return limit, max(0, limit - current)

    v1_pairs = (
        (
            Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
            Path("/sys/fs/cgroup/memory/memory.usage_in_bytes"),
        ),
        (
            Path("/sys/fs/cgroup/memory.limit_in_bytes"),
            Path("/sys/fs/cgroup/memory.usage_in_bytes"),
        ),
    )
    for limit_path, current_path in v1_pairs:
        limit_value = reader(limit_path)
        current_value = reader(current_path)
        if limit_value is None and current_value is None:
            continue
        if limit_value is None or current_value is None:
            raise ValueError
        limit = _unsigned(limit_value)
        current = _unsigned(current_value)
        if limit >= 1 << 60:
            return None
        return limit, max(0, limit - current)
    return None


def _unsigned(value: str) -> int:
    candidate = value.strip()
    if not candidate.isascii() or not candidate.isdecimal():
        raise ValueError
    return int(candidate)


__all__ = ["REVIEWER_HEADROOM_BYTES", "ReviewerHostStatus", "reviewer_host_status"]
