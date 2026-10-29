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
from decimal import Decimal
from enum import StrEnum
from typing import Final, cast

from mechanical_librarian.domain.audio_inspection import (
    AudioChapterComposition,
    AudioObservation,
    AudioObservationKind,
)
from mechanical_librarian.domain.audiobook_decision_qualification import (
    narrator_cast_disjoint,
)
from mechanical_librarian.domain.metadata_report import (
    ASSERTED_ASIN_BASES,
    AUDIOBOOK_PROVIDER_AUTHORITIES,
    AudioFacts,
    ClaimKind,
    ClaimScope,
    FieldState,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderChapterStructure,
    ProviderClaim,
    ProviderEvidenceKind,
    ProviderLaneObservation,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    RecordingCorroboration,
    RecordingLookupHint,
    TargetDestination,
    TargetFieldState,
    candidate_asins,
    canonical_asin,
    discovery_lane_identity,
    duration_milliseconds,
    provider_candidate_exact_work_identity_admission,
    provider_candidate_identity_title_claims,
    recording_variant_conflict,
)
from mechanical_librarian.domain.work_identity import (
    WorkDispatchLineage,
    work_title_extends,
    work_title_related,
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
# Every audiobook authority states its runtime in whole minutes, truncated, so
# the running time a minute states runs up to 59 seconds past it (#1307).
MINUTE_FLOOR_SECONDS: Final = 59
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
    # A product inside the band read exactly keeps its precedence over one the
    # minute floor alone brings in, so the floor reading (#1307) never changes
    # which candidate an earlier admission chose.
    for provider_minutes in (False, True):
        admitted: list[tuple[ProviderCandidate, CorroborationSignal]] = []
        for candidate in candidates:
            signal = runtime_agreement(
                local,
                candidate_runtime_seconds(candidate),
                provider_minutes=provider_minutes,
            )
            if signal.outcome is CorroborationOutcome.CORROBORATED:
                admitted.append((candidate, signal))
        if admitted:
            candidate, signal = min(admitted, key=lambda value: value[0].identity)
            return candidate, signal.reasons
    return None


def loose_band_survivors(
    candidates: tuple[ProviderCandidate, ...],
    local_claims: tuple[LocalClaim, ...],
) -> tuple[ProviderCandidate, ...] | None:
    """Keep the candidates the loose runtime band does not contradict (#1256).

    This is the narrowing an overflowing audiobook search page takes before the
    candidate bound applies, so it removes only what the band positively rules
    out: a candidate whose stated runtime is more than the loose band from this
    recording's. A tight-band, loose-band or runtime-less candidate survives,
    because an absence is not a contradiction and a near-miss is still the
    work. Survivors keep their input order.

    An item with no single local running time has nothing to narrow against, so
    it returns ``None`` rather than a set the band never judged.
    """
    if type(candidates) is not tuple or any(
        type(value) is not ProviderCandidate for value in candidates
    ):
        raise CorroborationValueError("corroboration input is invalid")
    local = local_claim_runtime_seconds(local_claims)
    if local is None:
        return None
    return tuple(
        candidate
        for candidate in candidates
        if runtime_agreement(local, candidate_runtime_seconds(candidate)).outcome
        is not CorroborationOutcome.CONTRADICTED
    )


def loose_band_near_miss(
    products: tuple[ProviderCandidate, ...],
    local_claims: tuple[LocalClaim, ...],
    title: str,
    authors: tuple[str, ...],
) -> int | None:
    """Say how far the nearest title-matching contradicted product ran (#1258).

    The loose band rules out a product whose stated runtime is more than
    `RUNTIME_BAND_LOOSE_SECONDS` from this recording's. Of the products it
    rules out, only those exact work-identity admission accepts against the
    lane's own title and full author set are this work, so the nearest of
    them is the near-miss: provider minus local, in whole seconds, the smaller
    magnitude first and the shorter product on a tie. It is a measurement and
    nothing reads it as evidence.

    An item with no single local running time, or a page with no such
    product, has none.
    """
    if type(products) is not tuple or any(
        type(value) is not ProviderCandidate for value in products
    ):
        raise CorroborationValueError("corroboration input is invalid")
    local = local_claim_runtime_seconds(local_claims)
    if local is None:
        return None
    return min(
        (
            stated - local
            for product in products
            if (stated := candidate_runtime_seconds(product)) is not None
            and runtime_agreement(local, stated).outcome
            is CorroborationOutcome.CONTRADICTED
            and provider_candidate_exact_work_identity_admission(
                product, title, authors, audiobook=True
            ).admitted
        ),
        key=lambda value: (abs(value), value),
        default=None,
    )


# Best first: the band a lane's closest retained candidate falls in.
_LANE_BANDS = (
    (CorroborationOutcome.CORROBORATED, "tight"),
    (CorroborationOutcome.WEAKLY_CORROBORATED, "loose"),
    (CorroborationOutcome.CONTRADICTED, "outside"),
    (CorroborationOutcome.NOT_COMPARABLE, "unstated"),
)


def lane_band(
    provider: ProviderObservation,
    lane: ProviderLaneObservation,
    local_runtime: int | None,
) -> str:
    """Name the best runtime band one lane's retained candidates reach (#1258).

    A derived measurement fact, never persisted: tight before loose before
    outside before unstated, and `none` for a lane that retained nothing.
    """
    if lane.status is not ProviderStatus.CANDIDATES:
        return "none"
    retained = {candidate.identity: candidate for candidate in provider.candidates}
    outcomes = {
        stated_runtime_agreement(
            local_runtime, retained[value.edition_identity]
        ).outcome
        for value in lane.candidates
    }
    return next(band for outcome, band in _LANE_BANDS if outcome in outcomes)


def recording_lookup_hints(
    products: tuple[ProviderCandidate, ...],
    retained: tuple[ProviderCandidate, ...],
    local_claims: tuple[LocalClaim, ...],
    rung: WorkDispatchLineage,
) -> tuple[RecordingLookupHint, ...]:
    """Keep one rung's tight-band products that admission did not keep (#1257).

    `products` is the rung's page after any #1256 narrowing, and `retained` is
    what qualification kept from it: admitted candidates and #1004's exact
    lookup candidates. A product becomes a hint only when it is not one of
    those, names exactly one valid canonical ASIN that none of them claims,
    and states a runtime inside the tight band of this recording's. The band
    is the only judgement made here, so a hint is lookup-only evidence: the
    per-ASIN lanes it feeds still apply exact work-identity admission.

    An item with no single local running time keeps none. Hints are ordered by
    ASIN, one per ASIN, and the per-item bound is the caller's.
    """
    if (
        type(products) is not tuple
        or type(retained) is not tuple
        or any(type(value) is not ProviderCandidate for value in (*products, *retained))
        or type(rung) is not WorkDispatchLineage
    ):
        raise CorroborationValueError("corroboration input is invalid")
    local = local_claim_runtime_seconds(local_claims)
    if local is None:
        return ()
    kept = {value.identity for value in retained}
    held = candidate_asins(retained)
    hints: dict[str, RecordingLookupHint] = {}
    for product in products:
        asin = _unambiguous_candidate_asin(product)
        stated = candidate_runtime_seconds(product)
        if (
            product.identity in kept
            or asin is None
            or asin in held
            # A sub-second statement rounds to no runtime a hint can carry.
            or not stated
            or runtime_agreement(local, stated, provider_minutes=True).outcome
            is not CorroborationOutcome.CORROBORATED
        ):
            continue
        hints.setdefault(asin, RecordingLookupHint(asin, stated, rung))
    return tuple(hints[asin] for asin in sorted(hints))


def runtime_short_of_stated(
    candidate: ProviderCandidate, local_claims: tuple[LocalClaim, ...]
) -> bool:
    """Say whether this recording runs short of what this candidate states.

    The same two running times `runtime_corroborated_candidate` compares, asked
    in one direction rather than symmetrically: corroboration wants to know
    whether the two agree, and this wants to know whether the file on the mount
    is missing time the edition says it has. A recording that is *longer* than
    the edition states is not short of it and says nothing here -- a publisher's
    branded outro is not a truncated download.

    Only past the tight band, so the one number this build treats as agreement
    is the one number it treats as silence here too.

    Nothing is stated when either side states no single running time: an
    absence is not a shortfall, and inventing one from a missing value is the
    error this gate exists to avoid.
    """
    if type(candidate) is not ProviderCandidate:
        raise CorroborationValueError("corroboration input is invalid")
    local = local_claim_runtime_seconds(local_claims)
    stated = candidate_runtime_seconds(candidate)
    if local is None or stated is None:
        return False
    return stated - local > RUNTIME_BAND_TIGHT_SECONDS


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
    local_seconds: int | None,
    provider_seconds: int | None,
    *,
    provider_minutes: bool = False,
) -> CorroborationSignal:
    """Compare two running times against the two named tolerance bands.

    `provider_minutes` says the provider's value is a whole-minute statement
    (#1307, ADR-0064 as amended): a minute is a floor, so the running time it
    states lies in `[m, m + 59]` seconds, and the tight band measures from
    that interval rather than from its lower end. The band stays 90 seconds
    wide, and the loose band still measures from the stated value.
    """
    if (
        type(local_seconds) is not int
        or type(provider_seconds) is not int
        or type(provider_minutes) is not bool
        or local_seconds < 0
        or provider_seconds < 0
    ):
        return _signal(
            CorroborationOutcome.NOT_COMPARABLE,
            CorroborationReason.RUNTIME_UNAVAILABLE,
        )
    difference = abs(local_seconds - provider_seconds)
    if (
        tight_runtime_distance(
            local_seconds, provider_seconds, provider_minutes=provider_minutes
        )
        <= RUNTIME_BAND_TIGHT_SECONDS
    ):
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


def tight_runtime_distance(
    local_seconds: int, provider_seconds: int, *, provider_minutes: bool = False
) -> int:
    """Measure a provider running time's distance for the tight band (#1307).

    A whole-minute statement is read as the interval `[m, m + 59]` seconds,
    and its distance is zero inside that interval; any other value is read
    exactly.
    """
    if not provider_minutes or provider_seconds % 60:
        return abs(local_seconds - provider_seconds)
    if local_seconds < provider_seconds:
        return provider_seconds - local_seconds
    return max(0, local_seconds - provider_seconds - MINUTE_FLOOR_SECONDS)


def stated_runtime_agreement(
    local_seconds: int | None, candidate: ProviderCandidate
) -> CorroborationSignal:
    """Compare a recording with the whole-minute runtime a product states."""
    return runtime_agreement(
        local_seconds, candidate_runtime_seconds(candidate), provider_minutes=True
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


def complete_chapter_runtime_agreement(
    audio: AudioFacts | None,
    local_claims: tuple[LocalClaim, ...],
    provider: ProviderChapterStructure | None,
) -> bool:
    """Require complete timing at a directly testable domain identity boundary."""
    local_seconds = local_claim_runtime_seconds(local_claims)
    if (
        audio is None
        or audio.composition
        not in {
            AudioChapterComposition.SINGLE_FILE,
            AudioChapterComposition.EMBEDDED_CONCATENATED,
        }
        or audio.chapter_rows_omitted
        or audio.chapters_truncated
        or not audio.chapters
        or provider is None
        or provider.durations_truncated
    ):
        return False
    end = 0
    for chapter in audio.chapters:
        if chapter.start_milliseconds != end or chapter.end_milliseconds <= end:
            return False
        end = chapter.end_milliseconds
    milliseconds = tuple(
        value
        for claim in local_claims
        if _valid_duration_claim(claim)
        and (value := duration_milliseconds(claim.canonical or "")) is not None
    )
    if local_seconds is None:
        if (
            audio.composition is not AudioChapterComposition.SINGLE_FILE
            or len(audio.tracks) != 1
            or audio.tracks[0].duration_milliseconds is not None
            or any(claim.kind is ClaimKind.DURATION for claim in local_claims)
            or tuple(
                observation
                for observation in audio.observations
                if observation.kind is AudioObservationKind.DURATION_UNSTATED
            )
            != (AudioObservation(AudioObservationKind.DURATION_UNSTATED, (1,)),)
            or any(
                observation.kind is AudioObservationKind.TRACK_DURATION_UNAVAILABLE
                for observation in audio.observations
            )
        ):
            return False
        local_seconds = (end + 500) // 1000
    elif (
        local_seconds <= 0
        or not milliseconds
        or any(abs(end - value) > 1000 for value in milliseconds)
    ):
        return False
    local = local_chapter_durations_seconds(audio)
    durations = provider.durations_seconds
    if (
        not audio.tracks
        or provider.chapter_count != len(durations)
        or len(local) < STRONG_CHAPTER_AGREEMENT_MINIMUM
        or any(value <= 0 for value in (*local, *durations))
        or len(set(local)) == 1
        or len(set(durations)) == 1
        or not 0 <= provider.runtime_seconds - sum(durations) <= len(durations)
    ):
        return False
    if (
        len(local) == len(durations)
        and runtime_agreement(local_seconds, provider.runtime_seconds).outcome
        is CorroborationOutcome.CORROBORATED
        and all(
            abs(one - other) <= CHAPTER_DURATION_TOLERANCE_SECONDS
            for one, other in zip(local, durations, strict=True)
        )
    ):
        return True
    return _bounded_endpoint_chapter_alignment(local, durations)


def _bounded_endpoint_chapter_alignment(
    local: tuple[int, ...], provider: tuple[int, ...]
) -> bool:
    """Prove one preferred bounded correspondence without an interior edit."""
    mappings: list[
        tuple[tuple[int, int], tuple[tuple[tuple[int, ...], tuple[int, ...]], ...]]
    ] = []
    for left_start in (0, 1):
        for left_end in (0, 1):
            for right_start in (0, 1):
                for right_end in (0, 1):
                    if (
                        sum(local[:left_start])
                        + (sum(local[-left_end:]) if left_end else 0)
                        > 600
                        or sum(provider[:right_start])
                        + (sum(provider[-right_end:]) if right_end else 0)
                        > 600
                    ):
                        continue
                    left: tuple[tuple[int, ...], ...] = tuple(
                        (i,) for i in range(left_start, len(local) - left_end)
                    )
                    right: tuple[tuple[int, ...], ...] = tuple(
                        (i,) for i in range(right_start, len(provider) - right_end)
                    )
                    for merge in (
                        None,
                        "left_start",
                        "left_end",
                        "right_start",
                        "right_end",
                    ):
                        if merge is not None and (
                            (merge.endswith("start") and (left_start or right_start))
                            or (merge.endswith("end") and (left_end or right_end))
                        ):
                            continue
                        one, other = left, right
                        if merge is not None:
                            groups = one if merge.startswith("left") else other
                            if len(groups) < 2:
                                continue
                            groups = (
                                (groups[0] + groups[1], *groups[2:])
                                if merge.endswith("start")
                                else (*groups[:-2], groups[-2] + groups[-1])
                            )
                            if merge.startswith("left"):
                                one = groups
                            else:
                                other = groups
                        if len(one) != len(other) or len(one) < 7:
                            continue
                        pairs = tuple(zip(one, other, strict=True))
                        interior = pairs[1:-1]
                        if len(interior) < 5 or any(
                            len(a) != 1
                            or len(b) != 1
                            or abs(local[a[0]] - provider[b[0]]) > 2
                            for a, b in interior
                        ):
                            continue
                        if (
                            len({local[a[0]] for a, b in interior}) == 1
                            or len({provider[b[0]] for a, b in interior}) == 1
                        ):
                            continue
                        if any(
                            len(a) == len(b) == 1
                            and abs(local[a[0]] - provider[b[0]]) > 2
                            for a, b in pairs
                        ):
                            continue
                        if (
                            abs(
                                sum(local[i] for a, b in pairs for i in a)
                                - sum(provider[i] for a, b in pairs for i in b)
                            )
                            > 90
                        ):
                            continue
                        edits = (
                            left_start
                            + left_end
                            + right_start
                            + right_end
                            + int(merge is not None)
                        )
                        mappings.append(((-len(interior), edits), pairs))
    if not mappings:
        return False
    best = min(score for score, _ in mappings)
    return len({pairs for score, pairs in mappings if score == best}) == 1


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
    seconds with the stated ordinal and its seconds, both one-based.

    `None` means *no cut could be named*, which is not the same as agreement,
    and there are two ways to reach it. Vectors the skip grid can make equal in
    length may agree at every cut it compares. Vectors whose lengths differ by
    more than the grid can absorb are compared nowhere at all: every candidate
    alignment is of unequal length and is skipped, so a recording with forty
    chapters against an edition's twelve returns `None` here while
    `chapter_vector_agreement` contradicts it on count. A caller must therefore
    read `None` as "no divergent cut to cite" and keep whatever the agreement
    gate said, never as "the cuts line up".
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
    "MINUTE_FLOOR_SECONDS",
    "RUNTIME_BAND_LOOSE_SECONDS",
    "RUNTIME_BAND_TIGHT_SECONDS",
    "STRONG_CHAPTER_AGREEMENT_MINIMUM",
    "candidate_runtime_seconds",
    "chapter_vector_agreement",
    "complete_chapter_runtime_agreement",
    "cover_hash_agreement",
    "first_divergent_chapter",
    "lane_band",
    "local_chapter_durations_seconds",
    "local_claim_runtime_seconds",
    "local_runtime_seconds",
    "loose_band_near_miss",
    "loose_band_survivors",
    "recording_lookup_hints",
    "runtime_agreement",
    "runtime_corroborated_candidate",
    "runtime_short_of_stated",
    "stated_runtime_agreement",
    "tight_runtime_distance",
    "admitted_recording_asin",
    "recording_admitted",
    "band_first_candidate",
    "corroborated_recording_candidate",
    "candidate_edition_asin",
    "candidate_narrator_disjoint",
    "carried_asin_claim",
    "carried_asin_precedence",
    "target_current_asin",
    "verified_carried_asin",
]


def recording_runtime_veto(candidate: ProviderCandidate, coverage: int) -> bool:
    """Reject retained malformed/conflicting statements before source selection."""
    durations = tuple(
        claim for claim in candidate.claims if claim.kind is ClaimKind.DURATION
    )
    if not durations:
        return False
    if len(durations) != 1:
        return True
    duration = durations[0]
    if duration.state is FieldState.ABSENT:
        return False
    seconds = candidate_runtime_seconds(candidate)
    return (
        duration.state is not FieldState.VALID
        or seconds is None
        or seconds <= 0
        or runtime_agreement(coverage, seconds, provider_minutes=True).outcome
        is not CorroborationOutcome.CORROBORATED
    )


def candidate_recording_runtime_veto(
    candidate: ProviderCandidate,
    local_claims: tuple[LocalClaim, ...],
    audio: AudioFacts | None,
) -> bool:
    """Reject runtime contradictions unless complete endpoints account for them."""
    durations = tuple(
        claim for claim in candidate.claims if claim.kind is ClaimKind.DURATION
    )
    if not durations or len(durations) == 1 and durations[0].state is FieldState.ABSENT:
        return False
    if (
        len(durations) != 1
        or durations[0].state is not FieldState.VALID
        or candidate_runtime_seconds(candidate) is None
    ):
        return True
    coverage = local_claim_runtime_seconds(local_claims)
    if coverage is None and audio is not None and audio.chapters:
        coverage = (audio.chapters[-1].end_milliseconds + 500) // 1000
    if coverage is None or not recording_runtime_veto(candidate, coverage):
        return False
    structures = tuple(
        e.chapters
        for e in candidate.evidence
        if e.kind is ProviderEvidenceKind.CHAPTER_STRUCTURE
    )
    return not (
        audio is not None
        and len(structures) == 1
        and structures[0] is not None
        and not recording_runtime_veto(candidate, structures[0].runtime_seconds)
        and complete_chapter_runtime_agreement(audio, local_claims, structures[0])
    )


def _local_identifier_asins(local_claims: tuple[LocalClaim, ...]) -> frozenset[str]:
    """Return the canonical ASINs the item's own surfaces carry.

    A target assertion is deliberately not one of them, and neither is an
    admission carried from an earlier preparation (#1316). The tag, the
    sidecar, and the path were read off the recording; the other two were
    stated about it, and ADR-0061 keeps those apart everywhere identity is at
    stake.
    """
    return frozenset(
        value
        for claim in local_claims
        if claim.kind is ClaimKind.ASIN
        and claim.basis not in ASSERTED_ASIN_BASES
        and (value := canonical_asin(claim.canonical)) is not None
    )


def _unambiguous_candidate_asin(candidate: ProviderCandidate) -> str | None:
    """A chapter source must identify one valid recording without alias conflict."""
    identifiers = tuple(
        claim for claim in candidate.claims if claim.kind is ClaimKind.ASIN
    )
    asins = {claim.value for claim in identifiers}
    if len(asins) != 1 or any(
        claim.state is not FieldState.VALID
        or canonical_asin(claim.value) != claim.value
        for claim in identifiers
    ):
        return None
    return next(iter(asins))


def _chapter_candidate_support(
    candidate: ProviderCandidate,
    audio: AudioFacts | None,
    local_claims: tuple[LocalClaim, ...],
    title: str | None,
    authors: tuple[str, ...],
    requested_asin: str | None,
) -> bool:
    """Qualify raw recording evidence before shaping can discard its vetoes."""
    if recording_variant_conflict(candidate, local_claims, title or "") is not None:
        return False
    if not provider_candidate_exact_work_identity_admission(
        candidate, title or "", authors, audiobook=audio is not None
    ).admitted:
        return False
    if (
        requested_asin is None
        or _unambiguous_candidate_asin(candidate) != requested_asin
    ):
        return False
    structures = tuple(
        value.chapters
        for value in candidate.evidence
        if value.kind is ProviderEvidenceKind.CHAPTER_STRUCTURE
    )
    durations = tuple(
        claim for claim in candidate.claims if claim.kind is ClaimKind.DURATION
    )
    if (
        audio is None
        or len(structures) != 1
        or structures[0] is None
        or len(durations) != 1
        or not complete_chapter_runtime_agreement(audio, local_claims, structures[0])
    ):
        return False
    coverage = local_claim_runtime_seconds(local_claims)
    if coverage is None:
        coverage = (audio.chapters[-1].end_milliseconds + 500) // 1000
    # The correspondence explains endpoint differences; the provider's stated
    # runtime must still agree with its own complete chapter structure.
    return not recording_runtime_veto(candidate, structures[0].runtime_seconds)


def _stated_runtime_admitted(report: MetadataReport) -> bool:
    """Keep the existing runtime-first decision identical for both consumers."""
    return any(
        observation.status is ProviderStatus.CANDIDATES
        and runtime_corroborated_candidate(observation.candidates, report.local_claims)
        is not None
        for observation in report.providers
    )


def recording_identity_basis(report: MetadataReport) -> dict[str, str] | None:
    """Select chapter support only after every matched same-ASIN veto is checked."""
    if type(report) is not MetadataReport:
        raise CorroborationValueError("provider observation is invalid")
    if _stated_runtime_admitted(report) or report.audio is None:
        return None
    if len(_local_identifier_asins(report.local_claims)) > 1:
        return None
    audio = report.audio
    if not audio.chapters:
        return None
    coverage = local_claim_runtime_seconds(report.local_claims)
    if coverage is None:
        coverage = (audio.chapters[-1].end_milliseconds + 500) // 1000
    fields = {field.destination: field for field in report.target.fields}
    authors_value = fields[TargetDestination.AUTHORS].value
    authors = (
        cast("tuple[str, ...]", authors_value) if type(authors_value) is tuple else ()
    )
    support: list[tuple[str, str, str]] = []
    vetoed: set[str] = set()
    for observation in report.providers:
        if observation.authority not in AUDIOBOOK_PROVIDER_AUTHORITIES:
            continue
        for candidate in (
            *observation.candidates,
            *observation.recording_lookup_candidates,
        ):
            if not provider_candidate_exact_work_identity_admission(
                candidate,
                report.target.current_title or "",
                authors,
                audiobook=report.audio is not None,
            ).admitted:
                continue
            identifiers = tuple(
                claim for claim in candidate.claims if claim.kind is ClaimKind.ASIN
            )
            asins = {
                value
                for claim in identifiers
                if claim.state is FieldState.VALID
                and (value := canonical_asin(claim.value)) is not None
            }
            if len(asins) != 1:
                vetoed.update(asins)
                continue
            asin = next(iter(asins))
            if any(
                claim.state is not FieldState.VALID or claim.value != asin
                for claim in identifiers
            ) or (
                recording_runtime_veto(candidate, coverage)
                and not _chapter_candidate_support(
                    candidate,
                    audio,
                    report.local_claims,
                    report.target.current_title,
                    authors,
                    asin,
                )
            ):
                vetoed.add(asin)
                continue
            if candidate not in observation.candidates:
                continue
            retained = any(
                lane.input_references
                and (
                    lane.kind is ProviderQueryKind.WORK
                    or lane.kind is ProviderQueryKind.ASIN
                    and lane.identity
                    == discovery_lane_identity(
                        {
                            "canonical_asin": asin,
                            "input_references": list(lane.input_references),
                            "kind": ProviderQueryKind.ASIN.value,
                        }
                    )
                )
                and any(
                    value.edition_identity == candidate.identity
                    for value in lane.candidates
                )
                for lane in observation.lanes
            )
            if not observation.lanes:
                retained = bool(observation.input_references) and (
                    observation.query_kind is ProviderQueryKind.WORK
                    or observation.query_kind is ProviderQueryKind.ASIN
                    and _local_identifier_asins(
                        tuple(
                            claim
                            for claim in report.local_claims
                            if claim.reference in observation.input_references
                        )
                    )
                    == {asin}
                )
            if not retained:
                continue
            # An acquisition hint alone never supplies recording support.
            if not any(
                value.kind is ProviderEvidenceKind.ASIN_MARKETPLACE
                and value.scope is ClaimScope.EDITION
                for value in candidate.evidence
            ):
                continue
            if _chapter_candidate_support(
                candidate,
                audio,
                report.local_claims,
                report.target.current_title,
                authors,
                asin,
            ):
                support.append((observation.provider, candidate.identity, asin))
    eligible = tuple(
        (provider, identity)
        for provider, identity, asin in support
        if asin not in vetoed
    )
    if not eligible:
        return None
    provider, candidate_identity = min(eligible)
    return {
        "basis": "chapter_runtime_and_structure",
        "provider": provider,
        "candidate": candidate_identity,
    }


def admitted_audiobook_identity(report: MetadataReport) -> bool:
    """Preserve stated-runtime admission, then try complete retained chapter timing."""
    if type(report) is not MetadataReport:
        raise CorroborationValueError("provider observation is invalid")
    return (
        _stated_runtime_admitted(report) or recording_identity_basis(report) is not None
    )


def candidate_narrator_disjoint(
    candidate: ProviderCandidate, local_claims: tuple[LocalClaim, ...]
) -> bool:
    """Whether a product credits only narrators the recording's own credits lack.

    An unknown cast on either side is never disjoint, so a recording without
    local narrator credits, or a product stating none, passes (#1252, #1316).
    """
    return narrator_cast_disjoint(
        _recording_narrators(local_claims), _candidate_narrators(candidate)
    )


def _recording_narrators(local_claims: tuple[LocalClaim, ...]) -> tuple[str, ...]:
    return tuple(
        claim.canonical
        for claim in local_claims
        if claim.kind is ClaimKind.CONTRIBUTOR
        and claim.role == "narrator"
        and claim.state is FieldState.VALID
        and claim.canonical is not None
    )


def _candidate_narrators(candidate: ProviderCandidate) -> tuple[str, ...]:
    return tuple(
        claim.value
        for claim in candidate.claims
        if claim.kind is ClaimKind.CONTRIBUTOR
        and claim.role == "narrator"
        and claim.state is FieldState.VALID
        and claim.value is not None
    )


class _Signal(StrEnum):
    AGREE = "agree"
    CONTRADICT = "contradict"
    UNKNOWN = "unknown"


# Words a series name carries that say nothing about which series it is.
_SERIES_NOISE: Final = frozenset(
    {
        "the",
        "a",
        "an",
        "series",
        "saga",
        "trilogy",
        "cycle",
        "chronicles",
        "book",
        "books",
        "novel",
        "novels",
        "sequence",
    }
)
_SEQUENCE_NUMBER: Final = re.compile(r"[0-9]+(?:\.[0-9]+)?")


def _series_key(value: str) -> str:
    folded = re.sub(r"[^\w\s]|_", " ", value.casefold())
    return " ".join(token for token in folded.split() if token not in _SERIES_NOISE)


def _sequence_number(value: str | None) -> Decimal | None:
    match = None if value is None else _SEQUENCE_NUMBER.search(value)
    return None if match is None else Decimal(match.group())


def _local_series(
    local_claims: tuple[LocalClaim, ...], corroboration: RecordingCorroboration
) -> tuple[tuple[str, str | None], ...]:
    names = tuple(
        claim.canonical
        for claim in local_claims
        if claim.kind is ClaimKind.SERIES
        and claim.state is FieldState.VALID
        and claim.canonical
    )
    sequences = tuple(
        claim.canonical
        for claim in local_claims
        if claim.kind is ClaimKind.SERIES_SEQUENCE
        and claim.state is FieldState.VALID
        and claim.canonical
    )
    tagged = (
        ((names[0], sequences[0] if len(sequences) == 1 else None),)
        if len(names) == 1
        else ()
    )
    return (
        *((value.name, value.sequence) for value in corroboration.series),
        *tagged,
    )


def _candidate_series(
    candidate: ProviderCandidate,
) -> tuple[tuple[str, str | None], ...]:
    ordered = tuple(sorted(candidate.claims, key=lambda claim: claim.raw_order))
    memberships: list[tuple[str, str | None]] = []
    for index, claim in enumerate(ordered):
        if (
            claim.kind is not ClaimKind.SERIES
            or claim.state is not FieldState.VALID
            or claim.value is None
        ):
            continue
        following = ordered[index + 1] if index + 1 < len(ordered) else None
        memberships.append(
            (
                claim.value,
                following.value
                if following is not None
                and following.kind is ClaimKind.SERIES_SEQUENCE
                and following.state is FieldState.VALID
                else None,
            )
        )
    return tuple(memberships)


def _series_signal(
    local: tuple[tuple[str, str | None], ...],
    product: tuple[tuple[str, str | None], ...],
) -> _Signal:
    """Agree on one shared series number, contradict on a different one.

    Two memberships name one series only when their normalized names are
    equal. A different number on any such pair contradicts, whatever another
    pair says; otherwise an equal number on one agrees.
    """
    agree = False
    for local_name, local_sequence in local:
        for product_name, product_sequence in product:
            left, right = _series_key(local_name), _series_key(product_name)
            if not left or left != right:
                continue
            left_number = _sequence_number(local_sequence)
            right_number = _sequence_number(product_sequence)
            if left_number is None or right_number is None:
                continue
            if left_number != right_number:
                return _Signal.CONTRADICT
            agree = True
    return _Signal.AGREE if agree else _Signal.UNKNOWN


def _narrator_signal(local: tuple[str, ...], candidate: ProviderCandidate) -> _Signal:
    product = _candidate_narrators(candidate)
    if not local or not product:
        return _Signal.UNKNOWN
    if narrator_cast_disjoint(local, product):
        return _Signal.CONTRADICT
    return _Signal.AGREE


def _corroborated_distance(
    candidate: ProviderCandidate,
    title: str,
    authors: tuple[str, ...],
    local_claims: tuple[LocalClaim, ...],
    corroboration: RecordingCorroboration,
) -> int | None:
    """The tight-band distance of one product the rule admits, or ``None``."""
    local = local_claim_runtime_seconds(local_claims)
    stated = candidate_runtime_seconds(candidate)
    claims = provider_candidate_identity_title_claims(candidate)
    if (
        local is None
        or not stated
        or claims is None
        or stated_runtime_agreement(local, candidate).outcome
        is not CorroborationOutcome.CORROBORATED
        or recording_variant_conflict(candidate, local_claims, title) is not None
    ):
        return None
    provider_title, provider_subtitle, provider_authors = claims
    if not work_title_related(title, provider_title, provider_subtitle):
        return None
    # The author is the query's own term, so it only ever contradicts.
    if narrator_cast_disjoint(authors, provider_authors):
        return None
    narrators = _narrator_signal(
        (*corroboration.narrators, *_recording_narrators(local_claims)), candidate
    )
    series = _series_signal(
        _local_series(local_claims, corroboration), _candidate_series(candidate)
    )
    if _Signal.CONTRADICT in {narrators, series} or _Signal.AGREE not in {
        narrators,
        series,
    }:
        return None
    # A title that runs on past the other, on either side, is the shape of a
    # series neighbour by the same author and narrator, so only an agreeing
    # series position admits it.
    if (
        work_title_extends(
            title,
            provider_title,
            provider_subtitle,
            decorations=tuple(
                name
                for name, _ in (
                    *_local_series(local_claims, corroboration),
                    *_candidate_series(candidate),
                )
            ),
            sequences=tuple(
                sequence
                for _, sequence in _candidate_series(candidate)
                if sequence is not None
            ),
        )
        and series is not _Signal.AGREE
    ):
        return None
    return tight_runtime_distance(local, stated, provider_minutes=True)


def corroborated_recording_candidate(
    candidates: tuple[ProviderCandidate, ...],
    title: str,
    authors: tuple[str, ...],
    local_claims: tuple[LocalClaim, ...],
    corroboration: RecordingCorroboration,
) -> ProviderCandidate | None:
    """Choose the one product the corroborated admission rule admits (#1307).

    The rule reads only where exact admission admitted nothing. A product
    qualifies when its whole-minute runtime is inside the tight band, its
    title is related to the local one (`work_title_related`, the mandatory
    guard), its narrator or series number agrees, and nothing contradicts it:
    no disjoint narrator cast or author set, no different series number, and
    no recording language or format conflict. When several products qualify,
    each must state a narrator and share narrators with every other, or none
    is admitted; of those that do, the closest runtime wins, then the
    identity.
    """
    if (
        type(candidates) is not tuple
        or any(type(value) is not ProviderCandidate for value in candidates)
        or type(corroboration) is not RecordingCorroboration
    ):
        raise CorroborationValueError("corroboration input is invalid")
    qualified = tuple(
        (distance, candidate)
        for candidate in candidates
        if (
            distance := _corroborated_distance(
                candidate, title, authors, local_claims, corroboration
            )
        )
        is not None
    )
    # Several qualifying editions are one recording only when each states a
    # narrator and no two casts are disjoint: an unnarrated edition beside
    # them could be any recording, so it does not pass by saying nothing.
    if not qualified or (
        len(qualified) > 1
        and (
            any(not _candidate_narrators(candidate) for _, candidate in qualified)
            or any(
                narrator_cast_disjoint(
                    _candidate_narrators(left), _candidate_narrators(right)
                )
                for _, left in qualified
                for _, right in qualified
            )
        )
    ):
        return None
    return min(qualified, key=lambda value: (value[0], value[1].identity))[1]


def band_first_candidate(
    admitted: tuple[ProviderCandidate, ...],
    title: str,
    local_claims: tuple[LocalClaim, ...],
    corroboration: RecordingCorroboration,
) -> ProviderCandidate | None:
    """Admit the one tight product of a set of title-admitted ones (#1260, #1307).

    Every product here already passed work-identity admission one by one, but
    each Audible product is its own work identity, so two of them refuse the
    set. When exactly one is inside the tight band, every other one is beyond
    the loose band, and the tight one has no recording conflict and no
    narrator cast disjoint from the recording's own, that one is the
    recording and the rest are other cuts of the work.
    """
    if type(admitted) is not tuple or any(
        type(value) is not ProviderCandidate for value in admitted
    ):
        raise CorroborationValueError("corroboration input is invalid")
    local = local_claim_runtime_seconds(local_claims)
    if local is None or len(admitted) < 2:
        return None
    outcomes = tuple(
        (stated_runtime_agreement(local, candidate).outcome, candidate)
        for candidate in admitted
    )
    tight = tuple(
        candidate
        for outcome, candidate in outcomes
        if outcome is CorroborationOutcome.CORROBORATED
    )
    if (
        len(tight) != 1
        or sum(outcome is CorroborationOutcome.CONTRADICTED for outcome, _ in outcomes)
        != len(admitted) - 1
        or recording_variant_conflict(tight[0], local_claims, title) is not None
        or _narrator_signal(
            (*corroboration.narrators, *_recording_narrators(local_claims)), tight[0]
        )
        is _Signal.CONTRADICT
    ):
        return None
    return tight[0]


def carried_asin_claim(local_claims: tuple[LocalClaim, ...]) -> LocalClaim | None:
    """Return the one admission an earlier preparation carried forward (#1316)."""
    claims = tuple(
        claim
        for claim in local_claims
        if claim.basis == "prior_admission"
        and claim.kind is ClaimKind.ASIN
        and claim.state is FieldState.VALID
        and claim.canonical is not None
        and canonical_asin(claim.canonical) == claim.canonical
    )
    return claims[0] if len(claims) == 1 else None


def _edition_asins(candidate: ProviderCandidate) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                value
                for claim in candidate.claims
                if claim.kind is ClaimKind.ASIN
                and claim.scope is ClaimScope.EDITION
                and claim.state is FieldState.VALID
                and (value := canonical_asin(claim.value)) is not None
            }
        )
    )


def _queried_asin(lane: ProviderLaneObservation, asins: tuple[str, ...]) -> str | None:
    """Name the ASIN one product lane looked up, among the ones offered.

    A lane identity is keyed on the ASIN it asked for, so it is recovered by
    recomputing that key rather than by trusting any candidate statement.
    """
    if lane.kind is not ProviderQueryKind.ASIN:
        return None
    return next(
        (
            asin
            for asin in asins
            if lane.identity
            == discovery_lane_identity(
                {
                    "canonical_asin": asin,
                    "input_references": list(lane.input_references),
                    "kind": ProviderQueryKind.ASIN.value,
                }
            )
        ),
        None,
    )


def candidate_edition_asin(
    observation: ProviderObservation,
    candidate: ProviderCandidate,
    current: str | None = None,
    carried: str | None = None,
) -> str | None:
    """Return the one edition ASIN a candidate stands for (#1316).

    AudioSilo lists one recording under its regional and relisted ASINs, and
    those siblings are one edition rather than a choice. A candidate stating
    one ASIN stands for it; one stating several stands for `current`, the
    target's own ASIN, when that is among them, and otherwise for the ASIN its
    lane looked up. When lanes looked it up by several of its siblings and one
    of them is `carried`, the verified carried ASIN, it stands for that: the
    carried ASIN wins (ADR-0088). A candidate no lane looked up by one of its
    siblings, or looked up by several none of which is the carry, stands for
    none.
    """
    asins = _edition_asins(candidate)
    if len(asins) <= 1:
        return asins[0] if asins else None
    if current is not None and current in asins:
        return current
    queried = {
        value
        for lane in observation.lanes
        if any(
            member.edition_identity == candidate.identity for member in lane.candidates
        )
        and (value := _queried_asin(lane, asins)) is not None
    }
    if len(queried) == 1:
        return queried.pop()
    return carried if carried is not None and carried in queried else None


def verified_carried_asin(report: MetadataReport) -> str | None:
    """Return the carried ASIN when a lookup of it verified it, else None."""
    claim = carried_asin_claim(report.local_claims)
    if claim is None or not _verified_carried_candidates(report):
        return None
    return claim.canonical


def _tight_recording(
    candidate: ProviderCandidate, local_claims: tuple[LocalClaim, ...]
) -> bool:
    """A recording the stated runtime admitted inside the tight band.

    Edition marketplace evidence survives only an admission, and the tight
    band read again over the retained runtime excludes one admitted on its
    chapter structure alone.
    """
    return (
        any(
            value.kind is ProviderEvidenceKind.ASIN_MARKETPLACE
            and value.scope is ClaimScope.EDITION
            for value in candidate.evidence
        )
        and runtime_corroborated_candidate((candidate,), local_claims) is not None
    )


def _carried_lanes(
    report: MetadataReport, claim: LocalClaim
) -> tuple[tuple[ProviderObservation, ProviderLaneObservation], ...]:
    asin = cast("str", claim.canonical)
    return tuple(
        (observation, lane)
        for observation in report.providers
        if observation.authority in AUDIOBOOK_PROVIDER_AUTHORITIES
        for lane in observation.lanes
        if claim.reference in lane.input_references
        and _queried_asin(lane, (asin,)) == asin
    )


def _verified_carried_candidates(
    report: MetadataReport,
) -> tuple[ProviderCandidate, ...]:
    """Return the products a direct lookup of the carried ASIN admitted."""
    claim = carried_asin_claim(report.local_claims)
    if claim is None or report.audio is None:
        return ()
    asin = cast("str", claim.canonical)
    verified: list[ProviderCandidate] = []
    for observation, lane in _carried_lanes(report, claim):
        if lane.status is not ProviderStatus.CANDIDATES:
            continue
        by_identity = {value.identity: value for value in observation.candidates}
        for member in lane.candidates:
            candidate = by_identity.get(member.edition_identity)
            if (
                candidate is not None
                and asin in _edition_asins(candidate)
                and _tight_recording(candidate, report.local_claims)
                and not candidate_narrator_disjoint(candidate, report.local_claims)
            ):
                verified.append(candidate)
    return tuple(verified)


def target_current_asin(report: MetadataReport) -> str | None:
    """Return the canonical ASIN the target records, if it records one."""
    return next(
        (
            canonical_asin(field.value)
            for field in report.target.fields
            if field.destination is TargetDestination.ASIN
            and field.state is TargetFieldState.VALUE
            and type(field.value) is str
        ),
        None,
    )


def _admitted_candidates(
    report: MetadataReport,
) -> tuple[tuple[ProviderObservation, ProviderCandidate], ...]:
    """The tight, narrator-compatible recordings audiobook authorities admitted."""
    return tuple(
        (observation, candidate)
        for observation in report.providers
        if observation.authority in AUDIOBOOK_PROVIDER_AUTHORITIES
        and observation.status is ProviderStatus.CANDIDATES
        for candidate in observation.candidates
        if _tight_recording(candidate, report.local_claims)
        and not candidate_narrator_disjoint(candidate, report.local_claims)
    )


# A carried lookup that ended in one of these answered; any other non-disabled
# status is a failure, which never refutes a carried edition (#1316).
_DEFINITIVE_LANE_STATUSES: Final = frozenset(
    {ProviderStatus.CANDIDATES, ProviderStatus.NO_MATCH}
)


def recording_admitted(report: MetadataReport) -> bool:
    """Whether an audiobook authority admitted a recording for this report.

    It is the admission `admitted_recording_asin` carries, read without its
    carrying rules: an item whose own surfaces name an ASIN, or one whose
    admitted products disagree on the edition, still admitted a recording.
    An audiobook that admitted none is searched again on a later preparation
    (#1451).
    """
    if type(report) is not MetadataReport:
        raise CorroborationValueError("provider observation is invalid")
    return report.audio is not None and bool(_admitted_candidates(report))


def admitted_recording_asin(report: MetadataReport) -> str | None:
    """Return the one edition ASIN this report admitted, to carry forward (#1316).

    Only an audiobook with no ASIN of its own carries anything: one that has
    its identifier already looks it up. The admission is a stated runtime
    inside the tight band at an audiobook authority, with edition marketplace
    evidence and a narrator cast not disjoint from the recording's own; a
    chapter-only admission is not carried. Every admitted product must name
    the same edition, a multi-ASIN statement counting as the ASIN its lane
    looked up, or nothing is carried.

    An ASIN this report itself carried is kept while any audiobook
    authority's lookup of it verified it, and kept again unverified while at
    least one of those lookups failed (it ended in anything but products or
    no match) or none answered (none was made, or every one was disabled).
    Only when one answered definitively, none verified it and none failed is
    it refuted: it is dropped, and this report cannot admit it again.

    The owner's value is never displaced: when the target already records an
    ASIN this report admitted as a distinct product, no other edition is
    carried.
    """
    if type(report) is not MetadataReport:
        raise CorroborationValueError("provider observation is invalid")
    if report.audio is None or _local_identifier_asins(report.local_claims):
        return None
    admitted_candidates = _admitted_candidates(report)
    asin = _carried_or_admitted_asin(report, admitted_candidates)
    current = target_current_asin(report)
    if (
        asin is not None
        and current is not None
        and asin != current
        and any(
            current in (edition_asins := _edition_asins(candidate))
            and asin not in edition_asins
            for _observation, candidate in admitted_candidates
        )
    ):
        # Carrying another edition would contest the owner's admitted one
        # every preparation, and the carried edition would never settle. A
        # product stating both is one recording's siblings, not a contest.
        return None
    return asin


def _carried_or_admitted_asin(
    report: MetadataReport,
    admitted_candidates: tuple[tuple[ProviderObservation, ProviderCandidate], ...],
) -> str | None:
    carried = carried_asin_claim(report.local_claims)
    refuted: str | None = None
    if carried is not None:
        asin = cast("str", carried.canonical)
        if _verified_carried_candidates(report):
            return asin
        # Verified nowhere. Only a lane that completed answers: it returned
        # products or no match. Any other ending, recoverable or not (a
        # malformed or oversized response, a candidate limit), is a failure
        # and leaves the question open, as does no answer at all. A disabled
        # provider is not asked, so it neither keeps nor refutes.
        statuses = tuple(
            lane.status
            for _observation, lane in _carried_lanes(report, carried)
            if lane.status is not ProviderStatus.DISABLED
        )
        if not statuses or any(
            status not in _DEFINITIVE_LANE_STATUSES for status in statuses
        ):
            return asin
        refuted = asin
    admitted: set[str | None] = {
        candidate_edition_asin(observation, candidate)
        for observation, candidate in admitted_candidates
    }
    admitted.discard(refuted)
    if len(admitted) != 1:
        return None
    return admitted.pop()


def carried_asin_precedence(report: MetadataReport) -> frozenset[tuple[str, str]]:
    """Name the products withheld at the ASIN field once a carried edition is verified.

    The one place the precedence rule lives (ADR-0088, the owner's decision of
    2026-10-28): once a direct lookup verified the carried ASIN, that is the
    ASIN proposed, and a tight product naming another ASIN whose narrator cast
    is not disjoint from the carried product's is the same recording relisted
    rather than a second choice, so it is withheld. Search still runs and
    still feeds every other field.

    The owner's value is never outranked. When this report admitted the
    target's own ASIN as the recording, and that product's narrator cast is
    not disjoint from the carried product's, the owner's edition stands and it
    is the carried products that are withheld; a product naming the target's
    own ASIN is never withheld. Returned as provider name and candidate
    identity pairs.
    """
    carried = _verified_carried_candidates(report)
    claim = carried_asin_claim(report.local_claims)
    if not carried or claim is None:
        return frozenset()
    current = target_current_asin(report)
    narrators = tuple(
        value for candidate in carried for value in _candidate_narrators(candidate)
    )
    products = tuple(
        (observation, candidate)
        for observation in report.providers
        if observation.authority in AUDIOBOOK_PROVIDER_AUTHORITIES
        for candidate in observation.candidates
        if current not in _edition_asins(candidate)
    )
    owner = tuple(
        candidate
        for _observation, candidate in _admitted_candidates(report)
        if current in _edition_asins(candidate)
        and claim.canonical not in _edition_asins(candidate)
    )
    owner_narrators = tuple(
        value for candidate in owner for value in _candidate_narrators(candidate)
    )
    if owner and not narrator_cast_disjoint(narrators, owner_narrators):
        return frozenset(
            (observation.provider, candidate.identity)
            for observation, candidate in products
            if claim.canonical in _edition_asins(candidate)
        )
    return frozenset(
        (observation.provider, candidate.identity)
        for observation, candidate in products
        if claim.canonical not in _edition_asins(candidate)
        and _tight_recording(candidate, report.local_claims)
        and not narrator_cast_disjoint(narrators, _candidate_narrators(candidate))
    )
