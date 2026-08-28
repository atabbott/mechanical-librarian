from __future__ import annotations

from pathlib import Path

import pytest

from mechanical_librarian.application.reviewer_host import reviewer_host_status


def _reader(values: dict[str, str]):
    return lambda path: values.get(str(path))


def test_v2_uses_the_smaller_of_memavailable_and_cgroup_headroom() -> None:
    status = reviewer_host_status(
        _reader(
            {
                "/proc/meminfo": "MemAvailable: 2097152 kB\n",
                "/sys/fs/cgroup/memory.max": str(3 * 1024**3),
                "/sys/fs/cgroup/memory.current": str(2 * 1024**3),
            }
        )
    )
    assert status.available is True
    assert status.headroom_bytes == 1024**3


def test_v1_finite_limit_below_one_gib_is_specific() -> None:
    status = reviewer_host_status(
        _reader(
            {
                "/proc/meminfo": "MemAvailable: 2097152 kB\n",
                "/sys/fs/cgroup/memory/memory.limit_in_bytes": str(512 * 1024**2),
                "/sys/fs/cgroup/memory/memory.usage_in_bytes": "0",
            }
        )
    )
    assert status.available is False
    assert status.reason == "cgroup_limit_below_1_gib"


def test_unlimited_cgroup_still_obeys_memavailable() -> None:
    status = reviewer_host_status(
        _reader(
            {
                "/proc/meminfo": "MemAvailable: 524288 kB\n",
                "/sys/fs/cgroup/memory.max": "max\n",
                "/sys/fs/cgroup/memory.current": "1\n",
            }
        )
    )
    assert status.available is False
    assert status.reason == "headroom_below_1_gib"


@pytest.mark.parametrize(
    "values",
    (
        {},
        {"/proc/meminfo": "MemTotal: 1 kB\n"},
        {"/proc/meminfo": "MemAvailable: nope kB\n"},
        {
            "/proc/meminfo": "MemAvailable: 2097152 kB\n",
            "/sys/fs/cgroup/memory.max": "bad",
            "/sys/fs/cgroup/memory.current": "0",
        },
        {
            "/proc/meminfo": "MemAvailable: 2097152 kB\n",
            "/sys/fs/cgroup/memory.max": str(2 * 1024**3),
        },
    ),
)
def test_malformed_or_partial_required_evidence_fails_closed(
    values: dict[str, str],
) -> None:
    status = reviewer_host_status(_reader(values))
    assert status.available is False
    assert status.reason == "host_evidence_unavailable"
