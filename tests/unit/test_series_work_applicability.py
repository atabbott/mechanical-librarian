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
    ProviderQueryKind,
    ProviderStatus,
    TargetDestination,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import (
    ProposalOutcome,
    evaluate_report,
    provider_candidate_represents_target_work,
)
from tests.support.hardcover_evidence import hardcover_envelope, isbn_claim
from tests.support.proposal_evidence import _fields, _report, _result
from tests.unit.test_rich_metadata_preview import (
    _automatic_series_preview,
    _local_series_claims,
    _preview_field,
)


def _isbn_series_report(identity="matching", provider="catalogue"):
    fields = _fields(
        "Synthetic work",
        authors=TargetField(TargetDestination.AUTHORS, TargetFieldState.MISSING)
        if identity == "missing_target_author"
        else TargetField(
            TargetDestination.AUTHORS, TargetFieldState.VALUE, ("Target Author",)
        ),
        series=TargetField(TargetDestination.SERIES, TargetFieldState.MISSING),
    )
    local_claims = (isbn_claim(), *_local_series_claims())
    plan = metadata_discovery_plan(
        TargetSnapshot("ebook", "Synthetic work", (), fields=fields), local_claims
    )
    payload = hardcover_envelope(
        [{"id": 1, "book_id": 10, "isbn_13": "9780306406157"}],
        [
            {
                "id": 10,
                "title": None if identity == "missing_title" else "Synthetic work",
                "contributions": []
                if identity == "missing_author"
                else [
                    {
                        "id": 1,
                        "contribution": "Author",
                        "author": {
                            "id": 1,
                            "name": "Target Author"
                            if identity != "different_author"
                            else "Different Author",
                        },
                    }
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
        observation = HardcoverMetadataProvider(provider, client, 65_536).observe(plan)
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
