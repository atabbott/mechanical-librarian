from __future__ import annotations

import pytest

from mechanical_librarian.domain.deterministic_cover_election import (
    CoverElectionCandidate,
    elect_confirmed_covers,
)


def _candidate(
    reference: str,
    *,
    identity: str = "verified_work_match",
    width: int = 100,
    height: int = 150,
    ordinal: int = 0,
    incumbent: bool = False,
) -> CoverElectionCandidate:
    return CoverElectionCandidate(
        reference=reference,
        identity=identity,
        identity_citations=("identity_" + reference,),
        source_citations=("source_" + reference,),
        incumbent=incumbent,
        width=width,
        height=height,
        packet_ordinal=ordinal,
    )


def test_election_uses_the_closed_total_order_and_retains_citations() -> None:
    election = elect_confirmed_covers(
        (
            _candidate("work-portrait", width=100, height=150, ordinal=3),
            _candidate(
                "edition-out-of-class",
                identity="verified_manifestation_match",
                width=400,
                height=100,
                ordinal=0,
            ),
            _candidate(
                "edition-square",
                identity="verified_manifestation_match",
                width=120,
                height=120,
                ordinal=2,
            ),
            _candidate(
                "edition-portrait",
                identity="verified_manifestation_match",
                width=100,
                height=150,
                ordinal=1,
            ),
        ),
        provider_fetching=False,
    )

    assert [item.candidate.reference for item in election.ranked] == [
        "edition-portrait",
        "edition-square",
        "edition-out-of-class",
        "work-portrait",
    ]
    assert election.recommendation is not None
    assert election.recommendation.rank == 1
    assert election.recommendation.citations == (
        "identity_edition-portrait",
        "source_edition-portrait",
    )


def test_election_breaks_equal_technical_ties_by_packet_ordinal_then_reference() -> (
    None
):
    election = elect_confirmed_covers(
        (
            _candidate("z", ordinal=1),
            _candidate("a", ordinal=1),
            _candidate("later", ordinal=2),
        ),
        provider_fetching=False,
    )

    assert [item.candidate.reference for item in election.ranked] == ["a", "z", "later"]


def test_election_is_not_ready_while_a_provider_is_actively_fetching() -> None:
    election = elect_confirmed_covers(
        (_candidate("confirmed"),), provider_fetching=True
    )

    assert election.ranked[0].rank == 1
    assert election.ready is False
    assert election.recommendation is None


def test_confirmed_incumbent_stays_out_of_rank_and_cannot_satisfy_readiness() -> None:
    incumbent_only = elect_confirmed_covers(
        (_candidate("incumbent", incumbent=True),), provider_fetching=False
    )
    alongside_alternative = elect_confirmed_covers(
        (
            _candidate(
                "incumbent",
                identity="verified_manifestation_match",
                incumbent=True,
            ),
            _candidate("alternative"),
        ),
        provider_fetching=False,
    )

    assert incumbent_only.ranked == ()
    assert incumbent_only.ready is False
    assert incumbent_only.recommendation is None
    assert [item.candidate.reference for item in alongside_alternative.ranked] == [
        "alternative"
    ]


def test_election_rejects_unconfirmed_identities_and_duplicate_references() -> None:
    with pytest.raises(ValueError, match="cover election candidate is invalid"):
        _candidate("plausible", identity="plausible_match")
    with pytest.raises(ValueError, match="cover election candidates are not unique"):
        elect_confirmed_covers(
            (_candidate("duplicate"), _candidate("duplicate")), provider_fetching=False
        )
