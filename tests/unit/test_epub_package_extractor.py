from __future__ import annotations

import hashlib
import json
import struct
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

from mechanical_librarian.adapters.evidence_extractors.epub_package import EpubPackageExtractor
from mechanical_librarian.domain.identifiers import (
    AdapterId,
    ConnectionId,
    ExternalItemId,
    Fingerprint,
    LibraryId,
    SchemaVersion,
    TargetId,
)
from mechanical_librarian.domain.inventory import FileReference
from mechanical_librarian.domain.local_evidence import (
    DeclarationKind,
    EpubPackageLimits,
    LocalEvidenceCancellation,
    LocalEvidenceCapability,
    LocalEvidenceField,
    LocalEvidenceOutcome,
    LocalEvidenceRequest,
    LocalEvidenceSource,
    ObservationApplicability,
)
from tests.support.epub_packages import (
    CONTAINER_MULTIPLE,
    CONTAINER_ONE,
    EPUB2_PACKAGE,
    EPUB3_PACKAGE,
    epub_data_descriptor_bytes,
    epub_bytes,
    epub_zip64_bytes,
)


def request(value: bytes, *, cancelled: bool = False) -> LocalEvidenceRequest:
    return LocalEvidenceRequest(
        TargetId(AdapterId("synthetic"), ConnectionId("local"), LibraryId("library"), ExternalItemId("book")),
        FileReference("books", "generated/book.epub"),
        LocalEvidenceSource(len(value), lambda offset, size: value[offset : offset + size]),
        Fingerprint("sha256", SchemaVersion("source-v1"), hashlib.sha256(value).hexdigest()),
        LocalEvidenceCapability.PACKAGE,
        EpubPackageLimits(),
        datetime.now(timezone.utc) + timedelta(seconds=30),
        time.monotonic() + 30.0,
        LocalEvidenceCancellation("unit-request", lambda: cancelled),
    )


def request_with_limits(value: bytes, limits: EpubPackageLimits) -> LocalEvidenceRequest:
    base = request(value)
    return LocalEvidenceRequest(
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
    def test_fixture_manifest_is_closed_synthetic_and_complete(self) -> None:
        path = Path(__file__).parents[1] / "fixtures" / "epub_package" / "manifest.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(
            set(value),
            {
                "boundary_matrix",
                "cohort",
                "coverage_matrix",
                "fixtures",
                "generator_module",
                "projections",
                "schema",
            },
        )
        required = {
            "expected_exit",
            "expected_outcome",
            "expected_projection",
            "expected_projection_sha256",
            "generation_inputs",
            "generator",
            "id",
            "provenance",
            "resource_partition",
            "spdx",
        }
        self.assertGreaterEqual(len(value["fixtures"]), 8)
        self.assertEqual(len({item["id"] for item in value["fixtures"]}), len(value["fixtures"]))
        for item in value["fixtures"]:
            self.assertEqual(set(item), required)
            self.assertEqual(item["spdx"], "AGPL-3.0-or-later")
            self.assertNotIn("/Users/", repr(item))
    def test_epub3_package_is_deterministic_cited_and_does_not_retain_artifacts(self) -> None:
        value = epub_bytes()
        extractor = EpubPackageExtractor()
        first = extractor.extract(request(value))
        second = extractor.extract(request(value))

        self.assertEqual(first.outcome, LocalEvidenceOutcome.SUCCESS)
        self.assertEqual(first.to_json(), second.to_json())
        self.assertIsNone(first.artifact)
        self.assertEqual(first.source_fingerprint.digest, hashlib.sha256(value).hexdigest())
        self.assertTrue({LocalEvidenceField.TITLE, LocalEvidenceField.CREATOR, LocalEvidenceField.PACKAGE_IDENTIFIER, LocalEvidenceField.COVER}.issubset({item.field for item in first.observations}))
        self.assertEqual(sum(item.kind is DeclarationKind.NAVIGATION for item in first.declarations), 1)
        self.assertEqual(sum(item.kind is DeclarationKind.MANIFEST_ITEM for item in first.declarations), 3)
        serialized = first.to_json()
        self.assertNotIn("chapter.xhtml", serialized)
        self.assertNotIn("cover.jpg", serialized)
        self.assertNotIn("TRAP", serialized)
        self.assertIn("unsupported_vendor_metadata", serialized)

    def test_bounded_producer_forced_zip64_local_headers_are_supported(self) -> None:
        report = EpubPackageExtractor().extract(request(epub_zip64_bytes()))
        self.assertEqual(report.outcome, LocalEvidenceOutcome.SUCCESS)

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

        self.assertEqual(valid.outcome, LocalEvidenceOutcome.SUCCESS)
        self.assertEqual(
            EpubPackageExtractor().extract(request(bytes(corrupted))).outcome,
            LocalEvidenceOutcome.INTEGRITY_FAILURE,
        )
        self.assertEqual(
            EpubPackageExtractor().extract(request(bytes(unsafe_flag))).outcome,
            LocalEvidenceOutcome.MALFORMED,
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
                    LocalEvidenceOutcome.SUCCESS,
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
                    LocalEvidenceOutcome.INTEGRITY_FAILURE,
                )

        invalid_version = bytearray(described)
        struct.pack_into("<H", invalid_version, second_local + 4, 0)
        struct.pack_into("<H", invalid_version, second_central + 6, 0)
        self.assertEqual(
            EpubPackageExtractor().extract(request(bytes(invalid_version))).outcome,
            LocalEvidenceOutcome.MALFORMED,
        )

    def test_epub2_preserves_legacy_declarations_without_dereferencing(self) -> None:
        report = EpubPackageExtractor().extract(request(epub_bytes(version=2)))
        self.assertEqual(report.outcome, LocalEvidenceOutcome.SUCCESS)
        self.assertIn(DeclarationKind.NCX, {item.kind for item in report.declarations})
        self.assertIn(DeclarationKind.GUIDE_REFERENCE, {item.kind for item in report.declarations})
        self.assertIn(LocalEvidenceField.PUBLICATION_DATE, {item.field for item in report.observations})
        self.assertIn(LocalEvidenceField.CREATOR_ROLE, {item.field for item in report.observations})
        self.assertIn(LocalEvidenceField.SORT_KEY, {item.field for item in report.observations})
        self.assertIn(LocalEvidenceField.IDENTIFIER_SCHEME, {item.field for item in report.observations})
        primary = next(
            item
            for item in report.observations
            if item.field is LocalEvidenceField.PACKAGE_IDENTIFIER
        )
        self.assertEqual(primary.raw_value, "0-306-40615-2")
        self.assertEqual(primary.normalized_value, "0306406152")
        self.assertIs(primary.applicability, ObservationApplicability.UNKNOWN)

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
        self.assertEqual(toured.outcome, LocalEvidenceOutcome.SUCCESS)
        self.assertIn("deprecated_tours", {item.code for item in toured.warnings})
        self.assertNotIn("Synthetic route", toured.to_json())
        self.assertNotIn("Synthetic stop", toured.to_json())

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
                self.assertEqual(invalid_tour_report.outcome, LocalEvidenceOutcome.SUCCESS)
                self.assertIn(
                    "invalid_deprecated_tours",
                    {item.code for item in invalid_tour_report.warnings},
                )

    def test_remote_manifest_resource_is_hashed_and_never_fetched_or_disclosed(self) -> None:
        package = EPUB3_PACKAGE.replace(
            b"</manifest>",
            b'<item id="remote" href="https://catalogue.example/private.xml" media-type="application/xml"/></manifest>',
        )
        report = EpubPackageExtractor().extract(request(epub_bytes(package=package)))
        self.assertEqual(report.outcome, LocalEvidenceOutcome.SUCCESS)
        self.assertNotIn("catalogue.example", report.to_json())
        self.assertEqual(sum(item.kind is DeclarationKind.MANIFEST_ITEM for item in report.declarations), 4)

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
                LocalEvidenceOutcome.MALFORMED,
                LocalEvidenceOutcome.MALFORMED,
                LocalEvidenceOutcome.MALFORMED,
                LocalEvidenceOutcome.SUCCESS,
                LocalEvidenceOutcome.SUCCESS,
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
            duplicate_primary_report.outcome, LocalEvidenceOutcome.SUCCESS
        )
        self.assertIn(
            "ambiguous_package_id",
            {item.code for item in duplicate_primary_report.warnings},
        )
        self.assertNotIn(
            LocalEvidenceField.PACKAGE_IDENTIFIER,
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
            LocalEvidenceField.PACKAGE_IDENTIFIER,
            {item.field for item in invalid_primary_report.observations},
        )

        duplicate_manifest_id = EPUB3_PACKAGE.replace(
            b'<item id="nav"', b'<item id="chapter"', 1
        )
        duplicate_manifest_report = EpubPackageExtractor().extract(
            request(epub_bytes(package=duplicate_manifest_id))
        )
        self.assertEqual(
            duplicate_manifest_report.outcome, LocalEvidenceOutcome.SUCCESS
        )
        self.assertFalse(
            any(
                item.identifier == "chapter"
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
            if item.field is LocalEvidenceField.COVER
        )
        cover_declaration = next(
            item
            for item in report.declarations
            if item.kind is DeclarationKind.COVER
        )
        self.assertEqual(report.outcome, LocalEvidenceOutcome.SUCCESS)
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
                self.assertEqual(report.outcome, LocalEvidenceOutcome.SUCCESS)
                self.assertIn(
                    "invalid_guide_reference",
                    {item.code for item in report.warnings},
                )
                self.assertFalse(
                    any(
                        item.kind is DeclarationKind.GUIDE_REFERENCE
                        for item in report.declarations
                    )
                )

    def test_repeated_legacy_cover_declarations_preserve_agreement_and_conflict(self) -> None:
        cover_meta = b'<meta name="cover" content="cover"/>'
        agreeing_package = EPUB2_PACKAGE.replace(
            cover_meta, cover_meta + cover_meta
        )
        agreeing = EpubPackageExtractor().extract(
            request(epub_bytes(version=2, package=agreeing_package))
        )
        self.assertEqual(agreeing.outcome, LocalEvidenceOutcome.SUCCESS)
        self.assertNotIn(
            "contradictory_cover_declaration",
            {item.code for item in agreeing.warnings},
        )
        self.assertEqual(
            sum(
                item.field is LocalEvidenceField.COVER
                for item in agreeing.observations
            ),
            1,
        )

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
        self.assertEqual(conflicting.outcome, LocalEvidenceOutcome.SUCCESS)
        self.assertIn(
            "contradictory_cover_declaration",
            {item.code for item in conflicting.warnings},
        )
        self.assertNotIn(
            LocalEvidenceField.COVER,
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
        self.assertEqual(current_conflict.outcome, LocalEvidenceOutcome.SUCCESS)
        self.assertIn(
            "contradictory_cover_declaration",
            {item.code for item in current_conflict.warnings},
        )
        self.assertNotIn(
            LocalEvidenceField.COVER,
            {item.field for item in current_conflict.observations},
        )

    def test_hostile_path_reference_matrix_is_typed_and_never_resolved_externally(self) -> None:
        references = {
            "/absolute.xhtml": LocalEvidenceOutcome.MALFORMED,
            "../../escape.xhtml": LocalEvidenceOutcome.MALFORMED,
            "chapter%2Fxhtml": LocalEvidenceOutcome.MALFORMED,
            "chapter\\xhtml": LocalEvidenceOutcome.MALFORMED,
            "file:///private/book.xhtml": LocalEvidenceOutcome.MALFORMED,
            "https://user@example.invalid/private": LocalEvidenceOutcome.MALFORMED,
            "x" * 1_025: LocalEvidenceOutcome.RESOURCE_LIMIT,
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
            LocalEvidenceOutcome.ENCRYPTED,
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
                    LocalEvidenceOutcome.MALFORMED,
                )
        xinclude = EPUB3_PACKAGE.replace(
            b"</metadata>",
            b'<xi:include xmlns:xi="http://www.w3.org/2001/XInclude" href="file:///private/value"/></metadata>',
        )
        self.assertEqual(
            EpubPackageExtractor().extract(
                request(epub_bytes(package=xinclude))
            ).outcome,
            LocalEvidenceOutcome.MALFORMED,
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
                    LocalEvidenceOutcome.MALFORMED,
                )
        for package, warning in (
            (missing_target, "unresolved_metadata_refinement"),
            (wrong_target, "inapplicable_metadata_refinement"),
        ):
            with self.subTest(compatible_package=hashlib.sha256(package).hexdigest()):
                report = EpubPackageExtractor().extract(
                    request(epub_bytes(package=package))
                )
                self.assertEqual(report.outcome, LocalEvidenceOutcome.SUCCESS)
                self.assertIn(warning, {item.code for item in report.warnings})
        trailing_cases = (
            (b'<collection role="index"/>', LocalEvidenceOutcome.SUCCESS),
            (
                b'<bindings><mediaType media-type="application/x-synthetic" handler="chapter"/></bindings>'
                b'<collection role="index"/>',
                LocalEvidenceOutcome.SUCCESS,
            ),
            (
                b'<guide><reference type="text" title="Start" href="chapter.xhtml"/></guide>'
                b'<bindings><mediaType media-type="application/x-synthetic" handler="chapter"/></bindings>'
                b'<collection role="index"/><collection role="preview"/>',
                LocalEvidenceOutcome.SUCCESS,
            ),
            (
                b'<collection role="index"/>'
                b'<bindings><mediaType media-type="application/x-synthetic" handler="chapter"/></bindings>',
                LocalEvidenceOutcome.MALFORMED,
            ),
            (
                b'<bindings><mediaType media-type="application/x-synthetic" handler="chapter"/></bindings>'
                b'<guide><reference type="text" title="Start" href="chapter.xhtml"/></guide>',
                LocalEvidenceOutcome.MALFORMED,
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
            LocalEvidenceOutcome.SUCCESS,
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
                serialized = report.to_json()
                self.assertEqual(report.outcome, LocalEvidenceOutcome.SUCCESS)
                self.assertNotIn(private, serialized)
                self.assertIn("unsafe-source-text-sha256", serialized)
                self.assertIn("unsafe_metadata_redacted", serialized)

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
                serialized = report.to_json()
                warning = next(
                    item
                    for item in report.warnings
                    if item.code == "invalid_package_id" and item.citation is not None
                )
                self.assertEqual(report.outcome, LocalEvidenceOutcome.SUCCESS)
                self.assertNotIn(namespace, serialized)
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
                self.assertEqual(report.outcome, LocalEvidenceOutcome.SUCCESS)
                self.assertNotIn("syntheticPrivateTokenSentinel", report.to_json())
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
                    if item.field is LocalEvidenceField.TITLE
                )
                self.assertEqual(report.outcome, LocalEvidenceOutcome.SUCCESS)
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
                    self.assertNotIn(value, report.to_json())
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
        self.assertEqual(report.outcome, LocalEvidenceOutcome.SUCCESS)
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
            if observation.field in {LocalEvidenceField.IDENTIFIER, LocalEvidenceField.PACKAGE_IDENTIFIER}
        }
        self.assertEqual(report.outcome, LocalEvidenceOutcome.SUCCESS)
        self.assertIn(LocalEvidenceField.CONTRIBUTOR_ROLE, fields)
        self.assertIn(ObservationApplicability.UNKNOWN, identifier_applicability)
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
            if item.field is LocalEvidenceField.IDENTIFIER_SCHEME
        }
        self.assertEqual(report.outcome, LocalEvidenceOutcome.SUCCESS)
        self.assertTrue(
            {LocalEvidenceField.EDITION, LocalEvidenceField.FORMAT, LocalEvidenceField.REGION}.issubset(fields)
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
                self.assertEqual(result.outcome, LocalEvidenceOutcome.SUCCESS)
                self.assertNotIn(
                    LocalEvidenceField.EDITION,
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
        self.assertEqual(implicit_report.outcome, LocalEvidenceOutcome.SUCCESS)
        self.assertIn(
            LocalEvidenceField.EDITION,
            {item.field for item in implicit_report.observations},
        )

        invalid_prefix = EPUB3_PACKAGE.replace(
            b"schema: http://schema.org/", b"schema: not-an-iri"
        )
        self.assertEqual(
            EpubPackageExtractor().extract(
                request(epub_bytes(package=invalid_prefix))
            ).outcome,
            LocalEvidenceOutcome.MALFORMED,
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
        self.assertEqual(report.outcome, LocalEvidenceOutcome.SUCCESS)
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
            if item.field is LocalEvidenceField.COLLECTION_POSITION
            and item.citation.selector == "calibre-fallback"
        ]
        self.assertEqual(unpaired_report.outcome, LocalEvidenceOutcome.SUCCESS)
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
        self.assertEqual(report.outcome, LocalEvidenceOutcome.SUCCESS)
        self.assertTrue(
            {
                LocalEvidenceField.SUBTITLE,
                LocalEvidenceField.PUBLISHER,
                LocalEvidenceField.SUBJECT,
                LocalEvidenceField.DESCRIPTION,
                LocalEvidenceField.RIGHTS,
                LocalEvidenceField.SOURCE,
                LocalEvidenceField.RELATION,
                LocalEvidenceField.TYPE,
            }.issubset(fields)
        )
        self.assertNotIn("contradictory_cover_declaration", {item.code for item in report.warnings})
        self.assertTrue(
            all(
                item.same_file_lineage is not None
                for item in report.observations
                if item.field in {LocalEvidenceField.COVER, LocalEvidenceField.SUBTITLE}
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
        self.assertEqual(conflict_report.outcome, LocalEvidenceOutcome.SUCCESS)
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
        self.assertEqual(creator_report.outcome, LocalEvidenceOutcome.SUCCESS)
        self.assertIn(
            "inapplicable_metadata_refinement",
            {item.code for item in creator_report.warnings},
        )
        self.assertTrue(
            all(
                item.declared_display_sequence is None
                for item in creator_report.observations
                if item.field is LocalEvidenceField.CREATOR
            )
        )

    def test_invalid_language_is_malformed_not_a_resource_failure(self) -> None:
        package = EPUB3_PACKAGE.replace(
            b'<dc:title id="title">',
            b'<dc:title id="title" xml:lang="not a language!">',
        )
        report = EpubPackageExtractor().extract(request(epub_bytes(package=package)))
        self.assertEqual(report.outcome, LocalEvidenceOutcome.MALFORMED)

    def test_multiple_renditions_entities_and_cancellation_are_typed(self) -> None:
        multiple = EpubPackageExtractor().extract(request(epub_bytes(container=CONTAINER_MULTIPLE)))
        entity = EpubPackageExtractor().extract(
            request(epub_bytes(package=b'<!DOCTYPE package [<!ENTITY x "value">]>' + EPUB3_PACKAGE))
        )
        cancelled = EpubPackageExtractor().extract(request(epub_bytes(), cancelled=True))
        self.assertEqual(multiple.outcome, LocalEvidenceOutcome.UNSUPPORTED_MULTIPLE_RENDITIONS)
        self.assertEqual(entity.outcome, LocalEvidenceOutcome.MALFORMED)
        self.assertEqual(cancelled.outcome, LocalEvidenceOutcome.CANCELLATION)
        self.assertFalse(multiple.observations or entity.observations or cancelled.observations)

    def test_unsupported_expat_is_rejected_before_the_source_is_opened(self) -> None:
        value = epub_bytes()
        reads = 0

        def reader(offset: int, size: int) -> bytes:
            nonlocal reads
            reads += 1
            return value[offset : offset + size]

        base = request(value)
        guarded = LocalEvidenceRequest(
            base.target,
            base.file,
            LocalEvidenceSource(len(value), reader),
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
        self.assertEqual(report.outcome, LocalEvidenceOutcome.UNAVAILABLE_SOURCE)
        self.assertEqual(reads, 0)

    def test_required_and_opaque_encryption_are_distinct(self) -> None:
        opaque = b'''<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><EncryptedData xmlns="http://www.w3.org/2001/04/xmlenc#"><CipherData><CipherReference URI="EPUB/cover.jpg"/></CipherData></EncryptedData></encryption>'''
        required = opaque.replace(b"EPUB/cover.jpg", b"EPUB/package.opf")
        opaque_report = EpubPackageExtractor().extract(request(epub_bytes(encryption=opaque)))
        required_report = EpubPackageExtractor().extract(request(epub_bytes(encryption=required)))
        self.assertEqual(opaque_report.outcome, LocalEvidenceOutcome.SUCCESS)
        self.assertIn("opaque_resource_encrypted", tuple(item.code for item in opaque_report.warnings))
        self.assertEqual(
            sum(item.kind is DeclarationKind.ENCRYPTION for item in opaque_report.declarations),
            1,
        )
        self.assertEqual(required_report.outcome, LocalEvidenceOutcome.ENCRYPTED)

        encrypted_key = b'''<EncryptedKey xmlns="http://www.w3.org/2001/04/xmlenc#"><CipherData><CipherValue>repository-authored-synthetic-key</CipherValue></CipherData></EncryptedKey>'''
        opaque_with_key = opaque.replace(b"<EncryptedData", encrypted_key + b"<EncryptedData")
        required_with_key = opaque_with_key.replace(
            b"EPUB/cover.jpg", b"EPUB/package.opf"
        )
        keyed_report = EpubPackageExtractor().extract(
            request(epub_bytes(encryption=opaque_with_key))
        )
        self.assertEqual(keyed_report.outcome, LocalEvidenceOutcome.SUCCESS)
        self.assertIn(
            "opaque_resource_encrypted", {item.code for item in keyed_report.warnings}
        )
        self.assertNotIn("synthetic-key", keyed_report.to_json())
        self.assertEqual(
            EpubPackageExtractor().extract(
                request(epub_bytes(encryption=required_with_key))
            ).outcome,
            LocalEvidenceOutcome.ENCRYPTED,
        )

        encoded_required = required.replace(b"package.opf", b"%70ackage.opf")
        wrong_namespace = opaque.replace(
            b'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"',
            b'xmlns="urn:wrong"',
        )
        empty = b'<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"/>'
        self.assertEqual(
            EpubPackageExtractor().extract(request(epub_bytes(encryption=encoded_required))).outcome,
            LocalEvidenceOutcome.ENCRYPTED,
        )
        self.assertEqual(
            EpubPackageExtractor().extract(request(epub_bytes(encryption=wrong_namespace))).outcome,
            LocalEvidenceOutcome.MALFORMED,
        )
        self.assertEqual(
            EpubPackageExtractor().extract(request(epub_bytes(encryption=empty))).outcome,
            LocalEvidenceOutcome.MALFORMED,
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
                    LocalEvidenceOutcome.MALFORMED,
                )

    def test_per_field_declaration_and_public_report_bounds_are_resource_limits(self) -> None:
        for count, outcome in (
            (32, LocalEvidenceOutcome.SUCCESS),
            (33, LocalEvidenceOutcome.RESOURCE_LIMIT),
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
        self.assertEqual(declaration_report.outcome, LocalEvidenceOutcome.RESOURCE_LIMIT)

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
        self.assertEqual(report.outcome, LocalEvidenceOutcome.RESOURCE_LIMIT)

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
                LocalEvidenceOutcome.MALFORMED,
                LocalEvidenceOutcome.UNSUPPORTED_COMPRESSION,
                LocalEvidenceOutcome.INTEGRITY_FAILURE,
                LocalEvidenceOutcome.MALFORMED,
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
                    LocalEvidenceOutcome.SUCCESS,
                )

        compatible = add_flags(
            epub_bytes(extra_entries=(("EPUB/", b"", zipfile.ZIP_DEFLATED),)),
            0x16,
        )
        report = EpubPackageExtractor().extract(request(compatible))
        self.assertEqual(report.outcome, LocalEvidenceOutcome.SUCCESS)
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
                    LocalEvidenceOutcome.MALFORMED,
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
                self.assertEqual(report.outcome, LocalEvidenceOutcome.SUCCESS)
                self.assertIn(
                    "noncanonical_mimetype",
                    {item.code for item in report.warnings},
                )

        self.assertEqual(
            EpubPackageExtractor().extract(
                request(epub_bytes(mimetype_value=b"application/not-epub"))
            ).outcome,
            LocalEvidenceOutcome.UNSUPPORTED_FORMAT,
        )

        long_description = "Synthetic description " + "x" * 6_600
        package = EPUB3_PACKAGE.replace(
            b"</metadata>",
            f"<dc:description>{long_description}</dc:description></metadata>".encode(),
        )
        report = EpubPackageExtractor().extract(request(epub_bytes(package=package)))
        self.assertEqual(report.outcome, LocalEvidenceOutcome.SUCCESS)
        self.assertIn(
            long_description,
            {
                item.raw_value
                for item in report.observations
                if item.field is LocalEvidenceField.DESCRIPTION
            },
        )

        legacy_version = EPUB2_PACKAGE.replace(b'version="2.0"', b'version="1.0"')
        legacy_report = EpubPackageExtractor().extract(
            request(epub_bytes(package=legacy_version))
        )
        self.assertEqual(legacy_report.outcome, LocalEvidenceOutcome.SUCCESS)
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
                self.assertEqual(compatible.outcome, LocalEvidenceOutcome.SUCCESS)
                self.assertIn(warning, {item.code for item in compatible.warnings})

        self.assertEqual(
            EpubPackageExtractor().extract(request(epub_bytes()[:-22])).outcome,
            LocalEvidenceOutcome.UNSUPPORTED_FORMAT,
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
            self.assertEqual(report.outcome, LocalEvidenceOutcome.RESOURCE_LIMIT)
            self.assertFalse(report.observations or report.declarations)

        aggregate = epub_bytes(
            extra_entries=(("EPUB/large-opaque.bin", b"x" * 1_000, zipfile.ZIP_STORED),)
        )
        aggregate_report = EpubPackageExtractor().extract(
            request_with_limits(aggregate, EpubPackageLimits(expanded_bytes_per_archive=2_000))
        )
        self.assertEqual(aggregate_report.outcome, LocalEvidenceOutcome.RESOURCE_LIMIT)

    def test_live_monotonic_deadline_source_io_and_allocator_failures_are_typed(self) -> None:
        value = epub_bytes()
        base = request(value)

        def delayed(offset: int, size: int) -> bytes:
            time.sleep(0.01)
            return value[offset : offset + size]

        late = LocalEvidenceRequest(
            base.target,
            base.file,
            LocalEvidenceSource(len(value), delayed),
            base.expected_source,
            base.capability,
            base.limits,
            base.deadline,
            time.monotonic() + 0.001,
            base.cancellation,
        )
        unavailable = LocalEvidenceRequest(
            base.target,
            base.file,
            LocalEvidenceSource(len(value), lambda _offset, _size: (_ for _ in ()).throw(OSError("EIO"))),
            base.expected_source,
            base.capability,
            base.limits,
            base.deadline,
            base.monotonic_deadline,
            base.cancellation,
        )
        self.assertEqual(EpubPackageExtractor().extract(late).outcome, LocalEvidenceOutcome.TIMEOUT)
        self.assertEqual(EpubPackageExtractor().extract(unavailable).outcome, LocalEvidenceOutcome.UNAVAILABLE_SOURCE)
        with patch(
            "mechanical_librarian.adapters.evidence_extractors.epub_package.expat.ParserCreate",
            side_effect=MemoryError,
        ):
            exhausted = EpubPackageExtractor().extract(request(value))
        self.assertEqual(exhausted.outcome, LocalEvidenceOutcome.RESOURCE_LIMIT)


if __name__ == "__main__":
    unittest.main()
