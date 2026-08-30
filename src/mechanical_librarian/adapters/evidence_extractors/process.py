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
from typing import IO, cast

from mechanical_librarian.domain.audio_inspection import (
    MAX_PUBLIC_REPORT_BYTES,
    AudioInspection,
    AudioInspectionLimits,
    AudioInspectionOutcome,
)
from mechanical_librarian.domain.epub_inspection import (
    EpubInspection,
    EpubInspectionOutcome,
)
from mechanical_librarian.domain.epub_opening import EpubOpeningLimits
from mechanical_librarian.domain.evidence_bounds import EvidenceValueError
from mechanical_librarian.domain.identifiers import Fingerprint

_STDOUT_LIMIT = 1_048_576
# The audio child's report is bounded by the audio protocol rather than by the
# EPUB one, and the two ceilings are not the same number.
_AUDIO_STDOUT_LIMIT = MAX_PUBLIC_REPORT_BYTES + 1
_STDERR_LIMIT = 65_536
_CONTEXT_LIMIT = 65_536
_WALL_SECONDS = 60.0
_CHILD_MAX_SECONDS = 55.0
_CHILD_MARGIN_SECONDS = 0.25
_TERMINATION_GRACE_SECONDS = 2.0
# The probe document is the whole audio child's input, so its ceiling plus the
# rest of one context has to fit in a single bounded write.
_AUDIO_CONTEXT_LIMIT = 12_582_912
# Resolved from a closed list rather than an inherited `PATH`: the child runs
# under `_minimal_environment`, which carries no `PATH` at all, and the shipped
# image installs the probe at exactly these locations.
_PROBE_PATHS = (
    "/usr/bin/ffprobe",
    "/usr/local/bin/ffprobe",
    "/opt/homebrew/bin/ffprobe",
)


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


def run_epub_inspector_child(
    descriptor: int,
    expected_source: Fingerprint,
    limits: EpubOpeningLimits,
    *,
    cancelled: Callable[[], bool] = lambda: False,
    logical_identity: str | None = None,
) -> EpubInspection:
    """Run the F07a capability through the same isolated child controls."""

    if type(limits) is not EpubOpeningLimits:
        return EpubInspection(EpubInspectionOutcome.INTERNAL_FAILURE, None)
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
) -> EpubInspection:
    """Capability-specific closed envelope; process authority matches F06."""

    def failure(outcome: EpubInspectionOutcome) -> EpubInspection:
        return EpubInspection(outcome, None)

    context = json.dumps(
        {
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
            "logical_identity": expected_source.digest
            if logical_identity is None
            else logical_identity,
            "capability": "opening",
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(context) > _CONTEXT_LIMIT:
        return failure(EpubInspectionOutcome.INTERNAL_FAILURE)
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
        assert (
            process.stdin is not None
            and process.stdout is not None
            and process.stderr is not None
        )
        process.stdin.write(context)
        process.stdin.close()
        os.set_blocking(process.stdout.fileno(), False)
        os.set_blocking(process.stderr.fileno(), False)
        stdout = bytearray()
        stderr = bytearray()
        stream: IO[bytes] | None
        selector.register(process.stdout, selectors.EVENT_READ, (stdout, _STDOUT_LIMIT))
        selector.register(process.stderr, selectors.EVENT_READ, (stderr, _STDERR_LIMIT))
        deadline = time.monotonic() + limits.wall_seconds
        overflow = False
        while selector.get_map() or process.poll() is None:
            if cancelled():
                _stop(process)
                return failure(EpubInspectionOutcome.CANCELLATION)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _stop(process)
                return failure(EpubInspectionOutcome.TIMEOUT)
            for key, _ in selector.select(min(remaining, 0.1)):
                stream = cast("IO[bytes]", key.fileobj)
                buffer, maximum = key.data
                try:
                    value = os.read(
                        stream.fileno(), min(16_384, maximum + 1 - len(buffer))
                    )
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
            return failure(EpubInspectionOutcome.CHILD_FAILURE)
        return_code = process.wait(timeout=_TERMINATION_GRACE_SECONDS)
        for stream, maximum, buffer in (
            (process.stdout, _STDOUT_LIMIT, stdout),
            (process.stderr, _STDERR_LIMIT, stderr),
        ):
            while True:
                try:
                    value = os.read(
                        stream.fileno(), min(16_384, maximum + 1 - len(buffer))
                    )
                except BlockingIOError:
                    break
                if not value:
                    break
                buffer.extend(value)
                if len(buffer) > maximum:
                    return failure(EpubInspectionOutcome.CHILD_FAILURE)
        if return_code == 71:
            return failure(EpubInspectionOutcome.RESOURCE_LIMIT)
        payload = bytes(stdout)
        if (
            return_code != 0
            or stderr
            or not payload.endswith(b"\n")
            or payload.count(b"\n") != 1
        ):
            return failure(EpubInspectionOutcome.CHILD_FAILURE)
        try:
            report = EpubInspection.from_json(payload[:-1].decode("utf-8", "strict"))
        except (EvidenceValueError, UnicodeError, ValueError):
            return failure(EpubInspectionOutcome.CHILD_FAILURE)
        if report.source is not None and report.source != expected_source:
            return failure(EpubInspectionOutcome.CHILD_FAILURE)
        return report
    except (OSError, ValueError, subprocess.SubprocessError):
        if process is not None:
            _stop(process)
        return failure(EpubInspectionOutcome.CHILD_FAILURE)
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


class _ChildRun:
    """One completed bounded child run, or the outcome that stopped it."""

    __slots__ = ("stdout", "stderr", "return_code", "failure")

    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        return_code: int = 0,
        failure: AudioInspectionOutcome | None = None,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.return_code = return_code
        self.failure = failure


def _drain(
    process: subprocess.Popen[bytes],
    *,
    stdout_limit: int,
    stderr_limit: int,
    wall_seconds: float,
    cancelled: Callable[[], bool],
) -> _ChildRun:
    """Read one child's bounded streams under a wall clock and a cancellation.

    The same controls the EPUB parent applies, factored out because the audio
    capability runs two children in sequence and neither may inherit an
    unbounded stream or an unbounded wait.
    """
    selector = selectors.DefaultSelector()
    try:
        assert process.stdout is not None and process.stderr is not None
        os.set_blocking(process.stdout.fileno(), False)
        os.set_blocking(process.stderr.fileno(), False)
        stdout = bytearray()
        stderr = bytearray()
        selector.register(process.stdout, selectors.EVENT_READ, (stdout, stdout_limit))
        selector.register(process.stderr, selectors.EVENT_READ, (stderr, stderr_limit))
        deadline = time.monotonic() + wall_seconds
        stream: IO[bytes] | None
        while selector.get_map() or process.poll() is None:
            if cancelled():
                _stop(process)
                return _ChildRun(failure=AudioInspectionOutcome.CANCELLATION)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _stop(process)
                return _ChildRun(failure=AudioInspectionOutcome.TIMEOUT)
            for key, _ in selector.select(min(remaining, 0.1)):
                stream = cast("IO[bytes]", key.fileobj)
                buffer, maximum = key.data
                try:
                    value = os.read(
                        stream.fileno(), min(16_384, maximum + 1 - len(buffer))
                    )
                except BlockingIOError:
                    continue
                if not value:
                    selector.unregister(stream)
                    continue
                buffer.extend(value)
                if len(buffer) > maximum:
                    _stop(process)
                    return _ChildRun(failure=AudioInspectionOutcome.RESOURCE_LIMIT)
        return _ChildRun(
            stdout=bytes(stdout),
            stderr=bytes(stderr),
            return_code=process.wait(timeout=_TERMINATION_GRACE_SECONDS),
        )
    finally:
        selector.close()


def resolve_probe_path() -> str | None:
    """Resolve the shipped probe binary without consulting an inherited PATH."""
    for candidate in _PROBE_PATHS:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def run_probe_child(
    descriptor: int,
    limits: AudioInspectionLimits,
    *,
    cancelled: Callable[[], bool] = lambda: False,
) -> tuple[bytes | None, AudioInspectionOutcome]:
    """Parse one media file's container in the isolated probe child.

    The parent runs this itself. The descriptor is passed through, never a
    path, so the child reads exactly the file the parent opened and validated
    and can never resolve a different one.

    The spawn goes through `bootstrap.probe_shim`, which installs the probe's
    resource ceilings and then becomes the probe. The ceilings used to be a
    `preexec_fn`, which runs Python between a fork and an exec: from a threaded
    supervisor that is a deadlock the parent waits on before any clock starts.
    The binary is still resolved here, so a missing probe is still refused
    before anything is spawned.
    """
    binary = resolve_probe_path()
    if binary is None:
        return None, AudioInspectionOutcome.INVALID_CONFIGURATION
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-B",
                "-m",
                "mechanical_librarian.bootstrap.probe_shim",
                str(descriptor),
                binary,
                "-v",
                "error",
                "-threads",
                "1",
                "-export_all",
                "1",
                "-show_format",
                "-show_chapters",
                "-show_streams",
                "-of",
                "json",
                f"/dev/fd/{descriptor}",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            pass_fds=(descriptor,),
            env=_minimal_environment(),
            cwd="/",
            start_new_session=True,
            shell=False,
        )
        run = _drain(
            process,
            stdout_limit=limits.probe_output_bytes,
            stderr_limit=_STDERR_LIMIT,
            wall_seconds=min(float(limits.wall_seconds), _WALL_SECONDS),
            cancelled=cancelled,
        )
        if run.failure is not None:
            return None, run.failure
        if run.return_code != 0 or not run.stdout:
            return None, AudioInspectionOutcome.PROBE_FAILURE
        return run.stdout, AudioInspectionOutcome.SUCCESS
    except (OSError, ValueError, subprocess.SubprocessError):
        return None, AudioInspectionOutcome.PROBE_FAILURE
    finally:
        if process is not None:
            _stop(process)
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()


def run_audio_inspector_child(
    descriptor: int,
    expected_source: Fingerprint,
    limits: AudioInspectionLimits,
    *,
    sidecars: tuple[tuple[str, str, int], ...] = (),
    path_file: str = "",
    path_folder: str | None = None,
    cancelled: Callable[[], bool] = lambda: False,
) -> AudioInspection:
    """Run F43's two-child audio capability behind one application-visible call."""

    def failure(outcome: AudioInspectionOutcome) -> AudioInspection:
        return AudioInspection(outcome, None)

    if type(limits) is not AudioInspectionLimits:
        return failure(AudioInspectionOutcome.INTERNAL_FAILURE)
    probe, outcome = run_probe_child(descriptor, limits, cancelled=cancelled)
    if probe is None:
        return failure(outcome)
    try:
        probe_text = probe.decode("utf-8", "strict")
    except UnicodeError:
        return failure(AudioInspectionOutcome.PROBE_FAILURE)
    context = json.dumps(
        {
            "capability": "audio",
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
            "path": {"file": path_file, "folder": path_folder},
            "probe": probe_text,
            "sidecars": [
                {"descriptor": value[2], "name": value[1], "surface": value[0]}
                for value in sidecars[: limits.sidecar_documents]
            ],
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(context) > _AUDIO_CONTEXT_LIMIT:
        return failure(AudioInspectionOutcome.RESOURCE_LIMIT)
    control_read, control_write = os.pipe()
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-B",
                "-m",
                "mechanical_librarian.bootstrap.audio_child",
                str(control_read),
                str(os.getpid()),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            pass_fds=(control_read, *(value[2] for value in sidecars)),
            env=_minimal_environment(),
            cwd="/",
            start_new_session=True,
            shell=False,
        )
        os.close(control_read)
        control_read = -1
        assert process.stdin is not None
        process.stdin.write(context)
        process.stdin.close()
        run = _drain(
            process,
            stdout_limit=_AUDIO_STDOUT_LIMIT,
            stderr_limit=_STDERR_LIMIT,
            wall_seconds=min(float(limits.wall_seconds), _WALL_SECONDS),
            cancelled=cancelled,
        )
        if run.failure is not None:
            return failure(run.failure)
        if run.return_code == 71:
            return failure(AudioInspectionOutcome.RESOURCE_LIMIT)
        payload = run.stdout
        if (
            run.return_code != 0
            or run.stderr
            or not payload.endswith(b"\n")
            or payload.count(b"\n") != 1
        ):
            return failure(AudioInspectionOutcome.CHILD_FAILURE)
        try:
            report = AudioInspection.from_json(payload[:-1].decode("utf-8", "strict"))
        except (EvidenceValueError, UnicodeError, ValueError):
            return failure(AudioInspectionOutcome.CHILD_FAILURE)
        if report.source is not None and report.source != expected_source:
            return failure(AudioInspectionOutcome.CHILD_FAILURE)
        return report
    except (OSError, ValueError, subprocess.SubprocessError):
        if process is not None:
            _stop(process)
        return failure(AudioInspectionOutcome.CHILD_FAILURE)
    finally:
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


__all__ = [
    "resolve_probe_path",
    "run_audio_inspector_child",
    "run_epub_inspector_child",
    "run_probe_child",
]
