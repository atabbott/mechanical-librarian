from __future__ import annotations

import hashlib
import struct
import time
import zipfile
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from mechanical_librarian.adapters.evidence_extractors.epub_package import (
    EpubPackageExtractor,
)
from mechanical_librarian.adapters.evidence_extractors.epub_package_model import (
    EpubInspectionOutcome,
    EpubObservationApplicability,
    EpubPackageCancellation,
    EpubPackageCapability,
    EpubPackageCitationSection,
    EpubPackageDeclarationKind,
    EpubPackageField,
    EpubPackageLimits,
    EpubPackageRequest,
    EpubPackageSource,
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
from tests.support.epub_packages import (
    CONTAINER_MULTIPLE,
    CONTAINER_ONE,
    EPUB2_PACKAGE,
    EPUB3_PACKAGE,
    epub_bytes,
    epub_data_descriptor_bytes,
    epub_zip64_bytes,
)


def request(value: bytes, *, cancelled: bool = False) -> EpubPackageRequest:
    return EpubPackageRequest(
        TargetId(
            AdapterId("synthetic"),
            ConnectionId("local"),
            LibraryId("library"),
            ExternalItemId("book"),
        ),
        FileReference("books", "generated/book.epub"),
        EpubPackageSource(
            len(value), lambda offset, size: value[offset : offset + size]
        ),
        Fingerprint(
            "sha256", SchemaVersion("source-v1"), hashlib.sha256(value).hexdigest()
        ),
        EpubPackageCapability.PACKAGE,
        EpubPackageLimits(),
        datetime.now(timezone.utc) + timedelta(seconds=30),
        time.monotonic() + 30.0,
        EpubPackageCancellation("unit-request", lambda: cancelled),
    )


def request_with_limits(value: bytes, limits: EpubPackageLimits) -> EpubPackageRequest:
    base = request(value)
    return EpubPackageRequest(
        base.target,
        base.file,
        base.source,
        base.expected_source,
        base.capability,
        limits,
        base.deadline,
        base.monotonic_deadline,
        base.cancellation,
    )


@pytest.mark.parametrize(
    ("value", "expected_linear", "warned"),
    (
        (None, True, False),
        ("yes", True, False),
        ("no", False, False),
        ("YES", False, True),
        (" no ", False, True),
    ),
)
def test_spine_linearity_is_exact_and_invalid_values_are_non_linear(
    value: str | None,
    expected_linear: bool,
    warned: bool,
) -> None:
    original = b'<itemref idref="chapter"/>'
    itemref = (
        original
        if value is None
        else f'<itemref idref="chapter" linear="{value}"/>'.encode()
    )
    report = EpubPackageExtractor().extract(
        request(epub_bytes(package=EPUB3_PACKAGE.replace(original, itemref)))
    )
    slot = next(
        item
        for item in report.declarations
        if item.kind is EpubPackageDeclarationKind.SPINE_SLOT
    )
    item = next(
        item
        for item in report.declarations
        if item.kind is EpubPackageDeclarationKind.SPINE_ITEM
    )
    assert (slot.linear, item.linear) == (expected_linear, expected_linear)
    assert (
        "invalid_spine_item" in {warning.code for warning in report.warnings}
    ) == warned


def test_epub3_package_is_deterministic_and_cited() -> None:
    value = epub_bytes()
    extractor = EpubPackageExtractor()
    first = extractor.extract(request(value))
    second = extractor.extract(request(value))

    assert first.outcome == EpubInspectionOutcome.SUCCESS
    assert first == second
    assert first.source_fingerprint.digest == hashlib.sha256(value).hexdigest()  # type: ignore[union-attr]  # reason: the test has just asserted `SUCCESS`, and a successful report always carries a source fingerprint; a `None` here would still fail this assertion loudly.
    assert {
        EpubPackageField.TITLE,
        EpubPackageField.CREATOR,
        EpubPackageField.PACKAGE_IDENTIFIER,
        EpubPackageField.COVER,
    }.issubset({item.field for item in first.observations})
    assert (
        sum(
            item.kind is EpubPackageDeclarationKind.NAVIGATION
            for item in first.declarations
        )
        == 1
    )
    assert (
        sum(
            item.kind is EpubPackageDeclarationKind.MANIFEST_ITEM
            for item in first.declarations
        )
        == 3
    )
    assert "unsupported_vendor_metadata" in {item.code for item in first.warnings}


def test_bounded_producer_forced_zip64_local_headers_are_supported() -> None:
    report = EpubPackageExtractor().extract(request(epub_zip64_bytes()))
    assert report.outcome == EpubInspectionOutcome.SUCCESS


def test_data_descriptors_are_exact_and_unsafe_general_flags_are_rejected() -> None:
    described = epub_data_descriptor_bytes()
    valid = EpubPackageExtractor().extract(request(described))
    corrupted = bytearray(described)
    descriptor = corrupted.find(b"PK\x07\x08")
    assert descriptor > 0
    corrupted[descriptor + 4] ^= 0x01

    unsafe_flag = bytearray(epub_bytes())
    central = unsafe_flag.find(b"PK\x01\x02")
    local = unsafe_flag.find(b"PK\x03\x04", 1)
    assert central > 0
    assert local > 0
    struct.pack_into(
        "<H",
        unsafe_flag,
        central + 8,
        struct.unpack_from("<H", unsafe_flag, central + 8)[0] | 0x20,
    )
    struct.pack_into(
        "<H",
        unsafe_flag,
        local + 6,
        struct.unpack_from("<H", unsafe_flag, local + 6)[0] | 0x20,
    )

    assert valid.outcome == EpubInspectionOutcome.SUCCESS
    assert (
        EpubPackageExtractor().extract(request(bytes(corrupted))).outcome
        == EpubInspectionOutcome.INTEGRITY_FAILURE
    )
    assert (
        EpubPackageExtractor().extract(request(bytes(unsafe_flag))).outcome
        == EpubInspectionOutcome.MALFORMED
    )


@pytest.mark.parametrize(
    "signed",
    (False, True),
)
def test_descriptor_signature_ambiguity_and_local_placeholders_are_validated(
    signed: bool,
) -> None:
    value = epub_data_descriptor_bytes(
        descriptor_signature=signed,
        crc_signature_member=True,
    )
    assert (
        EpubPackageExtractor().extract(request(value)).outcome
        == EpubInspectionOutcome.SUCCESS
    )

    described = epub_data_descriptor_bytes()
    first_local = described.find(b"PK\x03\x04")
    second_local = described.find(b"PK\x03\x04", first_local + 4)
    first_central = described.find(b"PK\x01\x02")
    second_central = described.find(b"PK\x01\x02", first_central + 4)
    assert second_local > first_local
    assert second_central > first_central
    for offset in (14, 18, 22):
        corrupted = bytearray(described)
        struct.pack_into("<L", corrupted, second_local + offset, 1)
        assert (
            EpubPackageExtractor().extract(request(bytes(corrupted))).outcome
            == EpubInspectionOutcome.INTEGRITY_FAILURE
        )

    invalid_version = bytearray(described)
    struct.pack_into("<H", invalid_version, second_local + 4, 0)
    struct.pack_into("<H", invalid_version, second_central + 6, 0)
    assert (
        EpubPackageExtractor().extract(request(bytes(invalid_version))).outcome
        == EpubInspectionOutcome.MALFORMED
    )


def test_epub2_preserves_legacy_declarations_without_dereferencing() -> None:
    report = EpubPackageExtractor().extract(request(epub_bytes(version=2)))
    assert report.outcome == EpubInspectionOutcome.SUCCESS
    assert EpubPackageDeclarationKind.NCX in {item.kind for item in report.declarations}
    assert EpubPackageDeclarationKind.GUIDE_REFERENCE in {
        item.kind for item in report.declarations
    }
    assert EpubPackageField.PUBLICATION_DATE in {
        item.field for item in report.observations
    }
    assert EpubPackageField.CREATOR_ROLE in {item.field for item in report.observations}
    assert EpubPackageField.SORT_KEY in {item.field for item in report.observations}
    assert EpubPackageField.IDENTIFIER_SCHEME in {
        item.field for item in report.observations
    }
    primary = next(
        item
        for item in report.observations
        if item.field is EpubPackageField.PACKAGE_IDENTIFIER
    )
    assert primary.raw_value == "0-306-40615-2"
    assert primary.normalized_value == "0306406152"
    assert primary.applicability is EpubObservationApplicability.UNKNOWN

    tours = b"""<tours>
    <tour title="Synthetic route">
      <site title="Synthetic stop" href="chapter.xhtml#start"/>
    </tour>
  </tours>
  """
    toured_package = EPUB2_PACKAGE.replace(b"  <guide>", b"  " + tours + b"<guide>")
    toured = EpubPackageExtractor().extract(request(epub_bytes(package=toured_package)))
    assert toured.outcome == EpubInspectionOutcome.SUCCESS
    assert "deprecated_tours" in {item.code for item in toured.warnings}

    invalid_tours = (
        EPUB2_PACKAGE.replace(b"  <guide>", b"  <tours/>\n  <guide>"),
        toured_package.replace(
            b'href="chapter.xhtml#start"', b'href="missing.xhtml"', 1
        ),
        toured_package.replace(b'title="Synthetic stop" ', b""),
        EPUB3_PACKAGE.replace(b"</package>", tours + b"</package>"),
    )
    for package in invalid_tours:
        invalid_tour_report = EpubPackageExtractor().extract(
            request(epub_bytes(package=package))
        )
        assert invalid_tour_report.outcome == EpubInspectionOutcome.SUCCESS
        assert "invalid_deprecated_tours" in {
            item.code for item in invalid_tour_report.warnings
        }


def test_epub2_deprecated_metadata_wrappers_preserve_evidence_and_bounds() -> None:
    wrapped = EPUB2_PACKAGE.replace(
        b'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/" '
        b'xmlns:opf="http://www.idpf.org/2007/opf">',
        b'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/" '
        b'xmlns:opf="http://www.idpf.org/2007/opf"><dc-metadata>',
    ).replace(
        b'    <meta name="cover" content="cover"/>',
        b"  </dc-metadata><x-metadata>"
        b'<meta name="cover" content="cover"/></x-metadata>',
    )
    value = epub_bytes(version=2, package=wrapped)
    first = EpubPackageExtractor().extract(request(value))
    second = EpubPackageExtractor().extract(request(value))

    assert first.outcome == EpubInspectionOutcome.SUCCESS
    assert first == second
    assert first.usage.metadata_nodes == 6
    assert {
        EpubPackageField.TITLE,
        EpubPackageField.CREATOR,
        EpubPackageField.LANGUAGE,
        EpubPackageField.PACKAGE_IDENTIFIER,
        EpubPackageField.COVER,
    } <= {item.field for item in first.observations}
    assert (
        EpubPackageExtractor()
        .extract(request_with_limits(value, EpubPackageLimits(metadata_nodes=6)))
        .outcome
        == EpubInspectionOutcome.SUCCESS
    )
    assert (
        EpubPackageExtractor()
        .extract(request_with_limits(value, EpubPackageLimits(metadata_nodes=5)))
        .outcome
        == EpubInspectionOutcome.RESOURCE_LIMIT
    )


def test_epub2_ncx_reference_is_required_and_rejects_fallback_information() -> None:
    ncx_item = b'<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
    invalid_packages = (
        EPUB2_PACKAGE.replace(b'<spine toc="ncx">', b"<spine>"),
        EPUB2_PACKAGE.replace(b'<spine toc="ncx">', b'<spine toc="">'),
        *(
            EPUB2_PACKAGE.replace(
                ncx_item,
                ncx_item[:-2] + b" " + attribute + b'="chapter"/>',
            )
            for attribute in (
                b"fallback",
                b"fallback-style",
                b"required-namespace",
            )
        ),
    )
    for package in invalid_packages:
        report = EpubPackageExtractor().extract(
            request(epub_bytes(version=2, package=package))
        )
        assert report.outcome == EpubInspectionOutcome.SUCCESS
        assert "invalid_ncx_declaration" in {item.code for item in report.warnings}
        assert not any(
            item.kind is EpubPackageDeclarationKind.NCX for item in report.declarations
        )

    epub3 = EpubPackageExtractor().extract(request(epub_bytes()))
    assert "invalid_ncx_declaration" not in {item.code for item in epub3.warnings}

    epub3_with_fallback = EPUB3_PACKAGE.replace(
        b'<item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>',
        b'<item id="ncx" href="toc.ncx" '
        b'media-type="application/x-dtbncx+xml" fallback="chapter"/>'
        b'<item id="chapter" href="chapter.xhtml" '
        b'media-type="application/xhtml+xml"/>',
    ).replace(b"<spine>", b'<spine toc="ncx">')
    fallback_report = EpubPackageExtractor().extract(
        request(epub_bytes(package=epub3_with_fallback))
    )
    assert "invalid_ncx_declaration" not in {
        item.code for item in fallback_report.warnings
    }
    assert EpubPackageDeclarationKind.NCX in {
        item.kind for item in fallback_report.declarations
    }


def test_remote_manifest_resource_is_hashed_and_never_fetched_or_disclosed() -> None:
    package = EPUB3_PACKAGE.replace(
        b"</manifest>",
        b'<item id="remote" href="https://catalogue.example/private.xml" media-type="application/xml"/></manifest>',
    )
    report = EpubPackageExtractor().extract(request(epub_bytes(package=package)))
    assert report.outcome == EpubInspectionOutcome.SUCCESS
    assert (
        sum(
            item.kind is EpubPackageDeclarationKind.MANIFEST_ITEM
            for item in report.declarations
        )
        == 4
    )


def test_namespaces_spine_cardinality_percent_paths_and_global_ids_are_strict() -> None:
    foreign_container = CONTAINER_ONE.replace(
        b"<rootfiles>",
        b'<evil:rootfiles xmlns:evil="urn:evil">',
    ).replace(b"</rootfiles>", b"</evil:rootfiles>")
    foreign_package = EPUB3_PACKAGE.replace(
        b'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">',
        b'<evil:metadata xmlns:evil="urn:evil" xmlns:dc="http://purl.org/dc/elements/1.1/">',
    ).replace(b"</metadata>", b"</evil:metadata>")
    empty_spine = EPUB3_PACKAGE.replace(
        b'<spine><itemref idref="chapter"/></spine>', b"<spine/>"
    )
    encoded_path = EPUB3_PACKAGE.replace(b"chapter.xhtml", b"chapter%20one.xhtml")
    duplicate_id = EPUB3_PACKAGE.replace(b'<item id="nav"', b'<item id="title"', 1)
    outcomes = (
        EpubPackageExtractor()
        .extract(request(epub_bytes(container=foreign_container)))
        .outcome,
        EpubPackageExtractor()
        .extract(request(epub_bytes(package=foreign_package)))
        .outcome,
        EpubPackageExtractor()
        .extract(request(epub_bytes(package=empty_spine)))
        .outcome,
        EpubPackageExtractor()
        .extract(
            request(
                epub_bytes(
                    package=encoded_path,
                    extra_entries=(
                        ("EPUB/chapter one.xhtml", b"opaque", zipfile.ZIP_STORED),
                    ),
                )
            )
        )
        .outcome,
        EpubPackageExtractor()
        .extract(request(epub_bytes(package=duplicate_id)))
        .outcome,
    )
    assert outcomes == (
        EpubInspectionOutcome.MALFORMED,
        EpubInspectionOutcome.MALFORMED,
        EpubInspectionOutcome.MALFORMED,
        EpubInspectionOutcome.SUCCESS,
        EpubInspectionOutcome.SUCCESS,
    )
    duplicate_report = EpubPackageExtractor().extract(
        request(epub_bytes(package=duplicate_id))
    )
    assert "ambiguous_package_id" in {item.code for item in duplicate_report.warnings}


def test_invalid_and_ambiguous_ids_are_omitted_from_semantic_lookups() -> None:
    duplicate_primary = EPUB3_PACKAGE.replace(
        b'<dc:identifier id="pub-id">9780306406157</dc:identifier>',
        b'<dc:identifier id="pub-id">9780306406157</dc:identifier>'
        b'<dc:identifier id="pub-id">9781861972712</dc:identifier>',
    )
    duplicate_primary_report = EpubPackageExtractor().extract(
        request(epub_bytes(package=duplicate_primary))
    )
    assert duplicate_primary_report.outcome == EpubInspectionOutcome.SUCCESS
    assert "ambiguous_package_id" in {
        item.code for item in duplicate_primary_report.warnings
    }
    assert EpubPackageField.PACKAGE_IDENTIFIER not in {
        item.field for item in duplicate_primary_report.observations
    }

    invalid_primary = EPUB3_PACKAGE.replace(b'id="pub-id"', b'id="bad id"', 1)
    invalid_primary_report = EpubPackageExtractor().extract(
        request(epub_bytes(package=invalid_primary))
    )
    assert "invalid_package_id" in {
        item.code for item in invalid_primary_report.warnings
    }
    assert EpubPackageField.PACKAGE_IDENTIFIER not in {
        item.field for item in invalid_primary_report.observations
    }

    duplicate_manifest_id = EPUB3_PACKAGE.replace(
        b'<item id="nav"', b'<item id="chapter"', 1
    )
    duplicate_manifest_report = EpubPackageExtractor().extract(
        request(epub_bytes(package=duplicate_manifest_id))
    )
    assert duplicate_manifest_report.outcome == EpubInspectionOutcome.SUCCESS
    assert not any(
        item.kind is EpubPackageDeclarationKind.SPINE_ITEM
        and item.identifier == "chapter"
        for item in duplicate_manifest_report.declarations
    )
    assert {"ambiguous_package_id", "invalid_manifest_item", "invalid_spine_item"} <= {
        item.code for item in duplicate_manifest_report.warnings
    }


def test_duplicate_cover_resource_keeps_cover_item_provenance() -> None:
    package = EPUB3_PACKAGE.replace(b'href="cover.jpg"', b'href="chapter.xhtml"')
    report = EpubPackageExtractor().extract(request(epub_bytes(package=package)))
    cover_observation = next(
        item for item in report.observations if item.field is EpubPackageField.COVER
    )
    cover_declaration = next(
        item
        for item in report.declarations
        if item.kind is EpubPackageDeclarationKind.COVER
    )
    assert report.outcome == EpubInspectionOutcome.SUCCESS
    assert "duplicate_manifest_resource" in {item.code for item in report.warnings}
    assert (
        cover_observation.citation.source_ordinal
        == cover_declaration.citation.source_ordinal
    )


def test_missing_guide_target_is_warned_and_omitted() -> None:
    reference = b'<reference type="text" title="Start" href="chapter.xhtml#start"/>'
    invalid_references = (
        reference.replace(b'href="chapter.xhtml#start"', b'href="missing.xhtml#start"'),
        b'<reference type="text" title="Start"/>',
        b'<landmark type="text" title="Start" href="chapter.xhtml#start"/>',
        b'<reference type="text" title="Start" href="chapter.xhtml#start">'
        b"<landmark/>"
        b"</reference>",
        b'<reference type="text" title="Start" '
        b'href="chapter.xhtml#start">unexpected</reference>',
    )
    for invalid_reference in invalid_references:
        package = EPUB2_PACKAGE.replace(reference, invalid_reference)
        report = EpubPackageExtractor().extract(
            request(epub_bytes(version=2, package=package))
        )
        assert report.outcome == EpubInspectionOutcome.SUCCESS
        assert "invalid_guide_reference" in {item.code for item in report.warnings}
        assert not any(
            item.kind is EpubPackageDeclarationKind.GUIDE_REFERENCE
            for item in report.declarations
        )

    fragment_only = EPUB2_PACKAGE.replace(
        b'href="chapter.xhtml#start"', b'href="#start"'
    )
    fragment_report = EpubPackageExtractor().extract(
        request(epub_bytes(version=2, package=fragment_only))
    )
    assert fragment_report.outcome == EpubInspectionOutcome.SUCCESS
    assert "invalid_guide_reference" in {item.code for item in fragment_report.warnings}


@pytest.mark.parametrize(
    "href",
    (b"a:b", b"chapter.xhtml?edition=1", b"chapter%ZZ.xhtml"),
)
def test_invalid_optional_resource_syntax_is_warned_and_omitted(href: bytes) -> None:
    package = EPUB3_PACKAGE.replace(b'href="chapter.xhtml"', b'href="' + href + b'"', 1)
    report = EpubPackageExtractor().extract(request(epub_bytes(package=package)))
    assert report.outcome == EpubInspectionOutcome.SUCCESS
    assert {"invalid_manifest_item", "invalid_spine_item"} <= {
        item.code for item in report.warnings
    }

    package = EPUB2_PACKAGE.replace(
        b'href="chapter.xhtml#start"', b'href="' + href + b'"', 1
    )
    report = EpubPackageExtractor().extract(
        request(epub_bytes(version=2, package=package))
    )
    assert report.outcome == EpubInspectionOutcome.SUCCESS
    assert "invalid_guide_reference" in {item.code for item in report.warnings}

    tours = b"""<tours>
    <tour title="Synthetic route">
      <site title="Synthetic stop" href="a:b"/>
    </tour>
  </tours>
  """
    toured = EPUB2_PACKAGE.replace(b"  <guide>", b"  " + tours + b"<guide>")
    report = EpubPackageExtractor().extract(
        request(epub_bytes(version=2, package=toured))
    )
    assert report.outcome == EpubInspectionOutcome.SUCCESS
    assert "invalid_deprecated_tours" in {item.code for item in report.warnings}


@pytest.mark.parametrize(
    "package",
    (
        EPUB3_PACKAGE.replace(
            b'<item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>',
            b'<item id="chapter" href="chapter.xhtml" media-type="image/jpeg"/>',
        ),
        EPUB2_PACKAGE.replace(b'idref="chapter"', b'idref="ncx"'),
    ),
)
def test_spine_requires_compatible_content_or_local_fallback(package: bytes) -> None:
    report = EpubPackageExtractor().extract(
        request(
            epub_bytes(
                version=2 if b'idref="ncx"' in package else 3,
                package=package,
            )
        )
    )
    assert report.outcome == EpubInspectionOutcome.SUCCESS
    assert "invalid_spine_item" in {item.code for item in report.warnings}
    assert not any(
        item.kind is EpubPackageDeclarationKind.SPINE_ITEM
        for item in report.declarations
    )

    fallback_package = EPUB3_PACKAGE.replace(
        b'<item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>',
        b'<item id="chapter" href="chapter.xhtml" media-type="application/pdf" fallback="chapter-fallback"/>'
        b'<item id="chapter-fallback" href="fallback.xhtml" media-type="application/xhtml+xml"/>',
    )
    fallback_report = EpubPackageExtractor().extract(
        request(
            epub_bytes(
                package=fallback_package,
                extra_entries=(("EPUB/fallback.xhtml", b"opaque", zipfile.ZIP_STORED),),
            )
        )
    )
    assert fallback_report.outcome == EpubInspectionOutcome.SUCCESS
    spine = next(
        item
        for item in fallback_report.declarations
        if item.kind is EpubPackageDeclarationKind.SPINE_ITEM
    )
    assert spine.identifier == "chapter"


@pytest.mark.parametrize(
    ("version", "base_package"),
    (
        (2, EPUB2_PACKAGE),
        (3, EPUB3_PACKAGE),
    ),
)
def test_repeated_spine_itemrefs_are_cited_warned_and_omitted(
    version: int,
    base_package: bytes,
) -> None:
    expected_ordinals = {2: (15, 16, 17), 3: (19, 20, 21)}
    package = base_package.replace(
        b"</manifest>",
        b'<item id="second" href="second.xhtml" '
        b'media-type="application/xhtml+xml"/></manifest>',
    ).replace(
        b'<itemref idref="chapter"/>',
        b'<itemref idref="chapter"/>'
        b'<itemref idref="chapter"/>'
        b'<itemref idref="second"/>',
    )
    value = epub_bytes(
        version=version,
        package=package,
        extra_entries=(
            (
                "EPUB/second.xhtml",
                b"PRIVATE-SPINE-CONTENT",
                zipfile.ZIP_STORED,
            ),
        ),
    )
    first = EpubPackageExtractor().extract(request(value))
    second = EpubPackageExtractor().extract(request(value))
    spine_items = [
        item
        for item in first.declarations
        if item.kind is EpubPackageDeclarationKind.SPINE_ITEM
    ]
    duplicate_warnings = [
        item for item in first.warnings if item.code == "invalid_spine_item"
    ]
    first_ordinal, duplicate_ordinal, second_ordinal = expected_ordinals[version]

    assert first.outcome == EpubInspectionOutcome.SUCCESS
    assert first == second
    assert [item.identifier for item in spine_items] == ["chapter", "second"]
    assert [item.source_ordinal for item in spine_items] == [
        first_ordinal,
        second_ordinal,
    ]
    assert [item.citation.source_ordinal for item in spine_items] == [
        first_ordinal,
        second_ordinal,
    ]
    assert len(duplicate_warnings) == 1
    assert duplicate_warnings[0].citation is not None
    assert duplicate_warnings[0].citation.source_ordinal == duplicate_ordinal
    assert duplicate_warnings[0].citation.section == EpubPackageCitationSection.SPINE


@pytest.mark.parametrize(
    ("version", "base_package"),
    (
        (2, EPUB2_PACKAGE),
        (3, EPUB3_PACKAGE),
    ),
)
def test_distinct_spine_itemrefs_remain_distinct(
    version: int,
    base_package: bytes,
) -> None:
    package = base_package.replace(
        b"</manifest>",
        b'<item id="second" href="second.xhtml" '
        b'media-type="application/xhtml+xml"/></manifest>',
    ).replace(
        b'<itemref idref="chapter"/>',
        b'<itemref idref="chapter"/><itemref idref="second"/>',
    )
    report = EpubPackageExtractor().extract(
        request(
            epub_bytes(
                version=version,
                package=package,
                extra_entries=(
                    (
                        "EPUB/second.xhtml",
                        b"opaque",
                        zipfile.ZIP_STORED,
                    ),
                ),
            )
        )
    )

    assert report.outcome == EpubInspectionOutcome.SUCCESS
    assert [
        item.identifier
        for item in report.declarations
        if item.kind is EpubPackageDeclarationKind.SPINE_ITEM
    ] == ["chapter", "second"]
    assert "invalid_spine_item" not in {item.code for item in report.warnings}


@pytest.mark.parametrize(
    ("version", "base_package"),
    (
        (2, EPUB2_PACKAGE),
        (3, EPUB3_PACKAGE),
    ),
)
def test_malformed_spine_itemref_does_not_claim_first_valid_occurrence(
    version: int,
    base_package: bytes,
) -> None:
    for malformed_content in (b"unexpected", b"<meta/>"):
        package = base_package.replace(
            b'<itemref idref="chapter"/>',
            b'<itemref idref="chapter">'
            + malformed_content
            + b'</itemref><itemref idref="chapter"/>',
        )
        report = EpubPackageExtractor().extract(
            request(epub_bytes(version=version, package=package))
        )
        spine_items = [
            item
            for item in report.declarations
            if item.kind is EpubPackageDeclarationKind.SPINE_ITEM
        ]
        invalid_warnings = [
            item for item in report.warnings if item.code == "invalid_spine_item"
        ]

        assert report.outcome == EpubInspectionOutcome.SUCCESS
        assert len(spine_items) == 1
        assert spine_items[0].identifier == "chapter"
        assert len(invalid_warnings) == 1
        assert invalid_warnings[0].citation is not None
        assert invalid_warnings[0].citation.section == EpubPackageCitationSection.SPINE
        assert (
            invalid_warnings[0].citation.source_ordinal < spine_items[0].source_ordinal
        )
        assert spine_items[0].source_ordinal == spine_items[0].citation.source_ordinal


@pytest.mark.parametrize(
    "package",
    (
        EPUB3_PACKAGE.replace(b'id="nav"', b'id="ghp_1234567890abcdef"'),
        EPUB3_PACKAGE.replace(
            b'media-type="application/xhtml+xml" properties="nav"',
            b'media-type="application/ghp_1234567890abcdef" properties="nav"',
            1,
        ),
        EPUB3_PACKAGE.replace(
            b'properties="nav"',
            b'properties="nav ghp_1234567890abcdef"',
        ),
    ),
)
def test_manifest_semantics_require_safe_local_compatible_resources(
    package: bytes,
) -> None:
    report = EpubPackageExtractor().extract(request(epub_bytes(package=package)))
    assert report.outcome == EpubInspectionOutcome.SUCCESS
    assert "unsafe_manifest_item" in {item.code for item in report.warnings}

    oversized_media = EPUB3_PACKAGE.replace(
        b"application/xhtml+xml",
        b"application/" + b"a" * 125,
        1,
    )
    oversized_report = EpubPackageExtractor().extract(
        request(epub_bytes(package=oversized_media))
    )
    assert oversized_report.outcome == EpubInspectionOutcome.SUCCESS
    assert "invalid_manifest_item" in {item.code for item in oversized_report.warnings}
    excessive_properties = EPUB3_PACKAGE.replace(
        b'properties="nav"',
        ('properties="' + " ".join(f"p{index}" for index in range(65)) + '"').encode(),
    )
    excessive_report = EpubPackageExtractor().extract(
        request(epub_bytes(package=excessive_properties))
    )
    assert excessive_report.outcome == EpubInspectionOutcome.SUCCESS
    assert "invalid_manifest_item" in {item.code for item in excessive_report.warnings}

    missing_cover = EPUB3_PACKAGE.replace(b'href="cover.jpg"', b'href="missing.jpg"')
    wrong_cover = EPUB3_PACKAGE.replace(
        b'media-type="image/jpeg" properties="cover-image"',
        b'media-type="application/xhtml+xml" properties="cover-image"',
    )
    wrong_navigation = EPUB3_PACKAGE.replace(
        b'media-type="application/xhtml+xml" properties="nav"',
        b'media-type="image/jpeg" properties="nav"',
        1,
    )
    for package, warning, forbidden_kind, forbidden_field in (
        (
            missing_cover,
            "missing_manifest_resource",
            EpubPackageDeclarationKind.COVER,
            EpubPackageField.COVER,
        ),
        (
            wrong_cover,
            "invalid_cover_declaration",
            EpubPackageDeclarationKind.COVER,
            EpubPackageField.COVER,
        ),
        (
            wrong_navigation,
            "invalid_navigation_declaration",
            EpubPackageDeclarationKind.NAVIGATION,
            EpubPackageField.NAVIGATION,
        ),
    ):
        report = EpubPackageExtractor().extract(request(epub_bytes(package=package)))
        assert report.outcome == EpubInspectionOutcome.SUCCESS
        assert warning in {item.code for item in report.warnings}
        assert forbidden_kind not in {item.kind for item in report.declarations}
        assert forbidden_field not in {item.field for item in report.observations}


@pytest.mark.parametrize(
    ("raw", "normalized", "scheme", "valid"),
    (
        (b"0-306-40615-2", "0306406152", "ISBN-10", True),
        (b"0-8044-2957-X", "080442957X", "ISBN-10", True),
        (b"9780306406157", "9780306406157", "ISBN-13", True),
        (b"9791090636071", "9791090636071", "ISBN-13", True),
        (b"urn:isbn:9780306406157", "9780306406157", "ISBN-13", True),
        (b"X000000018", "X000000018", None, False),
        (b"1234567890128", "1234567890128", None, False),
        (b"urn:isbn:9780306406158", "urn:isbn:9780306406158", None, False),
        (b"urn:isbn:not-an-isbn", "urn:isbn:not-an-isbn", None, False),
        (b"ISBN:not-an-isbn", "ISBN:not-an-isbn", None, False),
        (b"ISBN-10:9780306406157", "ISBN-10:9780306406157", None, False),
    ),
)
def test_isbn_families_validate_lexical_position_prefix_checksum_and_urn(
    raw: bytes,
    normalized: str,
    scheme: str | None,
    valid: bool,
) -> None:
    original = b"9780306406157"
    package = EPUB3_PACKAGE.replace(original, raw, 1)
    report = EpubPackageExtractor().extract(request(epub_bytes(package=package)))
    identifier = next(
        item
        for item in report.observations
        if item.field is EpubPackageField.IDENTIFIER
    )
    assert identifier.normalized_value == normalized
    assert identifier.applicability is (
        EpubObservationApplicability.UNKNOWN
        if valid
        else EpubObservationApplicability.INAPPLICABLE
    )
    inferred = {
        item.normalized_value
        for item in report.observations
        if item.field is EpubPackageField.IDENTIFIER_SCHEME
        and item.citation.selector == "inferred-identifier-type"
    }
    assert inferred == (set() if scheme is None else {scheme})
    assert ("invalid_identifier" in {item.code for item in report.warnings}) == (
        not valid
    )

    invalid_declared = EPUB2_PACKAGE.replace(
        b">0-306-40615-2</dc:identifier>",
        b">not-an-isbn</dc:identifier>",
    )
    declared_report = EpubPackageExtractor().extract(
        request(epub_bytes(version=2, package=invalid_declared))
    )
    declared_identifier = next(
        item
        for item in declared_report.observations
        if item.field is EpubPackageField.IDENTIFIER
    )
    assert declared_report.outcome == EpubInspectionOutcome.SUCCESS
    assert (
        declared_identifier.applicability is EpubObservationApplicability.INAPPLICABLE
    )
    assert "invalid_identifier" in {item.code for item in declared_report.warnings}
    assert "ISBN" in {
        item.normalized_value
        for item in declared_report.observations
        if item.field is EpubPackageField.IDENTIFIER_SCHEME
    }


def test_explicit_non_isbn_scheme_does_not_infer_an_isbn() -> None:
    package = EPUB2_PACKAGE.replace(
        b'opf:scheme="ISBN">0-306-40615-2',
        b'opf:scheme="LCCN">9780306406157',
    )
    report = EpubPackageExtractor().extract(
        request(epub_bytes(version=2, package=package))
    )
    identifier = next(
        item
        for item in report.observations
        if item.field is EpubPackageField.IDENTIFIER
    )
    assert identifier.normalized_value == "9780306406157"
    assert identifier.applicability is EpubObservationApplicability.UNKNOWN
    assert "ISBN-13" not in {
        item.normalized_value
        for item in report.observations
        if item.field is EpubPackageField.IDENTIFIER_SCHEME
    }


@pytest.mark.parametrize(
    ("scheme", "raw", "normalized"),
    (
        (b"isbn", b"9780306406157", "9780306406157"),
        (b"DoI", b"doi:10.1234/synthetic.1", "10.1234/synthetic.1"),
        (b"aSiN", b"asin:b012345678", "B012345678"),
        (
            b"UuId",
            b"12345678-1234-4abc-8123-123456789abc",
            "urn:uuid:12345678-1234-4abc-8123-123456789abc",
        ),
    ),
)
def test_declared_identifier_schemes_are_casefolded_and_normalized(
    scheme: bytes,
    raw: bytes,
    normalized: str,
) -> None:
    package = EPUB2_PACKAGE.replace(
        b'opf:scheme="ISBN">0-306-40615-2',
        b'opf:scheme="' + scheme + b'">' + raw,
    )
    report = EpubPackageExtractor().extract(
        request(epub_bytes(version=2, package=package))
    )
    identifier = next(
        item
        for item in report.observations
        if item.field is EpubPackageField.IDENTIFIER
    )
    assert identifier.normalized_value == normalized
    assert scheme.decode() in {
        item.normalized_value
        for item in report.observations
        if item.field is EpubPackageField.IDENTIFIER_SCHEME
    }


def test_repeated_legacy_cover_declarations_preserve_agreement_and_conflict() -> None:
    cover_meta = b'<meta name="cover" content="cover"/>'
    agreeing_package = EPUB2_PACKAGE.replace(cover_meta, cover_meta + cover_meta)
    agreeing = EpubPackageExtractor().extract(
        request(epub_bytes(version=2, package=agreeing_package))
    )
    assert agreeing.outcome == EpubInspectionOutcome.SUCCESS
    assert "contradictory_cover_declaration" not in {
        item.code for item in agreeing.warnings
    }
    assert (
        sum(item.field is EpubPackageField.COVER for item in agreeing.observations) == 1
    )
    agreeing_cover = next(
        item
        for item in agreeing.declarations
        if item.kind is EpubPackageDeclarationKind.COVER
    )
    assert agreeing_cover.citation.expanded_name.endswith("}meta")

    conflicting_package = EPUB2_PACKAGE.replace(
        cover_meta,
        cover_meta + b'<meta name="cover" content="cover2"/>',
    ).replace(
        b"</manifest>",
        b'<item id="cover2" href="cover2.jpg" media-type="image/jpeg"/></manifest>',
    )
    conflicting = EpubPackageExtractor().extract(
        request(
            epub_bytes(
                version=2,
                package=conflicting_package,
                extra_entries=(("EPUB/cover2.jpg", b"opaque", zipfile.ZIP_STORED),),
            )
        )
    )
    assert conflicting.outcome == EpubInspectionOutcome.SUCCESS
    assert "contradictory_cover_declaration" in {
        item.code for item in conflicting.warnings
    }
    assert EpubPackageField.COVER not in {
        item.field for item in conflicting.observations
    }

    current_conflict_package = EPUB3_PACKAGE.replace(
        b"</manifest>",
        b'<item id="cover2" href="cover2.jpg" media-type="image/jpeg" '
        b'properties="cover-image"/>'
        b"</manifest>",
    )
    current_conflict = EpubPackageExtractor().extract(
        request(
            epub_bytes(
                package=current_conflict_package,
                extra_entries=(("EPUB/cover2.jpg", b"opaque", zipfile.ZIP_STORED),),
            )
        )
    )
    assert current_conflict.outcome == EpubInspectionOutcome.SUCCESS
    assert "contradictory_cover_declaration" in {
        item.code for item in current_conflict.warnings
    }
    assert EpubPackageField.COVER not in {
        item.field for item in current_conflict.observations
    }


def test_hostile_path_reference_matrix_is_typed_and_never_resolved_externally() -> None:
    references = {
        "/absolute.xhtml": EpubInspectionOutcome.MALFORMED,
        "../../escape.xhtml": EpubInspectionOutcome.MALFORMED,
        "chapter%2Fxhtml": EpubInspectionOutcome.MALFORMED,
        "chapter\\xhtml": EpubInspectionOutcome.MALFORMED,
        "file:///private/book.xhtml": EpubInspectionOutcome.MALFORMED,
        "https://user@example.invalid/private": EpubInspectionOutcome.MALFORMED,
        "x" * 1_025: EpubInspectionOutcome.RESOURCE_LIMIT,
    }
    for reference, expected in references.items():
        package = EPUB3_PACKAGE.replace(b"chapter.xhtml", reference.encode(), 1)
        assert (
            EpubPackageExtractor().extract(request(epub_bytes(package=package))).outcome
            == expected
        )

    encrypted_zip = bytearray(epub_bytes())
    central = encrypted_zip.find(b"PK\x01\x02")
    struct.pack_into(
        "<H",
        encrypted_zip,
        central + 8,
        struct.unpack_from("<H", encrypted_zip, central + 8)[0] | 1,
    )
    assert (
        EpubPackageExtractor().extract(request(bytes(encrypted_zip))).outcome
        == EpubInspectionOutcome.ENCRYPTED
    )


@pytest.mark.parametrize(
    "declaration",
    (
        b'<!DOCTYPE package [<!ENTITY internal "value">]>',
        b'<!DOCTYPE package [<!ENTITY external SYSTEM "file:///private/value">]>',
        b'<!DOCTYPE package [<!ENTITY network SYSTEM "https://example.invalid/value">]>',
        b'<!DOCTYPE package [<!ENTITY % parameter "value">]>',
        b'<!DOCTYPE package [<!NOTATION image SYSTEM "image/jpeg"><!ENTITY unparsed SYSTEM "x" NDATA image>]>',
    ),
)
def test_entity_and_xinclude_matrix_is_rejected_without_external_access(
    declaration: bytes,
) -> None:
    package = declaration + EPUB3_PACKAGE
    assert (
        EpubPackageExtractor().extract(request(epub_bytes(package=package))).outcome
        == EpubInspectionOutcome.MALFORMED
    )
    xinclude = EPUB3_PACKAGE.replace(
        b"</metadata>",
        b'<xi:include xmlns:xi="http://www.w3.org/2001/XInclude" href="file:///private/value"/></metadata>',
    )
    assert (
        EpubPackageExtractor().extract(request(epub_bytes(package=xinclude))).outcome
        == EpubInspectionOutcome.MALFORMED
    )


def test_package_child_order_and_refinement_targets_are_closed() -> None:
    metadata_start = EPUB3_PACKAGE.index(b"  <metadata")
    metadata_end = EPUB3_PACKAGE.index(b"  </metadata>") + len(b"  </metadata>\n")
    manifest_start = EPUB3_PACKAGE.index(b"  <manifest>")
    manifest_end = EPUB3_PACKAGE.index(b"  </manifest>") + len(b"  </manifest>\n")
    metadata = EPUB3_PACKAGE[metadata_start:metadata_end]
    manifest = EPUB3_PACKAGE[manifest_start:manifest_end]
    reordered = (
        EPUB3_PACKAGE[:metadata_start]
        + manifest
        + metadata
        + EPUB3_PACKAGE[manifest_end:]
    )
    unknown_child = EPUB3_PACKAGE.replace(
        b"  <manifest>", b"  <unknown/>\n  <manifest>", 1
    )
    missing_target = EPUB3_PACKAGE.replace(
        b"</metadata>",
        b'<meta refines="#missing" property="role">aut</meta></metadata>',
    )
    wrong_target = EPUB3_PACKAGE.replace(
        b"</metadata>",
        b'<meta refines="#chapter" property="role">aut</meta></metadata>',
    )
    for package in (reordered, unknown_child):
        assert (
            EpubPackageExtractor().extract(request(epub_bytes(package=package))).outcome
            == EpubInspectionOutcome.MALFORMED
        )
    for package, warning in (
        (missing_target, "unresolved_metadata_refinement"),
        (wrong_target, "inapplicable_metadata_refinement"),
    ):
        report = EpubPackageExtractor().extract(request(epub_bytes(package=package)))
        assert report.outcome == EpubInspectionOutcome.SUCCESS
        assert warning in {item.code for item in report.warnings}
    trailing_cases = (
        (b'<collection role="index"/>', EpubInspectionOutcome.SUCCESS),
        (
            b'<bindings><mediaType media-type="application/x-synthetic" handler="chapter"/></bindings>'
            b'<collection role="index"/>',
            EpubInspectionOutcome.SUCCESS,
        ),
        (
            b'<guide><reference type="text" title="Start" href="chapter.xhtml"/></guide>'
            b'<bindings><mediaType media-type="application/x-synthetic" handler="chapter"/></bindings>'
            b'<collection role="index"/><collection role="preview"/>',
            EpubInspectionOutcome.SUCCESS,
        ),
        (
            b'<collection role="index"/>'
            b'<bindings><mediaType media-type="application/x-synthetic" handler="chapter"/></bindings>',
            EpubInspectionOutcome.MALFORMED,
        ),
        (
            b'<bindings><mediaType media-type="application/x-synthetic" handler="chapter"/></bindings>'
            b'<guide><reference type="text" title="Start" href="chapter.xhtml"/></guide>',
            EpubInspectionOutcome.MALFORMED,
        ),
    )
    for trailing_children, outcome in trailing_cases:
        package = EPUB3_PACKAGE.replace(
            b"</package>", trailing_children + b"</package>"
        )
        assert (
            EpubPackageExtractor().extract(request(epub_bytes(package=package))).outcome
            == outcome
        )
    container_links = CONTAINER_ONE.replace(
        b"</container>",
        b'<links><link href="https://example.invalid/metadata" rel="record"/></links></container>',
    )
    assert (
        EpubPackageExtractor()
        .extract(request(epub_bytes(container=container_links)))
        .outcome
        == EpubInspectionOutcome.SUCCESS
    )


@pytest.mark.parametrize(
    "private",
    (
        "Authorization: Bearer synthetic-token-1234",
        "/home/example/private/book.epub",
        r"C:\\library\\private\\book.epub",
        "file:///srv/private/book.epub",
        "spoof\u202ereversed",
    ),
)
def test_unsafe_metadata_is_one_way_projected_with_a_cited_warning(
    private: str,
) -> None:
    package = EPUB3_PACKAGE.replace(b"A Synthetic Book", private.encode("utf-8"))
    report = EpubPackageExtractor().extract(request(epub_bytes(package=package)))
    assert report.outcome == EpubInspectionOutcome.SUCCESS
    assert "unsafe_metadata_redacted" in {item.code for item in report.warnings}


@pytest.mark.parametrize(
    "namespace",
    (
        "https://user:synthetic-secret@example.invalid/ns",
        "file:///Users/example/private",
        "urn:synthetic:spoof\u202econtrol",
        "urn:synthetic:ordinary",
    ),
)
def test_untrusted_xml_namespaces_are_one_way_projected_in_citations(
    namespace: str,
) -> None:
    foreign = f'<x:quiet xmlns:x="{namespace}" id="bad id"/>'.encode("utf-8")
    package = EPUB3_PACKAGE.replace(b"</metadata>", foreign + b"</metadata>")
    report = EpubPackageExtractor().extract(request(epub_bytes(package=package)))
    warning = next(
        item
        for item in report.warnings
        if item.code == "invalid_package_id" and item.citation is not None
    )
    assert report.outcome == EpubInspectionOutcome.SUCCESS
    assert warning.citation.expanded_name.startswith("{urn:sha256:")  # type: ignore[union-attr]  # reason: the `next(...)` filter above selects only warnings whose citation is not `None`, which mypy does not narrow through.

    for foreign in (
        b'<syntheticPrivateTokenSentinel xmlns="" id="bad id"/>',
        b'<syntheticPrivateTokenSentinel id="bad id"/>',
    ):
        package = EPUB3_PACKAGE.replace(b"</metadata>", foreign + b"</metadata>")
        report = EpubPackageExtractor().extract(request(epub_bytes(package=package)))
        warning = next(
            item
            for item in report.warnings
            if item.code == "invalid_package_id" and item.citation is not None
        )
        assert report.outcome == EpubInspectionOutcome.SUCCESS
        assert warning.citation.expanded_name.startswith("{urn:sha256:")  # type: ignore[union-attr]  # reason: the `next(...)` filter above selects only warnings whose citation is not `None`, which mypy does not narrow through.


def test_metadata_value_projection_preserves_exact_limit_and_hashes_above() -> None:
    maximum = EpubPackageLimits.MAX_RAW_VALUE_CHARACTERS
    for length in (maximum - 1, maximum, maximum + 1):
        value = "x" * length
        package = EPUB3_PACKAGE.replace(b"A Synthetic Book", value.encode("utf-8"))
        report = EpubPackageExtractor().extract(request(epub_bytes(package=package)))
        title = next(
            item for item in report.observations if item.field is EpubPackageField.TITLE
        )
        assert report.outcome == EpubInspectionOutcome.SUCCESS
        if length <= maximum:
            assert title.raw_value == value
            assert "oversized_metadata_redacted" not in {
                item.code for item in report.warnings
            }
        else:
            assert title.raw_value != value
            assert title.raw_value.startswith("oversized-source-text-sha256:")
            assert "oversized_metadata_redacted" in {
                item.code for item in report.warnings
            }


def test_warning_occurrences_are_aggregated_within_report_bound() -> None:
    invalid_nodes = b"".join(
        f'<meta id="bad id {ordinal}"/>'.encode("ascii") for ordinal in range(40)
    )
    package = EPUB3_PACKAGE.replace(b"</metadata>", invalid_nodes + b"</metadata>")
    report = EpubPackageExtractor().extract(request(epub_bytes(package=package)))
    assert report.outcome == EpubInspectionOutcome.SUCCESS
    assert len(report.warnings) <= EpubPackageLimits.MAX_WARNINGS
    assert "warning_occurrences_aggregated" in {item.code for item in report.warnings}
    assert (
        next(
            item for item in report.warnings if item.code == "invalid_package_id"
        ).citation
        is None
    )


def test_normalization_preserves_roles_ambiguity_and_unknown_identifier_applicability() -> (
    None
):
    additions = b"""\n    <dc:identifier>urn:synthetic:alternate</dc:identifier>
    <dc:title>Alternate Synthetic Title</dc:title>
    <dc:contributor id="editor">E. Example</dc:contributor>
    <dc:date xmlns:opf="http://www.idpf.org/2007/opf" opf:event="publication">2025-01-01</dc:date>
    <dc:date xmlns:opf="http://www.idpf.org/2007/opf" opf:event="publication">2026-01-01</dc:date>
    <meta refines="#editor" property="role" scheme="marc:relators">edt</meta>
    <meta property="schema:bookEdition">First synthetic edition</meta>
    <meta property="schema:bookEdition">Second synthetic edition</meta>"""
    package = EPUB3_PACKAGE.replace(b"</metadata>", additions + b"\n  </metadata>")
    report = EpubPackageExtractor().extract(request(epub_bytes(package=package)))
    fields = {observation.field for observation in report.observations}
    warning_codes = {warning.code for warning in report.warnings}
    identifier_applicability = {
        observation.applicability
        for observation in report.observations
        if observation.field
        in {EpubPackageField.IDENTIFIER, EpubPackageField.PACKAGE_IDENTIFIER}
    }
    assert report.outcome == EpubInspectionOutcome.SUCCESS
    assert EpubPackageField.CONTRIBUTOR_ROLE in fields
    assert EpubObservationApplicability.UNKNOWN in identifier_applicability
    assert {
        "ambiguous_title",
        "ambiguous_identifier",
        "ambiguous_edition",
        "contradictory_date",
        "contradictory_series",
        "identifier_applicability_unknown",
    }.issubset(warning_codes)


def test_signed_offset_publication_datetimes_are_bounded_and_event_typed() -> None:
    additions = b"""
    <dc:date xmlns:opf="http://www.idpf.org/2007/opf" opf:event="publication">2026-01-13T00:00:00+00:00</dc:date>
    <dc:date>2026-01-13T12:34:56.789-14:00</dc:date>
    <dc:date xmlns:opf="http://www.idpf.org/2007/opf" opf:event="publication">2026-01-13T23:59:59+14:00</dc:date>
    <dc:date xmlns:opf="http://www.idpf.org/2007/opf" opf:event="publication">2026-01-13T00:00:00Z</dc:date>"""
    package = EPUB3_PACKAGE.replace(b"</metadata>", additions + b"\n  </metadata>")
    report = EpubPackageExtractor().extract(request(epub_bytes(package=package)))
    dates = [
        observation
        for observation in report.observations
        if observation.field is EpubPackageField.PUBLICATION_DATE
    ]

    assert report.outcome == EpubInspectionOutcome.SUCCESS
    assert [observation.raw_value for observation in dates] == [
        "2026-01-13T00:00:00+00:00",
        "2026-01-13T12:34:56.789-14:00",
        "2026-01-13T23:59:59+14:00",
        "2026-01-13T00:00:00Z",
    ]
    assert all(
        observation.normalized_value == observation.raw_value
        and observation.applicability is EpubObservationApplicability.WORK
        for observation in dates
    )
    assert [
        observation.normalized_value
        for observation in report.observations
        if observation.field is EpubPackageField.DATE_EVENT
    ] == ["publication", "publication", "publication"]


@pytest.mark.parametrize(
    ("event", "value", "field"),
    (
        ("publication", "2026-01-13T00:00:00", EpubPackageField.PUBLICATION_DATE),
        ("publication", "2026-01-13T00:00:00+14:01", EpubPackageField.PUBLICATION_DATE),
        ("publication", "2026-01-13T00:00:00-14:01", EpubPackageField.PUBLICATION_DATE),
        ("publication", "2026-01-13T00:00:00+15:00", EpubPackageField.PUBLICATION_DATE),
        ("publication", "2026-02-30T00:00:00+01:00", EpubPackageField.PUBLICATION_DATE),
        ("creation", "2026-01-13T00:00:00+01:00", EpubPackageField.CREATION_DATE),
        (
            "modification",
            "2026-01-13T00:00:00+01:00",
            EpubPackageField.MODIFICATION_DATE,
        ),
        ("copyright", "2026-01-13T00:00:00+01:00", EpubPackageField.COPYRIGHT_DATE),
        (
            "future-event",
            "2026-01-13T00:00:00+01:00",
            EpubPackageField.PUBLICATION_DATE,
        ),
    ),
    # The tier runner reduces a case identity through `[^A-Za-z0-9_]` -> `_`,
    # so the derived ids for the `+14:01` and `-14:01` offsets would collide on
    # one identity and fail the whole cohort. These are stated in the reduced
    # alphabet already, so the identity is what is written here.
    ids=(
        "naive",
        "offset_plus_14_01",
        "offset_minus_14_01",
        "offset_plus_15_00",
        "impossible_day",
        "creation_event",
        "modification_event",
        "copyright_event",
        "unknown_event",
    ),
)
def test_signed_offset_dates_reject_invalid_offsets_and_nonpublication_events(
    event: str,
    value: str,
    field: EpubPackageField,
) -> None:
    addition = (
        '<dc:date xmlns:opf="http://www.idpf.org/2007/opf" '
        f'opf:event="{event}">{value}</dc:date>'
    ).encode()
    package = EPUB3_PACKAGE.replace(b"</metadata>", addition + b"</metadata>")
    report = EpubPackageExtractor().extract(request(epub_bytes(package=package)))
    observation = next(
        item
        for item in report.observations
        if item.field is field and item.raw_value == value
    )

    assert observation.normalized_value is None
    assert observation.applicability is EpubObservationApplicability.INAPPLICABLE
    assert "invalid_date" in {warning.code for warning in report.warnings}
    assert event in {
        item.normalized_value
        for item in report.observations
        if item.field is EpubPackageField.DATE_EVENT
    }


def test_identifier_region_format_and_prefix_normalization_are_allowlisted() -> None:
    additions = b"""
    <dc:identifier>urn:uuid:12345678-1234-4abc-8123-123456789abc</dc:identifier>
    <dc:identifier>10.1234/synthetic.1</dc:identifier>
    <dc:identifier>ASIN:B012345678</dc:identifier>
    <dc:identifier>synthetic-proprietary-id</dc:identifier>
    <dc:identifier>9780306406158</dc:identifier>
    <dc:coverage>GB</dc:coverage>
    <meta property="schema:bookEdition">Second edition</meta>
    <meta property="schema:bookFormat">EBook</meta>
    <meta property="schema:contentLocation">Europe</meta>"""
    package = EPUB3_PACKAGE.replace(b"</metadata>", additions + b"</metadata>")
    report = EpubPackageExtractor().extract(request(epub_bytes(package=package)))
    fields = {item.field for item in report.observations}
    schemes = {
        item.normalized_value
        for item in report.observations
        if item.field is EpubPackageField.IDENTIFIER_SCHEME
    }
    assert report.outcome == EpubInspectionOutcome.SUCCESS
    assert {
        EpubPackageField.EDITION,
        EpubPackageField.FORMAT,
        EpubPackageField.REGION,
    }.issubset(fields)
    assert {"ISBN-13", "UUID", "DOI", "ASIN"}.issubset(schemes)
    assert "invalid_identifier" in {item.code for item in report.warnings}
    assert "identifier_applicability_unknown" in {item.code for item in report.warnings}

    bare = EPUB3_PACKAGE.replace(
        b"</metadata>", b'<meta property="bookEdition">Bare edition</meta></metadata>'
    )
    remapped = EPUB3_PACKAGE.replace(
        b'prefix="schema: http://schema.org/"',
        b'prefix="schema: https://vendor.example/vocab#"',
    ).replace(
        b"</metadata>",
        b'<meta property="schema:bookEdition">Vendor edition</meta></metadata>',
    )
    for value in (bare, remapped):
        result = EpubPackageExtractor().extract(request(epub_bytes(package=value)))
        assert result.outcome == EpubInspectionOutcome.SUCCESS
        assert EpubPackageField.EDITION not in {
            item.field for item in result.observations
        }
        assert "unsupported_vendor_metadata" in {item.code for item in result.warnings}
    implicit = EPUB3_PACKAGE.replace(
        b' prefix="schema: http://schema.org/"', b""
    ).replace(
        b"</metadata>",
        b'<meta property="schema:bookEdition">Reserved edition</meta></metadata>',
    )
    implicit_report = EpubPackageExtractor().extract(
        request(epub_bytes(package=implicit))
    )
    assert implicit_report.outcome == EpubInspectionOutcome.SUCCESS
    assert EpubPackageField.EDITION in {
        item.field for item in implicit_report.observations
    }

    invalid_prefix = EPUB3_PACKAGE.replace(
        b"schema: http://schema.org/", b"schema: not-an-iri"
    )
    assert (
        EpubPackageExtractor()
        .extract(request(epub_bytes(package=invalid_prefix)))
        .outcome
        == EpubInspectionOutcome.MALFORMED
    )


def test_repeated_calibre_values_preserve_occurrences_and_conflicts() -> None:
    package = EPUB3_PACKAGE.replace(
        b'<meta name="calibre:series" content="Lower Priority Series"/>',
        b'<meta name="calibre:series" content="First Series"/>'
        b'<meta name="calibre:series" content="Second Series"/>',
    ).replace(
        b'<meta name="calibre:series_index" content="9"/>',
        b'<meta name="calibre:series_index" content="1"/>'
        b'<meta name="calibre:series_index" content="2"/>',
    )
    report = EpubPackageExtractor().extract(request(epub_bytes(package=package)))
    calibre_values = [
        item.raw_value
        for item in report.observations
        if item.citation.selector == "calibre-fallback"
    ]
    assert report.outcome == EpubInspectionOutcome.SUCCESS
    assert calibre_values == ["First Series", "Second Series", "1", "2"]
    assert "contradictory_series" in {item.code for item in report.warnings}

    unpaired = EPUB3_PACKAGE.replace(
        b'<meta name="calibre:series_index" content="9"/>',
        b'<meta name="calibre:series_index" content="9"/>'
        b'<meta name="calibre:series_index" content="10"/>',
    )
    unpaired_report = EpubPackageExtractor().extract(
        request(epub_bytes(package=unpaired))
    )
    positions = [
        item
        for item in unpaired_report.observations
        if item.field is EpubPackageField.COLLECTION_POSITION
        and item.citation.selector == "calibre-fallback"
    ]
    assert unpaired_report.outcome == EpubInspectionOutcome.SUCCESS
    assert len(positions) == 1
    assert "incomplete_calibre_series" in {
        item.code for item in unpaired_report.warnings
    }


def test_standard_metadata_refinement_and_cover_precedence_matrix() -> None:
    additions = b"""
    <dc:title id="subtitle">Synthetic Subtitle</dc:title>
    <dc:publisher>Example Publisher</dc:publisher>
    <dc:subject>Example Subject</dc:subject>
    <dc:description>Example Description</dc:description>
    <dc:rights>Example Rights</dc:rights>
    <dc:source>Example Source</dc:source>
    <dc:relation>Example Relation</dc:relation>
    <dc:type>Text</dc:type>
    <meta refines="#subtitle" property="title-type">subtitle</meta>
    <meta refines="#subtitle" property="display-seq">2</meta>
    <meta name="cover" content="cover"/>"""
    package = EPUB3_PACKAGE.replace(b"</metadata>", additions + b"</metadata>")
    report = EpubPackageExtractor().extract(request(epub_bytes(package=package)))
    fields = {item.field for item in report.observations}
    assert report.outcome == EpubInspectionOutcome.SUCCESS
    assert {
        EpubPackageField.SUBTITLE,
        EpubPackageField.PUBLISHER,
        EpubPackageField.SUBJECT,
        EpubPackageField.DESCRIPTION,
        EpubPackageField.RIGHTS,
        EpubPackageField.SOURCE,
        EpubPackageField.RELATION,
        EpubPackageField.TYPE,
    }.issubset(fields)
    subtitle = [
        item for item in report.observations if item.raw_value == "Synthetic Subtitle"
    ]
    assert len(subtitle) == 1
    assert subtitle[0].field is EpubPackageField.SUBTITLE
    assert subtitle[0].declared_display_sequence == 2
    assert "title-type-subtitle@" in (subtitle[0].citation.selector or "")
    assert "display-seq@" in (subtitle[0].citation.selector or "")
    base_title = next(
        item for item in report.observations if item.raw_value == "A Synthetic Book"
    )
    assert base_title.declared_display_sequence is None
    assert "contradictory_cover_declaration" not in {
        item.code for item in report.warnings
    }
    assert all(
        item.same_file_lineage is not None
        for item in report.observations
        if item.field in {EpubPackageField.COVER, EpubPackageField.SUBTITLE}
    )

    conflict = package.replace(
        b"</manifest>",
        b'<item id="legacy-cover" href="legacy.jpg" media-type="image/jpeg"/></manifest>',
    ).replace(b'content="cover"', b'content="legacy-cover"')
    conflict_report = EpubPackageExtractor().extract(
        request(
            epub_bytes(
                package=conflict,
                extra_entries=(("EPUB/legacy.jpg", b"opaque", zipfile.ZIP_STORED),),
            )
        )
    )
    assert conflict_report.outcome == EpubInspectionOutcome.SUCCESS
    assert "contradictory_cover_declaration" in {
        item.code for item in conflict_report.warnings
    }

    creator_display = EPUB3_PACKAGE.replace(
        b"<dc:creator>", b'<dc:creator id="creator">'
    ).replace(
        b"</metadata>",
        b'<meta refines="#creator" property="display-seq">2</meta></metadata>',
    )
    creator_report = EpubPackageExtractor().extract(
        request(epub_bytes(package=creator_display))
    )
    assert creator_report.outcome == EpubInspectionOutcome.SUCCESS
    assert "inapplicable_metadata_refinement" in {
        item.code for item in creator_report.warnings
    }
    assert all(
        item.declared_display_sequence is None
        for item in creator_report.observations
        if item.field is EpubPackageField.CREATOR
    )


@pytest.mark.parametrize(
    ("original", "replacement", "field", "warning", "invalid_raw", "retained"),
    (
        (
            b'<meta property="dcterms:modified">2026-01-13T00:00:00Z</meta>',
            b'<meta property="dcterms:modified">2026-01-13</meta>',
            EpubPackageField.MODIFICATION_DATE,
            "invalid_date",
            "2026-01-13",
            True,
        ),
        (
            b'<meta property="group-position">2</meta>',
            b'<meta property="group-position">0</meta>',
            EpubPackageField.COLLECTION_POSITION,
            "invalid_metadata_refinement",
            "0",
            False,
        ),
        (
            b"<dc:language>en-GB</dc:language>",
            b"<dc:language>not a language!</dc:language>",
            EpubPackageField.LANGUAGE,
            "invalid_language",
            "not a language!",
            True,
        ),
        (
            b"<dc:language>en-GB</dc:language>",
            b"<dc:language>123</dc:language>",
            EpubPackageField.LANGUAGE,
            "invalid_language",
            "123",
            True,
        ),
        (
            b'<meta property="group-position">2</meta>',
            '<meta property="group-position">٢</meta>'.encode(),
            EpubPackageField.COLLECTION_POSITION,
            "invalid_metadata_refinement",
            "٢",
            False,
        ),
    ),
)
def test_title_type_and_optional_metadata_datatypes_are_local_refinements(
    original: bytes,
    replacement: bytes,
    field: EpubPackageField,
    warning: str,
    invalid_raw: str,
    retained: bool,
) -> None:
    titles = b"""
    <dc:title id="main-title">Main</dc:title>
    <dc:title id="subtitle-title">Subtitle</dc:title>
    <dc:title id="short-title">Short</dc:title>
    <dc:title id="collection-title">Collection</dc:title>
    <dc:title id="edition-title">Edition</dc:title>
    <dc:title id="expanded-title">Expanded</dc:title>
    <meta refines="#main-title" property="title-type">main</meta>
    <meta refines="#subtitle-title" property="title-type">subtitle</meta>
    <meta refines="#short-title" property="title-type">short</meta>
    <meta refines="#collection-title" property="title-type">collection</meta>
    <meta refines="#edition-title" property="title-type">edition</meta>
    <meta refines="#expanded-title" property="title-type">expanded</meta>
    <meta refines="#main-title" property="display-seq">1000000</meta>"""
    package = EPUB3_PACKAGE.replace(b"</metadata>", titles + b"</metadata>")
    report = EpubPackageExtractor().extract(request(epub_bytes(package=package)))
    by_value = {
        item.raw_value: item
        for item in report.observations
        if item.raw_value
        in {"Main", "Subtitle", "Short", "Collection", "Edition", "Expanded"}
    }
    assert report.outcome == EpubInspectionOutcome.SUCCESS
    assert len(by_value) == 6
    assert by_value["Main"].field is EpubPackageField.TITLE
    assert by_value["Subtitle"].field is EpubPackageField.SUBTITLE
    assert by_value["Short"].field is EpubPackageField.TITLE
    assert by_value["Collection"].field is EpubPackageField.COLLECTION
    assert by_value["Edition"].field is EpubPackageField.EDITION
    assert by_value["Expanded"].field is EpubPackageField.TITLE
    assert by_value["Main"].declared_display_sequence == 1_000_000

    invalid_package = EPUB3_PACKAGE.replace(original, replacement)
    invalid = EpubPackageExtractor().extract(
        request(epub_bytes(package=invalid_package))
    )
    assert invalid.outcome == EpubInspectionOutcome.SUCCESS
    assert warning in {item.code for item in invalid.warnings}
    values = [
        item
        for item in invalid.observations
        if item.field is field and item.raw_value == invalid_raw
    ]
    assert bool(values) == retained
    if retained:
        assert all(
            item.applicability is EpubObservationApplicability.INAPPLICABLE
            and item.normalized_value is None
            for item in values
        )

    for display_value, expected in (
        ("0", 0),
        ("1", 1),
        ("1000000", 1_000_000),
        ("1000001", None),
        ("4294967295", None),
    ):
        refinement = (
            f'<meta refines="#title" property="display-seq">{display_value}</meta>'
        ).encode()
        display_package = EPUB3_PACKAGE.replace(
            b"</metadata>", refinement + b"</metadata>"
        )
        display_report = EpubPackageExtractor().extract(
            request(epub_bytes(package=display_package))
        )
        title = next(
            item
            for item in display_report.observations
            if item.raw_value == "A Synthetic Book"
        )
        assert display_report.outcome == EpubInspectionOutcome.SUCCESS
        assert title.declared_display_sequence == expected
        assert (
            "invalid_metadata_refinement"
            in {item.code for item in display_report.warnings}
        ) == (expected is None)


@pytest.mark.parametrize(
    "invalid_language",
    ("not a language!", "1"),
)
def test_invalid_language_warns_and_omits_only_the_optional_qualifier(
    invalid_language: str,
) -> None:
    package = EPUB3_PACKAGE.replace(
        b'<dc:title id="title">',
        (f'<dc:title id="title" xml:lang="{invalid_language}">').encode(),
    )
    report = EpubPackageExtractor().extract(request(epub_bytes(package=package)))
    assert report.outcome == EpubInspectionOutcome.SUCCESS
    assert "invalid_language" in {item.code for item in report.warnings}
    title = next(
        item for item in report.observations if item.field is EpubPackageField.TITLE
    )
    assert title.language is None


def test_multiple_renditions_entities_and_cancellation_are_typed() -> None:
    multiple = EpubPackageExtractor().extract(
        request(epub_bytes(container=CONTAINER_MULTIPLE))
    )
    entity = EpubPackageExtractor().extract(
        request(
            epub_bytes(
                package=b'<!DOCTYPE package [<!ENTITY x "value">]>' + EPUB3_PACKAGE
            )
        )
    )
    cancelled = EpubPackageExtractor().extract(request(epub_bytes(), cancelled=True))
    assert multiple.outcome == EpubInspectionOutcome.UNSUPPORTED_MULTIPLE_RENDITIONS
    assert entity.outcome == EpubInspectionOutcome.MALFORMED
    assert cancelled.outcome == EpubInspectionOutcome.CANCELLATION
    assert not (multiple.observations or entity.observations or cancelled.observations)


def test_unsupported_expat_is_rejected_before_the_source_is_opened() -> None:
    value = epub_bytes()
    reads = 0

    def reader(offset: int, size: int) -> bytes:
        nonlocal reads
        reads += 1
        return value[offset : offset + size]

    base = request(value)
    guarded = EpubPackageRequest(
        base.target,
        base.file,
        EpubPackageSource(len(value), reader),
        base.expected_source,
        base.capability,
        base.limits,
        base.deadline,
        base.monotonic_deadline,
        base.cancellation,
    )
    with patch(
        "mechanical_librarian.adapters.evidence_extractors.epub_package.expat.EXPAT_VERSION",
        "expat_2.7.1",
    ):
        report = EpubPackageExtractor().extract(guarded)
    assert report.outcome == EpubInspectionOutcome.UNAVAILABLE_SOURCE
    assert reads == 0


def test_required_and_opaque_encryption_are_distinct() -> None:
    opaque = b"""<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><EncryptedData xmlns="http://www.w3.org/2001/04/xmlenc#"><CipherData><CipherReference URI="EPUB/cover.jpg"/></CipherData></EncryptedData></encryption>"""
    required = opaque.replace(b"EPUB/cover.jpg", b"EPUB/package.opf")
    opaque_report = EpubPackageExtractor().extract(
        request(epub_bytes(encryption=opaque))
    )
    required_report = EpubPackageExtractor().extract(
        request(epub_bytes(encryption=required))
    )
    assert opaque_report.outcome == EpubInspectionOutcome.SUCCESS
    assert "opaque_resource_encrypted" in tuple(
        item.code for item in opaque_report.warnings
    )
    assert (
        sum(
            item.kind is EpubPackageDeclarationKind.ENCRYPTION
            for item in opaque_report.declarations
        )
        == 1
    )
    assert required_report.outcome == EpubInspectionOutcome.ENCRYPTED
    assert (
        EpubPackageExtractor()
        .extract(request(epub_bytes(package=b"not XML", encryption=required)))
        .outcome
        == EpubInspectionOutcome.ENCRYPTED
    )

    encrypted_key = b"""<EncryptedKey xmlns="http://www.w3.org/2001/04/xmlenc#"><CipherData><CipherValue>repository-authored-synthetic-key</CipherValue></CipherData></EncryptedKey>"""
    opaque_with_key = opaque.replace(
        b"<EncryptedData", encrypted_key + b"<EncryptedData"
    )
    required_with_key = opaque_with_key.replace(b"EPUB/cover.jpg", b"EPUB/package.opf")
    keyed_report = EpubPackageExtractor().extract(
        request(epub_bytes(encryption=opaque_with_key))
    )
    assert keyed_report.outcome == EpubInspectionOutcome.SUCCESS
    assert "opaque_resource_encrypted" in {item.code for item in keyed_report.warnings}
    assert (
        EpubPackageExtractor()
        .extract(request(epub_bytes(encryption=required_with_key)))
        .outcome
        == EpubInspectionOutcome.ENCRYPTED
    )

    encoded_required = required.replace(b"package.opf", b"%70ackage.opf")
    wrong_namespace = opaque.replace(
        b'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"',
        b'xmlns="urn:wrong"',
    )
    empty = b'<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"/>'
    assert (
        EpubPackageExtractor()
        .extract(request(epub_bytes(encryption=encoded_required)))
        .outcome
        == EpubInspectionOutcome.ENCRYPTED
    )
    assert (
        EpubPackageExtractor()
        .extract(request(epub_bytes(encryption=wrong_namespace)))
        .outcome
        == EpubInspectionOutcome.MALFORMED
    )
    assert (
        EpubPackageExtractor().extract(request(epub_bytes(encryption=empty))).outcome
        == EpubInspectionOutcome.MALFORMED
    )

    invalid_documents = (
        b"""<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><EncryptedData xmlns="http://www.w3.org/2001/04/xmlenc#"/></encryption>""",
        opaque.replace(
            b"<CipherData>", b'<CipherData><CipherReference URI="EPUB/cover.jpg"/>', 1
        ),
        opaque.replace(b"EPUB/cover.jpg", b"EPUB/missing.jpg"),
        opaque.replace(b"<CipherData>", b"<Unexpected>", 1).replace(
            b"</CipherData>", b"</Unexpected>", 1
        ),
    )
    for document in invalid_documents:
        assert (
            EpubPackageExtractor()
            .extract(request(epub_bytes(encryption=document)))
            .outcome
            == EpubInspectionOutcome.MALFORMED
        )


@pytest.mark.parametrize(
    ("count", "outcome"),
    (
        (32, EpubInspectionOutcome.SUCCESS),
        (33, EpubInspectionOutcome.RESOURCE_LIMIT),
    ),
)
def test_per_field_declaration_and_public_report_bounds_are_resource_limits(
    count: int,
    outcome: EpubInspectionOutcome,
) -> None:
    subjects = b"".join(
        f"<dc:subject>subject-{index}</dc:subject>".encode() for index in range(count)
    )
    package = EPUB3_PACKAGE.replace(b"</metadata>", subjects + b"</metadata>")
    assert (
        EpubPackageExtractor().extract(request(epub_bytes(package=package))).outcome
        == outcome
    )

    remote_items = b"".join(
        (
            f'<item id="remote-{index}" href="https://example.invalid/{index}" '
            'media-type="application/octet-stream"/>'
        ).encode()
        for index in range(8_190)
    )
    declaration_package = EPUB3_PACKAGE.replace(
        b"</manifest>", remote_items + b"</manifest>"
    )
    declaration_report = EpubPackageExtractor().extract(
        request_with_limits(
            epub_bytes(package=declaration_package),
            EpubPackageLimits(manifest_items=8_192),
        )
    )
    assert declaration_report.outcome == EpubInspectionOutcome.RESOURCE_LIMIT

    large = "x" * 1_021
    additions: list[str] = []
    for local, count in (
        ("publisher", 32),
        ("subject", 32),
        ("description", 32),
        ("rights", 32),
        ("source", 32),
        ("relation", 32),
        ("type", 32),
        ("format", 19),
    ):
        additions.extend(
            f"<dc:{local}>{large}{index:03d}</dc:{local}>" for index in range(count)
        )
    report_package = EPUB3_PACKAGE.replace(
        b"</metadata>", "".join(additions).encode() + b"</metadata>"
    )
    report = EpubPackageExtractor().extract(request(epub_bytes(package=report_package)))
    assert report.outcome == EpubInspectionOutcome.SUCCESS


def test_raw_zip_preflight_rejects_nul_aliases_crc_and_compression_before_xml() -> None:
    original = epub_bytes()
    central = original.find(b"PK\x01\x02")
    assert central > 0

    nul = bytearray(original)
    nul[central + 46] = 0

    unsupported = bytearray(original)
    struct.pack_into("<H", unsupported, central + 10, 99)

    crc = bytearray(original)
    struct.pack_into("<L", crc, central + 16, 0)

    aliases = epub_bytes(
        extra_entries=(
            ("EPUB/opaque", b"one", zipfile.ZIP_STORED),
            ("EPUB/OPAQUE", b"two", zipfile.ZIP_STORED),
        )
    )

    results = tuple(
        EpubPackageExtractor().extract(request(bytes(value))).outcome
        for value in (nul, unsupported, crc, aliases)
    )
    assert results == (
        EpubInspectionOutcome.MALFORMED,
        EpubInspectionOutcome.UNSUPPORTED_COMPRESSION,
        EpubInspectionOutcome.INTEGRITY_FAILURE,
        EpubInspectionOutcome.MALFORMED,
    )


@pytest.mark.parametrize(
    "method",
    (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED),
)
def test_common_inert_zip_flags_and_directory_entries_are_tolerated(
    method: int,
) -> None:
    def add_flags(value: bytes, mask: int, *, central_only: bool = False) -> bytes:
        mutated = bytearray(value)
        position = 0
        while True:
            central = mutated.find(b"PK\x01\x02", position)
            if central < 0:
                break
            local = struct.unpack_from("<L", mutated, central + 42)[0]
            struct.pack_into(
                "<H",
                mutated,
                central + 8,
                struct.unpack_from("<H", mutated, central + 8)[0] | mask,
            )
            if not central_only:
                struct.pack_into(
                    "<H",
                    mutated,
                    local + 6,
                    struct.unpack_from("<H", mutated, local + 6)[0] | mask,
                )
            position = central + 46
        return bytes(mutated)

    directory = epub_bytes(extra_entries=(("EPUB/", b"", method),))
    assert (
        EpubPackageExtractor().extract(request(directory)).outcome
        == EpubInspectionOutcome.SUCCESS
    )

    compatible = add_flags(
        epub_bytes(extra_entries=(("EPUB/", b"", zipfile.ZIP_DEFLATED),)),
        0x16,
    )
    report = EpubPackageExtractor().extract(request(compatible))
    assert report.outcome == EpubInspectionOutcome.SUCCESS
    assert "noncanonical_zip_flags" in {item.code for item in report.warnings}

    for value in (
        add_flags(epub_bytes(), 0x10),
        add_flags(epub_bytes(), 0x20),
        add_flags(epub_bytes(), 0x40),
        add_flags(epub_bytes(), 0x2000),
        add_flags(epub_bytes(), 0x16, central_only=True),
        epub_bytes(extra_entries=(("EPUB/", b"not-empty", zipfile.ZIP_STORED),)),
        epub_bytes(
            extra_entries=(
                ("EPUB/", b"", zipfile.ZIP_STORED),
                ("EPUB", b"opaque", zipfile.ZIP_STORED),
            )
        ),
    ):
        assert (
            EpubPackageExtractor().extract(request(value)).outcome
            == EpubInspectionOutcome.MALFORMED
        )


@pytest.mark.parametrize(
    "value",
    (
        epub_bytes(mimetype_first=False),
        epub_bytes(mimetype_compression=zipfile.ZIP_DEFLATED),
        epub_bytes(mimetype_value=b"\xef\xbb\xbfapplication/epub+zip\r\n"),
    ),
)
def test_common_noncanonical_packaging_preserves_trustworthy_evidence(
    value: bytes,
) -> None:
    report = EpubPackageExtractor().extract(request(value))
    assert report.outcome == EpubInspectionOutcome.SUCCESS
    assert "noncanonical_mimetype" in {item.code for item in report.warnings}

    assert (
        EpubPackageExtractor()
        .extract(request(epub_bytes(mimetype_value=b"application/not-epub")))
        .outcome
        == EpubInspectionOutcome.UNSUPPORTED_FORMAT
    )

    long_description = "Synthetic description " + "x" * 6_600
    package = EPUB3_PACKAGE.replace(
        b"</metadata>",
        f"<dc:description>{long_description}</dc:description></metadata>".encode(),
    )
    report = EpubPackageExtractor().extract(request(epub_bytes(package=package)))
    assert report.outcome == EpubInspectionOutcome.SUCCESS
    assert long_description in {
        item.raw_value
        for item in report.observations
        if item.field is EpubPackageField.DESCRIPTION
    }

    legacy_version = EPUB2_PACKAGE.replace(b'version="2.0"', b'version="1.0"')
    legacy_report = EpubPackageExtractor().extract(
        request(epub_bytes(package=legacy_version))
    )
    assert legacy_report.outcome == EpubInspectionOutcome.SUCCESS
    assert "noncanonical_package_version" in {
        item.code for item in legacy_report.warnings
    }

    optional_defects = (
        (
            EPUB3_PACKAGE.replace(b'<item id="nav"', b'<item id="chapter"', 1),
            (),
            "invalid_manifest_item",
        ),
        (
            EPUB3_PACKAGE.replace(b'href="cover.jpg"', b'href="missing.jpg"'),
            (),
            "missing_manifest_resource",
        ),
        (
            EPUB3_PACKAGE.replace(
                b'<dc:identifier id="pub-id">9780306406157</dc:identifier>',
                b"",
            ),
            (),
            "incomplete_required_metadata",
        ),
    )
    for package, extra_entries, warning in optional_defects:
        compatible = EpubPackageExtractor().extract(
            request(epub_bytes(package=package, extra_entries=extra_entries))
        )
        assert compatible.outcome == EpubInspectionOutcome.SUCCESS
        assert warning in {item.code for item in compatible.warnings}

    assert (
        EpubPackageExtractor().extract(request(epub_bytes()[:-22])).outcome
        == EpubInspectionOutcome.UNSUPPORTED_FORMAT
    )


def test_configured_archive_xml_and_compression_bounds_fail_without_partial_success() -> (
    None
):
    ordinary = epub_bytes()
    entry_limited = EpubPackageExtractor().extract(
        request_with_limits(ordinary, EpubPackageLimits(archive_entries=2))
    )
    node_limited = EpubPackageExtractor().extract(
        request_with_limits(ordinary, EpubPackageLimits(xml_nodes_per_document=2))
    )
    ratio_bomb = epub_bytes(
        extra_entries=(("EPUB/opaque.bin", b"0" * 100_000, zipfile.ZIP_DEFLATED),)
    )
    ratio_limited = EpubPackageExtractor().extract(
        request_with_limits(
            ratio_bomb, EpubPackageLimits(compression_ratio_per_member=2)
        )
    )
    for report in (entry_limited, node_limited, ratio_limited):
        assert report.outcome == EpubInspectionOutcome.RESOURCE_LIMIT
        assert not (report.observations or report.declarations)

    aggregate = epub_bytes(
        extra_entries=(("EPUB/large-opaque.bin", b"x" * 1_000, zipfile.ZIP_STORED),)
    )
    aggregate_report = EpubPackageExtractor().extract(
        request_with_limits(
            aggregate, EpubPackageLimits(expanded_bytes_per_archive=2_000)
        )
    )
    assert aggregate_report.outcome == EpubInspectionOutcome.RESOURCE_LIMIT


def test_live_monotonic_deadline_source_io_and_allocator_failures_are_typed() -> None:
    value = epub_bytes()
    base = request(value)

    def delayed(offset: int, size: int) -> bytes:
        time.sleep(0.01)
        return value[offset : offset + size]

    late = EpubPackageRequest(
        base.target,
        base.file,
        EpubPackageSource(len(value), delayed),
        base.expected_source,
        base.capability,
        base.limits,
        base.deadline,
        time.monotonic() + 0.001,
        base.cancellation,
    )
    unavailable = EpubPackageRequest(
        base.target,
        base.file,
        EpubPackageSource(
            len(value), lambda _offset, _size: (_ for _ in ()).throw(OSError("EIO"))
        ),
        base.expected_source,
        base.capability,
        base.limits,
        base.deadline,
        base.monotonic_deadline,
        base.cancellation,
    )
    assert EpubPackageExtractor().extract(late).outcome == EpubInspectionOutcome.TIMEOUT
    assert (
        EpubPackageExtractor().extract(unavailable).outcome
        == EpubInspectionOutcome.UNAVAILABLE_SOURCE
    )
    with patch(
        "mechanical_librarian.adapters.evidence_extractors.epub_package.expat.ParserCreate",
        side_effect=MemoryError,
    ):
        exhausted = EpubPackageExtractor().extract(request(value))
    assert exhausted.outcome == EpubInspectionOutcome.RESOURCE_LIMIT
