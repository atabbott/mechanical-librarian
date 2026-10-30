"""Metadata-report discovery: lookup qualification, the per-family
discovery plan and its overflow, work-lane admission, and the
source-lexical timestamp voting matrix.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import pytest

from mechanical_librarian.application import metadata_report as app
from mechanical_librarian.application.configuration import (
    LibraryConfiguration,
    MediaMountConfiguration,
    ReportLimits,
    TargetConnectionConfiguration,
)
from mechanical_librarian.application.metadata_report import (
    MAX_ASIN_DISCOVERY_LANES,
    GenerateMetadataReport,
    InspectionResult,
    MetadataDiscoveryLane,
    MetadataDiscoveryPlan,
    MetadataLookup,
    MetadataProvider,
    MetadataReportError,
    ProviderBinding,
    ProviderLaneResult,
    ProviderResult,
    ReportRepository,
    TargetFile,
    TargetSnapshot,
    audiobook_asin_feed_plan,
    audiobook_discovery_plan,
    identifier_suppresses_discovery,
    metadata_discovery_plan,
    metadata_lookup,
    promoted_asins,
    provider_discovery_lane_order,
    provider_observation,
    qualify_provider_observation,
    usable_isbn_families,
)
from mechanical_librarian.domain import metadata_report as dom
from mechanical_librarian.domain.audiobook_corroboration import (
    runtime_corroborated_candidate,
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
    ComparisonMethod,
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
    ProviderLaneCandidate,
    ProviderLaneObservation,
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
    work_discovery_lane_payloads,
    work_ladder_diagnostics,
)
from mechanical_librarian.domain.proposal_set import (
    ProposalOutcome,
    evaluate_report,
)
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.shadow_review import ShadowReviewPacket
from mechanical_librarian.domain.work_identity import (
    WorkDispatchLineage,
    WorkDispatchRung,
    WorkTitleTransformationApplication,
    WorkTitleTransformationFamily,
    WorkTitleTransformationField,
    WorkTitleTransformationSide,
    narrowing_work_query,
    work_query_ladder,
)
from tests.support.audiobook_providers import (
    asserted_target,
    audio_target,
    target_asin_claim,
    target_title_claim,
)
from tests.support.audiobook_reports import (
    audio_local_claims as _audio_local_claims,
)
from tests.support.audiobook_reports import (
    audio_target_fields as _audio_target_fields,
)
from tests.support.audiobook_reports import (
    audiobook_candidate as _audiobook_candidate,
)
from tests.support.audiobook_reports import (
    qualified_audiobook as _qualified_audiobook,
)
from tests.support.audiobook_reports import (
    recorded_dispatch,
    recording_lookup_hint_report,
)
from tests.support.metadata_report_doubles import (
    FakeInspector,
    FakeReportRepository,
    FakeTarget,
    single_track_audio_facts,
)
from tests.support.metadata_report_schemas import synthetic_latin_work_report


def _work_dispatch(
    title: str, authors: tuple[str, ...], *, narrowed: bool = False
) -> WorkDispatchLineage:
    query = work_query_ladder(title, authors)[0]
    return WorkDispatchLineage.from_query(
        narrowing_work_query(query) if narrowed else query
    )


def _identity_candidate(
    title: str = "Example Book",
    authors: tuple[str, ...] = ("Ada Example",),
    *,
    identity: str = "edition-1",
    work_identity: str = "work-1",
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
            *(
                ProviderClaim.create(
                    provider="catalogue",
                    candidate=identity,
                    kind=ClaimKind.CREATOR,
                    value=author,
                    state=FieldState.VALID,
                    scope=ClaimScope.WORK,
                    applicability="not_applicable",
                    role="author",
                    raw_order=index + 1,
                )
                for index, author in enumerate(authors)
            ),
        ),
    )


def test_multiple_usable_families_keep_provider_lane_order() -> None:
    first = LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="9780306406157",
        canonical="9780306406157",
        excerpt="ISBN 9780306406157",
        applicability="ebook",
        checksum="valid_isbn_13",
        applicability_basis="ebook",
    )
    second = LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="9780140328721",
        canonical="9780140328721",
        excerpt="ISBN 9780140328721",
        applicability="ebook",
        checksum="valid_isbn_13",
        applicability_basis="ebook",
    )
    plan = MetadataDiscoveryPlan(
        (first, second),
        (
            MetadataDiscoveryLane(
                "lane_" + "1" * 64,
                ProviderQueryKind.ISBN,
                (first.reference,),
                matched_family="9780306406157",
                canonical_isbns=("9780306406157",),
            ),
            MetadataDiscoveryLane(
                "lane_" + "2" * 64,
                ProviderQueryKind.WORK,
                (first.reference,),
                title="Example Book",
                authors=("Ada Example",),
            ),
            MetadataDiscoveryLane(
                "lane_" + "3" * 64,
                ProviderQueryKind.ISBN,
                (second.reference,),
                matched_family="9780140328721",
                canonical_isbns=("9780140328721",),
            ),
        ),
    )

    assert (
        plan.lanes[0],
        plan.lanes[1],
        plan.lanes[2],
    ) == provider_discovery_lane_order(plan)


@pytest.mark.parametrize(
    "claim",
    (
        LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="9780306406157",
            canonical="9780306406157",
            excerpt="ISBN 9780306406157",
            applicability="print",
            checksum="valid_isbn_13",
            applicability_basis="print",
        ),
        LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="9780306406157",
            canonical="9780306406157",
            excerpt="ISBN 9780306406157",
            applicability="ambiguous",
            checksum="valid_isbn_13",
            applicability_basis="ambiguous",
        ),
        LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="9780306406157",
            canonical="9780306406157",
            excerpt="ISBN 9780306406157",
            applicability="ebook",
            checksum="valid_isbn_13",
            applicability_basis="ebook",
            rear_provenance=RearClaimProvenance("a" * 64, 1, 1, "isbn", "ISBN"),
        ),
    ),
    ids=("print", "ambiguous", "rear"),
)
def test_nonusable_isbn_does_not_reorder_or_suppress_discovery(
    claim: LocalClaim,
) -> None:
    plan = MetadataDiscoveryPlan(
        (claim,),
        (
            MetadataDiscoveryLane(
                "lane_" + "1" * 64,
                ProviderQueryKind.WORK,
                (claim.reference,),
                title="Example Book",
                authors=("Ada Example",),
            ),
            MetadataDiscoveryLane(
                "lane_" + "2" * 64,
                ProviderQueryKind.ISBN,
                (claim.reference,),
                matched_family="9780306406157",
                canonical_isbns=("9780306406157",),
            ),
        ),
    )

    assert frozenset() == usable_isbn_families((claim,))
    assert plan.lanes is provider_discovery_lane_order(plan)
    assert not identifier_suppresses_discovery(
        plan,
        [
            ProviderLaneResult(
                plan.lanes[1],
                ProviderStatus.CANDIDATES,
                (_identity_candidate(),),
            )
        ],
        plan.lanes[0],
    )


def test_one_usable_family_precedes_nonusable_identifier_lanes() -> None:
    usable = LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="9780306406157",
        canonical="9780306406157",
        excerpt="ISBN 9780306406157",
        applicability="ebook",
        checksum="valid_isbn_13",
        applicability_basis="ebook",
    )
    print_only = LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="9780140328721",
        canonical="9780140328721",
        excerpt="ISBN 9780140328721",
        applicability="print",
        checksum="valid_isbn_13",
        applicability_basis="print",
    )
    work = MetadataDiscoveryLane(
        "lane_" + "1" * 64,
        ProviderQueryKind.WORK,
        (usable.reference,),
        title="Example Book",
        authors=("Ada Example",),
    )
    print_lane = MetadataDiscoveryLane(
        "lane_" + "2" * 64,
        ProviderQueryKind.ISBN,
        (print_only.reference,),
        matched_family="9780140328721",
        canonical_isbns=("9780140328721",),
    )
    usable_lane = MetadataDiscoveryLane(
        "lane_" + "3" * 64,
        ProviderQueryKind.ISBN,
        (usable.reference,),
        matched_family="9780306406157",
        canonical_isbns=("9780306406157",),
    )
    plan = MetadataDiscoveryPlan((usable, print_only), (work, print_lane, usable_lane))

    assert (usable_lane, print_lane, work) == provider_discovery_lane_order(plan)
    assert identifier_suppresses_discovery(
        plan,
        [
            ProviderLaneResult(
                usable_lane,
                ProviderStatus.CANDIDATES,
                (_identity_candidate(),),
            )
        ],
        work,
    )
    assert not identifier_suppresses_discovery(
        plan,
        [
            ProviderLaneResult(
                print_lane,
                ProviderStatus.CANDIDATES,
                (_identity_candidate(),),
            )
        ],
        work,
    )
    assert not identifier_suppresses_discovery(
        plan,
        [
            ProviderLaneResult(
                usable_lane,
                ProviderStatus.CANDIDATES,
                (_identity_candidate(),),
            ),
            ProviderLaneResult(print_lane, ProviderStatus.UNAVAILABLE),
        ],
        work,
    )


@pytest.mark.parametrize(
    "candidate",
    (
        _identity_candidate("Different Book"),
        _identity_candidate(authors=()),
        _identity_candidate(authors=("Example, Ada",)),
    ),
    ids=("contradictory", "unavailable", "inverted"),
)
def test_contradictory_or_unavailable_isbn_identity_preserves_work_fallback(
    candidate: ProviderCandidate,
) -> None:
    usable = LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="9780306406157",
        canonical="9780306406157",
        excerpt="ISBN 9780306406157",
        applicability="ebook",
        checksum="valid_isbn_13",
        applicability_basis="ebook",
    )
    work = MetadataDiscoveryLane(
        "lane_" + "1" * 64,
        ProviderQueryKind.WORK,
        (usable.reference,),
        title="Example Book",
        authors=("Ada Example",),
    )
    isbn = MetadataDiscoveryLane(
        "lane_" + "2" * 64,
        ProviderQueryKind.ISBN,
        (usable.reference,),
        matched_family="9780306406157",
        canonical_isbns=("9780306406157",),
    )
    plan = MetadataDiscoveryPlan((usable,), (work, isbn))

    assert not identifier_suppresses_discovery(
        plan,
        [ProviderLaneResult(isbn, ProviderStatus.CANDIDATES, (candidate,))],
        work,
    )


@pytest.mark.parametrize(
    "candidates",
    (
        (_identity_candidate(), _identity_candidate("Different Book")),
        (_identity_candidate(), _identity_candidate(authors=())),
        (
            _identity_candidate(),
            _identity_candidate(identity="edition-2", work_identity="work-2"),
        ),
    ),
    ids=("contradictory", "unavailable", "multiple_work_identities"),
)
def test_mixed_isbn_candidate_set_preserves_work_fallback(
    candidates: tuple[ProviderCandidate, ...],
) -> None:
    usable = LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="9780306406157",
        canonical="9780306406157",
        excerpt="ISBN 9780306406157",
        applicability="ebook",
        checksum="valid_isbn_13",
        applicability_basis="ebook",
    )
    work = MetadataDiscoveryLane(
        "lane_" + "1" * 64,
        ProviderQueryKind.WORK,
        (usable.reference,),
        title="Example Book",
        authors=("Ada Example",),
    )
    isbn = MetadataDiscoveryLane(
        "lane_" + "2" * 64,
        ProviderQueryKind.ISBN,
        (usable.reference,),
        matched_family="9780306406157",
        canonical_isbns=("9780306406157",),
    )

    assert not identifier_suppresses_discovery(
        MetadataDiscoveryPlan((usable,), (work, isbn)),
        [ProviderLaneResult(isbn, ProviderStatus.CANDIDATES, candidates)],
        work,
    )


def test_one_work_candidate_set_suppresses_redundant_work_fallback() -> None:
    usable = LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="9780306406157",
        canonical="9780306406157",
        excerpt="ISBN 9780306406157",
        applicability="ebook",
        checksum="valid_isbn_13",
        applicability_basis="ebook",
    )
    work = MetadataDiscoveryLane(
        "lane_" + "1" * 64,
        ProviderQueryKind.WORK,
        (usable.reference,),
        title="Example Book",
        authors=("Ada Example",),
    )
    isbn = MetadataDiscoveryLane(
        "lane_" + "2" * 64,
        ProviderQueryKind.ISBN,
        (usable.reference,),
        matched_family="9780306406157",
        canonical_isbns=("9780306406157",),
    )

    assert identifier_suppresses_discovery(
        MetadataDiscoveryPlan((usable,), (work, isbn)),
        [
            ProviderLaneResult(
                isbn,
                ProviderStatus.CANDIDATES,
                (
                    _identity_candidate(),
                    _identity_candidate(identity="edition-2"),
                ),
            )
        ],
        work,
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


def _audio_target(title: str | None, authors: tuple[str, ...]) -> TargetSnapshot:
    populated = (
        TargetField(TargetDestination.AUTHORS, TargetFieldState.VALUE, authors)
        if authors
        else TargetField(TargetDestination.AUTHORS, TargetFieldState.MISSING)
    )
    fields = tuple(
        populated if field.destination is TargetDestination.AUTHORS else field
        for field in default_target_fields(title)
    )
    return TargetSnapshot("audiobook", title, (), fields=fields)


def _audio_claim(kind: ClaimKind, value: str, occurrence: int = 1) -> LocalClaim:
    return LocalClaim.create(
        kind=kind,
        observed=value,
        canonical=value,
        excerpt=value,
        applicability="not_applicable",
        basis="embedded_tag",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        occurrence=occurrence,
    )


def _target_title_claim(title: str) -> LocalClaim:
    return LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed=title,
        canonical=title,
        excerpt=title,
        applicability="not_applicable",
        basis="target_metadata",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.WORK,
    )


def test_audiobook_plan_builds_one_lane_per_distinct_canonical_asin() -> None:
    """Two surfaces naming one ASIN are one lookup; two ASINs are two.

    An ISBN-10-shaped ASIN is admitted beside a `B0`-shaped one because
    ADR-0061 measured seventeen genuine ones on the reference library, and a
    prefix filter would have refused every one of them.
    """
    target = _audio_target("The Recording", ("Ada Author",))
    claims = (
        _target_title_claim("The Recording"),
        _audio_claim(ClaimKind.ASIN, "B002V1BFKM", 1),
        _audio_claim(ClaimKind.ASIN, "b002v1bfkm", 2),
        _audio_claim(ClaimKind.ASIN, "0743563123", 3),
    )
    plan = audiobook_discovery_plan(target, claims)
    assert not plan.overflow
    assert [ProviderQueryKind.ASIN, ProviderQueryKind.ASIN] == [
        lane.kind for lane in plan.lanes
    ]
    assert {"B002V1BFKM", "0743563123"} == {lane.canonical_asin for lane in plan.lanes}
    shared = next(lane for lane in plan.lanes if lane.canonical_asin == "B002V1BFKM")
    assert 2 == len(shared.input_references)
    assert plan == audiobook_discovery_plan(target, claims)


def test_audiobook_plan_searches_only_when_no_identifier_is_present() -> None:
    target = _audio_target("The Recording", ("Ada Author",))
    identified = audiobook_discovery_plan(
        target,
        (
            _target_title_claim("The Recording"),
            _audio_claim(ClaimKind.ASIN, "B002V1BFKM"),
        ),
    )
    assert [ProviderQueryKind.ASIN] == [lane.kind for lane in identified.lanes]
    unidentified = audiobook_discovery_plan(
        target, (_target_title_claim("The Recording"),)
    )
    assert [ProviderQueryKind.WORK] == [lane.kind for lane in unidentified.lanes]
    assert ("Ada Author",) == unidentified.lanes[0].authors
    assert () == audiobook_discovery_plan(_audio_target(None, ()), ()).lanes
    # An assertion is not one of the item's own surfaces, so it neither builds
    # a product lane here nor suppresses the search that reaches the item.
    asserted = audiobook_discovery_plan(
        target,
        (_target_title_claim("The Recording"), target_asin_claim("B002V1BFKM")),
    )
    assert unidentified.lanes == asserted.lanes


def test_audiobook_plan_refuses_an_unbounded_identifier_fan_out() -> None:
    target = _audio_target("The Recording", ("Ada Author",))
    claims = (
        _target_title_claim("The Recording"),
        *(
            _audio_claim(ClaimKind.ASIN, f"B00{index:07d}", index + 1)
            for index in range(MAX_ASIN_DISCOVERY_LANES + 1)
        ),
    )
    overflowed = audiobook_discovery_plan(target, claims)
    assert overflowed.overflow
    assert () == overflowed.lanes


def test_asin_lane_requires_one_canonical_identifier_and_nothing_else() -> None:
    identity = "lane_" + "0" * 64
    reference = "local_" + "1" * 64
    assert (
        ProviderQueryKind.ASIN
        == MetadataDiscoveryLane(
            identity,
            ProviderQueryKind.ASIN,
            (reference,),
            canonical_asin="B002V1BFKM",
        ).kind
    )
    for keywords in (
        {},
        {"canonical_asin": "b002v1bfkm"},
        {"canonical_asin": "B002V1BFK"},
        {"canonical_asin": "B002V1BFKM", "matched_family": "9780306406157"},
        {"canonical_asin": "B002V1BFKM", "title": "The Recording"},
        {"canonical_asin": "B002V1BFKM", "canonical_isbns": ("9780306406157",)},
    ):
        with pytest.raises(MetadataReportError):
            MetadataDiscoveryLane(
                identity, ProviderQueryKind.ASIN, (reference,), **keywords
            )
    with pytest.raises(MetadataReportError):
        MetadataDiscoveryLane(
            identity, ProviderQueryKind.ASIN, (), canonical_asin="B002V1BFKM"
        )


def _feed_claims(asin: str | None = "B0SYNE7P1K") -> tuple[LocalClaim, ...]:
    """One asserted audiobook's evidence: no local ASIN, one running time."""
    return (
        target_title_claim(),
        *_audio_local_claims(),
        *(() if asin is None else (target_asin_claim(asin),)),
    )


def _search_observation(
    plan: MetadataDiscoveryPlan,
    local_claims: tuple[LocalClaim, ...],
    *,
    provider: str = "catalogue",
    asin: str = "B0SYNE7P1K",
) -> ProviderObservation:
    """One work-lane observation whose candidate the runtime band admitted."""
    lane = next(value for value in plan.lanes if value.kind is ProviderQueryKind.WORK)
    qualified = qualify_provider_observation(
        ProviderObservation(
            provider,
            ProviderStatus.CANDIDATES,
            (),
            (
                _audiobook_candidate(
                    local_claims,
                    identity=f"audible-product-{asin}",
                    asin=asin,
                    provider=provider,
                ),
            ),
            ProviderQueryKind.WORK,
            "audible_catalog",
            dispatch_lineage=_work_dispatch("Ember Tide Protocol", ("Corin Halloway",)),
        ),
        MetadataLookup(
            ProviderQueryKind.WORK,
            local_claims,
            "Ember Tide Protocol",
            ("Corin Halloway",),
        ),
    )
    return provider_observation(
        provider,
        (ProviderLaneResult(lane, qualified.status, qualified.candidates),),
        authority="audible_catalog",
    )


def test_asserted_asin_feeds_one_lane_after_local_lanes() -> None:
    """An assertion obtains per-ASIN enrichment without becoming a surface."""
    local_claims = _feed_claims("B0CW1KL2QD")
    target = asserted_target("B0CW1KL2QD")
    plan = audiobook_discovery_plan(target, local_claims)
    assert [ProviderQueryKind.WORK] == [lane.kind for lane in plan.lanes]

    feed = audiobook_asin_feed_plan(target, plan, ())

    assert feed is not None
    assert [ProviderQueryKind.ASIN] == [lane.kind for lane in feed.lanes]
    assert ["B0CW1KL2QD"] == [lane.canonical_asin for lane in feed.lanes]
    asserted = next(
        claim
        for claim in local_claims
        if claim.basis == "target_metadata"
        if claim.kind is ClaimKind.ASIN
    )
    assert (asserted.reference,) == feed.lanes[0].input_references
    assert local_claims == feed.local_claims
    assert feed == audiobook_asin_feed_plan(target, plan, ())
    # An assertion the target no longer carries feeds nothing, whatever claim
    # an older document retained.
    assert (
        audiobook_asin_feed_plan(
            _audio_target("Ember Tide Protocol", ("Corin Halloway",)), plan, ()
        )
        is None
    )


def test_feed_plan_is_bounded_and_ordered_local_promoted_asserted() -> None:
    """Four lanes is the whole budget, and promotion is spent before assertion.

    The local origin comes first by exclusion rather than by rank: an item that
    already names its own product has its identifier, so re-asking about the
    same one would spend egress to learn what the report already states.
    """
    local_claims = _feed_claims("B0ASSERTED")
    target = asserted_target("B0ASSERTED")
    plan = audiobook_discovery_plan(target, local_claims)
    promoted = ("B0PROMOTE1", "B0PROMOTE2", "B0PROMOTE3", "B0PROMOTE4")
    observations = tuple(
        _search_observation(
            plan, local_claims, provider=f"catalogue-{index}", asin=asin
        )
        for index, asin in enumerate(promoted)
    )

    feed = audiobook_asin_feed_plan(target, plan, observations)

    assert promoted == promoted_asins(observations)
    assert feed is not None
    assert MAX_ASIN_DISCOVERY_LANES == len(feed.lanes)
    assert set(promoted) == {lane.canonical_asin for lane in feed.lanes}
    identified = audiobook_discovery_plan(
        target, (*local_claims, _audio_claim(ClaimKind.ASIN, "B0LOCALASN", 9))
    )
    assert [ProviderQueryKind.ASIN] == [lane.kind for lane in identified.lanes]
    assert audiobook_asin_feed_plan(target, identified, observations) is None


def test_feed_lane_identities_never_collide_across_origins() -> None:
    """One ASIN is one lane whatever fed it, keyed on the references it names."""
    local_claims = _feed_claims()
    target = asserted_target()
    plan = audiobook_discovery_plan(target, local_claims)
    work_lane = plan.lanes[0]
    asserted = next(
        claim
        for claim in local_claims
        if claim.basis == "target_metadata" and claim.kind is ClaimKind.ASIN
    )
    search = (_search_observation(plan, local_claims),)
    unasserted = _audio_target("Ember Tide Protocol", ("Corin Halloway",))

    asserted_only = audiobook_asin_feed_plan(target, plan, ())
    promoted_only = audiobook_asin_feed_plan(unasserted, plan, search)
    both = audiobook_asin_feed_plan(target, plan, search)

    assert asserted_only is not None
    assert promoted_only is not None
    assert both is not None
    assert (asserted.reference,) == asserted_only.lanes[0].input_references
    assert work_lane.input_references == promoted_only.lanes[0].input_references
    assert 1 == len(both.lanes)
    assert (
        tuple(sorted({asserted.reference, *work_lane.input_references}))
        == both.lanes[0].input_references
    )
    assert 3 == len(
        {
            asserted_only.lanes[0].identity,
            promoted_only.lanes[0].identity,
            both.lanes[0].identity,
        }
    )


@pytest.mark.parametrize(
    ("provider_duration", "expected"),
    (
        (
            "58200.000",
            {
                ClaimKind.TITLE,
                ClaimKind.CREATOR,
                ClaimKind.ASIN,
                ClaimKind.DURATION,
                ClaimKind.CONTRIBUTOR,
            },
        ),
        (
            "51000.000",
            {ClaimKind.TITLE, ClaimKind.CREATOR, ClaimKind.ASIN, ClaimKind.DURATION},
        ),
    ),
    ids=("corroborated", "uncorroborated"),
)
@pytest.mark.parametrize("titled", (True, False), ids=("titled", "untitled"))
def test_an_asin_lane_without_a_local_asin_is_corroboration_gated(
    provider_duration: str, expected: set[ClaimKind], titled: bool
) -> None:
    """A fed lane never settles identity; the runtime band still decides.

    The lane names a product, but nothing on the recording said so, so an
    answer to it is treated exactly as an answer to a search is: only the
    candidate the band admits keeps its edition scope. Without a title and
    authors to admit against, no answer passes exact work admission, so each
    keeps only its identifier (#1257).
    """
    local_claims = _feed_claims()
    qualified = qualify_provider_observation(
        ProviderObservation(
            "catalogue",
            ProviderStatus.CANDIDATES,
            (),
            (_audiobook_candidate(local_claims, duration=provider_duration),),
            ProviderQueryKind.ASIN,
            "audnexus",
        ),
        MetadataLookup(
            ProviderQueryKind.ASIN,
            local_claims,
            work_title="Ember Tide Protocol" if titled else None,
            work_authors=("Corin Halloway",) if titled else (),
        ),
    )

    assert ProviderStatus.CANDIDATES is qualified.status
    assert (expected | {ClaimKind.CONTRIBUTOR} if titled else {ClaimKind.ASIN}) == {
        claim.kind for claim in qualified.candidates[0].claims
    }


def test_a_local_asin_retains_candidate_without_inventing_recording_support() -> None:
    """The item's own identifier is the warrant, so nothing re-derives it."""
    local_claims = (*_feed_claims(), _audio_claim(ClaimKind.ASIN, "B0SYNE7P1K", 9))
    candidate = _audiobook_candidate(local_claims, duration="51000.000")
    qualified = qualify_provider_observation(
        ProviderObservation(
            "catalogue",
            ProviderStatus.CANDIDATES,
            (),
            (candidate,),
            ProviderQueryKind.ASIN,
            "audnexus",
        ),
        MetadataLookup(ProviderQueryKind.ASIN, local_claims),
    )

    assert len(qualified.candidates) == 1
    assert qualified.candidates[0].identity == candidate.identity
    assert not any(
        e.kind is ProviderEvidenceKind.ASIN_MARKETPLACE
        for e in qualified.candidates[0].evidence
    )


def test_a_plan_refuses_two_lanes_for_one_asin() -> None:
    claim = _audio_claim(ClaimKind.ASIN, "B002V1BFKM")
    lanes = tuple(
        MetadataDiscoveryLane(
            f"lane_{index}" + "0" * 63,
            ProviderQueryKind.ASIN,
            (claim.reference,),
            canonical_asin="B002V1BFKM",
        )
        for index in (1, 2)
    )
    with pytest.raises(MetadataReportError):
        MetadataDiscoveryPlan((claim,), lanes)


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
    assert "metadata-report-v63" == report.schema_version
    previous = report.to_data()
    previous["schema_version"] = "metadata-report-v22"
    with pytest.raises(ReportValueError):
        MetadataReport.from_json(canonical_json(previous))


_REFUSED_TITLE = "Ember Tide Protocol: A Novel"
_REFUSED_AUTHORS = ("Corin Halloway",)
_REFUSED_LADDER = tuple(
    WorkDispatchLineage.from_query(query)
    for query in work_query_ladder(_REFUSED_TITLE, _REFUSED_AUTHORS, audiobook=True)
)


def _exhausted_lane(**changes: object) -> ProviderLaneObservation:
    lane = ProviderLaneObservation(
        "lane_" + "1" * 64,
        ProviderQueryKind.WORK,
        ProviderStatus.NO_MATCH,
        (target_title_claim(_REFUSED_TITLE).reference,),
        diagnostics=work_ladder_diagnostics(0, 3),
        exhausted_dispatch=_REFUSED_LADDER,
    )
    return replace(lane, **changes)


def _exhausted_report(
    lane: ProviderLaneObservation,
    *,
    title: str = _REFUSED_TITLE,
    audio: bool = True,
) -> MetadataReport:
    fields = audio_target(title, _REFUSED_AUTHORS).fields
    # The lane is the one its own target plans, so only its ladder can differ.
    lane = replace(
        lane,
        identity=work_discovery_lane_payloads(
            fields, (target_title_claim(_REFUSED_TITLE),)
        )[0].identity,
    )
    return MetadataReport.create(
        target=TargetFacts("primary", "audio", "item-1", fields),
        file=FileFacts(0, "a" * 64, "b" * 64, "m4b" if audio else "epub"),
        local_claims=(target_title_claim(_REFUSED_TITLE),),
        local_reasons=("target_metadata_title",),
        providers=(
            (recorded_dispatch if audio else lambda value: value)(
                ProviderObservation(
                    "audible",
                    ProviderStatus.NO_MATCH,
                    lane.input_references,
                    query_kind=ProviderQueryKind.WORK,
                    authority="audible_catalog",
                    lanes=(lane,),
                    accessed_at="2023-11-14T22:13:20.000000Z",
                    diagnostics=lane.diagnostics,
                )
            ),
        ),
        audio=single_track_audio_facts() if audio else None,
    )


def test_an_exhausted_audiobook_ladder_round_trips_with_its_dispatches() -> None:
    report = _exhausted_report(_exhausted_lane())

    assert 2 == len(_REFUSED_LADDER)
    assert _REFUSED_LADDER == report.providers[0].lanes[0].exhausted_dispatch
    assert report == MetadataReport.from_json(report.to_json())


@pytest.mark.parametrize(
    "changes",
    (
        {
            "status": ProviderStatus.CANDIDATES,
            "candidates": (
                ProviderLaneCandidate("lane_candidate_" + "2" * 64, "e", "w"),
            ),
        },
        {"kind": ProviderQueryKind.ISBN, "matched_family": "9780000000000"},
        {"kind": ProviderQueryKind.ASIN},
        {"exhausted_dispatch": (_REFUSED_LADDER[0], _REFUSED_LADDER[0])},
        {"diagnostics": work_ladder_diagnostics(0, 1)},
        {"diagnostics": work_ladder_diagnostics(1, 0)},
        {"diagnostics": work_ladder_diagnostics(3, 0)},
        {"diagnostics": work_ladder_diagnostics(2, 1)},
    ),
    ids=(
        "candidates",
        "isbn",
        "asin",
        "repeated_rung",
        "fewer_products_than_refused_rungs",
        "refused_rung_without_products",
        "more_empty_rungs_than_dispatched",
        "products_without_a_refused_rung",
    ),
)
def test_a_lane_states_an_exhausted_ladder_only_with_consistent_counts(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ReportValueError, match="exhausted dispatch is invalid"):
        _exhausted_lane(**changes)


def test_a_refusal_is_stated_only_by_a_lane_that_walked_its_ladder() -> None:
    with pytest.raises(ReportValueError, match="provider lane refusal is invalid"):
        _exhausted_lane(exhausted_dispatch=())


_LANE_CANDIDATE = ProviderLaneCandidate("lane_candidate_" + "2" * 64, "e", "w")
_EMPTY_LADDER = work_ladder_diagnostics(len(_REFUSED_LADDER), 0)
# One lane per row of the dispatch table (#1258): its status, and the ladder a
# no-match WORK lane states, decide which outcome it may carry.
_DISPATCH_LANES: dict[str, dict[str, object]] = {
    "admitted": {
        "status": ProviderStatus.CANDIDATES,
        "candidates": (_LANE_CANDIDATE,),
        "diagnostics": (),
        "exhausted_dispatch": (),
    },
    "refused_ladder": {},
    "empty_ladder": {"diagnostics": _EMPTY_LADDER},
    "no_ladder": {"diagnostics": (), "exhausted_dispatch": ()},
    "asin": {
        "kind": ProviderQueryKind.ASIN,
        "diagnostics": (),
        "exhausted_dispatch": (),
    },
    **{
        status.value: {"status": status, "diagnostics": (), "exhausted_dispatch": ()}
        for status in (
            ProviderStatus.CANDIDATE_LIMIT_EXCEEDED,
            ProviderStatus.INCOMPLETE,
            ProviderStatus.UNAVAILABLE,
            ProviderStatus.RATE_LIMITED,
            ProviderStatus.AUTHENTICATION,
            ProviderStatus.AUTHORIZATION,
            ProviderStatus.MALFORMED,
            ProviderStatus.OVERSIZED,
            ProviderStatus.INSUFFICIENT_INPUT,
            ProviderStatus.DISABLED,
        )
    },
}
_O = dom.DispatchOutcome
_DISPATCH_TABLE = (
    ("admitted", _O.ADMITTED),
    ("refused_ladder", _O.REFUSED),
    ("empty_ladder", _O.EMPTY),
    ("no_ladder", _O.EMPTY_OR_REFUSED),
    ("asin", _O.EMPTY),
    ("candidate_limit_exceeded", _O.OVERFLOW),
    ("incomplete", _O.STARVED),
    ("incomplete", _O.UNAVAILABLE),
    *(
        (status, _O.UNAVAILABLE)
        for status in (
            "unavailable",
            "rate_limited",
            "authentication",
            "authorization",
            "malformed",
            "oversized",
        )
    ),
    ("insufficient_input", _O.NOT_DISPATCHED),
    ("disabled", _O.NOT_DISPATCHED),
)
# One outcome per row the table does not allow.
_OFF_TABLE = (
    ("admitted", _O.OVERFLOW),
    ("refused_ladder", _O.EMPTY),
    ("empty_ladder", _O.REFUSED),
    ("no_ladder", _O.EMPTY),
    ("asin", _O.EMPTY_OR_REFUSED),
    ("candidate_limit_exceeded", _O.ADMITTED),
    ("incomplete", _O.NOT_DISPATCHED),
    *(
        (status, _O.STARVED)
        for status in (
            "unavailable",
            "rate_limited",
            "authentication",
            "authorization",
            "malformed",
            "oversized",
        )
    ),
    ("insufficient_input", _O.UNAVAILABLE),
    ("disabled", _O.EMPTY),
)


def _dispatch_lane(
    row: str, dispatch: dom.ProviderLaneDispatch | None
) -> ProviderLaneObservation:
    return _exhausted_lane(**_DISPATCH_LANES[row], dispatch=dispatch)


@pytest.mark.parametrize(
    ("row", "outcome"), _DISPATCH_TABLE, ids=[row for row, _ in _DISPATCH_TABLE]
)
def test_every_dispatch_table_row_is_accepted_and_round_trips(
    row: str, outcome: dom.DispatchOutcome
) -> None:
    lane = _dispatch_lane(row, dom.ProviderLaneDispatch(outcome))
    data = lane.to_data()

    assert {"near_miss_seconds": None, "outcome": outcome.value, "rungs": None} == (
        data["dispatch"]
    )
    assert lane == dom._provider_lane_from_data(data)
    assert outcome is dom.lane_dispatch_outcome(
        lane, starved=outcome is dom.DispatchOutcome.STARVED
    )


@pytest.mark.parametrize(
    ("row", "outcome"), _OFF_TABLE, ids=[row for row, _ in _OFF_TABLE]
)
def test_a_lane_refuses_every_outcome_its_row_does_not_allow(
    row: str, outcome: dom.DispatchOutcome
) -> None:
    with pytest.raises(ReportValueError, match="provider lane dispatch is invalid"):
        _dispatch_lane(row, dom.ProviderLaneDispatch(outcome))


def test_only_an_audiobook_report_states_lane_dispatch_records() -> None:
    record = dom.ProviderLaneDispatch(_O.EMPTY_OR_REFUSED)
    assert (record,) == tuple(
        lane.dispatch
        for lane in _exhausted_report(_dispatch_lane("no_ladder", record))
        .providers[0]
        .lanes
    )
    assert (None,) == tuple(
        lane.dispatch
        for lane in _exhausted_report(_dispatch_lane("no_ladder", None), audio=False)
        .providers[0]
        .lanes
    )
    with pytest.raises(
        ReportValueError, match="provider lane dispatch presence is invalid"
    ):
        _exhausted_report(_dispatch_lane("no_ladder", record), audio=False)


@pytest.mark.parametrize(
    ("status", "outcomes", "expected"),
    (
        ("candidates", (_O.ADMITTED, _O.REFUSED), _O.ADMITTED),
        ("no_match", (_O.EMPTY_OR_REFUSED, _O.EMPTY, _O.REFUSED), _O.REFUSED),
        ("no_match", (_O.EMPTY_OR_REFUSED, _O.EMPTY), _O.EMPTY),
        ("no_match", (_O.EMPTY_OR_REFUSED,), _O.EMPTY_OR_REFUSED),
        ("incomplete", (_O.UNAVAILABLE, _O.STARVED), _O.STARVED),
        ("incomplete", (_O.UNAVAILABLE,), _O.UNAVAILABLE),
        ("candidate_limit_exceeded", (_O.OVERFLOW,), _O.OVERFLOW),
        ("unavailable", (_O.UNAVAILABLE,), _O.UNAVAILABLE),
        ("malformed", (_O.UNAVAILABLE,), _O.UNAVAILABLE),
        ("query_limit_exceeded", (), _O.NOT_DISPATCHED),
        ("insufficient_input", (), _O.NOT_DISPATCHED),
        ("disabled", (_O.NOT_DISPATCHED,), _O.NOT_DISPATCHED),
    ),
    ids=(
        "admitted",
        "refusal_first",
        "empty_before_unknown",
        "empty_or_refused",
        "one_starved_lane",
        "unavailable",
        "overflow",
        "unavailable_status",
        "malformed",
        "query_limit",
        "insufficient_input",
        "disabled",
    ),
)
def test_a_provider_dispatch_class_follows_its_status_and_lane_outcomes(
    status: str,
    outcomes: tuple[dom.DispatchOutcome, ...],
    expected: dom.DispatchOutcome,
) -> None:
    observation = SimpleNamespace(
        status=ProviderStatus(status),
        lanes=tuple(
            SimpleNamespace(dispatch=dom.ProviderLaneDispatch(value))
            for value in outcomes
        ),
    )
    assert expected is dom.provider_dispatch_class(cast(Any, observation))


def test_only_a_work_lane_states_ladder_depth_or_a_near_miss() -> None:
    work = _dispatch_lane(
        "refused_ladder", dom.ProviderLaneDispatch(_O.REFUSED, 2, 900)
    )
    assert dom.ProviderLaneDispatch(_O.REFUSED, 2, 900) == work.dispatch
    for dispatch in (
        dom.ProviderLaneDispatch(_O.EMPTY, rungs=1),
        dom.ProviderLaneDispatch(_O.EMPTY, near_miss_seconds=900),
    ):
        with pytest.raises(ReportValueError, match="provider lane dispatch is invalid"):
            _dispatch_lane("asin", dispatch)


@pytest.mark.parametrize(
    "values",
    (
        ("refused", None, None),
        (_O.REFUSED, True, None),
        (_O.REFUSED, -1, None),
        (_O.REFUSED, 1.0, None),
        (_O.REFUSED, "1", None),
        (_O.REFUSED, None, True),
        (_O.REFUSED, None, 900.0),
        (_O.REFUSED, None, "900"),
    ),
    ids=(
        "text_outcome",
        "bool_rungs",
        "negative_rungs",
        "float_rungs",
        "text_rungs",
        "bool_near_miss",
        "float_near_miss",
        "text_near_miss",
    ),
)
def test_a_dispatch_record_holds_a_closed_outcome_and_exact_integers(
    values: tuple[object, object, object],
) -> None:
    with pytest.raises(ReportValueError, match="provider lane dispatch is invalid"):
        dom.ProviderLaneDispatch(*cast(Any, values))


@pytest.mark.parametrize(
    "dispatch",
    (
        {"near_miss_seconds": None, "outcome": "refused", "rungs": 2, "text": "x"},
        {"outcome": "refused", "rungs": 2},
        {"near_miss_seconds": None, "outcome": "unknown", "rungs": None},
        {"near_miss_seconds": None, "outcome": "refused", "rungs": False},
        "refused",
    ),
    ids=("unknown_key", "missing_key", "unknown_outcome", "bool_rungs", "not_object"),
)
def test_a_dispatch_record_is_read_back_only_in_its_closed_shape(
    dispatch: object,
) -> None:
    data = _dispatch_lane(
        "refused_ladder", dom.ProviderLaneDispatch(_O.REFUSED, 2)
    ).to_data()
    data["dispatch"] = dispatch
    with pytest.raises((ReportValueError, ValueError)):
        dom._provider_lane_from_data(data)


@pytest.mark.parametrize(
    ("lane", "report"),
    (
        ({"exhausted_dispatch": tuple(reversed(_REFUSED_LADDER))}, {}),
        ({"exhausted_dispatch": _REFUSED_LADDER[:1]}, {}),
        (
            {
                "exhausted_dispatch": (
                    _REFUSED_LADDER[0],
                    WorkDispatchLineage.from_query(
                        work_query_ladder(
                            "Caf\N{LATIN SMALL LETTER E WITH ACUTE}: Notes",
                            ("Jos\N{LATIN SMALL LETTER E WITH ACUTE}",),
                        )[-1]
                    ),
                )
            },
            {},
        ),
        ({}, {"title": "Ember Tide Protocol"}),
        ({}, {"audio": False}),
    ),
    ids=("out_of_order", "truncated", "out_of_ladder", "other_target", "ebook"),
)
def test_a_report_keeps_only_the_ladder_its_audiobook_target_produces(
    lane: dict[str, object], report: dict[str, Any]
) -> None:
    with pytest.raises(ReportValueError, match="exhausted dispatch is inconsistent"):
        _exhausted_report(_exhausted_lane(**lane), **report)


_HINT = dom.RecordingLookupHint("B000000011", 58_200, _REFUSED_LADDER[0])


@pytest.mark.parametrize(
    "changes",
    (
        {"recording_lookup_hints": [_HINT]},
        {"recording_lookup_hints": ("B000000011",)},
        {
            "recording_lookup_hints": tuple(
                dom.RecordingLookupHint(f"B00000001{index}", 58_200, _HINT.rung)
                for index in range(3)
            )
        },
        {"recording_lookup_hints": (_HINT, _HINT)},
        {"recording_lookup_hints": (replace(_HINT, asin="B000000012"), _HINT)},
        {
            "kind": ProviderQueryKind.ASIN,
            "exhausted_dispatch": (),
            "diagnostics": (),
        },
        {"exhausted_dispatch": (), "diagnostics": ()},
        {"exhausted_dispatch": _REFUSED_LADDER[1:]},
        {"diagnostics": work_ladder_diagnostics(len(_REFUSED_LADDER), 0)},
        {
            "status": ProviderStatus.CANDIDATES,
            "candidates": (
                ProviderLaneCandidate("lane_candidate_" + "2" * 64, "e", "w"),
            ),
            "exhausted_dispatch": (),
            "diagnostics": (),
        },
        {
            "status": ProviderStatus.UNAVAILABLE,
            "exhausted_dispatch": (),
            "diagnostics": (),
        },
    ),
    ids=(
        "list",
        "not_a_hint",
        "over_bound",
        "duplicated",
        "unsorted",
        "not_work",
        "no_match_without_exhaustion",
        "rung_outside_exhaustion",
        "no_refusal",
        "candidates_without_lineage",
        "other_status",
    ),
)
def test_a_lane_states_recording_lookup_hints_only_where_it_can(
    changes: dict[str, object],
) -> None:
    with pytest.raises(
        ReportValueError, match="provider lane recording lookup hints are invalid"
    ):
        _exhausted_lane(**{"recording_lookup_hints": (_HINT,), **changes})


def test_a_recording_lookup_hint_is_closed_and_canonical() -> None:
    for arguments in (
        ("b000000011", 58_200, _HINT.rung),
        (None, 58_200, _HINT.rung),
        ("B000000011", 0, _HINT.rung),
        ("B000000011", True, _HINT.rung),
        ("B000000011", 604_801, _HINT.rung),
        ("B000000011", 58_200, _HINT.rung.to_data()),
    ):
        with pytest.raises(ReportValueError, match="recording lookup hint is invalid"):
            dom.RecordingLookupHint(*arguments)
    assert 604_800 == replace(_HINT, runtime_seconds=604_800).runtime_seconds
    report = recording_lookup_hint_report()
    assert report == MetadataReport.from_json(report.to_json())
    hint = json.loads(report.to_json())["providers"][0]["lanes"][0][
        "recording_lookup_hints"
    ][0]
    for key, value in (("basis", "runtime_within_loose_band"), ("extra", 1)):
        changed = json.loads(report.to_json())
        changed["providers"][0]["lanes"][0]["recording_lookup_hints"][0] = {
            **hint,
            key: value,
        }
        with pytest.raises(ReportValueError):
            MetadataReport.from_json(json.dumps(changed))


def test_band_first_hints_feed_last_inside_the_cap_and_only_once() -> None:
    """Hints follow every stronger origin and never displace one (#1257)."""
    exact = ("B000000001", "B000000002", "B000000003")
    report = recording_lookup_hint_report(
        lookup_asins=exact, hint_asins=("B000000011", "B000000012")
    )
    plan = audiobook_discovery_plan(
        audio_target(), report.local_claims, audio=report.audio
    )
    (observation,) = report.providers
    (lane,) = observation.lanes

    feed = audiobook_asin_feed_plan(audio_target(), plan, (observation,))

    assert feed is not None
    assert MAX_ASIN_DISCOVERY_LANES == len(feed.lanes)
    # The fourth lane goes to the earlier hint; the later one is crowded out.
    assert {*exact, "B000000011"} == {value.canonical_asin for value in feed.lanes}
    assert all(value.input_references == lane.input_references for value in feed.lanes)
    # One pass: the fed plan is never fed again.
    assert audiobook_asin_feed_plan(audio_target(), feed, (observation,)) is None
    hinted = recording_lookup_hint_report(hint_asins=("B000000011", "B000000012"))
    only = audiobook_asin_feed_plan(audio_target(), plan, hinted.providers)
    assert only is not None
    assert {"B000000011", "B000000012"} == {
        value.canonical_asin for value in only.lanes
    }
    # A hint the target also asserts is one lane naming both origins.
    asserted = asserted_target("B000000012")
    asserted_plan = audiobook_discovery_plan(
        asserted, (*report.local_claims, target_asin_claim("B000000012"))
    )
    both = audiobook_asin_feed_plan(asserted, asserted_plan, hinted.providers)
    assert both is not None
    assert {"B000000011", "B000000012"} == {
        value.canonical_asin for value in both.lanes
    }
    assert {
        target_asin_claim("B000000012").reference,
        *lane.input_references,
    } == set(
        next(
            value for value in both.lanes if value.canonical_asin == "B000000012"
        ).input_references
    )


@pytest.mark.parametrize("authority", ("audnexus", "itunes_search"))
def test_only_an_audible_observation_carries_or_feeds_a_hint(authority: str) -> None:
    """Only Audible produces hints, so no other authority's is built or fed."""
    hinted = recording_lookup_hint_report(hint_asins=("B000000011",))
    plan = audiobook_discovery_plan(
        audio_target(), hinted.local_claims, audio=hinted.audio
    )
    (observation,) = hinted.providers
    (recorded,) = observation.lanes
    asserted = next(lane for lane in plan.lanes if lane.identity == recorded.identity)

    forged = replace(observation, authority=authority)
    assert audiobook_asin_feed_plan(audio_target(), plan, (forged,)) is None
    with pytest.raises(MetadataReportError, match="provider lane result is invalid"):
        provider_observation(
            observation.provider,
            (
                ProviderLaneResult(
                    asserted,
                    ProviderStatus.NO_MATCH,
                    diagnostics=recorded.diagnostics,
                    exhausted_dispatch=recorded.exhausted_dispatch,
                    recording_lookup_hints=recorded.recording_lookup_hints,
                ),
            ),
            authority=authority,
        )


def test_an_ebook_ladder_keeps_no_recording_lookup_hint() -> None:
    """A refused tight-runtime product on an ebook plan is no hint (#1257)."""
    local = _audio_local_claims()
    product = _audiobook_candidate(local, identity="product-1", asin="B000000011")

    def observe(query: Any) -> ProviderObservation:
        return ProviderObservation(
            "catalogue",
            ProviderStatus.CANDIDATES,
            (),
            (product,),
            ProviderQueryKind.WORK,
            "audible_catalog",
            dispatch_lineage=WorkDispatchLineage.from_query(query),
        )

    lookup = MetadataLookup(
        ProviderQueryKind.WORK, local, "Other Title", ("Corin Halloway",)
    )
    ebook = app.observe_work_ladder(lookup, observe)
    audio = app.observe_work_ladder(
        replace(lookup, audio=recording_lookup_hint_report().audio), observe
    )

    assert ProviderStatus.NO_MATCH is ebook.observation.status
    assert () == ebook.recording_lookup_hints
    assert ("B000000011",) == tuple(
        value.asin for value in audio.recording_lookup_hints
    )
    lane = ProviderLaneObservation(
        "lane_" + "1" * 64,
        ProviderQueryKind.WORK,
        ProviderStatus.NO_MATCH,
        ("local_" + "2" * 64,),
    )
    assert [] == lane.to_data()["recording_lookup_hints"]


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


def _drifting_lane_results(
    lanes: tuple[MetadataDiscoveryLane, ...],
) -> tuple[ProviderLaneResult, ...]:
    """One candidate identity restated across rungs with claims that disagree."""
    return (
        ProviderLaneResult(
            lanes[0],
            ProviderStatus.CANDIDATES,
            (
                _identity_candidate(
                    title="The Example",
                    identity="edition-shared",
                    work_identity="work-shared",
                ),
            ),
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
            (
                _identity_candidate(
                    title="A Different Example",
                    identity="edition-shared",
                    work_identity="work-shared",
                ),
            ),
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
        pytest.param(_drifting_lane_results, ProviderStatus.MALFORMED, id="drift"),
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
        "category": "malformed_response",
        "component": "provider",
        "count": 1,
        "current": None,
        "retryable": False,
        "stage": "response",
    }
    identity_diagnostic = {
        "category": "identity",
        "component": "provider",
        "count": 1,
        "current": None,
        "retryable": False,
        "stage": "identity",
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
        assert [identity_diagnostic, declared_diagnostic] == by_identity_diagnostics[
            lanes[0].identity
        ]
        assert [identity_diagnostic] == by_identity_diagnostics[lanes[1].identity]
        assert malformed_diagnostic not in by_identity_diagnostics[lanes[0].identity]
        assert [identity_diagnostic] == [
            value.to_data() for value in observation.diagnostics
        ]
    else:
        assert [] == by_identity_diagnostics[lanes[0].identity]
        assert [] == by_identity_diagnostics[lanes[1].identity]
        assert () == observation.diagnostics
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


def test_latin_candidate_set_keeps_evidence_exact_and_rejects_two_work_ids() -> None:
    def candidate(
        identity: str,
        work_identity: str,
        title: str,
        author: str,
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
                    value=author,
                    state=FieldState.VALID,
                    scope=ClaimScope.WORK,
                    applicability="not_applicable",
                    role="author",
                    raw_order=1,
                ),
            ),
        )

    local_title = "Caf\N{LATIN SMALL LETTER E WITH ACUTE}"
    local_authors = ("Jos\N{LATIN SMALL LETTER E WITH ACUTE}",)
    lookup = MetadataLookup(ProviderQueryKind.WORK, (), local_title, local_authors)
    dispatch = WorkDispatchLineage.from_query(
        work_query_ladder(local_title, local_authors)[-1]
    )
    recorded = candidate("edition-1", "work-1", "Cafe", "Jose")
    qualified = qualify_provider_observation(
        ProviderObservation(
            "catalogue",
            ProviderStatus.CANDIDATES,
            (),
            (recorded,),
            ProviderQueryKind.WORK,
            dispatch_lineage=dispatch,
        ),
        lookup,
    )

    assert ProviderStatus.CANDIDATES is qualified.status
    assert (recorded,) == qualified.candidates
    assert (local_title, local_authors) == (lookup.title, lookup.authors)
    assert qualified.dispatch_lineage == dispatch
    assert dispatch.transformations[-1] is (
        WorkTitleTransformationFamily.LATIN_COMBINING_MARKS
    )
    assert qualified.transformation_lineage is not None
    assert qualified.transformation_lineage.applications == (
        WorkTitleTransformationApplication(
            WorkTitleTransformationFamily.LATIN_COMBINING_MARKS,
            WorkTitleTransformationSide.LOCAL,
            WorkTitleTransformationField.TITLE,
            None,
        ),
        WorkTitleTransformationApplication(
            WorkTitleTransformationFamily.LATIN_COMBINING_MARKS,
            WorkTitleTransformationSide.LOCAL,
            WorkTitleTransformationField.AUTHORS,
            0,
        ),
    )

    provider_only = qualify_provider_observation(
        ProviderObservation(
            "catalogue",
            ProviderStatus.CANDIDATES,
            (),
            (
                candidate(
                    "edition-1",
                    "work-1",
                    "Caf\N{LATIN SMALL LETTER E WITH ACUTE}",
                    "Jos\N{LATIN SMALL LETTER E WITH ACUTE}",
                ),
            ),
            ProviderQueryKind.WORK,
            dispatch_lineage=WorkDispatchLineage.from_query(
                work_query_ladder("Cafe", ("Jose",))[0]
            ),
        ),
        MetadataLookup(ProviderQueryKind.WORK, (), "Cafe", ("Jose",)),
    )
    assert provider_only.transformation_lineage is not None
    assert {
        application.side
        for application in provider_only.transformation_lineage.applications
    } == {WorkTitleTransformationSide.PROVIDER}
    assert provider_only.dispatch_lineage is not None
    assert WorkTitleTransformationFamily.LATIN_COMBINING_MARKS not in (
        provider_only.dispatch_lineage.transformations
    )

    ambiguous = qualify_provider_observation(
        ProviderObservation(
            "catalogue",
            ProviderStatus.CANDIDATES,
            (),
            (
                recorded,
                candidate("edition-2", "work-2", "Cafe", "Jose"),
            ),
            ProviderQueryKind.WORK,
            dispatch_lineage=dispatch,
        ),
        lookup,
    )
    assert ProviderStatus.NO_MATCH is ambiguous.status
    assert ambiguous.dispatch_lineage is None
    assert ambiguous.transformation_lineage is None


@pytest.mark.parametrize(
    "variant",
    (
        "missing",
        "extra",
        "duplicate",
        "misordered",
        "wrong_side",
        "wrong_field",
        "wrong_author_ordinal",
        "noop_application",
    ),
)
def test_report_replay_rejects_each_forged_latin_application(variant: str) -> None:
    data = synthetic_latin_work_report().to_data()
    provider = cast(dict[str, object], cast(list[object], data["providers"])[0])
    lineage = cast(dict[str, object], provider["transformation_lineage"])
    applications = cast(list[dict[str, object]], lineage["applications"])
    title, author = applications

    if variant == "missing":
        applications[:] = [title]
    elif variant == "extra":
        applications.append({**author, "ordinal": 1})
    elif variant == "duplicate":
        applications.append(dict(author))
    elif variant == "misordered":
        applications.reverse()
    elif variant == "wrong_side":
        applications[:] = [author, {**title, "side": "provider"}]
    elif variant == "wrong_field":
        applications[0] = {**title, "field": "authors", "ordinal": 0}
    elif variant == "wrong_author_ordinal":
        applications[1] = {**author, "ordinal": 1}
    else:
        applications.append({**title, "family": "punctuation_ampersand_fold"})

    identity_data = dict(data)
    identity_data.pop("report_id")
    data["report_id"] = (
        "report_" + hashlib.sha256(canonical_json(identity_data).encode()).hexdigest()
    )
    with pytest.raises(ValueError):
        MetadataReport.from_json(canonical_json(data))


def test_report_json_separates_latin_dispatch_from_admission_evidence() -> None:
    document = json.loads(synthetic_latin_work_report().to_json())
    provider = document["providers"][0]

    assert [
        (field["destination"], field["value"])
        for field in document["target"]["fields"]
        if field["destination"] in {"title", "authors"}
    ] == [
        ("title", "Caf\N{LATIN SMALL LETTER E WITH ACUTE}"),
        ("authors", ["Jos\N{LATIN SMALL LETTER E WITH ACUTE}"]),
    ]
    assert provider["dispatch_lineage"] == {
        "candidate_limit_narrowing_retry": False,
        "rung": "punctuation_folded",
        "transformations": ["latin_combining_marks"],
    }
    assert provider["transformation_lineage"] == {
        "applications": [
            {
                "family": "latin_combining_marks",
                "field": "title",
                "ordinal": None,
                "side": "local",
            },
            {
                "family": "latin_combining_marks",
                "field": "authors",
                "ordinal": 0,
                "side": "local",
            },
        ],
        "production": "latin_combining_marks",
    }
    assert ["Cafe", "Jose"] == [
        claim["value"] for claim in provider["candidates"][0]["claims"]
    ]


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


def test_edition_overflow_replays_reordered_complete_work_authors() -> None:
    authors = ("Ada Author", "Bea Author")
    provider_authors = tuple(reversed(authors))
    claims = tuple(
        ProviderClaim.create(
            provider="catalogue",
            candidate="work-1",
            kind=ClaimKind.CREATOR,
            value=author,
            state=FieldState.VALID,
            scope=ClaimScope.WORK,
            applicability="not_applicable",
            role="author",
            raw_order=index,
        )
        for index, author in enumerate(provider_authors)
    )
    witness = EditionEnrichmentOverflow(
        "work-1",
        "The Example",
        claims,
        tuple(
            ProviderIdentityBinding(
                claim.reference,
                f"author-{index}",
                ClaimScope.WORK,
                "author",
                index,
            )
            for index, claim in enumerate(claims)
        ),
    )
    dispatch = _work_dispatch("The Example", authors, narrowed=True)

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
        MetadataLookup(ProviderQueryKind.WORK, (), "The Example", authors),
    )

    assert qualified.edition_enrichment_overflow == witness
    assert qualified.dispatch_lineage == dispatch
    assert qualified.transformation_lineage is None


def test_latin_edition_overflow_replays_only_its_exact_application_lineage() -> None:
    author = ProviderClaim.create(
        provider="catalogue",
        candidate="work-1",
        kind=ClaimKind.CREATOR,
        value="Jose",
        state=FieldState.VALID,
        scope=ClaimScope.WORK,
        applicability="not_applicable",
        role="author",
    )
    witness = EditionEnrichmentOverflow(
        "work-1",
        "Cafe",
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
    title = "Caf\N{LATIN SMALL LETTER E WITH ACUTE}"
    authors = ("Jos\N{LATIN SMALL LETTER E WITH ACUTE}",)
    dispatch = WorkDispatchLineage.from_query(work_query_ladder(title, authors)[-1])
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
        MetadataLookup(ProviderQueryKind.WORK, (), title, authors),
    )

    assert qualified.edition_enrichment_overflow == witness
    assert qualified.dispatch_lineage == dispatch
    assert qualified.transformation_lineage is not None
    assert qualified.transformation_lineage.applications == (
        WorkTitleTransformationApplication(
            WorkTitleTransformationFamily.LATIN_COMBINING_MARKS,
            WorkTitleTransformationSide.LOCAL,
            WorkTitleTransformationField.TITLE,
            None,
        ),
        WorkTitleTransformationApplication(
            WorkTitleTransformationFamily.LATIN_COMBINING_MARKS,
            WorkTitleTransformationSide.LOCAL,
            WorkTitleTransformationField.AUTHORS,
            0,
        ),
    )

    mismatched = qualify_provider_observation(
        replace(
            qualified,
            edition_enrichment_overflow=replace(witness, title="Cafes"),
        ),
        MetadataLookup(ProviderQueryKind.WORK, (), title, authors),
    )
    assert mismatched.edition_enrichment_overflow is None
    assert mismatched.transformation_lineage is None


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
            return ProviderResult(
                provider_observation(self.name, tuple(results)),
                cover_descriptors=self.cover_descriptors,
            )

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
            return ProviderResult(
                provider_observation(self.name, tuple(results)),
                cover_descriptors=self.cover_descriptors,
            )

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
            ProviderStatus.CANDIDATES,
            id="same_set_reordered",
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
def test_work_lookup_requires_the_complete_canonical_author_multiset(
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


def test_a_runtime_corroborated_audiobook_candidate_keeps_its_edition_identity() -> (
    None
):
    """The corroboration gate is what widens a work admission to an edition one."""
    local_claims = _audio_local_claims()
    qualified = _qualified_audiobook(local_claims)
    candidate = qualified.candidates[0]

    assert ProviderStatus.CANDIDATES is qualified.status
    assert {
        ClaimKind.TITLE,
        ClaimKind.CREATOR,
        ClaimKind.ASIN,
        ClaimKind.DURATION,
        ClaimKind.CONTRIBUTOR,
    } == {claim.kind for claim in candidate.claims}
    assert "B0SYNE7P1K" == next(
        claim.value for claim in candidate.claims if claim.kind is ClaimKind.ASIN
    )
    assert {"narrator"} == {
        claim.role for claim in candidate.claims if claim.kind is ClaimKind.CONTRIBUTOR
    }
    assert {
        ProviderEvidenceKind.ASIN_MARKETPLACE,
        ProviderEvidenceKind.CHAPTER_STRUCTURE,
        ProviderEvidenceKind.EDITION_WORK_RELATION,
    } == {value.kind for value in candidate.evidence}
    assert "us" == next(
        value.value
        for value in candidate.evidence
        if value.kind is ProviderEvidenceKind.ASIN_MARKETPLACE
    )


def test_the_retained_duration_claim_states_the_band_that_admitted_it() -> None:
    """The report explains the admission through the comparison it already has.

    The retained `DURATION` claim carries its link to the local running time
    under `DURATION_BAND_AGREEMENT`, which is the rule that admitted the
    candidate stated in the report's own closed comparison vocabulary.
    """
    local_claims = _audio_local_claims()
    candidate = _qualified_audiobook(local_claims).candidates[0]
    duration = next(
        claim for claim in candidate.claims if claim.kind is ClaimKind.DURATION
    )
    local = next(claim for claim in local_claims if claim.kind is ClaimKind.DURATION)
    link = next(
        link for link in duration.links if link.local_reference == local.reference
    )

    assert Comparison.AGREEMENT is link.outcome
    assert ComparisonMethod.DURATION_BAND_AGREEMENT is link.method


@pytest.mark.parametrize(
    ("local_duration", "provider_duration"),
    (("58244.201", "51000.000"), (None, "58200.000")),
    ids=("band_fails", "no_local_duration"),
)
def test_an_uncorroborated_audiobook_candidate_keeps_only_its_work_shape(
    local_duration: str | None, provider_duration: str
) -> None:
    candidate = _qualified_audiobook(
        _audio_local_claims(local_duration), duration=provider_duration
    ).candidates[0]

    assert {
        ClaimKind.TITLE,
        ClaimKind.CREATOR,
        ClaimKind.CONTRIBUTOR,
        ClaimKind.ASIN,
        ClaimKind.DURATION,
    } == {claim.kind for claim in candidate.claims}
    assert all(
        e.kind is ProviderEvidenceKind.EDITION_WORK_RELATION for e in candidate.evidence
    )


def test_an_ebook_authority_never_reaches_the_widening() -> None:
    """The gate is dead code for a provider that is not an audiobook authority.

    The candidate here states everything the band would need, and the local
    runtime agrees.  Only the authority differs, and that alone leaves the
    observation in the shape every ebook provider has always had.
    """
    local_claims = _audio_local_claims()
    for authority in ("google_books", "hardcover", "open_library", None):
        candidate = _qualified_audiobook(local_claims, authority=authority).candidates[
            0
        ]

        assert {ClaimKind.TITLE, ClaimKind.CREATOR, ClaimKind.CONTRIBUTOR} == {
            claim.kind for claim in candidate.claims
        }
        assert all(
            e.kind is ProviderEvidenceKind.EDITION_WORK_RELATION
            for e in candidate.evidence
        )


def test_several_corroborated_candidates_break_the_tie_on_identity() -> None:
    """One work in two marketplaces states one runtime, so the tie is real.

    Qualification cannot reach this today -- an `unresolved` work identity is
    keyed on the candidate identity, so a two-candidate set is two work
    identities and the admission above refuses it -- but the rule is total and
    its tie-break is what keeps a future multi-candidate set deterministic.
    """
    local_claims = _audio_local_claims()
    candidates = (
        _audiobook_candidate(
            local_claims,
            identity="audible-product-B0ZZZZZZZZ",
            asin="B0ZZZZZZZZ",
        ),
        _audiobook_candidate(
            local_claims,
            identity="audible-product-B0AAAAAAAA",
            asin="B0AAAAAAAA",
        ),
    )
    chosen = runtime_corroborated_candidate(candidates, local_claims)

    assert chosen is not None
    assert "audible-product-B0AAAAAAAA" == chosen[0].identity
    assert ("runtime_within_tight_band",) == chosen[1]


def test_an_admitted_audiobook_reaches_the_same_destinations_an_ebook_does() -> None:
    """F45: an admitted recording is proposed on, exactly as an ebook is.

    Evaluation was silent about audiobooks before #966, so the admitted ASIN
    and the narrator cast beside it reached no destination at all. Both are
    now decisions the owner can be asked to make; the running time that proved
    the admission is still not one, and the cover stays withheld.
    """
    local_claims = _audio_local_claims()
    qualified = _qualified_audiobook(local_claims)
    report = MetadataReport.create(
        target=TargetFacts("primary", "audio", "item-1", _audio_target_fields()),
        file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
        local_claims=local_claims,
        local_reasons=("embedded_tag_duration",),
        providers=(qualified,),
        audio=single_track_audio_facts(),
    )
    proposals = evaluate_report(report)

    assert ClaimKind.ASIN in {claim.kind for claim in qualified.candidates[0].claims}
    assert "audiobook" == proposals.media_kind
    outcomes = {result.destination: result for result in proposals.results}
    assert {TargetDestination.ASIN, TargetDestination.NARRATORS} == {
        destination
        for destination, result in outcomes.items()
        if result.outcome
        in {
            ProposalOutcome.PROPOSE_CHANGE,
            ProposalOutcome.ACCEPTABLE_CHOICE,
            ProposalOutcome.UNRESOLVED_CHOICE,
        }
    }
    assert ProposalOutcome.PROPOSE_CHANGE is outcomes[TargetDestination.ASIN].outcome
    # The complete cast belongs to an independently admitted recording.
    assert ("unique_typed_candidate",) == outcomes[TargetDestination.NARRATORS].reasons
    assert ("no_supported_evidence",) == outcomes[TargetDestination.COVER].reasons


@pytest.mark.parametrize(
    "context",
    (
        "absent",
        "complete",
        "title_only",
        "authors_only",
        "duplicate_authors",
        "wrong_audio",
    ),
)
def test_recording_lookup_and_plan_share_complete_transient_context(
    context: str,
) -> None:
    from tests.support.metadata_report_doubles import single_track_audio_facts

    values = {
        "audio": single_track_audio_facts(),
        "work_title": "Example",
        "work_authors": ("Author",),
    }
    if context == "absent":
        values = {}
    elif context == "title_only":
        values["work_authors"] = ()
    elif context == "authors_only":
        values["work_title"] = None
    elif context == "duplicate_authors":
        values["work_authors"] = ("Author", "Author")
    elif context == "wrong_audio":
        values["audio"] = object()
    for constructor in (
        lambda: MetadataLookup(ProviderQueryKind.ASIN, (), **values),
        lambda: MetadataDiscoveryPlan((), (), **values),
    ):
        if context in {"absent", "complete"}:
            result = constructor()
            assert result.work_title == values.get("work_title")
        else:
            with pytest.raises(MetadataReportError):
                constructor()


def test_lookup_hints_feed_all_recordings_under_existing_cap_without_promotion() -> (
    None
):
    from tests.support.audiobook_providers import audio_target
    from tests.support.audiobook_reports import recording_lookup_report

    report = recording_lookup_report(asins=tuple(f"B00000000{i}" for i in range(1, 6)))
    plan = audiobook_discovery_plan(
        audio_target(), report.local_claims, audio=report.audio
    )
    observation = report.providers[0]
    assert promoted_asins((observation,)) == ()
    feed = audiobook_asin_feed_plan(audio_target(), plan, (observation,))
    assert feed is not None and len(feed.lanes) == 4
    assert {lane.canonical_asin for lane in feed.lanes} == {
        f"B00000000{i}" for i in range(1, 5)
    }
    assert all(
        lane.input_references == observation.lanes[0].input_references
        for lane in feed.lanes
    )
    assert (feed.audio, feed.work_title, feed.work_authors) == (
        report.audio,
        plan.work_title,
        plan.work_authors,
    )
    assert len(observation.recording_lookup_candidates) == 5
    assert audiobook_asin_feed_plan(audio_target(), feed, (observation,)) is None


def test_failed_work_set_keeps_individual_recordings_and_original_runtime_order() -> (
    None
):
    from dataclasses import replace

    from mechanical_librarian.domain.work_identity import (
        WorkDispatchLineage,
        work_query_ladder,
    )
    from tests.support.audiobook_providers import work_plan
    from tests.support.audiobook_reports import recording_lookup_report

    report = recording_lookup_report()
    hints = report.providers[0].recording_lookup_candidates
    raw = ProviderObservation(
        "catalogue",
        ProviderStatus.CANDIDATES,
        (),
        hints,
        ProviderQueryKind.WORK,
        "audnexus",
        dispatch_lineage=WorkDispatchLineage.from_query(
            work_query_ladder("Ember Tide Protocol", ("Corin Halloway",))[0]
        ),
    )
    plan = work_plan(*(c for c in report.local_claims if c.basis != "target_metadata"))
    lookup = MetadataLookup(
        ProviderQueryKind.WORK, plan.local_claims, plan.work_title, plan.work_authors
    )
    assert qualify_provider_observation(raw, lookup).recording_lookup_candidates == ()
    lookup = replace(lookup, audio=report.audio)
    result = qualify_provider_observation(raw, lookup)
    assert result.status is ProviderStatus.NO_MATCH and result.candidates == ()
    assert result.recording_lookup_candidates == hints
    assert result.dispatch_lineage is None and result.transformation_lineage is None
    one = qualify_provider_observation(replace(raw, candidates=(hints[0],)), lookup)
    assert one.recording_lookup_candidates == ()
    source_claims = tuple(
        c for c in hints[0].claims if c.kind in {ClaimKind.ASIN, ClaimKind.DURATION}
    )
    retained = tuple(
        c
        for c in one.candidates[0].claims
        if c.kind in {ClaimKind.ASIN, ClaimKind.DURATION}
    )
    assert retained == source_claims
    assert not any(
        e.kind is ProviderEvidenceKind.ASIN_MARKETPLACE
        for e in one.candidates[0].evidence
    )


@pytest.mark.parametrize(
    "kind,requested,valid",
    [
        (ProviderQueryKind.ASIN, None, True),
        (ProviderQueryKind.ASIN, "B000000001", True),
        (ProviderQueryKind.ASIN, "b000000001", False),
        (ProviderQueryKind.ASIN, "bad", False),
        (ProviderQueryKind.ASIN, 1, False),
        (ProviderQueryKind.NONE, "B000000001", False),
        (ProviderQueryKind.ISBN, "B000000001", False),
    ],
)
def test_requested_asin_is_canonical_and_asin_only(kind, requested, valid) -> None:
    if valid:
        assert (
            MetadataLookup(kind, (), requested_asin=requested).requested_asin
            == requested
        )
    else:
        with pytest.raises(MetadataReportError):
            MetadataLookup(kind, (), requested_asin=requested)


@pytest.mark.parametrize("local", (False, True))
@pytest.mark.parametrize("requested_asin", (None, "B0SYNE7P1K", "B000000002"))
def test_request_binding_precedes_local_shortcut_and_chapter_widening(
    local: bool, requested_asin: str | None
) -> None:
    from tests.support.audiobook_reports import chapter_runtime_report

    report = chapter_runtime_report(
        asin_basis="embedded_tag" if local else "target_metadata"
    )
    source = report.providers[0]
    observation = ProviderObservation(
        source.provider,
        source.status,
        source.input_references,
        source.candidates,
        ProviderQueryKind.ASIN,
        source.authority,
    )
    result = qualify_provider_observation(
        observation,
        MetadataLookup(
            ProviderQueryKind.ASIN,
            report.local_claims,
            audio=report.audio,
            work_title="Ember Tide Protocol",
            work_authors=("Corin Halloway",),
            requested_asin=requested_asin,
        ),
    )
    if requested_asin == "B000000002":
        assert result.status is ProviderStatus.MALFORMED
        assert result.candidates == ()
        return
    assert result.status is ProviderStatus.CANDIDATES
    admitted = any(
        e.kind is ProviderEvidenceKind.ASIN_MARKETPLACE
        for e in result.candidates[0].evidence
    )
    assert admitted is (local or requested_asin is not None)


def test_bound_request_cannot_borrow_another_local_asin_shortcut() -> None:
    from tests.support.audiobook_reports import (
        audiobook_candidate,
        chapter_runtime_report,
    )

    local = chapter_runtime_report()
    source = local.providers[0]
    observation = ProviderObservation(
        source.provider,
        source.status,
        (),
        (
            audiobook_candidate(
                local.local_claims, asin="B000000002", duration="72000.000"
            ),
        ),
        ProviderQueryKind.ASIN,
        source.authority,
    )
    result = qualify_provider_observation(
        observation,
        MetadataLookup(
            ProviderQueryKind.ASIN,
            local.local_claims,
            requested_asin="B000000002",
        ),
    )
    assert result.status is ProviderStatus.CANDIDATES
    assert not any(
        e.kind is ProviderEvidenceKind.ASIN_MARKETPLACE
        for e in result.candidates[0].evidence
    )


_LEAF = dom.AudioDiscoveryTitle("path_leaf", "The Real Title")
_TAG = dom.AudioDiscoveryTitle("title_tag", "Another Real Title")


def _discovery_audio(
    *titles: dom.AudioDiscoveryTitle,
) -> dom.AudioFacts:
    return replace(single_track_audio_facts(), discovery_titles=titles)


def _series_target(title: str, series: str) -> TargetSnapshot:
    target = _audio_target(title, ("Ada Author",))
    recorded = TargetField(
        TargetDestination.SERIES,
        TargetFieldState.VALUE,
        (dom.TargetSeries(series, "3"),),
    )
    return replace(
        target,
        fields=tuple(
            recorded if field.destination is TargetDestination.SERIES else field
            for field in target.fields
        ),
    )


def test_an_alternative_lane_stands_beside_a_byte_identical_asserted_lane() -> None:
    target = _audio_target("Asserted Title", ("Ada Author",))
    claims = (_target_title_claim("Asserted Title"),)
    previous = audiobook_discovery_plan(target, claims)
    plan = audiobook_discovery_plan(target, claims, audio=_discovery_audio(_LEAF, _TAG))

    asserted = next(lane for lane in plan.lanes if lane.title_basis is None)
    alternative = next(lane for lane in plan.lanes if lane.title_basis is not None)
    assert 2 == len(plan.lanes)
    assert previous.lanes == (asserted,)
    assert ("path_leaf", "The Real Title") == (
        alternative.title_basis,
        alternative.title,
    )
    assert (ProviderQueryKind.WORK, ("Ada Author",)) == (
        alternative.kind,
        alternative.authors,
    )
    assert asserted.input_references == alternative.input_references
    assert alternative.identity == dom.discovery_lane_identity(
        {
            "authors": ["Ada Author"],
            "input_references": list(asserted.input_references),
            "kind": "work",
            "title": "The Real Title",
            "title_basis": "path_leaf",
        }
    )
    assert (asserted, alternative) == app.provider_discovery_lane_order(plan)


def test_a_title_equal_to_a_recorded_series_collapses_to_the_alternative() -> None:
    target = _series_target("Series Name", "series name")
    claims = (_target_title_claim("Series Name"),)
    plan = audiobook_discovery_plan(target, claims, audio=_discovery_audio(_LEAF))

    assert [("path_leaf", "The Real Title")] == [
        (lane.title_basis, lane.title) for lane in plan.lanes
    ]
    assert app.alternative_work_lane_permitted(plan, None)
    # Without a surviving discovery title the asserted lane stays alone.
    assert [None] == [
        lane.title_basis
        for lane in audiobook_discovery_plan(
            target, claims, audio=_discovery_audio()
        ).lanes
    ]
    # A position-like title the target does not record as a series keeps the
    # asserted lane beside the alternative.
    kept = audiobook_discovery_plan(
        _series_target("Plain Work, Book 3", "Other Series"),
        (_target_title_claim("Plain Work, Book 3"),),
        audio=_discovery_audio(_LEAF),
    )
    assert [None, "path_leaf"] == [
        lane.title_basis for lane in app.provider_discovery_lane_order(kept)
    ]


@pytest.mark.parametrize("title", ("Part II", "Book 3", "Volume 1"))
def test_a_title_with_no_query_rung_collapses_to_the_alternative(title: str) -> None:
    target = _audio_target(title, ("Ada Author",))
    claims = (_target_title_claim(title),)
    plan = audiobook_discovery_plan(target, claims, audio=_discovery_audio(_LEAF))

    assert () == work_query_ladder(title, ("Ada Author",), audiobook=True)
    assert [("path_leaf", "The Real Title")] == [
        (lane.title_basis, lane.title) for lane in plan.lanes
    ]
    assert app.alternative_work_lane_permitted(plan, None)


def test_a_report_replays_the_collapse_of_a_title_with_no_query_rung() -> None:
    fields = _audio_target("Part II", ("Ada Author",)).fields
    claims = (_target_title_claim("Part II"),)
    (asserted,) = work_discovery_lane_payloads(fields, claims)
    (alternative,) = work_discovery_lane_payloads(fields, claims, (_LEAF,))
    assert "path_leaf" == alternative.title_basis

    def report(identity: str) -> MetadataReport:
        lane = ProviderLaneObservation(
            identity,
            ProviderQueryKind.WORK,
            ProviderStatus.UNAVAILABLE,
            asserted.input_references,
        )
        return MetadataReport.create(
            target=TargetFacts("primary", "audio", "item-1", fields),
            file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
            local_claims=claims,
            local_reasons=("target_metadata_title",),
            providers=(
                recorded_dispatch(
                    ProviderObservation(
                        "audible",
                        ProviderStatus.UNAVAILABLE,
                        lane.input_references,
                        query_kind=ProviderQueryKind.WORK,
                        authority="audible_catalog",
                        lanes=(lane,),
                    )
                ),
            ),
            audio=_discovery_audio(_LEAF),
        )

    replayed = report(alternative.identity)
    assert replayed == MetadataReport.from_json(replayed.to_json())
    # The asserted lane is no longer planned, so a report claiming it is
    # refused rather than replayed.
    with pytest.raises(ReportValueError, match="provider work lane is unknown"):
        report(asserted.identity)


def test_discovery_titles_repeating_a_query_already_made_do_not_survive() -> None:
    fields = _series_target("Asserted Title", "Series Name").fields
    titles = (
        dom.AudioDiscoveryTitle("path_leaf", "asserted title"),
        dom.AudioDiscoveryTitle("title_tag", "Series Name"),
    )
    assert () == dom.surviving_discovery_titles(titles, fields)
    assert () == dom.surviving_discovery_titles(
        (dom.AudioDiscoveryTitle("path_leaf", "Ada Author"),), fields
    )
    assert (_LEAF,) == dom.surviving_discovery_titles(
        (_LEAF, dom.AudioDiscoveryTitle("title_tag", "THE REAL TITLE")), ()
    )


@pytest.mark.parametrize(
    "titles",
    (
        (_TAG, _LEAF),
        (_LEAF, _LEAF),
        (_LEAF, dom.AudioDiscoveryTitle("title_tag", "the real title")),
        (_LEAF, _TAG, _TAG),
        [_LEAF],
    ),
    ids=("tag_before_leaf", "repeated_basis", "closed_equal", "three", "list"),
)
def test_audio_facts_refuse_disordered_or_repeated_discovery_titles(
    titles: Any,
) -> None:
    with pytest.raises(ReportValueError):
        _discovery_audio(*titles) if type(titles) is tuple else replace(
            single_track_audio_facts(), discovery_titles=titles
        )


@pytest.mark.parametrize(
    ("basis", "title"),
    (
        ("folder", "The Real Title"),
        ("path_leaf", " The Real Title"),
        ("path_leaf", "The  Real Title"),
        ("path_leaf", "The Real\u202eTitle"),
        ("path_leaf", ""),
        ("path_leaf", "x" * 281),
    ),
)
def test_a_discovery_title_is_one_known_basis_and_normalized_text(
    basis: str, title: str
) -> None:
    with pytest.raises(ReportValueError):
        dom.AudioDiscoveryTitle(basis, title)


def test_discovery_titles_round_trip_and_are_a_closed_audio_key() -> None:
    audio = _discovery_audio(_LEAF, _TAG)
    data = audio.to_data()

    assert [
        {"basis": "path_leaf", "title": "The Real Title"},
        {"basis": "title_tag", "title": "Another Real Title"},
    ] == data["discovery_titles"]
    assert audio == dom._audio_facts_from_data(data)
    for broken in (
        {key: value for key, value in data.items() if key != "discovery_titles"},
        {**data, "discovery_titles": [{"basis": "path_leaf"}]},
        {**data, "discovery_titles": [{**data["discovery_titles"][0], "x": 1}]},
    ):
        with pytest.raises(ReportValueError):
            dom._audio_facts_from_data(broken)


def test_runtime_observations_keep_the_recorded_discovery_titles() -> None:
    audio = _discovery_audio(_LEAF)
    # Only each candidate's identity and short-runtime mark are read here.
    observation = cast(
        ProviderObservation,
        SimpleNamespace(
            candidates=(
                SimpleNamespace(
                    identity="audible-product-1", runtime_short_of_local=True
                ),
            )
        ),
    )
    observed = app._audio_with_runtime_observations(audio, (observation,))

    assert observed is not None and observed.observations
    assert (_LEAF,) == observed.discovery_titles


@pytest.mark.parametrize(
    "lanes",
    (
        ("alternative", "alternative"),
        ("asserted", "asserted"),
    ),
)
def test_a_plan_holds_one_asserted_and_one_alternative_work_lane_at_most(
    lanes: tuple[str, ...],
) -> None:
    def lane(index: int, kind: str) -> MetadataDiscoveryLane:
        return MetadataDiscoveryLane(
            f"lane_{index:064d}",
            ProviderQueryKind.WORK,
            ("local_" + "1" * 64,),
            title=f"Plain Work {index}",
            authors=("Ada Author",),
            title_basis="path_leaf" if kind == "alternative" else None,
        )

    with pytest.raises(MetadataReportError):
        MetadataDiscoveryPlan(
            (),
            tuple(lane(index, kind) for index, kind in enumerate(lanes)),
            audio=single_track_audio_facts(),
        )


def test_an_alternative_lane_belongs_only_to_an_audiobook_plan() -> None:
    alternative = MetadataDiscoveryLane(
        "lane_" + "2" * 64,
        ProviderQueryKind.WORK,
        (),
        title="The Real Title",
        authors=("Ada Author",),
        title_basis="path_leaf",
    )
    with pytest.raises(MetadataReportError):
        MetadataDiscoveryPlan((), (alternative,))
    assert MetadataDiscoveryPlan(
        (), (alternative,), audio=single_track_audio_facts()
    ).lanes == (alternative,)
    for broken in (
        {"title_basis": "folder"},
        {"kind": ProviderQueryKind.ISBN, "matched_family": "9780306406157"},
    ):
        with pytest.raises(MetadataReportError):
            replace(alternative, **broken)


@pytest.mark.parametrize(
    ("status", "permitted"),
    (
        (ProviderStatus.NO_MATCH, True),
        (ProviderStatus.UNAVAILABLE, False),
        (ProviderStatus.INCOMPLETE, False),
        (ProviderStatus.CANDIDATE_LIMIT_EXCEEDED, False),
        (ProviderStatus.INSUFFICIENT_INPUT, False),
        (ProviderStatus.RATE_LIMITED, False),
        (ProviderStatus.MALFORMED, False),
        (None, False),
    ),
)
def test_only_an_asserted_no_match_permits_the_alternative_lane(
    status: ProviderStatus | None, permitted: bool
) -> None:
    target = _audio_target("Asserted Title", ("Ada Author",))
    plan = audiobook_discovery_plan(
        target,
        (_target_title_claim("Asserted Title"),),
        audio=_discovery_audio(_LEAF),
    )
    asserted = app.provider_discovery_lane_order(plan)[0]
    result = None if status is None else ProviderLaneResult(asserted, status)

    assert permitted is app.alternative_work_lane_permitted(plan, result)
    assert not app.alternative_work_lane_permitted(
        plan,
        ProviderLaneResult(
            asserted,
            ProviderStatus.CANDIDATES,
            (_audiobook_candidate(_audio_local_claims()),),
        ),
    )


@pytest.mark.parametrize("duration", ("58200.000", "51000.000"))
def test_a_fed_asin_naming_another_work_keeps_only_its_identifier(
    duration: str,
) -> None:
    """Exact work admission gates a fed lane before any runtime can (#1257).

    A candidate the item's title and authors refuse keeps no runtime for
    stated-runtime admission, no work statement for a proposal and no
    media-health comparison, whatever its runtime; the admitted title still
    keeps its edition scope.
    """
    local_claims = _feed_claims()
    right = _audiobook_candidate(local_claims, duration=duration)
    wrong = _audiobook_candidate(
        local_claims,
        identity="audnexus-book-B000000011",
        asin="B000000011",
        duration=duration,
        title="Wrong Title",
    )
    qualified = qualify_provider_observation(
        ProviderObservation(
            "catalogue",
            ProviderStatus.CANDIDATES,
            (),
            (right, wrong),
            ProviderQueryKind.ASIN,
            "audnexus",
        ),
        MetadataLookup(
            ProviderQueryKind.ASIN,
            local_claims,
            work_title="Ember Tide Protocol",
            work_authors=("Corin Halloway",),
        ),
    )

    kept, refused = qualified.candidates
    assert ClaimKind.DURATION in {claim.kind for claim in kept.claims}
    assert {ClaimKind.ASIN} == {claim.kind for claim in refused.claims}
    assert () == refused.evidence
    assert refused.runtime_short_of_local is False
