"""Bounded parent protocol for the transient EPUB extraction child."""

from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import sys
import time
from collections.abc import Callable

from mechanical_librarian.domain.evidence_bounds import EvidenceValueError
from mechanical_librarian.domain.epub_opening import EpubOpeningLimits, EpubOpeningReport
from mechanical_librarian.domain.identifiers import Fingerprint
from mechanical_librarian.domain.local_evidence import (
    EpubPackageLimits,
    LocalEvidenceCapability,
    LocalEvidenceOutcome,
    LocalEvidenceReport,
)


_STDOUT_LIMIT = 1_048_576
_STDERR_LIMIT = 65_536
_CONTEXT_LIMIT = 65_536
_WALL_SECONDS = 60.0
_CHILD_MAX_SECONDS = 55.0
_CHILD_MARGIN_SECONDS = 0.25
_TERMINATION_GRACE_SECONDS = 2.0


def _failure(outcome: LocalEvidenceOutcome, limits: EpubPackageLimits) -> LocalEvidenceReport:
    return LocalEvidenceReport(outcome, LocalEvidenceCapability.PACKAGE, limits)


def _group_exists(identifier: int) -> bool:
    try:
        os.killpg(identifier, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for_group_exit(identifier: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while _group_exists(identifier) and time.monotonic() < deadline:
        time.sleep(0.01)
    return not _group_exists(identifier)


def _wait_for_group_until(identifier: int, deadline: float) -> bool:
    while _group_exists(identifier) and time.monotonic() < deadline:
        time.sleep(0.01)
    return not _group_exists(identifier)


def _stop(process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + _TERMINATION_GRACE_SECONDS
    leader_running = process.poll() is None
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        if leader_running:
            try:
                process.terminate()
            except OSError:
                pass
    if leader_running:
        try:
            process.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
    if not _wait_for_group_until(process.pid, deadline):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass
        _wait_for_group_exit(process.pid, 0.25)
    if process.poll() is None:
        try:
            process.wait(timeout=0.25)
        except subprocess.TimeoutExpired:
            pass


def _minimal_environment() -> dict[str, str]:
    return {
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONHASHSEED": "0",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1",
        "TMPDIR": "/nonexistent",
    }


def _bounded_timing(wall_seconds: float) -> tuple[float, float]:
    effective_wall_seconds = min(float(wall_seconds), _WALL_SECONDS)
    child_seconds = min(
        _CHILD_MAX_SECONDS,
        max(0.001, effective_wall_seconds - _CHILD_MARGIN_SECONDS),
    )
    return effective_wall_seconds, child_seconds


def run_epub_package_child(
    descriptor: int,
    expected_source: Fingerprint,
    limits: EpubPackageLimits,
    *,
    cancelled: Callable[[], bool] = lambda: False,
    wall_seconds: float = _WALL_SECONDS,
    logical_identity: str | None = None,
) -> LocalEvidenceReport:
    """Run one descriptor-only child and validate its closed report envelope."""

    if type(wall_seconds) not in {int, float} or isinstance(wall_seconds, bool) or wall_seconds <= 0:
        return _failure(LocalEvidenceOutcome.INTERNAL_FAILURE, limits)
    effective_wall_seconds, child_seconds = _bounded_timing(float(wall_seconds))
    context = json.dumps(
        {
            "child_seconds": child_seconds,
            "expected_source": {
                "algorithm": expected_source.algorithm,
                "digest": expected_source.digest,
                "schema": expected_source.schema.value,
            },
            "limits": limits.to_data(),
            "logical_identity": expected_source.digest if logical_identity is None else logical_identity,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(context) > _CONTEXT_LIMIT:
        return _failure(LocalEvidenceOutcome.INTERNAL_FAILURE, limits)
    control_read, control_write = os.pipe()
    process: subprocess.Popen[bytes] | None = None
    selector = selectors.DefaultSelector()
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-B",
                "-m",
                "mechanical_librarian.bootstrap.epub_child",
                str(descriptor),
                str(control_read),
                str(os.getpid()),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            pass_fds=(descriptor, control_read),
            env=_minimal_environment(),
            cwd="/",
            start_new_session=True,
            shell=False,
        )
        os.close(control_read)
        control_read = -1
        assert process.stdin is not None and process.stdout is not None and process.stderr is not None
        process.stdin.write(context)
        process.stdin.close()
        os.set_blocking(process.stdout.fileno(), False)
        os.set_blocking(process.stderr.fileno(), False)
        stdout_buffer = bytearray()
        stderr_buffer = bytearray()
        selector.register(process.stdout, selectors.EVENT_READ, (stdout_buffer, _STDOUT_LIMIT))
        selector.register(process.stderr, selectors.EVENT_READ, (stderr_buffer, _STDERR_LIMIT))
        deadline = time.monotonic() + effective_wall_seconds
        overflow = False
        while selector.get_map() or process.poll() is None:
            if cancelled():
                _stop(process)
                return _failure(LocalEvidenceOutcome.CANCELLATION, limits)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _stop(process)
                return _failure(LocalEvidenceOutcome.TIMEOUT, limits)
            for key, _ in selector.select(min(remaining, 0.1)):
                stream = key.fileobj
                buffer, maximum = key.data
                try:
                    value = os.read(stream.fileno(), min(16_384, maximum + 1 - len(buffer)))
                except BlockingIOError:
                    continue
                if not value:
                    selector.unregister(stream)
                    continue
                buffer.extend(value)
                if len(buffer) > maximum:
                    overflow = True
                    _stop(process)
                    break
            if overflow:
                break
        if overflow:
            return _failure(LocalEvidenceOutcome.CHILD_FAILURE, limits)
        return_code = process.wait(timeout=_TERMINATION_GRACE_SECONDS)
        # Streams normally unregister on EOF; retain references directly for
        # final bounded drains so fast-exit children cannot lose their tail.
        for stream, maximum, target in (
            (process.stdout, _STDOUT_LIMIT, "stdout"),
            (process.stderr, _STDERR_LIMIT, "stderr"),
        ):
            existing = stdout_buffer if target == "stdout" else stderr_buffer
            chunks = bytearray()
            while True:
                try:
                    value = os.read(stream.fileno(), 16_384)
                except BlockingIOError:
                    break
                if not value:
                    break
                chunks.extend(value)
                if len(existing) + len(chunks) > maximum:
                    return _failure(LocalEvidenceOutcome.CHILD_FAILURE, limits)
            if target == "stdout":
                stdout_buffer.extend(chunks)
            else:
                stderr_buffer.extend(chunks)
        stdout = bytes(stdout_buffer)
        stderr = bytes(stderr_buffer)
        if return_code == 71:
            return _failure(LocalEvidenceOutcome.RESOURCE_LIMIT, limits)
        if return_code != 0 or stderr or not stdout.endswith(b"\n") or stdout.count(b"\n") != 1:
            return _failure(LocalEvidenceOutcome.CHILD_FAILURE, limits)
        try:
            report = LocalEvidenceReport.from_json(stdout[:-1].decode("utf-8", "strict"))
        except (EvidenceValueError, UnicodeError):
            return _failure(LocalEvidenceOutcome.CHILD_FAILURE, limits)
        if report.limits != limits or report.capability is not LocalEvidenceCapability.PACKAGE:
            return _failure(LocalEvidenceOutcome.CHILD_FAILURE, limits)
        if report.source_fingerprint is not None and report.source_fingerprint != expected_source:
            return _failure(LocalEvidenceOutcome.CHILD_FAILURE, limits)
        return report
    except (OSError, ValueError, subprocess.SubprocessError):
        if process is not None:
            _stop(process)
        return _failure(LocalEvidenceOutcome.CHILD_FAILURE, limits)
    finally:
        selector.close()
        if control_read >= 0:
            os.close(control_read)
        try:
            os.close(control_write)
        except OSError:
            pass
        if process is not None:
            _stop(process)
        if process is not None:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()


def run_epub_opening_child(
    descriptor: int,
    expected_source: Fingerprint,
    limits: EpubOpeningLimits,
    *,
    cancelled: Callable[[], bool] = lambda: False,
    logical_identity: str | None = None,
) -> EpubOpeningReport:
    """Run the F07a capability through the same isolated child controls."""

    if type(limits) is not EpubOpeningLimits:
        return EpubOpeningReport(LocalEvidenceOutcome.INTERNAL_FAILURE, EpubOpeningLimits())
    return _run_epub_opening_protocol(
        descriptor,
        expected_source,
        limits,
        cancelled=cancelled,
        logical_identity=logical_identity,
    )


def _run_epub_opening_protocol(
    descriptor: int,
    expected_source: Fingerprint,
    limits: EpubOpeningLimits,
    *,
    cancelled: Callable[[], bool],
    logical_identity: str | None,
) -> EpubOpeningReport:
    """Capability-specific closed envelope; process authority matches F06."""

    def failure(outcome: LocalEvidenceOutcome) -> EpubOpeningReport:
        return EpubOpeningReport(outcome, limits)

    context = json.dumps(
        {
            "capability": "opening_matter",
            "child_seconds": min(
                float(limits.wall_seconds),
                max(0.001, float(limits.wall_seconds) - _CHILD_MARGIN_SECONDS),
            ),
            "expected_source": {
                "algorithm": expected_source.algorithm,
                "digest": expected_source.digest,
                "schema": expected_source.schema.value,
            },
            "limits": limits.to_data(),
            "logical_identity": expected_source.digest if logical_identity is None else logical_identity,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(context) > _CONTEXT_LIMIT:
        return failure(LocalEvidenceOutcome.INTERNAL_FAILURE)
    control_read, control_write = os.pipe()
    process: subprocess.Popen[bytes] | None = None
    selector = selectors.DefaultSelector()
    try:
        process = subprocess.Popen(
            [sys.executable, "-I", "-B", "-m", "mechanical_librarian.bootstrap.epub_child", str(descriptor), str(control_read), str(os.getpid())],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            close_fds=True, pass_fds=(descriptor, control_read), env=_minimal_environment(),
            cwd="/", start_new_session=True, shell=False,
        )
        os.close(control_read)
        control_read = -1
        assert process.stdin is not None and process.stdout is not None and process.stderr is not None
        process.stdin.write(context)
        process.stdin.close()
        os.set_blocking(process.stdout.fileno(), False)
        os.set_blocking(process.stderr.fileno(), False)
        stdout = bytearray()
        stderr = bytearray()
        selector.register(process.stdout, selectors.EVENT_READ, (stdout, _STDOUT_LIMIT))
        selector.register(process.stderr, selectors.EVENT_READ, (stderr, _STDERR_LIMIT))
        deadline = time.monotonic() + limits.wall_seconds
        overflow = False
        while selector.get_map() or process.poll() is None:
            if cancelled():
                _stop(process)
                return failure(LocalEvidenceOutcome.CANCELLATION)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _stop(process)
                return failure(LocalEvidenceOutcome.TIMEOUT)
            for key, _ in selector.select(min(remaining, 0.1)):
                stream = key.fileobj
                buffer, maximum = key.data
                try:
                    value = os.read(stream.fileno(), min(16_384, maximum + 1 - len(buffer)))
                except BlockingIOError:
                    continue
                if not value:
                    selector.unregister(stream)
                else:
                    buffer.extend(value)
                    if len(buffer) > maximum:
                        overflow = True
                        _stop(process)
                        break
            if overflow:
                break
        if overflow:
            return failure(LocalEvidenceOutcome.CHILD_FAILURE)
        return_code = process.wait(timeout=_TERMINATION_GRACE_SECONDS)
        for stream, maximum, buffer in ((process.stdout, _STDOUT_LIMIT, stdout), (process.stderr, _STDERR_LIMIT, stderr)):
            while True:
                try:
                    value = os.read(stream.fileno(), min(16_384, maximum + 1 - len(buffer)))
                except BlockingIOError:
                    break
                if not value:
                    break
                buffer.extend(value)
                if len(buffer) > maximum:
                    return failure(LocalEvidenceOutcome.CHILD_FAILURE)
        if return_code == 71:
            return failure(LocalEvidenceOutcome.RESOURCE_LIMIT)
        payload = bytes(stdout)
        if return_code != 0 or stderr or not payload.endswith(b"\n") or payload.count(b"\n") != 1:
            return failure(LocalEvidenceOutcome.CHILD_FAILURE)
        try:
            report = EpubOpeningReport.from_json(payload[:-1].decode("utf-8", "strict"))
        except (EvidenceValueError, UnicodeError):
            return failure(LocalEvidenceOutcome.CHILD_FAILURE)
        if report.limits != limits or (
            report.source_fingerprint is not None
            and report.source_fingerprint != expected_source
        ):
            return failure(LocalEvidenceOutcome.CHILD_FAILURE)
        return report
    except (OSError, ValueError, subprocess.SubprocessError):
        if process is not None:
            _stop(process)
        return failure(LocalEvidenceOutcome.CHILD_FAILURE)
    finally:
        selector.close()
        if control_read >= 0:
            os.close(control_read)
        try:
            os.close(control_write)
        except OSError:
            pass
        if process is not None:
            _stop(process)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()


__all__ = ["run_epub_opening_child", "run_epub_package_child"]
