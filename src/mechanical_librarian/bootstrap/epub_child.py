"""Private composition entry point for one transient EPUB extraction child."""

from __future__ import annotations

import hashlib
import json
import os
import resource
import signal
import sys
import threading
import time
import zipfile
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import cast

from mechanical_librarian.adapters.evidence_extractors import (
    epub_opening as opening_parser,
)
from mechanical_librarian.adapters.evidence_extractors.epub_local import (
    package_facts as _adapter_package_facts,
)
from mechanical_librarian.adapters.evidence_extractors.epub_local import (
    rear_facts as _adapter_rear_facts,
)
from mechanical_librarian.adapters.evidence_extractors.epub_opening import (
    EpubOpeningExtractor,
)
from mechanical_librarian.adapters.evidence_extractors.epub_package import (
    EpubPackageExtractor,
    _Rejected,
    _SourceIO,
)
from mechanical_librarian.adapters.evidence_extractors.epub_package_model import (
    EpubPackageCancellation,
    EpubPackageCapability,
    EpubPackageDeclarationKind,
    EpubPackageField,
    EpubPackageReport,
    EpubPackageRequest,
    EpubPackageSource,
    EpubPackageSourceError,
    epub_resource_identity,
)
from mechanical_librarian.domain.epub_inspection import (
    EpubInspection,
    EpubInspectionFact,
    EpubInspectionOutcome,
    EpubPackageLimits,
)
from mechanical_librarian.domain.epub_opening import (
    EpubOpeningLimits,
    EpubOpeningReport,
)
from mechanical_librarian.domain.epub_source import FileReference
from mechanical_librarian.domain.identifiers import (
    AdapterId,
    ConnectionId,
    ExternalItemId,
    Fingerprint,
    LibraryId,
    SchemaVersion,
    TargetId,
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
    expected = {"child_seconds", "expected_source", "limits", "logical_identity", "capability"}
    legacy = expected - {"capability"}
    if frozenset(decoded) == frozenset(legacy):
        decoded["capability"] = "opening"
    elif frozenset(decoded) != frozenset(expected):
        raise ValueError("invalid child context")
    return decoded


def _limits(value: object) -> EpubPackageLimits:
    item = _closed_object(value, {"profile", *EpubPackageLimits.HARD_CAPS})
    if item["profile"] != "epub-package-limits-v1":
        raise ValueError("invalid child context")
    return EpubPackageLimits(
        **cast(
            "dict[str, int]",
            {name: item[name] for name in EpubPackageLimits.HARD_CAPS},
        )
    )


def _opening_limits(value: object) -> EpubOpeningLimits:
    item = _closed_object(
        value,
        {"package", "profile", *EpubOpeningLimits.HARD_CAPS},
    )
    if item["profile"] != "epub-opening-limits-v1":
        raise ValueError("invalid child context")
    return EpubOpeningLimits(
        package=_limits(item["package"]),
        **cast(
            "dict[str, int]",
            {name: item[name] for name in EpubOpeningLimits.HARD_CAPS},
        ),
    )


def _fingerprint(value: object) -> Fingerprint:
    item = _closed_object(value, {"algorithm", "digest", "schema"})
    return Fingerprint(
        cast(str, item["algorithm"]),
        SchemaVersion(cast(str, item["schema"])),
        cast(str, item["digest"]),
    )


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


def _write_report(report: EpubInspection) -> None:
    payload = (report.to_json() + "\n").encode("utf-8")
    if len(payload) > _STDOUT_LIMIT:
        raise ValueError("child report exceeded protocol")
    offset = 0
    while offset < len(payload):
        offset += os.write(1, payload[offset:])


def _write_cover(value: dict[str, object], data: bytes | None, descriptor: int) -> None:
    payload = (json.dumps(value, separators=(",", ":")) + "\n").encode("utf-8")
    if len(payload) > _STDOUT_LIMIT:
        raise ValueError("child cover exceeded protocol")
    os.write(1, payload)
    if data is not None:
        offset = 0
        while offset < len(data):
            written = os.write(descriptor, data[offset:])
            if written <= 0:
                raise OSError("cover pipe write failed")
            offset += written


def _selected_cover(package_request: EpubPackageRequest) -> tuple[dict[str, object], bytes | None]:
    try:
        rejected = EpubPackageExtractor().preflight(package_request)
        if rejected is not None:
            return {"state": "invalid_declaration"}, None
        archive_class = zipfile.ZipFile
        with archive_class(_SourceIO(package_request), "r", allowZip64=True) as archive:
            report = EpubPackageExtractor().extract(package_request, archive)
            if not report.succeeded:
                return {"state": "invalid_declaration"}, None
            values = [item.raw_value for item in report.observations if item.field is EpubPackageField.COVER]
            if not values:
                return {"state": "absent"}, None
            if len(values) != 1:
                return {"state": "invalid_declaration"}, None
            media = [item.media_type for item in report.declarations if item.kind is EpubPackageDeclarationKind.MANIFEST_ITEM and item.resource_identity == values[0]]
            names = [name for name in archive.namelist() if epub_resource_identity(name) == values[0]]
            if len(media) != 1 or len(names) != 1:
                return {"state": "invalid_declaration"}, None
            if media[0] not in {"image/jpeg", "image/png", "image/webp"}:
                return {"state": "unsupported_format"}, None
            with archive.open(names[0]) as member:
                data = member.read(8 * 1024 * 1024 + 1)
            if len(data) > 8 * 1024 * 1024:
                return {"state": "oversized"}, None
            return {"state": "declared", "media_type": media[0], "length": len(data), "identity": values[0]}, data
    except (OSError, ValueError, zipfile.BadZipFile):
        return {"state": "invalid_declaration"}, None


def _inspect_opening(
    request: EpubPackageRequest,
    package_request: EpubPackageRequest,
    limits: EpubOpeningLimits,
) -> EpubOpeningReport:
    return _inspect_bundle(request, package_request, limits)[1]


def _inspect_bundle(
    request: EpubPackageRequest,
    package_request: EpubPackageRequest,
    limits: EpubOpeningLimits,
) -> tuple[EpubPackageReport | None, EpubOpeningReport, tuple[EpubInspectionFact, ...]]:
    try:
        rejected = EpubPackageExtractor().preflight(package_request)
        if rejected is not None:
            return (
                EpubPackageReport(rejected, EpubPackageCapability.PACKAGE, package_request.limits),
                EpubOpeningReport(rejected, limits),
                (),
            )
        with zipfile.ZipFile(_SourceIO(package_request), "r", allowZip64=True) as archive:
            package_report = EpubPackageExtractor().extract(package_request, archive)
            opening_report = EpubOpeningExtractor().extract(request, limits, package_report, archive)
            try:
                rear_facts = _adapter_rear_facts(request, package_report, opening_report, archive)
            # ``rejected`` is rebound by this handler, but mypy keeps the ``None``
            # narrowing left behind by the outer preflight result, so each use
            # restates the handler's own exception type through a no-op cast.
            except opening_parser._AggregateRejected as rejected:
                if (
                    cast("opening_parser._AggregateRejected", rejected).outcome
                    is not EpubInspectionOutcome.RESOURCE_LIMIT
                    or not opening_report.outcome.succeeded
                ):
                    raise
                return (
                    package_report,
                    replace(
                        opening_report,
                        warnings=(
                            *opening_report.warnings[: limits.warnings - 1],
                            "rear_resource_limit",
                        ),
                    ),
                    tuple(
                        fact
                        for fact in cast(
                            "opening_parser._AggregateRejected", rejected
                        ).partial_facts
                        if type(fact) is EpubInspectionFact
                    ),
                )
            return package_report, opening_report, rear_facts
    except _Rejected as rejected:
        return (
            None,
            EpubOpeningReport(rejected.outcome, limits),
            (),
        )
    except opening_parser._AggregateRejected as rejected:
        return (
            None,
            EpubOpeningReport(rejected.outcome, limits),
            (),
        )
    except EpubPackageSourceError:
        return (
            None,
            EpubOpeningReport(EpubInspectionOutcome.UNAVAILABLE_SOURCE, limits),
            (),
        )
    except zipfile.BadZipFile:
        return (
            None,
            EpubOpeningReport(EpubInspectionOutcome.UNSUPPORTED_FORMAT, limits),
            (),
        )
    except (OSError, ValueError):
        return (
            None,
            EpubOpeningReport(EpubInspectionOutcome.INTEGRITY_FAILURE, limits),
            (),
        )


def _package_facts(report: EpubPackageReport, source: Fingerprint) -> tuple[EpubInspectionFact, ...]:
    return _adapter_package_facts(report, source)


def _inspection(
    package_report: EpubPackageReport, opening_report: EpubOpeningReport,
    rear_facts: tuple[EpubInspectionFact, ...] = (),
) -> EpubInspection:
    opening = EpubInspection.from_opening_report(opening_report)
    source = package_report.source_fingerprint or opening.source
    if source is None:
        return EpubInspection(opening.outcome, None)
    if not package_report.succeeded:
        return EpubInspection(package_report.outcome, source)
    if opening.outcome not in {
        EpubInspectionOutcome.SUCCESS,
        EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE,
    }:
        return EpubInspection(opening.outcome, source)
    facts = (*_package_facts(package_report, source), *opening.facts, *rear_facts)
    outcome = EpubInspectionOutcome.SUCCESS if facts else EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE
    return EpubInspection(
        outcome,
        source,
        facts,
        opening.rear_resource_limit,
    )


def _rear_facts(
    request: EpubPackageRequest,
    package_report: EpubPackageReport,
    opening_report: EpubOpeningReport,
    archive: zipfile.ZipFile,
) -> tuple[EpubInspectionFact, ...]:
    return _adapter_rear_facts(request, package_report, opening_report, archive)


def _snapshot_source(descriptor: int, maximum: int) -> bytes:
    information = os.fstat(descriptor)
    if (
        type(maximum) is not int
        or maximum < 0
        or information.st_size < 0
        or information.st_size > maximum
    ):
        raise ValueError("source snapshot exceeds its limit")
    snapshot = bytearray()
    while len(snapshot) < information.st_size:
        amount = min(1_048_576, information.st_size - len(snapshot))
        value = os.pread(descriptor, amount, len(snapshot))
        if len(value) != amount:
            raise ValueError("source changed while snapshotting")
        snapshot.extend(value)
    return bytes(snapshot)


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) not in {3, 4}:
        return 70
    source_descriptor = int(arguments[0])
    control_descriptor = int(arguments[1])
    expected_parent = int(arguments[2])
    done = threading.Event()
    limits = EpubOpeningLimits()
    sys.dont_write_bytecode = True
    try:
        _install_parent_guard(control_descriptor, done, expected_parent)
        _apply_limits()
        signal.signal(signal.SIGXCPU, lambda *_arguments: os._exit(71))
        context = _read_context()
        capability = context.get("capability", "opening")
        if capability not in {"opening", "cover"}:
            raise ValueError("invalid child context")
        limits = _opening_limits(context["limits"])
        package_limits = limits.package
        expected = _fingerprint(context["expected_source"])
        child_seconds = context["child_seconds"]
        logical_identity = context["logical_identity"]
        if (
            type(child_seconds) is not float
            or not 0 < child_seconds <= float(limits.wall_seconds)
            or
            type(logical_identity) is not str
            or len(logical_identity) != 64
            or any(character not in "0123456789abcdef" for character in logical_identity)
        ):
            raise ValueError("invalid child context")
        snapshot = _snapshot_source(
            source_descriptor,
            package_limits.source_archive_bytes,
        )
        if hashlib.sha256(snapshot).hexdigest() != expected.digest:
            _write_report(
                EpubInspection(EpubInspectionOutcome.INTEGRITY_FAILURE, None)
            )
            return 0
        request = EpubPackageRequest(
            TargetId(AdapterId("local-epub"), ConnectionId("local"), LibraryId("local"), ExternalItemId(logical_identity)),
            FileReference("local", f"{logical_identity}.epub"),
            EpubPackageSource(
                len(snapshot),
                lambda offset, size: snapshot[offset : offset + size],
            ),
            expected,
            EpubPackageCapability.OPENING_MATTER,
            package_limits,
            datetime.now(timezone.utc) + timedelta(seconds=child_seconds),
            time.monotonic() + child_seconds,
            EpubPackageCancellation("epub-child", lambda: False),
        )
        package_request = EpubPackageRequest(
            request.target, request.file, request.source, request.expected_source,
            EpubPackageCapability.PACKAGE, request.limits, request.deadline,
            request.monotonic_deadline, request.cancellation,
        )
        if capability == "cover":
            if len(arguments) != 4:
                raise ValueError("cover descriptor is unavailable")
            result, data = _selected_cover(package_request)
            _write_cover(result, data, int(arguments[3]))
            return 0
        bundle = _inspect_bundle(request, package_request, limits)
        package_report, opening_report = bundle[:2]
        rear_facts = bundle[2] if len(bundle) == 3 else ()
        if package_report is None:
            _write_report(EpubInspection(opening_report.outcome, None))
        else:
            _write_report(_inspection(package_report, opening_report, rear_facts))
        return 0
    except MemoryError:
        try:
            _write_report(EpubInspection(EpubInspectionOutcome.RESOURCE_LIMIT, None))
            return 0
        except BaseException:
            return 70
    except BaseException:
        try:
            _write_report(EpubInspection(EpubInspectionOutcome.INTERNAL_FAILURE, None))
            return 0
        except BaseException:
            return 70
    finally:
        done.set()
        try:
            os.close(source_descriptor)
        except OSError:
            pass
        if len(arguments) == 4:
            try:
                os.close(int(arguments[3]))
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
