"""Hardcover's counted audio editions judge the accepted recording (#962).

An edition counts when it credits a narrator and states a runtime inside the
tight band of the file's. A match is challenged only when it is outside the
band and credits only narrators the counted editions do not: the same
narrator at a nearby runtime is the same recording (ADR-0090).
"""

from __future__ import annotations

import pytest

from mechanical_librarian.domain.audiobook_corroboration import (
    HardcoverEditionWitness,
    counted_audio_editions,
    hardcover_edition_witness,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    ReportValueError,
    TargetFacts,
    claim_scope,
    default_target_fields,
)
from tests.support.metadata_report_doubles import single_track_audio_facts

_LOCAL = 37_560
_WORK = "hardcover-work-0000000010"


def _claims(
    provider: str,
    identity: str,
    values: tuple[tuple[ClaimKind, str, str | None], ...],
) -> tuple[ProviderClaim, ...]:
    return tuple(
        ProviderClaim.create(
            provider=provider,
            candidate=identity,
            kind=kind,
            value=value,
            state=FieldState.VALID,
            scope=claim_scope(kind),
            applicability="not_applicable",
            role=role,
            raw_order=order,
        )
        for order, (kind, value, role) in enumerate(values)
    )


def _edition(edition_id: int, seconds: int, *narrators: str) -> ProviderCandidate:
    identity = f"hardcover-edition-{edition_id:010d}"
    return ProviderCandidate(
        identity,
        _WORK,
        _claims(
            "catalogue",
            identity,
            (
                (ClaimKind.DURATION, f"{seconds}.000", None),
                *((ClaimKind.CONTRIBUTOR, name, "narrator") for name in narrators),
            ),
        ),
    )


def _product(minutes: int | None, *narrators: str) -> ProviderCandidate:
    identity = "audible-product-B0PRODUCT1"
    runtime = (
        () if minutes is None else ((ClaimKind.DURATION, f"{minutes * 60}.000", None),)
    )
    return ProviderCandidate(
        identity,
        "unresolved",
        _claims(
            "audible",
            identity,
            (
                *runtime,
                *((ClaimKind.CONTRIBUTOR, name, "narrator") for name in narrators),
            ),
        ),
    )


def _report(
    editions: tuple[ProviderCandidate, ...],
    product: ProviderCandidate | None = None,
    *,
    cover_listing: bool = False,
) -> MetadataReport:
    duration = f"{_LOCAL}.000"
    work = ProviderCandidate(
        _WORK, _WORK, _claims("catalogue", _WORK, ((ClaimKind.TITLE, "Run", None),))
    )
    providers = [
        ProviderObservation(
            "catalogue",
            ProviderStatus.CANDIDATES,
            candidates=(work,),
            query_kind=ProviderQueryKind.DISCOVERY,
            authority="hardcover",
            audio_editions=tuple(sorted(editions, key=lambda e: e.identity)),
        )
    ]
    if product is not None:
        providers.append(
            ProviderObservation(
                "audible",
                ProviderStatus.CANDIDATES,
                candidates=(product,),
                query_kind=ProviderQueryKind.DISCOVERY,
                authority="audible_catalog",
            )
        )
    if cover_listing:
        identity = "itunes-collection-0000000001"
        providers.append(
            ProviderObservation(
                "itunes",
                ProviderStatus.CANDIDATES,
                candidates=(
                    ProviderCandidate(
                        identity,
                        "unresolved",
                        _claims("itunes", identity, ((ClaimKind.TITLE, "Run", None),)),
                    ),
                ),
                query_kind=ProviderQueryKind.DISCOVERY,
                authority="itunes_search",
            )
        )
    return MetadataReport.create(
        target=TargetFacts(
            "primary", "audiobooks", "item-1", default_target_fields("Run")
        ),
        file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
        local_claims=(
            LocalClaim.create(
                kind=ClaimKind.DURATION,
                observed=duration,
                canonical=duration,
                excerpt=duration,
                applicability="not_applicable",
                basis="embedded_tag",
                checksum="not_applicable",
                applicability_basis="not_applicable",
                state=FieldState.VALID,
                occurrence=1,
            ),
        ),
        local_reasons=("no_local_audio_claims",),
        providers=tuple(sorted(providers, key=lambda value: value.provider)),
        audio=single_track_audio_facts(),
    )


def test_a_different_narrator_far_outside_the_band_is_challenged() -> None:
    # The Martian shape: the matched product is a re-recording, minutes longer.
    report = _report(
        (_edition(3, _LOCAL + 34, "R. C. Bray"),),
        _product(_LOCAL // 60 + 6, "Wil Wheaton"),
    )
    assert ("hardcover-edition-0000000003",) == tuple(
        edition.identity for edition in counted_audio_editions(report)
    )
    assert HardcoverEditionWitness.CHALLENGED is hardcover_edition_witness(report)


def test_the_same_narrator_outside_the_band_is_the_same_recording() -> None:
    report = _report(
        (_edition(3, _LOCAL + 20, "Scott Brick"),),
        _product(_LOCAL // 60 + 4, "Scott Brick"),
    )
    assert HardcoverEditionWitness.AGREES is hardcover_edition_witness(report)


def test_a_match_inside_the_band_after_its_minute_floor_agrees() -> None:
    # 625 minutes is 37,500 s; its floor reaches 37,559 s, inside 90 s.
    report = _report(
        (_edition(3, _LOCAL, "Imra Vossel"),),
        _product(625, "Someone Else"),
    )
    assert HardcoverEditionWitness.AGREES is hardcover_edition_witness(report)


def test_a_match_with_no_narrator_is_never_challenged() -> None:
    report = _report((_edition(3, _LOCAL, "Imra Vossel"),), _product(700))
    assert HardcoverEditionWitness.AGREES is hardcover_edition_witness(report)


def test_a_match_with_no_runtime_is_never_challenged() -> None:
    report = _report((_edition(3, _LOCAL, "Imra Vossel"),), _product(None, "Other"))
    assert HardcoverEditionWitness.AGREES is hardcover_edition_witness(report)


def test_editions_that_disagree_on_narrator_witness_nothing() -> None:
    report = _report(
        (_edition(3, _LOCAL, "Imra Vossel"), _edition(4, _LOCAL - 30, "Other Reader")),
        _product(700, "Third Reader"),
    )
    assert HardcoverEditionWitness.EDITIONS_DISAGREE is hardcover_edition_witness(
        report
    )


def test_no_accepted_recording_leaves_the_edition_unmatched() -> None:
    report = _report((_edition(3, _LOCAL, "Imra Vossel"),))
    assert HardcoverEditionWitness.UNMATCHED is hardcover_edition_witness(report)


def test_a_cover_only_listing_is_no_accepted_recording() -> None:
    # iTunes is asked for a cover only (#1779): its listing states no runtime,
    # which must not read as a recording that agrees with the edition.
    report = _report((_edition(3, _LOCAL, "Imra Vossel"),), cover_listing=True)
    assert HardcoverEditionWitness.UNMATCHED is hardcover_edition_witness(report)
    challenged = _report(
        (_edition(3, _LOCAL + 34, "R. C. Bray"),),
        _product(_LOCAL // 60 + 6, "Wil Wheaton"),
        cover_listing=True,
    )
    assert HardcoverEditionWitness.CHALLENGED is hardcover_edition_witness(challenged)


@pytest.mark.parametrize("seconds", (_LOCAL + 91, _LOCAL - 91))
def test_an_edition_outside_the_tight_band_is_not_counted(seconds: int) -> None:
    report = _report((_edition(3, seconds, "R. C. Bray"),), _product(700, "Other"))
    assert () == counted_audio_editions(report)
    assert HardcoverEditionWitness.NO_EDITION is hardcover_edition_witness(report)


def test_an_edition_must_belong_to_an_admitted_hardcover_work() -> None:
    stray = ProviderCandidate(
        "hardcover-edition-0000000003",
        "hardcover-work-0000000099",
        _edition(3, _LOCAL, "Imra Vossel").claims,
    )
    with pytest.raises(ReportValueError, match="audio edition is invalid"):
        _report((stray,))


def test_an_edition_states_only_its_asin_runtime_and_narrators() -> None:
    identity = "hardcover-edition-0000000003"
    with pytest.raises(ReportValueError, match="audio edition is invalid"):
        _report(
            (
                ProviderCandidate(
                    identity,
                    _WORK,
                    _claims(
                        "catalogue",
                        identity,
                        (
                            (ClaimKind.DURATION, f"{_LOCAL}.000", None),
                            (ClaimKind.TITLE, "Run", None),
                            (ClaimKind.CONTRIBUTOR, "Imra Vossel", "narrator"),
                        ),
                    ),
                ),
            )
        )


def test_audio_editions_round_trip_through_the_report() -> None:
    report = _report(
        (_edition(3, _LOCAL + 34, "R. C. Bray"),), _product(700, "Wil Wheaton")
    )
    assert report == MetadataReport.from_json(report.to_json())


# --- Editions Audible cannot confirm (#1642) ----------------------------------
#
# An edition is independent of Audible only when Audible was asked for each of
# its ASINs and found nothing, or when it states none (ADR-0090 decision 2,
# narrowed). Built on the lead feed, so each lane is the one production asks.


def _confirmation_report(
    editions: tuple[ProviderCandidate, ...],
    answers: tuple[tuple[str, str, ProviderStatus], ...] = (),
    *,
    narrator: str | None = None,
) -> MetadataReport:
    """The editions, plus each (ASIN, authority, status) lead answer."""
    from mechanical_librarian.application.metadata_report import (
        audiobook_asin_feed_plan,
        provider_observation,
    )
    from tests.unit.test_carried_recording_admission import (
        _claims as _lead_claims,
    )
    from tests.unit.test_carried_recording_admission import (
        _lane_result,
        _plan,
        _snapshot,
    )
    from tests.unit.test_carried_recording_admission import (
        _report as _lead_report,
    )
    from tests.unit.test_hardcover_asin_leads import _hardcover

    claims = _lead_claims(carried=None, narrator=narrator)
    hardcover = _hardcover(*editions)
    feed = audiobook_asin_feed_plan(_snapshot(), _plan(claims), (hardcover,))
    observations: list[ProviderObservation] = [hardcover]
    for authority in sorted({authority for _asin, authority, _status in answers}):
        assert feed is not None
        results = tuple(
            _lane_result(feed, lane, authority, authority, status=status)
            for asin, lane_authority, status in answers
            if lane_authority == authority
            for lane in feed.lanes
            if lane.canonical_asin == asin
        )
        observations.append(
            provider_observation(authority, results, authority=authority)
        )
    return _lead_report(claims, tuple(observations))


def _unconfirmed(report: MetadataReport) -> tuple[str, ...]:
    from mechanical_librarian.domain.audiobook_corroboration import (
        unconfirmed_audio_editions,
    )

    return tuple(edition.identity for _, edition in unconfirmed_audio_editions(report))


def _lead_edition(edition_id: int, asin: str | None, narrator: str = "Ilsa Brandt"):
    from tests.unit.test_hardcover_asin_leads import _LOCAL as _LEAD_LOCAL
    from tests.unit.test_hardcover_asin_leads import _edition as _lead

    return _lead(edition_id, _LEAD_LOCAL, narrator, asin)


def test_an_edition_with_no_asin_is_unconfirmed() -> None:
    report = _confirmation_report((_lead_edition(3, None),))
    assert ("hardcover-edition-0000000003",) == _unconfirmed(report)


@pytest.mark.parametrize("authority", ("audible_catalog", "audnexus", "audiosilo"))
def test_an_asin_audible_could_not_find_is_unconfirmed(authority: str) -> None:
    report = _confirmation_report(
        (_lead_edition(3, "B0LEADREC1"),),
        (("B0LEADREC1", authority, ProviderStatus.NO_MATCH),),
    )
    assert ("hardcover-edition-0000000003",) == _unconfirmed(report)


@pytest.mark.parametrize(
    "answers",
    (
        # An ASIN never asked of Audible.
        (),
        # Asked only of iTunes, which lists no Audible product.
        (("B0LEADREC1", "itunes_search", ProviderStatus.NO_MATCH),),
        # A lane that failed says nothing about the ASIN.
        (("B0LEADREC1", "audnexus", ProviderStatus.UNAVAILABLE),),
        # Every lane that asked must find nothing.
        (
            ("B0LEADREC1", "audnexus", ProviderStatus.NO_MATCH),
            ("B0LEADREC1", "audible_catalog", ProviderStatus.RATE_LIMITED),
        ),
    ),
    ids=("never_asked", "not_audible", "failed", "one_lane_failed"),
)
def test_an_asin_audible_did_not_answer_is_not_unconfirmed(answers) -> None:
    report = _confirmation_report((_lead_edition(3, "B0LEADREC1"),), answers)
    assert () == _unconfirmed(report)


@pytest.mark.parametrize("admitted", (False, True), ids=("outside_band", "admitted"))
def test_an_asin_audible_answers_with_a_product_is_not_unconfirmed(
    admitted: bool,
) -> None:
    from mechanical_librarian.application.metadata_report import (
        audiobook_asin_feed_plan,
        provider_observation,
    )
    from mechanical_librarian.domain.audiobook_corroboration import (
        admitted_audiobook_identity,
    )
    from tests.unit.test_carried_recording_admission import (
        _candidate,
        _lane_result,
        _plan,
        _snapshot,
    )
    from tests.unit.test_carried_recording_admission import _claims as _lead_claims
    from tests.unit.test_carried_recording_admission import _report as _lead_report
    from tests.unit.test_hardcover_asin_leads import _LOCAL as _LEAD_LOCAL
    from tests.unit.test_hardcover_asin_leads import _hardcover

    claims = _lead_claims(carried=None, narrator=None)
    hardcover = _hardcover(_lead_edition(3, "B0LEADREC1"), _lead_edition(5, None))
    feed = audiobook_asin_feed_plan(_snapshot(), _plan(claims), (hardcover,))
    assert feed is not None
    lane = next(value for value in feed.lanes if value.canonical_asin == "B0LEADREC1")
    # Audnexus knows the ASIN. Outside the band it admits no recording, and the
    # edition is still Audible's word; inside it, the recording is admitted
    # and no edition is offered at all.
    answer = _candidate(
        "audnexus",
        ("B0LEADREC1",),
        **({} if admitted else {"duration": f"{_LEAD_LOCAL + 900}.000"}),
    )
    fed = provider_observation(
        "audnexus",
        (_lane_result(feed, lane, "audnexus", "audnexus", (answer,)),),
        authority="audnexus",
    )
    report = _lead_report(claims, (fed, hardcover))
    assert admitted_audiobook_identity(report) is admitted
    assert _unconfirmed(report) == (
        () if admitted else ("hardcover-edition-0000000005",)
    )


def test_editions_that_disagree_leave_nothing_unconfirmed() -> None:
    report = _confirmation_report(
        (_lead_edition(3, None), _lead_edition(4, None, "Other Reader"))
    )
    assert HardcoverEditionWitness.EDITIONS_DISAGREE is hardcover_edition_witness(
        report
    )
    assert () == _unconfirmed(report)


@pytest.mark.parametrize(
    ("narrator", "kept"),
    (("Ilsa Brandt", True), ("Someone Else", False), (None, True)),
    ids=("same_cast", "disjoint_cast", "file_names_none"),
)
def test_an_edition_must_share_the_files_own_cast(
    narrator: str | None, kept: bool
) -> None:
    report = _confirmation_report((_lead_edition(3, None),), narrator=narrator)
    assert bool(_unconfirmed(report)) is kept


def test_unconfirmed_editions_survive_the_report_round_trip() -> None:
    report = _confirmation_report(
        (_lead_edition(3, "B0LEADREC1"), _lead_edition(5, None)),
        (("B0LEADREC1", "audnexus", ProviderStatus.NO_MATCH),),
    )
    restored = MetadataReport.from_json(report.to_json())
    assert restored == report
    assert (
        "hardcover-edition-0000000003",
        "hardcover-edition-0000000005",
    ) == _unconfirmed(restored)
