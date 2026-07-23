"""Private composition entry point for one transient EPUB extraction child."""

from __future__ import annotations

import json
import hashlib
import os
import re
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
    _Rejected,
)
from mechanical_librarian.adapters.evidence_extractors.epub_opening import EpubOpeningExtractor
from mechanical_librarian.adapters.evidence_extractors.epub_local import package_facts as _adapter_package_facts, rear_facts as _adapter_rear_facts
from mechanical_librarian.adapters.evidence_extractors import epub_opening as opening_parser
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
from mechanical_librarian.domain.epub_inspection import EpubPackageLimits, EpubInspectionOutcome
from mechanical_librarian.domain.epub_inspection import EpubInspection, EpubInspectionFact, EpubInspectionField
from mechanical_librarian.domain.epub_source import FileReference
from mechanical_librarian.adapters.evidence_extractors.epub_package_model import (
    EpubPackageCancellation,
    EpubPackageCapability,
    EpubPackageRequest,
    EpubPackageSource,
    EpubPackageSourceError,
    EpubPackageField,
    EpubPackageDeclarationKind,
    EpubPackageReport,
    epub_resource_identity,
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
    if frozenset(decoded) != frozenset(expected):
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


def _write_report(report: EpubInspection) -> None:
    payload = (report.to_json() + "\n").encode("utf-8")
    if len(payload) > _STDOUT_LIMIT:
        raise ValueError("child report exceeded protocol")
    offset = 0
    while offset < len(payload):
        offset += os.write(1, payload[offset:])


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
) -> tuple[object, EpubOpeningReport, tuple[EpubInspectionFact, ...]]:
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
            return package_report, opening_report, _adapter_rear_facts(request, package_report, opening_report, archive)
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


_PACKAGE_FIELDS = {
    EpubPackageField.TITLE: EpubInspectionField.TITLE,
    EpubPackageField.SUBTITLE: EpubInspectionField.SUBTITLE,
    EpubPackageField.CREATOR: EpubInspectionField.CREATOR,
    EpubPackageField.CONTRIBUTOR: EpubInspectionField.CONTRIBUTOR,
    EpubPackageField.COLLECTION: EpubInspectionField.SERIES,
    EpubPackageField.COLLECTION_POSITION: EpubInspectionField.SERIES_SEQUENCE,
    EpubPackageField.PUBLICATION_DATE: EpubInspectionField.PUBLICATION_DATE,
    EpubPackageField.PUBLISHER: EpubInspectionField.PUBLISHER,
    EpubPackageField.DESCRIPTION: EpubInspectionField.DESCRIPTION,
    EpubPackageField.LANGUAGE: EpubInspectionField.LANGUAGE,
    EpubPackageField.SUBJECT: EpubInspectionField.SUBJECT,
}


def _package_facts(report, source: Fingerprint) -> tuple[EpubInspectionFact, ...]:
    return _adapter_package_facts(report, source)

    # Kept unreachable only until the next cleanup patch removes this legacy
    # implementation; the operational projection is adapter-owned above.
    facts: list[EpubInspectionFact] = []
    for observation in report.observations:
        field = _PACKAGE_FIELDS.get(observation.field)
        if field is None and observation.field is not EpubPackageField.IDENTIFIER:
            continue
        observed = observation.raw_value
        if not observed or len(observed) > 280:
            continue
        if observation.field is EpubPackageField.IDENTIFIER:
            canonical = observation.normalized_value
            if canonical is None or len(canonical) not in {10, 13}:
                continue
            field = EpubInspectionField.ISBN
            checksum = (
                "valid_isbn_10" if canonical is not None and len(canonical) == 10
                else "valid_isbn_13" if canonical is not None and len(canonical) == 13
                else "invalid"
            )
            media = "unknown"
            classification = "unlabelled"
        else:
            canonical = observation.normalized_value
            checksum = "not_applicable"
            media = "not_applicable"
            classification = "not_applicable"
        state = (
            "oversized" if observed.startswith("oversized-source-text-sha256:")
            else "unsupported" if observed.startswith("unsafe-source-text-sha256:")
            else "malformed" if field is EpubInspectionField.ISBN and canonical is None
            else "inapplicable" if observation.applicability.value == "inapplicable"
            else "untyped" if field is EpubInspectionField.SUBJECT
            else "valid"
        )
        facts.append(EpubInspectionFact(field, source, observed, canonical, observed, checksum, media, classification, "package_metadata", state=state))
    return tuple(facts)


def _inspection(
    package_report, opening_report: EpubOpeningReport,
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
    return EpubInspection(outcome, source, tuple(sorted(facts, key=lambda fact: (fact.basis, fact.field.value, fact.observed_value))))


_REAR_MARKER = re.compile(r"(?:copyright|©|all rights reserved)", re.I)
_REAR_BYLINE = re.compile(r"^(?:by|author\s*[:\-]?|written by)\s+(.+)$", re.I)


def _rear_facts(
    request: EpubPackageRequest,
    package_report: EpubPackageReport,
    opening_report: EpubOpeningReport,
    archive: zipfile.ZipFile,
) -> tuple[EpubInspectionFact, ...]:
    return _adapter_rear_facts(request, package_report, opening_report, archive)

    """Inspect the bounded tail pool without extending opening-matter selection.

    Package validation has already made spine identities opaque and unique.  The
    tail is deliberately independent: each of its last eight slots is consumed
    once, and a disposition never causes an earlier resource to be substituted.
    """
    if not package_report.succeeded or package_report.source_fingerprint is None:
        return ()
    manifests = {
        value.resource_identity: value
        for value in package_report.declarations
        if value.kind is EpubPackageDeclarationKind.MANIFEST_ITEM
        and value.resource_identity is not None
    }
    spine = [
        value.resource_identity
        for value in package_report.declarations
        if value.kind is EpubPackageDeclarationKind.SPINE_ITEM
        and value.resource_identity is not None
    ]
    encrypted = {
        value.resource_identity
        for value in package_report.declarations
        if value.kind is EpubPackageDeclarationKind.ENCRYPTION
        and value.resource_identity is not None
    }
    opened = {value.resource_identity for value in opening_report.resources if value.opened}
    rear_limits = EpubOpeningLimits(
        package=request.limits,
        selected_documents=4,
        expanded_bytes_per_resource=1_048_576,
        expanded_bytes_per_selection=4_194_304,
        text_characters_per_selection=262_144,
        nodes_per_resource=50_000,
        nodes_per_selection=50_000,
        facts=16,
    )
    facts: list[EpubInspectionFact] = []
    expanded = text = nodes = rear_opens = 0
    def inspect_archive(archive: zipfile.ZipFile) -> tuple[EpubInspectionFact, ...]:
        nonlocal expanded, text, nodes, rear_opens
        names = {epub_resource_identity(info.filename): info.filename for info in archive.infolist() if not info.is_dir()}
        for identity in reversed(spine[-8:]):
            opening_parser._check(request)
            # An opening-pool resource still consumes this fixed tail slot.
            if identity in opened:
                continue
            declaration = manifests.get(identity)
            if declaration is None or identity in encrypted:
                continue
            if declaration.media_type not in {"application/xhtml+xml", "text/html"}:
                continue
            name = names.get(identity)
            if name is None:
                continue
            if rear_opens >= 4:
                break
            data: bytes | None = None
            try:
                data = opening_parser._read_member(
                    archive, name, rear_limits.expanded_bytes_per_resource, request
                )
                rear_opens += 1
                expanded += len(data)
                if expanded > rear_limits.expanded_bytes_per_selection:
                    raise opening_parser._AggregateRejected(EpubInspectionOutcome.RESOURCE_LIMIT)
                projection = opening_parser._parse(data, declaration.media_type, rear_limits, request)
                text += projection.work_text_characters
                nodes += projection.work_nodes
                if text > rear_limits.text_characters_per_selection or nodes > rear_limits.nodes_per_selection:
                    raise opening_parser._AggregateRejected(EpubInspectionOutcome.RESOURCE_LIMIT)
            except opening_parser._AggregateRejected:
                raise
            except (opening_parser._ResourceRejected, opening_parser._MalformedMarkup,
                    opening_parser._UnsafeConstruct, opening_parser._UnsupportedEncoding,
                    ValueError):
                continue
            marker = _REAR_MARKER.search(projection.text)
            if marker is None:
                continue
            digest = hashlib.sha256(data).hexdigest()
            parent_ordinals = {ancestor for element in projection.elements for ancestor in element.ancestors}
            for element in projection.elements:
                if element.ordinal in parent_ordinals:
                    continue
                value = " ".join(element.exact_text.split())
                if not value or len(value) > 280:
                    continue
                field: EpubInspectionField | None = None
                canonical: str | None = None
                checksum = media = classification = "not_applicable"
                if element.semantics & {"title", "fulltitle"}:
                    field, canonical = EpubInspectionField.TITLE, value
                elif element.semantics & {"creator", "contributor"}:
                    field, canonical = EpubInspectionField.CREATOR, value
                else:
                    byline = _REAR_BYLINE.match(value)
                    if byline is not None and len(byline.group(1)) <= 280:
                        field, canonical = EpubInspectionField.CREATOR, byline.group(1)
                if field is not None:
                    facts.append(EpubInspectionFact(field, package_report.source_fingerprint, value, canonical, value, checksum, media, classification, "opening_matter"))
                isbn_matches = list(opening_parser._ISBN.finditer(value))[:4]
                for match in isbn_matches:
                    isbn = opening_parser._normal(match.group(0), opening_parser.OpeningField.IDENTIFIER)
                    if len(isbn) not in {10, 13} or not opening_parser._isbn_valid(isbn):
                        continue
                    facts.append(EpubInspectionFact(EpubInspectionField.ISBN, package_report.source_fingerprint, match.group(0), isbn, value, f"valid_isbn_{len(isbn)}", "unknown", "unlabelled", "opening_matter"))
                if len(facts) > 16:
                    raise opening_parser._AggregateRejected(EpubInspectionOutcome.RESOURCE_LIMIT)
        return tuple(facts)

    return inspect_archive(archive)


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
    if len(arguments) != 3:
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
        if "capability" in context:
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
        bundle = _inspect_bundle(request, package_request, limits)
        package_report, opening_report = bundle[:2]
        rear_facts = bundle[2] if len(bundle) == 3 else ()
        if package_report is None:
            _write_report(EpubInspection(opening_report.outcome, None))
        else:
            _write_report(_inspection(package_report, opening_report, rear_facts))
        return 0
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


if __name__ == "__main__":
    raise SystemExit(main())
