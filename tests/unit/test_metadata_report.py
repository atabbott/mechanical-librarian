from __future__ import annotations

from dataclasses import replace
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import httpx

from mechanical_librarian.adapters.persistence.metadata_reports import (
    open_repository,
)
from mechanical_librarian.adapters.providers.google_books_metadata import (
    GoogleBooksMetadataProvider,
)
from mechanical_librarian.application.configuration import (
    LibraryConfiguration,
    MediaMountConfiguration,
    ReportLimits,
    TargetConnectionConfiguration,
)
from mechanical_librarian.application.metadata_report import (
    CurrentnessError,
    GenerateMetadataReport,
    InspectionResult,
    MetadataLookup,
    ProviderBinding,
    ProviderLaneResult,
    TargetFile,
    TargetCover,
    TargetSnapshot,
    UnsupportedMediaError,
    MetadataReportError,
    _variant_marker,
    metadata_lookup,
    metadata_discovery_plan,
    provider_observation,
    qualify_provider_observation,
)
from mechanical_librarian.domain.cover_evidence import (
    CoverArtifact,
    CoverDesignation,
    CoverProvenance,
    CoverSource,
    CoverSourceKind,
    CoverSourceState,
    cover_source_id,
)
from mechanical_librarian.domain.epub_inspection import DateEvent, DatePrecision
from mechanical_librarian.domain.failure_diagnostics import (
    FailureCategory,
    FailureComponent,
    FailureDiagnostic,
    FailureStage,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    Comparison,
    ComparisonLink,
    ComparisonMethod,
    DateLinkConfidence,
    FieldResult,
    FieldSource,
    FieldSourceResult,
    FieldState,
    FileFacts,
    IdentifierEvidenceBasis,
    IdentifierRelation,
    LocalClaim,
    MetadataReport,
    MAX_CANDIDATES,
    MAX_CONTRIBUTIONS,
    MAX_DESCRIPTION_CHARACTERS,
    MAX_REPORT_BYTES,
    MAX_SCALAR_CHARACTERS,
    MAX_SERIES,
    MAX_TAGS,
    ProviderObservation,
    ProviderCandidate,
    ProviderClaim,
    ProviderEvidence,
    ProviderEvidenceKind,
    ProviderIdentityBinding,
    ProviderQueryKind,
    ProviderStatus,
    RearClaimProvenance,
    ReportValueError,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    canonical_json,
    comparison_links,
    claim_scope,
    default_target_fields,
    derive_identifier_evidence,
    derive_field_results,
    finalize_candidate_comparisons,
    normalize_title,
    provider_access_time,
    stable_reference,
)
from mechanical_librarian.interfaces.cli import main


class _Target:
    def __init__(self, snapshots: list[TargetSnapshot]) -> None:
        self.snapshots = snapshots
        self.calls = 0

    def read(self, item: str) -> TargetSnapshot:
        value = self.snapshots[self.calls]
        self.calls += 1
        return value


class _Inspector:
    def __init__(self, result: InspectionResult) -> None:
        self.result = result
        self.inspections = 0
        self.verifications = 0

    def inspect(self, mount, relative_path, maximum_bytes):
        self.inspections += 1
        return self.result

    def unchanged(self, mount, relative_path, expected_digest, maximum_bytes):
        self.verifications += 1
        return expected_digest == self.result.content_digest


class _Repository:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.closed = False

    def publish(self, report: MetadataReport, currentness, expected_current=None) -> str:
        currentness()
        self.values.setdefault(report.report_id, report.to_json())
        return self.values[report.report_id]

    def get(self, report_id: str) -> str:
        return self.values[report_id]

    def current(self, connection: str, library: str, item: str, file_identity: str) -> str:
        raise CurrentnessError("persisted current report is unavailable")

    def current_optional(self, connection: str, library: str, item: str, file_identity: str) -> str | None:
        return None

    def close(self) -> None:
        self.closed = True


class MetadataReportTests(unittest.TestCase):
    def test_provider_diagnostics_reject_foreign_and_noncanonical_aggregates(
        self,
    ) -> None:
        valid = FailureDiagnostic(
            FailureCategory.UNKNOWN,
            FailureComponent.PROVIDER,
            FailureStage.RESPONSE,
        )
        invalid = (
            FailureDiagnostic(
                FailureCategory.UNKNOWN,
                FailureComponent.REVIEWER,
                FailureStage.RESPONSE,
            ),
            FailureDiagnostic(
                FailureCategory.UNKNOWN,
                FailureComponent.PROVIDER,
                FailureStage.REQUEST,
            ),
            FailureDiagnostic(
                FailureCategory.CITATION_MEMBERSHIP,
                FailureComponent.PROVIDER,
                FailureStage.RESPONSE,
            ),
        )
        for diagnostic in invalid:
            with self.subTest(diagnostic=diagnostic):
                with self.assertRaises(ReportValueError):
                    ProviderObservation(
                        "catalogue",
                        ProviderStatus.UNAVAILABLE,
                        diagnostics=(diagnostic,),
                    )
        for diagnostics in (
            (valid, valid),
            (
                valid,
                FailureDiagnostic(
                    FailureCategory.UNKNOWN,
                    FailureComponent.PROVIDER,
                    FailureStage.RESPONSE,
                    count=2,
                ),
            ),
            (
                valid,
                FailureDiagnostic(
                    FailureCategory.HTTP_NON_SUCCESS,
                    FailureComponent.PROVIDER,
                    FailureStage.RESPONSE,
                ),
            ),
        ):
            with self.subTest(diagnostics=diagnostics):
                with self.assertRaises(ReportValueError):
                    ProviderObservation(
                        "catalogue",
                        ProviderStatus.UNAVAILABLE,
                        diagnostics=diagnostics,
                    )

        report = MetadataReport.create(
            target=TargetFacts(
                "primary",
                "ebooks",
                "item-1",
                default_target_fields(None),
            ),
            file=FileFacts(0, "a" * 64, "b" * 64),
            local_claims=(),
            local_reasons=("no_opening_claims",),
            providers=(
                ProviderObservation(
                    "catalogue",
                    ProviderStatus.UNAVAILABLE,
                    diagnostics=(valid,),
                ),
            ),
        )
        raw = json.loads(report.to_json())
        raw["providers"][0]["diagnostics"][0]["component"] = "reviewer"
        with self.assertRaises(ReportValueError):
            MetadataReport.from_json(canonical_json(raw))

    def test_provider_access_time_is_canonical_bounded_and_replay_stable(
        self,
    ) -> None:
        accessed_at = provider_access_time(1_700_000_000.125)
        self.assertEqual("2023-11-14T22:13:20.125000Z", accessed_at)
        observation = ProviderObservation(
            "catalogue",
            ProviderStatus.NO_MATCH,
            query_kind=ProviderQueryKind.WORK,
            authority="hardcover",
            accessed_at=accessed_at,
        )
        report = MetadataReport.create(
            target=TargetFacts(
                "primary",
                "ebooks",
                "item-1",
                default_target_fields(None),
            ),
            file=FileFacts(0, "a" * 64, "b" * 64),
            local_claims=(),
            local_reasons=("no_opening_claims",),
            providers=(observation,),
        )
        reopened = MetadataReport.from_json(report.to_json())
        self.assertEqual(accessed_at, reopened.providers[0].accessed_at)
        self.assertEqual(report.to_json(), reopened.to_json())
        missing = report.to_data()
        missing["providers"][0].pop("accessed_at")  # type: ignore[index]
        with self.assertRaises(ReportValueError):
            MetadataReport.from_json(canonical_json(missing))
        self.assertIsNone(
            ProviderObservation(
                "catalogue", ProviderStatus.INSUFFICIENT_INPUT
            ).to_data()["accessed_at"]
        )

        for invalid in (
            True,
            float("nan"),
            float("inf"),
            10**1000,
        ):
            with self.subTest(clock=invalid):
                with self.assertRaises(ReportValueError):
                    provider_access_time(invalid)  # type: ignore[arg-type]
        for invalid in (
            "",
            "2023-11-14T22:13:20Z",
            "2023-11-14T22:13:20.125000+00:00",
            "2023-02-29T22:13:20.125000Z",
            "2023-11-14T22:13:60.125000Z",
        ):
            with self.subTest(accessed_at=invalid):
                with self.assertRaises(ReportValueError):
                    ProviderObservation(
                        "catalogue",
                        ProviderStatus.NO_MATCH,
                        accessed_at=invalid,
                    )

    def test_provider_dates_preserve_lexical_normalized_precision_and_linkage(
        self,
    ) -> None:
        lexical = "2024-03-05T10:30:00-04:00"
        claim = ProviderClaim.create(
            provider="catalogue",
            candidate="edition-1",
            kind=ClaimKind.PUBLICATION_DATE,
            value=lexical,
            state=FieldState.VALID,
            scope=ClaimScope.EDITION,
            applicability="not_applicable",
        )
        self.assertEqual(lexical, claim.date_lexical)
        self.assertEqual(lexical, claim.value)
        self.assertEqual(DatePrecision.TIMESTAMP, claim.date_precision)
        self.assertEqual(DateEvent.MANIFESTATION_PUBLICATION, claim.date_event)
        self.assertEqual(DateLinkConfidence.EXPLICIT, claim.date_confidence)
        with self.assertRaises(ReportValueError):
            replace(claim, date_precision=DatePrecision.DAY)

        unresolved = ProviderEvidence(
            ProviderEvidenceKind.PUBLICATION_DATE_TEXT,
            "2024-03",
            ClaimScope.EDITION,
        )
        self.assertEqual("2024-03", unresolved.date_normalized)
        self.assertEqual(DatePrecision.MONTH, unresolved.date_precision)
        self.assertEqual(DateEvent.UNKNOWN, unresolved.date_event)
        self.assertEqual(DateLinkConfidence.UNRESOLVED, unresolved.date_confidence)
        self.assertEqual(0, unresolved.source_ordinal)
        with self.assertRaises(ReportValueError):
            replace(unresolved, source_ordinal=-1)
        with self.assertRaises(ReportValueError):
            ProviderClaim.create(
                provider="catalogue",
                candidate="edition-1",
                kind=ClaimKind.PUBLICATION_DATE,
                value="2024-03-05T10:30:00+14:01",
                state=FieldState.VALID,
                scope=ClaimScope.EDITION,
                applicability="not_applicable",
            )
        report = MetadataReport.create(
            target=TargetFacts(
                "primary",
                "ebooks",
                "item-1",
                default_target_fields(None),
            ),
            file=FileFacts(0, "a" * 64, "b" * 64),
            local_claims=(),
            local_reasons=("no_opening_claims",),
            providers=(
                ProviderObservation(
                    "catalogue",
                    ProviderStatus.CANDIDATES,
                    (),
                    (
                        ProviderCandidate(
                            "edition-1",
                            "work-1",
                            (claim,),
                            (),
                        ),
                    ),
                ),
            ),
        )
        replay_data = report.to_data()
        replay_data["providers"][0]["candidates"][0]["claims"][0][
            "date_precision"
        ] = "day"
        with self.assertRaises(ReportValueError):
            MetadataReport.from_json(canonical_json(replay_data))

    def test_provider_asin_is_canonical_before_reference_and_report_replay(
        self,
    ) -> None:
        claim = ProviderClaim.create(
            provider="catalogue",
            candidate="edition-1",
            kind=ClaimKind.ASIN,
            value="b012345678",
            state=FieldState.VALID,
            scope=ClaimScope.EDITION,
            applicability="not_applicable",
        )
        canonical = ProviderClaim.create(
            provider="catalogue",
            candidate="edition-1",
            kind=ClaimKind.ASIN,
            value="B012345678",
            state=FieldState.VALID,
            scope=ClaimScope.EDITION,
            applicability="not_applicable",
        )
        self.assertEqual("B012345678", claim.value)
        self.assertEqual(canonical.reference, claim.reference)
        with self.assertRaises(ReportValueError):
            replace(claim, value="b012345678")
        report = MetadataReport.create(
            target=TargetFacts(
                "primary",
                "ebooks",
                "item-1",
                default_target_fields(None),
            ),
            file=FileFacts(0, "a" * 64, "b" * 64),
            local_claims=(),
            local_reasons=("no_opening_claims",),
            providers=(
                ProviderObservation(
                    "catalogue",
                    ProviderStatus.CANDIDATES,
                    (),
                    (
                        ProviderCandidate(
                            "edition-1",
                            "work-1",
                            (claim,),
                        ),
                    ),
                ),
            ),
        )
        replayed = MetadataReport.from_json(report.to_json())
        self.assertEqual(
            "B012345678",
            replayed.providers[0].candidates[0].claims[0].value,
        )

    def test_local_date_event_scope_is_event_aware_and_replay_rejects_mismatch(
        self,
    ) -> None:
        claims = tuple(
            LocalClaim.create(
                kind=ClaimKind.DATE_EVENT,
                observed="1998",
                canonical="1998",
                excerpt="First published 1998",
                applicability="not_applicable",
                checksum="not_applicable",
                applicability_basis="not_applicable",
                date_event=event,
                date_precision=DatePrecision.YEAR,
            )
            for event in (
                DateEvent.WORK_FIRST_PUBLICATION,
                DateEvent.CREATION,
                DateEvent.COPYRIGHT,
            )
        )
        self.assertEqual(
            [claim.scope for claim in claims],
            [ClaimScope.WORK, ClaimScope.WORK, ClaimScope.EDITION],
        )
        with self.assertRaises(ReportValueError):
            replace(claims[0], scope=ClaimScope.EDITION)

        report = MetadataReport.create(
            target=TargetFacts(
                "primary",
                "ebooks",
                "item-1",
                default_target_fields("Example"),
            ),
            file=FileFacts(0, "a" * 64, "b" * 64),
            local_claims=claims,
            local_reasons=("opening_date_event",),
            providers=(),
        )
        replay_data = report.to_data()
        replay_data["local"]["claims"][0]["scope"] = "edition"
        with self.assertRaises(ReportValueError):
            MetadataReport.from_json(canonical_json(replay_data))

    def test_local_dates_and_contextual_credits_replay_without_candidate_conflation(
        self,
    ) -> None:
        publication = LocalClaim.create(
            kind=ClaimKind.PUBLICATION_DATE,
            observed="2024-03",
            canonical="2024-03",
            excerpt="This ebook edition published 2024-03",
            applicability="not_applicable",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            date_event=DateEvent.MANIFESTATION_PUBLICATION,
            date_precision=DatePrecision.MONTH,
            occurrence=1,
        )
        conversion = LocalClaim.create(
            kind=ClaimKind.CONTEXTUAL_CREDIT,
            observed="Tool House",
            canonical="Tool House",
            excerpt="EPUB conversion by Tool House",
            applicability="not_applicable",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            role="conversion",
            occurrence=2,
        )
        report = MetadataReport.create(
            target=TargetFacts(
                "primary",
                "ebooks",
                "item-1",
                default_target_fields("Example"),
            ),
            file=FileFacts(0, "a" * 64, "b" * 64),
            local_claims=(publication, conversion),
            local_reasons=("not_applicable",),
            providers=(),
        )
        replayed = MetadataReport.from_json(report.to_json())
        self.assertEqual(replayed, report)
        self.assertEqual(
            replayed.local_claims[0].date_event,
            DateEvent.MANIFESTATION_PUBLICATION,
        )
        self.assertIs(replayed.local_claims[0].date_precision, DatePrecision.MONTH)
        self.assertIs(replayed.local_claims[1].kind, ClaimKind.CONTEXTUAL_CREDIT)
        self.assertNotEqual(replayed.local_claims[1].kind, ClaimKind.CONTRIBUTOR)

    def test_exhausted_provider_reopens_as_typed_terminal_without_attempts(
        self,
    ) -> None:
        selected = TargetFile("a" * 64, "book.epub", "epub")
        snapshot = TargetSnapshot(
            "ebook", "Current title", (selected,)
        )
        target = _Target([snapshot, snapshot])
        claim = LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="9780306406157",
            canonical="9780306406157",
            excerpt="ISBN 9780306406157",
            applicability="ebook",
            basis="opening_matter",
            checksum="valid_isbn_13",
            applicability_basis="ebook",
            scope=ClaimScope.EDITION,
        )
        inspector = _Inspector(InspectionResult("c" * 64, (claim,)))
        dispatches = 0

        def unavailable(_: httpx.Request) -> httpx.Response:
            nonlocal dispatches
            dispatches += 1
            return httpx.Response(
                503,
                headers={"content-type": "application/json"},
                json={"error": {}},
            )

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "reports.sqlite"
            service = GenerateMetadataReport(
                target=target,
                inspector=inspector,
                repository_factory=lambda: open_repository(database),
                providers=(
                    ProviderBinding(
                        "google_books",
                        True,
                        lambda: GoogleBooksMetadataProvider(
                            "google_books",
                            httpx.Client(
                                transport=httpx.MockTransport(unavailable)
                            ),
                            65_536,
                            sleep=lambda _: None,
                            jitter=lambda: 0.0,
                        ),
                        "google_books",
                    ),
                ),
                connection=TargetConnectionConfiguration(
                    "primary",
                    "https://example.invalid",
                    "/private/target",
                ),
                library=LibraryConfiguration(
                    "ebooks",
                    "primary",
                    "library-id",
                    "ebook",
                    "books",
                    ("google_books",),
                ),
                mount=MediaMountConfiguration(
                    "books", "/media", "/library", True
                ),
                limits=ReportLimits(),
            )
            report = MetadataReport.from_json(service.run("item-1", 0))
            repository = open_repository(database, create=False)
            try:
                persisted = repository.get(report.report_id)
            finally:
                repository.close()
        reopened = MetadataReport.from_json(persisted)
        self.assertEqual(6, dispatches)
        self.assertEqual(ProviderStatus.UNAVAILABLE, reopened.providers[0].status)
        self.assertEqual((), reopened.providers[0].candidates)
        self.assertNotEqual(ProviderStatus.NO_MATCH, reopened.providers[0].status)
        self.assertEqual(
            ("http_non_success",),
            tuple(
                value.category.value
                for value in reopened.providers[0].diagnostics
            ),
        )
        self.assertEqual(
            (6,),
            tuple(value.count for value in reopened.providers[0].diagnostics),
        )
        self.assertNotIn('"attempt', persisted)
        for forbidden in (
            "delay_seconds",
            "elapsed_seconds",
            "ordinal",
            "retry-after",
        ):
            self.assertNotIn(forbidden, persisted)

    def test_rejected_cover_state_round_trips_in_serialized_report(self) -> None:
        source = CoverSource(
            cover_source_id(CoverSourceKind.OPEN_LIBRARY, "open_library:edition:OL1M"),
            CoverSourceKind.OPEN_LIBRARY,
            CoverSourceState.REJECTED,
            provenance=CoverProvenance(
                "open_library",
                CoverDesignation.EXACT_EDITION,
                "open-library-work-OL5W",
                "open-library-edition-OL1M",
            ),
        )
        report = MetadataReport.create(
            target=TargetFacts("primary", "ebooks", "item-1", default_target_fields("Current title")),
            file=FileFacts(0, "a" * 64, "b" * 64),
            local_claims=(),
            local_reasons=("no_opening_claims",),
            providers=(),
            cover_sources=(source,),
        )
        serialized = report.to_json()
        self.assertIn('"state":"rejected"', serialized)
        self.assertEqual(report, MetadataReport.from_json(serialized))

    def test_variant_marker_fails_closed_for_abridgement_nouns_and_preserves_token_boundaries(
        self,
    ) -> None:
        for field, value in (
            ("title", "The Example: An Abridgement"),
            ("subtitle", "An Abridgment"),
            ("title", "The Abridged Example"),
            ("subtitle", "An Adapted Translation"),
        ):
            with self.subTest(field=field, value=value):
                self.assertTrue(_variant_marker(value))

        for value in (
            "The Example",
            "Abridgementary Notes",
            "Abridgmental Notes",
            "Adaptational Notes",
            "Translationist Notes",
        ):
            with self.subTest(value=value):
                self.assertFalse(_variant_marker(value))

    def test_lookup_prefers_one_isbn_family_and_otherwise_requires_complete_target_authors(self) -> None:
        def target(
            title: str | None,
            authors: TargetField,
        ) -> TargetSnapshot:
            fields = tuple(
                authors if field.destination is TargetDestination.AUTHORS else field
                for field in default_target_fields(title)
            )
            return TargetSnapshot("ebook", title, (), fields=fields)

        complete = target(
            "The Example",
            TargetField(
                TargetDestination.AUTHORS,
                TargetFieldState.VALUE,
                ("Ada Author", "Ben Writer"),
            ),
        )
        self.assertEqual(
            MetadataLookup(
                ProviderQueryKind.WORK,
                (),
                "The Example",
                ("Ada Author", "Ben Writer"),
            ),
            metadata_lookup(complete, ()),
        )
        for state in (
            TargetFieldState.MISSING,
            TargetFieldState.NULL,
            TargetFieldState.EMPTY,
            TargetFieldState.UNLOADED,
            TargetFieldState.MALFORMED,
            TargetFieldState.OVERSIZED,
        ):
            with self.subTest(state=state):
                incomplete = target(
                    "The Example",
                    TargetField(TargetDestination.AUTHORS, state),
                )
                self.assertEqual(
                    ProviderQueryKind.NONE,
                    metadata_lookup(incomplete, ()).kind,
                )
        ambiguous = target(
            "The Example",
            TargetField(
                TargetDestination.AUTHORS,
                TargetFieldState.VALUE,
                ("Ada Author", "ADA AUTHOR"),
            ),
        )
        self.assertEqual(
            ProviderQueryKind.NONE,
            metadata_lookup(ambiguous, ()).kind,
        )

        isbn = LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="9780306406157",
            canonical="9780306406157",
            excerpt="9780306406157",
            applicability="ebook",
            checksum="valid_isbn_13",
            applicability_basis="ebook",
        )
        self.assertEqual(
            ProviderQueryKind.ISBN,
            metadata_lookup(complete, (isbn,)).kind,
        )
        competing = LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="9780140328721",
            canonical="9780140328721",
            excerpt="9780140328721",
            applicability="ebook",
            checksum="valid_isbn_13",
            applicability_basis="ebook",
        )
        self.assertEqual(
            ProviderQueryKind.NONE,
            metadata_lookup(complete, (isbn, competing)).kind,
        )

        target_title = LocalClaim.create(
            kind=ClaimKind.TITLE,
            observed="The Example",
            canonical="The Example",
            excerpt="The Example",
            applicability="not_applicable",
            basis="target_metadata",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            scope=ClaimScope.WORK,
        )
        plan = metadata_discovery_plan(
            complete, (target_title, isbn, competing)
        )
        self.assertFalse(plan.overflow)
        self.assertEqual(
            [ProviderQueryKind.ISBN, ProviderQueryKind.ISBN, ProviderQueryKind.WORK],
            sorted((lane.kind for lane in plan.lanes), key=lambda value: value.value),
        )
        self.assertEqual(
            {"9780306406157", "9780140328721"},
            {
                lane.matched_family
                for lane in plan.lanes
                if lane.kind is ProviderQueryKind.ISBN
            },
        )

    def test_discovery_plan_deduplicates_families_and_records_convergence(self) -> None:
        title = LocalClaim.create(
            kind=ClaimKind.TITLE,
            observed="The Example",
            canonical="The Example",
            excerpt="The Example",
            applicability="not_applicable",
            basis="target_metadata",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            scope=ClaimScope.WORK,
        )
        isbn_13 = LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="9780306406157",
            canonical="9780306406157",
            excerpt="ebook ISBN 9780306406157",
            applicability="ebook",
            checksum="valid_isbn_13",
            applicability_basis="ebook",
        )
        isbn_10 = LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="0-306-40615-2",
            canonical="0306406152",
            excerpt="print ISBN 0-306-40615-2",
            applicability="print",
            checksum="valid_isbn_10",
            applicability_basis="print",
            basis="opening_matter",
            rear_provenance=RearClaimProvenance(
                "a" * 64, 1, 1, "also_by", "rear.xhtml"
            ),
        )
        fields = tuple(
            TargetField(
                TargetDestination.AUTHORS,
                TargetFieldState.VALUE,
                ("Ada Author",),
            )
            if field.destination is TargetDestination.AUTHORS
            else field
            for field in default_target_fields("The Example")
        )
        plan = metadata_discovery_plan(
            TargetSnapshot("ebook", "The Example", (), fields=fields),
            (title, isbn_13, isbn_10),
        )
        isbn_lane = next(
            lane for lane in plan.lanes if lane.kind is ProviderQueryKind.ISBN
        )
        work_lane = next(
            lane for lane in plan.lanes if lane.kind is ProviderQueryKind.WORK
        )
        self.assertEqual(
            tuple(sorted((isbn_13.reference, isbn_10.reference))),
            isbn_lane.input_references,
        )
        candidate = ProviderCandidate("edition-1", "work-1", ())
        observation = provider_observation(
            "catalogue",
            (
                ProviderLaneResult(
                    isbn_lane, ProviderStatus.CANDIDATES, (candidate,)
                ),
                ProviderLaneResult(
                    work_lane, ProviderStatus.CANDIDATES, (candidate,)
                ),
            ),
        )
        self.assertEqual(1, len(observation.candidates))
        self.assertEqual(2, len(observation.lanes))
        self.assertEqual(1, len(observation.convergence))
        self.assertEqual(
            tuple(sorted(lane.identity for lane in plan.lanes)),
            observation.convergence[0].lane_identities,
        )
        lane_references = {
            value.reference
            for lane in observation.lanes
            for value in lane.candidates
        }
        self.assertEqual(2, len(lane_references))
        report = MetadataReport.create(
            target=TargetFacts(
                "primary", "ebooks", "item-1", fields
            ),
            file=FileFacts(0, "b" * 64, "c" * 64),
            local_claims=(title, isbn_13, isbn_10),
            local_reasons=("opening_matter_isbn",),
            providers=(observation,),
        )
        self.assertEqual(report, MetadataReport.from_json(report.to_json()))
        self.assertEqual("metadata-report-v20", report.schema_version)
        previous = report.to_data()
        previous["schema_version"] = "metadata-report-v13"
        with self.assertRaises(ReportValueError):
            MetadataReport.from_json(canonical_json(previous))

    def test_aggregate_candidate_failures_preserve_independent_lane_statuses(
        self,
    ) -> None:
        claims = tuple(
            LocalClaim.create(
                kind=ClaimKind.ISBN,
                observed=value,
                canonical=value,
                excerpt=f"ISBN {value}",
                applicability="unknown",
                checksum="valid_isbn_13",
                applicability_basis="unlabelled",
            )
            for value in (
                "9780061120084",
                "9780140328721",
                "9780306406157",
                "9780439554930",
            )
        )
        lanes = metadata_discovery_plan(
            TargetSnapshot("ebook", None, ()), claims
        ).lanes
        candidates = tuple(
            ProviderCandidate(f"edition-{index}", f"work-{index}", ())
            for index in range(MAX_CANDIDATES + 1)
        )
        overflow_results = (
            ProviderLaneResult(
                lanes[0], ProviderStatus.CANDIDATES, candidates[:3]
            ),
            ProviderLaneResult(
                lanes[1], ProviderStatus.CANDIDATES, candidates[3:]
            ),
            ProviderLaneResult(lanes[2], ProviderStatus.RATE_LIMITED),
            ProviderLaneResult(lanes[3], ProviderStatus.NO_MATCH),
        )
        conflict_results = (
            ProviderLaneResult(
                lanes[0],
                ProviderStatus.CANDIDATES,
                (ProviderCandidate("edition-shared", "work-one", ()),),
            ),
            ProviderLaneResult(
                lanes[1],
                ProviderStatus.CANDIDATES,
                (ProviderCandidate("edition-shared", "work-two", ()),),
            ),
            ProviderLaneResult(lanes[2], ProviderStatus.RATE_LIMITED),
            ProviderLaneResult(lanes[3], ProviderStatus.NO_MATCH),
        )

        for label, results, expected in (
            (
                "overflow",
                overflow_results,
                ProviderStatus.CANDIDATE_LIMIT_EXCEEDED,
            ),
            ("conflict", conflict_results, ProviderStatus.MALFORMED),
        ):
            observation = provider_observation("catalogue", results)
            replayed_order = provider_observation(
                "catalogue", tuple(reversed(results))
            )
            by_identity = {
                lane.identity: lane.status for lane in observation.lanes
            }
            with self.subTest(label=label):
                self.assertEqual(expected, observation.status)
                self.assertEqual((), observation.candidates)
                self.assertEqual(expected, by_identity[lanes[0].identity])
                self.assertEqual(expected, by_identity[lanes[1].identity])
                self.assertEqual(
                    ProviderStatus.RATE_LIMITED,
                    by_identity[lanes[2].identity],
                )
                self.assertEqual(
                    ProviderStatus.NO_MATCH,
                    by_identity[lanes[3].identity],
                )
                self.assertEqual(observation.to_data(), replayed_order.to_data())

    def test_discovery_plan_reports_exact_family_overflow_without_subset(self) -> None:
        claims: list[LocalClaim] = []
        for index in range(9):
            prefix = f"97800000000{index}"
            check = (
                10
                - sum(
                    int(character) * (1 if position % 2 == 0 else 3)
                    for position, character in enumerate(prefix)
                )
                % 10
            ) % 10
            value = prefix + str(check)
            claims.append(
                LocalClaim.create(
                    kind=ClaimKind.ISBN,
                    observed=value,
                    canonical=value,
                    excerpt=value,
                    applicability="unknown",
                    checksum="valid_isbn_13",
                    applicability_basis="unlabelled",
                )
            )
        plan = metadata_discovery_plan(
            TargetSnapshot("ebook", None, ()), tuple(claims)
        )
        self.assertTrue(plan.overflow)
        self.assertEqual((), plan.lanes)

        selected = TargetFile("a" * 64, "book.epub", "epub")
        snapshot = TargetSnapshot("ebook", None, (selected,))
        target = _Target([snapshot, snapshot])
        inspector = _Inspector(InspectionResult("b" * 64, tuple(claims)))
        repository = _Repository()
        service = GenerateMetadataReport(
            target=target,
            inspector=inspector,
            repository_factory=lambda: repository,
            providers=(
                ProviderBinding(
                    "catalogue",
                    True,
                    lambda: self.fail("overflow must not construct a provider"),
                ),
            ),
            connection=TargetConnectionConfiguration(
                "primary", "https://example.invalid", "/private/target"
            ),
            library=LibraryConfiguration(
                "ebooks",
                "primary",
                "library-id",
                "ebook",
                "books",
                ("catalogue",),
            ),
            mount=MediaMountConfiguration(
                "books", "/media", "/library", True
            ),
            limits=ReportLimits(),
        )
        report = MetadataReport.from_json(service.run("item-1", 0))
        self.assertEqual(
            ProviderStatus.QUERY_LIMIT_EXCEEDED,
            report.providers[0].status,
        )
        self.assertEqual(
            [
                {
                    "category": "lookup_construction",
                    "component": "provider",
                    "count": 1,
                    "current": None,
                    "retryable": False,
                    "stage": "lookup",
                }
            ],
            [
                diagnostic.to_data()
                for diagnostic in report.providers[0].diagnostics
            ],
        )

    def test_work_lookup_admits_one_exact_work_and_strips_edition_authority(self) -> None:
        lookup = MetadataLookup(
            ProviderQueryKind.WORK,
            (),
            "The Example",
            ("Ada Author",),
        )

        def claim(
            candidate: str,
            kind: ClaimKind,
            value: str,
            scope: ClaimScope,
            raw_order: int,
            role: str | None = None,
        ) -> ProviderClaim:
            return ProviderClaim.create(
                provider="catalogue",
                candidate=candidate,
                kind=kind,
                value=value,
                state=FieldState.VALID,
                scope=scope,
                applicability="ebook" if kind is ClaimKind.ISBN else "not_applicable",
                role=role,
                raw_order=raw_order,
            )

        identity = "edition-1"
        candidate = ProviderCandidate(
            identity,
            "work-1",
            (
                claim(identity, ClaimKind.ISBN, "9780306406157", ClaimScope.EDITION, 0),
                claim(identity, ClaimKind.TITLE, "The Example", ClaimScope.WORK, 1),
                claim(
                    identity,
                    ClaimKind.CREATOR,
                    "Ada Author",
                    ClaimScope.WORK,
                    2,
                    "author",
                ),
                claim(identity, ClaimKind.DESCRIPTION, "Work description", ClaimScope.WORK, 3),
            ),
            tuple(
                sorted(
                    (
                        ProviderEvidence(
                            ProviderEvidenceKind.EDITION_COVER_ID,
                            "123",
                            ClaimScope.EDITION,
                        ),
                        ProviderEvidence(
                            ProviderEvidenceKind.CONTRIBUTOR_IDENTITY,
                            "author-1",
                            ClaimScope.WORK,
                            "author",
                        ),
                        ProviderEvidence(
                            ProviderEvidenceKind.FIRST_PUBLICATION_DATE,
                            "2001-02-03",
                            ClaimScope.WORK,
                        ),
                    ),
                    key=lambda value: (
                        value.scope.value,
                        value.kind.value,
                        value.value,
                        value.role or "",
                        value.source_ordinal,
                    ),
                )
            ),
        )
        qualified = qualify_provider_observation(
            ProviderObservation(
                "catalogue",
                ProviderStatus.CANDIDATES,
                (),
                (candidate,),
                ProviderQueryKind.WORK,
            ),
            lookup,
        )
        self.assertEqual(ProviderStatus.CANDIDATES, qualified.status)
        self.assertEqual(
            {ClaimKind.TITLE, ClaimKind.CREATOR, ClaimKind.DESCRIPTION},
            {value.kind for value in qualified.candidates[0].claims},
        )
        self.assertEqual(
            {
                ProviderEvidenceKind.CONTRIBUTOR_IDENTITY,
                ProviderEvidenceKind.FIRST_PUBLICATION_DATE,
            },
            {value.kind for value in qualified.candidates[0].evidence},
        )

    def test_work_lookup_fails_closed_for_competing_work_or_variant_markers(self) -> None:
        lookup = MetadataLookup(
            ProviderQueryKind.WORK,
            (),
            "The Example",
            ("Ada Author",),
        )

        def candidate(
            identity: str,
            work: str,
            role: str | None = None,
            title: str = "The Example",
            subtitle: str | None = None,
        ) -> ProviderCandidate:
            values = [
                ProviderClaim.create(
                    provider="catalogue",
                    candidate=identity,
                    kind=ClaimKind.TITLE,
                    value=title,
                    state=FieldState.VALID,
                    scope=ClaimScope.WORK,
                    applicability="not_applicable",
                    raw_order=0,
                ),
                ProviderClaim.create(
                    provider="catalogue",
                    candidate=identity,
                    kind=ClaimKind.CREATOR,
                    value="Ada Author",
                    state=FieldState.VALID,
                    scope=ClaimScope.WORK,
                    applicability="not_applicable",
                    role="author",
                    raw_order=1,
                ),
            ]
            if role is not None:
                values.append(
                    ProviderClaim.create(
                        provider="catalogue",
                        candidate=identity,
                        kind=ClaimKind.CONTRIBUTOR,
                        value="Pat Contributor",
                        state=FieldState.VALID,
                        scope=ClaimScope.WORK,
                        applicability="not_applicable",
                        role=role,
                        raw_order=2,
                    )
                )
            if subtitle is not None:
                values.append(
                    ProviderClaim.create(
                        provider="catalogue",
                        candidate=identity,
                        kind=ClaimKind.SUBTITLE,
                        value=subtitle,
                        state=FieldState.VALID,
                        scope=ClaimScope.WORK,
                        applicability="not_applicable",
                        raw_order=3,
                    )
                )
            return ProviderCandidate(identity, work, tuple(values))

        def qualify(candidates: tuple[ProviderCandidate, ...]) -> ProviderObservation:
            return qualify_provider_observation(
                ProviderObservation(
                    "catalogue",
                    ProviderStatus.CANDIDATES,
                    (),
                    candidates,
                    ProviderQueryKind.WORK,
                ),
                lookup,
            )

        for candidates in (
            (candidate("edition-1", "work-1"), candidate("edition-2", "work-2")),
            (candidate("edition-1", "work-1", "translator"),),
            (candidate("edition-1", "work-1", "adapter"),),
            (candidate("edition-1", "work-1", "abridger"),),
        ):
            with self.subTest(candidates=candidates):
                result = qualify(candidates)
                self.assertEqual(ProviderStatus.NO_MATCH, result.status)
                self.assertEqual((), result.candidates)

        for spelling in ("Abridgement", "Abridgment"):
            for field, candidates in (
                (
                    "title",
                    (
                        candidate("edition-1", "work-1"),
                        candidate(
                            "edition-2",
                            "work-1",
                            title=f"The Example: An {spelling}",
                        ),
                    ),
                ),
                (
                    "subtitle",
                    (candidate("edition-1", "work-1", subtitle=f"An {spelling}"),),
                ),
            ):
                with self.subTest(field=field, spelling=spelling):
                    result = qualify(candidates)
                    self.assertEqual(ProviderStatus.NO_MATCH, result.status)
                    self.assertEqual((), result.candidates)

        qualified = qualify((candidate("edition-1", "work-1"),))
        self.assertEqual(ProviderStatus.CANDIDATES, qualified.status)
        self.assertEqual(("edition-1",), tuple(value.identity for value in qualified.candidates))

    def test_work_lookup_requires_the_complete_canonical_author_order(self) -> None:
        lookup = MetadataLookup(
            ProviderQueryKind.WORK,
            (),
            "The Example",
            ("Zed Author", "Ada Author"),
        )
        for label, provider_authors, expected in (
            (
                "exact normalized order",
                ("ZED AUTHOR", "ada author"),
                ProviderStatus.CANDIDATES,
            ),
            (
                "same set wrong order",
                ("Ada Author", "Zed Author"),
                ProviderStatus.NO_MATCH,
            ),
            ("missing author", ("Zed Author",), ProviderStatus.NO_MATCH),
            (
                "extra duplicate",
                ("Zed Author", "Ada Author", "Ada Author"),
                ProviderStatus.NO_MATCH,
            ),
            (
                "duplicate replacement",
                ("Zed Author", "Zed Author"),
                ProviderStatus.NO_MATCH,
            ),
        ):
            claims = (
                ProviderClaim.create(
                    provider="catalogue",
                    candidate="edition-1",
                    kind=ClaimKind.TITLE,
                    value="The Example",
                    state=FieldState.VALID,
                    scope=ClaimScope.WORK,
                    applicability="not_applicable",
                    raw_order=0,
                ),
                *(
                    ProviderClaim.create(
                        provider="catalogue",
                        candidate="edition-1",
                        kind=ClaimKind.CREATOR,
                        value=value,
                        state=FieldState.VALID,
                        scope=ClaimScope.WORK,
                        applicability="not_applicable",
                        role="author",
                        raw_order=index,
                    )
                    for index, value in enumerate(provider_authors, start=1)
                ),
            )
            result = qualify_provider_observation(
                ProviderObservation(
                    "catalogue",
                    ProviderStatus.CANDIDATES,
                    (),
                    (ProviderCandidate("edition-1", "work-1", claims),),
                    ProviderQueryKind.WORK,
                ),
                lookup,
            )
            with self.subTest(label=label):
                self.assertEqual(expected, result.status)
                self.assertEqual(
                    expected is ProviderStatus.CANDIDATES,
                    bool(result.candidates),
                )

    def test_closed_claim_vocabulary_is_exact(self) -> None:
        self.assertEqual(
            {kind.value for kind in ClaimKind},
            {
                "title",
                "subtitle",
                "creator",
                "contributor",
                "series",
                "series_sequence",
                "isbn",
                "asin",
            "publication_date",
            "date_event",
            "contextual_credit",
                "publisher",
                "description",
                "language",
                "subject",
                "imprint",
                "genre",
                "tag",
            },
        )

    def test_final_currentness_rejects_absent_abs_source_that_becomes_bytes(self) -> None:
        selected = TargetFile("a" * 64, "book.epub", "epub")
        cover = TargetCover("descriptor", "/cover", "item-1")
        class Target(_Target):
            def read_cover(self, _value: TargetCover, *, deadline=None) -> bytes:
                return b"appeared-after-observation"
        target = Target([
            TargetSnapshot("ebook", "Title", (selected,), cover),
            TargetSnapshot("ebook", "Title", (selected,), cover),
        ])
        class Observer:
            def observe(self, *_args, **_kwargs):
                return (CoverSource(
                    cover_source_id(CoverSourceKind.ABS, "descriptor"),
                    CoverSourceKind.ABS,
                    CoverSourceState.ABSENT,
                ),)
        repository = _Repository()
        service = GenerateMetadataReport(
            target=target, inspector=_Inspector(InspectionResult("c" * 64, ())),
            repository_factory=lambda: repository, providers=(),
            connection=TargetConnectionConfiguration("primary", "https://example.invalid", "/private/target"),
            library=LibraryConfiguration("ebooks", "primary", "library-id", "ebook", "books", ()),
            mount=MediaMountConfiguration("books", "/media", "/library", True),
            limits=ReportLimits(), cover_observer=Observer(),
        )
        with self.assertRaises(CurrentnessError):
            service.run("item-1", 0)
        self.assertEqual({}, repository.values)

    def test_final_currentness_rejects_same_path_abs_byte_drift(self) -> None:
        selected = TargetFile("a" * 64, "book.epub", "epub")
        cover = TargetCover("descriptor", "/cover", "item-1")
        snapshots = [
            TargetSnapshot("ebook", "Title", (selected,), cover),
            TargetSnapshot("ebook", "Title", (selected,), cover),
        ]
        class Target(_Target):
            def read_cover(self, value: TargetCover, *, deadline=None) -> bytes:
                if value != cover:
                    raise AssertionError("unexpected cover")
                return b"changed-after-staging"
        target = Target(snapshots)
        artifact = CoverArtifact.create(b"staged-original", b"preview", "image/png", 1, 1)
        class Observer:
            def observe(self, *_args, **_kwargs):
                return (
                    CoverSource(
                        cover_source_id(CoverSourceKind.ABS, "descriptor"),
                        CoverSourceKind.ABS,
                        CoverSourceState.RETRIEVED,
                        artifact,
                    ),
                )
        repository = _Repository()
        service = GenerateMetadataReport(
            target=target,
            inspector=_Inspector(InspectionResult("c" * 64, ())),
            repository_factory=lambda: repository,
            providers=(),
            connection=TargetConnectionConfiguration("primary", "https://example.invalid", "/private/target"),
            library=LibraryConfiguration("ebooks", "primary", "library-id", "ebook", "books", ()),
            mount=MediaMountConfiguration("books", "/media", "/library", True),
            limits=ReportLimits(),
            cover_observer=Observer(),
        )
        with self.assertRaises(CurrentnessError):
            service.run("item-1", 0)
        self.assertEqual({}, repository.values)

    def test_repository_closes_when_inspection_or_provider_validation_fails(self) -> None:
        selected = TargetFile("a" * 64, "book.epub", "epub")
        repository = _Repository()
        class BrokenInspector:
            def inspect(self, *args): raise RuntimeError("broken")
            def unchanged(self, *args): return True
        service = GenerateMetadataReport(target=_Target([TargetSnapshot("ebook", "Title", (selected,))]), inspector=BrokenInspector(), repository_factory=lambda: repository, providers=(), connection=TargetConnectionConfiguration("primary", "https://example.invalid", "/private/target"), library=LibraryConfiguration("ebooks", "primary", "library-id", "ebook", "books", ()), mount=MediaMountConfiguration("books", "/media", "/library", True), limits=ReportLimits())
        with self.assertRaises(MetadataReportError):
            service.run("item-1", 0)
        self.assertTrue(repository.closed)
        repository = _Repository()
        inspector = _Inspector(InspectionResult("c" * 64, ()))
        class MismatchedProvider:
            def observe(self, claims, *, deadline=None): return ProviderObservation("other", ProviderStatus.NO_MATCH)
        service = GenerateMetadataReport(target=_Target([TargetSnapshot("ebook", "Title", (selected,))]), inspector=inspector, repository_factory=lambda: repository, providers=(ProviderBinding("catalogue", True, MismatchedProvider),), connection=TargetConnectionConfiguration("primary", "https://example.invalid", "/private/target"), library=LibraryConfiguration("ebooks", "primary", "library-id", "ebook", "books", ("catalogue",)), mount=MediaMountConfiguration("books", "/media", "/library", True), limits=ReportLimits())
        with self.assertRaises(MetadataReportError):
            service.run("item-1", 0)
        self.assertTrue(repository.closed)
    def test_f09j_exact_max_shape_fits_two_mebibytes_and_round_trips(self) -> None:
        """The admitted provider envelope fits before its parser is widened."""
        local_claims = tuple(
            LocalClaim.create(
                kind=ClaimKind.CONTRIBUTOR, observed="c" * 280,
                canonical="c" * 280, excerpt="c" * 280,
                applicability="not_applicable", checksum="not_applicable",
                applicability_basis="not_applicable", scope=ClaimScope.WORK,
                role="author", occurrence=index,
            )
            for index in range(1, 65)
        )
        target = LocalClaim.create(
            kind=ClaimKind.TITLE, observed="Current title", canonical="Current title",
            excerpt="Current title", applicability="not_applicable",
            basis="target_metadata", checksum="not_applicable",
            applicability_basis="not_applicable", scope=ClaimScope.WORK,
        )
        local_claims = (target, *local_claims)
        def claim(candidate: str, kind: ClaimKind, value: str, raw_order: int, role: str | None = None) -> ProviderClaim:
            bare = ProviderClaim.create(
                provider="catalogue", candidate=candidate, kind=kind, value=value,
                state=FieldState.VALID,
                scope=ClaimScope.EDITION if kind is ClaimKind.ISBN else ClaimScope.WORK,
                applicability="ebook" if kind is ClaimKind.ISBN else "not_applicable",
                role=role, raw_order=raw_order,
            )
            return ProviderClaim.create(
                provider="catalogue", candidate=candidate, kind=kind, value=value,
                state=FieldState.VALID, scope=bare.scope, applicability=bare.applicability,
                role=role, raw_order=raw_order, links=comparison_links(bare, local_claims),
            )
        candidates = []
        for number in range(MAX_CANDIDATES):
            identity = f"hardcover-edition-{number:010d}"
            claims = [claim(identity, ClaimKind.DESCRIPTION, "d" * MAX_DESCRIPTION_CHARACTERS, 0)]
            claims += [claim(identity, ClaimKind.CONTRIBUTOR, "c" * 512, index + 1, "author") for index in range(MAX_CONTRIBUTIONS)]
            offset = len(claims)
            claims += [claim(identity, ClaimKind.SERIES, f"s{number:03d}-{index:03d}" + "x" * (MAX_SCALAR_CHARACTERS - 8), offset + index) for index in range(MAX_SERIES)]
            offset = len(claims)
            claims += [claim(identity, ClaimKind.TAG, f"t{number:03d}-{index:03d}" + "x" * (MAX_SCALAR_CHARACTERS - 8), offset + index) for index in range(MAX_TAGS)]
            candidates.append(ProviderCandidate(identity, f"work-{identity}", tuple(claims)))
        report = MetadataReport.create(
            target=TargetFacts("primary", "ebooks", "item-1", default_target_fields("Current title")),
            file=FileFacts(0, "a" * 64, "b" * 64), local_claims=local_claims,
            local_reasons=("opening_matter_contributor",),
            providers=(ProviderObservation("catalogue", ProviderStatus.CANDIDATES, (), tuple(candidates)),),
        )
        self.assertLessEqual(len(report.to_json().encode("utf-8")), MAX_REPORT_BYTES)
        self.assertEqual(MetadataReport.from_json(report.to_json()), report)
    def test_field_results_are_complete_canonical_and_separate_from_claims(self) -> None:
        results = derive_field_results(())
        report = MetadataReport.create(
            target=TargetFacts("primary", "ebooks", "item-1", default_target_fields("Current title")),
            file=FileFacts(0, "a" * 64, "b" * 64),
            local_claims=(),
            local_reasons=("no_opening_claims",),
            providers=(),
            field_results=results,
        )
        self.assertEqual(report.field_results, results)
        self.assertEqual(MetadataReport.from_json(report.to_json()), report)
        with self.assertRaises(ReportValueError):
            MetadataReport.create(
                target=report.target, file=report.file, local_claims=(),
                local_reasons=("no_opening_claims",), providers=(),
                field_results=results[:-1],
            )
        contradictory = tuple(
            FieldResult(value.kind, FieldState.VALID, value.sources)
            if value.kind is ClaimKind.TITLE else value
            for value in results
        )
        with self.assertRaises(ReportValueError):
            MetadataReport.create(
                target=report.target, file=report.file, local_claims=(),
                local_reasons=("no_opening_claims",), providers=(),
                field_results=contradictory,
            )
        source_contradiction = tuple(
            FieldResult(
                value.kind,
                value.state,
                (
                    FieldSourceResult(FieldSource.EPUB, FieldState.ABSENT),
                    FieldSourceResult(FieldSource.TARGET_METADATA, FieldState.VALID),
                ),
            ) if value.kind is ClaimKind.TITLE else value
            for value in results
        )
        with self.assertRaises(ReportValueError):
            MetadataReport.create(
                target=report.target, file=report.file, local_claims=(),
                local_reasons=("no_opening_claims",), providers=(),
                field_results=source_contradiction,
            )

    def test_non_valid_observation_state_is_projected_to_its_field_result(self) -> None:
        claim = LocalClaim.create(
            kind=ClaimKind.DESCRIPTION,
            observed="oversized-source-text-sha256:" + "a" * 64,
            canonical=None,
            excerpt="oversized-source-text-sha256:" + "a" * 64,
            applicability="not_applicable",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            basis="package_metadata",
            state=FieldState.OVERSIZED,
        )
        report = MetadataReport.create(
            target=TargetFacts("primary", "ebooks", "item-1", default_target_fields("Current title")),
            file=FileFacts(0, "a" * 64, "b" * 64),
            local_claims=(claim,), local_reasons=("package_metadata_description",), providers=(),
        )
        self.assertEqual(
            dict((value.kind, value.state) for value in report.field_results)[ClaimKind.DESCRIPTION],
            FieldState.OVERSIZED,
        )

    def test_malformed_and_inapplicable_claims_derive_independent_field_results(self) -> None:
        claims = (
            LocalClaim.create(kind=ClaimKind.ISBN, observed="not-an-isbn", canonical=None,
                              excerpt="not-an-isbn", applicability="unknown", checksum="invalid",
                              applicability_basis="unlabelled", state=FieldState.MALFORMED),
            LocalClaim.create(kind=ClaimKind.PUBLISHER, observed="not supplied", canonical="not supplied",
                              excerpt="not supplied", applicability="not_applicable", checksum="not_applicable",
                              applicability_basis="not_applicable", state=FieldState.INAPPLICABLE),
        )
        report = MetadataReport.create(
            target=TargetFacts("primary", "ebooks", "item-1", default_target_fields("Current title")),
            file=FileFacts(0, "a" * 64, "b" * 64), local_claims=claims,
            local_reasons=("no_opening_claims",), providers=(),
        )
        states = {value.kind: value.state for value in report.field_results}
        self.assertEqual(states[ClaimKind.ISBN], FieldState.MALFORMED)
        self.assertEqual(states[ClaimKind.PUBLISHER], FieldState.INAPPLICABLE)

    def test_source_capabilities_keep_target_title_only_and_epub_exact(self) -> None:
        title = LocalClaim.create(
            kind=ClaimKind.TITLE, observed="Current title", canonical="Current title",
            excerpt="Current title", applicability="not_applicable", basis="target_metadata",
            checksum="not_applicable", applicability_basis="not_applicable",
        )
        report = MetadataReport.create(
            target=TargetFacts("primary", "ebooks", "item-1", default_target_fields("Current title")),
            file=FileFacts(0, "a" * 64, "b" * 64), local_claims=(title,),
            local_reasons=("no_opening_claims",), providers=(),
        )
        fields = {value.kind: {source.source: source.state for source in value.sources} for value in report.field_results}
        self.assertEqual(fields[ClaimKind.TITLE][FieldSource.TARGET_METADATA], FieldState.VALID)
        self.assertEqual(fields[ClaimKind.TITLE][FieldSource.EPUB], FieldState.ABSENT)
        self.assertTrue(all(fields[kind][FieldSource.TARGET_METADATA] is FieldState.UNSUPPORTED for kind in ClaimKind if kind is not ClaimKind.TITLE))

    def test_local_claim_source_sentinels_and_public_evidence_are_closed(self) -> None:
        valid = LocalClaim.create(
            kind=ClaimKind.TITLE, observed="Current title", canonical="Current title",
            excerpt="Current title", applicability="not_applicable", basis="target_metadata",
            checksum="not_applicable", applicability_basis="not_applicable",
        )
        with self.assertRaises(ReportValueError):
            LocalClaim.create(
                kind=ClaimKind.PUBLISHER, observed="Example Press", canonical="Example Press",
                excerpt="Example Press", applicability="not_applicable", basis="target_metadata",
                checksum="not_applicable", applicability_basis="not_applicable",
            )
        for prefix, state in (("unsafe-source-text-sha256:", FieldState.UNSUPPORTED), ("oversized-source-text-sha256:", FieldState.OVERSIZED)):
            sentinel = prefix + "a" * 64
            with self.subTest(prefix=prefix):
                claim = LocalClaim.create(
                    kind=ClaimKind.TITLE, observed=sentinel, canonical=None, excerpt=sentinel,
                    applicability="not_applicable", basis="package_metadata",
                    checksum="not_applicable", applicability_basis="not_applicable", state=state,
                )
                self.assertEqual(claim.state, state)
                opposite = FieldState.OVERSIZED if state is FieldState.UNSUPPORTED else FieldState.UNSUPPORTED
                for forged_state, canonical in ((FieldState.VALID, None), (opposite, None), (state, "Forged")):
                    with self.assertRaises(ReportValueError):
                        LocalClaim.create(
                            kind=ClaimKind.TITLE, observed=sentinel, canonical=canonical, excerpt=sentinel,
                            applicability="not_applicable", basis="package_metadata",
                            checksum="not_applicable", applicability_basis="not_applicable", state=forged_state,
                        )
        for field, value in (("observed", "/Users/private/title"), ("canonical", "Authorization: Bearer abcdefgh"), ("excerpt", "/Users/private/title"), ("role", "Authorization: Bearer abcdefgh")):
            values = {"observed": "Ada Example", "canonical": "Ada Example", "excerpt": "Ada Example", "role": None}
            values[field] = value
            with self.subTest(field=field), self.assertRaises(ReportValueError):
                LocalClaim.create(
                    kind=ClaimKind.CREATOR, observed=values["observed"], canonical=values["canonical"],
                    excerpt=values["excerpt"], applicability="not_applicable", basis="package_metadata",
                    checksum="not_applicable", applicability_basis="not_applicable", role=values["role"],
                )
        for field in ("marker", "citation"):
            values = {"marker": "copyright", "citation": "Copyright 2026"}
            values[field] = "/Users/private/title"
            with self.subTest(rear_field=field), self.assertRaises(ReportValueError):
                RearClaimProvenance("a" * 64, 1, 1, values["marker"], values["citation"])
        report = MetadataReport.create(
            target=TargetFacts("primary", "ebooks", "item-1", default_target_fields("Current title")),
            file=FileFacts(0, "a" * 64, "b" * 64), local_claims=(valid,),
            local_reasons=("no_opening_claims",), providers=(),
        )
        data = report.to_data()
        data["local"]["claims"][0]["canonical"] = "/Users/private/title"
        with self.assertRaises(ReportValueError):
            MetadataReport.from_json(canonical_json(data))

        for role in (
            "unsafe-source-text-sha256:" + "a" * 64,
            "oversized-source-text-sha256:" + "a" * 64,
        ):
            claim = LocalClaim.create(
                kind=ClaimKind.CREATOR, observed="Ada Example", canonical="Ada Example",
                excerpt="Ada Example", applicability="not_applicable", basis="package_metadata",
                checksum="not_applicable", applicability_basis="not_applicable", role=role,
            )
            self.assertEqual(claim.role, role)
        for role in ("unsafe-source-text-sha256:" + "A" * 64, "oversized-source-text-sha256:short", "x" * 65):
            with self.subTest(role=role), self.assertRaises(ReportValueError):
                LocalClaim.create(
                    kind=ClaimKind.CREATOR, observed="Ada Example", canonical="Ada Example",
                    excerpt="Ada Example", applicability="not_applicable", basis="package_metadata",
                    checksum="not_applicable", applicability_basis="not_applicable", role=role,
                )
        replay = MetadataReport.create(
            target=TargetFacts("primary", "ebooks", "item-1", default_target_fields("Current title")),
            file=FileFacts(0, "a" * 64, "b" * 64), local_claims=(
                LocalClaim.create(
                    kind=ClaimKind.CREATOR, observed="Ada Example", canonical="Ada Example",
                    excerpt="Ada Example", applicability="not_applicable", basis="package_metadata",
                    checksum="not_applicable", applicability_basis="not_applicable", role="author",
                ),
            ), local_reasons=("package_metadata_creator",), providers=(),
        )
        replay_data = replay.to_data()
        replay_data["local"]["claims"][0]["role"] = "unsafe-source-text-sha256:" + "A" * 64
        with self.assertRaises(ReportValueError):
            MetadataReport.from_json(canonical_json(replay_data))

    def test_provider_claim_and_local_role_rear_provenance_are_closed(self) -> None:
        def provider(kind: ClaimKind, value: str) -> ProviderClaim:
            return ProviderClaim.create(provider="catalogue", candidate="candidate", kind=kind, value=value, state=FieldState.VALID, scope=ClaimScope.EDITION if kind is ClaimKind.ISBN else ClaimScope.WORK, applicability="ebook" if kind is ClaimKind.ISBN else "not_applicable")
        self.assertEqual(provider(ClaimKind.TITLE, "A title").kind, ClaimKind.TITLE)
        self.assertEqual(provider(ClaimKind.ISBN, "9780306406157").kind, ClaimKind.ISBN)
        self.assertEqual(provider(ClaimKind.PUBLISHER, "Example Press").kind, ClaimKind.PUBLISHER)
        for kind, value in ((ClaimKind.TITLE, "/Users/private/title"), (ClaimKind.TITLE, "Authorization: Bearer abcdefgh")):
            with self.subTest(provider_kind=kind, value=value), self.assertRaises(ReportValueError):
                provider(kind, value)
        provenance = RearClaimProvenance("a" * 64, 1, 1, "copyright", "Copyright 2026")
        valid_rear = LocalClaim.create(
            kind=ClaimKind.CREATOR, observed="Ada Example", canonical="Ada Example", excerpt="Ada Example",
            applicability="not_applicable", basis="opening_matter", checksum="not_applicable",
            applicability_basis="not_applicable", role="author", rear_provenance=provenance,
        )
        self.assertEqual(valid_rear.rear_provenance, provenance)
        for kind in (
            ClaimKind.SUBTITLE,
            ClaimKind.PUBLISHER,
            ClaimKind.IMPRINT,
        ):
            with self.subTest(valid_rear_kind=kind):
                claim = LocalClaim.create(
                    kind=kind, observed="Bounded value", canonical="Bounded value",
                    excerpt="Bounded value", applicability="not_applicable",
                    basis="opening_matter", checksum="not_applicable",
                    applicability_basis="not_applicable",
                    rear_provenance=provenance,
                )
                self.assertEqual(claim.rear_provenance, provenance)
        for kind, basis, role, rear in ((ClaimKind.TITLE, "opening_matter", "author", None), (ClaimKind.DESCRIPTION, "opening_matter", None, provenance), (ClaimKind.TITLE, "package_metadata", None, provenance), (ClaimKind.TITLE, "target_metadata", None, provenance)):
            with self.subTest(kind=kind, basis=basis, role=role, rear=rear is not None), self.assertRaises(ReportValueError):
                LocalClaim.create(
                    kind=kind, observed="A title", canonical="A title", excerpt="A title",
                    applicability="not_applicable", basis=basis, checksum="not_applicable",
                    applicability_basis="not_applicable", role=role, rear_provenance=rear,
                )

    def test_comparison_links_are_complete_per_local_claim_and_canonical(self) -> None:
        agree = LocalClaim.create(kind=ClaimKind.TITLE, observed="Shared title", canonical="Shared title", excerpt="Shared title", applicability="not_applicable", checksum="not_applicable", applicability_basis="not_applicable", scope=ClaimScope.WORK)
        disagree = LocalClaim.create(kind=ClaimKind.TITLE, observed="Other title", canonical="Other title", excerpt="Other title", applicability="not_applicable", checksum="not_applicable", applicability_basis="not_applicable", scope=ClaimScope.WORK)
        invalid = LocalClaim.create(kind=ClaimKind.TITLE, observed="unsafe-source-text-sha256:" + "a" * 64, canonical=None, excerpt="unsafe-source-text-sha256:" + "a" * 64, applicability="not_applicable", checksum="not_applicable", applicability_basis="not_applicable", scope=ClaimScope.WORK, state=FieldState.UNSUPPORTED)
        edition = LocalClaim.create(kind=ClaimKind.TITLE, observed="Shared title", canonical="Shared title", excerpt="Shared title", applicability="not_applicable", checksum="not_applicable", applicability_basis="not_applicable", scope=ClaimScope.EDITION)
        author = LocalClaim.create(kind=ClaimKind.CREATOR, observed="Ada", canonical="Ada", excerpt="Ada", applicability="not_applicable", checksum="not_applicable", applicability_basis="not_applicable", scope=ClaimScope.WORK, role="author")
        provider = ProviderClaim.create(provider="catalogue", candidate="candidate", kind=ClaimKind.TITLE, value="Shared title", state=FieldState.VALID, scope=ClaimScope.WORK, applicability="not_applicable")
        links = tuple(sorted((ComparisonLink(agree.reference, Comparison.AGREEMENT, ComparisonMethod.EXACT), ComparisonLink(disagree.reference, Comparison.DISAGREEMENT), ComparisonLink(invalid.reference, Comparison.NOT_COMPARABLE), ComparisonLink(edition.reference, Comparison.NOT_COMPARABLE)), key=lambda link: link.local_reference))
        linked = ProviderClaim.create(provider="catalogue", candidate="candidate", kind=ClaimKind.TITLE, value="Shared title", state=FieldState.VALID, scope=ClaimScope.WORK, applicability="not_applicable", links=links)
        report = MetadataReport.create(target=TargetFacts("primary", "ebooks", "item-1", default_target_fields("Current title")), file=FileFacts(0, "a" * 64, "b" * 64), local_claims=(agree, disagree, invalid, edition, author), local_reasons=("opening_title",), providers=(ProviderObservation("catalogue", ProviderStatus.CANDIDATES, candidates=(ProviderCandidate("candidate", "work-candidate", (linked,)),)),))
        changed_work = MetadataReport.create(target=TargetFacts("primary", "ebooks", "item-1", default_target_fields("Current title")), file=FileFacts(0, "a" * 64, "b" * 64), local_claims=(agree, disagree, invalid, edition, author), local_reasons=("opening_title",), providers=(ProviderObservation("catalogue", ProviderStatus.CANDIDATES, candidates=(ProviderCandidate("candidate", "different-work", (linked,)),)),))
        self.assertEqual(provider.links, ())
        self.assertEqual({link.outcome for link in linked.links}, {Comparison.AGREEMENT, Comparison.DISAGREEMENT, Comparison.NOT_COMPARABLE})
        self.assertNotIn(author.reference, {link.local_reference for link in linked.links})
        self.assertNotEqual(report.report_id, changed_work.report_id)
        self.assertEqual(MetadataReport.from_json(changed_work.to_json()), changed_work)
        contributor = LocalClaim.create(kind=ClaimKind.CREATOR, observed="Ada", canonical="Ada", excerpt="Ada", applicability="not_applicable", checksum="not_applicable", applicability_basis="not_applicable", scope=ClaimScope.WORK, role="translator")
        creator = ProviderClaim.create(provider="catalogue", candidate="candidate", kind=ClaimKind.CREATOR, value="Ada", state=FieldState.VALID, scope=ClaimScope.WORK, applicability="not_applicable", role="author")
        self.assertEqual(
            {link.local_reference: link.outcome for link in comparison_links(creator, (author, contributor))},
            {author.reference: Comparison.AGREEMENT, contributor.reference: Comparison.NOT_COMPARABLE},
        )
        data = report.to_data()
        data["providers"][0]["candidates"][0]["claims"][0]["links"] = []
        with self.assertRaises(ReportValueError):
            MetadataReport.from_json(canonical_json(data))
        data = report.to_data()
        data["providers"][0]["candidates"][0]["claims"][0]["links"][0]["outcome"] = "disagreement"
        with self.assertRaises(ReportValueError):
            MetadataReport.from_json(canonical_json(data))

    def test_title_comparison_methods_are_closed_and_preserve_diacritics(self) -> None:
        def local(
            value: str, kind: ClaimKind = ClaimKind.TITLE
        ) -> LocalClaim:
            return LocalClaim.create(
                kind=kind,
                observed=value,
                canonical=normalize_title(value),
                excerpt=value,
                applicability="not_applicable",
                checksum="not_applicable",
                applicability_basis="not_applicable",
                scope=ClaimScope.WORK,
            )

        cases = (
            ("Exact Title", "Exact Title", ComparisonMethod.EXACT),
            (
                "The Road—Translated",
                "The Road—Translated",
                ComparisonMethod.EXACT,
            ),
            ("CAFE\u0301   SOCIETY", "café society", ComparisonMethod.UNICODE_CASE_WHITESPACE),
            ("The “Long\u00a0Road”—Home", 'the "long road"-home', ComparisonMethod.TYPOGRAPHIC_PUNCTUATION),
        )
        for provider_value, local_value, method in cases:
            claim = local(local_value)
            provider = ProviderClaim.create(
                provider="catalogue",
                candidate="candidate",
                kind=ClaimKind.TITLE,
                value=provider_value,
                state=FieldState.VALID,
                scope=ClaimScope.WORK,
                applicability="not_applicable",
            )
            with self.subTest(method=method):
                self.assertEqual(
                    comparison_links(provider, (claim,)),
                    (
                        ComparisonLink(
                            claim.reference, Comparison.AGREEMENT, method
                        ),
                    ),
                )

        subtitle_claim = local(
            "the road-abridgment", ClaimKind.SUBTITLE
        )
        subtitle_provider = ProviderClaim.create(
            provider="catalogue",
            candidate="candidate",
            kind=ClaimKind.SUBTITLE,
            value="The Road—Abridgment",
            state=FieldState.VALID,
            scope=ClaimScope.WORK,
            applicability="not_applicable",
        )
        self.assertIs(
            comparison_links(subtitle_provider, (subtitle_claim,))[0].outcome,
            Comparison.DISAGREEMENT,
        )

        for provider_value, local_value in (
            ("Cafe", "Café"),
            ("The Road", "The Long Road"),
            ("The Road: Home", "The Road"),
            ("The Road (Translated)", "the road (translated)"),
            ("The Road—Translated", "the road-translated"),
            ("The Road–Translation", "the road-translation"),
            ("The Road—Adapted", "the road-adapted"),
            ("The Road–Adaptation", "the road-adaptation"),
            ("The Road—Variant", "the road-variant"),
            ("The Road–Abridged", "the road-abridged"),
            ("The Road—Abridgement", "the road-abridgement"),
            ("The Road–Abridgment", "the road-abridgment"),
            ("The Road “Translated”", 'the road "translated"'),
            ("The Road ‘Abridged’", "the road 'abridged'"),
            ("The Rod", "The Road"),
            ("The ‘Road", "the 'road"),
            ("The Road’", "the road'"),
            ("The ’Road‘", "the 'road'"),
            ("The “Road’", "the \"road'"),
            ("The ‘“Road’”", "the '\"road'\""),
            ('The "Road"', "the 'road'"),
        ):
            claim = local(local_value)
            provider = ProviderClaim.create(
                provider="catalogue",
                candidate="candidate",
                kind=ClaimKind.TITLE,
                value=provider_value,
                state=FieldState.VALID,
                scope=ClaimScope.WORK,
                applicability="not_applicable",
            )
            with self.subTest(provider=provider_value, local=local_value):
                self.assertEqual(
                    comparison_links(provider, (claim,))[0].outcome,
                    Comparison.DISAGREEMENT,
                )

        for provider_value, local_value in (
            ("The ‘Road’", "the 'road'"),
            ("The “Road”", 'the "road"'),
            ("The “Long ‘Road’”", 'the "long \'road\'"'),
        ):
            claim = local(local_value)
            provider = ProviderClaim.create(
                provider="catalogue",
                candidate="candidate",
                kind=ClaimKind.TITLE,
                value=provider_value,
                state=FieldState.VALID,
                scope=ClaimScope.WORK,
                applicability="not_applicable",
            )
            link = comparison_links(provider, (claim,))[0]
            with self.subTest(paired=provider_value):
                self.assertIs(link.outcome, Comparison.AGREEMENT)
                self.assertIs(
                    link.method, ComparisonMethod.TYPOGRAPHIC_PUNCTUATION
                )

        replay_local = local('The "Road"')
        replay_provider = ProviderClaim.create(
            provider="catalogue",
            candidate="candidate",
            kind=ClaimKind.TITLE,
            value="The “Road”",
            state=FieldState.VALID,
            scope=ClaimScope.WORK,
            applicability="not_applicable",
        )
        replay_candidate = finalize_candidate_comparisons(
            ProviderCandidate("candidate", "work", (replay_provider,)),
            (replay_local,),
        )
        replay_report = MetadataReport.create(
            target=TargetFacts(
                "primary",
                "ebooks",
                "item-1",
                default_target_fields("Current title"),
            ),
            file=FileFacts(0, "a" * 64, "b" * 64),
            local_claims=(replay_local,),
            local_reasons=("opening_title",),
            providers=(
                ProviderObservation(
                    "catalogue",
                    ProviderStatus.CANDIDATES,
                    candidates=(replay_candidate,),
                ),
            ),
        )
        reopened = MetadataReport.from_json(replay_report.to_json())
        self.assertEqual(reopened, replay_report)
        self.assertIs(
            reopened.providers[0].candidates[0].claims[0].links[0].method,
            ComparisonMethod.TYPOGRAPHIC_PUNCTUATION,
        )

    def test_complete_creator_order_and_comma_inversion_are_conservative(self) -> None:
        def local(value: str, occurrence: int = 0) -> LocalClaim:
            return LocalClaim.create(
                kind=ClaimKind.CREATOR,
                observed=value,
                canonical=value,
                excerpt=value,
                applicability="not_applicable",
                basis="package_metadata",
                checksum="not_applicable",
                applicability_basis="not_applicable",
                scope=ClaimScope.WORK,
                role="author",
                occurrence=occurrence,
            )

        def provider(value: str, raw_order: int) -> ProviderClaim:
            return ProviderClaim.create(
                provider="catalogue",
                candidate="candidate",
                kind=ClaimKind.CREATOR,
                value=value,
                state=FieldState.VALID,
                scope=ClaimScope.WORK,
                applicability="not_applicable",
                role="author",
                raw_order=raw_order,
            )

        locals_ = (local("Ada Example"), local("Bob Example", 1))
        ordered = finalize_candidate_comparisons(
            ProviderCandidate(
                "candidate",
                "work",
                (provider("ADA  EXAMPLE", 0), provider("Bob Example", 1)),
            ),
            locals_,
        )
        self.assertEqual(
            tuple(
                link.method
                for claim in ordered.claims
                for link in claim.links
                if link.outcome is Comparison.AGREEMENT
            ),
            (
                ComparisonMethod.UNICODE_CASE_WHITESPACE,
                ComparisonMethod.EXACT,
            ),
        )
        for values in (
            ("Ada Example",),
            ("Bob Example", "Ada Example"),
            ("Ada Example", "Bob Example", "Bob Example"),
        ):
            candidate = finalize_candidate_comparisons(
                ProviderCandidate(
                    "candidate",
                    "work",
                    tuple(provider(value, index) for index, value in enumerate(values)),
                ),
                locals_,
            )
            with self.subTest(values=values):
                self.assertFalse(
                    any(
                        link.outcome is Comparison.AGREEMENT
                        for claim in candidate.claims
                        for link in claim.links
                    )
                )

        one = (local("Ada Example"),)
        for value, expected in (
            ("Example, Ada", ComparisonMethod.CONSERVATIVE_COMMA_INVERSION),
            ("Example, Ada, Jr.", ComparisonMethod.NONE),
            ("Example University, Ada", ComparisonMethod.NONE),
            ("Example, Ada and Bob", ComparisonMethod.NONE),
            ("Penguin, Random House", ComparisonMethod.NONE),
            ("Studios, Universal", ComparisonMethod.NONE),
            ("Smith, John Esq.", ComparisonMethod.NONE),
            ("Smith, John MBE", ComparisonMethod.NONE),
            ("Smith, John OBE", ComparisonMethod.NONE),
            ("Smith, John Ph.D.", ComparisonMethod.NONE),
            ("Smith, John M.D.", ComparisonMethod.NONE),
            ("Example,", ComparisonMethod.NONE),
        ):
            candidate = finalize_candidate_comparisons(
                ProviderCandidate("candidate", "work", (provider(value, 0),)),
                one,
            )
            link = candidate.claims[0].links[0]
            with self.subTest(value=value):
                self.assertIs(link.method, expected)
                self.assertIs(
                    link.outcome,
                    (
                        Comparison.AGREEMENT
                        if expected is not ComparisonMethod.NONE
                        else Comparison.DISAGREEMENT
                    ),
                )
        for organization in ("Library", "Libraries"):
            organization_local = (
                local(f"British {organization}"),
            )
            organization_candidate = finalize_candidate_comparisons(
                ProviderCandidate(
                    "candidate",
                    "work",
                    (provider(f"{organization}, British", 0),),
                ),
                organization_local,
            )
            with self.subTest(organization=organization):
                self.assertIs(
                    organization_candidate.claims[0].links[0].outcome,
                    Comparison.DISAGREEMENT,
                )

    def test_identity_bound_aliases_collapse_only_one_complete_creator_set(self) -> None:
        local_claims = tuple(
            LocalClaim.create(
                kind=ClaimKind.CREATOR,
                observed=value,
                canonical=value,
                excerpt=value,
                applicability="not_applicable",
                basis="package_metadata",
                checksum="not_applicable",
                applicability_basis="not_applicable",
                scope=ClaimScope.WORK,
                role="author",
                occurrence=index,
            )
            for index, value in enumerate(("Ada Example", "Bob Example"))
        )
        claims = tuple(
            ProviderClaim.create(
                provider="catalogue",
                candidate="candidate",
                kind=ClaimKind.CREATOR,
                value=value,
                state=FieldState.VALID,
                scope=ClaimScope.WORK,
                applicability="not_applicable",
                role="author",
                raw_order=index,
            )
            for index, value in enumerate(
                ("A. Example", "Ada Example", "Bob Example")
            )
        )
        bindings = tuple(
            sorted(
                (
                    ProviderIdentityBinding(
                        claim.reference,
                        identity,
                        ClaimScope.WORK,
                        "author",
                        (0, 2, 7)[index],
                    )
                    for index, (claim, identity) in enumerate(
                        zip(
                            claims,
                            ("author-1", "author-1", "author-2"),
                            strict=True,
                        )
                    )
                ),
                key=lambda value: (
                    value.scope.value,
                    value.role,
                    value.source_ordinal,
                    value.identity,
                    value.claim_reference,
                ),
            )
        )
        candidate = finalize_candidate_comparisons(
            ProviderCandidate("candidate", "work", claims, (), bindings),
            local_claims,
        )
        self.assertEqual(
            tuple(
                (
                    claim.value,
                    next(
                        link.method
                        for link in claim.links
                        if link.outcome is Comparison.AGREEMENT
                    ),
                    next(
                        link.local_reference
                        for link in claim.links
                        if link.outcome is Comparison.AGREEMENT
                    ),
                )
                for claim in candidate.claims
            ),
            (
                (
                    "A. Example",
                    ComparisonMethod.STRUCTURAL_IDENTITY_BINDING,
                    local_claims[0].reference,
                ),
                (
                    "Ada Example",
                    ComparisonMethod.EXACT,
                    local_claims[0].reference,
                ),
                (
                    "Bob Example",
                    ComparisonMethod.EXACT,
                    local_claims[1].reference,
                ),
            ),
        )
        replay = MetadataReport.create(
            target=TargetFacts(
                "primary",
                "ebooks",
                "item-1",
                default_target_fields("Current title"),
            ),
            file=FileFacts(0, "a" * 64, "b" * 64),
            local_claims=local_claims,
            local_reasons=("package_metadata_creator",),
            providers=(
                ProviderObservation(
                    "catalogue",
                    ProviderStatus.CANDIDATES,
                    candidates=(candidate,),
                ),
            ),
        )
        self.assertEqual(replay, MetadataReport.from_json(replay.to_json()))
        contradictory_bindings = tuple(
            sorted(
                (
                    replace(bindings[0], source_ordinal=7),
                    replace(bindings[1], source_ordinal=8),
                    replace(bindings[2], source_ordinal=0),
                ),
                key=lambda value: (
                    value.scope.value,
                    value.role,
                    value.source_ordinal,
                    value.identity,
                    value.claim_reference,
                ),
            )
        )
        contradictory = finalize_candidate_comparisons(
            ProviderCandidate(
                "candidate",
                "work",
                claims,
                (),
                contradictory_bindings,
            ),
            local_claims,
        )
        self.assertFalse(
            any(
                link.outcome is Comparison.AGREEMENT
                for claim in contradictory.claims
                for link in claim.links
            )
        )

        direct_claims = claims[1:]
        reversed_bindings = (
            replace(bindings[2], source_ordinal=0),
            replace(bindings[1], source_ordinal=7),
        )
        reversed_bound = finalize_candidate_comparisons(
            ProviderCandidate(
                "candidate",
                "work",
                direct_claims,
                (),
                reversed_bindings,
            ),
            local_claims,
        )
        self.assertFalse(
            any(
                link.outcome is Comparison.AGREEMENT
                for claim in reversed_bound.claims
                for link in claim.links
            )
        )

        partial = finalize_candidate_comparisons(
            ProviderCandidate(
                "candidate",
                "work",
                direct_claims,
                (),
                (replace(bindings[1], source_ordinal=0),),
            ),
            local_claims,
        )
        self.assertEqual(
            (
                ComparisonMethod.EXACT,
                ComparisonMethod.EXACT,
            ),
            tuple(
                link.method
                for claim in partial.claims
                for link in claim.links
                if link.outcome is Comparison.AGREEMENT
            )
        )
        partial_alias = finalize_candidate_comparisons(
            ProviderCandidate(
                "candidate",
                "work",
                claims,
                (),
                (replace(bindings[0], source_ordinal=0),),
            ),
            local_claims,
        )
        self.assertFalse(
            any(
                link.outcome is Comparison.AGREEMENT
                for claim in partial_alias.claims
                for link in claim.links
            )
        )
        partial_replay = MetadataReport.create(
            target=replay.target,
            file=replay.file,
            local_claims=local_claims,
            local_reasons=("package_metadata_creator",),
            providers=(
                ProviderObservation(
                    "catalogue",
                    ProviderStatus.CANDIDATES,
                    candidates=(partial,),
                ),
            ),
        )
        self.assertEqual(
            partial_replay,
            MetadataReport.from_json(partial_replay.to_json()),
        )
        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "reports.sqlite")
            repository.publish(partial_replay, lambda: True)
            self.assertEqual(
                partial_replay,
                MetadataReport.from_json(
                    repository.get(partial_replay.report_id)
                ),
            )
            repository.close()
        duplicate_ordinal = replace(bindings[1], source_ordinal=0)
        with self.assertRaises(ReportValueError):
            ProviderCandidate(
                "candidate",
                "work",
                claims,
                (),
                tuple(
                    sorted(
                        (bindings[0], duplicate_ordinal, bindings[2]),
                        key=lambda value: (
                            value.scope.value,
                            value.role,
                            value.source_ordinal,
                            value.identity,
                            value.claim_reference,
                        ),
                    )
                ),
            )
        tampered = replay.to_data()
        tampered["providers"][0]["candidates"][0]["identity_bindings"][0][
            "claim_reference"
        ] = claims[2].reference
        with self.assertRaises(ReportValueError):
            MetadataReport.from_json(canonical_json(tampered))
        ordinal_tampered = replay.to_data()
        ordinal_tampered["providers"][0]["candidates"][0][
            "identity_bindings"
        ][1]["source_ordinal"] = 0
        serialized_tamper = canonical_json(ordinal_tampered)
        with self.assertRaises(ReportValueError):
            MetadataReport.from_json(serialized_tamper)
        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "reports.sqlite")
            repository.publish(replay, lambda: True)
            repository._connection.execute(
                "DROP TRIGGER metadata_reports_no_update"
            )
            repository._connection.execute(
                "UPDATE metadata_reports SET document=?,document_digest=?",
                (
                    serialized_tamper,
                    hashlib.sha256(serialized_tamper.encode()).hexdigest(),
                ),
            )
            with self.assertRaises(MetadataReportError):
                repository.get(replay.report_id)
            repository.close()

        duplicated_claims = tuple(
            ProviderClaim.create(
                provider="catalogue",
                candidate="duplicated",
                kind=ClaimKind.CREATOR,
                value=value,
                state=FieldState.VALID,
                scope=ClaimScope.WORK,
                applicability="not_applicable",
                role="author",
                raw_order=index,
            )
            for index, value in enumerate(
                ("Ada Example", "ADA  EXAMPLE", "Bob Example")
            )
        )
        duplicated_bindings = tuple(
            ProviderIdentityBinding(
                claim.reference,
                identity,
                ClaimScope.WORK,
                "author",
                index,
            )
            for index, (claim, identity) in enumerate(
                zip(
                    duplicated_claims,
                    ("author-1", "author-1", "author-2"),
                    strict=True,
                )
            )
        )
        duplicated = finalize_candidate_comparisons(
            ProviderCandidate(
                "duplicated",
                "work",
                duplicated_claims,
                (),
                duplicated_bindings,
            ),
            local_claims,
        )
        self.assertFalse(
            any(
                link.outcome is Comparison.AGREEMENT
                for claim in duplicated.claims
                for link in claim.links
            )
        )

        duplicate_local = tuple(
            LocalClaim.create(
                kind=ClaimKind.CREATOR,
                observed="Ada Example",
                canonical="Ada Example",
                excerpt="Ada Example",
                applicability="not_applicable",
                basis="package_metadata",
                checksum="not_applicable",
                applicability_basis="not_applicable",
                scope=ClaimScope.WORK,
                role="author",
                occurrence=index,
            )
            for index in range(2)
        )
        duplicate_provider = tuple(
            ProviderClaim.create(
                provider="catalogue",
                candidate="duplicates",
                kind=ClaimKind.CREATOR,
                value="Ada Example",
                state=FieldState.VALID,
                scope=ClaimScope.WORK,
                applicability="not_applicable",
                role="author",
                raw_order=index,
            )
            for index in range(2)
        )
        for provider_values, local_values in (
            (duplicate_provider, local_claims),
            (claims[1:], duplicate_local),
            (duplicate_provider, duplicate_local),
        ):
            duplicate_set = finalize_candidate_comparisons(
                ProviderCandidate("duplicates", "work", provider_values),
                local_values,
            )
            with self.subTest(
                provider_count=len(provider_values),
                local_count=len(local_values),
            ):
                self.assertFalse(
                    any(
                        link.outcome is Comparison.AGREEMENT
                        for claim in duplicate_set.claims
                        for link in claim.links
                    )
                )

    def test_date_comparison_normalizes_both_lexicals_without_losing_precision(
        self,
    ) -> None:
        def local(value: str, precision: DatePrecision) -> LocalClaim:
            return LocalClaim.create(
                kind=ClaimKind.PUBLICATION_DATE,
                observed=value,
                canonical=value,
                excerpt=value,
                applicability="not_applicable",
                basis="package_metadata",
                checksum="not_applicable",
                applicability_basis="not_applicable",
                scope=ClaimScope.EDITION,
                date_event=DateEvent.MANIFESTATION_PUBLICATION,
                date_precision=precision,
            )

        zulu = local("2024-03-05T14:30:00Z", DatePrecision.TIMESTAMP)
        positive_offset = local(
            "2024-03-05T11:30:00+01:00",
            DatePrecision.TIMESTAMP,
        )
        rollover = local(
            "2024-03-05T23:30:00Z",
            DatePrecision.TIMESTAMP,
        )
        different = local(
            "2024-03-05T14:30:01Z",
            DatePrecision.TIMESTAMP,
        )
        day = local("2024-03-05", DatePrecision.DAY)
        naive = local(
            "2024-03-05T14:30:00",
            DatePrecision.TIMESTAMP,
        )
        invalid = local(
            "2024-02-30T14:30:00Z",
            DatePrecision.TIMESTAMP,
        )

        def provider(value: str) -> ProviderClaim:
            return ProviderClaim.create(
                provider="catalogue",
                candidate="edition-1",
                kind=ClaimKind.PUBLICATION_DATE,
                value=value,
                state=FieldState.VALID,
                scope=ClaimScope.EDITION,
                applicability="not_applicable",
            )

        negative_offset = provider("2024-03-05T10:30:00-04:00")
        self.assertEqual(
            {
                zulu.reference: Comparison.AGREEMENT,
                different.reference: Comparison.DISAGREEMENT,
                day.reference: Comparison.DISAGREEMENT,
            },
            {
                link.local_reference: link.outcome
                for link in comparison_links(
                    negative_offset,
                    (zulu, different, day),
                )
            },
        )
        self.assertEqual(
            (
                ComparisonLink(
                    positive_offset.reference,
                    Comparison.AGREEMENT,
                    ComparisonMethod.CLOSED_FIELD_NORMALIZATION,
                ),
            ),
            comparison_links(
                provider("2024-03-05T10:30:00Z"),
                (positive_offset,),
            ),
        )
        self.assertEqual(
            (
                ComparisonLink(
                    rollover.reference,
                    Comparison.AGREEMENT,
                    ComparisonMethod.CLOSED_FIELD_NORMALIZATION,
                ),
            ),
            comparison_links(
                provider("2024-03-06T00:30:00+01:00"),
                (rollover,),
            ),
        )
        for malformed in (naive, invalid):
            with (
                self.subTest(value=malformed.canonical),
                self.assertRaises(ReportValueError),
            ):
                comparison_links(negative_offset, (malformed,))
        with self.assertRaises(ReportValueError):
            comparison_links(
                provider("2024-03-05T14:30:00"),
                (zulu,),
            )
        self.assertEqual("2024-03-05T14:30:00Z", zulu.canonical)
        self.assertEqual(
            "2024-03-05T10:30:00-04:00",
            negative_offset.date_lexical,
        )

    def test_identifier_evidence_classifies_provider_local_work_relations(self) -> None:
        def local(
            value: str,
            *,
            basis: str,
            applicability: str,
            applicability_basis: str,
            occurrence: int,
        ) -> LocalClaim:
            return LocalClaim.create(
                kind=ClaimKind.ISBN,
                observed=value,
                canonical=value,
                excerpt=value,
                applicability=applicability,
                basis=basis,
                checksum=f"valid_isbn_{len(value)}",
                applicability_basis=applicability_basis,
                scope=ClaimScope.EDITION,
                occurrence=occurrence,
            )

        package = local(
            "9780306406157",
            basis="package_metadata",
            applicability="unknown",
            applicability_basis="unlabelled",
            occurrence=1,
        )
        opening = local(
            "0306406152",
            basis="opening_matter",
            applicability="ebook",
            applicability_basis="ebook",
            occurrence=2,
        )
        same_work_print = local(
            "9780140328721",
            basis="opening_matter",
            applicability="print",
            applicability_basis="paperback",
            occurrence=3,
        )
        other_work = local(
            "9783161484100",
            basis="opening_matter",
            applicability="unknown",
            applicability_basis="unlabelled",
            occurrence=4,
        )
        unresolved = local(
            "9781861972712",
            basis="opening_matter",
            applicability="ambiguous",
            applicability_basis="ambiguous",
            occurrence=5,
        )
        malformed = LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="9780306406158",
            canonical=None,
            excerpt="9780306406158",
            applicability="ebook",
            basis="opening_matter",
            checksum="invalid",
            applicability_basis="ebook",
            scope=ClaimScope.EDITION,
            state=FieldState.MALFORMED,
            occurrence=6,
        )
        local_claims = (
            package,
            opening,
            same_work_print,
            other_work,
            unresolved,
            malformed,
        )

        def provider_claim(
            candidate: str,
            value: str,
            raw_order: int,
        ) -> ProviderClaim:
            bare = ProviderClaim.create(
                provider="catalogue",
                candidate=candidate,
                kind=ClaimKind.ISBN,
                value=value,
                state=FieldState.VALID,
                scope=ClaimScope.EDITION,
                applicability="ebook",
                raw_order=raw_order,
            )
            return ProviderClaim.create(
                provider="catalogue",
                candidate=candidate,
                kind=ClaimKind.ISBN,
                value=value,
                state=FieldState.VALID,
                scope=ClaimScope.EDITION,
                applicability="ebook",
                raw_order=raw_order,
                links=comparison_links(bare, local_claims),
            )

        seed = provider_claim("edition-seed", "9780306406157", 0)
        alternate = provider_claim("edition-alternate", "9780140328721", 0)
        unrelated = provider_claim("edition-unrelated", "9783161484100", 0)
        providers = (
            ProviderObservation(
                "catalogue",
                ProviderStatus.CANDIDATES,
                (),
                (
                    ProviderCandidate(
                        "edition-alternate",
                        "work-one",
                        (alternate,),
                    ),
                    ProviderCandidate("edition-seed", "work-one", (seed,)),
                    ProviderCandidate(
                        "edition-unrelated",
                        "work-two",
                        (unrelated,),
                    ),
                ),
            ),
        )

        evidence = derive_identifier_evidence(local_claims, providers)
        by_reference = {item.reference: item for item in evidence}
        self.assertEqual(
            {item.reference for item in evidence},
            {
                *(claim.reference for claim in local_claims),
                seed.reference,
                alternate.reference,
                unrelated.reference,
            },
        )
        self.assertEqual(
            by_reference[package.reference].relation,
            IdentifierRelation.ASSOCIATED,
        )
        self.assertEqual(
            by_reference[opening.reference].relation,
            IdentifierRelation.ACCEPTED,
        )
        self.assertEqual(
            by_reference[package.reference].basis,
            IdentifierEvidenceBasis.PROVIDER_WORK_IDENTITY,
        )
        self.assertEqual(
            by_reference[opening.reference].basis,
            IdentifierEvidenceBasis.EXPLICIT_EBOOK,
        )
        self.assertEqual(
            by_reference[same_work_print.reference].relation,
            IdentifierRelation.ASSOCIATED,
        )
        self.assertEqual(
            by_reference[same_work_print.reference].basis,
            IdentifierEvidenceBasis.PROVIDER_WORK_IDENTITY,
        )
        self.assertEqual(
            by_reference[other_work.reference].relation,
            IdentifierRelation.UNRELATED,
        )
        self.assertEqual(
            by_reference[unresolved.reference].relation,
            IdentifierRelation.UNRESOLVED,
        )
        self.assertEqual(
            by_reference[malformed.reference].relation,
            IdentifierRelation.INVALID,
        )
        self.assertEqual(
            by_reference[seed.reference].relation,
            IdentifierRelation.ACCEPTED,
        )
        self.assertEqual(
            by_reference[alternate.reference].relation,
            IdentifierRelation.ASSOCIATED,
        )
        self.assertEqual(
            by_reference[unrelated.reference].relation,
            IdentifierRelation.UNRELATED,
        )
        self.assertEqual(by_reference[alternate.reference].provider, "catalogue")
        self.assertEqual(
            by_reference[alternate.reference].candidate_identity,
            "edition-alternate",
        )
        self.assertEqual(
            by_reference[alternate.reference].work_identity,
            "work-one",
        )
        self.assertEqual(
            set(by_reference[alternate.reference].to_data()),
            {
                "basis",
                "candidate_identity",
                "provider",
                "reference",
                "related_references",
                "relation",
                "work_identity",
            },
        )
        self.assertTrue(
            {
                "observed",
                "canonical",
                "excerpt",
                "resource_identity",
            }.isdisjoint(by_reference[alternate.reference].to_data())
        )

        report = MetadataReport.create(
            target=TargetFacts("primary", "ebooks", "item-1", default_target_fields("Current title")),
            file=FileFacts(0, "a" * 64, "b" * 64),
            local_claims=local_claims,
            local_reasons=("opening_matter_isbn",),
            providers=providers,
        )
        self.assertEqual(report.identifier_evidence, evidence)
        self.assertEqual(MetadataReport.from_json(report.to_json()), report)
        self.assertEqual(report.local_claims, local_claims)

    def test_identifier_evidence_keeps_provider_work_collisions_unresolved(
        self,
    ) -> None:
        accepted = LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="9780306406157",
            canonical="9780306406157",
            excerpt="9780306406157",
            applicability="ebook",
            basis="opening_matter",
            checksum="valid_isbn_13",
            applicability_basis="ebook",
            scope=ClaimScope.EDITION,
            occurrence=1,
        )
        alternate = LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="9780140328721",
            canonical="9780140328721",
            excerpt="9780140328721",
            applicability="print",
            basis="opening_matter",
            checksum="valid_isbn_13",
            applicability_basis="paperback",
            scope=ClaimScope.EDITION,
            occurrence=2,
        )

        def provider_claim(
            provider: str,
            candidate: str,
            value: str,
            raw_order: int,
        ) -> ProviderClaim:
            return ProviderClaim.create(
                provider=provider,
                candidate=candidate,
                kind=ClaimKind.ISBN,
                value=value,
                state=FieldState.VALID,
                scope=ClaimScope.EDITION,
                applicability="ebook",
                raw_order=raw_order,
            )

        collision_seed_a = provider_claim(
            "collision", "edition-a", "9780306406157", 0
        )
        collision_alternate_a = provider_claim(
            "collision", "edition-a", "9780140328721", 1
        )
        collision_seed_b = provider_claim(
            "collision", "edition-b", "9780306406157", 0
        )
        collision_alternate_b = provider_claim(
            "collision", "edition-b", "9783161484100", 1
        )
        collision = ProviderObservation(
            "collision",
            ProviderStatus.CANDIDATES,
            (),
            (
                ProviderCandidate(
                    "edition-a",
                    "work-a",
                    (collision_seed_a, collision_alternate_a),
                ),
                ProviderCandidate(
                    "edition-b",
                    "work-b",
                    (collision_seed_b, collision_alternate_b),
                ),
            ),
        )

        collision_evidence = {
            item.reference: item
            for item in derive_identifier_evidence(
                (accepted, alternate),
                (collision,),
            )
        }
        self.assertEqual(
            collision_evidence[alternate.reference].relation,
            IdentifierRelation.UNRESOLVED,
        )
        self.assertEqual(
            collision_evidence[collision_alternate_a.reference].relation,
            IdentifierRelation.UNRESOLVED,
        )
        self.assertEqual(
            collision_evidence[collision_alternate_b.reference].relation,
            IdentifierRelation.UNRESOLVED,
        )

        independent_seed = provider_claim(
            "independent", "edition-c", "9780306406157", 0
        )
        independent_alternate = provider_claim(
            "independent", "edition-c", "9780140328721", 1
        )
        independent = ProviderObservation(
            "independent",
            ProviderStatus.CANDIDATES,
            (),
            (
                ProviderCandidate(
                    "edition-c",
                    "work-c",
                    (independent_seed, independent_alternate),
                ),
            ),
        )
        combined_evidence = {
            item.reference: item
            for item in derive_identifier_evidence(
                (accepted, alternate),
                (collision, independent),
            )
        }
        self.assertEqual(
            combined_evidence[independent_alternate.reference].relation,
            IdentifierRelation.ASSOCIATED,
        )
        self.assertEqual(
            combined_evidence[alternate.reference].relation,
            IdentifierRelation.ASSOCIATED,
        )

    def test_identifier_evidence_preserves_repeats_conflicts_and_rejects_tampering(self) -> None:
        provenance = RearClaimProvenance(
            "a" * 64,
            12,
            1,
            "copyright",
            "Copyright",
        )
        claims = (
            LocalClaim.create(
                kind=ClaimKind.ISBN,
                observed="9780306406157",
                canonical="9780306406157",
                excerpt="9780306406157",
                applicability="unknown",
                basis="package_metadata",
                checksum="valid_isbn_13",
                applicability_basis="unlabelled",
                occurrence=1,
            ),
            LocalClaim.create(
                kind=ClaimKind.ISBN,
                observed="0306406152",
                canonical="0306406152",
                excerpt="0306406152",
                applicability="unknown",
                basis="opening_matter",
                checksum="valid_isbn_10",
                applicability_basis="unlabelled",
                rear_provenance=provenance,
                occurrence=2,
            ),
            LocalClaim.create(
                kind=ClaimKind.ISBN,
                observed="0306406152",
                canonical="0306406152",
                excerpt="0306406152",
                applicability="unknown",
                basis="opening_matter",
                checksum="valid_isbn_10",
                applicability_basis="unlabelled",
                rear_provenance=provenance,
                occurrence=3,
            ),
            LocalClaim.create(
                kind=ClaimKind.ISBN,
                observed="9780140328721",
                canonical="9780140328721",
                excerpt="9780140328721",
                applicability="ambiguous",
                basis="opening_matter",
                checksum="valid_isbn_13",
                applicability_basis="ambiguous",
                rear_provenance=provenance,
                occurrence=4,
            ),
        )
        evidence = derive_identifier_evidence(claims, ())
        self.assertEqual(len(evidence), len(claims))
        self.assertEqual(
            [item.relation for item in evidence],
            [
                IdentifierRelation.ACCEPTED,
                IdentifierRelation.ACCEPTED,
                IdentifierRelation.ACCEPTED,
                IdentifierRelation.UNRESOLVED,
            ],
        )
        primary_package = LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="9783161484100",
            canonical="9783161484100",
            excerpt="9783161484100",
            applicability="ebook",
            basis="package_metadata",
            checksum="valid_isbn_13",
            applicability_basis="ebook",
            occurrence=5,
        )
        primary_evidence = derive_identifier_evidence((primary_package,), ())[0]
        self.assertEqual(
            (primary_evidence.relation, primary_evidence.basis),
            (
                IdentifierRelation.ACCEPTED,
                IdentifierEvidenceBasis.EXPLICIT_EBOOK,
            ),
        )
        inferred_package = LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="9783161484100",
            canonical="9783161484100",
            excerpt="9783161484100",
            applicability="unknown",
            basis="package_metadata",
            checksum="valid_isbn_13",
            applicability_basis="unlabelled",
            occurrence=6,
        )
        inferred_evidence = derive_identifier_evidence((inferred_package,), ())[0]
        self.assertEqual(
            (inferred_evidence.relation, inferred_evidence.basis),
            (
                IdentifierRelation.UNRESOLVED,
                IdentifierEvidenceBasis.INSUFFICIENT_CONTEXT,
            ),
        )
        report = MetadataReport.create(
            target=TargetFacts("primary", "ebooks", "item-1", default_target_fields("Current title")),
            file=FileFacts(0, "a" * 64, "b" * 64),
            local_claims=claims,
            local_reasons=("opening_matter_isbn",),
            providers=(),
        )
        self.assertEqual(
            [claim.to_data() for claim in report.local_claims],
            [claim.to_data() for claim in claims],
        )
        data = report.to_data()
        data["identifier_evidence"][0]["relation"] = "associated"
        with self.assertRaises(ReportValueError):
            MetadataReport.from_json(canonical_json(data))

    def test_identifier_evidence_is_claim_local_under_applicability_conflicts(self) -> None:
        def claim(
            value: str,
            *,
            basis: str,
            applicability: str,
            applicability_basis: str,
            occurrence: int,
        ) -> LocalClaim:
            return LocalClaim.create(
                kind=ClaimKind.ISBN,
                observed=value,
                canonical=value,
                excerpt=value,
                applicability=applicability,
                basis=basis,
                checksum="valid_isbn_13",
                applicability_basis=applicability_basis,
                occurrence=occurrence,
            )

        secondary_package = claim(
            "9780306406157",
            basis="package_metadata",
            applicability="unknown",
            applicability_basis="unlabelled",
            occurrence=1,
        )
        paperback = claim(
            "9780306406157",
            basis="opening_matter",
            applicability="print",
            applicability_basis="paperback",
            occurrence=2,
        )
        primary_ebook = claim(
            "9780140328721",
            basis="package_metadata",
            applicability="ebook",
            applicability_basis="ebook",
            occurrence=3,
        )
        primary_print = claim(
            "9780140328721",
            basis="opening_matter",
            applicability="print",
            applicability_basis="hardcover",
            occurrence=4,
        )
        unknown = claim(
            "9783161484100",
            basis="package_metadata",
            applicability="unknown",
            applicability_basis="unlabelled",
            occurrence=5,
        )
        explicit_ebook = claim(
            "9783161484100",
            basis="opening_matter",
            applicability="ebook",
            applicability_basis="ebook",
            occurrence=6,
        )
        ambiguous = claim(
            "9783161484100",
            basis="opening_matter",
            applicability="ambiguous",
            applicability_basis="ambiguous",
            occurrence=7,
        )
        explicit_print = claim(
            "9783161484100",
            basis="opening_matter",
            applicability="print",
            applicability_basis="print",
            occurrence=8,
        )
        evidence = {
            item.reference: item
            for item in derive_identifier_evidence(
                (
                    secondary_package,
                    paperback,
                    primary_ebook,
                    primary_print,
                    unknown,
                    explicit_ebook,
                    ambiguous,
                    explicit_print,
                ),
                (),
            )
        }
        self.assertEqual(
            {
                claim.reference: evidence[claim.reference].relation
                for claim in (
                    secondary_package,
                    paperback,
                    primary_ebook,
                    primary_print,
                    unknown,
                    explicit_ebook,
                    ambiguous,
                    explicit_print,
                )
            },
            {
                secondary_package.reference: IdentifierRelation.UNRESOLVED,
                paperback.reference: IdentifierRelation.UNRESOLVED,
                primary_ebook.reference: IdentifierRelation.ACCEPTED,
                primary_print.reference: IdentifierRelation.UNRESOLVED,
                unknown.reference: IdentifierRelation.UNRESOLVED,
                explicit_ebook.reference: IdentifierRelation.ACCEPTED,
                ambiguous.reference: IdentifierRelation.UNRESOLVED,
                explicit_print.reference: IdentifierRelation.UNRESOLVED,
            },
        )
        self.assertEqual(
            evidence[primary_ebook.reference].basis,
            IdentifierEvidenceBasis.EXPLICIT_EBOOK,
        )
        self.assertEqual(
            evidence[explicit_ebook.reference].basis,
            IdentifierEvidenceBasis.EXPLICIT_EBOOK,
        )

    def test_maximum_description_comparison_is_field_appropriate(self) -> None:
        local_value = "d" * 512
        provider_value = "p" * MAX_DESCRIPTION_CHARACTERS
        local = LocalClaim.create(kind=ClaimKind.DESCRIPTION, observed=local_value, canonical=local_value, excerpt=local_value, applicability="not_applicable", checksum="not_applicable", applicability_basis="not_applicable", scope=ClaimScope.WORK)
        bare = ProviderClaim.create(provider="catalogue", candidate="candidate", kind=ClaimKind.DESCRIPTION, value=provider_value, state=FieldState.VALID, scope=ClaimScope.WORK, applicability="not_applicable")
        self.assertEqual(comparison_links(bare, (local,)), (ComparisonLink(local.reference, Comparison.DISAGREEMENT),))

    def test_edition_field_scope_agrees_with_same_scope_provider_evidence(self) -> None:
        for kind in (ClaimKind.PUBLICATION_DATE, ClaimKind.PUBLISHER, ClaimKind.IMPRINT, ClaimKind.LANGUAGE):
            self.assertEqual(claim_scope(kind), ClaimScope.EDITION)
        local = LocalClaim.create(kind=ClaimKind.PUBLISHER, observed="Press", canonical="Press", excerpt="Press", applicability="not_applicable", checksum="not_applicable", applicability_basis="not_applicable", scope=claim_scope(ClaimKind.PUBLISHER))
        imprint = LocalClaim.create(kind=ClaimKind.IMPRINT, observed="Press", canonical="Press", excerpt="Press", applicability="not_applicable", checksum="not_applicable", applicability_basis="not_applicable", scope=claim_scope(ClaimKind.IMPRINT))
        provider = ProviderClaim.create(provider="catalogue", candidate="candidate", kind=ClaimKind.PUBLISHER, value="Press", state=FieldState.VALID, scope=ClaimScope.EDITION, applicability="not_applicable")
        provider_imprint = ProviderClaim.create(provider="catalogue", candidate="candidate", kind=ClaimKind.IMPRINT, value="Press", state=FieldState.VALID, scope=ClaimScope.EDITION, applicability="not_applicable")
        self.assertEqual(comparison_links(provider, (local, imprint)), (ComparisonLink(local.reference, Comparison.AGREEMENT, ComparisonMethod.EXACT),))
        self.assertEqual(comparison_links(provider_imprint, (local, imprint)), (ComparisonLink(imprint.reference, Comparison.AGREEMENT, ComparisonMethod.EXACT),))
        normalized_provider = ProviderClaim.create(provider="catalogue", candidate="candidate", kind=ClaimKind.PUBLISHER, value="PRESS", state=FieldState.VALID, scope=ClaimScope.EDITION, applicability="not_applicable")
        self.assertEqual(
            comparison_links(normalized_provider, (local,)),
            (
                ComparisonLink(
                    local.reference,
                    Comparison.AGREEMENT,
                    ComparisonMethod.CLOSED_FIELD_NORMALIZATION,
                ),
            ),
        )
        normalized_local = LocalClaim.create(kind=ClaimKind.PUBLISHER, observed="Press  House", canonical="Press House", excerpt="Press  House", applicability="not_applicable", checksum="not_applicable", applicability_basis="not_applicable", scope=ClaimScope.EDITION)
        canonical_provider = ProviderClaim.create(provider="catalogue", candidate="candidate", kind=ClaimKind.PUBLISHER, value="Press House", state=FieldState.VALID, scope=ClaimScope.EDITION, applicability="not_applicable")
        self.assertEqual(
            comparison_links(canonical_provider, (normalized_local,)),
            (
                ComparisonLink(
                    normalized_local.reference,
                    Comparison.AGREEMENT,
                    ComparisonMethod.CLOSED_FIELD_NORMALIZATION,
                ),
            ),
        )

    def test_non_valid_title_sentinel_cannot_become_canonical(self) -> None:
        sentinel = "unsafe-source-text-sha256:" + "a" * 64
        claim = LocalClaim.create(
            kind=ClaimKind.TITLE, observed=sentinel, canonical=None, excerpt=sentinel,
            applicability="not_applicable", basis="package_metadata", checksum="not_applicable",
            applicability_basis="not_applicable", state=FieldState.UNSUPPORTED,
        )
        report = MetadataReport.create(
            target=TargetFacts("primary", "ebooks", "item-1", default_target_fields("Current title")),
            file=FileFacts(0, "a" * 64, "b" * 64), local_claims=(claim,),
            local_reasons=("package_metadata_title",), providers=(),
        )
        self.assertIsNone(report.local_claims[0].canonical)
        self.assertEqual(report.field_results[0].state, FieldState.UNSUPPORTED)

    def test_closed_local_field_vocabulary_accepts_package_and_contextual_evidence(self) -> None:
        publisher = LocalClaim.create(
            kind=ClaimKind.PUBLISHER,
            observed="Example Press",
            canonical="Example Press",
            excerpt="Publisher: Example Press",
            applicability="not_applicable",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            basis="package_metadata",
        )
        subject = LocalClaim.create(
            kind=ClaimKind.SUBJECT,
            observed="Speculative fiction",
            canonical=None,
            excerpt="Subject: Speculative fiction",
            applicability="not_applicable",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            basis="package_metadata",
        )
        self.assertEqual(publisher.kind, ClaimKind.PUBLISHER)
        self.assertIsNone(subject.canonical)

    def test_report_is_closed_canonical_and_content_addressed(self) -> None:
        claim = LocalClaim.create(
            kind=ClaimKind.TITLE,
            observed="A title",
            canonical="A title",
            excerpt="A title",
            applicability="not_applicable",
            checksum="not_applicable",
            applicability_basis="not_applicable",
        )
        report = MetadataReport.create(
            target=TargetFacts("primary", "ebooks", "item-1", default_target_fields("Current title")),
            file=FileFacts(0, "a" * 64, "b" * 64),
            local_claims=(claim,),
            local_reasons=("opening_title",),
            providers=(ProviderObservation("catalogue", ProviderStatus.DISABLED),),
        )
        self.assertEqual(MetadataReport.from_json(report.to_json()), report)
        self.assertTrue(report.report_id.startswith("report_"))
        self.assertEqual(report.local_status, "candidate")

    def test_title_claims_retain_exact_512_characters_but_isbn_evidence_stays_bounded(self) -> None:
        title = "T" * 512
        claim = LocalClaim.create(
            kind=ClaimKind.TITLE,
            observed=title,
            canonical=title,
            excerpt=title,
            applicability="not_applicable",
            basis="target_metadata",
            checksum="not_applicable",
            applicability_basis="not_applicable",
        )
        self.assertEqual(claim.observed, title)
        self.assertEqual(TargetFacts("primary", "ebooks", "item-1", default_target_fields(title)).current_title, title)
        with self.assertRaises(ReportValueError):
            LocalClaim.create(
                kind=ClaimKind.ISBN,
                observed="9" * 281,
                canonical=None,
                excerpt="9" * 281,
                applicability="unknown",
                checksum="invalid",
                applicability_basis="unlabelled",
            )

    def test_safe_observed_titles_preserve_exact_text_and_canonicalize_claims(self) -> None:
        for title, canonical in (
            ("Cafe\u0301", "Café"),
            ("An  intentional  title", "An intentional title"),
        ):
            claim = LocalClaim.create(
                kind=ClaimKind.TITLE,
                observed=title,
                canonical=normalize_title(title),
                excerpt=title,
                applicability="not_applicable",
                basis="target_metadata",
                checksum="not_applicable",
                applicability_basis="not_applicable",
            )
            report = MetadataReport.create(
                target=TargetFacts("primary", "ebooks", "item-1", default_target_fields(title)),
                file=FileFacts(0, "a" * 64, "b" * 64),
                local_claims=(claim,),
                local_reasons=("no_opening_claims",),
                providers=(),
            )
            replay = MetadataReport.create(
                target=TargetFacts("primary", "ebooks", "item-1", default_target_fields(title)),
                file=FileFacts(0, "a" * 64, "b" * 64),
                local_claims=(claim,),
                local_reasons=("no_opening_claims",),
                providers=(),
            )
            with self.subTest(title=title):
                self.assertEqual(report.target.current_title, title)
                self.assertEqual(report.local_claims[0].observed, title)
                self.assertEqual(report.local_claims[0].canonical, canonical)
                self.assertEqual(report.report_id, replay.report_id)
                self.assertEqual(MetadataReport.from_json(report.to_json()), report)

    def test_application_replays_after_file_reorder_and_constructs_provider_once(self) -> None:
        first_file = TargetFile("a" * 64, "one.epub", "epub")
        second_file = TargetFile("b" * 64, "two.epub", "epub")
        target = _Target(
            [
                TargetSnapshot("ebook", "Current title", (first_file, second_file)),
                TargetSnapshot("ebook", "Current title", (second_file, first_file)),
            ]
        )
        claim = LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="9780306406157",
            canonical="9780306406157",
            excerpt="eBook ISBN: 9780306406157",
            applicability="ebook",
            checksum="valid_isbn_13",
            applicability_basis="ebook",
        )
        inspector = _Inspector(InspectionResult("c" * 64, (claim,)))
        repository = _Repository()
        constructions: list[str] = []

        class Provider:
            def observe(self, claims, *, deadline=None):
                return ProviderObservation("catalogue", ProviderStatus.NO_MATCH, tuple(sorted(value.reference for value in claims if value.kind is ClaimKind.ISBN)))

        service = GenerateMetadataReport(
            target=target,
            inspector=inspector,
            repository_factory=lambda: repository,
            providers=(ProviderBinding("catalogue", True, lambda: constructions.append("one") or Provider()),),
            connection=TargetConnectionConfiguration("primary", "https://example.invalid", "/private/target"),
            library=LibraryConfiguration("ebooks", "primary", "library-id", "ebook", "books", ("catalogue",)),
            mount=MediaMountConfiguration("books", "/media", "/library", True),
            limits=ReportLimits(),
        )
        serialized = service.run("item-1", 0)
        report = MetadataReport.from_json(serialized)
        self.assertEqual(report.file.identity, "a" * 64)
        self.assertEqual(constructions, ["one"])
        self.assertEqual((inspector.inspections, inspector.verifications), (1, 1))
        self.assertTrue(repository.closed)

    def test_application_shares_aggregate_deadline_with_metadata_and_cover_providers(
        self,
    ) -> None:
        selected = TargetFile("a" * 64, "book.epub", "epub")
        snapshot = TargetSnapshot("ebook", "Current title", (selected,))
        target = _Target([snapshot, snapshot])
        inspector = _Inspector(InspectionResult("c" * 64, ()))
        repository = _Repository()
        observed: list[tuple[str, float]] = []

        class Provider:
            def observe(self, _plan, *, deadline):
                observed.append(("metadata", deadline))
                return ProviderObservation("catalogue", ProviderStatus.NO_MATCH)

        class CoverObserver:
            def observe(
                self,
                _target,
                _providers,
                _local_sources,
                _descriptors,
                aggregate_deadline,
            ):
                observed.append(("cover", aggregate_deadline))
                return ()

        service = GenerateMetadataReport(
            target=target,
            inspector=inspector,
            repository_factory=lambda: repository,
            providers=(ProviderBinding("catalogue", True, Provider),),
            connection=TargetConnectionConfiguration(
                "primary", "https://example.invalid", "/private/target"
            ),
            library=LibraryConfiguration(
                "ebooks",
                "primary",
                "library-id",
                "ebook",
                "books",
                ("catalogue",),
            ),
            mount=MediaMountConfiguration(
                "books", "/media", "/library", True
            ),
            limits=ReportLimits(),
            cover_observer=CoverObserver(),
        )

        report = MetadataReport.from_json(service.run("item-1", 0))

        self.assertEqual(ProviderStatus.NO_MATCH, report.providers[0].status)
        self.assertEqual(
            ["metadata", "cover"], [boundary for boundary, _ in observed]
        )
        self.assertEqual(observed[0][1], observed[1][1])

    def test_incomplete_cause_property_is_mapped_without_private_material(self) -> None:
        selected = TargetFile("a" * 64, "book.epub", "epub")
        snapshot = TargetSnapshot("ebook", "Current title", (selected,))
        target = _Target([snapshot, snapshot])
        inspector = _Inspector(InspectionResult("c" * 64, ()))
        repository = _Repository()

        class Provider:
            @property
            def attempt_records(self):
                return ()

            @property
            def incomplete_causes(self):
                return (type("Cause", (), {"cause": "aggregate_bytes"})(),)

            def observe(self, _plan, *, deadline):
                return ProviderObservation(
                    "catalogue", ProviderStatus.INCOMPLETE
                )

        service = GenerateMetadataReport(
            target=target,
            inspector=inspector,
            repository_factory=lambda: repository,
            providers=(ProviderBinding("catalogue", True, Provider),),
            connection=TargetConnectionConfiguration(
                "primary", "https://example.invalid", "/private/target"
            ),
            library=LibraryConfiguration(
                "ebooks",
                "primary",
                "library-id",
                "ebook",
                "books",
                ("catalogue",),
            ),
            mount=MediaMountConfiguration(
                "books", "/media", "/library", True
            ),
            limits=ReportLimits(),
        )

        report = MetadataReport.from_json(service.run("item-1", 0))

        self.assertEqual(
            [
                FailureCategory.MALFORMED_OVER_BOUND_RESPONSE,
            ],
            [
                diagnostic.category
                for diagnostic in report.providers[0].diagnostics
            ],
        )
        self.assertNotIn(
            FailureCategory.TIMEOUT,
            [diagnostic.category for diagnostic in report.providers[0].diagnostics],
        )
        self.assertNotIn("credential", report.to_json())
        self.assertNotIn("/private/library", report.to_json())

    def test_provider_parser_attempt_and_incomplete_diagnostics_merge_once_on_reopen(
        self,
    ) -> None:
        selected = TargetFile("a" * 64, "book.epub", "epub")
        snapshot = TargetSnapshot("ebook", "Current title", (selected,))
        target = _Target([snapshot, snapshot])
        claim = LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="9780306406157",
            canonical="9780306406157",
            excerpt="ISBN 9780306406157",
            applicability="ebook",
            basis="opening_matter",
            checksum="valid_isbn_13",
            applicability_basis="ebook",
            scope=ClaimScope.EDITION,
        )
        inspector = _Inspector(InspectionResult("c" * 64, (claim,)))

        class Provider:
            cover_descriptors = ()
            attempt_records = (
                type(
                    "Attempt",
                    (),
                    {"category": "http_503", "outcome": "retry"},
                )(),
            )
            incomplete_causes = (
                type("Cause", (), {"cause": "aggregate_bytes"})(),
            )

            def observe(self, _plan, *, deadline):
                return ProviderObservation(
                    "catalogue",
                    ProviderStatus.INCOMPLETE,
                    diagnostics=(
                        FailureDiagnostic(
                            FailureCategory.SCHEMA_CONSTRUCTION,
                            FailureComponent.PROVIDER,
                            FailureStage.SCHEMA,
                            count=1,
                        ),
                    ),
                )

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "reports.sqlite"
            service = GenerateMetadataReport(
                target=target,
                inspector=inspector,
                repository_factory=lambda: open_repository(database),
                providers=(
                    ProviderBinding("catalogue", True, Provider),
                ),
                connection=TargetConnectionConfiguration(
                    "primary",
                    "https://example.invalid",
                    "/private/target",
                ),
                library=LibraryConfiguration(
                    "ebooks",
                    "primary",
                    "library-id",
                    "ebook",
                    "books",
                    ("catalogue",),
                ),
                mount=MediaMountConfiguration(
                    "books",
                    "/media",
                    "/library",
                    True,
                ),
                limits=ReportLimits(),
            )
            generated = MetadataReport.from_json(service.run("item-1", 0))
            repository = open_repository(database, create=False)
            try:
                persisted = repository.get(generated.report_id)
            finally:
                repository.close()
            reopened = MetadataReport.from_json(persisted)

        expected = (
            (
                FailureCategory.HTTP_NON_SUCCESS,
                FailureStage.RESPONSE,
                True,
                1,
            ),
            (
                FailureCategory.MALFORMED_OVER_BOUND_RESPONSE,
                FailureStage.RESPONSE,
                False,
                1,
            ),
            (
                FailureCategory.SCHEMA_CONSTRUCTION,
                FailureStage.SCHEMA,
                None,
                1,
            ),
        )
        self.assertEqual(
            expected,
            tuple(
                (
                    value.category,
                    value.stage,
                    value.retryable,
                    value.count,
                )
                for value in reopened.providers[0].diagnostics
            ),
        )
        self.assertEqual(generated.to_json(), reopened.to_json())

    def test_application_preserves_inspector_occurrence_order_after_target_title(self) -> None:
        claims = tuple(
            LocalClaim.create(kind=ClaimKind.CREATOR, observed=value, canonical=value, excerpt=value,
                              applicability="not_applicable", checksum="not_applicable",
                              applicability_basis="not_applicable")
            for value in ("Second in source", "First by hash")
        )
        target = _Target([TargetSnapshot("ebook", "Current title", (TargetFile("a" * 64, "one.epub", "epub"),))] * 2)
        inspector = _Inspector(InspectionResult("c" * 64, claims))
        repository = _Repository()
        service = GenerateMetadataReport(target=target, inspector=inspector, repository_factory=lambda: repository, providers=(), connection=TargetConnectionConfiguration("primary", "https://example.invalid", "/private/target"), library=LibraryConfiguration("ebooks", "primary", "library-id", "ebook", "books", ()), mount=MediaMountConfiguration("books", "/media", "/library", True), limits=ReportLimits())
        report = MetadataReport.from_json(service.run("item-1", 0))
        self.assertEqual([claim.observed for claim in report.local_claims], ["Current title", "Second in source", "First by hash"])

    def test_duplicate_occurrences_are_serialized_in_source_order_and_replay_stably(self) -> None:
        claims = tuple(LocalClaim.create(kind=ClaimKind.CREATOR, observed="Repeated person", canonical="Repeated person", excerpt="Repeated person", applicability="not_applicable", checksum="not_applicable", applicability_basis="not_applicable", occurrence=ordinal) for ordinal in (2, 1))
        report = MetadataReport.create(target=TargetFacts("primary", "ebooks", "item-1", default_target_fields("Current title")), file=FileFacts(0, "a" * 64, "b" * 64), local_claims=claims, local_reasons=("package_metadata_creator",), providers=())
        self.assertEqual([claim.occurrence for claim in report.local_claims], [2, 1])
        self.assertEqual(MetadataReport.from_json(report.to_json()).to_json(), report.to_json())

    def test_unsupported_media_precedes_inspector_provider_and_repository(self) -> None:
        target = _Target([TargetSnapshot("audiobook", "Audio", ())])
        inspector = _Inspector(InspectionResult("c" * 64, ()))
        activity: list[str] = []
        repository = _Repository()
        service = GenerateMetadataReport(
            target=target,
            inspector=inspector,
            repository_factory=lambda: activity.append("repository") or repository,
            providers=(ProviderBinding("catalogue", True, lambda: activity.append("provider")),),
            connection=TargetConnectionConfiguration("primary", "https://example.invalid", "/private/target"),
            library=LibraryConfiguration("audio", "primary", "library-id", "audiobook", None, ("catalogue",)),
            mount=None,
            limits=ReportLimits(),
        )
        with self.assertRaises(UnsupportedMediaError):
            service.run("item-1", 0)
        self.assertEqual(activity, [])
        self.assertEqual(inspector.inspections, 0)

    def test_currentness_drift_inside_publication_precondition_publishes_nothing(self) -> None:
        selected = TargetFile("a" * 64, "book.epub", "epub")
        target = _Target(
            [
                TargetSnapshot("ebook", "Initial title", (selected,)),
                TargetSnapshot("ebook", "Changed title", (selected,)),
            ]
        )
        inspector = _Inspector(InspectionResult("c" * 64, ()))
        activity: list[str] = []
        repository = _Repository()
        service = GenerateMetadataReport(
            target=target,
            inspector=inspector,
            repository_factory=lambda: activity.append("repository") or repository,
            providers=(ProviderBinding("catalogue", False, lambda: activity.append("provider")),),
            connection=TargetConnectionConfiguration("primary", "https://example.invalid", "/private/target"),
            library=LibraryConfiguration("ebooks", "primary", "library-id", "ebook", "books", ("catalogue",)),
            mount=MediaMountConfiguration("books", "/media", "/library", True),
            limits=ReportLimits(),
        )
        with self.assertRaises(CurrentnessError):
            service.run("item-1", 0)
        self.assertEqual(activity, ["repository"])
        self.assertEqual(repository.values, {})
        self.assertTrue(repository.closed)
        self.assertEqual((inspector.inspections, inspector.verifications), (1, 0))

    def test_currentness_covers_every_target_destination(self) -> None:
        selected = TargetFile("a" * 64, "book.epub", "epub")
        initial = TargetSnapshot("ebook", "Current title", (selected,))
        changed_fields = tuple(
            TargetField(
                field.destination,
                TargetFieldState.VALUE,
                ("Changed genre",),
            )
            if field.destination is TargetDestination.GENRES
            else field
            for field in initial.fields
        )
        target = _Target(
            [
                initial,
                TargetSnapshot(
                    "ebook",
                    "Current title",
                    (selected,),
                    fields=changed_fields,
                ),
            ]
        )
        inspector = _Inspector(InspectionResult("c" * 64, ()))
        repository = _Repository()
        service = GenerateMetadataReport(
            target=target,
            inspector=inspector,
            repository_factory=lambda: repository,
            providers=(),
            connection=TargetConnectionConfiguration(
                "primary", "https://example.invalid", "/private/target"
            ),
            library=LibraryConfiguration(
                "ebooks", "primary", "library-id", "ebook", "books", ()
            ),
            mount=MediaMountConfiguration(
                "books", "/media", "/library", True
            ),
            limits=ReportLimits(),
        )

        with self.assertRaises(CurrentnessError):
            service.run("item-1", 0)
        self.assertEqual(repository.values, {})
        self.assertTrue(repository.closed)

    def test_cli_has_only_required_contract_and_redacts_failure(self) -> None:
        output = io.StringIO()
        errors = io.StringIO()
        exit_code = main(["metadata-report", "--config", "/private/config"], runner=lambda *args: "", stdout=output, stderr=errors)
        self.assertEqual(exit_code, 2)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(errors.getvalue(), "Metadata report failed: invalid invocation.\n")
        self.assertNotIn("private", errors.getvalue())

    def test_cli_success_is_exact_and_unexpected_failures_are_redacted(self) -> None:
        report = MetadataReport.create(
            target=TargetFacts("primary", "ebooks", "item-1", default_target_fields("Current title")),
            file=FileFacts(0, "a" * 64, "b" * 64),
            local_claims=(),
            local_reasons=("no_opening_claims",),
            providers=(),
        )
        arguments = [
            "metadata-report", "--config", "/private/config.json", "--database", "/private/reports.sqlite", "--artifact-store", "/private/artifacts",
            "--connection", "primary", "--library", "ebooks", "--item", "item-1", "--file-index", "0",
        ]
        output = io.StringIO()
        errors = io.StringIO()
        self.assertEqual(main(arguments, runner=lambda *args: report.to_json(), stdout=output, stderr=errors), 0)
        self.assertEqual(output.getvalue(), report.to_json() + "\n")
        self.assertEqual(errors.getvalue(), "")

        output = io.StringIO()
        errors = io.StringIO()
        self.assertEqual(main(arguments, runner=lambda *args: (_ for _ in ()).throw(RuntimeError("token=/private/secret")), stdout=output, stderr=errors), 1)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(errors.getvalue(), "Metadata report failed safely.\n")

    def test_cli_rejects_missing_artifact_store_before_runner(self) -> None:
        arguments = [
            "metadata-report", "--config", "config", "--database", "reports", "--connection", "primary",
            "--library", "ebooks", "--item", "item-1", "--file-index", "0",
        ]
        self.assertEqual(2, main(arguments, runner=lambda *_: (_ for _ in ()).throw(AssertionError()), stdout=io.StringIO(), stderr=io.StringIO()))


if __name__ == "__main__":
    unittest.main()
