from __future__ import annotations

import io
import unittest
from pathlib import Path

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
    TargetFile,
    TargetCover,
    TargetSnapshot,
    UnsupportedMediaError,
    MetadataReportError,
    _variant_marker,
    metadata_lookup,
    qualify_provider_observation,
)
from mechanical_librarian.domain.cover_evidence import (
    CoverArtifact,
    CoverSource,
    CoverSourceKind,
    CoverSourceState,
    cover_source_id,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    Comparison,
    ComparisonLink,
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
    normalize_title,
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

    def test_work_lookup_accepts_the_complete_author_set_in_provider_order(self) -> None:
        lookup = MetadataLookup(
            ProviderQueryKind.WORK,
            (),
            "The Example",
            ("Ada Author", "Bea Author"),
        )
        claims = tuple(
            ProviderClaim.create(
                provider="catalogue",
                candidate="edition-1",
                kind=kind,
                value=value,
                state=FieldState.VALID,
                scope=ClaimScope.WORK,
                applicability="not_applicable",
                role=role,
                raw_order=index,
            )
            for index, (kind, value, role) in enumerate(
                (
                    (ClaimKind.TITLE, "The Example", None),
                    (ClaimKind.CREATOR, "Bea Author", "author"),
                    (ClaimKind.CREATOR, "Ada Author", "author"),
                )
            )
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
        self.assertEqual(ProviderStatus.CANDIDATES, result.status)

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
            def observe(self, claims): return ProviderObservation("other", ProviderStatus.NO_MATCH)
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
        for kind, basis, role, rear in ((ClaimKind.TITLE, "opening_matter", "author", None), (ClaimKind.PUBLISHER, "opening_matter", None, provenance), (ClaimKind.TITLE, "package_metadata", None, provenance), (ClaimKind.TITLE, "target_metadata", None, provenance)):
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
        links = tuple(sorted((ComparisonLink(agree.reference, Comparison.AGREEMENT), ComparisonLink(disagree.reference, Comparison.DISAGREEMENT), ComparisonLink(invalid.reference, Comparison.NOT_COMPARABLE), ComparisonLink(edition.reference, Comparison.NOT_COMPARABLE)), key=lambda link: link.local_reference))
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
        self.assertEqual(comparison_links(provider, (local, imprint)), (ComparisonLink(local.reference, Comparison.AGREEMENT),))
        self.assertEqual(comparison_links(provider_imprint, (local, imprint)), (ComparisonLink(imprint.reference, Comparison.AGREEMENT),))

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
            def observe(self, claims):
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
