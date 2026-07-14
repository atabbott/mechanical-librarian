#!/usr/bin/env python3
"""Measure one synthetic F06 child extraction without production authority."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import resource
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from mechanical_librarian.adapters.evidence_extractors import process as process_module
from mechanical_librarian.domain.identifiers import Fingerprint, SchemaVersion
from mechanical_librarian.domain.local_evidence import EpubPackageLimits
from tests.support.epub_packages import epub_bytes


def _descriptor_count() -> int:
    for root in (Path("/proc/self/fd"), Path("/dev/fd")):
        try:
            return len(tuple(root.iterdir()))
        except OSError:
            continue
    raise RuntimeError("descriptor qualification requires /proc/self/fd or /dev/fd")


def _process_group_members(group: int) -> int:
    proc = Path("/proc")
    if not proc.is_dir():
        return 1
    members = 0
    for candidate in proc.iterdir():
        if not candidate.name.isdigit():
            continue
        try:
            fields = (candidate / "stat").read_text(encoding="ascii").split()
            if len(fields) > 4 and int(fields[4]) == group:
                members += 1
        except (OSError, UnicodeError, ValueError):
            continue
    return members


def _linux_process_metrics(pid: int) -> tuple[int, int]:
    rss_kib = 0
    descriptors = 0
    try:
        for line in Path(f"/proc/{pid}/status").read_text(encoding="ascii").splitlines():
            if line.startswith("VmRSS:"):
                rss_kib = int(line.split()[1])
                break
        descriptors = len(tuple(Path(f"/proc/{pid}/fd").iterdir()))
    except (OSError, UnicodeError, ValueError):
        pass
    return rss_kib, descriptors


def main() -> int:
    value = epub_bytes()
    expected = Fingerprint(
        "sha256",
        SchemaVersion("source-v1"),
        hashlib.sha256(value).hexdigest(),
    )
    before_fds = _descriptor_count()
    before_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    children: list[subprocess.Popen[bytes]] = []
    peak_rss_kib = 0
    peak_child_fds = 0
    peak_processes = 0
    stop = threading.Event()
    real_popen = process_module.subprocess.Popen

    def capture(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        child = real_popen(*args, **kwargs)
        children.append(child)
        return child

    def sample() -> None:
        nonlocal peak_child_fds, peak_processes, peak_rss_kib
        while not stop.wait(0.001):
            for child in tuple(children):
                rss_kib, descriptors = _linux_process_metrics(child.pid)
                peak_rss_kib = max(peak_rss_kib, rss_kib)
                peak_child_fds = max(peak_child_fds, descriptors)
                peak_processes = max(peak_processes, _process_group_members(child.pid))

    started = time.monotonic()
    sampler = threading.Thread(target=sample, name="f06-metric-sampler", daemon=True)
    sampler.start()
    process_module.subprocess.Popen = capture
    try:
        with tempfile.TemporaryDirectory(prefix="ml-f06-") as directory:
            source = Path(directory) / "synthetic.epub"
            source.write_bytes(value)
            descriptor = os.open(source, os.O_RDONLY)
            try:
                report = process_module.run_epub_package_child(
                    descriptor,
                    expected,
                    EpubPackageLimits(),
                )
            finally:
                os.close(descriptor)
    finally:
        process_module.subprocess.Popen = real_popen
        stop.set()
        sampler.join(timeout=1)
    elapsed = time.monotonic() - started
    after_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    survivors = sum(child.poll() is None for child in children)
    if report.outcome.value != "success" or len(children) != 1 or survivors:
        raise RuntimeError("F06 qualification did not complete cleanly")
    rusage_rss_kib = int(after_usage.ru_maxrss)
    if platform.system() == "Darwin":
        rusage_rss_kib //= 1_024
    measured = {
        "child_cpu_system_seconds": round(after_usage.ru_stime - before_usage.ru_stime, 6),
        "child_cpu_user_seconds": round(after_usage.ru_utime - before_usage.ru_utime, 6),
        "child_peak_descriptors": peak_child_fds,
        "child_peak_rss_kib": max(peak_rss_kib, rusage_rss_kib),
        "elapsed_seconds": round(elapsed, 6),
        "output_bytes": len(report.to_json().encode("utf-8")),
        "parent_descriptor_delta": _descriptor_count() - before_fds,
        "peak_process_group_members": peak_processes or 1,
        "started_children": len(children),
        "surviving_children": survivors,
    }
    evidence = {
        "environment": {
            "implementation": platform.python_implementation(),
            "platform": platform.system().lower(),
            "python": platform.python_version(),
        },
        "measured": measured,
        "outcome": report.outcome.value,
        "schema": "epub-package-qualification-v1",
        "source": "repository-authored synthetic EPUB package",
    }
    print(json.dumps(evidence, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
