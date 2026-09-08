from __future__ import annotations

import pytest

from mechanical_librarian.domain.deterministic_cover_election import (
    CoverActualTargetState,
    CoverElectionCandidate,
    elect_confirmed_covers,
    strictly_better_cover_rank,
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
        actual_target_state=CoverActualTargetState.KNOWN_EMPTY,
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
        actual_target_state=CoverActualTargetState.KNOWN_EMPTY,
    )

    assert [item.candidate.reference for item in election.ranked] == ["a", "z", "later"]


def test_election_is_not_ready_while_a_provider_is_actively_fetching() -> None:
    election = elect_confirmed_covers(
        (_candidate("confirmed"),),
        provider_fetching=True,
        actual_target_state=CoverActualTargetState.KNOWN_EMPTY,
    )

    assert election.ranked[0].rank == 1
    assert election.ready is False
    assert election.recommendation is None


def test_confirmed_incumbent_stays_out_of_rank_and_cannot_satisfy_readiness() -> None:
    incumbent_only = elect_confirmed_covers(
        (_candidate("incumbent", incumbent=True),),
        provider_fetching=False,
        actual_target_state=CoverActualTargetState.CONFIRMED,
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
        actual_target_state=CoverActualTargetState.CONFIRMED,
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
            (_candidate("duplicate"), _candidate("duplicate")),
            provider_fetching=False,
            actual_target_state=CoverActualTargetState.KNOWN_EMPTY,
        )


@pytest.mark.parametrize(
    ("target_state", "incumbent", "candidate", "expected"),
    (
        (
            CoverActualTargetState.KNOWN_EMPTY,
            None,
            _candidate("empty-fill", width=300, height=450),
            "empty-fill",
        ),
        (
            CoverActualTargetState.CONFIRMED,
            _candidate(
                "large-incumbent",
                identity="verified_manifestation_match",
                width=3000,
                height=4500,
                incumbent=True,
            ),
            _candidate("small-replacement", width=300, height=450, ordinal=1),
            None,
        ),
        (
            CoverActualTargetState.CONFIRMED,
            _candidate("small-incumbent", width=300, height=450, incumbent=True),
            _candidate(
                "edition-upgrade",
                identity="verified_manifestation_match",
                width=3000,
                height=4500,
                ordinal=1,
            ),
            "edition-upgrade",
        ),
        (
            CoverActualTargetState.UNKNOWN,
            None,
            _candidate("owner-only", width=3000, height=4500),
            None,
        ),
    ),
)
def test_automatic_recommendation_compares_the_actual_target(
    target_state: CoverActualTargetState,
    incumbent: CoverElectionCandidate | None,
    candidate: CoverElectionCandidate,
    expected: str | None,
) -> None:
    election = elect_confirmed_covers(
        (candidate,) if incumbent is None else (incumbent, candidate),
        provider_fetching=False,
        actual_target_state=target_state,
    )

    assert election.ready is True
    assert [item.candidate.reference for item in election.ranked] == [
        candidate.reference
    ]
    assert (
        None
        if election.recommendation is None
        else election.recommendation.candidate.reference
    ) == expected


@pytest.mark.parametrize(
    "malformed",
    (
        (0, 0, -1, -1, -1, 0, "candidate"),
        [0, 0, -1, -1, -1, 0],
        [0, 0, -1, -1, -1, 0, 1],
        [False, 0, -1, -1, -1, 0, "candidate"],
        [0, 0, -1, -1, -1, 0, "candidate", "extra"],
    ),
)
def test_strict_rank_comparison_rejects_malformed_vectors(malformed: object) -> None:
    valid = [0, 0, -100, -10, -10, 0, "valid"]
    assert strictly_better_cover_rank(malformed, valid) is False
    assert strictly_better_cover_rank(valid, malformed) is False


def test_strict_rank_comparison_preserves_ties_and_all_seven_dimensions() -> None:
    incumbent = [1, 1, -100, -10, -10, 2, "b"]

    assert strictly_better_cover_rank([0, 1, -100, -10, -10, 2, "b"], incumbent)
    assert strictly_better_cover_rank([1, 0, -100, -10, -10, 2, "b"], incumbent)
    assert strictly_better_cover_rank([1, 1, -101, -10, -10, 2, "b"], incumbent)
    assert strictly_better_cover_rank([1, 1, -100, -11, -10, 2, "b"], incumbent)
    assert strictly_better_cover_rank([1, 1, -100, -10, -11, 2, "b"], incumbent)
    assert strictly_better_cover_rank([1, 1, -100, -10, -10, 1, "b"], incumbent)
    assert strictly_better_cover_rank([1, 1, -100, -10, -10, 2, "a"], incumbent)
    assert strictly_better_cover_rank(incumbent, incumbent) is False
