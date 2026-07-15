from __future__ import annotations

import unittest
import time
from datetime import datetime, timedelta, timezone

from mechanical_librarian.application.local_evidence import extract_local_evidence
from mechanical_librarian.domain.identifiers import (
    AdapterId,
    CompatibilityVersion,
    ConnectionId,
    ExtractorVersion,
    ExternalItemId,
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
    ObservationApplicability,
    local_file_lineage,
)


class FakeExtractor:
    def __init__(
        self,
        outcome: LocalEvidenceOutcome,
        report: LocalEvidenceReport | None = None,
    ) -> None:
        self.outcome = outcome
        self.report = report
        self.requests: list[LocalEvidenceRequest] = []

    def extract(self, request: LocalEvidenceRequest) -> LocalEvidenceReport:
        self.requests.append(request)
        if self.report is not None:
            return self.report
        return LocalEvidenceReport(
            self.outcome,
            request.capability,
            request.limits,
            request.expected_source if self.outcome is LocalEvidenceOutcome.SUCCESS else None,
        )


def request(cancelled: bool = False) -> LocalEvidenceRequest:
    return LocalEvidenceRequest(
        TargetId(
            AdapterId("synthetic"),
            ConnectionId("connection"),
            LibraryId("library"),
            ExternalItemId("item"),
        ),
        FileReference("books", "generated/book.epub"),
        LocalEvidenceSource(4, lambda offset, size: b"epub"[offset : offset + size]),
        Fingerprint("sha256", SchemaVersion("source-v1"), "a" * 64),
        LocalEvidenceCapability.PACKAGE,
        EpubPackageLimits(source_archive_bytes=1_024),
        datetime.now(timezone.utc) + timedelta(seconds=60),
        time.monotonic() + 60.0,
        LocalEvidenceCancellation("contract-request", lambda: cancelled),
    )


class LocalEvidenceExtractorContractTests(unittest.TestCase):
    def test_fake_contract_preserves_every_typed_outcome(self) -> None:
        for outcome in LocalEvidenceOutcome:
            with self.subTest(outcome=outcome):
                extractor = FakeExtractor(outcome)
                extraction_request = request()
                report = extract_local_evidence(extractor, extraction_request)
                self.assertEqual(report.outcome, outcome)
                self.assertEqual(extractor.requests, [extraction_request])
                self.assertEqual(report.limits.source_archive_bytes, 1_024)

    def test_cancellation_is_observation_only_and_no_authority_is_delegated(self) -> None:
        extraction_request = request(cancelled=True)
        self.assertTrue(extraction_request.cancellation.is_cancelled())
        self.assertEqual(
            set(vars(extraction_request)),
            {
                "target",
                "file",
                "source",
                "expected_source",
                "capability",
                "limits",
                "deadline",
                "monotonic_deadline",
                "cancellation",
            },
        )
        self.assertFalse(
            any(
                hasattr(extraction_request, authority)
                for authority in (
                    "network",
                    "credentials",
                    "database",
                    "persist",
                    "write",
                    "spawn",
                )
            )
        )
        self.assertEqual(extraction_request.source.read_at(0, 4), b"epub")
        with self.assertRaises(ValueError):
            extraction_request.source.read_at(0, 5)

    def test_fake_preserves_repeated_observations_and_raw_citation_order(self) -> None:
        extraction_request = request()
        observations = tuple(
            LocalEvidenceObservation(
                LocalEvidenceField.TITLE,
                value,
                value,
                ObservationApplicability.WORK,
                LocalEvidenceCitation(
                    extraction_request.expected_source,
                    "b" * 64,
                    CitationSection.METADATA,
                    "{http://purl.org/dc/elements/1.1/}title",
                    ordinal,
                ),
                SchemaVersion("package-observation-v1"),
                ExtractorVersion("fake-extractor-v1"),
                CompatibilityVersion("epub-3.3"),
                same_file_lineage=local_file_lineage(
                    extraction_request.expected_source
                ),
            )
            for ordinal, value in ((1, "Second alphabetically"), (2, "First alphabetically"))
        )
        expected = LocalEvidenceReport(
            LocalEvidenceOutcome.SUCCESS,
            extraction_request.capability,
            extraction_request.limits,
            extraction_request.expected_source,
            observations,
        )
        extractor = FakeExtractor(LocalEvidenceOutcome.SUCCESS, expected)

        actual = extract_local_evidence(extractor, extraction_request)

        self.assertEqual(actual.observations, observations)
        self.assertEqual([item.citation.source_ordinal for item in actual.observations], [1, 2])
        self.assertEqual([item.raw_value for item in actual.observations], [
            "Second alphabetically",
            "First alphabetically",
        ])
        self.assertEqual(extractor.requests, [extraction_request])


if __name__ == "__main__":
    unittest.main()
    LocalEvidenceCitation,
    LocalEvidenceField,
    LocalEvidenceObservation,
