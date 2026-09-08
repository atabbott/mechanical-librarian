"""Reviewer-independent election over identity-confirmed cover candidates."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import cast

_IDENTITY_TIERS = {
    "verified_manifestation_match": 0,
    "verified_work_match": 1,
}


class CoverActualTargetState(Enum):
    """What exact evidence says about the target's actual current cover."""

    KNOWN_EMPTY = "known_empty"
    CONFIRMED = "confirmed"
    UNKNOWN = "unknown"


def strictly_better_cover_rank(candidate: object, incumbent: object) -> bool:
    """Compare two exact seven-part rank vectors, rejecting malformed input."""

    keys: list[tuple[int, int, int, int, int, int, str]] = []
    for value in (candidate, incumbent):
        if (
            type(value) is not list
            or len(value) != 7
            or any(type(component) is not int for component in value[:6])
            or type(value[6]) is not str
        ):
            return False
        keys.append(cast("tuple[int, int, int, int, int, int, str]", tuple(value)))
    return keys[0] < keys[1]


@dataclass(frozen=True, slots=True)
class CoverElectionCandidate:
    """The exact technical facts available for one collapsed packet candidate."""

    reference: str
    identity: str
    identity_citations: tuple[str, ...]
    source_citations: tuple[str, ...]
    incumbent: bool
    width: int
    height: int
    packet_ordinal: int

    def __post_init__(self) -> None:
        if (
            type(self.reference) is not str
            or not self.reference
            or self.identity not in _IDENTITY_TIERS
            or not self.source_citations
            or any(
                type(item) is not str or not item for item in self.identity_citations
            )
            or any(type(item) is not str or not item for item in self.source_citations)
            or type(self.incumbent) is not bool
            or type(self.width) is not int
            or type(self.height) is not int
            or type(self.packet_ordinal) is not int
            or self.width < 1
            or self.height < 1
            or self.packet_ordinal < 0
        ):
            raise ValueError("cover election candidate is invalid")

    @property
    def rank_key(self) -> tuple[int, int, int, int, int, int, str]:
        """The closed total order; smaller keys are better."""
        return (
            _IDENTITY_TIERS[self.identity],
            0 if self.height >= self.width and self.height <= 2 * self.width else 1,
            -(self.width * self.height),
            -self.width,
            -self.height,
            self.packet_ordinal,
            self.reference,
        )


@dataclass(frozen=True, slots=True)
class ElectedCover:
    """One ranked candidate and all closed citations that made it eligible."""

    candidate: CoverElectionCandidate
    rank: int

    @property
    def citations(self) -> tuple[str, ...]:
        return self.candidate.identity_citations + self.candidate.source_citations


@dataclass(frozen=True, slots=True)
class DeterministicCoverElection:
    """Independent owner readiness, replacement order, and automatic choice."""

    ranked: tuple[ElectedCover, ...]
    ready: bool
    automatic_recommendation: ElectedCover | None

    @property
    def recommendation(self) -> ElectedCover | None:
        return self.automatic_recommendation


def elect_confirmed_covers(
    candidates: tuple[CoverElectionCandidate, ...],
    *,
    provider_fetching: bool,
    actual_target_state: CoverActualTargetState,
) -> DeterministicCoverElection:
    """Rank confirmed candidates without observations or provider preference input."""
    if (
        type(provider_fetching) is not bool
        or type(actual_target_state) is not CoverActualTargetState
    ):
        raise ValueError("cover election readiness is invalid")
    if len({candidate.reference for candidate in candidates}) != len(candidates):
        raise ValueError("cover election candidates are not unique")
    ordered = sorted(
        (candidate for candidate in candidates if not candidate.incumbent),
        key=lambda candidate: candidate.rank_key,
    )
    ranked = tuple(
        ElectedCover(candidate=candidate, rank=index)
        for index, candidate in enumerate(ordered, start=1)
    )
    incumbents = tuple(candidate for candidate in candidates if candidate.incumbent)
    if (
        len(incumbents) > 1
        or (
            actual_target_state is CoverActualTargetState.CONFIRMED
            and len(incumbents) != 1
        )
        or (actual_target_state is CoverActualTargetState.KNOWN_EMPTY and incumbents)
    ):
        raise ValueError("cover election incumbent is invalid")
    ready = bool(ranked) and not provider_fetching
    recommendation = (
        ranked[0]
        if ready and actual_target_state is CoverActualTargetState.KNOWN_EMPTY
        else ranked[0]
        if ready
        and actual_target_state is CoverActualTargetState.CONFIRMED
        and strictly_better_cover_rank(
            list(ranked[0].candidate.rank_key), list(incumbents[0].rank_key)
        )
        else None
    )
    return DeterministicCoverElection(ranked, ready, recommendation)


__all__ = [
    "CoverActualTargetState",
    "CoverElectionCandidate",
    "DeterministicCoverElection",
    "ElectedCover",
    "elect_confirmed_covers",
    "strictly_better_cover_rank",
]
