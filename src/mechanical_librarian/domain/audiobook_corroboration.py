"""Closed corroboration rules over local audio facts and provider structure.

An audiobook's identity is rarely settled by an identifier: on the maintainer's
reference library an ASIN is present on some local surface for 16.9% of items,
and the asserted and embedded values disagree on roughly 17% of the overlap. So
identity here is carried by structure -- how long the recording runs, how its
chapters are cut, and what its cover looks like -- compared against what a
provider states about the same recording.

Every function here is pure: it reads two sides and returns one closed signal.
Nothing dispatches, acquires, hashes, or writes, and no signal is a decision.
The three rules and their parameters live in one module precisely because the
parameters are measured at slice-2 qualification and must be tunable in one
place (ADR-0064).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from mechanical_librarian.domain.metadata_report import (
    AudioFacts,
    ClaimKind,
    ClaimScope,
    FieldState,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderChapterStructure,
    ProviderClaim,
    duration_milliseconds,
)

# Ninety whole seconds. A provider states a runtime in whole minutes, so two
# sources that agree exactly still differ by up to half a minute of rounding on
# each side, and the measured gate that admits a runtime as corroborating
# evidence sits at 90 s. The report's claim-link comparison happens to use the
# same number today, but that is a comparison over stated claims and this is the
# corroboration gate: each is measured on its own and retuned on its own at
# slice-2 qualification, so the value is owned here (ADR-0064).
RUNTIME_BAND_TIGHT_SECONDS: Final = 90
# Ten minutes: an edition whose runtime differs by less than this is routinely
# the same recording with a different intro, outro, or end-credit block.
# Beyond it, two recordings of one work are the ordinary explanation.
RUNTIME_BAND_LOOSE_SECONDS: Final = 600
# Chapter boundaries are cut once and re-encoded, so two statements of one
# chapter agree to about a second. Two is the measured tolerance.
CHAPTER_DURATION_TOLERANCE_SECONDS: Final = 2
# Publishers add or drop a single branded intro or outro chapter, and only
# ever one, so alignment may skip at most one chapter at each end of each
# side. Anything wider stops being an offset and becomes a different cut.
CHAPTER_ALIGNMENT_SKIP: Final = 1
# Measured cross-work collision at this tolerance is 2.59% over all lengths
# and 0.39% once five or more chapters are compared, so five is the floor for
# a strong agreement rather than a weak one.
STRONG_CHAPTER_AGREEMENT_MINIMUM: Final = 5
# A 64-bit perceptual hash. The rule is defined here; computing the hashes is
# a later packet's concern and this module never touches an image byte.
COVER_PERCEPTUAL_HASH_BITS: Final = 64
COVER_PERCEPTUAL_HASH_DISTANCE: Final = 10

_PERCEPTUAL_HASH: Final = re.compile(
    r"^[0-9a-f]{" + str(COVER_PERCEPTUAL_HASH_BITS // 4) + r"}$"
)


class CorroborationOutcome(StrEnum):
    """Closed outcome for one structural corroboration rule."""

    CORROBORATED = "corroborated"
    WEAKLY_CORROBORATED = "weakly_corroborated"
    CONTRADICTED = "contradicted"
    NOT_COMPARABLE = "not_comparable"


class CorroborationReason(StrEnum):
    """The complete rule-coded vocabulary these three rules can state."""

    RUNTIME_WITHIN_TIGHT_BAND = "runtime_within_tight_band"
    RUNTIME_WITHIN_LOOSE_BAND = "runtime_within_loose_band"
    RUNTIME_OUTSIDE_BANDS = "runtime_outside_bands"
    RUNTIME_UNAVAILABLE = "runtime_unavailable"
    CHAPTER_VECTOR_ALIGNED = "chapter_vector_aligned"
    CHAPTER_VECTOR_ALIGNED_WITH_OFFSET = "chapter_vector_aligned_with_offset"
    CHAPTER_VECTOR_BELOW_STRONG_MINIMUM = "chapter_vector_below_strong_minimum"
    CHAPTER_VECTOR_DIVERGED = "chapter_vector_diverged"
    CHAPTER_VECTOR_INCOMPLETE = "chapter_vector_incomplete"
    CHAPTER_VECTOR_UNAVAILABLE = "chapter_vector_unavailable"
    COVER_HASH_WITHIN_DISTANCE = "cover_hash_within_distance"
    COVER_HASH_OUTSIDE_DISTANCE = "cover_hash_outside_distance"
    COVER_HASH_SCOPE_INELIGIBLE = "cover_hash_scope_ineligible"
    COVER_HASH_UNAVAILABLE = "cover_hash_unavailable"


_OUTCOME_REASON_SETS: dict[CorroborationOutcome, frozenset[tuple[str, ...]]] = {
    CorroborationOutcome.CORROBORATED: frozenset(
        {
            (CorroborationReason.CHAPTER_VECTOR_ALIGNED.value,),
            (CorroborationReason.CHAPTER_VECTOR_ALIGNED_WITH_OFFSET.value,),
            (CorroborationReason.COVER_HASH_WITHIN_DISTANCE.value,),
            (CorroborationReason.RUNTIME_WITHIN_TIGHT_BAND.value,),
        }
    ),
    CorroborationOutcome.WEAKLY_CORROBORATED: frozenset(
        {
            (
                CorroborationReason.CHAPTER_VECTOR_ALIGNED.value,
                CorroborationReason.CHAPTER_VECTOR_BELOW_STRONG_MINIMUM.value,
            ),
            (
                CorroborationReason.CHAPTER_VECTOR_ALIGNED_WITH_OFFSET.value,
                CorroborationReason.CHAPTER_VECTOR_BELOW_STRONG_MINIMUM.value,
            ),
            (CorroborationReason.RUNTIME_WITHIN_LOOSE_BAND.value,),
        }
    ),
    CorroborationOutcome.CONTRADICTED: frozenset(
        {
            (CorroborationReason.CHAPTER_VECTOR_DIVERGED.value,),
            (CorroborationReason.COVER_HASH_OUTSIDE_DISTANCE.value,),
            (CorroborationReason.RUNTIME_OUTSIDE_BANDS.value,),
        }
    ),
    CorroborationOutcome.NOT_COMPARABLE: frozenset(
        {
            (CorroborationReason.CHAPTER_VECTOR_INCOMPLETE.value,),
            (CorroborationReason.CHAPTER_VECTOR_UNAVAILABLE.value,),
            (CorroborationReason.COVER_HASH_SCOPE_INELIGIBLE.value,),
            (CorroborationReason.COVER_HASH_UNAVAILABLE.value,),
            (CorroborationReason.RUNTIME_UNAVAILABLE.value,),
        }
    ),
}


class CorroborationValueError(ValueError):
    """A corroboration signal is outside its closed vocabulary."""


@dataclass(frozen=True, slots=True)
class CorroborationSignal:
    """One rule's outcome with the exact reasons that produced it."""

    outcome: CorroborationOutcome
    reasons: tuple[str, ...]
    compared: int = 0

    def __post_init__(self) -> None:
        if (
            type(self.outcome) is not CorroborationOutcome
            or type(self.reasons) is not tuple
            or type(self.compared) is not int
            or self.compared < 0
        ):
            raise CorroborationValueError("corroboration signal is invalid")
        if self.reasons != tuple(sorted(set(self.reasons))):
            raise CorroborationValueError("corroboration reasons are not canonical")
        if any(
            value not in {reason.value for reason in CorroborationReason}
            for value in self.reasons
        ):
            raise CorroborationValueError("corroboration reason is outside its set")
        if self.reasons not in _OUTCOME_REASON_SETS[self.outcome]:
            raise CorroborationValueError("corroboration reasons do not fit outcome")


def _signal(
    outcome: CorroborationOutcome,
    *reasons: CorroborationReason,
    compared: int = 0,
) -> CorroborationSignal:
    return CorroborationSignal(
        outcome,
        tuple(sorted({reason.value for reason in reasons})),
        compared,
    )


def local_runtime_seconds(report: MetadataReport) -> int | None:
    """Read one audiobook's own running time in whole seconds, or ``None``.

    The container may state a duration on more than one surface. They are only
    a running time if they agree once rounded to the second; disagreeing
    surfaces leave the item without a local runtime rather than with a guess.
    """
    if type(report) is not MetadataReport:
        raise CorroborationValueError("corroboration input is invalid")
    return local_claim_runtime_seconds(report.local_claims)


def local_claim_runtime_seconds(local_claims: tuple[LocalClaim, ...]) -> int | None:
    """Read one running time off a claim set, under the same agreement rule."""
    if type(local_claims) is not tuple:
        raise CorroborationValueError("corroboration input is invalid")
    values = {
        seconds
        for claim in local_claims
        if _valid_duration_claim(claim)
        and (seconds := _claim_runtime_seconds(claim)) is not None
    }
    return values.pop() if len(values) == 1 else None


def runtime_corroborated_candidate(
    candidates: tuple[ProviderCandidate, ...],
    local_claims: tuple[LocalClaim, ...],
) -> tuple[ProviderCandidate, tuple[str, ...]] | None:
    """Choose the one candidate whose stated runtime is this recording's.

    An audiobook's edition identity is what this gate decides, so it fails
    closed in every direction that is not a positive statement: an item with no
    single local running time, a candidate that states none, a candidate whose
    runtime only agrees inside the loose band, and a candidate set where none
    agrees all return ``None`` and leave the caller with its work-scope shape.

    Only the tight band admits. The loose band exists to explain a plausible
    near-miss, not to promote one: ten minutes is wide enough to hold a
    different cut of the same work, and an ASIN naming the wrong cut is exactly
    the error this gate exists to prevent.

    Several candidates inside the tight band is a real outcome -- a work
    published in more than one marketplace states one runtime -- so the tie is
    broken on identity, which is deterministic and provider-native.
    """
    if type(candidates) is not tuple or any(
        type(value) is not ProviderCandidate for value in candidates
    ):
        raise CorroborationValueError("corroboration input is invalid")
    local = local_claim_runtime_seconds(local_claims)
    if local is None:
        return None
    admitted: list[tuple[ProviderCandidate, CorroborationSignal]] = []
    for candidate in candidates:
        signal = runtime_agreement(local, candidate_runtime_seconds(candidate))
        if signal.outcome is CorroborationOutcome.CORROBORATED:
            admitted.append((candidate, signal))
    if not admitted:
        return None
    candidate, signal = min(admitted, key=lambda value: value[0].identity)
    return candidate, signal.reasons


def local_chapter_durations_seconds(audio: AudioFacts | None) -> tuple[int, ...]:
    """Project the embedded chapter table onto whole-second durations."""
    if audio is None:
        return ()
    if type(audio) is not AudioFacts:
        raise CorroborationValueError("corroboration input is invalid")
    return tuple(
        (chapter.end_milliseconds - chapter.start_milliseconds + 500) // 1000
        for chapter in audio.chapters
    )


def runtime_agreement(
    local_seconds: int | None, provider_seconds: int | None
) -> CorroborationSignal:
    """Compare two running times against the two named tolerance bands."""
    if (
        type(local_seconds) is not int
        or type(provider_seconds) is not int
        or local_seconds < 0
        or provider_seconds < 0
    ):
        return _signal(
            CorroborationOutcome.NOT_COMPARABLE,
            CorroborationReason.RUNTIME_UNAVAILABLE,
        )
    difference = abs(local_seconds - provider_seconds)
    if difference <= RUNTIME_BAND_TIGHT_SECONDS:
        return _signal(
            CorroborationOutcome.CORROBORATED,
            CorroborationReason.RUNTIME_WITHIN_TIGHT_BAND,
        )
    if difference <= RUNTIME_BAND_LOOSE_SECONDS:
        return _signal(
            CorroborationOutcome.WEAKLY_CORROBORATED,
            CorroborationReason.RUNTIME_WITHIN_LOOSE_BAND,
        )
    return _signal(
        CorroborationOutcome.CONTRADICTED,
        CorroborationReason.RUNTIME_OUTSIDE_BANDS,
    )


def chapter_vector_agreement(
    local_durations: tuple[int, ...],
    provider: ProviderChapterStructure | None,
    *,
    local_truncated: bool = False,
) -> CorroborationSignal:
    """Align two chapter-duration vectors within the per-chapter tolerance.

    Alignment may skip up to one leading and one trailing chapter on either
    side, because a publisher's branded intro or outro is a systematic offset
    rather than a different cut. A vector either side truncated is not
    comparable at all: a partial cut proves nothing about the rest of it.
    """
    if type(local_durations) is not tuple or any(
        type(value) is not int for value in local_durations
    ):
        raise CorroborationValueError("corroboration input is invalid")
    if provider is not None and type(provider) is not ProviderChapterStructure:
        raise CorroborationValueError("corroboration input is invalid")
    if provider is None or not provider.durations_seconds or not local_durations:
        return _signal(
            CorroborationOutcome.NOT_COMPARABLE,
            CorroborationReason.CHAPTER_VECTOR_UNAVAILABLE,
        )
    if local_truncated or provider.durations_truncated:
        return _signal(
            CorroborationOutcome.NOT_COMPARABLE,
            CorroborationReason.CHAPTER_VECTOR_INCOMPLETE,
        )
    best = _best_alignment(local_durations, provider.durations_seconds)
    if best is None:
        return _signal(
            CorroborationOutcome.CONTRADICTED,
            CorroborationReason.CHAPTER_VECTOR_DIVERGED,
        )
    compared, offset = best
    aligned = (
        CorroborationReason.CHAPTER_VECTOR_ALIGNED_WITH_OFFSET
        if offset
        else CorroborationReason.CHAPTER_VECTOR_ALIGNED
    )
    if compared < STRONG_CHAPTER_AGREEMENT_MINIMUM:
        return _signal(
            CorroborationOutcome.WEAKLY_CORROBORATED,
            aligned,
            CorroborationReason.CHAPTER_VECTOR_BELOW_STRONG_MINIMUM,
            compared=compared,
        )
    return _signal(CorroborationOutcome.CORROBORATED, aligned, compared=compared)


def cover_hash_agreement(
    local_hash: str | None,
    provider_hash: str | None,
    scope: ClaimScope,
) -> CorroborationSignal:
    """Compare two perceptual cover hashes, at work scope only.

    An audiobook edition's art routinely differs from the print edition's while
    naming the same work, so this rule corroborates a work and never an
    edition; asked at edition scope it declines rather than answers.
    """
    if type(scope) is not ClaimScope:
        raise CorroborationValueError("corroboration input is invalid")
    if scope is not ClaimScope.WORK:
        return _signal(
            CorroborationOutcome.NOT_COMPARABLE,
            CorroborationReason.COVER_HASH_SCOPE_INELIGIBLE,
        )
    left = _perceptual_hash(local_hash)
    right = _perceptual_hash(provider_hash)
    if left is None or right is None:
        return _signal(
            CorroborationOutcome.NOT_COMPARABLE,
            CorroborationReason.COVER_HASH_UNAVAILABLE,
        )
    if bin(left ^ right).count("1") <= COVER_PERCEPTUAL_HASH_DISTANCE:
        return _signal(
            CorroborationOutcome.CORROBORATED,
            CorroborationReason.COVER_HASH_WITHIN_DISTANCE,
        )
    return _signal(
        CorroborationOutcome.CONTRADICTED,
        CorroborationReason.COVER_HASH_OUTSIDE_DISTANCE,
    )


def _valid_duration_claim(claim: LocalClaim) -> bool:
    return (
        type(claim) is LocalClaim
        and claim.kind is ClaimKind.DURATION
        and claim.state is FieldState.VALID
        and claim.canonical is not None
    )


def _claim_runtime_seconds(claim: LocalClaim) -> int | None:
    milliseconds = duration_milliseconds(claim.canonical or "")
    return None if milliseconds is None else (milliseconds + 500) // 1000


def candidate_runtime_seconds(candidate: ProviderCandidate) -> int | None:
    """Read the one running time a candidate states, or nothing at all.

    Two `DURATION` claims on one candidate is two answers to a question that
    has one, so a candidate that states more than one states none here.
    """
    values = {
        seconds
        for claim in candidate.claims
        if _valid_provider_duration_claim(claim)
        and (seconds := _provider_claim_runtime_seconds(claim)) is not None
    }
    return values.pop() if len(values) == 1 else None


def _valid_provider_duration_claim(claim: ProviderClaim) -> bool:
    return (
        type(claim) is ProviderClaim
        and claim.kind is ClaimKind.DURATION
        and claim.state is FieldState.VALID
        and claim.value is not None
    )


def _provider_claim_runtime_seconds(claim: ProviderClaim) -> int | None:
    milliseconds = duration_milliseconds(claim.value or "")
    return None if milliseconds is None else (milliseconds + 500) // 1000


def _best_alignment(
    local: tuple[int, ...], provider: tuple[int, ...]
) -> tuple[int, bool] | None:
    """Return the widest tolerated alignment as (compared, offset applied)."""
    skips = range(CHAPTER_ALIGNMENT_SKIP + 1)
    best: tuple[int, bool] | None = None
    for local_head in skips:
        for local_tail in skips:
            for provider_head in skips:
                for provider_tail in skips:
                    left = local[local_head : len(local) - local_tail]
                    right = provider[provider_head : len(provider) - provider_tail]
                    if not left or len(left) != len(right):
                        continue
                    if any(
                        abs(one - other) > CHAPTER_DURATION_TOLERANCE_SECONDS
                        for one, other in zip(left, right, strict=True)
                    ):
                        continue
                    offset = bool(
                        local_head or local_tail or provider_head or provider_tail
                    )
                    if best is None or (len(left), not offset) > (best[0], not best[1]):
                        best = (len(left), offset)
    return best


def first_divergent_chapter(
    local_durations: tuple[int, ...], provider_durations: tuple[int, ...]
) -> tuple[int, int, int, int] | None:
    """Name the first cut two chapter vectors disagree on, and both lengths.

    The agreement gate answers whether the vectors align at all; a reader who
    is told they do not needs the cut that proves it. Both questions have to be
    asked under the same rule, so the same skip grid and the same per-chapter
    tolerance are walked here: a publisher's branded intro is a systematic
    offset, and naming the recording's second chapter against the edition's
    first is the difference between a discrepancy an owner can open the file on
    and an arithmetic complaint about chapter one.

    The alignment chosen is the one that agrees for longest before it fails,
    preferring no skip where two agree equally, which is the same preference
    `chapter_vector_agreement` applies. Returns the local ordinal and its
    seconds with the stated ordinal and its seconds, both one-based, or `None`
    when no alignment disagrees anywhere within the cuts it compares -- a
    recording contradicted on chapter count alone.
    """
    if (
        type(local_durations) is not tuple
        or type(provider_durations) is not tuple
        or any(
            type(value) is not int for value in (*local_durations, *provider_durations)
        )
    ):
        raise CorroborationValueError("corroboration input is invalid")
    skips = range(CHAPTER_ALIGNMENT_SKIP + 1)
    best: tuple[int, bool, int, int, int, int] | None = None
    for local_head in skips:
        for local_tail in skips:
            for provider_head in skips:
                for provider_tail in skips:
                    left = local_durations[
                        local_head : len(local_durations) - local_tail
                    ]
                    right = provider_durations[
                        provider_head : len(provider_durations) - provider_tail
                    ]
                    if not left or len(left) != len(right):
                        continue
                    for position, (one, other) in enumerate(
                        zip(left, right, strict=True)
                    ):
                        if abs(one - other) > CHAPTER_DURATION_TOLERANCE_SECONDS:
                            offset = bool(
                                local_head
                                or local_tail
                                or provider_head
                                or provider_tail
                            )
                            candidate = (
                                position,
                                not offset,
                                local_head + position + 1,
                                one,
                                provider_head + position + 1,
                                other,
                            )
                            if best is None or candidate[:2] > best[:2]:
                                best = candidate
                            break
    if best is None:
        return None
    return best[2], best[3], best[4], best[5]


def _perceptual_hash(value: str | None) -> int | None:
    if type(value) is not str or _PERCEPTUAL_HASH.fullmatch(value) is None:
        return None
    return int(value, 16)


__all__ = [
    "CHAPTER_ALIGNMENT_SKIP",
    "CHAPTER_DURATION_TOLERANCE_SECONDS",
    "COVER_PERCEPTUAL_HASH_BITS",
    "COVER_PERCEPTUAL_HASH_DISTANCE",
    "CorroborationOutcome",
    "CorroborationReason",
    "CorroborationSignal",
    "CorroborationValueError",
    "RUNTIME_BAND_LOOSE_SECONDS",
    "RUNTIME_BAND_TIGHT_SECONDS",
    "STRONG_CHAPTER_AGREEMENT_MINIMUM",
    "candidate_runtime_seconds",
    "chapter_vector_agreement",
    "cover_hash_agreement",
    "first_divergent_chapter",
    "local_chapter_durations_seconds",
    "local_claim_runtime_seconds",
    "local_runtime_seconds",
    "runtime_agreement",
    "runtime_corroborated_candidate",
]
