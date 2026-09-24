from __future__ import annotations

import hashlib
import io
import json
import re
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

import mechanical_librarian.bootstrap.epub_child as child
from mechanical_librarian.adapters.evidence_extractors.epub_package import (
    EpubPackageExtractor,
    _Rejected,
)
from mechanical_librarian.adapters.evidence_extractors.epub_package_model import (
    EpubPackageCancellation,
    EpubPackageCapability,
    EpubPackageDeclarationKind,
    EpubPackageLimits,
    EpubPackageReport,
    EpubPackageRequest,
    EpubPackageSource,
    EpubPackageSourceError,
)
from mechanical_librarian.adapters.evidence_extractors.process import (
    run_epub_inspector_child,
)
from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.application.configuration import MediaMountConfiguration
from mechanical_librarian.application.metadata_report import ItemInspectionError
from mechanical_librarian.bootstrap import composition
from mechanical_librarian.domain.epub_inspection import (
    DateEvent,
    DatePrecision,
    EpubInspection,
    EpubInspectionField,
    EpubInspectionOutcome,
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
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    TargetFacts,
    claim_scope,
    default_target_fields,
)
from tests.support.author_evidence import (
    AUTHOR_STATEMENTS,
    LEGITIMATE_AUTHOR_STATEMENTS,
    MIXED_AUTHOR_STATEMENTS,
    TYPED_CREDIT_STATEMENTS,
    author_epub,
)
from tests.support.epub_packages import (
    EPUB2_PACKAGE,
    EPUB3_PACKAGE,
    _opening_epub,
    _opening_package,
    _xhtml,
    epub_bytes,
    opening_resource_limit,
)


def _opening_context(digest: str) -> dict[str, object]:
    return {
        "child_seconds": 4.5,
        "expected_source": {
            "algorithm": "sha256",
            "digest": digest,
            "schema": "local-file-v1",
        },
        "limits": EpubOpeningLimits().to_data(),
        "logical_identity": digest,
    }


def test_snapshot_digest_mismatch_rejects_before_any_parser_dispatch() -> None:
    expected = hashlib.sha256(b"original").hexdigest()
    written: list[EpubOpeningReport] = []
    with (
        patch.object(child, "_install_parent_guard"),
        patch.object(child, "_apply_limits"),
        patch.object(child, "_read_context", return_value=_opening_context(expected)),
        patch.object(child, "_snapshot_source", return_value=b"changed then restored"),
        patch.object(child, "_write_report", side_effect=written.append),
        patch.object(child, "_inspect_opening") as inspect,
        patch.object(child.signal, "signal"),
        patch.object(child.os, "close"),
    ):
        assert child.main(["10", "11", "123"]) == 0
    inspect.assert_not_called()
    assert [value.outcome for value in written] == [
        EpubInspectionOutcome.INTEGRITY_FAILURE
    ]


def test_snapshot_memory_exhaustion_is_a_resource_limit_terminal() -> None:
    expected = hashlib.sha256(b"original").hexdigest()
    written: list[EpubInspection] = []
    with (
        patch.object(child, "_install_parent_guard"),
        patch.object(child, "_apply_limits"),
        patch.object(child, "_read_context", return_value=_opening_context(expected)),
        patch.object(child, "_snapshot_source", side_effect=MemoryError()),
        patch.object(child, "_write_report", side_effect=written.append),
        patch.object(child.signal, "signal"),
        patch.object(child.os, "close"),
    ):
        assert child.main(["10", "11", "123"]) == 0
    assert [(value.outcome, value.facts) for value in written] == [
        (EpubInspectionOutcome.RESOURCE_LIMIT, ())
    ]


def test_matching_snapshot_is_the_immutable_source_used_for_all_parser_reads() -> None:
    snapshot = bytearray(b"one bounded immutable snapshot")
    expected = hashlib.sha256(snapshot).hexdigest()
    observed: list[bytes] = []

    def inspect(request, package_request, limits):
        snapshot[:] = b"x" * len(snapshot)
        observed.append(request.source.read_at(0, request.source.length))
        observed.append(
            package_request.source.read_at(0, package_request.source.length)
        )
        return (
            EpubPackageReport(
                EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE,
                EpubPackageCapability.PACKAGE,
                limits.package,
            ),
            EpubOpeningReport(EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE, limits),
        )

    written: list[EpubOpeningReport] = []
    with (
        patch.object(child, "_install_parent_guard"),
        patch.object(child, "_apply_limits"),
        patch.object(child, "_read_context", return_value=_opening_context(expected)),
        patch.object(child, "_snapshot_source", return_value=bytes(snapshot)),
        patch.object(child, "_write_report", side_effect=written.append),
        patch.object(child, "_inspect_bundle", side_effect=inspect),
        patch.object(child.signal, "signal"),
        patch.object(child.os, "close"),
    ):
        assert child.main(["10", "11", "123"]) == 0
    assert observed == [b"one bounded immutable snapshot"] * 2
    assert [value.outcome for value in written] == [
        EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE
    ]


def test_unknown_capability_is_rejected_without_package_dispatch() -> None:
    context = {
        "capability": "unknown",
        "child_seconds": 4.5,
        "expected_source": {
            "algorithm": "sha256",
            "digest": "a" * 64,
            "schema": "source-v1",
        },
        "limits": EpubPackageLimits().to_data(),
        "logical_identity": "b" * 64,
    }
    written: list[EpubPackageReport] = []
    with (
        patch.object(child, "_install_parent_guard"),
        patch.object(child, "_apply_limits"),
        patch.object(child, "_read_context", return_value=context),
        patch.object(child, "_write_report", side_effect=written.append),
        patch.object(child.signal, "signal"),
        patch.object(child.os, "close"),
        patch.object(EpubPackageExtractor, "extract") as extract,
    ):
        assert child.main(["10", "11", "123"]) == 0
    assert len(written) == 1
    assert written[0].outcome == EpubInspectionOutcome.INTERNAL_FAILURE
    extract.assert_not_called()


def test_entrypoint_import_does_not_load_network_database_or_secret_implementations() -> (
    None
):
    source = Path(__file__).parents[2] / "src"
    program = """
import json
import sys
import mechanical_librarian.bootstrap.epub_child
forbidden = (
    "httpx",
    "pydantic",
    "sqlite3",
    "mechanical_librarian.adapters.library_services",
    "mechanical_librarian.adapters.persistence",
    "mechanical_librarian.infrastructure.security.target_secrets",
)
print(json.dumps(sorted(name for name in sys.modules if name.startswith(forbidden))))
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", program],
        cwd="/",
        env={"PYTHONPATH": str(source), "PYTHONDONTWRITEBYTECODE": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr.decode()
    assert json.loads(completed.stdout) == []


def test_linux_guard_rejects_a_child_already_reparented_before_installation() -> None:
    with (
        patch.object(child.sys, "platform", "linux"),
        patch.object(child.os, "getppid", return_value=1),
        patch.object(child.os, "_exit", side_effect=SystemExit(70)) as exit_process,
    ):
        with pytest.raises(SystemExit):
            child._install_parent_guard(10, threading.Event(), 999)
    exit_process.assert_called_once_with(70)


def test_constructor_timeout_preserves_typed_outcome() -> None:
    assert (
        _constructed_outcome(_Rejected(EpubInspectionOutcome.TIMEOUT))
        == EpubInspectionOutcome.TIMEOUT
    )


def test_constructor_source_failure_remains_unavailable() -> None:
    assert (
        _constructed_outcome(EpubPackageSourceError("read failed"))
        == EpubInspectionOutcome.UNAVAILABLE_SOURCE
    )


def _constructed_outcome(error: Exception) -> EpubInspectionOutcome:
    extractor = Mock()
    extractor.preflight.return_value = None
    with (
        patch.object(child, "EpubPackageExtractor", return_value=extractor),
        patch.object(child, "_SourceIO", return_value=object()),
        patch.object(child.zipfile, "ZipFile", side_effect=error),
    ):
        return child._inspect_opening(object(), object(), EpubOpeningLimits()).outcome


def _crc_mismatch(value: bytes, names: tuple[str, ...]) -> bytes:
    corrupted = bytearray(value)
    with zipfile.ZipFile(io.BytesIO(value), "r") as archive:
        for name in names:
            info = archive.getinfo(name)
            name_length, extra_length = struct.unpack_from(
                "<HH", corrupted, info.header_offset + 26
            )
            payload_offset = info.header_offset + 30 + name_length + extra_length
            corrupted[payload_offset] ^= 1
    return bytes(corrupted)


def _request(value: bytes, *, cancelled=lambda: False, deadline_seconds: float = 30.0):
    source = Fingerprint(
        "sha256", SchemaVersion("source-v1"), hashlib.sha256(value).hexdigest()
    )
    limits = EpubOpeningLimits()
    return EpubPackageRequest(
        TargetId(
            AdapterId("local-epub"),
            ConnectionId("local"),
            LibraryId("local"),
            ExternalItemId("0" * 64),
        ),
        FileReference("local", "fixture.epub"),
        EpubPackageSource(
            len(value), lambda offset, size: value[offset : offset + size]
        ),
        source,
        EpubPackageCapability.OPENING_MATTER,
        limits.package,
        datetime.now(timezone.utc) + timedelta(seconds=deadline_seconds),
        time.monotonic() + deadline_seconds,
        EpubPackageCancellation("rear-test", cancelled),
    )


def _bundle(tail: bytes):
    items = tuple(
        (f"slot{index}", f"slot{index}.xhtml", "application/xhtml+xml", "")
        for index in range(9)
    )
    package = _opening_package(
        items=items, spine=tuple(identity for identity, *_ in items)
    )
    value = _opening_epub(
        package=package,
        entries=tuple(
            (
                f"EPUB/slot{index}.xhtml",
                tail if index == 8 else _xhtml("<p>ordinary synthetic prose</p>"),
                8,
            )
            for index in range(9)
        ),
    )
    request = _request(value)
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
    return request, child._inspect_bundle(request, package_request, EpubOpeningLimits())


def _rear_facts(request, package_report, opening_report, archive):
    # Reconstruct through the actual producer for direct adapter failure seams.
    _, selection = child.EpubOpeningExtractor().extract_with_selection(
        request,
        opening_report.limits,
        package_report,
        archive,
    )
    assert selection is not None
    return child._rear_facts(
        request, package_report, opening_report, archive, selection
    )


def _inspect_in_child(value: bytes):
    source = Fingerprint(
        "sha256", SchemaVersion("local-file-v1"), hashlib.sha256(value).hexdigest()
    )
    with tempfile.TemporaryFile() as fixture:
        fixture.write(value)
        fixture.flush()
        return run_epub_inspector_child(
            fixture.fileno(),
            source,
            EpubOpeningLimits(),
            logical_identity=source.digest,
        )


def _inspect_locally(value: bytes) -> EpubInspection:
    digest = hashlib.sha256(value).hexdigest()
    request = replace(
        _request(value),
        target=TargetId(
            AdapterId("local-epub"),
            ConnectionId("local"),
            LibraryId("local"),
            ExternalItemId(digest),
        ),
        file=FileReference("local", digest + ".epub"),
        expected_source=Fingerprint("sha256", SchemaVersion("local-file-v1"), digest),
    )
    package, opening, rear = child._inspect_bundle(
        request,
        replace(request, capability=EpubPackageCapability.PACKAGE),
        EpubOpeningLimits(),
    )
    assert package is not None
    return child._inspection(package, opening, rear)


def _front_epub(markup: str) -> bytes:
    return _opening_epub(
        package=_opening_package(
            items=(("front", "front.xhtml", "application/xhtml+xml", ""),),
            spine=("front",),
        ),
        entries=(("EPUB/front.xhtml", _xhtml(markup), zipfile.ZIP_STORED),),
    )


def _label_facts(markup: str, source: str):
    if source == "front":
        return _inspect_locally(
            _front_epub(f'<section epub:type="copyright-page">{markup}</section>')
        ).facts
    request, (package, opening, _) = _bundle(_xhtml(markup))
    with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
        return _rear_facts(request, package, opening, archive)


def _package_facts(package: bytes):
    value = epub_bytes(package=package)
    request = _request(value)
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
    report = EpubPackageExtractor().extract(package_request)
    assert report.outcome == EpubInspectionOutcome.SUCCESS
    assert report.source_fingerprint is not None
    return child._package_facts(report, report.source_fingerprint)


def test_package_facts_projects_only_recognized_valid_isbns_and_normalizes_titles() -> (
    None
):
    package = (
        EPUB3_PACKAGE.replace(
            b'<dc:title id="title">A Synthetic Book</dc:title>',
            '<dc:title id="main">  Cafe\u0301   Registry  </dc:title>'.encode(),
        )
        .replace(
            b'<dc:identifier id="pub-id">9780306406157</dc:identifier>',
            b'<dc:identifier id="pub-id">urn:uuid:00000000-0000-4000-8000-000000000007</dc:identifier>',
        )
        .replace(
            b"</metadata>",
            b"""<dc:title id="subtitle">  The   Sequel  </dc:title>
    <meta refines="#subtitle" property="title-type">subtitle</meta>
    <dc:identifier>10.1234/synthetic.1</dc:identifier>
    <dc:identifier>ASIN:B012345678</dc:identifier>
    <dc:identifier>urn:synthetic:alternate</dc:identifier>
    <dc:identifier>9780306406157</dc:identifier></metadata>""",
        )
    )
    facts = _package_facts(package)
    assert [
        (fact.field.value, fact.canonical_value)
        for fact in facts
        if fact.field.value in {"title", "subtitle", "isbn"}
    ] == [
        ("title", "Caf\u00e9 Registry"),
        ("subtitle", "The Sequel"),
        ("isbn", "9780306406157"),
    ]
    assert [
        (fact.media, fact.classification_basis)
        for fact in facts
        if fact.field.value == "isbn"
    ] == [("unknown", "unlabelled")]


def test_package_facts_reject_isbn_shaped_non_isbn_scheme_values() -> None:
    package = EPUB2_PACKAGE.replace(
        b'opf:scheme="ISBN">0-306-40615-2',
        b'opf:scheme="LCCN">9780306406157',
    )
    facts = _package_facts(package)
    assert [fact for fact in facts if fact.field.value == "isbn"] == []


def test_package_facts_casefold_declared_isbn_scheme() -> None:
    package = EPUB2_PACKAGE.replace(
        b'opf:scheme="ISBN">0-306-40615-2',
        b'opf:scheme="iSbN">9780306406157',
    )
    facts = _package_facts(package)
    assert [fact.canonical_value for fact in facts if fact.field.value == "isbn"] == [
        "9780306406157"
    ]


@pytest.mark.parametrize(
    ("scheme", "value"),
    (
        (b"ISBN-10", b"9780306406157"),
        (b"iSbN-13", b"0306406152"),
    ),
)
def test_package_facts_reject_declared_isbn_length_mismatches(
    scheme: bytes,
    value: bytes,
) -> None:
    package = EPUB2_PACKAGE.replace(
        b'opf:scheme="ISBN">0-306-40615-2',
        b'opf:scheme="' + scheme + b'">' + value,
    )
    facts = _package_facts(package)
    assert [fact for fact in facts if fact.field.value == "isbn"] == []


def test_package_facts_preserve_punctuated_invalid_declared_isbn() -> None:
    package = EPUB2_PACKAGE.replace(
        b'opf:scheme="ISBN">0-306-40615-2',
        b'opf:scheme="ISBN-10">0-306-40615-3',
    )
    facts = _package_facts(package)
    isbn = next(fact for fact in facts if fact.field.value == "isbn")
    assert (isbn.observed_value, isbn.canonical_value, isbn.checksum, isbn.state) == (
        "0-306-40615-3",
        None,
        "invalid",
        "malformed",
    )


def test_package_primary_isbn_is_distinct_from_secondary_isbn() -> None:
    package = EPUB3_PACKAGE.replace(
        b"</metadata>",
        b"<dc:identifier>9780140328721</dc:identifier></metadata>",
    )
    facts = _package_facts(package)
    assert [
        (
            fact.canonical_value,
            fact.media,
            fact.classification_basis,
        )
        for fact in facts
        if fact.field.value == "isbn"
    ] == [
        ("9780306406157", "ebook", "ebook"),
        ("9780140328721", "unknown", "unlabelled"),
    ]


def test_inferred_package_identifier_remains_unknown_in_isolated_child() -> None:
    package = EPUB3_PACKAGE.replace(b' unique-identifier="pub-id"', b"")
    value = epub_bytes(package=package)
    request = _request(value)
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
    package_report = EpubPackageExtractor().extract(package_request)
    assert "inferred_package_identifier" in {
        warning.code for warning in package_report.warnings
    }

    report = _inspect_in_child(value)
    assert report.outcome == EpubInspectionOutcome.SUCCESS
    assert [
        (
            fact.canonical_value,
            fact.media,
            fact.classification_basis,
        )
        for fact in report.facts
        if fact.field is EpubInspectionField.ISBN
    ] == [("9780306406157", "unknown", "unlabelled")]


def test_package_facts_mark_missing_invalid_date_and_language_canonicals_malformed() -> (
    None
):
    package = EPUB3_PACKAGE.replace(
        b"<dc:language>en-GB</dc:language>",
        b"<dc:language>not a language!</dc:language>",
    ).replace(b"</metadata>", b"<dc:date>2026-02-30</dc:date></metadata>")
    facts = _package_facts(package)
    states = {fact.field.value: fact.state for fact in facts}
    assert states["publication_date"] == "malformed"
    assert states["language"] == "malformed"


def test_signed_offset_publication_date_replays_with_lexical_value() -> None:
    value = epub_bytes(
        package=EPUB3_PACKAGE.replace(
            b"</metadata>",
            b"""<dc:date>2026-01-13T12:34:56.789+05:30</dc:date>
  </metadata>""",
        )
    )
    inspection = _inspect_in_child(value)
    publication = next(
        fact
        for fact in inspection.facts
        if fact.field is EpubInspectionField.PUBLICATION_DATE
    )

    assert (
        publication.observed_value,
        publication.canonical_value,
        publication.state,
        publication.date_event,
        publication.date_precision,
    ) == (
        "2026-01-13T12:34:56.789+05:30",
        "2026-01-13T12:34:56.789000+05:30",
        "valid",
        DateEvent.MANIFESTATION_PUBLICATION,
        DatePrecision.TIMESTAMP,
    )
    assert EpubInspection.from_json(inspection.to_json()) == inspection


def test_package_only_date_lexicals_retain_typed_precision_in_child_replay() -> None:
    value = epub_bytes(
        package=EPUB3_PACKAGE.replace(
            b"</metadata>",
            b"""<dc:date>1750</dc:date>
    <dc:date>2024-03-05T10:30:00.1234567Z</dc:date>
  </metadata>""",
        )
    )
    inspection = _inspect_in_child(value)
    publication = [
        fact
        for fact in inspection.facts
        if fact.field is EpubInspectionField.PUBLICATION_DATE
        and fact.observed_value in {"1750", "2024-03-05T10:30:00.1234567Z"}
    ]
    assert [
        (
            fact.observed_value,
            fact.canonical_value,
            fact.state,
            fact.date_precision,
        )
        for fact in publication
    ] == [
        ("1750", "1750", "valid", DatePrecision.YEAR),
        (
            "2024-03-05T10:30:00.1234567Z",
            "2024-03-05T10:30:00.123456+00:00",
            "valid",
            DatePrecision.TIMESTAMP,
        ),
    ]
    assert EpubInspection.from_json(inspection.to_json()) == inspection


def test_opening_date_lexical_replays_with_normalized_canonical() -> None:
    package = _opening_package(
        items=(("opening", "opening.xhtml", "application/xhtml+xml", ""),),
        spine=("opening",),
    )
    value = _opening_epub(
        package=package,
        entries=(
            (
                "EPUB/opening.xhtml",
                _xhtml(
                    '<section epub:type="copyright-page">'
                    "<p>This ebook edition published "
                    "2024-03-05T10:30:00Z</p>"
                    "</section>"
                ),
                8,
            ),
        ),
    )
    inspection = _inspect_in_child(value)
    fact = next(
        value
        for value in inspection.facts
        if value.basis == "opening_matter"
        and value.field is EpubInspectionField.PUBLICATION_DATE
    )
    assert (
        fact.observed_value,
        fact.canonical_value,
        fact.date_precision,
    ) == (
        "2024-03-05T10:30:00Z",
        "2024-03-05T10:30:00+00:00",
        DatePrecision.TIMESTAMP,
    )
    assert inspection == EpubInspection.from_json(inspection.to_json())


@pytest.mark.parametrize(
    ("declared", "expected", "state", "canonical", "precision"),
    (
        (
            "conversion",
            DateEvent.CONVERSION,
            "valid",
            "2010-09-04",
            DatePrecision.DAY,
        ),
        (
            "vendor-event",
            DateEvent.UNKNOWN,
            "malformed",
            None,
            None,
        ),
    ),
)
def test_package_nonpublication_date_events_never_become_publication_facts(
    declared: str,
    expected: DateEvent,
    state: str,
    canonical: str | None,
    precision: DatePrecision | None,
) -> None:
    package = EPUB2_PACKAGE.replace(
        b'opf:event="publication"',
        f'opf:event="{declared}"'.encode(),
    )
    facts = _package_facts(package)
    dated = [fact for fact in facts if fact.date_event is not None]
    assert [
        (
            fact.field,
            fact.date_event,
            fact.state,
            fact.canonical_value,
            fact.date_precision,
        )
        for fact in dated
    ] == [
        (
            EpubInspectionField.DATE_EVENT,
            expected,
            state,
            canonical,
            precision,
        )
    ]


def test_package_oversized_sentinel_reaches_report_as_noncanonical_oversized_evidence() -> (
    None
):
    package = EPUB3_PACKAGE.replace(b"A Synthetic Book", b"x" * 4_097)
    facts = _package_facts(package)
    title = next(fact for fact in facts if fact.field.value == "title")
    assert title.observed_value.startswith("oversized-source-text-sha256:")
    assert title.canonical_value is None
    assert title.state == "oversized"
    claim = LocalClaim.create(
        kind=ClaimKind(title.field.value),
        observed=title.observed_value,
        canonical=title.canonical_value,
        excerpt=title.supporting_context,
        applicability=title.media,
        checksum=title.checksum,
        applicability_basis=title.classification_basis,
        basis=title.basis,
        state=FieldState(title.state),
    )
    report = MetadataReport.create(
        target=TargetFacts(
            "primary", "ebooks", "item-1", default_target_fields("Current title")
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(claim,),
        local_reasons=("package_metadata_title",),
        providers=(),
    )
    assert report.field_results[0].state == FieldState.OVERSIZED


def test_package_sentinels_and_explicit_invalid_isbns_preserve_only_the_admitted_forms() -> (
    None
):
    exact = "unsafe-source-text-sha256:" + "a" * 64
    nonexact = "unsafe-source-text-sha256:" + "A" * 64
    for value, expected_state, expected_canonical in (
        (exact, "unsupported", None),
        (nonexact, "valid", nonexact),
    ):
        facts = _package_facts(
            EPUB3_PACKAGE.replace(b"A Synthetic Book", value.encode())
        )
        title = next(fact for fact in facts if fact.field.value == "title")
        assert (title.state, title.canonical_value) == (
            expected_state,
            expected_canonical,
        )
    epub2 = EPUB2_PACKAGE.replace(b"0-306-40615-2", b"0-306-40615-3")
    urn = EPUB3_PACKAGE.replace(
        b"</metadata>",
        b"<dc:identifier>urn:isbn:9780306406158</dc:identifier></metadata>",
    )
    for package in (epub2, urn):
        invalid = [
            fact
            for fact in _package_facts(package)
            if fact.field.value == "isbn" and fact.canonical_value is None
        ]
        assert [(fact.checksum, fact.state) for fact in invalid] == [
            ("invalid", "malformed")
        ]
    non_isbn = EPUB3_PACKAGE.replace(
        b'<dc:identifier id="pub-id">9780306406157</dc:identifier>',
        b'<dc:identifier id="pub-id">urn:uuid:00000000-0000-4000-8000-000000000007</dc:identifier>',
    )
    assert [
        fact for fact in _package_facts(non_isbn) if fact.field.value == "isbn"
    ] == []


def test_package_role_projection_bounds_unsafe_and_oversized_values_for_report_replay() -> (
    None
):
    unsafe = "unsafe-source-text-sha256:" + "a" * 64
    oversized = "oversized-source-text-sha256:" + "a" * 64
    for role, sentinel_prefix in (
        ("author", None),
        ("y" * 64, None),
        (unsafe, None),
        (oversized, None),
        ("/Users/private/role", "unsafe"),
        ("x" * 65, "oversized"),
    ):
        facts = _package_facts(
            EPUB2_PACKAGE.replace(b'opf:role="aut"', f'opf:role="{role}"'.encode())
        )
        creator = next(fact for fact in facts if fact.field.value == "creator")
        if sentinel_prefix is None:
            assert creator.role == ("other" if role == "y" * 64 else role)
        else:
            assert re.search(
                rf"^{sentinel_prefix}-source-text-sha256:[0-9a-f]{{64}}$",
                creator.role or "",
            ), rf"^{sentinel_prefix}-source-text-sha256:[0-9a-f]{{64}}$"
        claim = LocalClaim.create(
            kind=ClaimKind.CREATOR,
            observed=creator.observed_value,
            canonical=creator.canonical_value,
            excerpt=creator.supporting_context,
            applicability=creator.media,
            checksum=creator.checksum,
            applicability_basis=creator.classification_basis,
            basis=creator.basis,
            state=FieldState(creator.state),
            role=creator.role,
        )
        report = MetadataReport.create(
            target=TargetFacts(
                "primary", "ebooks", "item-1", default_target_fields("Current title")
            ),
            file=FileFacts(0, "a" * 64, "b" * 64),
            local_claims=(claim,),
            local_reasons=("package_metadata_creator",),
            providers=(),
        )
        assert (
            MetadataReport.from_json(report.to_json()).local_claims[0].role
            == creator.role
        )


def test_package_projection_excludes_book_producer_contributors_only() -> None:
    package = EPUB2_PACKAGE.replace(
        b"</metadata>",
        b"""<dc:contributor opf:role="bkp">Synthetic Production Credit</dc:contributor>
    <dc:contributor opf:role="edt">Synthetic Editor</dc:contributor>
  </metadata>""",
    )
    value = epub_bytes(package=package)
    request = _request(value)
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
    report = EpubPackageExtractor().extract(package_request)

    assert ("contributor_role", "bkp") in [
        (observation.field.value, observation.normalized_value)
        for observation in report.observations
    ]

    projected = child._package_facts(report, report.source_fingerprint)
    contributors = [
        (fact.canonical_value, fact.role)
        for fact in projected
        if fact.field is EpubInspectionField.CONTRIBUTOR
    ]
    contextual = [
        (fact.canonical_value, fact.role)
        for fact in projected
        if fact.field is EpubInspectionField.CONTEXTUAL_CREDIT
    ]

    assert contributors == [("Synthetic Editor", "editor")]
    assert contextual == [("Synthetic Production Credit", "book_producer")]


def test_package_compound_roles_keep_bibliographic_and_contextual_evidence() -> None:
    package = EPUB3_PACKAGE.replace(
        b"</metadata>",
        (
            '<dc:contributor id="artist">Synthetic Artist</dc:contributor>'
            '<meta refines="#artist" property="role" '
            'scheme="marc:relators">trl</meta>'
            '<meta refines="#artist" property="role" '
            'scheme="marc:relators">ill</meta>'
            "</metadata>"
        ).encode(),
    )
    value = epub_bytes(package=package)
    request = _request(value)
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
    report = EpubPackageExtractor().extract(package_request)
    assert report.outcome == EpubInspectionOutcome.SUCCESS
    role_observations = [
        observation
        for observation in report.observations
        if observation.field.value == "contributor_role"
    ]
    assert [observation.normalized_value for observation in role_observations] == [
        "trl",
        "ill",
    ]
    assert (
        len({observation.citation.identity for observation in role_observations}) == 2
    )
    facts = child._package_facts(report, report.source_fingerprint)
    assert [
        (fact.canonical_value, fact.role)
        for fact in facts
        if fact.field is EpubInspectionField.CONTRIBUTOR
        and fact.canonical_value == "Synthetic Artist"
    ] == [("Synthetic Artist", "translator+illustrator")]
    assert (
        EpubInspection.from_json(
            EpubInspection(
                EpubInspectionOutcome.SUCCESS,
                facts[0].source,
                facts,
            ).to_json()
        ).facts
        == facts
    )


def test_rear_pool_reads_only_tail_evidence_with_marker_and_explicit_cues() -> None:
    request, (package, opening, _) = _bundle(
        _xhtml(
            '<p>All rights reserved</p><h1 epub:type="fulltitle">Rear Clock</h1><p>by Ada Example</p><p>ISBN 9780306406157</p>'
        )
    )
    with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
        facts = _rear_facts(request, package, opening, archive)
    assert [(fact.field.value, fact.canonical_value) for fact in facts] == [
        ("title", "Rear Clock"),
        ("creator", "Ada Example"),
        ("isbn", "9780306406157"),
    ]


def test_rear_indicia_retains_correlated_multiple_series_memberships() -> None:
    request, (package, opening, _) = _bundle(
        _xhtml(
            "<p>All rights reserved</p>"
            "<p>Book 2.5 of the Clockwork Archive</p>"
            "<p>Colophon Cycle Part 1</p>"
        )
    )
    with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
        facts = _rear_facts(request, package, opening, archive)
    series = tuple(
        (fact.field, fact.canonical_value, fact.supporting_context)
        for fact in facts
        if fact.field
        in {EpubInspectionField.SERIES, EpubInspectionField.SERIES_SEQUENCE}
    )
    assert series == (
        (
            EpubInspectionField.SERIES_SEQUENCE,
            "2.5",
            "Book 2.5 of the Clockwork Archive",
        ),
        (
            EpubInspectionField.SERIES,
            "Clockwork Archive",
            "Book 2.5 of the Clockwork Archive",
        ),
        (
            EpubInspectionField.SERIES_SEQUENCE,
            "1",
            "Colophon Cycle Part 1",
        ),
        (
            EpubInspectionField.SERIES,
            "Colophon Cycle",
            "Colophon Cycle Part 1",
        ),
    )
    assert all(
        fact.rear_provenance is not None
        for fact in facts
        if fact.field
        in {EpubInspectionField.SERIES, EpubInspectionField.SERIES_SEQUENCE}
    )


def test_isolated_inspector_maps_rear_series_to_canonical_report_claims(
    tmp_path: Path,
) -> None:
    request, _ = _bundle(
        _xhtml("<p>All rights reserved</p><p>Book 2.5 of the Clockwork Archive</p>")
    )
    payload = request.source.read_at(0, request.source.length)
    (tmp_path / "book.epub").write_bytes(payload)
    inspection = composition._IsolatedEpubInspector(
        CoverArtifactStore(tmp_path / "artifacts")
    ).inspect(
        MediaMountConfiguration("books", str(tmp_path), "/library", True),
        "book.epub",
        len(payload) + 1,
    )
    series_claims = tuple(
        claim
        for claim in inspection.claims
        if claim.kind in {ClaimKind.SERIES, ClaimKind.SERIES_SEQUENCE}
    )
    assert [(claim.kind, claim.canonical) for claim in series_claims] == [
        (ClaimKind.SERIES_SEQUENCE, "2.5"),
        (ClaimKind.SERIES, "Clockwork Archive"),
    ]
    assert all(claim.rear_provenance is not None for claim in series_claims)
    report = MetadataReport.create(
        target=TargetFacts(
            "primary", "ebooks", "item-1", default_target_fields("Current title")
        ),
        file=FileFacts(0, "a" * 64, inspection.content_digest),
        local_claims=inspection.claims,
        local_reasons=("opening_matter",),
        providers=(),
    )
    assert MetadataReport.from_json(report.to_json()) == report
    assert str(tmp_path) not in report.to_json()


@pytest.mark.parametrize(
    "fault",
    (composition.LocalMediaError("media unavailable"), OSError("I/O unavailable")),
    ids=("local-media", "os-error"),
)
def test_isolated_inspector_maps_local_media_failures_to_unreadable_outcome(
    tmp_path: Path, fault: OSError
) -> None:
    inspector = composition._IsolatedEpubInspector(
        CoverArtifactStore(tmp_path / "artifacts")
    )
    with (
        patch.object(composition, "open_local_media", side_effect=fault),
        pytest.raises(ItemInspectionError) as raised,
    ):
        inspector.inspect(
            MediaMountConfiguration("books", str(tmp_path), "/library", True),
            "book.epub",
            1,
        )
    assert raised.value.outcome is EpubInspectionOutcome.UNAVAILABLE_SOURCE


def test_isolated_inspector_maps_media_limit_to_parser_resource_outcome(
    tmp_path: Path,
) -> None:
    inspector = composition._IsolatedEpubInspector(
        CoverArtifactStore(tmp_path / "artifacts")
    )
    with (
        patch.object(
            composition,
            "open_local_media",
            side_effect=composition.LocalMediaLimitError(
                "local media source exceeds its byte limit"
            ),
        ),
        pytest.raises(ItemInspectionError) as raised,
    ):
        inspector.inspect(
            MediaMountConfiguration("books", str(tmp_path), "/library", True),
            "book.epub",
            1,
        )
    assert raised.value.outcome is EpubInspectionOutcome.RESOURCE_LIMIT


@pytest.mark.parametrize(
    "markup",
    (
        "<p>All rights reserved</p><p>Read Clockwork Archive Book 2 today</p>",
        "<p>All rights reserved</p><p>Also by Ada: Book 2 of Other Work</p>",
        "<p>All rights reserved</p><p>Reading order: Book 2 of Clockwork Archive</p>",
        "<p>All rights reserved</p><p>Book 2 of Clockwork Archive today</p>",
    ),
)
def test_rear_indicia_rejects_prose_catalogue_and_trailing_series_ambiguity(
    markup: str,
) -> None:
    request, (package, opening, _) = _bundle(_xhtml(markup))
    with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
        facts = _rear_facts(request, package, opening, archive)
    assert not any(
        fact.field in {EpubInspectionField.SERIES, EpubInspectionField.SERIES_SEQUENCE}
        for fact in facts
    )


@pytest.mark.parametrize(
    "heading",
    (
        "Reading order",
        "Publication order",
        "Books in reading order",
        "Clockwork Archive publication order",
        "Books in the series",
        "Part in series",
    ),
)
def test_rear_order_heading_excludes_its_forward_series_scope(heading: str) -> None:
    request, (package, opening, _) = _bundle(
        _xhtml(
            f"<p>All rights reserved</p><h2>{heading}</h2><p>Book 2 of Clockwork Archive</p>"
        )
    )
    with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
        facts = _rear_facts(request, package, opening, archive)
    assert not any(
        fact.field in {EpubInspectionField.SERIES, EpubInspectionField.SERIES_SEQUENCE}
        for fact in facts
    )


@pytest.mark.parametrize(
    "markup",
    (
        "<p>Series: a series of historical mysteries</p>",
        "<p>Book 2 of Clockwork Archive coming soon</p>",
        "<p>Book 2 of Clockwork Archive today!</p>",
    ),
)
def test_rear_series_trailing_clauses_are_rejected(markup: str) -> None:
    request, (package, opening, _) = _bundle(
        _xhtml("<p>All rights reserved</p>" + markup)
    )
    with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
        facts = _rear_facts(request, package, opening, archive)
    assert not any(
        fact.field in {EpubInspectionField.SERIES, EpubInspectionField.SERIES_SEQUENCE}
        for fact in facts
    )


def test_rear_series_label_can_begin_with_a_reserved_name_first_lead() -> None:
    request, (package, opening, _) = _bundle(
        _xhtml("<p>All rights reserved</p><p>Series: New Worlds</p>")
    )
    with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
        facts = _rear_facts(request, package, opening, archive)
    assert [
        (fact.field, fact.canonical_value)
        for fact in facts
        if fact.field
        in {EpubInspectionField.SERIES, EpubInspectionField.SERIES_SEQUENCE}
    ] == [(EpubInspectionField.SERIES, "New Worlds")]


@pytest.mark.parametrize(
    ("markup", "expected"),
    (
        (
            "<p>Series: Élan Cycle</p>",
            ((EpubInspectionField.SERIES, "Élan Cycle"),),
        ),
        (
            "<p>Élan Cycle Book 2.5</p>",
            (
                (EpubInspectionField.SERIES_SEQUENCE, "2.5"),
                (EpubInspectionField.SERIES, "Élan Cycle"),
            ),
        ),
    ),
)
def test_rear_unicode_series_names_preserve_label_and_order_first_forms(
    markup, expected
) -> None:
    request, (package, opening, _) = _bundle(
        _xhtml("<p>All rights reserved</p>" + markup)
    )
    with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
        facts = _rear_facts(request, package, opening, archive)
    assert (
        tuple(
            (fact.field, fact.canonical_value)
            for fact in facts
            if fact.field
            in {EpubInspectionField.SERIES, EpubInspectionField.SERIES_SEQUENCE}
        )
        == expected
    )


@pytest.mark.parametrize(
    "markup",
    (
        "<p>élan Cycle Book 2</p>",
        "<p>ёлка Cycle Book 2</p>",
        "<p>Book 2 of élan Cycle</p>",
        "<p>Book 2 of ёлка Cycle</p>",
    ),
)
def test_rear_unicode_series_names_require_explicit_cased_or_numeric_tokens(
    markup: str,
) -> None:
    request, (package, opening, _) = _bundle(
        _xhtml("<p>All rights reserved</p>" + markup)
    )
    with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
        facts = _rear_facts(request, package, opening, archive)
    assert not any(
        fact.field in {EpubInspectionField.SERIES, EpubInspectionField.SERIES_SEQUENCE}
        for fact in facts
    )


@pytest.mark.parametrize(
    "markup",
    (
        "<p>Book 2 of A Song of Ice and Fire</p>",
        "<p>Book 2 of Clockwork Archive by Ada</p>",
        "<p>Book 2 of Clockwork Archive for sale</p>",
        "<p>Book 2 of Clockwork Archive continues</p>",
        "<p>Book 2 of Clockwork Archive By Ada</p>",
        "<p>Book 2 of Clockwork Archive By The Author</p>",
        "<p>Book 2 of Clockwork Archive By An Editor</p>",
        "<p>Book 2 of Clockwork Archive For Sale</p>",
        "<p>Book 2 of Clockwork Archive Continues</p>",
        "<p>Buy Clockwork Archive Book 2</p>",
        "<p>New Clockwork Archive Book 2</p>",
        "<p>Get Clockwork Archive Book 2</p>",
        "<p>Start Clockwork Archive Book 2</p>",
    ),
)
def test_rear_series_name_grammar_is_closed(markup: str) -> None:
    request, (package, opening, _) = _bundle(
        _xhtml("<p>All rights reserved</p>" + markup)
    )
    with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
        facts = _rear_facts(request, package, opening, archive)
    series = tuple(
        (fact.field, fact.canonical_value)
        for fact in facts
        if fact.field
        in {EpubInspectionField.SERIES, EpubInspectionField.SERIES_SEQUENCE}
    )
    if "A Song" in markup:
        assert series == (
            (EpubInspectionField.SERIES_SEQUENCE, "2"),
            (EpubInspectionField.SERIES, "A Song of Ice and Fire"),
        )
    else:
        assert series == ()


def test_rear_fact_limit_keeps_a_series_pair_atomic() -> None:
    markup = "".join(f"<p>Subtitle: Earlier {index}</p>" for index in range(15))
    request, (package, opening, _) = _bundle(
        _xhtml(
            "<p>All rights reserved</p>" + markup + "<p>Book 2 of Clockwork Archive</p>"
        )
    )
    with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
        with pytest.raises(child.opening_parser._AggregateRejected) as raised:
            _rear_facts(request, package, opening, archive)
    assert raised.value.outcome is EpubInspectionOutcome.RESOURCE_LIMIT
    assert len(raised.value.partial_facts) == 15
    assert not any(
        fact.field in {EpubInspectionField.SERIES, EpubInspectionField.SERIES_SEQUENCE}
        for fact in raised.value.partial_facts
    )


def test_rear_series_indicia_aggregate_improves_recall_without_false_positives() -> (
    None
):
    samples = (
        ("<p>Book 2.5 of the Clockwork Archive</p>", 2),
        ("<p>Part 1 of the Colophon Cycle</p>", 2),
        ("<p>Series: Unpositioned Archive</p>", 1),
        ("<p>Also by Ada: Book 2 of Other Work</p>", 0),
        ("<p>Reading order: Book 2 of Clockwork Archive</p>", 0),
        ("<p>Book 2 of Clockwork Archive today</p>", 0),
    )
    expected = found = false_positives = 0
    for markup, expected_count in samples:
        request, (package, opening, _) = _bundle(
            _xhtml("<p>All rights reserved</p>" + markup)
        )
        with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
            facts = _rear_facts(request, package, opening, archive)
        actual_count = sum(
            fact.field
            in {EpubInspectionField.SERIES, EpubInspectionField.SERIES_SEQUENCE}
            for fact in facts
        )
        expected += expected_count
        found += min(actual_count, expected_count)
        false_positives += max(0, actual_count - expected_count)
    assert {
        "expected": expected,
        "found": found,
        "false_positives": false_positives,
    } == {
        "expected": 5,
        "found": 5,
        "false_positives": 0,
    }


def test_front_and_rear_bibliographic_indicia_have_paired_semantics() -> None:
    front = _inspect_in_child(
        _front_epub(
            '<section epub:type="copyright-page">'
            "<p>Subtitle: A Bounded Companion</p>"
            "<p>Publisher: Example House</p>"
            "<p>Imprint: Example Mark</p>"
            "</section>"
        )
    )
    request, (package, opening, _) = _bundle(
        _xhtml(
            "<p>All rights reserved</p>"
            "<p>Subtitle: A Bounded Companion</p>"
            "<p>Publisher: Example House</p>"
            "<p>Imprint: Example Mark</p>"
        )
    )
    with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
        rear_facts = _rear_facts(request, package, opening, archive)

    fields = {
        EpubInspectionField.SUBTITLE,
        EpubInspectionField.PUBLISHER,
        EpubInspectionField.IMPRINT,
    }
    front_facts = tuple(
        fact
        for fact in front.facts
        if fact.basis == "opening_matter" and fact.field in fields
    )
    rear_facts = tuple(fact for fact in rear_facts if fact.field in fields)
    assert [
        (
            fact.field,
            fact.observed_value,
            fact.canonical_value,
            fact.supporting_context,
        )
        for fact in rear_facts
    ] == [
        (
            fact.field,
            fact.observed_value,
            fact.canonical_value,
            fact.supporting_context,
        )
        for fact in front_facts
    ]
    assert [fact.supporting_context for fact in rear_facts] == [
        "Subtitle: A Bounded Companion",
        "Publisher: Example House",
        "Imprint: Example Mark",
    ]
    assert all(fact.rear_provenance is not None for fact in rear_facts)
    assert all(fact.rear_provenance is None for fact in front_facts)
    inspection = EpubInspection(
        EpubInspectionOutcome.SUCCESS,
        rear_facts[0].source,
        rear_facts,
    )
    assert EpubInspection.from_json(inspection.to_json()) == inspection


@pytest.mark.parametrize("source", ("front", "rear"))
def test_front_and_rear_labels_reject_contextual_values_without_name_guessing(
    source: str,
) -> None:
    markup = (
        "<p>All rights reserved</p>"
        "<p>Publisher: Printed by Printer House</p>"
        "<p>Publisher: Distributed by Retail House</p>"
        "<p>Publisher: Distributor: Distribution House</p>"
        "<p>Publisher: Retailer: Retail House</p>"
        "<p>Publisher: Copyright holder: Rights House</p>"
        "<p>Publisher: Copyright-holder: Rights House</p>"
        '<h1 epub:type="fulltitle">'
        "Publisher: Copyright © 2024 Rights House</h1>"
        "<p>Publisher: Copyright (c) 2024 Rights House</p>"
        "<p>Publisher: Copyright 2024 Rights House</p>"
        "<p>Publisher: Copyright by Rights House</p>"
        "<p>Publisher: © 2024 Rights House</p>"
        "<p>Publisher: (c) 2024 Rights House</p>"
        "<p>Imprint: EPUB conversion by Conversion House</p>"
        "<p>Publisher: Printer House</p>"
        "<p>Publisher: Distributor Books</p>"
        "<p>Publisher: Retailer Press</p>"
        "<p>Publisher: Copyright House</p>"
        "<p>Imprint: Conversion Press</p>"
    )

    fields = {
        EpubInspectionField.PUBLISHER,
        EpubInspectionField.IMPRINT,
    }
    expected = [
        (EpubInspectionField.PUBLISHER, "Printer House"),
        (EpubInspectionField.PUBLISHER, "Distributor Books"),
        (EpubInspectionField.PUBLISHER, "Retailer Press"),
        (EpubInspectionField.PUBLISHER, "Copyright House"),
        (EpubInspectionField.IMPRINT, "Conversion Press"),
    ]
    facts = tuple(
        fact
        for fact in _label_facts(markup, source)
        if fact.field in fields and (source == "rear" or fact.basis == "opening_matter")
    )
    assert [(fact.field, fact.canonical_value) for fact in facts] == expected


@pytest.mark.parametrize("source", ("front", "rear"))
@pytest.mark.parametrize(
    ("label", "rejected", "control"),
    (
        ("Publisher", "Converted by Converter", "Conversion Press"),
        ("Publisher", "Scanned by Archive Bot", "Scanner House"),
        ("Publisher", "Scanning by Archive Bot", "Scanning Stories"),
        ("Imprint", "Produced using Toolchain", "Production Imprint"),
        ("Imprint", "Created with Toolchain", "Creative Imprint"),
        ("Publisher", "Distributed by Distributor", "Distributor Books"),
        ("Publisher", "Retailed by Retailer", "Retailer Press"),
        ("Imprint", "Workflow: Synthetic Pipeline", "Workflow Press"),
        ("Publisher", "Preview of Another Work", "Preview House"),
        ("Imprint", "Also by Another Author", "Also House"),
        ("Subtitle", "Sample chapter", "Sample Companion"),
        (
            "Subtitle",
            "Other works by Another Author",
            "Other Works Companion",
        ),
    ),
)
def test_front_and_rear_labels_apply_contextual_and_promotion_vocabularies(
    label: str,
    rejected: str,
    control: str,
    source: str,
) -> None:
    fields = {
        "Publisher": EpubInspectionField.PUBLISHER,
        "Imprint": EpubInspectionField.IMPRINT,
        "Subtitle": EpubInspectionField.SUBTITLE,
    }
    markup = (
        f"<p>All rights reserved</p><p>{label}: {rejected}</p><p>{label}: {control}</p>"
    )
    facts = _label_facts(markup, source)
    assert [
        fact.canonical_value
        for fact in facts
        if fact.basis == "opening_matter" and fact.field is fields[label]
    ] == [control]


@pytest.mark.parametrize("source", ("front", "rear"))
def test_front_and_rear_labels_preserve_exact_occurrence_ownership(
    source: str,
) -> None:
    markup = (
        "<p>All rights reserved</p>"
        "<p><span>Publisher: House One</span> "
        "<span>Imprint:</span> Inline <em>Mark</em></p>"
        "<p><span>Publisher: House Two</span> "
        "<span>Subtitle: Companion</span></p>"
        "<p><span>Publisher: Repeat House</span> "
        "<span>Publisher: Repeat House</span></p>"
        "<p><span>Publisher: Echo House</span> "
        "<span>Publisher:</span> Echo House</p>"
        "<p><span>Publisher:</span> Split House "
        "<span>Imprint:</span> Split Mark "
        "<span>Subtitle:</span> Split Companion</p>"
        "<div><p><span><em>Publisher: Nested House</em></span></p></div>"
        "<p><span>Publisher: Safe House</span> "
        "<span>Imprint:</span> EPUB conversion by Toolchain</p>"
    )

    fields = {
        EpubInspectionField.SUBTITLE,
        EpubInspectionField.PUBLISHER,
        EpubInspectionField.IMPRINT,
    }
    expected = [
        (
            EpubInspectionField.PUBLISHER,
            "House One",
            "Publisher: House One",
        ),
        (
            EpubInspectionField.IMPRINT,
            "Inline Mark",
            "Imprint: Inline Mark",
        ),
        (
            EpubInspectionField.PUBLISHER,
            "House Two",
            "Publisher: House Two",
        ),
        (
            EpubInspectionField.SUBTITLE,
            "Companion",
            "Subtitle: Companion",
        ),
        (
            EpubInspectionField.PUBLISHER,
            "Repeat House",
            "Publisher: Repeat House",
        ),
        (
            EpubInspectionField.PUBLISHER,
            "Repeat House",
            "Publisher: Repeat House",
        ),
        (
            EpubInspectionField.PUBLISHER,
            "Echo House",
            "Publisher: Echo House",
        ),
        (
            EpubInspectionField.PUBLISHER,
            "Echo House",
            "Publisher: Echo House",
        ),
        (
            EpubInspectionField.PUBLISHER,
            "Split House",
            "Publisher: Split House",
        ),
        (
            EpubInspectionField.IMPRINT,
            "Split Mark",
            "Imprint: Split Mark",
        ),
        (
            EpubInspectionField.SUBTITLE,
            "Split Companion",
            "Subtitle: Split Companion",
        ),
        (
            EpubInspectionField.PUBLISHER,
            "Nested House",
            "Publisher: Nested House",
        ),
        (
            EpubInspectionField.PUBLISHER,
            "Safe House",
            "Publisher: Safe House",
        ),
    ]
    facts = _label_facts(markup, source)
    assert [
        (
            fact.field,
            fact.canonical_value,
            fact.supporting_context,
        )
        for fact in facts
        if fact.basis == "opening_matter" and fact.field in fields
    ] == expected


@pytest.mark.parametrize("source", ("front", "rear"))
def test_front_and_rear_bibliographic_canonical_values_share_normalization(
    source: str,
) -> None:
    markup = (
        "<p>All rights reserved</p>"
        "<p>Subtitle: Cafe\u0301   Companion</p>"
        "<p>Publisher: Cafe\u0301   House</p>"
        "<p>Imprint: Cafe\u0301   Mark</p>"
    )
    expected = [
        (EpubInspectionField.SUBTITLE, "Café Companion"),
        (EpubInspectionField.PUBLISHER, "Café House"),
        (EpubInspectionField.IMPRINT, "Café Mark"),
    ]
    fields = {field for field, _value in expected}
    facts = _label_facts(markup, source)
    assert [
        (fact.field, fact.canonical_value)
        for fact in facts
        if fact.basis == "opening_matter" and fact.field in fields
    ] == expected


def test_rear_split_labels_preserve_the_sixteen_fact_cap() -> None:
    labels = "".join(
        f"<span>Imprint:</span> {chr(ord('A') + index)} " for index in range(17)
    )
    request, (package, opening, _) = _bundle(
        _xhtml(f"<p>All rights reserved</p><p>{labels}</p>")
    )
    with pytest.raises(child.opening_parser._AggregateRejected) as raised:
        with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
            _rear_facts(request, package, opening, archive)
    assert raised.value.outcome == EpubInspectionOutcome.RESOURCE_LIMIT
    assert [
        (
            fact.canonical_value,
            fact.supporting_context,
            fact.rear_provenance is not None,
        )
        for fact in raised.value.partial_facts
    ] == [
        (chr(ord("A") + index), f"Imprint: {chr(ord('A') + index)}", True)
        for index in range(16)
    ]


def test_rear_bibliographic_indicia_preserves_conflicts_and_repetition() -> None:
    request, (package, opening, _) = _bundle(
        _xhtml(
            "<p>All rights reserved</p>"
            '<p epub:type="subtitle">A Structural Companion</p>'
            "<p>Publisher: House One</p>"
            "<p>Publisher: House One</p>"
            '<p epub:type="publisher">House Two</p>'
            "<p>Imprint: Mark One</p>"
            '<p epub:type="imprint">Mark Two</p>'
            "<p>Copyright holder: Not a Publisher</p>"
            '<p epub:type="publisher">Printed by Printer House</p>'
            "<p>Distributed by Retail House</p>"
            "<p>EPUB conversion by Conversion House</p>"
            "<p>Publisher: /Users/private/publisher</p>"
            "<p>Imprint: Authorization: Bearer abcdefgh</p>"
            '<section epub:type="advertisement">'
            "<p>Publisher: Advertised House</p>"
            "</section>"
            '<section epub:type="preview">'
            "<p>Imprint: Preview Mark</p>"
            "</section>"
        )
    )
    with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
        facts = _rear_facts(request, package, opening, archive)

    assert [
        (fact.field, fact.canonical_value)
        for fact in facts
        if fact.field
        in {
            EpubInspectionField.SUBTITLE,
            EpubInspectionField.PUBLISHER,
            EpubInspectionField.IMPRINT,
        }
    ] == [
        (EpubInspectionField.SUBTITLE, "A Structural Companion"),
        (EpubInspectionField.PUBLISHER, "House One"),
        (EpubInspectionField.PUBLISHER, "House One"),
        (EpubInspectionField.PUBLISHER, "House Two"),
        (EpubInspectionField.IMPRINT, "Mark One"),
        (EpubInspectionField.IMPRINT, "Mark Two"),
    ]


def test_front_labels_suppress_generic_title_semantics() -> None:
    inspection = _inspect_locally(
        _front_epub(
            '<section epub:type="titlepage">'
            '<h1 epub:type="fulltitle">'
            "Publisher: Label House</h1>"
            '<h1 epub:type="title">'
            "Imprint: Label Mark</h1>"
            '<h1 epub:type="fulltitle">'
            "Subtitle: Label Companion</h1>"
            '<h1 epub:type="fulltitle">'
            "Publisher: Copyright © 2024 Rights House</h1>"
            "</section>"
        )
    )
    front = tuple(
        fact
        for fact in inspection.facts
        if fact.basis == "opening_matter"
        and fact.field
        in {
            EpubInspectionField.SUBTITLE,
            EpubInspectionField.PUBLISHER,
            EpubInspectionField.IMPRINT,
        }
    )
    assert [(fact.field, fact.canonical_value) for fact in front] == [
        (EpubInspectionField.PUBLISHER, "Label House"),
        (EpubInspectionField.IMPRINT, "Label Mark"),
        (EpubInspectionField.SUBTITLE, "Label Companion"),
    ]
    assert not any(
        fact.basis == "opening_matter" and fact.field is EpubInspectionField.TITLE
        for fact in inspection.facts
    )


@pytest.mark.parametrize("source", ("front", "rear"))
def test_front_and_rear_labels_enforce_the_exact_excerpt_bound(source: str) -> None:
    prefix = "Publisher: "
    for value_length, admitted in (
        (280 - len(prefix), True),
        (281 - len(prefix), False),
    ):
        publisher = "P" * value_length
        markup = f"<p>All rights reserved</p><p>{prefix}{publisher}</p>"
        facts = _label_facts(markup, source)
        publishers = tuple(
            fact
            for fact in facts
            if fact.basis == "opening_matter"
            and fact.field is EpubInspectionField.PUBLISHER
        )
        assert bool(publishers) == admitted
        if admitted:
            assert (
                publishers[0].observed_value,
                publishers[0].supporting_context,
            ) == (publisher, prefix + publisher)


def test_rear_bibliographic_indicia_has_aggregate_safe_quality_evidence() -> None:
    samples = (
        (
            "<p>All rights reserved</p>"
            "<p>Subtitle: Companion One</p>"
            "<p>Publisher: House One</p>"
            "<p>Imprint: Mark One</p>",
            3,
        ),
        (
            "<p>Copyright</p>"
            '<p epub:type="subtitle">Companion Two</p>'
            '<p epub:type="publisher">House Two</p>'
            '<p epub:type="imprint">Mark Two</p>',
            3,
        ),
        (
            "<p>Copyright</p><p>Printed by Printer House</p>"
            "<p>Distributed by Retail House</p>",
            0,
        ),
        (
            '<p>Copyright</p><section epub:type="preview">'
            "<p>Subtitle: Other Work</p>"
            "<p>Publisher: Preview House</p>"
            "<p>Imprint: Preview Mark</p></section>",
            0,
        ),
    )
    expected = found = false_positives = 0
    fields = {
        EpubInspectionField.SUBTITLE,
        EpubInspectionField.PUBLISHER,
        EpubInspectionField.IMPRINT,
    }
    for markup, expected_count in samples:
        request, (package, opening, _) = _bundle(_xhtml(markup))
        with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
            facts = _rear_facts(request, package, opening, archive)
        actual_count = sum(fact.field in fields for fact in facts)
        expected += expected_count
        found += min(actual_count, expected_count)
        false_positives += max(0, actual_count - expected_count)
    aggregate = {
        "expected": expected,
        "found": found,
        "false_positives": false_positives,
    }
    assert aggregate == {"expected": 6, "found": 6, "false_positives": 0}


def test_rear_indicia_retains_roles_dates_and_contextual_credits_with_provenance() -> (
    None
):
    request, (package, opening, _) = _bundle(
        _xhtml(
            "<p>All rights reserved</p>"
            "<p>Translated and illustrated by Bea Artist</p>"
            "<p>EPUB conversion by Tool House</p>"
            "<p>First published 1998</p>"
            "<p>This ebook edition published 2024-03-05</p>"
            "<p>Copyright 2024</p>"
        )
    )
    with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
        facts = _rear_facts(request, package, opening, archive)

    assert [
        (fact.field, fact.canonical_value, fact.role)
        for fact in facts
        if fact.field
        in {
            EpubInspectionField.CONTRIBUTOR,
            EpubInspectionField.CONTEXTUAL_CREDIT,
        }
    ] == [
        (
            EpubInspectionField.CONTRIBUTOR,
            "Bea Artist",
            "translator+illustrator",
        ),
        (
            EpubInspectionField.CONTEXTUAL_CREDIT,
            "Tool House",
            "conversion",
        ),
    ]
    assert [
        (
            fact.field,
            fact.canonical_value,
            fact.date_event,
            fact.date_precision,
        )
        for fact in facts
        if fact.field
        in {
            EpubInspectionField.PUBLICATION_DATE,
            EpubInspectionField.DATE_EVENT,
        }
    ] == [
        (
            EpubInspectionField.DATE_EVENT,
            "1998",
            DateEvent.WORK_FIRST_PUBLICATION,
            DatePrecision.YEAR,
        ),
        (
            EpubInspectionField.PUBLICATION_DATE,
            "2024-03-05",
            DateEvent.MANIFESTATION_PUBLICATION,
            DatePrecision.DAY,
        ),
        (
            EpubInspectionField.DATE_EVENT,
            "2024",
            DateEvent.COPYRIGHT,
            DatePrecision.YEAR,
        ),
    ]
    assert all(fact.rear_provenance is not None for fact in facts)
    inspection = EpubInspection(
        EpubInspectionOutcome.SUCCESS,
        facts[0].source,
        facts,
    )
    assert inspection == EpubInspection.from_json(inspection.to_json())
    assert not any(
        fact.field is EpubInspectionField.CONTRIBUTOR
        and fact.canonical_value == "Tool House"
        for fact in facts
    )


def test_rear_mixed_edition_and_unsigned_datetime_evidence_fails_closed() -> None:
    request, (package, opening, _) = _bundle(
        _xhtml(
            "<p>All rights reserved</p>"
            "<p>First published as this ebook edition 2024</p>"
            "<p>THIS EBOOK EDITION: FIRST PUBLISHED 2025</p>"
            "<p>Digital edition, originally published 2026</p>"
            "<p>EPUB publication date: 2024-03-05T10:30:00</p>"
        )
    )
    with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
        facts = _rear_facts(request, package, opening, archive)

    assert not any(
        fact.field
        in {
            EpubInspectionField.PUBLICATION_DATE,
            EpubInspectionField.DATE_EVENT,
        }
        for fact in facts
    )


def test_rear_indicia_rejects_malformed_date_tokens_and_stops_credit_prose() -> None:
    request, (package, opening, _) = _bundle(
        _xhtml(
            "<p>All rights reserved</p>"
            '<h1 epub:type="fulltitle">A Sample of Life</h1>'
            "<p>Translated by A. B. Carter. Copyright 2024/03/05</p>"
            "<p>EPUB publication date: 2024-03-05T10:30:00+14:01</p>"
            "<p>Copyright 2024abc</p>"
        )
    )
    with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
        facts = _rear_facts(request, package, opening, archive)
    assert (EpubInspectionField.TITLE, "A Sample of Life") in {
        (fact.field, fact.canonical_value) for fact in facts
    }
    assert ("A. B. Carter", "translator") in {
        (fact.canonical_value, fact.role)
        for fact in facts
        if fact.field is EpubInspectionField.CONTRIBUTOR
    }
    assert not any(fact.date_event is not None for fact in facts)


def test_rear_promotion_notice_is_bounded_to_its_forward_sibling_scope() -> None:
    request, (package, opening, _) = _bundle(
        _xhtml(
            '<section epub:type="copyright-page">'
            "<p>All rights reserved</p>"
            "<p>Edited by Earlier Editor</p>"
            '<section epub:type="preview">'
            "<p>Copyright 2022</p>"
            "<p>Edited by Semantic Promo Editor</p>"
            "</section>"
            "<h2>Preview of Another Work</h2>"
            "<p>Copyright 2024</p>"
            "<p>Edited by Promo Editor</p>"
            "</section>"
            '<section epub:type="copyright-page">'
            '<h1 epub:type="fulltitle">A Sample of Life</h1>'
            "<p>Copyright 2023</p>"
            "<p>Edited by Sibling Editor</p>"
            "</section>"
        )
    )
    with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
        facts = _rear_facts(request, package, opening, archive)
    assert {
        (fact.canonical_value, fact.role)
        for fact in facts
        if fact.field is EpubInspectionField.CONTRIBUTOR
    } == {
        ("Earlier Editor", "editor"),
        ("Sibling Editor", "editor"),
    }
    assert {
        fact.canonical_value for fact in facts if fact.date_event is DateEvent.COPYRIGHT
    } == {"2023"}
    assert (EpubInspectionField.TITLE, "A Sample of Life") in {
        (fact.field, fact.canonical_value) for fact in facts
    }


def test_rear_uncontained_promotion_run_stops_at_target_section() -> None:
    request, (package, opening, _) = _bundle(
        _xhtml(
            "<p>All rights reserved</p>"
            "<p>Edited by Earlier Editor</p>"
            "<h2>Preview of Another Work</h2>"
            "<p>Copyright 2024</p>"
            "<p>Edited by Promo Editor</p>"
            '<section epub:type="copyright-page">'
            '<h1 epub:type="fulltitle">A Sample of Life</h1>'
            "<p>Copyright 2023</p>"
            "<p>Edited by Target Editor</p>"
            "</section>"
        )
    )
    with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
        facts = _rear_facts(request, package, opening, archive)
    assert {
        (fact.canonical_value, fact.role)
        for fact in facts
        if fact.field is EpubInspectionField.CONTRIBUTOR
    } == {
        ("Earlier Editor", "editor"),
        ("Target Editor", "editor"),
    }
    assert {
        fact.canonical_value for fact in facts if fact.date_event is DateEvent.COPYRIGHT
    } == {"2023"}
    assert (EpubInspectionField.TITLE, "A Sample of Life") in {
        (fact.field, fact.canonical_value) for fact in facts
    }


@pytest.mark.parametrize(
    "marker",
    (
        "All rights reserved",
        "All <em>rights</em> reserved",
        "Colophon",
        "Colo<em>phon</em>",
        "Credits",
        "Cred<em>its</em>",
        "Copyright page",
        "Copy<em>right</em> page",
    ),
)
def test_rear_marker_detection_accepts_plain_and_inline_assembled_text(
    marker: str,
) -> None:
    request, (package, opening, _) = _bundle(
        _xhtml(f"<p>{marker}</p><p>Edited by Marker Editor</p>")
    )
    with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
        facts = _rear_facts(request, package, opening, archive)
    assert ("Marker Editor", "editor") in {
        (fact.canonical_value, fact.role)
        for fact in facts
        if fact.field is EpubInspectionField.CONTRIBUTOR
    }


def test_rear_descendant_promotion_credit_and_date_ambiguity_fail_closed() -> None:
    request, (package, opening, _) = _bundle(
        _xhtml(
            "<p>All rights reserved</p>"
            "<p>Legal notice <span>Also by</span> "
            "<span>First published 1999</span></p>"
            "<p>EPUB conversion by Tool House and "
            "Scanned by Archive Bot</p>"
            "<p>Copyright notice last modified 2024</p>"
            "<p>Copyright 2023</p>"
        )
    )
    with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
        facts = _rear_facts(request, package, opening, archive)
    assert {
        (fact.canonical_value, fact.role)
        for fact in facts
        if fact.field is EpubInspectionField.CONTEXTUAL_CREDIT
    } == {
        ("Tool House", "conversion"),
        ("Archive Bot", "scanner"),
    }
    assert {
        (fact.canonical_value, fact.date_event)
        for fact in facts
        if fact.date_event is not None
    } == {("2023", DateEvent.COPYRIGHT)}


@pytest.mark.parametrize(
    "excluded",
    ("Advertisement", "Preview of Another Work", "Also by"),
)
def test_rear_colophon_is_admitted_but_advertisement_and_preview_are_not(
    excluded: str,
) -> None:
    request, (package, opening, _) = _bundle(
        _xhtml("<h2>Colophon</h2><p>Edited by Colophon Editor</p>")
    )
    with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
        facts = _rear_facts(request, package, opening, archive)
    assert [
        (fact.canonical_value, fact.role)
        for fact in facts
        if fact.field is EpubInspectionField.CONTRIBUTOR
    ] == [("Colophon Editor", "editor")]

    request, (package, opening, _) = _bundle(
        _xhtml(
            f"<h2>{excluded}</h2><p>Copyright 2024</p><p>Edited by Unrelated Editor</p>"
        )
    )
    with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
        facts = _rear_facts(request, package, opening, archive)
    assert facts == ()


def test_rear_pool_admits_nested_explicit_cues_but_scans_isbns_only_on_leaves() -> None:
    tail = _xhtml(
        '<p>All rights reserved</p><h1 epub:type="fulltitle">Nested <span>Title</span></h1>'
        '<p epub:type="creator">Ada <span>Example</span></p>'
        '<p epub:type="contributor">Bea <span>Contributor</span></p>'
        "<p>by <span>Cara Example</span></p><section><span>Plain container</span></section>"
        "<p><span>ISBN 9780306406157</span></p>"
    )
    request, (package, opening, _) = _bundle(tail)
    with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
        facts = _rear_facts(request, package, opening, archive)
    assert [(fact.field.value, fact.canonical_value) for fact in facts] == [
        ("title", "Nested Title"),
        ("creator", "Ada Example"),
        ("contributor", "Bea Contributor"),
        ("isbn", "9780306406157"),
    ]
    assert sum(fact.field.value == "isbn" for fact in facts) == 1


def test_rear_pool_rejects_isbn_shaped_digits_owned_by_a_uri() -> None:
    request, (package, opening, _) = _bundle(
        _xhtml(
            "<p>All rights reserved</p><p>"
            "x:9780306406157, "
            "https://example.invalid/item;id=9780306406157"
            "</p>"
        )
    )
    with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
        facts = _rear_facts(request, package, opening, archive)
    assert [fact for fact in facts if fact.field.value == "isbn"] == []


def test_rear_pool_preserves_colon_delimited_media_label_isbns() -> None:
    request, (package, opening, _) = _bundle(
        _xhtml("<p>All rights reserved</p><p>Paperback: 9780306406157</p>")
    )
    with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
        facts = _rear_facts(request, package, opening, archive)
    assert [fact.canonical_value for fact in facts if fact.field.value == "isbn"] == [
        "9780306406157"
    ]


def test_rear_pool_does_not_replace_a_tail_slot_without_marker() -> None:
    request, (package, opening, _) = _bundle(
        _xhtml('<h1 epub:type="fulltitle">Unmarked Tail</h1><p>ISBN 9780306406157</p>')
    )
    with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
        assert _rear_facts(request, package, opening, archive) == ()


def test_invalid_final_spine_slot_cannot_displace_a_valid_rear_tail_slot() -> None:
    items = (
        (("slot0", "slot0.xhtml", "application/xhtml+xml", ""),)
        + tuple(
            (f"slot{index}", f"slot{index}.png", "image/png", "")
            for index in range(1, 8)
        )
        + (("slot8", "slot8.xhtml", "application/xhtml+xml", ""),)
    )
    package = _opening_package(
        items=items, spine=tuple(identity for identity, *_ in items)
    ).replace(b'<itemref idref="slot8"/>', b'<itemref idref="slot8" linear="MAYBE"/>')
    value = _opening_epub(
        package=package,
        entries=(
            (
                "EPUB/slot0.xhtml",
                _xhtml(
                    '<p>All rights reserved</p><h1 epub:type="fulltitle">Retained Tail</h1>'
                ),
                8,
            ),
            *(
                (f"EPUB/slot{index}.png", b"synthetic-image", 0)
                for index in range(1, 8)
            ),
            ("EPUB/slot8.xhtml", _xhtml("<p>ordinary prose</p>"), 8),
        ),
    )
    request = _request(value)
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
    package_report = EpubPackageExtractor().extract(package_request)
    assert not next(
        item
        for item in package_report.declarations
        if item.kind is EpubPackageDeclarationKind.SPINE_SLOT
        and item.identifier == "slot8"
    ).linear
    opening = EpubOpeningReport(
        EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE,
        EpubOpeningLimits(),
        package_report.source_fingerprint,
    )
    with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
        facts = _rear_facts(request, package_report, opening, archive)
    assert [(fact.field.value, fact.canonical_value) for fact in facts] == [
        ("title", "Retained Tail")
    ]


@pytest.mark.parametrize(
    "unsafe",
    ("/Users/private/title", "Authorization: Bearer abcdefgh"),
)
def test_rear_pool_suppresses_unsafe_cues_without_losing_safe_facts(
    unsafe: str,
) -> None:
    request, (package, opening, _) = _bundle(
        _xhtml(
            f'<p>All rights reserved</p><h1 epub:type="fulltitle">{unsafe}</h1><p>by Ada Example</p>'
        )
    )
    with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
        facts = _rear_facts(request, package, opening, archive)
    assert [(fact.field.value, fact.canonical_value) for fact in facts] == [
        ("creator", "Ada Example")
    ]


def test_rear_pool_caps_combined_navigation_opening_and_rear_resources_at_nine() -> (
    None
):
    items = (
        ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
    ) + tuple(
        (f"slot{index}", f"slot{index}.xhtml", "application/xhtml+xml", "")
        for index in range(9)
    )
    package = _opening_package(
        items=items, spine=tuple(f"slot{index}" for index in range(9))
    )
    value = _opening_epub(
        package=package,
        entries=(("EPUB/nav.xhtml", _xhtml('<nav epub:type="toc"></nav>'), 8),)
        + tuple(
            (
                f"EPUB/slot{index}.xhtml",
                _xhtml(
                    f'<p>All rights reserved</p><h1 epub:type="fulltitle">Slot {index}</h1>'
                ),
                8,
            )
            for index in range(9)
        ),
    )
    request = _request(value)
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
    package_report, opening_report, _ = child._inspect_bundle(
        request, package_request, EpubOpeningLimits()
    )
    assert sum(resource.opened for resource in opening_report.resources) == 6
    with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
        facts = _rear_facts(request, package_report, opening_report, archive)
    assert [(fact.field.value, fact.canonical_value) for fact in facts] == [
        ("title", "Slot 8"),
        ("title", "Slot 7"),
        ("title", "Slot 6"),
    ]
    assert (
        sum(resource.opened for resource in opening_report.resources) + len(facts) == 9
    )


def test_rear_pool_counts_duplicate_navigation_open_operations_against_budget() -> None:
    items = (
        ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
    ) + tuple(
        (f"slot{index}", f"slot{index}.xhtml", "application/xhtml+xml", "")
        for index in range(9)
    )
    package = _opening_package(
        items=items, spine=("nav",) + tuple(f"slot{index}" for index in range(9))
    )
    value = _opening_epub(
        package=package,
        entries=(("EPUB/nav.xhtml", _xhtml('<nav epub:type="toc"></nav>'), 8),)
        + tuple(
            (
                f"EPUB/slot{index}.xhtml",
                _xhtml(
                    f'<p>All rights reserved</p><h1 epub:type="fulltitle">Slot {index}</h1>'
                ),
                8,
            )
            for index in range(9)
        ),
    )
    request = _request(value)
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
    package_report, opening_report, _ = child._inspect_bundle(
        request, package_request, EpubOpeningLimits()
    )
    assert opening_report.usage.opened_resources == 6
    assert (
        len(
            {
                resource.resource_identity
                for resource in opening_report.resources
                if resource.opened
            }
        )
        == 5
    )
    with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
        facts = _rear_facts(request, package_report, opening_report, archive)
    assert [(fact.field.value, fact.canonical_value) for fact in facts] == [
        ("title", "Slot 8"),
        ("title", "Slot 7"),
        ("title", "Slot 6"),
    ]
    assert opening_report.usage.opened_resources + len(facts) <= 9


def test_rear_pool_charges_crc_failed_opening_attempts_before_rear_reads() -> None:
    items = (
        (("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),)
        + tuple(
            (f"opening{index}", f"opening{index}.xhtml", "application/xhtml+xml", "")
            for index in range(5)
        )
        + tuple(
            (f"rear{index}", f"rear{index}.xhtml", "application/xhtml+xml", "")
            for index in range(4)
        )
    )
    package = _opening_package(
        items=items,
        spine=tuple(identity for identity, *_ in items if identity != "nav"),
    )
    value = _opening_epub(
        package=package,
        entries=(("EPUB/nav.xhtml", _xhtml('<nav epub:type="toc"></nav>'), 0),)
        + tuple(
            (
                f"EPUB/opening{index}.xhtml",
                _xhtml('<h1 epub:type="fulltitle">Opening</h1>'),
                0,
            )
            for index in range(5)
        )
        + tuple(
            (
                f"EPUB/rear{index}.xhtml",
                _xhtml(
                    f'<p>All rights reserved</p><h1 epub:type="fulltitle">Rear {index}</h1>'
                ),
                0,
            )
            for index in range(4)
        ),
    )
    value = _crc_mismatch(
        value, tuple(f"EPUB/opening{index}.xhtml" for index in range(5))
    )
    request = _request(value)
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
    package_report, opening_report, _ = child._inspect_bundle(
        request, package_request, EpubOpeningLimits()
    )
    assert opening_report.usage.opened_resources == 6
    assert all(resource.opened for resource in opening_report.resources)
    with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
        facts = _rear_facts(request, package_report, opening_report, archive)
    assert [(fact.field.value, fact.canonical_value) for fact in facts] == [
        ("title", "Rear 3"),
        ("title", "Rear 2"),
        ("title", "Rear 1"),
    ]
    assert opening_report.usage.opened_resources + len(facts) <= 9


def test_navigation_read_failure_is_an_opened_operation() -> None:
    package = _opening_package(
        items=(
            ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
            ("safe", "safe.xhtml", "application/xhtml+xml", ""),
        ),
        spine=("safe",),
    )
    value = _opening_epub(
        package=package,
        entries=(
            ("EPUB/nav.xhtml", _xhtml('<nav epub:type="toc"></nav>'), 0),
            ("EPUB/safe.xhtml", _xhtml('<h1 epub:type="fulltitle">Safe Title</h1>'), 0),
        ),
    )
    request = _request(value)
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
    original_read = child.opening_parser._read_member

    def fail_navigation_read(archive, name, maximum, active_request):
        if name == "EPUB/nav.xhtml":
            raise ValueError("synthetic navigation read failure")
        return original_read(archive, name, maximum, active_request)

    with patch.object(
        child.opening_parser, "_read_member", side_effect=fail_navigation_read
    ):
        with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
            package_report = EpubPackageExtractor().extract(package_request, archive)
            opening_report = child.EpubOpeningExtractor().extract(
                request, EpubOpeningLimits(), package_report, archive
            )
    assert opening_report.usage.opened_resources == 2
    assert opening_report.resources[0].opened
    assert opening_report.resources[0].selection_path == ("epub3_navigation",)


def test_rear_pool_preserves_cancellation_terminal() -> None:
    value = _opening_epub(
        package=_opening_package(
            items=(("tail", "tail.xhtml", "application/xhtml+xml", ""),),
            spine=("tail",),
        ),
        entries=(("EPUB/tail.xhtml", _xhtml("<p>All rights reserved</p>"), 8),),
    )
    initial = _request(value)
    package_request = EpubPackageRequest(
        initial.target,
        initial.file,
        initial.source,
        initial.expected_source,
        EpubPackageCapability.PACKAGE,
        initial.limits,
        initial.deadline,
        initial.monotonic_deadline,
        initial.cancellation,
    )
    package, opening, _ = child._inspect_bundle(
        initial, package_request, EpubOpeningLimits()
    )
    request = _request(value, cancelled=lambda: True)
    with pytest.raises(child.opening_parser._AggregateRejected) as raised:
        with zipfile.ZipFile(child._SourceIO(initial), "r") as archive:
            _, selection = child.EpubOpeningExtractor().extract_with_selection(
                initial, opening.limits, package, archive
            )
            assert selection is not None
            child._rear_facts(request, package, opening, archive, selection)
    assert raised.value.outcome == EpubInspectionOutcome.CANCELLATION


def test_rear_resource_limit_preserves_opening_evidence_in_the_isolated_child() -> None:
    rear_text = "".join(f"{ordinal:06x}" for ordinal in range(43_691))[:262_145]
    package = _opening_package(
        items=(
            ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
            ("opening", "opening.xhtml", "application/xhtml+xml", ""),
            ("rear", "rear.xhtml", "application/xhtml+xml", ""),
        ),
        spine=("opening", "rear"),
    )
    value = _opening_epub(
        package=package,
        entries=(
            (
                "EPUB/nav.xhtml",
                _xhtml(
                    '<nav epub:type="landmarks"><ol><li><a epub:type="titlepage" href="opening.xhtml">Opening</a></li></ol></nav>'
                ),
                8,
            ),
            (
                "EPUB/opening.xhtml",
                _xhtml(
                    '<h1 epub:type="fulltitle">Synthetic Opening Title</h1><main epub:type="bodymatter">Synthetic body boundary</main>'
                ),
                8,
            ),
            (
                "EPUB/rear.xhtml",
                _xhtml("<p>All rights reserved</p><p>" + rear_text + "</p>"),
                8,
            ),
        ),
    )
    request = _request(value)
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
    package_report, opening_report, rear = child._inspect_bundle(
        request, package_request, EpubOpeningLimits()
    )
    assert rear == ()
    assert "rear_resource_limit" in opening_report.warnings
    report = _inspect_in_child(value)
    assert report.outcome == EpubInspectionOutcome.SUCCESS
    assert report.rear_resource_limit
    assert "Fixture Registry Title" in {fact.canonical_value for fact in report.facts}
    assert "Synthetic Opening Title" in {fact.canonical_value for fact in report.facts}
    assert not any(fact.rear_provenance is not None for fact in report.facts)


def test_rear_resource_limit_preserves_facts_admitted_before_exhaustion() -> None:
    items = tuple(
        (
            f"slot{index}",
            f"slot{index}.xhtml",
            "application/xhtml+xml",
            "",
        )
        for index in range(9)
    )
    oversized_rear = "".join(f"{ordinal:06x}" for ordinal in range(174_764))[:1_048_577]
    value = _opening_epub(
        package=_opening_package(
            items=items,
            spine=tuple(identity for identity, *_rest in items),
        ),
        entries=tuple(
            (
                f"EPUB/slot{index}.xhtml",
                (
                    _xhtml(
                        "<p>All rights reserved</p>"
                        "<p>Translated by Retained Translator</p>"
                    )
                    if index == 8
                    else _xhtml("<p>" + oversized_rear + "</p>")
                    if index == 7
                    else _xhtml("<p>ordinary synthetic prose</p>")
                ),
                8,
            )
            for index in range(9)
        ),
    )
    request = _request(value)
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
    package_report = EpubPackageExtractor().extract(package_request)
    assert package_report.outcome == EpubInspectionOutcome.SUCCESS
    opening_report = EpubOpeningReport(
        EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE,
        EpubOpeningLimits(),
        package_report.source_fingerprint,
    )
    with pytest.raises(child.opening_parser._AggregateRejected) as raised:
        with zipfile.ZipFile(child._SourceIO(request), "r") as archive:
            _rear_facts(request, package_report, opening_report, archive)
    rear = raised.value.partial_facts
    assert raised.value.outcome is EpubInspectionOutcome.RESOURCE_LIMIT
    assert [
        (fact.canonical_value, fact.role)
        for fact in rear
        if fact.field is EpubInspectionField.CONTRIBUTOR
    ] == [("Retained Translator", "translator")]


def test_whole_opening_resource_limit_remains_terminal_in_the_isolated_child() -> None:
    report = _inspect_in_child(opening_resource_limit())
    assert (report.outcome, report.facts) == (EpubInspectionOutcome.RESOURCE_LIMIT, ())


def test_double_opening_and_rear_resource_limits_remain_terminal_in_the_isolated_child() -> (
    None
):
    package = _opening_package(
        items=(("opening", "opening.xhtml", "application/xhtml+xml", ""),),
        spine=("opening",),
    )
    value = _opening_epub(
        package=package,
        entries=(
            (
                "EPUB/opening.xhtml",
                _xhtml("<p>" + "x" * 600_000 + "</p>"),
                zipfile.ZIP_STORED,
            ),
        ),
    )
    report = _inspect_in_child(value)
    assert (report.outcome, report.facts) == (EpubInspectionOutcome.RESOURCE_LIMIT, ())


def test_child_protocol_preserves_exact_opening_terminal_outcomes() -> None:
    malformed = epub_bytes(package=EPUB3_PACKAGE.replace(b"</metadata>", b"<"))
    cancelled = epub_bytes()
    for value, cancelled_check, expected in (
        (malformed, lambda: False, EpubInspectionOutcome.MALFORMED),
        (cancelled, lambda: True, EpubInspectionOutcome.CANCELLATION),
    ):
        request = _request(value, cancelled=cancelled_check)
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
        package, opening, _ = child._inspect_bundle(
            request, package_request, EpubOpeningLimits()
        )
        assert opening.outcome == expected
        assert child._inspection(package, opening).outcome == expected
    for outcome in EpubInspectionOutcome:
        if outcome is EpubInspectionOutcome.SUCCESS:
            continue
        assert (
            child.EpubInspection.from_opening_report(
                EpubOpeningReport(outcome, EpubOpeningLimits())
            ).outcome
            == outcome
        )


def test_child_projects_explicit_creator_and_contributor_as_distinct_local_fields() -> (
    None
):
    package = _opening_package(
        items=(("opening", "opening.xhtml", "application/xhtml+xml", ""),),
        spine=("opening",),
    )
    value = _opening_epub(
        package=package,
        entries=(
            (
                "EPUB/opening.xhtml",
                _xhtml(
                    '<section epub:type="contributors"><p epub:type="creator">Ada Creator</p><p epub:type="contributor">Bea Contributor</p></section>'
                ),
                8,
            ),
        ),
    )
    request = _request(value)
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
    package_report, opening_report, rear = child._inspect_bundle(
        request, package_request, EpubOpeningLimits()
    )
    inspection = child._inspection(package_report, opening_report, rear)
    assert [
        (fact.field.value, fact.canonical_value)
        for fact in inspection.facts
        if fact.field.value in {"creator", "contributor"}
    ] == [("creator", "Ada Creator"), ("contributor", "Bea Contributor")]
    claims = tuple(
        LocalClaim.create(
            kind=ClaimKind(fact.field.value),
            observed=fact.observed_value,
            canonical=fact.canonical_value,
            excerpt=fact.supporting_context,
            applicability=fact.media,
            checksum=fact.checksum,
            applicability_basis=fact.classification_basis,
            basis=fact.basis,
            state=FieldState(fact.state),
            role=fact.role,
        )
        for fact in inspection.facts
        if fact.field.value in {"creator", "contributor"}
    )
    report = MetadataReport.create(
        target=TargetFacts(
            "primary", "ebooks", "item-1", default_target_fields("Current title")
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=claims,
        local_reasons=("opening_matter",),
        providers=(),
    )
    assert {
        field.kind: field.state
        for field in report.field_results
        if field.kind in {ClaimKind.CREATOR, ClaimKind.CONTRIBUTOR}
    } == {ClaimKind.CREATOR: FieldState.VALID, ClaimKind.CONTRIBUTOR: FieldState.VALID}


def test_child_preserves_explicit_publisher_and_imprint_as_distinct_report_fields() -> (
    None
):
    package = _opening_package(
        items=(("opening", "opening.xhtml", "application/xhtml+xml", ""),),
        spine=("opening",),
    )
    value = _opening_epub(
        package=package,
        entries=(
            (
                "EPUB/opening.xhtml",
                _xhtml(
                    '<section epub:type="copyright-page">'
                    '<p epub:type="publisher">Example House</p>'
                    '<p epub:type="imprint">Example Imprint</p>'
                    "</section>"
                ),
                8,
            ),
        ),
    )
    inspection = _inspect_in_child(value)
    edition_facts = tuple(
        fact
        for fact in inspection.facts
        if fact.basis == "opening_matter"
        and fact.field
        in {
            EpubInspectionField.PUBLISHER,
            EpubInspectionField.IMPRINT,
        }
    )
    assert [(fact.field, fact.canonical_value) for fact in edition_facts] == [
        (EpubInspectionField.PUBLISHER, "Example House"),
        (EpubInspectionField.IMPRINT, "Example Imprint"),
    ]
    assert EpubInspection.from_json(inspection.to_json()) == inspection

    claims = tuple(
        LocalClaim.create(
            kind=ClaimKind(fact.field.value),
            observed=fact.observed_value,
            canonical=fact.canonical_value,
            excerpt=fact.supporting_context,
            applicability=fact.media,
            checksum=fact.checksum,
            applicability_basis=fact.classification_basis,
            basis=fact.basis,
            scope=claim_scope(ClaimKind(fact.field.value)),
            state=FieldState(fact.state),
            occurrence=occurrence,
        )
        for occurrence, fact in enumerate(edition_facts, 1)
    )
    report = MetadataReport.create(
        target=TargetFacts(
            "primary", "ebooks", "item-1", default_target_fields("Current title")
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=claims,
        local_reasons=("opening_matter",),
        providers=(),
    )
    replayed = MetadataReport.from_json(report.to_json())
    assert [(claim.kind, claim.canonical) for claim in replayed.local_claims] == [
        (ClaimKind.PUBLISHER, "Example House"),
        (ClaimKind.IMPRINT, "Example Imprint"),
    ]
    assert {
        field.kind: field.state
        for field in replayed.field_results
        if field.kind in {ClaimKind.PUBLISHER, ClaimKind.IMPRINT}
    } == {
        ClaimKind.PUBLISHER: FieldState.VALID,
        ClaimKind.IMPRINT: FieldState.VALID,
    }


@pytest.mark.parametrize("body, expected, role", AUTHOR_STATEMENTS)
@pytest.mark.parametrize("recovered", (False, True))
def test_rear_author_statement_preserves_whole_credit(
    body: str, expected: str | None, role: str | None, recovered: bool
) -> None:
    report = _inspect_locally(author_epub(body, recovered=recovered, rear=True))
    authors = [
        fact for fact in report.facts if fact.field is EpubInspectionField.CREATOR
    ]
    assert [(fact.canonical_value, fact.role) for fact in authors] == (
        [] if expected is None else [(expected, role)]
    )


@pytest.mark.parametrize("rear", (False, True))
@pytest.mark.parametrize(
    "role, fragment, duplicate, expected",
    (
        ("copyright-page", "#blocked", False, ("Allowed Example",)),
        ("copyright-page", "", False, ()),
        ("copyright-page", "#missing", False, ("Blocked Example", "Allowed Example")),
        ("copyright-page", "#blocked", True, ("Blocked Example", "Allowed Example")),
        ("bodymatter", "#blocked", False, ()),
        ("preview", "#blocked", False, ("Allowed Example",)),
    ),
)
def test_original_navigation_bounds_authority_in_both_pools(
    rear: bool, role: str, fragment: str, duplicate: bool, expected: tuple[str, ...]
) -> None:
    from tests.support.author_evidence import fragment_author_epub

    report = _inspect_in_child(
        fragment_author_epub(role, fragment, rear=rear, duplicate=duplicate)
    )
    assert (
        tuple(
            fact.canonical_value
            for fact in report.facts
            if fact.field is EpubInspectionField.CREATOR
        )
        == expected
    )


def test_opening_selection_is_shared_without_additional_navigation_reads() -> None:
    from tests.support.author_evidence import fragment_author_epub

    value = fragment_author_epub()
    request = _request(value)
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
    reads = []
    original = child.opening_parser._read_member

    def read(archive, name, maximum, active_request):
        reads.append(name)
        return original(archive, name, maximum, active_request)

    with (
        patch.object(
            child.opening_parser,
            "_candidate_graph",
            wraps=child.opening_parser._candidate_graph,
        ) as graph,
        patch.object(child.opening_parser, "_read_member", side_effect=read),
    ):
        _, opening, rear = child._inspect_bundle(
            request, package_request, EpubOpeningLimits()
        )
    assert graph.call_count == 1
    assert reads.count("EPUB/nav.xhtml") == 1
    assert opening.usage.opened_resources == 6
    assert len([name for name in reads if name.startswith("EPUB/s")]) == 8
    assert tuple(
        fact.canonical_value
        for fact in rear
        if fact.field is EpubInspectionField.CREATOR
    ) == ("Allowed Example",)


def test_package_creator_roles_remain_true_through_isolated_projection() -> None:
    report = _inspect_in_child(
        author_epub(
            "<p>Authors: Ada Example</p>",
            package_creators=(("Ada Example", None), ("Bea Example", "aut")),
        )
    )
    package = [
        fact
        for fact in report.facts
        if fact.field is EpubInspectionField.CREATOR
        and fact.basis == "package_metadata"
    ]
    assert [(fact.canonical_value, fact.role) for fact in package] == [
        ("Ada Example", None),
        ("Bea Example", "author"),
    ]


@pytest.mark.parametrize("rear", (False, True))
@pytest.mark.parametrize("recovered", (False, True))
def test_whole_credit_context_and_role_are_retained_without_authorship(
    rear: bool,
    recovered: bool,
) -> None:
    report = _inspect_in_child(
        author_epub(
            '<p>Author photograph <span epub:type="creator">by Pat Example</span></p>',
            rear=rear,
            recovered=recovered,
        )
    )
    assert not any(fact.field is EpubInspectionField.CREATOR for fact in report.facts)
    credits = [
        fact for fact in report.facts if fact.field is EpubInspectionField.CONTRIBUTOR
    ]
    assert [
        (fact.canonical_value, fact.role, fact.supporting_context) for fact in credits
    ] == [("Pat Example", "photographer", "Author photograph by Pat Example")]
    assert EpubInspection.from_json(report.to_json()) == report


@pytest.mark.parametrize("rear", (False, True))
@pytest.mark.parametrize("recovered", (False, True))
def test_whole_author_context_keeps_inline_name_and_explicit_role(
    rear: bool, recovered: bool
) -> None:
    report = _inspect_in_child(
        author_epub(
            "<p>Authors: <em>Élodie O’Neill-Smith</em></p>",
            rear=rear,
            recovered=recovered,
        )
    )
    creators = [
        fact for fact in report.facts if fact.field is EpubInspectionField.CREATOR
    ]
    assert [
        (fact.canonical_value, fact.role, fact.supporting_context) for fact in creators
    ] == [("Élodie O’Neill-Smith", "author", "Authors: Élodie O’Neill-Smith")]
    assert EpubInspection.from_json(report.to_json()) == report


@pytest.mark.parametrize(
    "body, expected",
    [(body, None) for body in MIXED_AUTHOR_STATEMENTS]
    + list(LEGITIMATE_AUTHOR_STATEMENTS),
)
@pytest.mark.parametrize("recovered", (False, True))
def test_rear_author_mixed_indicia_refuses_authority(body, expected, recovered):
    report = _inspect_locally(author_epub(body, rear=True, recovered=recovered))
    creators = [
        fact for fact in report.facts if fact.field is EpubInspectionField.CREATOR
    ]
    assert [(fact.canonical_value, fact.role) for fact in creators] == (
        [] if expected is None else [(expected, "author")]
    )


@pytest.mark.parametrize("body,context,role,field", TYPED_CREDIT_STATEMENTS)
@pytest.mark.parametrize("recovered", (False, True))
def test_rear_typed_credit_containers_preserve_roles_without_authors(
    body, context, role, field, recovered
):
    report = _inspect_locally(author_epub(body, rear=True, recovered=recovered))
    credits = [
        fact
        for fact in report.facts
        if fact.field is getattr(EpubInspectionField, field)
    ]
    assert [
        (fact.canonical_value, fact.role, fact.supporting_context) for fact in credits
    ] == [("Bea Artist", role, context)]
    assert not [
        fact for fact in report.facts if fact.field is EpubInspectionField.CREATOR
    ]


@pytest.mark.parametrize("language", ("en", "UND", "und"))
def test_isolated_language_acquisition_retains_raw_unknown(language):
    package = EPUB3_PACKAGE.replace(
        b"<dc:language>en-GB</dc:language>",
        f"<dc:language>{language}</dc:language>".encode(),
    )
    report = _inspect_in_child(epub_bytes(package=package))
    assert report.outcome is EpubInspectionOutcome.SUCCESS
    facts = [
        fact for fact in report.facts if fact.field is EpubInspectionField.LANGUAGE
    ]
    assert len(facts) == 1
    assert facts[0].canonical_value == language
    assert facts[0].observed_value == language


@pytest.mark.parametrize("rear", (False, True), ids=("front", "rear"))
def test_recovered_conversion_credit_retains_front_rear_authority(rear: bool) -> None:
    report = _inspect_in_child(
        author_epub("<div>Converted by Bea Artist</div>", rear=rear, recovered=True)
    )
    assert [
        (fact.canonical_value, fact.role, fact.supporting_context)
        for fact in report.facts
        if fact.field is EpubInspectionField.CONTEXTUAL_CREDIT
    ] == ([("Bea Artist", "conversion", "Converted by Bea Artist")] if rear else [])
    assert not any(fact.field is EpubInspectionField.CREATOR for fact in report.facts)
    assert EpubInspection.from_json(report.to_json()) == report


@pytest.mark.parametrize(
    "program, outcome",
    [
        pytest.param(
            "print('not-json')",
            EpubInspectionOutcome.CHILD_FAILURE,
            id="malformed-output",
        ),
        pytest.param(
            "raise SystemExit(2)", EpubInspectionOutcome.CHILD_FAILURE, id="crash"
        ),
        pytest.param(
            "import time; time.sleep(30)", EpubInspectionOutcome.TIMEOUT, id="timeout"
        ),
        pytest.param(
            "import sys; sys.stdout.buffer.write(b'x' * 1048577)",
            EpubInspectionOutcome.CHILD_FAILURE,
            id="oversized-output",
        ),
    ],
)
def test_inspector_process_failures_reap_child_and_close_pipes(
    program: str, outcome: EpubInspectionOutcome
) -> None:
    # Only launch is substituted: the real OS process, streams, selector,
    # parent protocol loop and termination path remain under test.
    launch = subprocess.Popen
    processes: list[subprocess.Popen[bytes]] = []

    def spawn(_command, **kwargs):
        process = launch([sys.executable, "-I", "-B", "-c", program], **kwargs)
        processes.append(process)
        return process

    value = b"synthetic source"
    source = Fingerprint(
        "sha256", SchemaVersion("local-file-v1"), hashlib.sha256(value).hexdigest()
    )
    with tempfile.TemporaryFile() as fixture:
        fixture.write(value)
        fixture.flush()
        with patch(
            "mechanical_librarian.adapters.evidence_extractors.process.subprocess.Popen",
            side_effect=spawn,
        ):
            report = run_epub_inspector_child(
                fixture.fileno(),
                source,
                EpubOpeningLimits(wall_seconds=1),
                logical_identity=source.digest,
            )
    assert report.outcome is outcome
    assert len(processes) == 1
    process = processes[0]
    assert process.poll() is not None
    assert process.stdin is not None and process.stdin.closed
    assert process.stdout is not None and process.stdout.closed
    assert process.stderr is not None and process.stderr.closed
