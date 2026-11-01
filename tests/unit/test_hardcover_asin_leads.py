"""Hardcover's counted audio editions lead the phase-two ASIN feed (#962).

When the editions challenge the accepted Audible match, or name a recording
no audiobook authority put forward, their ASINs are looked up like any other
identifier the item's own surfaces did not name. The identifier-lane gate
still decides whether the answer is this recording, and the owner reviews
what it proposes: an edition never fills a value by itself (ADR-0090).
"""

from __future__ import annotations

import pytest

from mechanical_librarian.application.metadata_report import (
    MAX_ASIN_DISCOVERY_LANES,
    audiobook_asin_feed_plan,
    provider_observation,
)
from mechanical_librarian.domain.audiobook_corroboration import (
    HardcoverEditionWitness,
    hardcover_edition_leads,
    hardcover_edition_witness,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    LocalClaim,
    ProviderCandidate,
    ProviderClaim,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    TargetDestination,
)
from mechanical_librarian.domain.proposal_set import ProposalOutcome, evaluate_report
from tests.support.metadata_report_doubles import single_track_audio_facts
from tests.unit.test_carried_recording_admission import (
    _DIGEST,
    _NARRATOR,
    _TITLE,
    _asin_values,
    _candidate,
    _catalogue,
    _claims,
    _lane_result,
    _plan,
    _report,
    _snapshot,
)

# The file is 58,244 s long; the editions and products below are judged
# against it.
_LOCAL = 58_244
_LEAD = "B0LEADREC1"
_ISBN_SHAPED = "1400193346"
_WRONG = "B0WRONGREC"
_WORK = "hardcover-work-0000000010"


def _edition(
    edition_id: int, seconds: int, narrator: str, asin: str | None
) -> ProviderCandidate:
    identity = f"hardcover-edition-{edition_id:010d}"
    values: list[tuple[ClaimKind, str, str | None]] = []
    if asin is not None:
        values.append((ClaimKind.ASIN, asin, None))
    values += [
        (ClaimKind.DURATION, f"{seconds}.000", None),
        (ClaimKind.CONTRIBUTOR, narrator, "narrator"),
    ]
    return ProviderCandidate(
        identity,
        _WORK,
        tuple(
            ProviderClaim.create(
                provider="hardcover",
                candidate=identity,
                kind=kind,
                value=value,
                state=FieldState.VALID,
                scope=ClaimScope.EDITION if role is None else ClaimScope.WORK,
                applicability="not_applicable",
                role=role,
                raw_order=order,
            )
            for order, (kind, value, role) in enumerate(values)
        ),
    )


def _hardcover(*editions: ProviderCandidate) -> ProviderObservation:
    work = ProviderCandidate(
        _WORK,
        _WORK,
        (
            ProviderClaim.create(
                provider="hardcover",
                candidate=_WORK,
                kind=ClaimKind.TITLE,
                value=_TITLE,
                state=FieldState.VALID,
                scope=ClaimScope.WORK,
                applicability="not_applicable",
                raw_order=0,
            ),
        ),
    )
    return ProviderObservation(
        "hardcover",
        ProviderStatus.CANDIDATES,
        candidates=(work,),
        query_kind=ProviderQueryKind.DISCOVERY,
        authority="hardcover",
        audio_editions=tuple(sorted(editions, key=lambda value: value.identity)),
    )


def _wrong_match(claims: tuple[LocalClaim, ...]) -> ProviderObservation:
    """Audible's search admits a lone product: another narrator, minutes longer."""
    return _catalogue(
        claims,
        search=(
            _candidate(
                "audible",
                (_WRONG,),
                narrator="Another Reader",
                duration=f"{_LOCAL + 600}.000",
            ),
        ),
    )


def _leads(*observations: ProviderObservation) -> tuple[str, ...]:
    claims = _claims(carried=None, narrator=None)
    return hardcover_edition_leads(
        claims, single_track_audio_facts(_DIGEST), observations
    )


def test_a_challenged_match_gives_its_edition_asins_as_leads() -> None:
    claims = _claims(carried=None, narrator=None)
    hardcover = _hardcover(
        _edition(3, _LOCAL + 30, _NARRATOR, _LEAD),
        _edition(4, _LOCAL - 20, _NARRATOR, _ISBN_SHAPED),
        _edition(5, _LOCAL, _NARRATOR, None),
    )
    catalogue = _wrong_match(claims)
    witness = hardcover_edition_witness(_report(claims, (catalogue, hardcover)))

    assert HardcoverEditionWitness.CHALLENGED is witness
    assert (_LEAD, _ISBN_SHAPED) == _leads(catalogue, hardcover)


def test_an_unmatched_edition_gives_a_lead() -> None:
    hardcover = _hardcover(_edition(3, _LOCAL, _NARRATOR, _LEAD))
    assert (_LEAD,) == _leads(hardcover)


@pytest.mark.parametrize(
    "editions",
    (
        # Editions that disagree on the cast name no recording.
        (
            _edition(3, _LOCAL, _NARRATOR, _LEAD),
            _edition(4, _LOCAL, "Other Reader", "B0OTHERREC"),
        ),
        # An edition outside the tight band is not counted.
        (_edition(3, _LOCAL + 91, _NARRATOR, _LEAD),),
    ),
)
def test_disagreeing_or_uncounted_editions_give_no_lead(
    editions: tuple[ProviderCandidate, ...],
) -> None:
    assert () == _leads(_hardcover(*editions))


def test_an_agreeing_match_needs_no_lead() -> None:
    claims = _claims(carried=None, narrator=None)
    catalogue = _catalogue(claims, search=(_candidate("audible", (_WRONG,)),))
    hardcover = _hardcover(_edition(3, _LOCAL, _NARRATOR, _LEAD))

    assert HardcoverEditionWitness.AGREES is hardcover_edition_witness(
        _report(claims, (catalogue, hardcover))
    )
    assert () == _leads(catalogue, hardcover)


def test_leads_come_before_the_searched_products_inside_the_cap() -> None:
    claims = _claims(carried=None, narrator=None)
    catalogue = _wrong_match(claims)
    leads = tuple(f"B0LEAD{index:04d}" for index in range(MAX_ASIN_DISCOVERY_LANES))
    hardcover = _hardcover(
        *(
            _edition(index + 1, _LOCAL, _NARRATOR, asin)
            for index, asin in enumerate(leads)
        )
    )

    without = audiobook_asin_feed_plan(_snapshot(), _plan(claims), (catalogue,))
    feed = audiobook_asin_feed_plan(_snapshot(), _plan(claims), (catalogue, hardcover))

    assert without is not None
    assert feed is not None
    # The searched product is still asked about when Hardcover names nothing,
    # and the leads displace it when they fill the cap.
    assert {_WRONG} <= {lane.canonical_asin for lane in without.lanes}
    assert set(leads) == {lane.canonical_asin for lane in feed.lanes}


def _fed_report(answer: ProviderCandidate) -> tuple[set[str], ProposalOutcome]:
    claims = _claims(carried=None, narrator=None)
    catalogue = _wrong_match(claims)
    hardcover = _hardcover(_edition(3, _LOCAL + 30, _NARRATOR, _LEAD))
    plan = _plan(claims)
    feed = audiobook_asin_feed_plan(_snapshot(), plan, (catalogue, hardcover))
    assert feed is not None
    lane = next(value for value in feed.lanes if value.canonical_asin == _LEAD)
    fed = provider_observation(
        "enrichment",
        (_lane_result(feed, lane, "enrichment", "audnexus", (answer,)),),
        authority="audnexus",
    )
    report = _report(claims, (catalogue, fed, hardcover))
    narrators = next(
        value
        for value in evaluate_report(report).results
        if value.destination is TargetDestination.NARRATORS
    )
    return _asin_values(report), narrators.outcome


def test_a_lead_audible_confirms_is_proposed_for_review() -> None:
    asins, narrators = _fed_report(_candidate("enrichment", (_LEAD,)))

    assert _LEAD in asins
    assert _WRONG not in asins
    assert ProposalOutcome.PROPOSE_CHANGE is narrators


def test_a_lead_outside_the_band_proposes_nothing() -> None:
    asins, narrators = _fed_report(
        _candidate("enrichment", (_LEAD,), duration=f"{_LOCAL + 900}.000")
    )

    assert _LEAD not in asins
    assert ProposalOutcome.PROPOSE_CHANGE is not narrators
