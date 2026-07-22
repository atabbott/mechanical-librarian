from __future__ import annotations

import hashlib
import importlib
import json
import struct
import time
import unittest
import zipfile
from xml.parsers import expat
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from mechanical_librarian.adapters.evidence_extractors.epub_package import (
    EpubPackageExtractor,
)
from mechanical_librarian.domain.evidence_bounds import EvidenceValueError
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
from mechanical_librarian.adapters.evidence_extractors.epub_package_model import (
    EpubPackageLimits,
    EpubPackageCancellation,
    EpubPackageCapability,
    EpubInspectionOutcome,
    EpubPackageReport,
    EpubPackageRequest,
    EpubPackageSource,
)
from mechanical_librarian.interfaces.cli import _EPUB_PACKAGE_EXIT
from tests.support.epub_packages import (
    CONTAINER_MULTIPLE,
    CONTAINER_ONE,
    EPUB2_PACKAGE,
    EPUB3_PACKAGE,
    MATERIALIZED_RECIPES,
    epub_bytes,
    epub_data_descriptor_bytes,
    epub_zip64_bytes,
)


MANIFEST = (
    Path(__file__).parents[1] / "fixtures" / "epub_package" / "manifest.json"
)
QUALIFICATION = (
    Path(__file__).parents[2]
    / "docs"
    / "reference"
    / "epub-package-f06-qualification.json"
)
UNSAFE_METADATA = "Authorization: Bearer synthetic-token-1234"


@dataclass(frozen=True)
class ExecutableScenario:
    partition: str
    build: Callable[[], tuple[bytes, EpubPackageLimits, bool]]


@dataclass(frozen=True)
class ExistingEvidence:
    partition: str
    test_id: str
    outcome: EpubInspectionOutcome


def _scenario(
    partition: str,
    build: Callable[[], tuple[bytes, EpubPackageLimits, bool]],
) -> ExecutableScenario:
    return ExecutableScenario(partition, build)


def _ordinary(value: bytes) -> tuple[bytes, EpubPackageLimits, bool]:
    return value, EpubPackageLimits(), False


def generate_epub3() -> tuple[bytes, EpubPackageLimits, bool]:
    return _ordinary(epub_bytes())


def generate_opf2() -> tuple[bytes, EpubPackageLimits, bool]:
    return _ordinary(epub_bytes(version=2))


def generate_stored() -> tuple[bytes, EpubPackageLimits, bool]:
    return _ordinary(epub_bytes(compression=zipfile.ZIP_STORED))


def generate_zip64() -> tuple[bytes, EpubPackageLimits, bool]:
    return _ordinary(epub_zip64_bytes())


def generate_data_descriptors() -> tuple[bytes, EpubPackageLimits, bool]:
    return _ordinary(epub_data_descriptor_bytes())


def generate_multiple_renditions() -> tuple[bytes, EpubPackageLimits, bool]:
    return _ordinary(epub_bytes(container=CONTAINER_MULTIPLE))


def _required_encryption() -> bytes:
    return b'''<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><EncryptedData xmlns="http://www.w3.org/2001/04/xmlenc#"><CipherData><CipherReference URI="EPUB/package.opf"/></CipherData></EncryptedData></encryption>'''


def generate_required_encryption() -> tuple[bytes, EpubPackageLimits, bool]:
    return _ordinary(epub_bytes(encryption=_required_encryption()))


def generate_opaque_encryption() -> tuple[bytes, EpubPackageLimits, bool]:
    return _ordinary(
        epub_bytes(
            encryption=_required_encryption().replace(
                b"EPUB/package.opf", b"EPUB/cover.jpg"
            )
        )
    )


def generate_percent_alias_encryption() -> tuple[bytes, EpubPackageLimits, bool]:
    return _ordinary(
        epub_bytes(encryption=_required_encryption().replace(b"package.opf", b"%70ackage.opf"))
    )


def mutate_foreign_container_namespace() -> tuple[bytes, EpubPackageLimits, bool]:
    container = CONTAINER_ONE.replace(
        b"<rootfiles>", b'<evil:rootfiles xmlns:evil="urn:evil">'
    ).replace(b"</rootfiles>", b"</evil:rootfiles>")
    return _ordinary(epub_bytes(container=container))


def mutate_duplicate_global_id() -> tuple[bytes, EpubPackageLimits, bool]:
    package = EPUB3_PACKAGE.replace(b'<item id="nav"', b'<item id="title"', 1)
    return _ordinary(epub_bytes(package=package))


def mutate_doctype_entity() -> tuple[bytes, EpubPackageLimits, bool]:
    package = b'<!DOCTYPE package [<!ENTITY x "value">]>' + EPUB3_PACKAGE
    return _ordinary(epub_bytes(package=package))


def mutate_unsafe_zip_flag() -> tuple[bytes, EpubPackageLimits, bool]:
    value = bytearray(epub_bytes())
    central = value.find(b"PK\x01\x02")
    local = value.find(b"PK\x03\x04")
    struct.pack_into("<H", value, central + 8, struct.unpack_from("<H", value, central + 8)[0] | 0x20)
    struct.pack_into("<H", value, local + 6, struct.unpack_from("<H", value, local + 6)[0] | 0x20)
    return _ordinary(bytes(value))


def mutate_required_member_crc() -> tuple[bytes, EpubPackageLimits, bool]:
    value = bytearray(epub_bytes())
    central = value.find(b"PK\x01\x02")
    struct.pack_into("<L", value, central + 16, 0)
    return _ordinary(bytes(value))


def limit_per_member_expansion() -> tuple[bytes, EpubPackageLimits, bool]:
    return epub_bytes(), EpubPackageLimits(expanded_bytes_per_member=100), False


def limit_aggregate_expansion() -> tuple[bytes, EpubPackageLimits, bool]:
    value = epub_bytes(
        extra_entries=(("EPUB/large-opaque.bin", b"x" * 1_000, zipfile.ZIP_STORED),)
    )
    return value, EpubPackageLimits(expanded_bytes_per_archive=2_000), False


def limit_compression_ratio() -> tuple[bytes, EpubPackageLimits, bool]:
    value = epub_bytes(
        extra_entries=(("EPUB/opaque.bin", b"0" * 100_000, zipfile.ZIP_DEFLATED),)
    )
    return value, EpubPackageLimits(compression_ratio_per_member=2), False


def generate_unsafe_metadata() -> tuple[bytes, EpubPackageLimits, bool]:
    package = EPUB3_PACKAGE.replace(b"A Synthetic Book", UNSAFE_METADATA.encode("utf-8"))
    return _ordinary(epub_bytes(package=package))


def generate_metadata_matrix() -> tuple[bytes, EpubPackageLimits, bool]:
    additions = b'''
    <dc:title id="subtitle">Synthetic Subtitle</dc:title>
    <dc:contributor id="editor">E. Example</dc:contributor>
    <dc:publisher>Example Publisher</dc:publisher>
    <dc:subject>Example Subject</dc:subject>
    <dc:description>Example Description</dc:description>
    <dc:rights>Example Rights</dc:rights>
    <dc:source>Example Source</dc:source>
    <dc:relation>Example Relation</dc:relation>
    <dc:type>Text</dc:type>
    <dc:coverage>GB</dc:coverage>
    <meta refines="#subtitle" property="title-type">subtitle</meta>
    <meta refines="#subtitle" property="display-seq">2</meta>
    <meta refines="#editor" property="role">edt</meta>
    <meta property="schema:bookEdition">Second edition</meta>
    <meta property="schema:bookFormat">EBook</meta>
    <meta property="schema:contentLocation">Europe</meta>
    <meta name="cover" content="cover"/>'''
    package = EPUB3_PACKAGE.replace(b"</metadata>", additions + b"</metadata>")
    return _ordinary(epub_bytes(package=package))


def generate_identifier_matrix() -> tuple[bytes, EpubPackageLimits, bool]:
    additions = b'''
    <dc:identifier>urn:uuid:12345678-1234-4abc-8123-123456789abc</dc:identifier>
    <dc:identifier>10.1234/synthetic.1</dc:identifier>
    <dc:identifier>ASIN:B012345678</dc:identifier>
    <dc:identifier>synthetic-proprietary-id</dc:identifier>
    <dc:identifier>9780306406158</dc:identifier>'''
    package = EPUB3_PACKAGE.replace(b"</metadata>", additions + b"</metadata>")
    return _ordinary(epub_bytes(package=package))


def generate_calibre_conflict() -> tuple[bytes, EpubPackageLimits, bool]:
    package = EPUB3_PACKAGE.replace(
        b'<meta name="calibre:series" content="Lower Priority Series"/>',
        b'<meta name="calibre:series" content="First Series"/>'
        b'<meta name="calibre:series" content="Second Series"/>',
    ).replace(
        b'<meta name="calibre:series_index" content="9"/>',
        b'<meta name="calibre:series_index" content="1"/>'
        b'<meta name="calibre:series_index" content="2"/>',
    )
    return _ordinary(epub_bytes(package=package))


DISPATCH: dict[str, ExecutableScenario | ExistingEvidence] = {
    "generate_epub3": _scenario("ordinary-epub3", generate_epub3),
    "generate_opf2": _scenario("ordinary-opf2", generate_opf2),
    "generate_stored": _scenario("zip-stored", generate_stored),
    "generate_zip64": _scenario("zip64", generate_zip64),
    "generate_data_descriptors": _scenario("zip-data-descriptor", generate_data_descriptors),
    "generate_multiple_renditions": _scenario("rootfile-count", generate_multiple_renditions),
    "generate_required_encryption": _scenario("encryption-required", generate_required_encryption),
    "generate_opaque_encryption": _scenario("encryption-opaque", generate_opaque_encryption),
    "generate_percent_alias_encryption": _scenario("encryption-percent-alias", generate_percent_alias_encryption),
    "mutate_foreign_container_namespace": _scenario("xml-namespace", mutate_foreign_container_namespace),
    "mutate_duplicate_global_id": _scenario("opf-graph-id", mutate_duplicate_global_id),
    "mutate_doctype_entity": _scenario("xml-entity", mutate_doctype_entity),
    "mutate_unsafe_zip_flag": _scenario("zip-flag", mutate_unsafe_zip_flag),
    "mutate_required_member_crc": _scenario("zip-crc", mutate_required_member_crc),
    "limit_per_member_expansion": _scenario("expanded-bytes-per-member", limit_per_member_expansion),
    "limit_aggregate_expansion": _scenario("expanded-bytes-per-archive", limit_aggregate_expansion),
    "limit_compression_ratio": _scenario("compression-ratio", limit_compression_ratio),
    "generate_unsafe_metadata": _scenario("metadata-privacy", generate_unsafe_metadata),
    "generate_metadata_matrix": _scenario("metadata-standard-matrix", generate_metadata_matrix),
    "generate_identifier_matrix": _scenario("metadata-identifier-matrix", generate_identifier_matrix),
    "generate_calibre_conflict": _scenario("metadata-calibre-conflict", generate_calibre_conflict),
    "evidence_process_cancellation": ExistingEvidence(
        "process-cancellation",
        "tests.integration.test_epub_opening_report.EpubOpeningProcessTests.test_isolated_child_succeeds_and_cancelled_sibling_does_not_poison_next_run",
        EpubInspectionOutcome.CANCELLATION,
    ),
    "evidence_process_timeout": ExistingEvidence(
        "process-timeout",
        "tests.integration.test_epub_opening_report.EpubOpeningProcessTests.test_timeout_and_output_overflow_discard_partial_state_and_leave_a_usable_sibling",
        EpubInspectionOutcome.TIMEOUT,
    ),
    "evidence_child_crash": ExistingEvidence(
        "process-crash",
        "tests.integration.test_epub_opening_report.EpubOpeningProcessTests.test_timeout_and_output_overflow_discard_partial_state_and_leave_a_usable_sibling",
        EpubInspectionOutcome.CHILD_FAILURE,
    ),
    "evidence_process_group_reap": ExistingEvidence(
        "process-group",
        "tests.integration.test_epub_opening_report.EpubOpeningProcessTests.test_timeout_and_output_overflow_discard_partial_state_and_leave_a_usable_sibling",
        EpubInspectionOutcome.CHILD_FAILURE,
    ),
    "evidence_parent_loss": ExistingEvidence(
        "process-parent-loss",
        "tests.integration.test_epub_opening_report.EpubOpeningProcessTests.test_timeout_and_output_overflow_discard_partial_state_and_leave_a_usable_sibling",
        EpubInspectionOutcome.CHILD_FAILURE,
    ),
    "evidence_closed_child_environment": ExistingEvidence(
        "process-environment",
        "tests.integration.test_epub_opening_report.EpubOpeningProcessTests.test_timeout_and_output_overflow_discard_partial_state_and_leave_a_usable_sibling",
        EpubInspectionOutcome.SUCCESS,
    ),
}


def _request(
    value: bytes, limits: EpubPackageLimits, cancelled: bool
) -> EpubPackageRequest:
    return EpubPackageRequest(
        TargetId(
            AdapterId("synthetic"),
            ConnectionId("manifest"),
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
        limits,
        datetime.now(timezone.utc) + timedelta(seconds=30),
        time.monotonic() + 30.0,
        EpubPackageCancellation("manifest-request", lambda: cancelled),
    )


def _run_test_id(test_id: str) -> None:
    module_name, class_name, method_name = test_id.rsplit(".", 2)
    module = importlib.import_module(module_name)
    test_class = getattr(module, class_name)
    if not callable(getattr(test_class, method_name, None)):
        raise AssertionError(f"manifest evidence target does not exist: {test_id}")
    result = unittest.TestResult()
    unittest.TestSuite((test_class(method_name),)).run(result)
    if result.errors or result.failures or result.unexpectedSuccesses:
        details = result.errors + result.failures
        raise AssertionError(f"manifest evidence failed: {test_id}: {details!r}")
    if result.skipped:
        raise AssertionError(f"manifest evidence skipped: {test_id}: {result.skipped!r}")


def _projection(report: EpubPackageReport) -> dict[str, object]:
    citations = (
        [item.citation.section.value for item in report.observations]
        + [item.citation.section.value for item in report.declarations]
        + [
            item.citation.section.value
            for item in report.warnings
            if item.citation is not None
        ]
    )
    return {
        "citation_sections": dict(sorted(Counter(citations).items())),
        "observation_fields": [item.field.value for item in report.observations],
        "warnings": [item.code for item in report.warnings],
    }


def _exact_projection_sha256(report: EpubPackageReport) -> str:
    data = report.to_data()
    projection = {
        "declarations": data["declarations"],
        "normalized_observations": data["normalized_observations"],
        "observations": data["observations"],
        "warnings": data["warnings"],
    }
    serialized = json.dumps(
        projection, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


EMPTY_PROJECTION_SHA256 = _exact_projection_sha256(
    EpubPackageReport(
        EpubInspectionOutcome.MALFORMED,
        EpubPackageCapability.PACKAGE,
        EpubPackageLimits(),
    )
)


class EpubPackageFixtureManifestTests(unittest.TestCase):
    def test_every_manifest_entry_has_executable_or_linked_typed_evidence(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(
            set(manifest),
            {
                "boundary_matrix",
                "cohort",
                "coverage_matrix",
                "fixtures",
                "generator_module",
                "materialization",
                "projections",
                "schema",
            },
        )
        self.assertEqual(manifest["schema"], "synthetic-fixture-manifest-v2")
        self.assertEqual(manifest["cohort"], "epub-evidence-f07a-v2")
        self.assertEqual(
            manifest["generator_module"],
            "tests.unit.test_epub_package_fixture_manifest",
        )
        entries = manifest["fixtures"]
        package_entries = [
            entry
            for entry in entries
            if "f06-package-qualification" in entry["consumer_ownership"]
        ]
        self.assertEqual(
            {entry["generator"] for entry in package_entries}, set(DISPATCH)
        )
        self.assertEqual(len({entry["id"] for entry in entries}), len(entries))

        for entry in package_entries:
            with self.subTest(fixture=entry["id"]):
                dispatch = DISPATCH[entry["generator"]]
                expected = EpubInspectionOutcome(entry["expected_outcome"])
                self.assertEqual(entry["resource_partition"], dispatch.partition)
                self.assertEqual(entry["expected_exit"], _EPUB_PACKAGE_EXIT[expected])
                self.assertEqual(entry["spdx"], "AGPL-3.0-or-later")
                self.assertEqual(
                    entry["generation_inputs"],
                    {"recipe": entry["generator"], "seed": "deterministic"},
                )
                self.assertNotIn("/Users/", repr(entry))

                if isinstance(dispatch, ExistingEvidence):
                    self.assertEqual(expected, dispatch.outcome)
                    self.assertEqual(
                        manifest["projections"][entry["expected_projection"]],
                        {"citation_sections": {}, "observation_fields": [], "warnings": []},
                    )
                    self.assertEqual(
                        entry["expected_projection_sha256"],
                        EMPTY_PROJECTION_SHA256,
                    )
                    _run_test_id(dispatch.test_id)
                    continue

                first = dispatch.build()
                second = dispatch.build()
                self.assertEqual(first, second)
                value, limits, cancelled = first
                first_report = EpubPackageExtractor().extract(
                    _request(value, limits, cancelled)
                )
                second_report = EpubPackageExtractor().extract(
                    _request(second[0], second[1], second[2])
                )
                self.assertEqual(first_report.to_json(), second_report.to_json())
                report = first_report
                self.assertEqual(report.outcome, expected)
                self.assertEqual(_EPUB_PACKAGE_EXIT[report.outcome], entry["expected_exit"])
                self.assertEqual(
                    _projection(report),
                    manifest["projections"][entry["expected_projection"]],
                )
                self.assertEqual(
                    _exact_projection_sha256(report),
                    entry["expected_projection_sha256"],
                )
                if entry["id"] == "unsafe-metadata-one-way-projection":
                    serialized = report.to_json()
                    self.assertNotIn(UNSAFE_METADATA, serialized)
                    self.assertIn("unsafe-source-text-sha256", serialized)
                    self.assertIn("unsafe_metadata_redacted", serialized)
        for entry in entries:
            if (
                entry["consumer_ownership"] == ["f07a-opening-qualification"]
                and entry["classification"] == "linked"
            ):
                with self.subTest(linked_fixture=entry["id"]):
                    _run_test_id(entry["generator"])

    def test_registry_v2_declares_stable_identity_ownership_and_materialization(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        materialization = manifest["materialization"]
        self.assertEqual(
            materialization,
            {
                "flat_output": True,
                "sha256_index": "SHA256SUMS",
                "network": "forbidden",
                "generated_files_committed": False,
                "limits": {
                    "maximum_fixture_count": 64,
                    "maximum_fixture_bytes": 8_388_608,
                    "maximum_total_bytes": 67_108_864,
                },
            },
        )
        materialized_recipes = {
            entry["deterministic_recipe"]
            for entry in manifest["fixtures"]
            if entry["classification"] == "materialized"
        }
        self.assertEqual(materialized_recipes, set(MATERIALIZED_RECIPES))
        for entry in manifest["fixtures"]:
            with self.subTest(fixture=entry["id"]):
                self.assertEqual(
                    entry["stable_identity"], f"epub-fixture:{entry['id']}:v1"
                )
                self.assertIn(
                    entry["classification"], {"materialized", "virtual", "linked"}
                )
                self.assertTrue(entry["classification_reason"])
                self.assertTrue(entry["consumer_ownership"])
                self.assertEqual(entry["spdx"], "AGPL-3.0-or-later")
                self.assertEqual(
                    entry["generation_inputs"],
                    {
                        "recipe": entry.get(
                            "generator", entry["deterministic_recipe"]
                        ),
                        "seed": "deterministic",
                    },
                )
                package = entry["expected"]["package"]
                self.assertIsInstance(package["outcome"], str)
                self.assertIsInstance(package["exit"], int)
                opening = entry["expected"]["opening"]
                if opening is not None:
                    self.assertIn(
                        opening["outcome"],
                        {
                            "success",
                            "no_applicable_evidence",
                            "malformed",
                            "encrypted",
                            "unsupported_format",
                            "unsupported_version",
                            "unsupported_compression",
                            "resource_limit",
                            "timeout",
                            "cancellation",
                            "unstable_source",
                            "child_failure",
                            "internal_failure",
                        },
                    )
                    self.assertIsInstance(opening["exit"], int)
                if entry["classification"] == "materialized":
                    self.assertEqual(
                        entry["materialized_filename"], f"{entry['id']}.epub"
                    )
                else:
                    self.assertIsNone(entry["deterministic_recipe"])
                    self.assertIsNone(entry["materialized_filename"])

    def test_every_configurable_limit_has_default_and_hard_cap_boundary_cases(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        matrix = manifest["boundary_matrix"]
        expected_cases = [
            "below-default",
            "exact-default",
            "above-default",
            "below-hard-cap",
            "exact-hard-cap",
            "above-hard-cap",
        ]
        self.assertEqual(matrix["cases"], expected_cases)
        self.assertEqual(
            matrix["enforcement_cases"],
            ["usage-below-limit", "usage-exact-limit", "usage-above-limit"],
        )
        self.assertEqual(
            matrix["constructible_hard_usage"],
            [
                "archive_entries",
                "attributes_per_element",
                "compression_ratio_per_member",
                "container_xml_bytes",
                "decoded_xml_text_characters",
                "expanded_bytes_per_archive",
                "expanded_bytes_per_member",
                "guide_references",
                "metadata_nodes",
                "opf_package_bytes",
                "prefix_mappings",
                "source_archive_bytes",
                "xml_attributes_per_document",
                "xml_depth",
                "xml_nodes_per_document",
            ],
        )
        self.assertEqual(
            matrix["nonconstructible_hard_usage"],
            {
                "compressed_bytes_per_member": (
                    "an exact-cap member plus mandatory ZIP structure exceeds "
                    "the equal source-archive hard cap"
                ),
                "manifest_items": (
                    "an exact-cap manifest plus mandatory package/rootfile "
                    "declarations exceeds the immutable public declaration cap"
                ),
                "spine_items": (
                    "an exact-cap spine plus mandatory package and manifest "
                    "declarations exceeds the immutable public declaration cap"
                ),
            },
        )
        defaults = EpubPackageLimits()
        self.assertEqual(matrix["limits"], list(EpubPackageLimits.HARD_CAPS))
        for name, hard_cap in EpubPackageLimits.HARD_CAPS.items():
            default = getattr(defaults, name)
            with self.subTest(limit=name):
                for value in (default - 1, default, default + 1, hard_cap - 1, hard_cap):
                    self.assertGreaterEqual(value, 1)
                    EpubPackageLimits(**{name: value})
                with self.assertRaises(EvidenceValueError):
                    EpubPackageLimits(**{name: hard_cap + 1})

    def test_every_configurable_limit_is_enforced_below_exact_and_above_usage(self) -> None:
        def fixture(name: str) -> bytes:
            if name == "spine_items":
                package = EPUB3_PACKAGE.replace(
                    b'<spine><itemref idref="chapter"/></spine>',
                    b'<spine><itemref idref="chapter"/><itemref idref="chapter"/></spine>',
                )
                return epub_bytes(package=package)
            if name == "guide_references":
                package = EPUB2_PACKAGE.replace(
                    b"</guide>",
                    b'<reference type="cover" href="cover.jpg"/></guide>',
                )
                return epub_bytes(version=2, package=package)
            if name == "prefix_mappings":
                package = EPUB3_PACKAGE.replace(
                    b'prefix="schema: http://schema.org/"',
                    b'prefix="schema: http://schema.org/ vendor: https://example.invalid/vocab#"',
                )
                return epub_bytes(package=package)
            return epub_bytes()

        def outcome(name: str, value: bytes, limit: int) -> EpubInspectionOutcome | None:
            limits = EpubPackageLimits(**{name: limit})
            try:
                extraction_request = _request(value, limits, False)
            except EvidenceValueError:
                return None
            return EpubPackageExtractor().extract(extraction_request).outcome

        defaults = EpubPackageLimits()
        for name in EpubPackageLimits.HARD_CAPS:
            value = fixture(name)
            low = 1
            high = getattr(defaults, name)
            self.assertEqual(outcome(name, value, high), EpubInspectionOutcome.SUCCESS)
            while low < high:
                middle = (low + high) // 2
                result = outcome(name, value, middle)
                if result is EpubInspectionOutcome.SUCCESS:
                    high = middle
                else:
                    self.assertIn(result, {None, EpubInspectionOutcome.RESOURCE_LIMIT})
                    low = middle + 1
            exact = low
            with self.subTest(limit=name, exact=exact):
                self.assertGreater(exact, 1)
                self.assertEqual(
                    outcome(name, value, exact + 1),
                    EpubInspectionOutcome.SUCCESS,
                )
                self.assertEqual(
                    outcome(name, value, exact),
                    EpubInspectionOutcome.SUCCESS,
                )
                self.assertIn(
                    outcome(name, value, exact - 1),
                    {None, EpubInspectionOutcome.RESOURCE_LIMIT},
                )

    def test_constructible_hard_caps_have_real_below_exact_and_above_inputs(self) -> None:
        def report(
            value: bytes,
            name: str,
            **supporting_limits: int,
        ) -> EpubInspectionOutcome:
            limits = EpubPackageLimits(
                **{
                    name: EpubPackageLimits.HARD_CAPS[name],
                    **supporting_limits,
                }
            )
            return EpubPackageExtractor().extract(_request(value, limits, False)).outcome

        def virtual_request(
            source: EpubPackageSource,
            limits: EpubPackageLimits,
        ) -> EpubPackageRequest:
            return EpubPackageRequest(
                TargetId(
                    AdapterId("synthetic"),
                    ConnectionId("virtual-boundary"),
                    LibraryId("library"),
                    ExternalItemId("book"),
                ),
                FileReference("books", "generated/virtual-boundary.epub"),
                source,
                Fingerprint(
                    "sha256",
                    SchemaVersion("source-v1"),
                    hashlib.sha256(b"virtual-boundary").hexdigest(),
                ),
                EpubPackageCapability.PACKAGE,
                limits,
                datetime.now(timezone.utc) + timedelta(seconds=30),
                time.monotonic() + 30.0,
                EpubPackageCancellation("virtual-boundary", lambda: False),
            )

        def virtual_epub(
            opaque: tuple[tuple[str, int, int, int], ...],
        ) -> EpubPackageSource:
            entries: list[tuple[str, int, int, int, int, int]] = []
            segments: list[tuple[int, bytes]] = []
            cursor = 0

            def add(
                name: str,
                method: int,
                compressed: int,
                expanded: int,
                crc: int,
                data: bytes | None,
            ) -> None:
                nonlocal cursor
                encoded = name.encode("utf-8")
                needed = 20 if method == zipfile.ZIP_DEFLATED else 10
                local = b"PK\x03\x04" + struct.pack(
                    "<5H3L2H",
                    needed,
                    0x800,
                    method,
                    0,
                    0,
                    crc,
                    compressed,
                    expanded,
                    len(encoded),
                    0,
                ) + encoded
                segments.append((cursor, local))
                offset = cursor
                cursor += len(local)
                if data is not None:
                    self.assertEqual(len(data), compressed)
                    segments.append((cursor, data))
                cursor += compressed
                entries.append((name, method, compressed, expanded, crc, offset))

            required = (
                ("mimetype", b"application/epub+zip"),
                ("META-INF/container.xml", CONTAINER_ONE),
                ("EPUB/package.opf", EPUB3_PACKAGE),
                ("EPUB/nav.xhtml", b"opaque navigation"),
                ("EPUB/chapter.xhtml", b"opaque chapter"),
                ("EPUB/cover.jpg", b"opaque cover"),
            )
            for name, data in required:
                add(
                    name,
                    zipfile.ZIP_STORED,
                    len(data),
                    len(data),
                    zipfile.crc32(data),
                    data,
                )
            for name, method, compressed, expanded in opaque:
                add(name, method, compressed, expanded, 0, None)

            central_offset = cursor
            central = bytearray()
            for name, method, compressed, expanded, crc, offset in entries:
                encoded = name.encode("utf-8")
                needed = 20 if method == zipfile.ZIP_DEFLATED else 10
                central.extend(b"PK\x01\x02")
                central.extend(
                    struct.pack(
                        "<6H3L5H2L",
                        20,
                        needed,
                        0x800,
                        method,
                        0,
                        0,
                        crc,
                        compressed,
                        expanded,
                        len(encoded),
                        0,
                        0,
                        0,
                        0,
                        0,
                        offset,
                    )
                )
                central.extend(encoded)
            trailer = bytes(central) + b"PK\x05\x06" + struct.pack(
                "<4H2LH",
                0,
                0,
                len(entries),
                len(entries),
                len(central),
                central_offset,
                0,
            )
            segments.append((cursor, trailer))
            length = cursor + len(trailer)

            def read(offset: int, size: int) -> bytes:
                self.assertLessEqual(size, 2 * 1024 * 1024)
                value = bytearray(size)
                end = offset + size
                for start, content in segments:
                    overlap_start = max(offset, start)
                    overlap_end = min(end, start + len(content))
                    if overlap_start < overlap_end:
                        value[overlap_start - offset : overlap_end - offset] = (
                            content[
                                overlap_start - start : overlap_end - start
                            ]
                        )
                return bytes(value)

            return EpubPackageSource(length, read)

        def virtual_outcome(
            source: EpubPackageSource,
            name: str,
            **supporting_limits: int,
        ) -> EpubInspectionOutcome | None:
            limits = EpubPackageLimits(
                **{
                    name: EpubPackageLimits.HARD_CAPS[name],
                    **supporting_limits,
                }
            )
            try:
                request = virtual_request(source, limits)
            except EvidenceValueError:
                return None
            return EpubPackageExtractor().extract(request).outcome

        def padded_xml(value: bytes, closing: bytes, size: int) -> bytes:
            comment_characters = size - len(value) - len(b"<!---->")
            self.assertGreaterEqual(comment_characters, 0)
            return value.replace(
                closing,
                b"<!--" + b"x" * comment_characters + b"-->" + closing,
                1,
            )

        def xml_counts(value: bytes) -> tuple[int, int, int]:
            nodes = attributes = text = 0
            parser = expat.ParserCreate(namespace_separator="}")

            def start(_name: str, values: dict[str, str]) -> None:
                nonlocal nodes, attributes
                nodes += 1
                attributes += len(values)

            def characters(value: str) -> None:
                nonlocal text
                text += len(value)

            parser.StartElementHandler = start
            parser.CharacterDataHandler = characters
            parser.Parse(value, True)
            return nodes, attributes, text

        archive_values = tuple(
            epub_bytes(
                extra_entries=tuple(
                    (f"EPUB/boundary-{index}", b"", zipfile.ZIP_STORED)
                    for index in range(extra_count)
                )
            )
            for extra_count in (8_184, 8_185, 8_186)
        )
        foreign_counts = (2_037, 2_038, 2_039)
        metadata_values = tuple(
            epub_bytes(
                package=EPUB3_PACKAGE.replace(
                    b"</metadata>",
                    b'<x:value xmlns:x="urn:synthetic"/>' * count + b"</metadata>",
                )
            )
            for count in foreign_counts
        )
        guide_values = tuple(
            epub_bytes(
                version=2,
                package=EPUB2_PACKAGE.replace(
                    b"</guide>",
                    b'<reference type="text" href="chapter.xhtml"/>' * count
                    + b"</guide>",
                ),
            )
            for count in (510, 511, 512)
        )

        def prefixed(count: int) -> bytes:
            mappings = " ".join(
                f"p{index}: https://example.invalid/vocab/{index}#"
                for index in range(count - 1)
            )
            package = EPUB3_PACKAGE.replace(
                b'prefix="schema: http://schema.org/"',
                f'prefix="schema: http://schema.org/ {mappings}"'.encode(),
            )
            return epub_bytes(package=package)

        prefix_values = tuple(prefixed(count) for count in (255, 256, 257))

        def attributed(count: int) -> bytes:
            attributes = " ".join(f'a{index}="x"' for index in range(count))
            node = f'<x:value xmlns:x="urn:synthetic" {attributes}/>'.encode()
            return epub_bytes(
                package=EPUB3_PACKAGE.replace(
                    b"</metadata>", node + b"</metadata>"
                )
            )

        attribute_values = tuple(attributed(count) for count in (255, 256, 257))

        def nested(depth: int) -> bytes:
            levels = depth - 2
            node = (
                b'<x:value xmlns:x="urn:synthetic">'
                + b"<x:value>" * (levels - 1)
                + b"</x:value>" * levels
            )
            return epub_bytes(
                package=EPUB3_PACKAGE.replace(
                    b"</metadata>", node + b"</metadata>"
                )
            )

        depth_values = tuple(nested(depth) for depth in (127, 128, 129))
        container_limit = EpubPackageLimits.HARD_CAPS["container_xml_bytes"]
        container_values = tuple(
            epub_bytes(
                container=padded_xml(CONTAINER_ONE, b"</container>", size),
                compression=zipfile.ZIP_STORED,
            )
            for size in (container_limit - 1, container_limit, container_limit + 1)
        )
        package_limit = EpubPackageLimits.HARD_CAPS["opf_package_bytes"]
        package_values = tuple(
            epub_bytes(
                package=padded_xml(EPUB3_PACKAGE, b"</package>", size),
                compression=zipfile.ZIP_STORED,
            )
            for size in (package_limit - 1, package_limit, package_limit + 1)
        )

        def node_package(total: int) -> bytes:
            base_nodes = xml_counts(EPUB3_PACKAGE)[0]
            node = (
                b'<x:values xmlns:x="urn:synthetic">'
                + b"<x:v/>" * (total - base_nodes - 1)
                + b"</x:values>"
            )
            return EPUB3_PACKAGE.replace(b"</metadata>", node + b"</metadata>")

        node_limit = EpubPackageLimits.HARD_CAPS["xml_nodes_per_document"]
        node_values = tuple(
            epub_bytes(package=node_package(total))
            for total in (node_limit - 1, node_limit, node_limit + 1)
        )

        def attributed_document(total: int) -> bytes:
            base_attributes = xml_counts(EPUB3_PACKAGE)[1]
            remaining = total - base_attributes
            complete, final = divmod(remaining, 256)

            def node(count: int) -> bytes:
                attributes = b" ".join(
                    f'a{index}="x"'.encode() for index in range(count)
                )
                return b"<x:v " + attributes + b"/>"

            additions = b"<x:values>" + node(256) * complete
            if final:
                additions += node(final)
            additions += b"</x:values>"
            package = EPUB3_PACKAGE.replace(
                b'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">',
                b'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:x="urn:synthetic">',
            )
            return package.replace(b"</metadata>", additions + b"</metadata>")

        attribute_limit = EpubPackageLimits.HARD_CAPS[
            "xml_attributes_per_document"
        ]
        document_attribute_values = tuple(
            epub_bytes(package=attributed_document(total))
            for total in (attribute_limit - 1, attribute_limit, attribute_limit + 1)
        )

        def text_package(total: int) -> bytes:
            base_text = xml_counts(EPUB3_PACKAGE)[2]
            node = (
                b'<x:value xmlns:x="urn:synthetic">'
                + b"x" * (total - base_text)
                + b"</x:value>"
            )
            return EPUB3_PACKAGE.replace(b"</metadata>", node + b"</metadata>")

        text_limit = EpubPackageLimits.HARD_CAPS[
            "decoded_xml_text_characters"
        ]
        text_values = tuple(
            epub_bytes(package=text_package(total), compression=zipfile.ZIP_STORED)
            for total in (text_limit - 1, text_limit, text_limit + 1)
        )
        for name, values in (
            ("archive_entries", archive_values),
            ("attributes_per_element", attribute_values),
            ("container_xml_bytes", container_values),
            ("decoded_xml_text_characters", text_values),
            ("guide_references", guide_values),
            ("metadata_nodes", metadata_values),
            ("opf_package_bytes", package_values),
            ("prefix_mappings", prefix_values),
            ("xml_attributes_per_document", document_attribute_values),
            ("xml_depth", depth_values),
            ("xml_nodes_per_document", node_values),
        ):
            with self.subTest(limit=name):
                support = {}
                if name in {
                    "decoded_xml_text_characters",
                    "xml_attributes_per_document",
                    "xml_nodes_per_document",
                }:
                    support["opf_package_bytes"] = EpubPackageLimits.HARD_CAPS[
                        "opf_package_bytes"
                    ]
                if name in {
                    "xml_attributes_per_document",
                    "xml_nodes_per_document",
                }:
                    support["compression_ratio_per_member"] = (
                        EpubPackageLimits.HARD_CAPS[
                            "compression_ratio_per_member"
                        ]
                    )
                if name == "xml_attributes_per_document":
                    support["attributes_per_element"] = EpubPackageLimits.HARD_CAPS[
                        "attributes_per_element"
                    ]
                self.assertEqual(
                    report(values[0], name, **support), EpubInspectionOutcome.SUCCESS
                )
                self.assertEqual(
                    report(values[1], name, **support), EpubInspectionOutcome.SUCCESS
                )
                self.assertEqual(
                    report(values[2], name, **support),
                    EpubInspectionOutcome.RESOURCE_LIMIT,
                )

        ratio_limit = EpubPackageLimits.HARD_CAPS[
            "compression_ratio_per_member"
        ]
        ratio_sources = tuple(
            virtual_epub(
                (("EPUB/opaque.bin", zipfile.ZIP_DEFLATED, 1_000, expanded),)
            )
            for expanded in (
                1_000 * (ratio_limit - 1),
                1_000 * ratio_limit,
                1_000 * ratio_limit + 1,
            )
        )
        self.assertEqual(
            tuple(
                virtual_outcome(source, "compression_ratio_per_member")
                for source in ratio_sources
            ),
            (
                EpubInspectionOutcome.SUCCESS,
                EpubInspectionOutcome.SUCCESS,
                EpubInspectionOutcome.RESOURCE_LIMIT,
            ),
        )

        member_limit = EpubPackageLimits.HARD_CAPS["expanded_bytes_per_member"]
        member_sources = tuple(
            virtual_epub(
                (
                    (
                        "EPUB/opaque.bin",
                        zipfile.ZIP_DEFLATED,
                        (expanded + ratio_limit - 1) // ratio_limit,
                        expanded,
                    ),
                )
            )
            for expanded in (member_limit - 1, member_limit, member_limit + 1)
        )
        member_support = {
            "compression_ratio_per_member": ratio_limit,
            "expanded_bytes_per_archive": EpubPackageLimits.HARD_CAPS[
                "expanded_bytes_per_archive"
            ],
        }
        self.assertEqual(
            tuple(
                virtual_outcome(
                    source,
                    "expanded_bytes_per_member",
                    **member_support,
                )
                for source in member_sources
            ),
            (
                EpubInspectionOutcome.SUCCESS,
                EpubInspectionOutcome.SUCCESS,
                EpubInspectionOutcome.RESOURCE_LIMIT,
            ),
        )

        archive_limit = EpubPackageLimits.HARD_CAPS[
            "expanded_bytes_per_archive"
        ]
        base_expanded = sum(
            len(value)
            for value in (
                b"application/epub+zip",
                CONTAINER_ONE,
                EPUB3_PACKAGE,
                b"opaque navigation",
                b"opaque chapter",
                b"opaque cover",
            )
        )

        def archive_source(total: int) -> EpubPackageSource:
            opaque_total = total - base_expanded
            quotient, remainder = divmod(opaque_total, 4)
            sizes = [quotient + (1 if index < remainder else 0) for index in range(4)]
            return virtual_epub(
                tuple(
                    (
                        f"EPUB/opaque-{index}.bin",
                        zipfile.ZIP_DEFLATED,
                        (expanded + ratio_limit - 1) // ratio_limit,
                        expanded,
                    )
                    for index, expanded in enumerate(sizes)
                )
            )

        archive_sources = tuple(
            archive_source(total)
            for total in (archive_limit - 1, archive_limit, archive_limit + 1)
        )
        archive_support = {
            "compression_ratio_per_member": ratio_limit,
            "expanded_bytes_per_member": member_limit,
        }
        self.assertEqual(
            tuple(
                virtual_outcome(
                    source,
                    "expanded_bytes_per_archive",
                    **archive_support,
                )
                for source in archive_sources
            ),
            (
                EpubInspectionOutcome.SUCCESS,
                EpubInspectionOutcome.SUCCESS,
                EpubInspectionOutcome.RESOURCE_LIMIT,
            ),
        )

        source_limit = EpubPackageLimits.HARD_CAPS["source_archive_bytes"]

        def source_sized(size: int) -> EpubPackageSource:
            empty = virtual_epub(
                (("EPUB/padding.bin", zipfile.ZIP_STORED, 0, 0),)
            )
            padding = size - empty.length
            self.assertGreaterEqual(padding, 0)
            return virtual_epub(
                (("EPUB/padding.bin", zipfile.ZIP_STORED, padding, padding),)
            )

        source_values = tuple(
            source_sized(size)
            for size in (source_limit - 1, source_limit, source_limit + 1)
        )
        source_support = {
            "compressed_bytes_per_member": EpubPackageLimits.HARD_CAPS[
                "compressed_bytes_per_member"
            ],
            "expanded_bytes_per_member": member_limit,
            "expanded_bytes_per_archive": archive_limit,
        }
        self.assertEqual(
            tuple(
                virtual_outcome(
                    source,
                    "source_archive_bytes",
                    **source_support,
                )
                for source in source_values
            ),
            (
                EpubInspectionOutcome.SUCCESS,
                EpubInspectionOutcome.SUCCESS,
                None,
            ),
        )

    def test_hostile_and_normalization_coverage_matrix_executes(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        matrix = manifest["coverage_matrix"]
        self.assertEqual(
            {item["partition"] for item in matrix},
            {
                "encryption-graph",
                "metadata-identifiers-formats-regions",
                "identifier-isbn-validation",
                "manifest-semantic-privacy",
                "metadata-repetition-precedence",
                "metadata-standard-refinements-cover",
                "metadata-title-and-datatypes",
                "opf-order-refinement-graph",
                "path-and-zip-encryption",
                "report-declaration-output-bounds",
                "xml-entities-xinclude",
                "zip-directory-and-known-flag-residue",
                "zip-descriptor-local-header",
                "zip-preflight-alias-crc-compression",
            },
        )
        for item in matrix:
            with self.subTest(partition=item["partition"]):
                self.assertEqual(set(item), {"partition", "test_id"})
                _run_test_id(item["test_id"])

    def test_committed_linux_measurements_are_bounded_and_reproducible(self) -> None:
        evidence = json.loads(QUALIFICATION.read_text(encoding="utf-8"))
        self.assertEqual(evidence["schema"], "epub-package-qualification-v1")
        self.assertEqual(evidence["outcome"], "success")
        self.assertEqual(evidence["environment"]["platform"], "linux")
        self.assertIn("@sha256:", evidence["environment"]["image"])
        self.assertEqual(evidence["runner"], "scripts/qualify_epub_package.py")
        self.assertNotIn("/Users/", repr(evidence))
        constraints = evidence["constraints"]
        self.assertTrue(constraints["container_read_only"])
        self.assertEqual(constraints["network"], "none")
        self.assertEqual(constraints["runtime_user"], "65534:65534")
        measured = evidence["measured"]
        self.assertLessEqual(measured["child_peak_rss_kib"], 256 * 1_024)
        self.assertLessEqual(measured["child_peak_descriptors"], 64)
        self.assertLessEqual(measured["peak_process_group_members"], 1)
        self.assertLessEqual(measured["output_bytes"], 524_288)
        self.assertLess(measured["elapsed_seconds"], 5)
        self.assertEqual(measured["parent_descriptor_delta"], 0)
        self.assertEqual(measured["started_children"], 1)
        self.assertEqual(measured["surviving_children"], 0)


if __name__ == "__main__":
    unittest.main()
