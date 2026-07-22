"""Private composition entry point for one transient EPUB extraction child."""

from __future__ import annotations

import json
import os
import resource
import signal
import sys
import threading
import time
import zipfile
from datetime import datetime, timedelta, timezone

from mechanical_librarian.adapters.evidence_extractors.epub_package import (
    EpubPackageExtractor,
    _SourceIO,
)
from mechanical_librarian.adapters.evidence_extractors.epub_opening import EpubOpeningExtractor
from mechanical_librarian.domain.identifiers import (
    AdapterId,
    ConnectionId,
    ExternalItemId,
    Fingerprint,
    LibraryId,
    SchemaVersion,
    TargetId,
)
from mechanical_librarian.domain.epub_opening import EpubOpeningLimits, EpubOpeningReport
from mechanical_librarian.domain.inventory import FileReference
from mechanical_librarian.adapters.evidence_extractors.epub_package_model import (
    EpubPackageLimits,
    EpubPackageCancellation,
    EpubPackageCapability,
    EpubInspectionOutcome,
    EpubPackageReport,
    EpubPackageRequest,
    EpubPackageSource,
)


_ADDRESS_SPACE = 256 * 1024 * 1024
_CPU_SECONDS = 30
_OPEN_FILES = 64
_CONTEXT_LIMIT = 65_536
_STDOUT_LIMIT = 1_048_576


def _apply_limits() -> None:
    for limit, value in (
        (resource.RLIMIT_CORE, 0),
        (resource.RLIMIT_NOFILE, _OPEN_FILES),
        (resource.RLIMIT_CPU, _CPU_SECONDS),
    ):
        current_soft, current_hard = resource.getrlimit(limit)
        hard = value if current_hard == resource.RLIM_INFINITY else min(value, current_hard)
        resource.setrlimit(limit, (min(value, hard), hard))
    try:
        current_soft, current_hard = resource.getrlimit(resource.RLIMIT_AS)
        hard = _ADDRESS_SPACE if current_hard == resource.RLIM_INFINITY else min(_ADDRESS_SPACE, current_hard)
        resource.setrlimit(resource.RLIMIT_AS, (min(_ADDRESS_SPACE, hard), hard))
    except ValueError:
        # Darwin's framework build rejects RLIMIT_AS/DATA/RSS values below its
        # existing shared-library reservations. macOS is therefore a
        # development-only compatibility target for this command; Linux
        # production must fail closed if RLIMIT_AS cannot be installed.
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
    decoded = json.loads(value)
    if type(decoded) is not dict:
        raise ValueError("invalid child context")
    expected = {"child_seconds", "expected_source", "limits", "logical_identity"}
    if frozenset(decoded) not in {
        frozenset(expected),
        frozenset({*expected, "capability"}),
    }:
        raise ValueError("invalid child context")
    return decoded


def _limits(value: object) -> EpubPackageLimits:
    item = _closed_object(value, {"profile", *EpubPackageLimits.HARD_CAPS})
    if item["profile"] != "epub-package-limits-v1":
        raise ValueError("invalid child context")
    return EpubPackageLimits(**{name: item[name] for name in EpubPackageLimits.HARD_CAPS})


def _opening_limits(value: object) -> EpubOpeningLimits:
    item = _closed_object(
        value,
        {"package", "profile", *EpubOpeningLimits.HARD_CAPS},
    )
    if item["profile"] != "epub-opening-limits-v1":
        raise ValueError("invalid child context")
    return EpubOpeningLimits(
        package=_limits(item["package"]),
        **{name: item[name] for name in EpubOpeningLimits.HARD_CAPS},
    )


def _fingerprint(value: object) -> Fingerprint:
    item = _closed_object(value, {"algorithm", "digest", "schema"})
    return Fingerprint(item["algorithm"], SchemaVersion(item["schema"]), item["digest"])


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
    watcher = threading.Thread(target=_parent_watch, args=(descriptor, done), daemon=True)
    watcher.start()


def _write_report(report: EpubPackageReport | EpubOpeningReport) -> None:
    payload = (report.to_json() + "\n").encode("utf-8")
    if len(payload) > _STDOUT_LIMIT:
        raise ValueError("child report exceeded protocol")
    offset = 0
    while offset < len(payload):
        offset += os.write(1, payload[offset:])


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 3:
        return 70
    source_descriptor = int(arguments[0])
    control_descriptor = int(arguments[1])
    expected_parent = int(arguments[2])
    done = threading.Event()
    limits: EpubPackageLimits | EpubOpeningLimits = EpubPackageLimits()
    capability = EpubPackageCapability.PACKAGE
    sys.dont_write_bytecode = True
    try:
        _install_parent_guard(control_descriptor, done, expected_parent)
        _apply_limits()
        signal.signal(signal.SIGXCPU, lambda *_arguments: os._exit(71))
        context = _read_context()
        if "capability" in context and context["capability"] != "opening_matter":
            raise ValueError("invalid child context")
        capability = (
            EpubPackageCapability.OPENING_MATTER
            if context.get("capability") == "opening_matter"
            else EpubPackageCapability.PACKAGE
        )
        limits = (
            _opening_limits(context["limits"])
            if capability is EpubPackageCapability.OPENING_MATTER
            else _limits(context["limits"])
        )
        package_limits = limits.package if type(limits) is EpubOpeningLimits else limits
        expected = _fingerprint(context["expected_source"])
        child_seconds = context["child_seconds"]
        logical_identity = context["logical_identity"]
        if (
            type(child_seconds) is not float
            or not 0 < child_seconds <= (
                float(limits.wall_seconds)
                if type(limits) is EpubOpeningLimits
                else 55.0
            )
            or
            type(logical_identity) is not str
            or len(logical_identity) != 64
            or any(character not in "0123456789abcdef" for character in logical_identity)
        ):
            raise ValueError("invalid child context")
        length = os.fstat(source_descriptor).st_size
        request = EpubPackageRequest(
            TargetId(AdapterId("local-epub"), ConnectionId("local"), LibraryId("local"), ExternalItemId(logical_identity)),
            FileReference("local", f"{logical_identity}.epub"),
            EpubPackageSource(length, lambda offset, size: os.pread(source_descriptor, size, offset)),
            expected,
            capability,
            package_limits,
            datetime.now(timezone.utc) + timedelta(seconds=child_seconds),
            time.monotonic() + child_seconds,
            EpubPackageCancellation("epub-child", lambda: False),
        )
        if type(limits) is EpubOpeningLimits:
            package_request = EpubPackageRequest(
                request.target,
                request.file,
                request.source,
                request.expected_source,
                EpubPackageCapability.PACKAGE,
                request.limits,
                request.deadline,
                request.monotonic_deadline,
                request.cancellation,
            )
            try:
                with zipfile.ZipFile(_SourceIO(package_request), "r", allowZip64=True) as archive:
                    package_report = EpubPackageExtractor().extract(package_request, archive)
                    report = EpubOpeningExtractor().extract(
                        request, limits, package_report, archive
                    )
            except zipfile.BadZipFile:
                report = EpubOpeningReport(EpubInspectionOutcome.UNSUPPORTED_FORMAT, limits)
            except (OSError, ValueError):
                report = EpubOpeningReport(EpubInspectionOutcome.INTEGRITY_FAILURE, limits)
        else:
            report = EpubPackageExtractor().extract(request)
        _write_report(report)
        return 0
    except BaseException:
        try:
            _write_report(
                EpubOpeningReport(EpubInspectionOutcome.INTERNAL_FAILURE, limits)
                if type(limits) is EpubOpeningLimits
                else EpubPackageReport(
                    EpubInspectionOutcome.INTERNAL_FAILURE,
                    EpubPackageCapability.PACKAGE,
                    limits,
                )
            )
            return 0
        except BaseException:
            return 70
    finally:
        done.set()
        try:
            os.close(source_descriptor)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
