"""Hardcover's counted audio editions judge the accepted recording (#962).

An edition counts when it credits a narrator and states a runtime inside the
tight band of the file's. A match is challenged only when it is outside the
band and credits only narrators the counted editions do not: the same
narrator at a nearby runtime is the same recording (ADR-0089).
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


def _product(minutes: int, *narrators: str) -> ProviderCandidate:
    identity = "audible-product-B0PRODUCT1"
    return ProviderCandidate(
        identity,
        "unresolved",
        _claims(
            "audible",
            identity,
            (
                (ClaimKind.DURATION, f"{minutes * 60}.000", None),
                *((ClaimKind.CONTRIBUTOR, name, "narrator") for name in narrators),
            ),
        ),
    )


def _report(
    editions: tuple[ProviderCandidate, ...],
    product: ProviderCandidate | None = None,
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
