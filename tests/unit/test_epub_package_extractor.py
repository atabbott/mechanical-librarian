from __future__ import annotations

import hashlib
import struct
import time
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

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
        TargetId(AdapterId("synthetic"), ConnectionId("local"), LibraryId("library"), ExternalItemId("book")),
        FileReference("books", "generated/book.epub"),
        EpubPackageSource(len(value), lambda offset, size: value[offset : offset + size]),
        Fingerprint("sha256", SchemaVersion("source-v1"), hashlib.sha256(value).hexdigest()),
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


class EpubPackageExtractorTests(unittest.TestCase):
    def test_spine_linearity_is_exact_and_invalid_values_are_non_linear(self) -> None:
        original = b'<itemref idref="chapter"/>'
        for value, expected_linear, warned in ((None, True, False), ("yes", True, False), ("no", False, False), ("YES", False, True), (" no ", False, True)):
            itemref = original if value is None else f'<itemref idref="chapter" linear="{value}"/>'.encode()
            report = EpubPackageExtractor().extract(request(epub_bytes(package=EPUB3_PACKAGE.replace(original, itemref))))
            with self.subTest(value=value):
                slot = next(item for item in report.declarations if item.kind is EpubPackageDeclarationKind.SPINE_SLOT)
                item = next(item for item in report.declarations if item.kind is EpubPackageDeclarationKind.SPINE_ITEM)
                self.assertEqual((slot.linear, item.linear), (expected_linear, expected_linear))
                self.assertEqual("invalid_spine_item" in {warning.code for warning in report.warnings}, warned)

    def test_epub3_package_is_deterministic_and_cited(self) -> None:
        value = epub_bytes()
        extractor = EpubPackageExtractor()
        first = extractor.extract(request(value))
        second = extractor.extract(request(value))

        self.assertEqual(first.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertEqual(first, second)
        self.assertEqual(first.source_fingerprint.digest, hashlib.sha256(value).hexdigest())
        self.assertTrue({EpubPackageField.TITLE, EpubPackageField.CREATOR, EpubPackageField.PACKAGE_IDENTIFIER, EpubPackageField.COVER}.issubset({item.field for item in first.observations}))
        self.assertEqual(sum(item.kind is EpubPackageDeclarationKind.NAVIGATION for item in first.declarations), 1)
        self.assertEqual(sum(item.kind is EpubPackageDeclarationKind.MANIFEST_ITEM for item in first.declarations), 3)
        self.assertIn("unsupported_vendor_metadata", {item.code for item in first.warnings})

    def test_bounded_producer_forced_zip64_local_headers_are_supported(self) -> None:
        report = EpubPackageExtractor().extract(request(epub_zip64_bytes()))
        self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)

    def test_data_descriptors_are_exact_and_unsafe_general_flags_are_rejected(self) -> None:
        described = epub_data_descriptor_bytes()
        valid = EpubPackageExtractor().extract(request(described))
        corrupted = bytearray(described)
        descriptor = corrupted.find(b"PK\x07\x08")
        self.assertGreater(descriptor, 0)
        corrupted[descriptor + 4] ^= 0x01

        unsafe_flag = bytearray(epub_bytes())
        central = unsafe_flag.find(b"PK\x01\x02")
        local = unsafe_flag.find(b"PK\x03\x04", 1)
        self.assertGreater(central, 0)
        self.assertGreater(local, 0)
        struct.pack_into("<H", unsafe_flag, central + 8, struct.unpack_from("<H", unsafe_flag, central + 8)[0] | 0x20)
        struct.pack_into("<H", unsafe_flag, local + 6, struct.unpack_from("<H", unsafe_flag, local + 6)[0] | 0x20)

        self.assertEqual(valid.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertEqual(
            EpubPackageExtractor().extract(request(bytes(corrupted))).outcome,
            EpubInspectionOutcome.INTEGRITY_FAILURE,
        )
        self.assertEqual(
            EpubPackageExtractor().extract(request(bytes(unsafe_flag))).outcome,
            EpubInspectionOutcome.MALFORMED,
        )

    def test_descriptor_signature_ambiguity_and_local_placeholders_are_validated(self) -> None:
        for signed in (False, True):
            with self.subTest(signed=signed):
                value = epub_data_descriptor_bytes(
                    descriptor_signature=signed,
                    crc_signature_member=True,
                )
                self.assertEqual(
                    EpubPackageExtractor().extract(request(value)).outcome,
                    EpubInspectionOutcome.SUCCESS,
                )

        described = epub_data_descriptor_bytes()
        first_local = described.find(b"PK\x03\x04")
        second_local = described.find(b"PK\x03\x04", first_local + 4)
        first_central = described.find(b"PK\x01\x02")
        second_central = described.find(b"PK\x01\x02", first_central + 4)
        self.assertGreater(second_local, first_local)
        self.assertGreater(second_central, first_central)
        for offset in (14, 18, 22):
            with self.subTest(local_field_offset=offset):
                corrupted = bytearray(described)
                struct.pack_into("<L", corrupted, second_local + offset, 1)
                self.assertEqual(
                    EpubPackageExtractor().extract(request(bytes(corrupted))).outcome,
                    EpubInspectionOutcome.INTEGRITY_FAILURE,
                )

        invalid_version = bytearray(described)
        struct.pack_into("<H", invalid_version, second_local + 4, 0)
        struct.pack_into("<H", invalid_version, second_central + 6, 0)
        self.assertEqual(
            EpubPackageExtractor().extract(request(bytes(invalid_version))).outcome,
            EpubInspectionOutcome.MALFORMED,
        )

    def test_epub2_preserves_legacy_declarations_without_dereferencing(self) -> None:
        report = EpubPackageExtractor().extract(request(epub_bytes(version=2)))
        self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertIn(EpubPackageDeclarationKind.NCX, {item.kind for item in report.declarations})
        self.assertIn(EpubPackageDeclarationKind.GUIDE_REFERENCE, {item.kind for item in report.declarations})
        self.assertIn(EpubPackageField.PUBLICATION_DATE, {item.field for item in report.observations})
        self.assertIn(EpubPackageField.CREATOR_ROLE, {item.field for item in report.observations})
        self.assertIn(EpubPackageField.SORT_KEY, {item.field for item in report.observations})
        self.assertIn(EpubPackageField.IDENTIFIER_SCHEME, {item.field for item in report.observations})
        primary = next(
            item
            for item in report.observations
            if item.field is EpubPackageField.PACKAGE_IDENTIFIER
        )
        self.assertEqual(primary.raw_value, "0-306-40615-2")
        self.assertEqual(primary.normalized_value, "0306406152")
        self.assertIs(primary.applicability, EpubObservationApplicability.UNKNOWN)

        tours = b'''<tours>
    <tour title="Synthetic route">
      <site title="Synthetic stop" href="chapter.xhtml#start"/>
    </tour>
  </tours>
  '''
        toured_package = EPUB2_PACKAGE.replace(b"  <guide>", b"  " + tours + b"<guide>")
        toured = EpubPackageExtractor().extract(
            request(epub_bytes(package=toured_package))
        )
        self.assertEqual(toured.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertIn("deprecated_tours", {item.code for item in toured.warnings})

        invalid_tours = (
            EPUB2_PACKAGE.replace(b"  <guide>", b"  <tours/>\n  <guide>"),
            toured_package.replace(b'href="chapter.xhtml#start"', b'href="missing.xhtml"', 1),
            toured_package.replace(b'title="Synthetic stop" ', b""),
            EPUB3_PACKAGE.replace(b"</package>", tours + b"</package>"),
        )
        for package in invalid_tours:
            with self.subTest(invalid_tours=hashlib.sha256(package).hexdigest()):
                invalid_tour_report = EpubPackageExtractor().extract(
                    request(epub_bytes(package=package))
                )
                self.assertEqual(invalid_tour_report.outcome, EpubInspectionOutcome.SUCCESS)
                self.assertIn(
                    "invalid_deprecated_tours",
                    {item.code for item in invalid_tour_report.warnings},
                )

    def test_epub2_deprecated_metadata_wrappers_preserve_evidence_and_bounds(self) -> None:
        wrapped = EPUB2_PACKAGE.replace(
            b'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/" '
            b'xmlns:opf="http://www.idpf.org/2007/opf">',
            b'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/" '
            b'xmlns:opf="http://www.idpf.org/2007/opf"><dc-metadata>',
        ).replace(
            b'    <meta name="cover" content="cover"/>',
            b'  </dc-metadata><x-metadata>'
            b'<meta name="cover" content="cover"/></x-metadata>',
        )
        value = epub_bytes(version=2, package=wrapped)
        first = EpubPackageExtractor().extract(request(value))
        second = EpubPackageExtractor().extract(request(value))

        self.assertEqual(first.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertEqual(first, second)
        self.assertEqual(first.usage.metadata_nodes, 6)
        self.assertTrue(
            {
                EpubPackageField.TITLE,
                EpubPackageField.CREATOR,
                EpubPackageField.LANGUAGE,
                EpubPackageField.PACKAGE_IDENTIFIER,
                EpubPackageField.COVER,
            }
            <= {item.field for item in first.observations}
        )
        self.assertEqual(
            EpubPackageExtractor().extract(
                request_with_limits(value, EpubPackageLimits(metadata_nodes=6))
            ).outcome,
            EpubInspectionOutcome.SUCCESS,
        )
        self.assertEqual(
            EpubPackageExtractor().extract(
                request_with_limits(value, EpubPackageLimits(metadata_nodes=5))
            ).outcome,
            EpubInspectionOutcome.RESOURCE_LIMIT,
        )

    def test_epub2_ncx_reference_is_required_and_rejects_fallback_information(self) -> None:
        ncx_item = (
            b'<item id="ncx" href="toc.ncx" '
            b'media-type="application/x-dtbncx+xml"/>'
        )
        invalid_packages = (
            EPUB2_PACKAGE.replace(b'<spine toc="ncx">', b"<spine>"),
            EPUB2_PACKAGE.replace(b'<spine toc="ncx">', b'<spine toc="">'),
            *(
                EPUB2_PACKAGE.replace(
                    ncx_item,
                    ncx_item[:-2]
                    + b" "
                    + attribute
                    + b'="chapter"/>',
                )
                for attribute in (
                    b"fallback",
                    b"fallback-style",
                    b"required-namespace",
                )
            ),
        )
        for package in invalid_packages:
            with self.subTest(package=hashlib.sha256(package).hexdigest()):
                report = EpubPackageExtractor().extract(
                    request(epub_bytes(version=2, package=package))
                )
                self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
                self.assertIn(
                    "invalid_ncx_declaration",
                    {item.code for item in report.warnings},
                )
                self.assertFalse(
                    any(
                        item.kind is EpubPackageDeclarationKind.NCX
                        for item in report.declarations
                    )
                )

        epub3 = EpubPackageExtractor().extract(request(epub_bytes()))
        self.assertNotIn(
            "invalid_ncx_declaration", {item.code for item in epub3.warnings}
        )

        epub3_with_fallback = EPUB3_PACKAGE.replace(
            b'<item id="chapter" href="chapter.xhtml" '
            b'media-type="application/xhtml+xml"/>',
            b'<item id="ncx" href="toc.ncx" '
            b'media-type="application/x-dtbncx+xml" fallback="chapter"/>'
            b'<item id="chapter" href="chapter.xhtml" '
            b'media-type="application/xhtml+xml"/>',
        ).replace(b"<spine>", b'<spine toc="ncx">')
        fallback_report = EpubPackageExtractor().extract(
            request(epub_bytes(package=epub3_with_fallback))
        )
        self.assertNotIn(
            "invalid_ncx_declaration",
            {item.code for item in fallback_report.warnings},
        )
        self.assertIn(
            EpubPackageDeclarationKind.NCX,
            {item.kind for item in fallback_report.declarations},
        )

    def test_remote_manifest_resource_is_hashed_and_never_fetched_or_disclosed(self) -> None:
        package = EPUB3_PACKAGE.replace(
            b"</manifest>",
            b'<item id="remote" href="https://catalogue.example/private.xml" media-type="application/xml"/></manifest>',
        )
        report = EpubPackageExtractor().extract(request(epub_bytes(package=package)))
        self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertEqual(sum(item.kind is EpubPackageDeclarationKind.MANIFEST_ITEM for item in report.declarations), 4)

    def test_namespaces_spine_cardinality_percent_paths_and_global_ids_are_strict(self) -> None:
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
        duplicate_id = EPUB3_PACKAGE.replace(
            b'<item id="nav"', b'<item id="title"', 1
        )
        outcomes = (
            EpubPackageExtractor().extract(request(epub_bytes(container=foreign_container))).outcome,
            EpubPackageExtractor().extract(request(epub_bytes(package=foreign_package))).outcome,
            EpubPackageExtractor().extract(request(epub_bytes(package=empty_spine))).outcome,
            EpubPackageExtractor().extract(
                request(
                    epub_bytes(
                        package=encoded_path,
                        extra_entries=(("EPUB/chapter one.xhtml", b"opaque", zipfile.ZIP_STORED),),
                    )
                )
            ).outcome,
            EpubPackageExtractor().extract(request(epub_bytes(package=duplicate_id))).outcome,
        )
        self.assertEqual(
            outcomes,
            (
                EpubInspectionOutcome.MALFORMED,
                EpubInspectionOutcome.MALFORMED,
                EpubInspectionOutcome.MALFORMED,
                EpubInspectionOutcome.SUCCESS,
                EpubInspectionOutcome.SUCCESS,
            ),
        )
        duplicate_report = EpubPackageExtractor().extract(
            request(epub_bytes(package=duplicate_id))
        )
        self.assertIn(
            "ambiguous_package_id",
            {item.code for item in duplicate_report.warnings},
        )

    def test_invalid_and_ambiguous_ids_are_omitted_from_semantic_lookups(self) -> None:
        duplicate_primary = EPUB3_PACKAGE.replace(
            b'<dc:identifier id="pub-id">9780306406157</dc:identifier>',
            b'<dc:identifier id="pub-id">9780306406157</dc:identifier>'
            b'<dc:identifier id="pub-id">9781861972712</dc:identifier>',
        )
        duplicate_primary_report = EpubPackageExtractor().extract(
            request(epub_bytes(package=duplicate_primary))
        )
        self.assertEqual(
            duplicate_primary_report.outcome, EpubInspectionOutcome.SUCCESS
        )
        self.assertIn(
            "ambiguous_package_id",
            {item.code for item in duplicate_primary_report.warnings},
        )
        self.assertNotIn(
            EpubPackageField.PACKAGE_IDENTIFIER,
            {item.field for item in duplicate_primary_report.observations},
        )

        invalid_primary = EPUB3_PACKAGE.replace(b'id="pub-id"', b'id="bad id"', 1)
        invalid_primary_report = EpubPackageExtractor().extract(
            request(epub_bytes(package=invalid_primary))
        )
        self.assertIn(
            "invalid_package_id",
            {item.code for item in invalid_primary_report.warnings},
        )
        self.assertNotIn(
            EpubPackageField.PACKAGE_IDENTIFIER,
            {item.field for item in invalid_primary_report.observations},
        )

        duplicate_manifest_id = EPUB3_PACKAGE.replace(
            b'<item id="nav"', b'<item id="chapter"', 1
        )
        duplicate_manifest_report = EpubPackageExtractor().extract(
            request(epub_bytes(package=duplicate_manifest_id))
        )
        self.assertEqual(
            duplicate_manifest_report.outcome, EpubInspectionOutcome.SUCCESS
        )
        self.assertFalse(
            any(
                item.kind is EpubPackageDeclarationKind.SPINE_ITEM and item.identifier == "chapter"
                for item in duplicate_manifest_report.declarations
            )
        )
        self.assertTrue(
            {"ambiguous_package_id", "invalid_manifest_item", "invalid_spine_item"}
            <= {item.code for item in duplicate_manifest_report.warnings}
        )

    def test_duplicate_cover_resource_keeps_cover_item_provenance(self) -> None:
        package = EPUB3_PACKAGE.replace(
            b'href="cover.jpg"', b'href="chapter.xhtml"'
        )
        report = EpubPackageExtractor().extract(request(epub_bytes(package=package)))
        cover_observation = next(
            item
            for item in report.observations
            if item.field is EpubPackageField.COVER
        )
        cover_declaration = next(
            item
            for item in report.declarations
            if item.kind is EpubPackageDeclarationKind.COVER
        )
        self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertIn(
            "duplicate_manifest_resource", {item.code for item in report.warnings}
        )
        self.assertEqual(
            cover_observation.citation.source_ordinal,
            cover_declaration.citation.source_ordinal,
        )

    def test_missing_guide_target_is_warned_and_omitted(self) -> None:
        reference = (
            b'<reference type="text" title="Start" '
            b'href="chapter.xhtml#start"/>'
        )
        invalid_references = (
            reference.replace(
                b'href="chapter.xhtml#start"', b'href="missing.xhtml#start"'
            ),
            b'<reference type="text" title="Start"/>',
            b'<landmark type="text" title="Start" href="chapter.xhtml#start"/>',
            b'<reference type="text" title="Start" href="chapter.xhtml#start">'
            b"<landmark/>"
            b"</reference>",
            b'<reference type="text" title="Start" '
            b'href="chapter.xhtml#start">unexpected</reference>',
        )
        for invalid_reference in invalid_references:
            with self.subTest(
                invalid_reference=hashlib.sha256(invalid_reference).hexdigest()
            ):
                package = EPUB2_PACKAGE.replace(reference, invalid_reference)
                report = EpubPackageExtractor().extract(
                    request(epub_bytes(version=2, package=package))
                )
                self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
                self.assertIn(
                    "invalid_guide_reference",
                    {item.code for item in report.warnings},
                )
                self.assertFalse(
                    any(
                        item.kind is EpubPackageDeclarationKind.GUIDE_REFERENCE
                        for item in report.declarations
                    )
                )

        fragment_only = EPUB2_PACKAGE.replace(
            b'href="chapter.xhtml#start"', b'href="#start"'
        )
        fragment_report = EpubPackageExtractor().extract(
            request(epub_bytes(version=2, package=fragment_only))
        )
        self.assertEqual(fragment_report.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertIn(
            "invalid_guide_reference",
            {item.code for item in fragment_report.warnings},
        )

    def test_invalid_optional_resource_syntax_is_warned_and_omitted(self) -> None:
        for href in (b"a:b", b"chapter.xhtml?edition=1", b"chapter%ZZ.xhtml"):
            with self.subTest(kind="manifest", href=href):
                package = EPUB3_PACKAGE.replace(
                    b'href="chapter.xhtml"', b'href="' + href + b'"', 1
                )
                report = EpubPackageExtractor().extract(
                    request(epub_bytes(package=package))
                )
                self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
                self.assertTrue(
                    {"invalid_manifest_item", "invalid_spine_item"}
                    <= {item.code for item in report.warnings}
                )

            with self.subTest(kind="guide", href=href):
                package = EPUB2_PACKAGE.replace(
                    b'href="chapter.xhtml#start"', b'href="' + href + b'"', 1
                )
                report = EpubPackageExtractor().extract(
                    request(epub_bytes(version=2, package=package))
                )
                self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
                self.assertIn(
                    "invalid_guide_reference",
                    {item.code for item in report.warnings},
                )

        tours = b'''<tours>
    <tour title="Synthetic route">
      <site title="Synthetic stop" href="a:b"/>
    </tour>
  </tours>
  '''
        toured = EPUB2_PACKAGE.replace(b"  <guide>", b"  " + tours + b"<guide>")
        report = EpubPackageExtractor().extract(
            request(epub_bytes(version=2, package=toured))
        )
        self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertIn(
            "invalid_deprecated_tours", {item.code for item in report.warnings}
        )

    def test_spine_requires_compatible_content_or_local_fallback(self) -> None:
        incompatible_packages = (
            EPUB3_PACKAGE.replace(
                b'<item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>',
                b'<item id="chapter" href="chapter.xhtml" media-type="image/jpeg"/>',
            ),
            EPUB2_PACKAGE.replace(b'idref="chapter"', b'idref="ncx"'),
        )
        for package in incompatible_packages:
            with self.subTest(package=hashlib.sha256(package).hexdigest()):
                report = EpubPackageExtractor().extract(
                    request(
                        epub_bytes(
                            version=2 if b'idref="ncx"' in package else 3,
                            package=package,
                        )
                    )
                )
                self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
                self.assertIn(
                    "invalid_spine_item", {item.code for item in report.warnings}
                )
                self.assertFalse(
                    any(
                        item.kind is EpubPackageDeclarationKind.SPINE_ITEM
                        for item in report.declarations
                    )
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
                    extra_entries=(
                        ("EPUB/fallback.xhtml", b"opaque", zipfile.ZIP_STORED),
                    ),
                )
            )
        )
        self.assertEqual(fallback_report.outcome, EpubInspectionOutcome.SUCCESS)
        spine = next(
            item
            for item in fallback_report.declarations
            if item.kind is EpubPackageDeclarationKind.SPINE_ITEM
        )
        self.assertEqual(spine.identifier, "chapter")

    def test_repeated_spine_itemrefs_are_cited_warned_and_omitted(self) -> None:
        expected_ordinals = {2: (15, 16, 17), 3: (19, 20, 21)}
        for version, base_package in (
            (2, EPUB2_PACKAGE),
            (3, EPUB3_PACKAGE),
        ):
            with self.subTest(version=version):
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
                    item
                    for item in first.warnings
                    if item.code == "invalid_spine_item"
                ]
                first_ordinal, duplicate_ordinal, second_ordinal = (
                    expected_ordinals[version]
                )

                self.assertEqual(first.outcome, EpubInspectionOutcome.SUCCESS)
                self.assertEqual(first, second)
                self.assertEqual(
                    [item.identifier for item in spine_items],
                    ["chapter", "second"],
                )
                self.assertEqual(
                    [item.source_ordinal for item in spine_items],
                    [first_ordinal, second_ordinal],
                )
                self.assertEqual(
                    [item.citation.source_ordinal for item in spine_items],
                    [first_ordinal, second_ordinal],
                )
                self.assertEqual(len(duplicate_warnings), 1)
                self.assertIsNotNone(duplicate_warnings[0].citation)
                self.assertEqual(
                    duplicate_warnings[0].citation.source_ordinal,
                    duplicate_ordinal,
                )
                self.assertEqual(
                    duplicate_warnings[0].citation.section,
                    EpubPackageCitationSection.SPINE,
                )

    def test_distinct_spine_itemrefs_remain_distinct(self) -> None:
        for version, base_package in (
            (2, EPUB2_PACKAGE),
            (3, EPUB3_PACKAGE),
        ):
            with self.subTest(version=version):
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

                self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
                self.assertEqual(
                    [
                        item.identifier
                        for item in report.declarations
                        if item.kind is EpubPackageDeclarationKind.SPINE_ITEM
                    ],
                    ["chapter", "second"],
                )
                self.assertNotIn(
                    "invalid_spine_item", {item.code for item in report.warnings}
                )

    def test_malformed_spine_itemref_does_not_claim_first_valid_occurrence(self) -> None:
        for version, base_package in (
            (2, EPUB2_PACKAGE),
            (3, EPUB3_PACKAGE),
        ):
            for malformed_content in (b"unexpected", b"<meta/>"):
                with self.subTest(version=version, content=malformed_content):
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
                        item
                        for item in report.warnings
                        if item.code == "invalid_spine_item"
                    ]

                    self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
                    self.assertEqual(len(spine_items), 1)
                    self.assertEqual(spine_items[0].identifier, "chapter")
                    self.assertEqual(len(invalid_warnings), 1)
                    self.assertIsNotNone(invalid_warnings[0].citation)
                    self.assertEqual(
                        invalid_warnings[0].citation.section,
                        EpubPackageCitationSection.SPINE,
                    )
                    self.assertLess(
                        invalid_warnings[0].citation.source_ordinal,
                        spine_items[0].source_ordinal,
                    )
                    self.assertEqual(
                        spine_items[0].source_ordinal,
                        spine_items[0].citation.source_ordinal,
                    )

    def test_manifest_semantics_require_safe_local_compatible_resources(self) -> None:
        hostile_values = (
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
        )
        for package in hostile_values:
            with self.subTest(package=hashlib.sha256(package).hexdigest()):
                report = EpubPackageExtractor().extract(
                    request(epub_bytes(package=package))
                )
                self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
                self.assertIn(
                    "unsafe_manifest_item", {item.code for item in report.warnings}
                )

        oversized_media = EPUB3_PACKAGE.replace(
            b"application/xhtml+xml",
            b"application/" + b"a" * 125,
            1,
        )
        oversized_report = EpubPackageExtractor().extract(
            request(epub_bytes(package=oversized_media))
        )
        self.assertEqual(oversized_report.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertIn(
            "invalid_manifest_item", {item.code for item in oversized_report.warnings}
        )
        excessive_properties = EPUB3_PACKAGE.replace(
            b'properties="nav"',
            ('properties="' + " ".join(f"p{index}" for index in range(65)) + '"').encode(),
        )
        excessive_report = EpubPackageExtractor().extract(
            request(epub_bytes(package=excessive_properties))
        )
        self.assertEqual(excessive_report.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertIn(
            "invalid_manifest_item", {item.code for item in excessive_report.warnings}
        )

        missing_cover = EPUB3_PACKAGE.replace(
            b'href="cover.jpg"', b'href="missing.jpg"'
        )
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
            with self.subTest(warning=warning):
                report = EpubPackageExtractor().extract(
                    request(epub_bytes(package=package))
                )
                self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
                self.assertIn(warning, {item.code for item in report.warnings})
                self.assertNotIn(forbidden_kind, {item.kind for item in report.declarations})
                self.assertNotIn(forbidden_field, {item.field for item in report.observations})

    def test_isbn_families_validate_lexical_position_prefix_checksum_and_urn(self) -> None:
        cases = (
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
        )
        original = b"9780306406157"
        for raw, normalized, scheme, valid in cases:
            with self.subTest(raw=raw):
                package = EPUB3_PACKAGE.replace(original, raw, 1)
                report = EpubPackageExtractor().extract(
                    request(epub_bytes(package=package))
                )
                identifier = next(
                    item
                    for item in report.observations
                    if item.field is EpubPackageField.IDENTIFIER
                )
                self.assertEqual(identifier.normalized_value, normalized)
                self.assertIs(
                    identifier.applicability,
                    (
                        EpubObservationApplicability.UNKNOWN
                        if valid
                        else EpubObservationApplicability.INAPPLICABLE
                    ),
                )
                inferred = {
                    item.normalized_value
                    for item in report.observations
                    if item.field is EpubPackageField.IDENTIFIER_SCHEME
                    and item.citation.selector == "inferred-identifier-type"
                }
                self.assertEqual(inferred, set() if scheme is None else {scheme})
                self.assertEqual(
                    "invalid_identifier" in {item.code for item in report.warnings},
                    not valid,
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
        self.assertEqual(declared_report.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertIs(
            declared_identifier.applicability,
            EpubObservationApplicability.INAPPLICABLE,
        )
        self.assertIn(
            "invalid_identifier", {item.code for item in declared_report.warnings}
        )
        self.assertIn(
            "ISBN",
            {
                item.normalized_value
                for item in declared_report.observations
                if item.field is EpubPackageField.IDENTIFIER_SCHEME
            },
        )

    def test_explicit_non_isbn_scheme_does_not_infer_an_isbn(self) -> None:
        package = EPUB2_PACKAGE.replace(
            b'opf:scheme="ISBN">0-306-40615-2',
            b'opf:scheme="LCCN">9780306406157',
        )
        report = EpubPackageExtractor().extract(request(epub_bytes(version=2, package=package)))
        identifier = next(
            item for item in report.observations
            if item.field is EpubPackageField.IDENTIFIER
        )
        self.assertEqual(identifier.normalized_value, "9780306406157")
        self.assertIs(identifier.applicability, EpubObservationApplicability.UNKNOWN)
        self.assertNotIn(
            "ISBN-13",
            {
                item.normalized_value
                for item in report.observations
                if item.field is EpubPackageField.IDENTIFIER_SCHEME
            },
        )

    def test_declared_identifier_schemes_are_casefolded_and_normalized(self) -> None:
        cases = (
            (b'isbn', b'9780306406157', "9780306406157"),
            (b'DoI', b'doi:10.1234/synthetic.1', "10.1234/synthetic.1"),
            (b'aSiN', b'asin:b012345678', "B012345678"),
            (
                b'UuId',
                b'12345678-1234-4abc-8123-123456789abc',
                "urn:uuid:12345678-1234-4abc-8123-123456789abc",
            ),
        )
        for scheme, raw, normalized in cases:
            with self.subTest(scheme=scheme):
                package = EPUB2_PACKAGE.replace(
                    b'opf:scheme="ISBN">0-306-40615-2',
                    b'opf:scheme="' + scheme + b'">' + raw,
                )
                report = EpubPackageExtractor().extract(
                    request(epub_bytes(version=2, package=package))
                )
                identifier = next(
                    item for item in report.observations
                    if item.field is EpubPackageField.IDENTIFIER
                )
                self.assertEqual(identifier.normalized_value, normalized)
                self.assertIn(
                    scheme.decode(),
                    {
                        item.normalized_value
                        for item in report.observations
                        if item.field is EpubPackageField.IDENTIFIER_SCHEME
                    },
                )

    def test_repeated_legacy_cover_declarations_preserve_agreement_and_conflict(self) -> None:
        cover_meta = b'<meta name="cover" content="cover"/>'
        agreeing_package = EPUB2_PACKAGE.replace(
            cover_meta, cover_meta + cover_meta
        )
        agreeing = EpubPackageExtractor().extract(
            request(epub_bytes(version=2, package=agreeing_package))
        )
        self.assertEqual(agreeing.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertNotIn(
            "contradictory_cover_declaration",
            {item.code for item in agreeing.warnings},
        )
        self.assertEqual(
            sum(
                item.field is EpubPackageField.COVER
                for item in agreeing.observations
            ),
            1,
        )
        agreeing_cover = next(
            item
            for item in agreeing.declarations
            if item.kind is EpubPackageDeclarationKind.COVER
        )
        self.assertTrue(agreeing_cover.citation.expanded_name.endswith("}meta"))

        conflicting_package = EPUB2_PACKAGE.replace(
            cover_meta,
            cover_meta + b'<meta name="cover" content="cover2"/>',
        ).replace(
            b"</manifest>",
            b'<item id="cover2" href="cover2.jpg" media-type="image/jpeg"/>'
            b"</manifest>",
        )
        conflicting = EpubPackageExtractor().extract(
            request(
                epub_bytes(
                    version=2,
                    package=conflicting_package,
                    extra_entries=(
                        ("EPUB/cover2.jpg", b"opaque", zipfile.ZIP_STORED),
                    ),
                )
            )
        )
        self.assertEqual(conflicting.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertIn(
            "contradictory_cover_declaration",
            {item.code for item in conflicting.warnings},
        )
        self.assertNotIn(
            EpubPackageField.COVER,
            {item.field for item in conflicting.observations},
        )

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
                    extra_entries=(
                        ("EPUB/cover2.jpg", b"opaque", zipfile.ZIP_STORED),
                    ),
                )
            )
        )
        self.assertEqual(current_conflict.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertIn(
            "contradictory_cover_declaration",
            {item.code for item in current_conflict.warnings},
        )
        self.assertNotIn(
            EpubPackageField.COVER,
            {item.field for item in current_conflict.observations},
        )

    def test_hostile_path_reference_matrix_is_typed_and_never_resolved_externally(self) -> None:
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
            with self.subTest(reference=hashlib.sha256(reference.encode()).hexdigest()):
                self.assertEqual(
                    EpubPackageExtractor().extract(
                        request(epub_bytes(package=package))
                    ).outcome,
                    expected,
                )

        encrypted_zip = bytearray(epub_bytes())
        central = encrypted_zip.find(b"PK\x01\x02")
        struct.pack_into(
            "<H",
            encrypted_zip,
            central + 8,
            struct.unpack_from("<H", encrypted_zip, central + 8)[0] | 1,
        )
        self.assertEqual(
            EpubPackageExtractor().extract(request(bytes(encrypted_zip))).outcome,
            EpubInspectionOutcome.ENCRYPTED,
        )

    def test_entity_and_xinclude_matrix_is_rejected_without_external_access(self) -> None:
        declarations = (
            b'<!DOCTYPE package [<!ENTITY internal "value">]>',
            b'<!DOCTYPE package [<!ENTITY external SYSTEM "file:///private/value">]>',
            b'<!DOCTYPE package [<!ENTITY network SYSTEM "https://example.invalid/value">]>',
            b'<!DOCTYPE package [<!ENTITY % parameter "value">]>',
            b'<!DOCTYPE package [<!NOTATION image SYSTEM "image/jpeg"><!ENTITY unparsed SYSTEM "x" NDATA image>]>',
        )
        for declaration in declarations:
            with self.subTest(declaration=hashlib.sha256(declaration).hexdigest()):
                package = declaration + EPUB3_PACKAGE
                self.assertEqual(
                    EpubPackageExtractor().extract(
                        request(epub_bytes(package=package))
                    ).outcome,
                    EpubInspectionOutcome.MALFORMED,
                )
        xinclude = EPUB3_PACKAGE.replace(
            b"</metadata>",
            b'<xi:include xmlns:xi="http://www.w3.org/2001/XInclude" href="file:///private/value"/></metadata>',
        )
        self.assertEqual(
            EpubPackageExtractor().extract(
                request(epub_bytes(package=xinclude))
            ).outcome,
            EpubInspectionOutcome.MALFORMED,
        )

    def test_package_child_order_and_refinement_targets_are_closed(self) -> None:
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
            with self.subTest(package=hashlib.sha256(package).hexdigest()):
                self.assertEqual(
                    EpubPackageExtractor().extract(
                        request(epub_bytes(package=package))
                    ).outcome,
                    EpubInspectionOutcome.MALFORMED,
                )
        for package, warning in (
            (missing_target, "unresolved_metadata_refinement"),
            (wrong_target, "inapplicable_metadata_refinement"),
        ):
            with self.subTest(compatible_package=hashlib.sha256(package).hexdigest()):
                report = EpubPackageExtractor().extract(
                    request(epub_bytes(package=package))
                )
                self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
                self.assertIn(warning, {item.code for item in report.warnings})
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
            with self.subTest(trailing_children=trailing_children):
                package = EPUB3_PACKAGE.replace(
                    b"</package>", trailing_children + b"</package>"
                )
                self.assertEqual(
                    EpubPackageExtractor().extract(
                        request(epub_bytes(package=package))
                    ).outcome,
                    outcome,
                )
        container_links = CONTAINER_ONE.replace(
            b"</container>",
            b'<links><link href="https://example.invalid/metadata" rel="record"/></links></container>',
        )
        self.assertEqual(
            EpubPackageExtractor().extract(
                request(epub_bytes(container=container_links))
            ).outcome,
            EpubInspectionOutcome.SUCCESS,
        )

    def test_unsafe_metadata_is_one_way_projected_with_a_cited_warning(self) -> None:
        unsafe_values = (
            "Authorization: Bearer synthetic-token-1234",
            "/home/example/private/book.epub",
            r"C:\\library\\private\\book.epub",
            "file:///srv/private/book.epub",
            "spoof\u202ereversed",
        )
        for private in unsafe_values:
            with self.subTest(private=private):
                package = EPUB3_PACKAGE.replace(b"A Synthetic Book", private.encode("utf-8"))
                report = EpubPackageExtractor().extract(request(epub_bytes(package=package)))
                self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
                self.assertIn("unsafe_metadata_redacted", {item.code for item in report.warnings})

    def test_untrusted_xml_namespaces_are_one_way_projected_in_citations(self) -> None:
        namespaces = (
            "https://user:synthetic-secret@example.invalid/ns",
            "file:///Users/example/private",
            "urn:synthetic:spoof\u202econtrol",
            "urn:synthetic:ordinary",
        )
        for namespace in namespaces:
            with self.subTest(namespace=hashlib.sha256(namespace.encode()).hexdigest()):
                foreign = (
                    f'<x:quiet xmlns:x="{namespace}" id="bad id"/>'.encode("utf-8")
                )
                package = EPUB3_PACKAGE.replace(
                    b"</metadata>", foreign + b"</metadata>"
                )
                report = EpubPackageExtractor().extract(
                    request(epub_bytes(package=package))
                )
                warning = next(
                    item
                    for item in report.warnings
                    if item.code == "invalid_package_id" and item.citation is not None
                )
                self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
                self.assertTrue(
                    warning.citation.expanded_name.startswith("{urn:sha256:")
                )

        for foreign in (
            b'<syntheticPrivateTokenSentinel xmlns="" id="bad id"/>',
            b'<syntheticPrivateTokenSentinel id="bad id"/>',
        ):
            with self.subTest(foreign=hashlib.sha256(foreign).hexdigest()):
                package = EPUB3_PACKAGE.replace(
                    b"</metadata>", foreign + b"</metadata>"
                )
                report = EpubPackageExtractor().extract(
                    request(epub_bytes(package=package))
                )
                warning = next(
                    item
                    for item in report.warnings
                    if item.code == "invalid_package_id" and item.citation is not None
                )
                self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
                self.assertTrue(
                    warning.citation.expanded_name.startswith("{urn:sha256:")
                )

    def test_metadata_value_projection_preserves_exact_limit_and_hashes_above(self) -> None:
        maximum = EpubPackageLimits.MAX_RAW_VALUE_CHARACTERS
        for length in (maximum - 1, maximum, maximum + 1):
            with self.subTest(length=length):
                value = "x" * length
                package = EPUB3_PACKAGE.replace(
                    b"A Synthetic Book", value.encode("utf-8")
                )
                report = EpubPackageExtractor().extract(
                    request(epub_bytes(package=package))
                )
                title = next(
                    item
                    for item in report.observations
                    if item.field is EpubPackageField.TITLE
                )
                self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
                if length <= maximum:
                    self.assertEqual(title.raw_value, value)
                    self.assertNotIn(
                        "oversized_metadata_redacted",
                        {item.code for item in report.warnings},
                    )
                else:
                    self.assertNotEqual(title.raw_value, value)
                    self.assertTrue(
                        title.raw_value.startswith("oversized-source-text-sha256:")
                    )
                    self.assertIn(
                        "oversized_metadata_redacted",
                        {item.code for item in report.warnings},
                    )

    def test_warning_occurrences_are_aggregated_within_report_bound(self) -> None:
        invalid_nodes = b"".join(
            f'<meta id="bad id {ordinal}"/>'.encode("ascii")
            for ordinal in range(40)
        )
        package = EPUB3_PACKAGE.replace(
            b"</metadata>", invalid_nodes + b"</metadata>"
        )
        report = EpubPackageExtractor().extract(request(epub_bytes(package=package)))
        self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertLessEqual(len(report.warnings), EpubPackageLimits.MAX_WARNINGS)
        self.assertIn(
            "warning_occurrences_aggregated",
            {item.code for item in report.warnings},
        )
        self.assertIsNone(
            next(
                item
                for item in report.warnings
                if item.code == "invalid_package_id"
            ).citation
        )

    def test_normalization_preserves_roles_ambiguity_and_unknown_identifier_applicability(self) -> None:
        additions = b'''\n    <dc:identifier>urn:synthetic:alternate</dc:identifier>
    <dc:title>Alternate Synthetic Title</dc:title>
    <dc:contributor id="editor">E. Example</dc:contributor>
    <dc:date xmlns:opf="http://www.idpf.org/2007/opf" opf:event="publication">2025-01-01</dc:date>
    <dc:date xmlns:opf="http://www.idpf.org/2007/opf" opf:event="publication">2026-01-01</dc:date>
    <meta refines="#editor" property="role" scheme="marc:relators">edt</meta>
    <meta property="schema:bookEdition">First synthetic edition</meta>
    <meta property="schema:bookEdition">Second synthetic edition</meta>'''
        package = EPUB3_PACKAGE.replace(b"</metadata>", additions + b"\n  </metadata>")
        report = EpubPackageExtractor().extract(request(epub_bytes(package=package)))
        fields = {observation.field for observation in report.observations}
        warning_codes = {warning.code for warning in report.warnings}
        identifier_applicability = {
            observation.applicability
            for observation in report.observations
            if observation.field in {EpubPackageField.IDENTIFIER, EpubPackageField.PACKAGE_IDENTIFIER}
        }
        self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertIn(EpubPackageField.CONTRIBUTOR_ROLE, fields)
        self.assertIn(EpubObservationApplicability.UNKNOWN, identifier_applicability)
        self.assertTrue(
            {
                "ambiguous_title",
                "ambiguous_identifier",
                "ambiguous_edition",
                "contradictory_date",
                "contradictory_series",
                "identifier_applicability_unknown",
            }.issubset(warning_codes)
        )

    def test_signed_offset_publication_datetimes_are_bounded_and_event_typed(self) -> None:
        additions = b'''
    <dc:date xmlns:opf="http://www.idpf.org/2007/opf" opf:event="publication">2026-01-13T00:00:00+00:00</dc:date>
    <dc:date>2026-01-13T12:34:56.789-14:00</dc:date>
    <dc:date xmlns:opf="http://www.idpf.org/2007/opf" opf:event="publication">2026-01-13T23:59:59+14:00</dc:date>
    <dc:date xmlns:opf="http://www.idpf.org/2007/opf" opf:event="publication">2026-01-13T00:00:00Z</dc:date>'''
        package = EPUB3_PACKAGE.replace(
            b"</metadata>", additions + b"\n  </metadata>"
        )
        report = EpubPackageExtractor().extract(
            request(epub_bytes(package=package))
        )
        dates = [
            observation
            for observation in report.observations
            if observation.field is EpubPackageField.PUBLICATION_DATE
        ]

        self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertEqual(
            [observation.raw_value for observation in dates],
            [
                "2026-01-13T00:00:00+00:00",
                "2026-01-13T12:34:56.789-14:00",
                "2026-01-13T23:59:59+14:00",
                "2026-01-13T00:00:00Z",
            ],
        )
        self.assertTrue(
            all(
                observation.normalized_value == observation.raw_value
                and observation.applicability is EpubObservationApplicability.WORK
                for observation in dates
            )
        )
        self.assertEqual(
            [
                observation.normalized_value
                for observation in report.observations
                if observation.field is EpubPackageField.DATE_EVENT
            ],
            ["publication", "publication", "publication"],
        )

    def test_signed_offset_dates_reject_invalid_offsets_and_nonpublication_events(self) -> None:
        cases = (
            ("publication", "2026-01-13T00:00:00", EpubPackageField.PUBLICATION_DATE),
            ("publication", "2026-01-13T00:00:00+14:01", EpubPackageField.PUBLICATION_DATE),
            ("publication", "2026-01-13T00:00:00-14:01", EpubPackageField.PUBLICATION_DATE),
            ("publication", "2026-01-13T00:00:00+15:00", EpubPackageField.PUBLICATION_DATE),
            ("publication", "2026-02-30T00:00:00+01:00", EpubPackageField.PUBLICATION_DATE),
            ("creation", "2026-01-13T00:00:00+01:00", EpubPackageField.CREATION_DATE),
            ("modification", "2026-01-13T00:00:00+01:00", EpubPackageField.MODIFICATION_DATE),
            ("copyright", "2026-01-13T00:00:00+01:00", EpubPackageField.COPYRIGHT_DATE),
            ("future-event", "2026-01-13T00:00:00+01:00", EpubPackageField.PUBLICATION_DATE),
        )
        for event, value, field in cases:
            with self.subTest(event=event, value=value):
                addition = (
                    '<dc:date xmlns:opf="http://www.idpf.org/2007/opf" '
                    f'opf:event="{event}">{value}</dc:date>'
                ).encode()
                package = EPUB3_PACKAGE.replace(
                    b"</metadata>", addition + b"</metadata>"
                )
                report = EpubPackageExtractor().extract(
                    request(epub_bytes(package=package))
                )
                observation = next(
                    item
                    for item in report.observations
                    if item.field is field and item.raw_value == value
                )

                self.assertIsNone(observation.normalized_value)
                self.assertIs(
                    observation.applicability,
                    EpubObservationApplicability.INAPPLICABLE,
                )
                self.assertIn(
                    "invalid_date",
                    {warning.code for warning in report.warnings},
                )
                self.assertIn(
                    event,
                    {
                        item.normalized_value
                        for item in report.observations
                        if item.field is EpubPackageField.DATE_EVENT
                    },
                )

    def test_identifier_region_format_and_prefix_normalization_are_allowlisted(self) -> None:
        additions = b'''
    <dc:identifier>urn:uuid:12345678-1234-4abc-8123-123456789abc</dc:identifier>
    <dc:identifier>10.1234/synthetic.1</dc:identifier>
    <dc:identifier>ASIN:B012345678</dc:identifier>
    <dc:identifier>synthetic-proprietary-id</dc:identifier>
    <dc:identifier>9780306406158</dc:identifier>
    <dc:coverage>GB</dc:coverage>
    <meta property="schema:bookEdition">Second edition</meta>
    <meta property="schema:bookFormat">EBook</meta>
    <meta property="schema:contentLocation">Europe</meta>'''
        package = EPUB3_PACKAGE.replace(b"</metadata>", additions + b"</metadata>")
        report = EpubPackageExtractor().extract(request(epub_bytes(package=package)))
        fields = {item.field for item in report.observations}
        schemes = {
            item.normalized_value
            for item in report.observations
            if item.field is EpubPackageField.IDENTIFIER_SCHEME
        }
        self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertTrue(
            {EpubPackageField.EDITION, EpubPackageField.FORMAT, EpubPackageField.REGION}.issubset(fields)
        )
        self.assertTrue({"ISBN-13", "UUID", "DOI", "ASIN"}.issubset(schemes))
        self.assertIn("invalid_identifier", {item.code for item in report.warnings})
        self.assertIn("identifier_applicability_unknown", {item.code for item in report.warnings})

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
            with self.subTest(value=hashlib.sha256(value).hexdigest()):
                result = EpubPackageExtractor().extract(request(epub_bytes(package=value)))
                self.assertEqual(result.outcome, EpubInspectionOutcome.SUCCESS)
                self.assertNotIn(
                    EpubPackageField.EDITION,
                    {item.field for item in result.observations},
                )
                self.assertIn(
                    "unsupported_vendor_metadata",
                    {item.code for item in result.warnings},
                )
        implicit = EPUB3_PACKAGE.replace(
            b' prefix="schema: http://schema.org/"', b""
        ).replace(
            b"</metadata>",
            b'<meta property="schema:bookEdition">Reserved edition</meta></metadata>',
        )
        implicit_report = EpubPackageExtractor().extract(
            request(epub_bytes(package=implicit))
        )
        self.assertEqual(implicit_report.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertIn(
            EpubPackageField.EDITION,
            {item.field for item in implicit_report.observations},
        )

        invalid_prefix = EPUB3_PACKAGE.replace(
            b"schema: http://schema.org/", b"schema: not-an-iri"
        )
        self.assertEqual(
            EpubPackageExtractor().extract(
                request(epub_bytes(package=invalid_prefix))
            ).outcome,
            EpubInspectionOutcome.MALFORMED,
        )

    def test_repeated_calibre_values_preserve_occurrences_and_conflicts(self) -> None:
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
        self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertEqual(calibre_values, ["First Series", "Second Series", "1", "2"])
        self.assertIn("contradictory_series", {item.code for item in report.warnings})

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
        self.assertEqual(unpaired_report.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertEqual(len(positions), 1)
        self.assertIn(
            "incomplete_calibre_series",
            {item.code for item in unpaired_report.warnings},
        )

    def test_standard_metadata_refinement_and_cover_precedence_matrix(self) -> None:
        additions = b'''
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
    <meta name="cover" content="cover"/>'''
        package = EPUB3_PACKAGE.replace(b"</metadata>", additions + b"</metadata>")
        report = EpubPackageExtractor().extract(request(epub_bytes(package=package)))
        fields = {item.field for item in report.observations}
        self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertTrue(
            {
                EpubPackageField.SUBTITLE,
                EpubPackageField.PUBLISHER,
                EpubPackageField.SUBJECT,
                EpubPackageField.DESCRIPTION,
                EpubPackageField.RIGHTS,
                EpubPackageField.SOURCE,
                EpubPackageField.RELATION,
                EpubPackageField.TYPE,
            }.issubset(fields)
        )
        subtitle = [
            item
            for item in report.observations
            if item.raw_value == "Synthetic Subtitle"
        ]
        self.assertEqual(len(subtitle), 1)
        self.assertIs(subtitle[0].field, EpubPackageField.SUBTITLE)
        self.assertEqual(subtitle[0].declared_display_sequence, 2)
        self.assertIn("title-type-subtitle@", subtitle[0].citation.selector or "")
        self.assertIn("display-seq@", subtitle[0].citation.selector or "")
        base_title = next(
            item
            for item in report.observations
            if item.raw_value == "A Synthetic Book"
        )
        self.assertIsNone(base_title.declared_display_sequence)
        self.assertNotIn("contradictory_cover_declaration", {item.code for item in report.warnings})
        self.assertTrue(
            all(
                item.same_file_lineage is not None
                for item in report.observations
                if item.field in {EpubPackageField.COVER, EpubPackageField.SUBTITLE}
            )
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
        self.assertEqual(conflict_report.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertIn(
            "contradictory_cover_declaration",
            {item.code for item in conflict_report.warnings},
        )

        creator_display = EPUB3_PACKAGE.replace(
            b"<dc:creator>", b'<dc:creator id="creator">'
        ).replace(
            b"</metadata>",
            b'<meta refines="#creator" property="display-seq">2</meta></metadata>',
        )
        creator_report = EpubPackageExtractor().extract(
            request(epub_bytes(package=creator_display))
        )
        self.assertEqual(creator_report.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertIn(
            "inapplicable_metadata_refinement",
            {item.code for item in creator_report.warnings},
        )
        self.assertTrue(
            all(
                item.declared_display_sequence is None
                for item in creator_report.observations
                if item.field is EpubPackageField.CREATOR
            )
        )

    def test_title_type_and_optional_metadata_datatypes_are_local_refinements(self) -> None:
        titles = b'''
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
    <meta refines="#main-title" property="display-seq">1000000</meta>'''
        package = EPUB3_PACKAGE.replace(b"</metadata>", titles + b"</metadata>")
        report = EpubPackageExtractor().extract(request(epub_bytes(package=package)))
        by_value = {
            item.raw_value: item
            for item in report.observations
            if item.raw_value
            in {"Main", "Subtitle", "Short", "Collection", "Edition", "Expanded"}
        }
        self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertEqual(len(by_value), 6)
        self.assertIs(by_value["Main"].field, EpubPackageField.TITLE)
        self.assertIs(by_value["Subtitle"].field, EpubPackageField.SUBTITLE)
        self.assertIs(by_value["Short"].field, EpubPackageField.TITLE)
        self.assertIs(by_value["Collection"].field, EpubPackageField.COLLECTION)
        self.assertIs(by_value["Edition"].field, EpubPackageField.EDITION)
        self.assertIs(by_value["Expanded"].field, EpubPackageField.TITLE)
        self.assertEqual(by_value["Main"].declared_display_sequence, 1_000_000)

        invalid_values = (
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
                b'<dc:language>en-GB</dc:language>',
                b'<dc:language>not a language!</dc:language>',
                EpubPackageField.LANGUAGE,
                "invalid_language",
                "not a language!",
                True,
            ),
            (
                b'<dc:language>en-GB</dc:language>',
                b'<dc:language>123</dc:language>',
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
        )
        for original, replacement, field, warning, invalid_raw, retained in invalid_values:
            with self.subTest(warning=warning):
                invalid_package = EPUB3_PACKAGE.replace(original, replacement)
                invalid = EpubPackageExtractor().extract(
                    request(epub_bytes(package=invalid_package))
                )
                self.assertEqual(invalid.outcome, EpubInspectionOutcome.SUCCESS)
                self.assertIn(warning, {item.code for item in invalid.warnings})
                values = [
                    item
                    for item in invalid.observations
                    if item.field is field and item.raw_value == invalid_raw
                ]
                self.assertEqual(bool(values), retained)
                if retained:
                    self.assertTrue(
                        all(
                        item.applicability is EpubObservationApplicability.INAPPLICABLE
                        and item.normalized_value is None
                        for item in values
                        )
                    )

        for display_value, expected in (
            ("0", 0),
            ("1", 1),
            ("1000000", 1_000_000),
            ("1000001", None),
            ("4294967295", None),
        ):
            with self.subTest(display_value=display_value):
                refinement = (
                    f'<meta refines="#title" property="display-seq">'
                    f"{display_value}</meta>"
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
                self.assertEqual(display_report.outcome, EpubInspectionOutcome.SUCCESS)
                self.assertEqual(title.declared_display_sequence, expected)
                self.assertEqual(
                    "invalid_metadata_refinement"
                    in {item.code for item in display_report.warnings},
                    expected is None,
                )

    def test_invalid_language_warns_and_omits_only_the_optional_qualifier(self) -> None:
        for invalid_language in ("not a language!", "1"):
            with self.subTest(invalid_language=invalid_language):
                package = EPUB3_PACKAGE.replace(
                    b'<dc:title id="title">',
                    (
                        f'<dc:title id="title" xml:lang="{invalid_language}">'
                    ).encode(),
                )
                report = EpubPackageExtractor().extract(
                    request(epub_bytes(package=package))
                )
                self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
                self.assertIn(
                    "invalid_language", {item.code for item in report.warnings}
                )
                title = next(
                    item
                    for item in report.observations
                    if item.field is EpubPackageField.TITLE
                )
                self.assertIsNone(title.language)

    def test_multiple_renditions_entities_and_cancellation_are_typed(self) -> None:
        multiple = EpubPackageExtractor().extract(request(epub_bytes(container=CONTAINER_MULTIPLE)))
        entity = EpubPackageExtractor().extract(
            request(epub_bytes(package=b'<!DOCTYPE package [<!ENTITY x "value">]>' + EPUB3_PACKAGE))
        )
        cancelled = EpubPackageExtractor().extract(request(epub_bytes(), cancelled=True))
        self.assertEqual(multiple.outcome, EpubInspectionOutcome.UNSUPPORTED_MULTIPLE_RENDITIONS)
        self.assertEqual(entity.outcome, EpubInspectionOutcome.MALFORMED)
        self.assertEqual(cancelled.outcome, EpubInspectionOutcome.CANCELLATION)
        self.assertFalse(multiple.observations or entity.observations or cancelled.observations)

    def test_unsupported_expat_is_rejected_before_the_source_is_opened(self) -> None:
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
        self.assertEqual(report.outcome, EpubInspectionOutcome.UNAVAILABLE_SOURCE)
        self.assertEqual(reads, 0)

    def test_required_and_opaque_encryption_are_distinct(self) -> None:
        opaque = b'''<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><EncryptedData xmlns="http://www.w3.org/2001/04/xmlenc#"><CipherData><CipherReference URI="EPUB/cover.jpg"/></CipherData></EncryptedData></encryption>'''
        required = opaque.replace(b"EPUB/cover.jpg", b"EPUB/package.opf")
        opaque_report = EpubPackageExtractor().extract(request(epub_bytes(encryption=opaque)))
        required_report = EpubPackageExtractor().extract(request(epub_bytes(encryption=required)))
        self.assertEqual(opaque_report.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertIn("opaque_resource_encrypted", tuple(item.code for item in opaque_report.warnings))
        self.assertEqual(
            sum(item.kind is EpubPackageDeclarationKind.ENCRYPTION for item in opaque_report.declarations),
            1,
        )
        self.assertEqual(required_report.outcome, EpubInspectionOutcome.ENCRYPTED)
        self.assertEqual(
            EpubPackageExtractor().extract(
                request(epub_bytes(package=b"not XML", encryption=required))
            ).outcome,
            EpubInspectionOutcome.ENCRYPTED,
        )

        encrypted_key = b'''<EncryptedKey xmlns="http://www.w3.org/2001/04/xmlenc#"><CipherData><CipherValue>repository-authored-synthetic-key</CipherValue></CipherData></EncryptedKey>'''
        opaque_with_key = opaque.replace(b"<EncryptedData", encrypted_key + b"<EncryptedData")
        required_with_key = opaque_with_key.replace(
            b"EPUB/cover.jpg", b"EPUB/package.opf"
        )
        keyed_report = EpubPackageExtractor().extract(
            request(epub_bytes(encryption=opaque_with_key))
        )
        self.assertEqual(keyed_report.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertIn(
            "opaque_resource_encrypted", {item.code for item in keyed_report.warnings}
        )
        self.assertEqual(
            EpubPackageExtractor().extract(
                request(epub_bytes(encryption=required_with_key))
            ).outcome,
            EpubInspectionOutcome.ENCRYPTED,
        )

        encoded_required = required.replace(b"package.opf", b"%70ackage.opf")
        wrong_namespace = opaque.replace(
            b'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"',
            b'xmlns="urn:wrong"',
        )
        empty = b'<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"/>'
        self.assertEqual(
            EpubPackageExtractor().extract(request(epub_bytes(encryption=encoded_required))).outcome,
            EpubInspectionOutcome.ENCRYPTED,
        )
        self.assertEqual(
            EpubPackageExtractor().extract(request(epub_bytes(encryption=wrong_namespace))).outcome,
            EpubInspectionOutcome.MALFORMED,
        )
        self.assertEqual(
            EpubPackageExtractor().extract(request(epub_bytes(encryption=empty))).outcome,
            EpubInspectionOutcome.MALFORMED,
        )

        invalid_documents = (
            b'''<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><EncryptedData xmlns="http://www.w3.org/2001/04/xmlenc#"/></encryption>''',
            opaque.replace(b"<CipherData>", b"<CipherData><CipherReference URI=\"EPUB/cover.jpg\"/>", 1),
            opaque.replace(b"EPUB/cover.jpg", b"EPUB/missing.jpg"),
            opaque.replace(b"<CipherData>", b"<Unexpected>", 1).replace(
                b"</CipherData>", b"</Unexpected>", 1
            ),
        )
        for document in invalid_documents:
            with self.subTest(document=hashlib.sha256(document).hexdigest()):
                self.assertEqual(
                    EpubPackageExtractor().extract(
                        request(epub_bytes(encryption=document))
                    ).outcome,
                    EpubInspectionOutcome.MALFORMED,
                )

    def test_per_field_declaration_and_public_report_bounds_are_resource_limits(self) -> None:
        for count, outcome in (
            (32, EpubInspectionOutcome.SUCCESS),
            (33, EpubInspectionOutcome.RESOURCE_LIMIT),
        ):
            subjects = b"".join(
                f"<dc:subject>subject-{index}</dc:subject>".encode()
                for index in range(count)
            )
            package = EPUB3_PACKAGE.replace(b"</metadata>", subjects + b"</metadata>")
            with self.subTest(subjects=count):
                self.assertEqual(
                    EpubPackageExtractor().extract(
                        request(epub_bytes(package=package))
                    ).outcome,
                    outcome,
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
        self.assertEqual(declaration_report.outcome, EpubInspectionOutcome.RESOURCE_LIMIT)

        large = "x" * 1_021
        additions = []
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
        self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)

    def test_raw_zip_preflight_rejects_nul_aliases_crc_and_compression_before_xml(self) -> None:
        original = epub_bytes()
        central = original.find(b"PK\x01\x02")
        self.assertGreater(central, 0)

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
        self.assertEqual(
            results,
            (
                EpubInspectionOutcome.MALFORMED,
                EpubInspectionOutcome.UNSUPPORTED_COMPRESSION,
                EpubInspectionOutcome.INTEGRITY_FAILURE,
                EpubInspectionOutcome.MALFORMED,
            ),
        )

    def test_common_inert_zip_flags_and_directory_entries_are_tolerated(self) -> None:
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

        for method in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            with self.subTest(directory_method=method):
                directory = epub_bytes(extra_entries=(("EPUB/", b"", method),))
                self.assertEqual(
                    EpubPackageExtractor().extract(request(directory)).outcome,
                    EpubInspectionOutcome.SUCCESS,
                )

        compatible = add_flags(
            epub_bytes(extra_entries=(("EPUB/", b"", zipfile.ZIP_DEFLATED),)),
            0x16,
        )
        report = EpubPackageExtractor().extract(request(compatible))
        self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertIn("noncanonical_zip_flags", {item.code for item in report.warnings})

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
            with self.subTest(rejected_sha256=hashlib.sha256(value).hexdigest()):
                self.assertEqual(
                    EpubPackageExtractor().extract(request(value)).outcome,
                    EpubInspectionOutcome.MALFORMED,
                )

    def test_common_noncanonical_packaging_preserves_trustworthy_evidence(self) -> None:
        variants = (
            epub_bytes(mimetype_first=False),
            epub_bytes(mimetype_compression=zipfile.ZIP_DEFLATED),
            epub_bytes(mimetype_value=b"\xef\xbb\xbfapplication/epub+zip\r\n"),
        )
        for value in variants:
            with self.subTest(variant=hashlib.sha256(value).hexdigest()):
                report = EpubPackageExtractor().extract(request(value))
                self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
                self.assertIn(
                    "noncanonical_mimetype",
                    {item.code for item in report.warnings},
                )

        self.assertEqual(
            EpubPackageExtractor().extract(
                request(epub_bytes(mimetype_value=b"application/not-epub"))
            ).outcome,
            EpubInspectionOutcome.UNSUPPORTED_FORMAT,
        )

        long_description = "Synthetic description " + "x" * 6_600
        package = EPUB3_PACKAGE.replace(
            b"</metadata>",
            f"<dc:description>{long_description}</dc:description></metadata>".encode(),
        )
        report = EpubPackageExtractor().extract(request(epub_bytes(package=package)))
        self.assertEqual(report.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertIn(
            long_description,
            {
                item.raw_value
                for item in report.observations
                if item.field is EpubPackageField.DESCRIPTION
            },
        )

        legacy_version = EPUB2_PACKAGE.replace(b'version="2.0"', b'version="1.0"')
        legacy_report = EpubPackageExtractor().extract(
            request(epub_bytes(package=legacy_version))
        )
        self.assertEqual(legacy_report.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertIn(
            "noncanonical_package_version",
            {item.code for item in legacy_report.warnings},
        )

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
            with self.subTest(optional_warning=warning):
                compatible = EpubPackageExtractor().extract(
                    request(epub_bytes(package=package, extra_entries=extra_entries))
                )
                self.assertEqual(compatible.outcome, EpubInspectionOutcome.SUCCESS)
                self.assertIn(warning, {item.code for item in compatible.warnings})

        self.assertEqual(
            EpubPackageExtractor().extract(request(epub_bytes()[:-22])).outcome,
            EpubInspectionOutcome.UNSUPPORTED_FORMAT,
        )

    def test_configured_archive_xml_and_compression_bounds_fail_without_partial_success(self) -> None:
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
            request_with_limits(ratio_bomb, EpubPackageLimits(compression_ratio_per_member=2))
        )
        for report in (entry_limited, node_limited, ratio_limited):
            self.assertEqual(report.outcome, EpubInspectionOutcome.RESOURCE_LIMIT)
            self.assertFalse(report.observations or report.declarations)

        aggregate = epub_bytes(
            extra_entries=(("EPUB/large-opaque.bin", b"x" * 1_000, zipfile.ZIP_STORED),)
        )
        aggregate_report = EpubPackageExtractor().extract(
            request_with_limits(aggregate, EpubPackageLimits(expanded_bytes_per_archive=2_000))
        )
        self.assertEqual(aggregate_report.outcome, EpubInspectionOutcome.RESOURCE_LIMIT)

    def test_live_monotonic_deadline_source_io_and_allocator_failures_are_typed(self) -> None:
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
            EpubPackageSource(len(value), lambda _offset, _size: (_ for _ in ()).throw(OSError("EIO"))),
            base.expected_source,
            base.capability,
            base.limits,
            base.deadline,
            base.monotonic_deadline,
            base.cancellation,
        )
        self.assertEqual(EpubPackageExtractor().extract(late).outcome, EpubInspectionOutcome.TIMEOUT)
        self.assertEqual(EpubPackageExtractor().extract(unavailable).outcome, EpubInspectionOutcome.UNAVAILABLE_SOURCE)
        with patch(
            "mechanical_librarian.adapters.evidence_extractors.epub_package.expat.ParserCreate",
            side_effect=MemoryError,
        ):
            exhausted = EpubPackageExtractor().extract(request(value))
        self.assertEqual(exhausted.outcome, EpubInspectionOutcome.RESOURCE_LIMIT)


if __name__ == "__main__":
    unittest.main()
