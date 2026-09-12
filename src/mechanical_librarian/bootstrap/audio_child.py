"""Private composition entry point for one transient audio interpretation child.

The parent harness has already run the probe: this child never opens the media
file and never starts a process, which is what lets it keep ``RLIMIT_NPROC`` at
one.  What it does own is every remaining untrusted byte -- the probe document
and each sidecar it is handed a descriptor for -- and it reads and interprets
all of them inside these limits.
"""

from __future__ import annotations

import json
import os
import resource
import signal
import sys
import threading
from typing import cast

from mechanical_librarian.adapters.evidence_extractors.audio_local import (
    AudioPathContext,
    AudioSidecar,
    interpret,
)
from mechanical_librarian.domain.audio_inspection import (
    MAX_PUBLIC_REPORT_BYTES,
    AudioEvidenceSurface,
    AudioInspection,
    AudioInspectionLimits,
    AudioInspectionOutcome,
)
from mechanical_librarian.domain.identifiers import Fingerprint, SchemaVersion

_ADDRESS_SPACE = 256 * 1024 * 1024
_CPU_SECONDS = 30
_OPEN_FILES = 64
_CONTEXT_LIMIT = 12_582_912
# One newline past the protocol's own ceiling, so exactly the largest report
# `AudioInspection.from_json` accepts can also be written.
_STDOUT_LIMIT = MAX_PUBLIC_REPORT_BYTES + 1


def _apply_limits() -> None:
    for limit, value in (
        (resource.RLIMIT_CORE, 0),
        (resource.RLIMIT_NOFILE, _OPEN_FILES),
        (resource.RLIMIT_CPU, _CPU_SECONDS),
    ):
        current_soft, current_hard = resource.getrlimit(limit)
        hard = (
            value
            if current_hard == resource.RLIM_INFINITY
            else min(value, current_hard)
        )
        resource.setrlimit(limit, (min(value, hard), hard))
    try:
        current_soft, current_hard = resource.getrlimit(resource.RLIMIT_AS)
        hard = (
            _ADDRESS_SPACE
            if current_hard == resource.RLIM_INFINITY
            else min(_ADDRESS_SPACE, current_hard)
        )
        resource.setrlimit(resource.RLIMIT_AS, (min(_ADDRESS_SPACE, hard), hard))
    except ValueError:
        # Darwin's framework build rejects RLIMIT_AS below its shared-library
        # reservations; macOS stays a development-only compatibility target and
        # Linux production still fails closed.
        if sys.platform != "darwin":
            raise
    if sys.platform.startswith("linux"):
        current_soft, current_hard = resource.getrlimit(resource.RLIMIT_NPROC)
        hard = 1 if current_hard == resource.RLIM_INFINITY else min(1, current_hard)
        resource.setrlimit(resource.RLIMIT_NPROC, (hard, hard))


def _closed_object(value: object, keys: set[str]) -> dict[str, object]:
    if type(value) is not dict or set(value) != keys:
        raise ValueError("invalid child context")
    return value


def _read_context() -> dict[str, object]:
    value = sys.stdin.buffer.read(_CONTEXT_LIMIT + 1)
    if len(value) > _CONTEXT_LIMIT:
        raise ValueError("invalid child context")
    return _closed_object(
        json.loads(value),
        {
            "capability",
            "child_seconds",
            "expected_source",
            "limits",
            "path",
            "probe",
            "sidecars",
        },
    )


def _limits(value: object) -> AudioInspectionLimits:
    item = _closed_object(value, {"profile", *AudioInspectionLimits.HARD_CAPS})
    if item["profile"] != "audio-inspection-limits-v1":
        raise ValueError("invalid child context")
    return AudioInspectionLimits(
        **cast(
            "dict[str, int]",
            {name: item[name] for name in AudioInspectionLimits.HARD_CAPS},
        )
    )


def _fingerprint(value: object) -> Fingerprint:
    item = _closed_object(value, {"algorithm", "digest", "schema"})
    return Fingerprint(
        cast(str, item["algorithm"]),
        SchemaVersion(cast(str, item["schema"])),
        cast(str, item["digest"]),
    )


def _path_context(value: object) -> AudioPathContext:
    item = _closed_object(value, {"file", "folder"})
    file_name = item["file"]
    folder_name = item["folder"]
    if type(file_name) is not str or (
        folder_name is not None and type(folder_name) is not str
    ):
        raise ValueError("invalid child context")
    return AudioPathContext(file_name, folder_name)


def _sidecars(value: object, limits: AudioInspectionLimits) -> tuple[AudioSidecar, ...]:
    if type(value) is not list or len(value) > limits.sidecar_documents:
        raise ValueError("invalid child context")
    values: list[AudioSidecar] = []
    for item in value:
        entry = _closed_object(item, {"descriptor", "name", "surface"})
        descriptor = entry["descriptor"]
        name = entry["name"]
        surface_value = entry["surface"]
        if (
            type(descriptor) is not int
            or descriptor < 0
            or type(name) is not str
            or not 1 <= len(name) <= 255
            or type(surface_value) is not str
        ):
            raise ValueError("invalid child context")
        surface = AudioEvidenceSurface(surface_value)
        text = _sidecar_text(descriptor, limits.sidecar_bytes)
        if text is not None:
            values.append(AudioSidecar(surface, name, text))
    return tuple(values)


def _sidecar_text(descriptor: int, maximum: int) -> str | None:
    """Read one sidecar's bytes under its own cap, or none at all.

    A sidecar is owner-supplied text of unknown encoding.  Over-long or
    unreadable documents are dropped rather than truncated into a document
    whose grammar would then be read out of a fragment.

    Bytes that are not UTF-8 are undecodable in exactly that sense.  Replacing
    them would publish a U+FFFD the owner's file never held, as evidence
    attributed to their file, so the sidecar is dropped whole instead.  A
    replacement character the source really carries still decodes and stays.
    """
    try:
        information = os.fstat(descriptor)
        if information.st_size > maximum:
            return None
        value = os.pread(descriptor, maximum, 0)
    except OSError:
        return None
    try:
        return value.decode("utf-8", "strict")
    except UnicodeError:
        return None


class _Expired(BaseException):
    """The child's own deadline elapsed before interpretation finished."""


def _expire(*_arguments: object) -> None:
    raise _Expired()


def _parent_watch(descriptor: int, done: threading.Event) -> None:
    try:
        while os.read(descriptor, 1):
            pass
    except OSError:
        pass
    finally:
        os.close(descriptor)
    if not done.is_set():
        os._exit(70)


def _install_parent_guard(
    descriptor: int, done: threading.Event, expected_parent: int
) -> None:
    if sys.platform.startswith("linux"):
        import ctypes

        if os.getppid() != expected_parent:
            os._exit(70)
        library = ctypes.CDLL(None, use_errno=True)
        if library.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "could not install parent-death signal")
        if os.getppid() != expected_parent:
            os._exit(70)
        os.close(descriptor)
        return
    watcher = threading.Thread(
        target=_parent_watch, args=(descriptor, done), daemon=True
    )
    watcher.start()


def _write_report(report: AudioInspection) -> None:
    payload = (report.to_json() + "\n").encode("utf-8")
    if len(payload) > _STDOUT_LIMIT:
        raise ValueError("child report exceeded protocol")
    offset = 0
    while offset < len(payload):
        offset += os.write(1, payload[offset:])


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 2:
        return 70
    control_descriptor = int(arguments[0])
    expected_parent = int(arguments[1])
    done = threading.Event()
    sys.dont_write_bytecode = True
    try:
        _install_parent_guard(control_descriptor, done, expected_parent)
        _apply_limits()
        signal.signal(signal.SIGXCPU, lambda *_arguments: os._exit(71))
        context = _read_context()
        if context["capability"] != "audio":
            raise ValueError("invalid child context")
        limits = _limits(context["limits"])
        expected = _fingerprint(context["expected_source"])
        child_seconds = context["child_seconds"]
        probe = context["probe"]
        if (
            type(child_seconds) is not float
            or not 0 < child_seconds <= float(limits.wall_seconds)
            or type(probe) is not str
            or len(probe) > limits.probe_output_bytes
        ):
            raise ValueError("invalid child context")
        # The parent already holds a wall clock over this process.  This is the
        # child's own copy of it, so an interpretation that runs away inside a
        # bounded document still ends as a named timeout rather than waiting on
        # a kill from outside.
        signal.signal(signal.SIGALRM, _expire)
        signal.setitimer(signal.ITIMER_REAL, child_seconds)
        result = interpret(
            probe,
            _sidecars(context["sidecars"], limits),
            _path_context(context["path"]),
            expected,
            limits,
        )
        # The deadline covered the interpretation, which is the unbounded part.
        # An alarm arriving during the write would turn a complete report into
        # a child failure, and the parent's own wall clock still bounds it.
        signal.setitimer(signal.ITIMER_REAL, 0)
        _write_report(
            AudioInspection(
                result.outcome,
                expected if result.outcome.succeeded else None,
                result.facts,
                result.chapters,
                result.embedded_cover,
                result.chapters_truncated,
                result.title,
                result.container_facts,
                result.chapter_rows_omitted,
            )
        )
        return 0
    except _Expired:
        try:
            _write_report(AudioInspection(AudioInspectionOutcome.TIMEOUT, None))
            return 0
        except BaseException:
            return 70
    except MemoryError:
        try:
            _write_report(AudioInspection(AudioInspectionOutcome.RESOURCE_LIMIT, None))
            return 0
        except BaseException:
            return 70
    except BaseException:
        try:
            _write_report(
                AudioInspection(AudioInspectionOutcome.INTERNAL_FAILURE, None)
            )
            return 0
        except BaseException:
            return 70
    finally:
        # Disarmed on every exit, including the reports written above: an
        # alarm still pending when this process ends has nothing left to
        # interrupt but the writing of the report itself.
        signal.setitimer(signal.ITIMER_REAL, 0)
        done.set()


if __name__ == "__main__":
    raise SystemExit(main())
