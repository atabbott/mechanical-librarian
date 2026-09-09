"""Series applicability through real provider, proposal and preview boundaries."""

import json
from dataclasses import replace

import httpx
import pytest

from mechanical_librarian.adapters.providers.hardcover_metadata import (
    HardcoverMetadataProvider,
)
from mechanical_librarian.application.metadata_report import (
    TargetSnapshot,
    metadata_discovery_plan,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    ProviderClaim,
    ProviderLaneCandidate,
    ProviderQueryKind,
    ProviderStatus,
    ReportValueError,
    TargetDestination,
    TargetField,
    TargetFieldState,
    provider_lane_candidate_reference,
)
from mechanical_librarian.domain.proposal_set import (
    ProposalOutcome,
    evaluate_report,
    provider_candidate_represents_target_series_work,
    provider_candidate_represents_target_work,
)
from tests.support.hardcover_evidence import hardcover_envelope, isbn_claim
from tests.support.proposal_evidence import _fields, _report, _result
from tests.unit.test_rich_metadata_preview import (
    _automatic_series_preview,
    _local_series_claims,
    _preview_field,
)


def _isbn_series_report(
    identity="matching",
    provider="catalogue",
    *,
    target_title="Synthetic work",
    provider_title="Synthetic work",
    target_author="Target Author",
    provider_author="Target Author",
    edition_title=None,
    edition_id=1,
    work_id=10,
):
    fields = _fields(
        target_title,
        authors=TargetField(TargetDestination.AUTHORS, TargetFieldState.MISSING)
        if identity == "missing_target_author"
        else TargetField(
            TargetDestination.AUTHORS,
            TargetFieldState.VALUE,
            (target_author,) if type(target_author) is str else target_author,
        ),
        series=TargetField(TargetDestination.SERIES, TargetFieldState.MISSING),
    )
    local_claims = (isbn_claim(), *_local_series_claims())
    plan = metadata_discovery_plan(
        TargetSnapshot("ebook", target_title, (), fields=fields), local_claims
    )
    payload = hardcover_envelope(
        [
            {
                "id": edition_id,
                "book_id": work_id,
                "isbn_13": "9780306406157",
                "title": edition_title,
            }
        ],
        [
            {
                "id": work_id,
                "title": None if identity == "missing_title" else provider_title,
                "contributions": []
                if identity == "missing_author"
                else [
                    {
                        "id": index + 1,
                        "contribution": "Author",
                        "author": {
                            "id": index + 1,
                            "name": author
                            if identity != "different_author"
                            else "Different Author",
                        },
                    }
                    for index, author in enumerate(
                        (provider_author,)
                        if type(provider_author) is str
                        else provider_author
                    )
                ],
                "book_series": [
                    {
                        "id": 1,
                        "position": "8",
                        "series": {"id": 1, "name": "Unrelated umbrella"},
                    },
                    {
                        "id": 2,
                        "position": "2",
                        "series": {"id": 2, "name": "Reading Order"},
                    },
                ],
            }
        ],
    )
    operations = []

    def handler(request):
        operations.append(json.loads(request.content)["operationName"])
        return httpx.Response(
            200, headers={"content-type": "application/json"}, json=payload
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        observation = (
            HardcoverMetadataProvider(provider, client, 65_536)
            .observe(plan)
            .observation
        )
    assert operations == ["MetadataEvidenceByIsbn"]
    assert observation.status is ProviderStatus.CANDIDATES
    assert len(observation.candidates) == 1
    assert tuple(lane.kind for lane in observation.lanes) == (ProviderQueryKind.ISBN,)
    return _report(fields=fields, providers=(observation,), local_claims=local_claims)


@pytest.mark.parametrize(
    "option", ("primary_narrative", "source_order", "all_qualified")
)
@pytest.mark.parametrize(
    "identity",
    (
        "matching",
        "different_author",
        "missing_author",
        "missing_title",
        "missing_target_author",
    ),
)
def test_only_target_applicable_series_survives_without_changing_ask(option, identity):
    report = _isbn_series_report(identity)
    source = report.providers[0]
    applicable = identity == "matching"
    assert (
        provider_candidate_represents_target_work(report, source, source.candidates[0])
        is applicable
    )
    proposal = _result(evaluate_report(report), TargetDestination.SERIES)
    preview = _automatic_series_preview(report, option)
    ask = _preview_field(report, "series", option)
    # The safety check removes automatic authority; it never filters/re-elects
    # the proposal or changes the still-reviewable Ask projection.
    assert proposal.outcome is ProposalOutcome.PROPOSE_CHANGE
    assert len(proposal.alternatives) == 1
    assert ask.outcome == "projected"
    assert ask.desired_value == ({"name": "Unrelated umbrella", "sequence": "8"},)
    assert set(ask.source_evidence_references) == {
        claim.reference
        for claim in source.candidates[0].claims
        if claim.kind in {ClaimKind.SERIES, ClaimKind.SERIES_SEQUENCE}
    }
    if applicable:
        assert preview == ask
    else:
        assert (preview.outcome, preview.reason, preview.desired_value) == (
            "abstain",
            "target_work_applicability_unproved",
            None,
        )
        assert preview.source_candidate_id is None
        assert preview.source_evidence_references == ()
        assert preview.source_combination_ids == ()
        assert preview.qualified_value is None


@pytest.mark.parametrize(
    "option", ("primary_narrative", "source_order", "all_qualified")
)
def test_unselected_inapplicable_subset_does_not_veto_selected_bundle(option):
    selected = _isbn_series_report()
    unrelated = _isbn_series_report("different_author", "unrelated").providers[0]
    candidate = unrelated.candidates[0]
    # Remove the outer membership, preserving the candidate's actual identity
    # and successful lane. Its remaining subset cannot supply the elected bundle.
    unrelated = replace(
        unrelated,
        candidates=(
            replace(
                candidate,
                claims=tuple(
                    claim
                    for claim in candidate.claims
                    if claim.value not in {"Unrelated umbrella", "8"}
                ),
            ),
        ),
    )
    report = _report(
        fields=selected.target.fields,
        local_claims=selected.local_claims,
        providers=(*selected.providers, unrelated),
    )
    assert not provider_candidate_represents_target_work(
        report, unrelated, unrelated.candidates[0]
    )
    assert _automatic_series_preview(report, option) == _automatic_series_preview(
        selected, option
    )


@pytest.mark.parametrize(
    "option", ("primary_narrative", "source_order", "all_qualified")
)
@pytest.mark.parametrize(
    "identity_fields",
    (
        pytest.param({"target_author": "Author, Target"}, id="comma-inverted-author"),
        pytest.param(
            {"target_title": "Synthetic work (Reading Order)"},
            id="selected-source-attested-series-decoration",
        ),
        pytest.param(
            {
                "target_title": "Synthetic work (Reading Order)",
                "target_author": "Author, Target",
            },
            id="attested-decoration-and-comma-inversion",
        ),
        pytest.param(
            {
                "target_title": "Synthetic work: The complete journey",
                "edition_title": "Synthetic work: The complete journey",
            },
            id="exact-isbn-edition-title",
        ),
        pytest.param(
            {
                "target_title": "Synthetic work (Reading Order)",
                "target_author": ("Target Author", "Second Author"),
                "provider_author": ("Target Author", "Second Author"),
            },
            id="complete-ordered-authors",
        ),
    ),
)
def test_isbn_series_accepts_source_attested_identity_formatting(
    option, identity_fields
):
    report = _isbn_series_report(**identity_fields)
    field = _automatic_series_preview(report, option)
    assert field.outcome == "projected", field.reason
    assert field == _preview_field(report, "series", option)


@pytest.mark.parametrize(
    "option", ("primary_narrative", "source_order", "all_qualified")
)
@pytest.mark.parametrize(
    "identity_fields",
    (
        pytest.param(
            {
                "target_title": "Synthetic work (Reading Order)",
                "provider_author": "Other Author",
            },
            id="different-author-attested-decoration",
        ),
        pytest.param(
            {"provider_title": "Synthetic work and Other stories"}, id="contained-work"
        ),
        pytest.param({"provider_title": "Synthetic work II"}, id="same-author-sequel"),
        pytest.param(
            {"target_title": "Synthetic work: An anthology"}, id="anthology-prefix"
        ),
        pytest.param(
            {"target_title": "Synthetic work: The complete journey"},
            id="unattested-colon-subtitle",
        ),
        pytest.param(
            {"target_title": "Synthetic work (Different Order)"},
            id="unattested-parenthesized-series",
        ),
        pytest.param(
            {"target_title": "Synthetic work (Reading Order) (Reading Order)"},
            id="stacked-decoration",
        ),
        pytest.param(
            {"target_title": "Synthetic work (2)"}, id="bare-position-not-series-name"
        ),
        pytest.param({"target_author": "Author, T."}, id="initial-is-not-full-name"),
        pytest.param(
            {"target_author": "Author, Target, Jr."}, id="ambiguous-inversion"
        ),
        pytest.param(
            {
                "target_title": "Synthetic work: The complete journey",
                "edition_title": "Synthetic work: The complete journey",
                "provider_author": "Other Author",
            },
            id="exact-edition-title-different-work-author",
        ),
        pytest.param(
            {
                "target_author": "Author, Target",
                "edition_title": "Synthetic work",
                "provider_title": "Synthetic work (Abridged)",
            },
            id="exact-edition-title-does-not-bypass-work-variant",
        ),
        pytest.param(
            {
                "target_title": "Synthetic work (Reading Order)",
                "target_author": ("Target Author", "Target Author"),
                "provider_author": ("Target Author", "Second Author"),
            },
            id="duplicate-complete-target-author-set",
        ),
        pytest.param(
            {
                "target_title": "Synthetic work (Reading Order)",
                "target_author": ("Second Author", "Target Author"),
                "provider_author": ("Target Author", "Second Author"),
            },
            id="reordered-complete-author-set",
        ),
        pytest.param(
            {
                "target_title": "Synthetic work (Reading Order)",
                "target_author": ("Author, Target", "Second Author"),
                "provider_author": ("Target Author", "Second Author"),
            },
            id="comma-inversion-does-not-expand-to-multiple-authors",
        ),
    ),
)
def test_isbn_series_identity_formatting_does_not_admit_unrelated_work(
    option, identity_fields
):
    report = _isbn_series_report(**identity_fields)
    assert _preview_field(report, "series", option).outcome == "projected"
    field = _automatic_series_preview(report, option)
    assert (field.outcome, field.reason, field.desired_value) == (
        "abstain",
        "target_work_applicability_unproved",
        None,
    )


@pytest.mark.parametrize(
    "option", ("primary_narrative", "source_order", "all_qualified")
)
@pytest.mark.parametrize(
    "fault",
    (
        "missing-lane",
        "failed-lane",
        "fabricated-lane-identity",
        "wrong-input-reference",
        "wrong-lane-work",
        "wrong-lane-edition",
        "missing-isbn",
        "conflicting-isbn",
        "missing-title",
        "malformed-title",
        "ambiguous-title",
        "missing-author",
        "variant-title",
        "variant-subtitle",
        "unresolved-work",
    ),
)
def test_series_identity_recovery_requires_complete_bound_isbn_lane(option, fault):
    report = _isbn_series_report(target_author="Author, Target")
    provider = report.providers[0]
    source = provider.candidates[0]
    lane = provider.lanes[0]
    assert not provider_candidate_represents_target_work(report, provider, source)
    assert provider_candidate_represents_target_series_work(report, provider, source)
    if fault == "missing-lane":
        provider = replace(provider, lanes=())
    elif fault == "failed-lane":
        provider = replace(
            provider,
            lanes=(replace(lane, status=ProviderStatus.UNAVAILABLE, candidates=()),),
        )
    elif fault == "fabricated-lane-identity":
        lane = replace(lane, identity="lane_" + "a" * 64)
        member = lane.candidates[0]
        lane = replace(
            lane,
            candidates=(
                replace(
                    member,
                    reference=provider_lane_candidate_reference(
                        provider.provider,
                        lane.identity,
                        member.edition_identity,
                        member.work_identity,
                    ),
                ),
            ),
        )
        provider = replace(provider, lanes=(lane,))
    elif fault == "wrong-input-reference":
        provider = replace(
            provider,
            input_references=(report.local_claims[-1].reference,),
            lanes=(
                replace(lane, input_references=(report.local_claims[-1].reference,)),
            ),
        )
    elif fault in {"wrong-lane-work", "wrong-lane-edition"}:
        member = lane.candidates[0]
        member = replace(
            member,
            **{
                "work_identity"
                if fault == "wrong-lane-work"
                else "edition_identity": "other"
            },
        )
        # The immutable observation rejects cross-edition/work membership
        # before an evaluator can see it; do not forge a report past that floor.
        with pytest.raises(ReportValueError, match="provider lane candidate"):
            replace(provider, lanes=(replace(lane, candidates=(member,)),))
        return
    else:
        claims = list(source.claims)
        if fault in {"missing-isbn", "missing-title", "missing-author"}:
            kind = {
                "missing-isbn": ClaimKind.ISBN,
                "missing-title": ClaimKind.TITLE,
                "missing-author": ClaimKind.CREATOR,
            }[fault]
            claims = [claim for claim in claims if claim.kind is not kind]
        elif fault == "conflicting-isbn":
            original = next(
                claim
                for claim in claims
                if claim.kind is ClaimKind.ISBN and claim.state is FieldState.VALID
            )
            claims.append(
                replace(original, value="9780140328721", raw_order=len(claims))
            )
        elif fault in {"malformed-title", "ambiguous-title", "variant-title"}:
            original = next(
                claim
                for claim in claims
                if claim.kind is ClaimKind.TITLE and claim.scope is ClaimScope.WORK
            )
            if fault == "ambiguous-title":
                claims.append(
                    replace(original, value="Another work", raw_order=len(claims))
                )
            else:
                claims[claims.index(original)] = replace(
                    original,
                    value=None
                    if fault == "malformed-title"
                    else "Synthetic work (Abridged)",
                    state=FieldState.MALFORMED
                    if fault == "malformed-title"
                    else FieldState.VALID,
                )
        elif fault == "variant-subtitle":
            original = next(
                claim for claim in claims if claim.kind is ClaimKind.SUBTITLE
            )
            claims[claims.index(original)] = replace(
                original, value="An adaptation", state=FieldState.VALID
            )
        # Reissue changed synthetic claims through the real reference factory.
        claims = [
            ProviderClaim.create(
                provider=provider.provider,
                candidate=source.identity,
                kind=claim.kind,
                value=claim.value,
                state=claim.state,
                scope=claim.scope,
                applicability=claim.applicability,
                role=claim.role,
                raw_order=claim.raw_order,
                links=claim.links,
                date_event=claim.date_event,
                date_precision=claim.date_precision,
                date_lexical=claim.date_lexical,
                date_confidence=claim.date_confidence,
            )
            for claim in claims
        ]
        source = replace(
            source,
            claims=tuple(claims),
            identity_bindings=()
            if fault == "missing-author"
            else source.identity_bindings,
            work_identity="unresolved"
            if fault == "unresolved-work"
            else source.work_identity,
            evidence=() if fault == "unresolved-work" else source.evidence,
        )
        if fault == "unresolved-work":
            member = lane.candidates[0]
            lane = replace(
                lane,
                candidates=(
                    replace(
                        member,
                        work_identity="unresolved",
                        reference=provider_lane_candidate_reference(
                            provider.provider,
                            lane.identity,
                            member.edition_identity,
                            "unresolved",
                        ),
                    ),
                ),
            )
        provider = replace(provider, candidates=(source,), lanes=(lane,))
    report = _report(
        fields=report.target.fields,
        local_claims=report.local_claims,
        providers=(provider,),
    )
    assert not provider_candidate_represents_target_series_work(
        report, provider, source
    )
    assert _preview_field(report, "series", option).outcome == "projected"
    assert _automatic_series_preview(report, option).outcome == "abstain"


@pytest.mark.parametrize(
    "option", ("primary_narrative", "source_order", "all_qualified")
)
@pytest.mark.parametrize(
    "sibling_shape", ("matching", "other-work", "other-author", "no-isbn", "unselected")
)
def test_series_identity_recovery_proves_whole_selected_lane(option, sibling_shape):
    report = _isbn_series_report(target_author="Author, Target")
    provider = report.providers[0]
    source = provider.candidates[0]
    sibling = (
        _isbn_series_report(
            edition_id=2,
            work_id=20 if sibling_shape == "other-work" else 10,
            provider_author="Other Author"
            if sibling_shape == "other-author"
            else "Target Author",
        )
        .providers[0]
        .candidates[0]
    )
    if sibling_shape == "no-isbn":
        sibling = replace(
            sibling,
            claims=tuple(
                claim for claim in sibling.claims if claim.kind is not ClaimKind.ISBN
            ),
        )
    lane = provider.lanes[0]
    member = ProviderLaneCandidate(
        provider_lane_candidate_reference(
            provider.provider, lane.identity, sibling.identity, sibling.work_identity
        ),
        sibling.identity,
        sibling.work_identity,
    )
    if sibling_shape == "unselected":
        # This retained candidate supplies no membership to the selected source
        # bundle and does not belong to its successful lane.
        sibling = replace(
            sibling,
            claims=tuple(
                claim
                for claim in sibling.claims
                if claim.kind not in {ClaimKind.SERIES, ClaimKind.SERIES_SEQUENCE}
            ),
        )
    else:
        lane = replace(
            lane,
            candidates=tuple(
                sorted((*lane.candidates, member), key=lambda value: value.reference)
            ),
        )
    provider = replace(provider, candidates=(source, sibling), lanes=(lane,))
    report = _report(
        fields=report.target.fields,
        local_claims=report.local_claims,
        providers=(provider,),
    )
    applicable = sibling_shape in {"matching", "unselected"}
    assert (
        provider_candidate_represents_target_series_work(report, provider, source)
        is applicable
    )
    field = _automatic_series_preview(report, option)
    ask = _preview_field(report, "series", option)
    assert ask.outcome == "projected"
    if applicable:
        assert field == ask
    else:
        assert field.outcome == "abstain"


def test_title_decoration_cannot_borrow_another_candidates_attestation():
    report = _isbn_series_report(target_title="Synthetic work (Reading Order)")
    provider = report.providers[0]
    source = provider.candidates[0]
    sibling = _isbn_series_report(edition_id=2).providers[0].candidates[0]
    # The real adapter's sibling retains Reading Order, but this source does not.
    source = replace(
        source,
        claims=tuple(
            claim
            for claim in source.claims
            if claim.value not in {"Reading Order", "2"}
        ),
    )
    provider = replace(provider, candidates=(source, sibling))
    report = _report(
        fields=report.target.fields,
        local_claims=report.local_claims,
        providers=(provider,),
    )
    assert not provider_candidate_represents_target_series_work(
        report, provider, source
    )


def test_series_identity_recovery_checks_exact_lane_without_rebuilding_provider():
    report = _isbn_series_report(target_author="Author, Target")
    provider = report.providers[0]
    source = provider.candidates[0]
    valid = provider.lanes[0]
    wrong_identity = "lane_" + "a" * 64
    wrong = replace(
        valid,
        identity=wrong_identity,
        candidates=(
            ProviderLaneCandidate(
                provider_lane_candidate_reference(
                    provider.provider,
                    wrong_identity,
                    source.identity,
                    source.work_identity,
                ),
                source.identity,
                source.work_identity,
            ),
        ),
    )
    provider = replace(
        provider,
        query_kind=ProviderQueryKind.DISCOVERY,
        lanes=tuple(sorted((valid, wrong), key=lambda lane: lane.identity)),
    )
    report = _report(
        fields=report.target.fields,
        local_claims=report.local_claims,
        providers=(provider,),
    )
    assert provider_candidate_represents_target_series_work(report, provider, source)
    # The unrelated successful lane cannot borrow the real lane's target inputs.
    provider = replace(
        provider,
        convergence=(),
        lanes=tuple(
            sorted(
                (
                    replace(valid, status=ProviderStatus.UNAVAILABLE, candidates=()),
                    wrong,
                ),
                key=lambda lane: lane.identity,
            )
        ),
    )
    assert not provider_candidate_represents_target_series_work(
        report, provider, source
    )
