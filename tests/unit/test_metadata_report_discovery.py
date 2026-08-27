"""Metadata-report discovery: lookup qualification, the per-family
discovery plan and its overflow, work-lane admission, and the
source-lexical timestamp voting matrix.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import replace
from typing import cast

import pytest

from mechanical_librarian.application.configuration import (
    LibraryConfiguration,
    MediaMountConfiguration,
    ReportLimits,
    TargetConnectionConfiguration,
)
from mechanical_librarian.application.metadata_report import (
    GenerateMetadataReport,
    InspectionResult,
    MetadataDiscoveryLane,
    MetadataDiscoveryPlan,
    MetadataLookup,
    MetadataProvider,
    MetadataReportError,
    ProviderBinding,
    ProviderLaneResult,
    ReportRepository,
    TargetFile,
    TargetSnapshot,
    metadata_discovery_plan,
    metadata_lookup,
    provider_observation,
    qualify_provider_observation,
)
from mechanical_librarian.domain.epub_inspection import DateEvent, DatePrecision
from mechanical_librarian.domain.failure_diagnostics import (
    FailureCategory,
    FailureComponent,
    FailureDiagnostic,
    FailureStage,
)
from mechanical_librarian.domain.metadata_report import (
    MAX_CANDIDATES,
    ClaimKind,
    ClaimScope,
    Comparison,
    ComparisonLink,
    EditionEnrichmentOverflow,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderEvidence,
    ProviderEvidenceKind,
    ProviderIdentityBinding,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    PublicationDateRelation,
    RearClaimProvenance,
    ReportValueError,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    canonical_json,
    default_target_fields,
    finalize_candidate_comparisons,
    normalize_date_value,
    publication_date_is_voting,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.shadow_review import ShadowReviewPacket
from mechanical_librarian.domain.work_identity import (
    WorkDispatchLineage,
    WorkDispatchRung,
    WorkTitleTransformationFamily,
    narrowing_work_query,
    work_query_ladder,
)
from tests.support.metadata_report_doubles import (
    FakeInspector,
    FakeReportRepository,
    FakeTarget,
)


def _work_dispatch(
    title: str, authors: tuple[str, ...], *, narrowed: bool = False
) -> WorkDispatchLineage:
    query = work_query_ladder(title, authors)[0]
    return WorkDispatchLineage.from_query(
        narrowing_work_query(query) if narrowed else query
    )


def test_lookup_prefers_one_isbn_family_and_otherwise_requires_complete_target_authors() -> (
    None
):
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
    assert MetadataLookup(
        ProviderQueryKind.WORK,
        (),
        "The Example",
        ("Ada Author", "Ben Writer"),
    ) == metadata_lookup(complete, ())
    for state in (
        TargetFieldState.MISSING,
        TargetFieldState.NULL,
        TargetFieldState.EMPTY,
        TargetFieldState.UNLOADED,
        TargetFieldState.MALFORMED,
        TargetFieldState.OVERSIZED,
    ):
        incomplete = target(
            "The Example",
            TargetField(TargetDestination.AUTHORS, state),
        )
        assert ProviderQueryKind.NONE == metadata_lookup(incomplete, ()).kind
    ambiguous = target(
        "The Example",
        TargetField(
            TargetDestination.AUTHORS,
            TargetFieldState.VALUE,
            ("Ada Author", "ADA AUTHOR"),
        ),
    )
    assert ProviderQueryKind.NONE == metadata_lookup(ambiguous, ()).kind

    isbn = LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="9780306406157",
        canonical="9780306406157",
        excerpt="9780306406157",
        applicability="ebook",
        checksum="valid_isbn_13",
        applicability_basis="ebook",
    )
    assert ProviderQueryKind.ISBN == metadata_lookup(complete, (isbn,)).kind
    competing = LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="9780140328721",
        canonical="9780140328721",
        excerpt="9780140328721",
        applicability="ebook",
        checksum="valid_isbn_13",
        applicability_basis="ebook",
    )
    assert ProviderQueryKind.NONE == metadata_lookup(complete, (isbn, competing)).kind

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
    plan = metadata_discovery_plan(complete, (target_title, isbn, competing))
    assert not plan.overflow
    assert [
        ProviderQueryKind.ISBN,
        ProviderQueryKind.ISBN,
        ProviderQueryKind.WORK,
    ] == sorted((lane.kind for lane in plan.lanes), key=lambda value: value.value)
    assert {"9780306406157", "9780140328721"} == {
        lane.matched_family
        for lane in plan.lanes
        if lane.kind is ProviderQueryKind.ISBN
    }


def test_discovery_plan_deduplicates_families_and_records_convergence() -> None:
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
        rear_provenance=RearClaimProvenance("a" * 64, 1, 1, "also_by", "rear.xhtml"),
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
    isbn_lane = next(lane for lane in plan.lanes if lane.kind is ProviderQueryKind.ISBN)
    work_lane = next(lane for lane in plan.lanes if lane.kind is ProviderQueryKind.WORK)
    assert (
        tuple(sorted((isbn_13.reference, isbn_10.reference)))
        == isbn_lane.input_references
    )
    candidate = ProviderCandidate(
        "edition-1",
        "work-1",
        (
            ProviderClaim.create(
                provider="catalogue",
                candidate="edition-1",
                kind=ClaimKind.TITLE,
                value="The Example",
                state=FieldState.VALID,
                scope=ClaimScope.WORK,
                applicability="not_applicable",
            ),
            ProviderClaim.create(
                provider="catalogue",
                candidate="edition-1",
                kind=ClaimKind.CREATOR,
                value="Ada Author",
                state=FieldState.VALID,
                scope=ClaimScope.WORK,
                applicability="not_applicable",
                role="author",
                raw_order=1,
            ),
        ),
    )
    observation = provider_observation(
        "catalogue",
        (
            ProviderLaneResult(isbn_lane, ProviderStatus.CANDIDATES, (candidate,)),
            ProviderLaneResult(
                work_lane,
                ProviderStatus.CANDIDATES,
                (candidate,),
                diagnostics=(
                    FailureDiagnostic(
                        FailureCategory.LOOKUP_CONSTRUCTION,
                        FailureComponent.PROVIDER,
                        FailureStage.LOOKUP,
                        retryable=False,
                    ),
                ),
                dispatch_lineage=_work_dispatch("The Example", ("Ada Author",)),
            ),
        ),
    )
    assert 1 == len(observation.candidates)
    assert 2 == len(observation.lanes)
    assert 1 == len(observation.convergence)
    assert (
        tuple(sorted(lane.identity for lane in plan.lanes))
        == observation.convergence[0].lane_identities
    )
    lane_references = {
        value.reference for lane in observation.lanes for value in lane.candidates
    }
    assert 2 == len(lane_references)
    report = MetadataReport.create(
        target=TargetFacts("primary", "ebooks", "item-1", fields),
        file=FileFacts(0, "b" * 64, "c" * 64),
        local_claims=(title, isbn_13, isbn_10),
        local_reasons=("opening_matter_isbn",),
        providers=(observation,),
    )
    assert report == MetadataReport.from_json(report.to_json())
    persisted_work_lane = next(
        lane
        for lane in report.providers[0].lanes
        if lane.identity == work_lane.identity
    )
    assert [value.to_data() for value in persisted_work_lane.diagnostics] == [
        {
            "category": "lookup_construction",
            "component": "provider",
            "count": 1,
            "current": None,
            "retryable": False,
            "stage": "lookup",
        }
    ]
    assert "metadata-report-v28" == report.schema_version
    previous = report.to_data()
    previous["schema_version"] = "metadata-report-v22"
    with pytest.raises(ReportValueError):
        MetadataReport.from_json(canonical_json(previous))


@pytest.fixture(scope="module")
def aggregate_plan() -> MetadataDiscoveryPlan:
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
    return metadata_discovery_plan(TargetSnapshot("ebook", None, ()), claims)


def _overflow_lane_results(
    lanes: tuple[MetadataDiscoveryLane, ...],
) -> tuple[ProviderLaneResult, ...]:
    candidates = tuple(
        ProviderCandidate(f"edition-{index}", f"work-{index}", ())
        for index in range(MAX_CANDIDATES + 1)
    )
    return (
        ProviderLaneResult(lanes[0], ProviderStatus.CANDIDATES, candidates[:3]),
        ProviderLaneResult(lanes[1], ProviderStatus.CANDIDATES, candidates[3:]),
        ProviderLaneResult(lanes[2], ProviderStatus.RATE_LIMITED),
        ProviderLaneResult(lanes[3], ProviderStatus.NO_MATCH),
    )


def _conflicting_lane_results(
    lanes: tuple[MetadataDiscoveryLane, ...],
) -> tuple[ProviderLaneResult, ...]:
    return (
        ProviderLaneResult(
            lanes[0],
            ProviderStatus.CANDIDATES,
            (ProviderCandidate("edition-shared", "work-one", ()),),
            diagnostics=(
                FailureDiagnostic(
                    FailureCategory.PROVIDER_DECLARED_ERROR,
                    FailureComponent.PROVIDER,
                    FailureStage.RESPONSE,
                    retryable=False,
                ),
            ),
        ),
        ProviderLaneResult(
            lanes[1],
            ProviderStatus.CANDIDATES,
            (ProviderCandidate("edition-shared", "work-two", ()),),
        ),
        ProviderLaneResult(lanes[2], ProviderStatus.RATE_LIMITED),
        ProviderLaneResult(lanes[3], ProviderStatus.NO_MATCH),
    )


@pytest.mark.parametrize(
    ("build", "expected"),
    (
        pytest.param(
            _overflow_lane_results,
            ProviderStatus.CANDIDATE_LIMIT_EXCEEDED,
            id="overflow",
        ),
        pytest.param(
            _conflicting_lane_results, ProviderStatus.MALFORMED, id="conflict"
        ),
    ),
)
def test_aggregate_candidate_failures_preserve_independent_lane_statuses(
    aggregate_plan: MetadataDiscoveryPlan,
    build: Callable[
        [tuple[MetadataDiscoveryLane, ...]], tuple[ProviderLaneResult, ...]
    ],
    expected: ProviderStatus,
) -> None:
    lanes = aggregate_plan.lanes
    results = build(lanes)
    observation = provider_observation("catalogue", results)
    replayed_order = provider_observation("catalogue", tuple(reversed(results)))
    by_identity = {lane.identity: lane.status for lane in observation.lanes}
    assert expected == observation.status
    assert () == observation.candidates
    assert expected == by_identity[lanes[0].identity]
    assert expected == by_identity[lanes[1].identity]
    assert ProviderStatus.RATE_LIMITED == by_identity[lanes[2].identity]
    assert ProviderStatus.NO_MATCH == by_identity[lanes[3].identity]
    malformed_diagnostic = {
        "category": "malformed_over_bound_response",
        "component": "provider",
        "count": 1,
        "current": None,
        "retryable": False,
        "stage": "response",
    }
    declared_diagnostic = {
        "category": "provider_declared_error",
        "component": "provider",
        "count": 1,
        "current": None,
        "retryable": False,
        "stage": "response",
    }
    by_identity_diagnostics = {
        lane.identity: [value.to_data() for value in lane.diagnostics]
        for lane in observation.lanes
    }
    if expected is ProviderStatus.MALFORMED:
        assert [malformed_diagnostic, declared_diagnostic] == by_identity_diagnostics[
            lanes[0].identity
        ]
        assert [malformed_diagnostic] == by_identity_diagnostics[lanes[1].identity]
    else:
        assert [] == by_identity_diagnostics[lanes[0].identity]
        assert [] == by_identity_diagnostics[lanes[1].identity]
    assert observation.to_data() == replayed_order.to_data()
    report = MetadataReport.create(
        target=TargetFacts("primary", "ebooks", "item-1", default_target_fields(None)),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=aggregate_plan.local_claims,
        local_reasons=("no_opening_claims",),
        providers=(observation,),
    )
    replayed = MetadataReport.from_json(report.to_json())
    assert by_identity_diagnostics[lanes[0].identity] == [
        value.to_data() for value in replayed.providers[0].lanes[0].diagnostics
    ]


def test_discovery_plan_reports_exact_family_overflow_without_subset() -> None:
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
    plan = metadata_discovery_plan(TargetSnapshot("ebook", None, ()), tuple(claims))
    assert plan.overflow
    assert () == plan.lanes

    title = LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed="Example: A Subtitle",
        canonical="Example: A Subtitle",
        excerpt="Example: A Subtitle",
        applicability="not_applicable",
        basis="target_metadata",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.WORK,
    )
    fields = tuple(
        TargetField(
            field.destination,
            TargetFieldState.VALUE,
            ("Ada & Bea",),
        )
        if field.destination is TargetDestination.AUTHORS
        else field
        for field in default_target_fields("Example: A Subtitle")
    )
    target_with_work = TargetSnapshot("ebook", "Example: A Subtitle", (), fields=fields)
    assert 3 == len(work_query_ladder("Example: A Subtitle", ("Ada & Bea",)))
    over_budget = metadata_discovery_plan(target_with_work, (title, *claims[:7]))
    assert over_budget.overflow
    assert () == over_budget.lanes
    boundary = metadata_discovery_plan(target_with_work, (title, *claims[:6]))
    assert not boundary.overflow
    assert 7 == len(boundary.lanes)
    isbn_only = metadata_discovery_plan(
        TargetSnapshot("ebook", None, ()), tuple(claims[:7])
    )
    work_only = metadata_discovery_plan(target_with_work, (title,))
    with pytest.raises(MetadataReportError):
        MetadataDiscoveryPlan(
            (title, *claims[:7]),
            tuple(
                sorted(
                    (*isbn_only.lanes, *work_only.lanes), key=lambda lane: lane.identity
                )
            ),
        )

    selected = TargetFile("a" * 64, "book.epub", "epub")
    snapshot = TargetSnapshot("ebook", None, (selected,))
    target = FakeTarget([snapshot, snapshot])
    inspector = FakeInspector(InspectionResult("b" * 64, tuple(claims)))
    repository = FakeReportRepository()
    service = GenerateMetadataReport(
        target=target,
        inspector=inspector,
        repository_factory=lambda: cast(ReportRepository, repository),
        providers=(
            ProviderBinding(
                "catalogue",
                True,
                lambda: pytest.fail("overflow must not construct a provider"),
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
        mount=MediaMountConfiguration("books", "/media", "/library", True),
        limits=ReportLimits(),
    )
    report = MetadataReport.from_json(service.run("item-1", 0))
    assert ProviderStatus.QUERY_LIMIT_EXCEEDED == report.providers[0].status
    assert [
        {
            "category": "lookup_construction",
            "component": "provider",
            "count": 1,
            "current": None,
            "retryable": False,
            "stage": "lookup",
        }
    ] == [diagnostic.to_data() for diagnostic in report.providers[0].diagnostics]


def test_work_lookup_admits_one_exact_work_and_strips_edition_authority() -> None:
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
    linked_publication = ProviderEvidence(
        ProviderEvidenceKind.PUBLICATION_DATE_TEXT,
        "2001-02-03",
        ClaimScope.EDITION,
        links=(
            ComparisonLink(
                "local_" + ("a" * 64),
                Comparison.NOT_COMPARABLE,
                publication_date_relation=(
                    PublicationDateRelation.MANIFESTATION_INCOMPATIBLE
                ),
            ),
        ),
    )
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
            claim(
                identity, ClaimKind.DESCRIPTION, "Work description", ClaimScope.WORK, 3
            ),
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
                    linked_publication,
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
            dispatch_lineage=_work_dispatch("The Example", ("Ada Author",)),
        ),
        lookup,
    )
    assert ProviderStatus.CANDIDATES == qualified.status
    assert {ClaimKind.TITLE, ClaimKind.CREATOR, ClaimKind.DESCRIPTION} == {
        value.kind for value in qualified.candidates[0].claims
    }
    assert {
        ProviderEvidenceKind.CONTRIBUTOR_IDENTITY,
        ProviderEvidenceKind.FIRST_PUBLICATION_DATE,
        ProviderEvidenceKind.PUBLICATION_DATE_TEXT,
    } == {value.kind for value in qualified.candidates[0].evidence}
    assert linked_publication == next(
        value
        for value in qualified.candidates[0].evidence
        if value.kind is ProviderEvidenceKind.PUBLICATION_DATE_TEXT
    )


def test_work_lookup_treats_filename_author_prefix_as_query_only() -> None:
    lookup = MetadataLookup(
        ProviderQueryKind.WORK,
        (),
        "Ada_Author_-_The Example",
        ("Ada Author",),
    )
    candidate = ProviderCandidate(
        "edition-1",
        "work-1",
        (
            ProviderClaim.create(
                provider="catalogue",
                candidate="edition-1",
                kind=ClaimKind.TITLE,
                value="The Example",
                state=FieldState.VALID,
                scope=ClaimScope.WORK,
                applicability="not_applicable",
            ),
            ProviderClaim.create(
                provider="catalogue",
                candidate="edition-1",
                kind=ClaimKind.CREATOR,
                value="Ada Author",
                state=FieldState.VALID,
                scope=ClaimScope.WORK,
                applicability="not_applicable",
                role="author",
                raw_order=1,
            ),
        ),
    )
    dispatch = _work_dispatch("Ada_Author_-_The Example", ("Ada Author",))

    qualified = qualify_provider_observation(
        ProviderObservation(
            "catalogue",
            ProviderStatus.CANDIDATES,
            (),
            (candidate,),
            ProviderQueryKind.WORK,
            dispatch_lineage=dispatch,
        ),
        lookup,
    )

    assert qualified.status is ProviderStatus.NO_MATCH
    assert qualified.dispatch_lineage is None
    assert qualified.transformation_lineage is None


def test_work_lookup_rejects_missing_or_ambiguous_decoration_lineage() -> None:
    lookup = MetadataLookup(
        ProviderQueryKind.WORK,
        (),
        "Ada_Author_-_The Example",
        ("Ada Author",),
    )

    def candidate(
        identity: str, title: str, work_identity: str = "work-1"
    ) -> ProviderCandidate:
        return ProviderCandidate(
            identity,
            work_identity,
            (
                ProviderClaim.create(
                    provider="catalogue",
                    candidate=identity,
                    kind=ClaimKind.TITLE,
                    value=title,
                    state=FieldState.VALID,
                    scope=ClaimScope.WORK,
                    applicability="not_applicable",
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
            ),
        )

    missing = qualify_provider_observation(
        ProviderObservation(
            "catalogue",
            ProviderStatus.CANDIDATES,
            (),
            (candidate("edition-1", "The Example"),),
            ProviderQueryKind.WORK,
        ),
        lookup,
    )
    ambiguous = qualify_provider_observation(
        ProviderObservation(
            "catalogue",
            ProviderStatus.CANDIDATES,
            (),
            (
                candidate("edition-1", "The Example"),
                candidate("edition-2", "Ada_Author_-_The Example"),
            ),
            ProviderQueryKind.WORK,
            dispatch_lineage=WorkDispatchLineage(WorkDispatchRung.NORMALIZED),
        ),
        lookup,
    )
    false_dispatch = qualify_provider_observation(
        ProviderObservation(
            "catalogue",
            ProviderStatus.CANDIDATES,
            (),
            (candidate("edition-1", "The Example"),),
            ProviderQueryKind.WORK,
            dispatch_lineage=WorkDispatchLineage(
                WorkDispatchRung.NORMALIZED,
                (WorkTitleTransformationFamily.BRACKET_SOURCE_TAG,),
            ),
        ),
        lookup,
    )
    exact_without_dispatch = qualify_provider_observation(
        ProviderObservation(
            "catalogue",
            ProviderStatus.CANDIDATES,
            (),
            (candidate("edition-1", "The Example"),),
            ProviderQueryKind.WORK,
        ),
        MetadataLookup(
            ProviderQueryKind.WORK,
            (),
            "The Example",
            ("Ada Author",),
        ),
    )
    typed = candidate("edition-1", "The Example")
    mixed = replace(
        typed,
        claims=(
            *typed.claims,
            ProviderClaim.create(
                provider="catalogue",
                candidate="edition-1",
                kind=ClaimKind.CREATOR,
                value="Ada Author",
                state=FieldState.VALID,
                scope=ClaimScope.WORK,
                applicability="not_applicable",
                role=None,
                raw_order=2,
            ),
        ),
    )
    mixed_creators = qualify_provider_observation(
        ProviderObservation(
            "catalogue",
            ProviderStatus.CANDIDATES,
            (),
            (mixed,),
            ProviderQueryKind.WORK,
            dispatch_lineage=_work_dispatch(
                "Ada_Author_-_The Example", ("Ada Author",)
            ),
        ),
        lookup,
    )
    ambiguous_identity = qualify_provider_observation(
        ProviderObservation(
            "catalogue",
            ProviderStatus.CANDIDATES,
            (),
            (
                candidate("edition-1", "The Example"),
                candidate("edition-2", "The Example", "work-2"),
            ),
            ProviderQueryKind.WORK,
            dispatch_lineage=_work_dispatch(
                "Ada_Author_-_The Example", ("Ada Author",)
            ),
        ),
        lookup,
    )

    assert missing.status is ProviderStatus.NO_MATCH
    assert ambiguous.status is ProviderStatus.NO_MATCH
    assert false_dispatch.status is ProviderStatus.NO_MATCH
    assert exact_without_dispatch.status is ProviderStatus.NO_MATCH
    assert mixed_creators.status is ProviderStatus.NO_MATCH
    assert ambiguous_identity.status is ProviderStatus.NO_MATCH

    exact_lookup = MetadataLookup(
        ProviderQueryKind.WORK,
        (),
        "The Example",
        ("Ada Author",),
    )
    target = TargetFacts(
        "primary",
        "ebooks",
        "item-1",
        tuple(
            TargetField(
                TargetDestination.AUTHORS,
                TargetFieldState.VALUE,
                ("Ada Author",),
            )
            if field.destination is TargetDestination.AUTHORS
            else field
            for field in default_target_fields("The Example")
        ),
    )
    dispatch = _work_dispatch("The Example", ("Ada Author",))
    same_work_candidates = (
        candidate("edition-1", "The Example"),
        candidate("edition-2", "The Example"),
    )
    same_work_qualified = qualify_provider_observation(
        ProviderObservation(
            "catalogue",
            ProviderStatus.CANDIDATES,
            (),
            same_work_candidates,
            ProviderQueryKind.WORK,
            dispatch_lineage=dispatch,
        ),
        exact_lookup,
    )
    assert same_work_qualified.status is ProviderStatus.CANDIDATES
    assert ("edition-1",) == tuple(
        value.identity for value in same_work_qualified.candidates
    )
    same_work_report = MetadataReport.create(
        target=target,
        file=FileFacts(0, "b" * 64, "c" * 64),
        local_claims=(),
        local_reasons=("target_metadata",),
        providers=(
            ProviderObservation(
                "catalogue",
                ProviderStatus.CANDIDATES,
                (),
                same_work_candidates,
                ProviderQueryKind.WORK,
                dispatch_lineage=dispatch,
            ),
        ),
    )
    assert same_work_report == MetadataReport.from_json(same_work_report.to_json())

    with pytest.raises(ReportValueError, match="work identity lineage"):
        MetadataReport.create(
            target=target,
            file=FileFacts(0, "b" * 64, "c" * 64),
            local_claims=(),
            local_reasons=("target_metadata",),
            providers=(
                replace(
                    same_work_report.providers[0],
                    candidates=(
                        same_work_candidates[0],
                        candidate("edition-2", "The Example", "work-2"),
                    ),
                ),
            ),
        )

    tampered = same_work_report.to_data()
    tampered["providers"][0]["candidates"][1]["work_identity"] = "work-2"
    identity_data = dict(tampered)
    identity_data.pop("report_id")
    tampered["report_id"] = (
        "report_" + hashlib.sha256(canonical_json(identity_data).encode()).hexdigest()
    )
    with pytest.raises(ReportValueError, match="work identity lineage"):
        MetadataReport.from_json(canonical_json(tampered))

    with pytest.raises(ReportValueError, match="work identity lineage"):
        MetadataReport.create(
            target=target,
            file=FileFacts(0, "b" * 64, "c" * 64),
            local_claims=(),
            local_reasons=("target_metadata",),
            providers=(
                ProviderObservation(
                    "catalogue",
                    ProviderStatus.CANDIDATES,
                    (),
                    (mixed,),
                    ProviderQueryKind.WORK,
                    dispatch_lineage=_work_dispatch("The Example", ("Ada Author",)),
                ),
            ),
        )


def test_edition_overflow_rejects_query_only_decoration_and_admits_exact() -> None:
    author = ProviderClaim.create(
        provider="catalogue",
        candidate="work-1",
        kind=ClaimKind.CREATOR,
        value="Ada Author",
        state=FieldState.VALID,
        scope=ClaimScope.WORK,
        applicability="not_applicable",
        role="author",
    )
    witness = EditionEnrichmentOverflow(
        "work-1",
        "The Example",
        (author,),
        (
            ProviderIdentityBinding(
                author.reference,
                "author-1",
                ClaimScope.WORK,
                "author",
            ),
        ),
    )
    dispatch = _work_dispatch(
        "Ada_Author_-_The Example", ("Ada Author",), narrowed=True
    )

    qualified = qualify_provider_observation(
        ProviderObservation(
            "catalogue",
            ProviderStatus.CANDIDATE_LIMIT_EXCEEDED,
            (),
            (),
            ProviderQueryKind.WORK,
            edition_enrichment_overflow=witness,
            dispatch_lineage=dispatch,
        ),
        MetadataLookup(
            ProviderQueryKind.WORK,
            (),
            "Ada_Author_-_The Example",
            ("Ada Author",),
        ),
    )

    assert qualified.edition_enrichment_overflow is None
    assert qualified.dispatch_lineage is None
    assert qualified.transformation_lineage is None

    exact_dispatch = _work_dispatch("The Example", ("Ada Author",), narrowed=True)
    exact = qualify_provider_observation(
        ProviderObservation(
            "catalogue",
            ProviderStatus.CANDIDATE_LIMIT_EXCEEDED,
            (),
            (),
            ProviderQueryKind.WORK,
            edition_enrichment_overflow=witness,
            dispatch_lineage=exact_dispatch,
        ),
        MetadataLookup(
            ProviderQueryKind.WORK,
            (),
            "The Example",
            ("Ada Author",),
        ),
    )
    assert exact.edition_enrichment_overflow == witness
    assert exact.dispatch_lineage == exact_dispatch
    assert exact.transformation_lineage is None

    variant_witness = EditionEnrichmentOverflow(
        witness.work_identity,
        "The Example (Variant)",
        witness.authors,
        witness.identity_bindings,
    )
    rejected_variant = qualify_provider_observation(
        ProviderObservation(
            "catalogue",
            ProviderStatus.CANDIDATE_LIMIT_EXCEEDED,
            (),
            (),
            ProviderQueryKind.WORK,
            edition_enrichment_overflow=variant_witness,
            dispatch_lineage=_work_dispatch("The Example (Variant)", ("Ada Author",)),
        ),
        MetadataLookup(
            ProviderQueryKind.WORK,
            (),
            "The Example (Variant)",
            ("Ada Author",),
        ),
    )
    assert rejected_variant.edition_enrichment_overflow is None


def test_generate_report_preserves_only_work_lane_date_explanations() -> None:
    creator = LocalClaim.create(
        kind=ClaimKind.CREATOR,
        observed="Ada Author",
        canonical="Ada Author",
        excerpt="Ada Author",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.WORK,
        role="author",
    )
    selected = TargetFile("a" * 64, "book.epub", "epub")
    target_fields = tuple(
        TargetField(
            TargetDestination.AUTHORS,
            TargetFieldState.VALUE,
            ("Ada Author",),
        )
        if field.destination is TargetDestination.AUTHORS
        else field
        for field in default_target_fields("The Example")
    )
    snapshot = TargetSnapshot("ebook", "The Example", (selected,), fields=target_fields)

    class Provider:
        cover_descriptors = ()

        def __init__(self, name: str) -> None:
            self.name = name

        def observe(self, plan, *, deadline=None):
            results = []
            for lane in plan.lanes:
                lookup = MetadataLookup(
                    lane.kind,
                    plan.local_claims,
                    lane.title,
                    lane.authors,
                    lane.matched_family,
                )
                candidate = ProviderCandidate(
                    f"{self.name}-edition",
                    f"{self.name}-work",
                    (
                        ProviderClaim.create(
                            provider=self.name,
                            candidate=f"{self.name}-edition",
                            kind=ClaimKind.TITLE,
                            value="The Example",
                            state=FieldState.VALID,
                            scope=ClaimScope.WORK,
                            applicability="not_applicable",
                        ),
                        ProviderClaim.create(
                            provider=self.name,
                            candidate=f"{self.name}-edition",
                            kind=ClaimKind.CREATOR,
                            value="Ada Author",
                            state=FieldState.VALID,
                            scope=ClaimScope.WORK,
                            applicability="not_applicable",
                            role="author",
                            raw_order=1,
                        ),
                    ),
                    (
                        ProviderEvidence(
                            ProviderEvidenceKind.PHYSICAL_FORMAT,
                            "Hardcover",
                            ClaimScope.EDITION,
                        ),
                        ProviderEvidence(
                            ProviderEvidenceKind.PUBLICATION_DATE_TEXT,
                            "2001-02-03",
                            ClaimScope.EDITION,
                            source_ordinal=1,
                        ),
                    ),
                )
                physical = ProviderObservation(
                    self.name,
                    ProviderStatus.CANDIDATES,
                    candidates=(
                        finalize_candidate_comparisons(
                            candidate,
                            plan.local_claims,
                            self.name,
                        ),
                    ),
                    query_kind=lane.kind,
                    dispatch_lineage=_work_dispatch("The Example", ("Ada Author",)),
                )
                qualified = qualify_provider_observation(physical, lookup)
                results.append(
                    ProviderLaneResult(
                        lane,
                        qualified.status,
                        qualified.candidates,
                        dispatch_lineage=qualified.dispatch_lineage,
                        transformation_lineage=qualified.transformation_lineage,
                    )
                )
            return provider_observation(self.name, tuple(results))

    names = ("google_books", "hardcover", "open_library")
    repository = FakeReportRepository()
    service = GenerateMetadataReport(
        target=FakeTarget([snapshot, snapshot]),
        inspector=FakeInspector(InspectionResult("b" * 64, (creator,))),
        repository_factory=lambda: cast(ReportRepository, repository),
        providers=tuple(
            ProviderBinding(
                name,
                True,
                cast(
                    "Callable[[], MetadataProvider]",
                    lambda name=name: Provider(name),
                ),
            )
            for name in names
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
            names,
        ),
        mount=MediaMountConfiguration("books", "/media", "/library", True),
        limits=ReportLimits(),
    )
    report = MetadataReport.from_json(service.run("item-1", 0))
    assert set(names) == {value.provider for value in report.providers}
    for provider in report.providers:
        assert ProviderStatus.CANDIDATES == provider.status, provider.to_data()
        candidate = provider.candidates[0]
        retained = next(
            value
            for value in candidate.evidence
            if value.kind is ProviderEvidenceKind.PUBLICATION_DATE_TEXT
        )
        assert (
            PublicationDateRelation.MANIFESTATION_INCOMPATIBLE
            == retained.links[0].publication_date_relation
        )
        assert all(
            value.scope is ClaimScope.WORK or value is retained
            for value in candidate.evidence
        )


def _assert_timestamp_voting_report(
    *,
    lexical: str,
    voting: bool,
    provider_count: int,
    local_present: bool,
    isbn: LocalClaim,
    snapshot: TargetSnapshot,
    provider_type,
) -> None:
    package_date = LocalClaim.create(
        kind=ClaimKind.PUBLICATION_DATE,
        observed=lexical,
        canonical=normalize_date_value(lexical)[0],
        excerpt=lexical,
        applicability="not_applicable",
        basis="package_metadata",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.EDITION,
        date_event=DateEvent.MANIFESTATION_PUBLICATION,
        date_precision=DatePrecision.TIMESTAMP,
    )
    names = tuple(f"catalogue_{index}" for index in range(1, provider_count + 1))
    repository = FakeReportRepository()
    service = GenerateMetadataReport(
        target=FakeTarget([snapshot, snapshot]),
        inspector=FakeInspector(
            InspectionResult(
                "b" * 64,
                (isbn, package_date) if local_present else (isbn,),
            )
        ),
        repository_factory=lambda: cast(ReportRepository, repository),
        providers=tuple(
            ProviderBinding(
                name,
                True,
                cast(
                    "Callable[[], MetadataProvider]",
                    lambda name=name: provider_type(name, lexical),
                ),
            )
            for name in names
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
            names,
        ),
        mount=MediaMountConfiguration("books", "/media", "/library", True),
        limits=ReportLimits(),
    )
    serialized = service.run("item-1", 0)
    report = MetadataReport.from_json(serialized)
    assert serialized == report.to_json()
    retained_local = tuple(
        value
        for value in report.local_claims
        if value.kind is ClaimKind.PUBLICATION_DATE
    )
    # Annotated so the equality below compares two variadic tuples: an
    # inline `(package_date,) if local_present else ()` is a fixed-length
    # union, and mypy narrows `retained_local` to it, making every later
    # `retained_local[0]` an out-of-range index against the empty arm.
    expected_local: tuple[LocalClaim, ...] = (package_date,) if local_present else ()
    assert expected_local == retained_local
    if local_present:
        assert voting == publication_date_is_voting(
            retained_local[0].observed,
            retained_local[0].date_precision,
        )
    provider_dates = tuple(
        claim
        for provider in report.providers
        for candidate in provider.candidates
        for claim in candidate.claims
        if claim.kind is ClaimKind.PUBLICATION_DATE
    )
    provider_text = tuple(
        evidence
        for provider in report.providers
        for candidate in provider.candidates
        for evidence in candidate.evidence
        if evidence.kind is ProviderEvidenceKind.PUBLICATION_DATE_TEXT
    )
    assert (provider_count if voting else 0) == len(provider_dates)
    assert (0 if voting else provider_count) == len(provider_text)
    proposal = evaluate_report(report)
    case = ReviewCase.create(proposal, report)
    packet = ShadowReviewPacket.create(case, proposal, report)
    assert packet == ShadowReviewPacket.from_json(packet.to_json(), report)
    publication_references = {
        reference
        for value in proposal.results
        if value.destination
        in {
            TargetDestination.PUBLISHED_DATE,
            TargetDestination.PUBLISHED_YEAR,
        }
        for candidate in value.alternatives
        for reference in candidate.evidence_references
    }
    source_references = {
        *(value.reference for value in provider_dates),
        *((retained_local[0].reference,) if local_present else ()),
    }
    # Generated-report providers do not declare independent provider authority;
    # timestamp parsing alone must never manufacture publication authority.
    assert source_references.isdisjoint(publication_references)


def test_generate_report_uses_source_lexical_timestamp_voting_matrix() -> None:
    isbn = LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="9780306406157",
        canonical="9780306406157",
        excerpt="9780306406157",
        applicability="ebook",
        basis="package_metadata",
        checksum="valid_isbn_13",
        applicability_basis="ebook",
        scope=ClaimScope.EDITION,
    )
    selected = TargetFile("a" * 64, "book.epub", "epub")
    snapshot = TargetSnapshot("ebook", None, (selected,))

    class Provider:
        cover_descriptors = ()

        def __init__(self, name: str, lexical: str) -> None:
            self.name = name
            self.lexical = lexical

        def observe(self, plan, *, deadline=None):
            results = []
            for lane in plan.lanes:
                candidate = ProviderCandidate(
                    f"{self.name}-edition",
                    "work-1",
                    (
                        ProviderClaim.create(
                            provider=self.name,
                            candidate=f"{self.name}-edition",
                            kind=ClaimKind.ISBN,
                            value="9780306406157",
                            state=FieldState.VALID,
                            scope=ClaimScope.EDITION,
                            applicability="ebook",
                        ),
                        ProviderClaim.create(
                            provider=self.name,
                            candidate=f"{self.name}-edition",
                            kind=ClaimKind.PUBLICATION_DATE,
                            value=self.lexical,
                            state=FieldState.VALID,
                            scope=ClaimScope.EDITION,
                            applicability="not_applicable",
                            raw_order=1,
                        ),
                    ),
                )
                physical = ProviderObservation(
                    self.name,
                    ProviderStatus.CANDIDATES,
                    candidates=(
                        finalize_candidate_comparisons(
                            candidate,
                            plan.local_claims,
                            self.name,
                        ),
                    ),
                    query_kind=lane.kind,
                )
                lookup = MetadataLookup(
                    lane.kind,
                    plan.local_claims,
                    lane.title,
                    lane.authors,
                    lane.matched_family,
                )
                qualified = qualify_provider_observation(physical, lookup)
                results.append(
                    ProviderLaneResult(
                        lane,
                        qualified.status,
                        qualified.candidates,
                    )
                )
            return provider_observation(self.name, tuple(results))

    lexical_cases = (
        ("2001-02-03T04:05:06Z", True),
        ("2001-02-03T04:05:06+01:00", True),
        ("2001-02-03T04:05:06+01:00:00", False),
        ("2001-02-03T04:05:06+01:00:01", False),
        ("2001-02-03T04:05:06+01:00:00.5", False),
    )
    assert normalize_date_value(lexical_cases[1][0]) == normalize_date_value(
        lexical_cases[2][0]
    )
    for lexical, voting in lexical_cases:
        assert voting == publication_date_is_voting(lexical, DatePrecision.TIMESTAMP)
        for provider_count in (1, 2):
            for local_present in (False, True):
                _assert_timestamp_voting_report(
                    lexical=lexical,
                    voting=voting,
                    provider_count=provider_count,
                    local_present=local_present,
                    isbn=isbn,
                    snapshot=snapshot,
                    provider_type=Provider,
                )


def test_work_lookup_fails_closed_for_competing_work_or_variant_markers() -> None:
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
                dispatch_lineage=_work_dispatch("The Example", ("Ada Author",)),
            ),
            lookup,
        )

    for candidates in (
        (candidate("edition-1", "work-1"), candidate("edition-2", "work-2")),
        (candidate("edition-1", "work-1", "translator"),),
        (candidate("edition-1", "work-1", "adapter"),),
        (candidate("edition-1", "work-1", "abridger"),),
    ):
        result = qualify(candidates)
        assert ProviderStatus.NO_MATCH == result.status
        assert () == result.candidates

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
            result = qualify(candidates)
            assert ProviderStatus.NO_MATCH == result.status, (field, spelling)
            assert () == result.candidates, (field, spelling)

    qualified = qualify((candidate("edition-1", "work-1"),))
    assert ProviderStatus.CANDIDATES == qualified.status
    assert ("edition-1",) == tuple(value.identity for value in qualified.candidates)


@pytest.mark.parametrize(
    ("provider_authors", "expected"),
    (
        pytest.param(
            ("ZED AUTHOR", "ada author"),
            ProviderStatus.CANDIDATES,
            id="exact_normalized_order",
        ),
        pytest.param(
            ("Ada Author", "Zed Author"),
            ProviderStatus.NO_MATCH,
            id="same_set_wrong_order",
        ),
        pytest.param(("Zed Author",), ProviderStatus.NO_MATCH, id="missing_author"),
        pytest.param(
            ("Zed Author", "Ada Author", "Ada Author"),
            ProviderStatus.NO_MATCH,
            id="extra_duplicate",
        ),
        pytest.param(
            ("Zed Author", "Zed Author"),
            ProviderStatus.NO_MATCH,
            id="duplicate_replacement",
        ),
    ),
)
def test_work_lookup_requires_the_complete_canonical_author_order(
    provider_authors, expected
) -> None:
    lookup = MetadataLookup(
        ProviderQueryKind.WORK,
        (),
        "The Example",
        ("Zed Author", "Ada Author"),
    )
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
            dispatch_lineage=_work_dispatch(
                "The Example", ("Zed Author", "Ada Author")
            ),
        ),
        lookup,
    )
    assert expected == result.status
    assert (expected is ProviderStatus.CANDIDATES) == bool(result.candidates)
