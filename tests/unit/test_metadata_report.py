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
    ProviderBinding,
    TargetFile,
    TargetCover,
    TargetSnapshot,
    UnsupportedMediaError,
    MetadataReportError,
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
    ProviderStatus,
    RearClaimProvenance,
    ReportValueError,
    TargetFacts,
    canonical_json,
    comparison_links,
    claim_scope,
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
            target=TargetFacts("primary", "ebooks", "item-1", "Current title"),
            file=FileFacts(0, "a" * 64, "b" * 64), local_claims=local_claims,
            local_reasons=("opening_matter_contributor",),
            providers=(ProviderObservation("catalogue", ProviderStatus.CANDIDATES, (), tuple(candidates)),),
        )
        self.assertLessEqual(len(report.to_json().encode("utf-8")), MAX_REPORT_BYTES)
        self.assertEqual(MetadataReport.from_json(report.to_json()), report)
    def test_field_results_are_complete_canonical_and_separate_from_claims(self) -> None:
        results = derive_field_results(())
        report = MetadataReport.create(
            target=TargetFacts("primary", "ebooks", "item-1", "Current title"),
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
            target=TargetFacts("primary", "ebooks", "item-1", "Current title"),
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
            target=TargetFacts("primary", "ebooks", "item-1", "Current title"),
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
            target=TargetFacts("primary", "ebooks", "item-1", "Current title"),
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
            target=TargetFacts("primary", "ebooks", "item-1", "Current title"),
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
            target=TargetFacts("primary", "ebooks", "item-1", "Current title"),
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
        report = MetadataReport.create(target=TargetFacts("primary", "ebooks", "item-1", "Current title"), file=FileFacts(0, "a" * 64, "b" * 64), local_claims=(agree, disagree, invalid, edition, author), local_reasons=("opening_title",), providers=(ProviderObservation("catalogue", ProviderStatus.CANDIDATES, candidates=(ProviderCandidate("candidate", "work-candidate", (linked,)),)),))
        changed_work = MetadataReport.create(target=TargetFacts("primary", "ebooks", "item-1", "Current title"), file=FileFacts(0, "a" * 64, "b" * 64), local_claims=(agree, disagree, invalid, edition, author), local_reasons=("opening_title",), providers=(ProviderObservation("catalogue", ProviderStatus.CANDIDATES, candidates=(ProviderCandidate("candidate", "different-work", (linked,)),)),))
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
        provider = ProviderClaim.create(provider="catalogue", candidate="candidate", kind=ClaimKind.PUBLISHER, value="Press", state=FieldState.VALID, scope=ClaimScope.EDITION, applicability="not_applicable")
        self.assertEqual(comparison_links(provider, (local,)), (ComparisonLink(local.reference, Comparison.AGREEMENT),))

    def test_non_valid_title_sentinel_cannot_become_canonical(self) -> None:
        sentinel = "unsafe-source-text-sha256:" + "a" * 64
        claim = LocalClaim.create(
            kind=ClaimKind.TITLE, observed=sentinel, canonical=None, excerpt=sentinel,
            applicability="not_applicable", basis="package_metadata", checksum="not_applicable",
            applicability_basis="not_applicable", state=FieldState.UNSUPPORTED,
        )
        report = MetadataReport.create(
            target=TargetFacts("primary", "ebooks", "item-1", "Current title"),
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
            target=TargetFacts("primary", "ebooks", "item-1", "Current title"),
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
        self.assertEqual(TargetFacts("primary", "ebooks", "item-1", title).current_title, title)
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
                target=TargetFacts("primary", "ebooks", "item-1", title),
                file=FileFacts(0, "a" * 64, "b" * 64),
                local_claims=(claim,),
                local_reasons=("no_opening_claims",),
                providers=(),
            )
            replay = MetadataReport.create(
                target=TargetFacts("primary", "ebooks", "item-1", title),
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
        report = MetadataReport.create(target=TargetFacts("primary", "ebooks", "item-1", "Current title"), file=FileFacts(0, "a" * 64, "b" * 64), local_claims=claims, local_reasons=("package_metadata_creator",), providers=())
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
            target=TargetFacts("primary", "ebooks", "item-1", "Current title"),
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
