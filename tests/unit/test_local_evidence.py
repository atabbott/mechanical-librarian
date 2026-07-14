from __future__ import annotations

import json
import time
import unittest
from datetime import datetime, timedelta, timezone

from mechanical_librarian.domain.evidence_bounds import EvidenceValueError
from mechanical_librarian.domain.identifiers import (
    AdapterId,
    CompatibilityVersion,
    ConnectionId,
    ExternalItemId,
    ExtractorVersion,
    Fingerprint,
    LibraryId,
    SchemaVersion,
    TargetId,
)
from mechanical_librarian.domain.inventory import FileReference
from mechanical_librarian.domain.local_evidence import (
    CitationSection,
    EpubPackageLimits,
    LocalEvidenceCancellation,
    LocalEvidenceCapability,
    LocalEvidenceCitation,
    LocalEvidenceField,
    LocalEvidenceObservation,
    LocalEvidenceOutcome,
    LocalEvidenceReport,
    LocalEvidenceRequest,
    LocalEvidenceSource,
    LocalEvidenceWarning,
    ObservationApplicability,
    local_resource_identity,
)


def fingerprint(value: str = "a") -> Fingerprint:
    return Fingerprint("sha256", SchemaVersion("source-v1"), value * 64)


def citation(ordinal: int) -> LocalEvidenceCitation:
    return LocalEvidenceCitation(
        fingerprint(),
        "b" * 64,
        CitationSection.METADATA,
        "{http://purl.org/dc/elements/1.1/}title",
        ordinal,
        "title-main",
    )


def observation(
    field: LocalEvidenceField,
    ordinal: int,
    value: str,
    display: int | None = None,
) -> LocalEvidenceObservation:
    return LocalEvidenceObservation(
        field=field,
        raw_value=value,
        normalized_value=value.casefold(),
        applicability=ObservationApplicability.WORK,
        citation=citation(ordinal),
        schema_version=SchemaVersion("package-observation-v1"),
        extractor_version=ExtractorVersion("epub-package-v1"),
        compatibility_version=CompatibilityVersion("epub-3.3"),
        declared_display_sequence=display,
        language="en-GB",
        same_file_lineage="c" * 64,
    )


class LocalEvidenceTests(unittest.TestCase):
    def test_repeated_observations_round_trip_without_collapsing_source_order(self) -> None:
        report = LocalEvidenceReport(
            outcome=LocalEvidenceOutcome.SUCCESS,
            capability=LocalEvidenceCapability.PACKAGE,
            limits=EpubPackageLimits(),
            source_fingerprint=fingerprint(),
            observations=(
                observation(LocalEvidenceField.CREATOR, 1, "Same"),
                observation(LocalEvidenceField.CREATOR, 2, "Same"),
                observation(LocalEvidenceField.TITLE, 3, "Later", 2),
            ),
            warnings=(LocalEvidenceWarning("ambiguous_title", citation(3)),),
        )

        restored = LocalEvidenceReport.from_json(report.to_json())

        self.assertEqual(restored, report)
        self.assertEqual(
            tuple(item.citation.source_ordinal for item in restored.observations),
            (1, 2, 3),
        )
        self.assertEqual(
            tuple(item.field for item in restored.normalized_observations),
            (
                LocalEvidenceField.TITLE,
                LocalEvidenceField.CREATOR,
                LocalEvidenceField.CREATOR,
            ),
        )
        self.assertIsNone(restored.artifact)

    def test_closed_canonical_schema_rejects_unknown_duplicate_and_reordered_json(self) -> None:
        report = LocalEvidenceReport(
            LocalEvidenceOutcome.SUCCESS,
            LocalEvidenceCapability.PACKAGE,
            EpubPackageLimits(),
            fingerprint(),
            (observation(LocalEvidenceField.TITLE, 1, "Title"),),
        )
        value = json.loads(report.to_json())
        value["unknown"] = True
        with self.assertRaises(EvidenceValueError):
            LocalEvidenceReport.from_json(
                json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            )
        with self.assertRaises(EvidenceValueError):
            LocalEvidenceReport.from_json('{"artifact":null,"artifact":null}')
        with self.assertRaises(EvidenceValueError):
            LocalEvidenceReport.from_json(json.dumps(report.to_data()))

    def test_outcomes_are_complete_typed_envelopes_and_failures_cannot_look_partial(self) -> None:
        self.assertEqual(
            {item.value for item in LocalEvidenceOutcome},
            {
                "invalid_invocation",
                "success",
                "no_applicable_evidence",
                "invalid_configuration",
                "invalid_reference",
                "unsupported_format",
                "unsupported_version",
                "unsupported_compression",
                "unsupported_multiple_renditions",
                "encrypted",
                "malformed",
                "integrity_failure",
                "resource_limit",
                "timeout",
                "cancellation",
                "unstable_source",
                "unavailable_source",
                "child_failure",
                "internal_failure",
            },
        )
        for outcome in LocalEvidenceOutcome:
            if outcome is LocalEvidenceOutcome.SUCCESS:
                continue
            with self.subTest(outcome=outcome):
                report = LocalEvidenceReport(
                    outcome, LocalEvidenceCapability.PACKAGE, EpubPackageLimits()
                )
                self.assertEqual(LocalEvidenceReport.from_json(report.to_json()), report)
                if not outcome.succeeded:
                    with self.assertRaises(EvidenceValueError):
                        LocalEvidenceReport(
                            outcome,
                            LocalEvidenceCapability.PACKAGE,
                            EpubPackageLimits(),
                            observations=(
                                observation(LocalEvidenceField.TITLE, 1, "partial"),
                            ),
                        )

    def test_bounds_ordering_and_path_free_citation_are_constructor_invariants(self) -> None:
        with self.assertRaises(EvidenceValueError):
            EpubPackageLimits(archive_entries=8_193)
        self.assertEqual(EpubPackageLimits(archive_entries=1).archive_entries, 1)
        with self.assertRaises(EvidenceValueError):
            LocalEvidenceCitation(
                fingerprint(),
                "b" * 64,
                CitationSection.METADATA,
                "title",
                1,
                "../../private",
            )
        with self.assertRaises(EvidenceValueError):
            LocalEvidenceCitation(
                fingerprint(),
                "b" * 64,
                CitationSection.METADATA,
                "{file:///Users/private/package.opf}title",
                1,
            )
        self.assertEqual(
            local_resource_identity("OPS/package.opf"),
            "4f9acde629bc2391e0d1bc19daef01f7419dc8af6fc5002f6bfb9abc7bdab53e",
        )
        with self.assertRaises(EvidenceValueError):
            local_resource_identity("OPS/../private.opf")
        with self.assertRaises(EvidenceValueError):
            LocalEvidenceReport(
                LocalEvidenceOutcome.SUCCESS,
                LocalEvidenceCapability.PACKAGE,
                EpubPackageLimits(),
                fingerprint(),
                (
                    observation(LocalEvidenceField.TITLE, 2, "second"),
                    observation(LocalEvidenceField.TITLE, 1, "first"),
                ),
            )
        with self.assertRaises(EvidenceValueError):
            LocalEvidenceReport(
                LocalEvidenceOutcome.SUCCESS,
                LocalEvidenceCapability.PACKAGE,
                EpubPackageLimits(),
                fingerprint(),
                tuple(
                    observation(LocalEvidenceField.TITLE, index + 1, str(index))
                    for index in range(33)
                ),
            )

    def test_request_binds_logical_identity_limits_deadline_and_cancellation(self) -> None:
        target = TargetId(
            AdapterId("synthetic"),
            ConnectionId("connection"),
            LibraryId("library"),
            ExternalItemId("item"),
        )
        state = False
        request = LocalEvidenceRequest(
            target,
            FileReference("books", "generated/book.epub"),
            LocalEvidenceSource(4, lambda offset, size: b"epub"[offset : offset + size]),
            fingerprint(),
            LocalEvidenceCapability.PACKAGE,
            EpubPackageLimits(),
            datetime.now(timezone.utc) + timedelta(seconds=60),
            time.monotonic() + 60.0,
            LocalEvidenceCancellation("request-one", lambda: state),
        )
        self.assertFalse(request.cancellation.is_cancelled())
        self.assertEqual(request.file.mount, "books")
        self.assertEqual(request.source.read_at(1, 2), b"pu")
        with self.assertRaises(EvidenceValueError):
            request.source.read_at(3, 2)


if __name__ == "__main__":
    unittest.main()
