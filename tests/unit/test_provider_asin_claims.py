"""Canonical ASIN grammar across report, proposal and packet linkage.

Canonical linkage does not establish recording applicability or grant Automatic
ASIN authority; the report, provenance and qualification guards remain separate.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from mechanical_librarian.application.metadata_report import (
    MetadataLookup,
    qualify_provider_observation,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    MetadataReport,
    ProviderClaim,
    ProviderQueryKind,
    ReportValueError,
    TargetDestination,
    canonical_asin,
)
from mechanical_librarian.domain.proposal_set import (
    _eligible_identifier,
    evaluate_report,
)
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.shadow_review import (
    ShadowReviewPacket,
    ShadowReviewValueError,
)
from tests.support.audiobook_reports import (
    asin_lane_observation,
    asserted_asin_report,
    audiobook_candidate,
)

_CANONICAL = ("B0SYNE7P1K", "0743563123", "9780306406", "0000000000", "ZZZZZZZZZZ")
_REFUSED = (
    "B0SYNE7P1",
    "B0SYNE7P1KK",
    "B0SYNE7P1K!",
    "B0SYNE7P-1K",
    "B0SYNE7P 1",
    "b0syne7p1k",
    "",
)


def _claim(value: str) -> ProviderClaim:
    return ProviderClaim.create(
        provider="audible",
        candidate="audible-product",
        kind=ClaimKind.ASIN,
        value=value,
        state=FieldState.VALID,
        scope=ClaimScope.EDITION,
        applicability="not_applicable",
    )


@pytest.mark.parametrize("value", _CANONICAL)
def test_the_report_layer_accepts_every_canonical_identifier(value: str) -> None:
    assert value == _claim(value).value
    assert value == canonical_asin(value)


@pytest.mark.parametrize("value", _REFUSED)
def test_the_report_layer_still_refuses_a_noncanonical_identifier(value: str) -> None:
    assert canonical_asin(value) != value
    with pytest.raises(ReportValueError, match="invalid"):
        ProviderClaim(
            "provider_" + "0" * 64,
            ClaimKind.ASIN,
            value,
            FieldState.VALID,
            ClaimScope.EDITION,
            "not_applicable",
        )


def test_the_claim_factory_normalises_case_and_the_grammar_stays_shared() -> None:
    """`create` upper-cases before validating, so both spellings are one claim."""
    assert "B0SYNE7P1K" == _claim("b0syne7p1k").value
    assert _claim("b0syne7p1k").reference == _claim("B0SYNE7P1K").reference
    for value in (*_CANONICAL, *_REFUSED, "  B0SYNE7P1K  "):
        report_admits = canonical_asin(value) == value
        try:
            ProviderClaim(
                "provider_" + "0" * 64,
                ClaimKind.ASIN,
                value,
                FieldState.VALID,
                ClaimScope.EDITION,
                "not_applicable",
            )
        except ReportValueError:
            assert not report_admits
        else:
            assert report_admits


def test_the_proposal_path_asks_the_one_canonical_asin_question() -> None:
    """#957: the write destination stopped keeping a second, narrower grammar.

    An ISBN-10-shaped ASIN is a real audiobook identifier -- 17 of the
    reference library's 103 are shaped that way -- so refusing it here refused
    a value the report had already admitted.
    """
    assert _eligible_identifier(TargetDestination.ASIN, "0743563123", "not_applicable")
    for value in (*_CANONICAL, *_REFUSED):
        assert _eligible_identifier(
            TargetDestination.ASIN, value, "not_applicable"
        ) == (canonical_asin(value) is not None)


@pytest.mark.parametrize("value", ("0743563123", "B0SYNE7P1K"))
def test_packet_linkage_accepts_canonical_asin_and_rejects_changed_facts(
    value: str,
) -> None:
    base = asserted_asin_report(provider_duration="58200.000")
    observation = asin_lane_observation(base.local_claims)
    candidate = audiobook_candidate(
        base.local_claims, identity="synthetic-product", asin=value
    )
    date = ProviderClaim.create(
        provider=observation.provider,
        candidate=candidate.identity,
        kind=ClaimKind.PUBLICATION_DATE,
        value="2025-02-03",
        state=FieldState.VALID,
        scope=ClaimScope.EDITION,
        applicability="not_applicable",
        raw_order=5,
    )
    observation = replace(
        observation, candidates=(replace(candidate, claims=(*candidate.claims, date)),)
    )
    report = MetadataReport.create(
        target=base.target,
        file=base.file,
        local_claims=base.local_claims,
        local_reasons=base.local_reasons,
        audio=base.audio,
        providers=(
            qualify_provider_observation(
                observation,
                MetadataLookup(
                    ProviderQueryKind.ASIN,
                    base.local_claims,
                    audio=base.audio,
                    work_title=base.target.current_title,
                    work_authors=("Corin Halloway",),
                    requested_asin=value,
                ),
            ),
        ),
    )
    report = MetadataReport.from_json(report.to_json())
    proposal = evaluate_report(report)
    packet = ShadowReviewPacket.create(
        ReviewCase.create(proposal, report), proposal, report
    )
    assert ShadowReviewPacket.from_json(packet.to_json(), report, proposal) == packet
    citation = next(
        item for item in packet.citations if item["citation_id"] == date.reference
    )
    assert citation["fact"]["linkage"]["identifier_facts"][0]["value"] == value
    for invalid in (
        "b0syne7p1k",
        "074356312",
        "07435631233",
        "074356312!",
        "B000000001",
    ):
        document = json.loads(packet.to_json())
        changed = next(
            item
            for item in document["citations"]
            if item["citation_id"] == date.reference
        )
        changed["fact"]["linkage"]["identifier_facts"][0]["value"] = invalid
        with pytest.raises(ShadowReviewValueError):
            ShadowReviewPacket.from_json(
                json.dumps(document, sort_keys=True, separators=(",", ":")),
                report,
                proposal,
            )
    document = json.loads(packet.to_json())
    changed = next(
        item for item in document["citations"] if item["citation_id"] == date.reference
    )
    changed["fact"]["linkage"]["identifier_facts"][0]["reference"] = (
        "provider_" + "0" * 64
    )
    with pytest.raises(ShadowReviewValueError):
        ShadowReviewPacket.from_json(
            json.dumps(document, sort_keys=True, separators=(",", ":")),
            report,
            proposal,
        )
