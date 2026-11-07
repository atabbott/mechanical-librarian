"""Measured cover rules over one consideration's identity-eligible set (ADR-0093).

One pure stage, per medium. It reads no reviewer observation and makes no model
or provider call. In order:

1. **Shape.** An ebook never takes square art. An audiobook takes portrait art
   only when square art it could take exists (a square current cover counts).
   Landscape art is not a shape fault for either medium.
2. **Same-art collapse.** Two images are the same art when every bound below
   holds between them; a group is a connected component of that relation over
   the current cover and the eligible candidates of this one call. Collapse
   removes copies only: it never admits a candidate or establishes identity,
   because publisher templates collide across works.
3. **Resolution.** Decides only between copies of one art, by pixel area. A copy
   replaces the current cover only when it is larger by the materiality margin,
   so near-equal copies do not churn.
4. **Size floor.** Between different arts the only size rule is the floor on the
   short edge.
5. **Omnibus.** When the book is an omnibus, one member volume's cover is never
   eligible.

What remains keeps ADR-0059's order with its portrait-sane step replaced by the
shape rule: verified manifestation before verified work, then pixel area, width,
height, source precedence and the opaque reference.

A readable current cover with no detectable issue is kept. A current cover with
an issue is replaced by the first candidate in that order, except that a sharper
copy of the same art takes that copy. ``current_cover_protection`` then decides
whether a replacement is automatic or a suggestion. A library title that names
another work holds selection in every mode.

The same-art bounds and the shape band come from the 2026-10-24 near-duplicate
study of the owner's labelled covers: pHash and dHash Hamming distances, the
total-variation distance of 4x4x4 RGB histograms, the mean absolute difference
of a 4x4-cell mean-RGB layout (both on a 0 to 1 scale), and the absolute log
ratio of the two aspect ratios.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum

# TODO(#1363 Q1): value from the near-duplicate study
SAME_ART_MAX_PHASH_DISTANCE = 8
"""Largest pHash Hamming distance (of 64 bits) between two copies of one art."""

# TODO(#1363 Q1): value from the near-duplicate study
SAME_ART_MAX_DHASH_DISTANCE = 8
"""Largest dHash Hamming distance (of 64 bits) between two copies of one art."""

# TODO(#1363 Q1): value from the near-duplicate study
SAME_ART_MAX_COLOUR_DISTANCE = 0.25
"""Largest total-variation distance between two 64-bin RGB histograms."""

# TODO(#1363 Q1): value from the near-duplicate study
SAME_ART_MAX_LAYOUT_DISTANCE = 0.06
"""Largest mean absolute difference between two 4x4 mean-RGB layouts, 0 to 1."""

# TODO(#1363 Q1): value from the near-duplicate study
SAME_ART_MAX_ASPECT_DISTANCE = 0.05
"""Largest absolute log ratio between two height-to-width ratios."""

# TODO(#1363 Q1): value from the near-duplicate study
SHARPER_COPY_MIN_AREA_RATIO = 1.5
"""How many times the current cover's pixel area a copy must have to replace it."""

# TODO(#1363 Q1): value from the near-duplicate study
SQUARE_MIN_ASPECT = 0.9
"""Smallest height-to-width ratio of square art."""

# TODO(#1363 Q1): value from the near-duplicate study
SQUARE_MAX_ASPECT = 1.1
"""Largest height-to-width ratio of square art; anything taller is portrait."""

COVER_FLOOR_SHORT_EDGE = 300
"""ADR-0093's floor: an image under this many pixels on its short edge fails."""

COLOUR_BINS = 64
LAYOUT_VALUES = 48

_HASH = re.compile(r"^[0-9a-f]{16}$")
_IDENTITY_TIERS = {"verified_manifestation_match": 0, "verified_work_match": 1}
MEDIA = ("ebook", "audiobook")


class CoverProtection(Enum):
    """The closed values of ``review.cover_current_protection``."""

    PROTECTED = "protected"
    UNPROTECTED = "unprotected"
    PROPOSALS_ONLY = "proposals_only"


class CoverIssue(Enum):
    """A detectable issue with the current cover, in reason priority order."""

    TITLE_NAMES_OTHER_WORK = "title_names_other_work"
    UNREADABLE = "unreadable"
    WRONG_SHAPE = "wrong_shape"
    BELOW_FLOOR = "below_floor"
    EDITION_LANGUAGE = "edition_language"
    SHARPER_COPY = "sharper_copy"


class CoverExclusion(Enum):
    """Why a candidate left the eligible set.

    Floor, edition language and omnibus are checked first, in that order, and
    the first failure is kept. Shape is checked last over what remains, because
    an audiobook's portrait art is wrong only when takeable square art exists.
    """

    WRONG_SHAPE = "wrong_shape"
    BELOW_FLOOR = "below_floor"
    EDITION_LANGUAGE = "edition_language"
    OMNIBUS_VOLUME = "omnibus_volume"


class CoverOutcome(Enum):
    """What the stage decides for one cover field."""

    KEEP = "keep"
    REPLACE = "replace"
    SUGGEST = "suggest"
    HOLD = "hold"
    FILL = "fill"
    EMPTY = "empty"


@dataclass(frozen=True, slots=True)
class CoverMeasures:
    """Same-art measures computed from decoded pixels, never from stated sizes."""

    phash: str
    dhash: str
    colour: tuple[int, ...]
    layout: tuple[int, ...]

    def __post_init__(self) -> None:
        if (
            type(self.phash) is not str
            or _HASH.fullmatch(self.phash) is None
            or type(self.dhash) is not str
            or _HASH.fullmatch(self.dhash) is None
            or type(self.colour) is not tuple
            or len(self.colour) != COLOUR_BINS
            or any(type(value) is not int or value < 0 for value in self.colour)
            or sum(self.colour) < 1
            or type(self.layout) is not tuple
            or len(self.layout) != LAYOUT_VALUES
            or any(
                type(value) is not int or not 0 <= value <= 255 for value in self.layout
            )
        ):
            raise ValueError("cover measures are invalid")


def _valid_dimensions(width: object, height: object) -> bool:
    return type(width) is int and type(height) is int and width >= 1 and height >= 1


@dataclass(frozen=True, slots=True)
class CoverRuleCandidate:
    """One identity-eligible candidate with the facts the rules read."""

    reference: str
    identity: str
    width: int
    height: int
    packet_ordinal: int
    measures: CoverMeasures | None = None
    non_english_edition: bool = False
    omnibus_volume: bool = False
    title_differs: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.reference) is not str
            or not self.reference
            or self.identity not in _IDENTITY_TIERS
            or not _valid_dimensions(self.width, self.height)
            or type(self.packet_ordinal) is not int
            or self.packet_ordinal < 0
            or (self.measures is not None and type(self.measures) is not CoverMeasures)
            or type(self.non_english_edition) is not bool
            or type(self.omnibus_volume) is not bool
            or type(self.title_differs) is not bool
        ):
            raise ValueError("cover rule candidate is invalid")

    @property
    def rank_key(self) -> tuple[int, int, int, int, int, str]:
        """ADR-0059's order without its portrait-sane step; smaller is better."""
        return (
            _IDENTITY_TIERS[self.identity],
            -(self.width * self.height),
            -self.width,
            -self.height,
            self.packet_ordinal,
            self.reference,
        )


@dataclass(frozen=True, slots=True)
class CurrentCover:
    """The target's actual current cover; an unreadable one has no pixels."""

    reference: str
    identity: str | None
    readable: bool
    width: int | None = None
    height: int | None = None
    measures: CoverMeasures | None = None
    non_english_edition: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.reference) is not str
            or not self.reference
            or (self.identity is not None and self.identity not in _IDENTITY_TIERS)
            or type(self.readable) is not bool
            or (self.measures is not None and type(self.measures) is not CoverMeasures)
            or type(self.non_english_edition) is not bool
        ):
            raise ValueError("current cover is invalid")
        if self.readable:
            if not _valid_dimensions(self.width, self.height):
                raise ValueError("current cover is invalid")
        elif (
            self.width is not None
            or self.height is not None
            or self.measures is not None
            or self.non_english_edition
        ):
            raise ValueError("current cover is invalid")

    @property
    def size(self) -> tuple[int, int] | None:
        """The decoded width and height, or ``None`` when unreadable."""
        if self.width is None or self.height is None:
            return None
        return (self.width, self.height)


@dataclass(frozen=True, slots=True)
class SameArtGroup:
    """Two or more images of one art; the representative stands for the art."""

    members: tuple[str, ...]
    representative: str


@dataclass(frozen=True, slots=True)
class MeasuredCoverElection:
    """The stage's decision and the facts behind it, all closed values."""

    outcome: CoverOutcome
    choice: str | None
    issues: tuple[CoverIssue, ...]
    edition_preferred: bool
    ranked: tuple[str, ...]
    groups: tuple[SameArtGroup, ...]
    excluded: tuple[tuple[str, CoverExclusion], ...]

    @property
    def reason(self) -> CoverIssue | None:
        """The issue recorded on the decision: the first in priority order."""
        return self.issues[0] if self.issues else None


def _hamming(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def _colour_distance(left: tuple[int, ...], right: tuple[int, ...]) -> float:
    left_total, right_total = sum(left), sum(right)
    return 0.5 * sum(
        abs(a / left_total - b / right_total) for a, b in zip(left, right, strict=True)
    )


def _layout_distance(left: tuple[int, ...], right: tuple[int, ...]) -> float:
    return sum(abs(a - b) for a, b in zip(left, right, strict=True)) / (
        255 * LAYOUT_VALUES
    )


def _aspect(width: int, height: int) -> float:
    return height / width


def same_art(
    left: CoverMeasures,
    left_size: tuple[int, int],
    right: CoverMeasures,
    right_size: tuple[int, int],
) -> bool:
    """Whether every same-art bound holds between two measured images."""
    return (
        _hamming(left.phash, right.phash) <= SAME_ART_MAX_PHASH_DISTANCE
        and _hamming(left.dhash, right.dhash) <= SAME_ART_MAX_DHASH_DISTANCE
        and _colour_distance(left.colour, right.colour) <= SAME_ART_MAX_COLOUR_DISTANCE
        and _layout_distance(left.layout, right.layout) <= SAME_ART_MAX_LAYOUT_DISTANCE
        and abs(math.log(_aspect(*left_size) / _aspect(*right_size)))
        <= SAME_ART_MAX_ASPECT_DISTANCE
    )


def _is_square(width: int, height: int) -> bool:
    return SQUARE_MIN_ASPECT <= _aspect(width, height) <= SQUARE_MAX_ASPECT


def _is_portrait(width: int, height: int) -> bool:
    return _aspect(width, height) > SQUARE_MAX_ASPECT


def _below_floor(width: int, height: int) -> bool:
    return min(width, height) < COVER_FLOOR_SHORT_EDGE


def elect_measured_covers(
    candidates: tuple[CoverRuleCandidate, ...],
    *,
    medium: str,
    current: CurrentCover | None,
    protection: CoverProtection,
    prefer_edition: bool,
    title_conflict: bool,
    omnibus: bool,
) -> MeasuredCoverElection:
    """Decide one cover field from its identity-eligible candidates.

    ``current`` is ``None`` for a known-empty target. ``title_conflict`` is true
    when the proposal set's title result is an unresolved choice with a material
    title conflict; ``omnibus`` is true when EPUB opening evidence records omnibus
    membership for the book.
    """
    if (
        type(candidates) is not tuple
        or any(type(candidate) is not CoverRuleCandidate for candidate in candidates)
        or medium not in MEDIA
        or (current is not None and type(current) is not CurrentCover)
        or type(protection) is not CoverProtection
        or type(prefer_edition) is not bool
        or type(title_conflict) is not bool
        or type(omnibus) is not bool
    ):
        raise ValueError("cover rule input is invalid")
    references = [candidate.reference for candidate in candidates]
    if current is not None:
        references.append(current.reference)
    if len(set(references)) != len(references):
        raise ValueError("cover rule references are not unique")

    current_size = None if current is None else current.size
    readable = current if current_size is not None else None

    # Checks other than shape, which itself depends on what else is takeable.
    excluded: dict[str, CoverExclusion] = {}
    for candidate in candidates:
        if _below_floor(candidate.width, candidate.height):
            excluded[candidate.reference] = CoverExclusion.BELOW_FLOOR
        elif candidate.non_english_edition:
            excluded[candidate.reference] = CoverExclusion.EDITION_LANGUAGE
        elif omnibus and candidate.omnibus_volume:
            excluded[candidate.reference] = CoverExclusion.OMNIBUS_VOLUME
    takeable = [c for c in candidates if c.reference not in excluded]
    square_exists = any(_is_square(c.width, c.height) for c in takeable) or (
        current_size is not None and _is_square(*current_size)
    )

    def wrong_shape(width: int, height: int) -> bool:
        if medium == "ebook":
            return _is_square(width, height)
        return square_exists and _is_portrait(width, height)

    for candidate in takeable:
        if wrong_shape(candidate.width, candidate.height):
            excluded[candidate.reference] = CoverExclusion.WRONG_SHAPE
    eligible = [c for c in takeable if c.reference not in excluded]

    # Same-art groups: connected components over measured images.
    members: list[tuple[str, CoverMeasures | None, tuple[int, int]]] = [
        (c.reference, c.measures, (c.width, c.height)) for c in eligible
    ]
    if readable is not None and current_size is not None:
        members.insert(0, (readable.reference, readable.measures, current_size))
    # Every bound is symmetric, so the components do not depend on input order.
    parent = list(range(len(members)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for left in range(len(members)):
        for right in range(left + 1, len(members)):
            left_measures, right_measures = members[left][1], members[right][1]
            if (
                left_measures is not None
                and right_measures is not None
                and same_art(
                    left_measures, members[left][2], right_measures, members[right][2]
                )
            ):
                parent[find(left)] = find(right)
    components: dict[int, list[int]] = {}
    for index in range(len(members)):
        components.setdefault(find(index), []).append(index)

    by_reference = {c.reference: c for c in eligible}
    ranked_candidates: list[CoverRuleCandidate] = []
    groups: list[SameArtGroup] = []
    current_art: set[str] = set()
    sharper: CoverRuleCandidate | None = None
    for indices in components.values():
        group = [members[index][0] for index in indices]
        copies = [by_reference[ref] for ref in group if ref in by_reference]
        best = (
            min(copies, key=lambda c: (-(c.width * c.height), c.rank_key))
            if copies
            else None
        )
        if best is not None:
            ranked_candidates.append(best)
        representative = best.reference if best is not None else group[0]
        if (
            readable is not None
            and current_size is not None
            and readable.reference in group
        ):
            current_art = set(group)
            if best is not None and best.width * best.height >= (
                SHARPER_COPY_MIN_AREA_RATIO * current_size[0] * current_size[1]
            ):
                sharper = best
            else:
                representative = readable.reference
        if len(group) > 1:
            groups.append(SameArtGroup(tuple(sorted(group)), representative))
    ranked_candidates.sort(key=lambda c: c.rank_key)
    ranked = tuple(c.reference for c in ranked_candidates)
    groups.sort(key=lambda group: group.members)
    exclusions = tuple(sorted(excluded.items()))

    def result(
        outcome: CoverOutcome,
        choice: str | None,
        issues: tuple[CoverIssue, ...] = (),
        edition_preferred: bool = False,
    ) -> MeasuredCoverElection:
        return MeasuredCoverElection(
            outcome,
            choice,
            tuple(sorted(issues, key=list(CoverIssue).index)),
            edition_preferred,
            ranked,
            tuple(groups),
            exclusions,
        )

    if title_conflict and eligible:
        differing = sum(1 for c in eligible if c.title_differs)
        if 2 * differing > len(eligible):
            return result(CoverOutcome.HOLD, None, (CoverIssue.TITLE_NAMES_OTHER_WORK,))

    if current is None:
        if not ranked:
            return result(CoverOutcome.EMPTY, None)
        return result(CoverOutcome.FILL, ranked[0])

    issues: list[CoverIssue] = []
    if readable is None or current_size is None:
        issues.append(CoverIssue.UNREADABLE)
    else:
        if wrong_shape(*current_size):
            issues.append(CoverIssue.WRONG_SHAPE)
        if _below_floor(*current_size):
            issues.append(CoverIssue.BELOW_FLOOR)
        if readable.non_english_edition:
            issues.append(CoverIssue.EDITION_LANGUAGE)
        if sharper is not None:
            issues.append(CoverIssue.SHARPER_COPY)

    if issues:
        choice = (
            sharper.reference if sharper is not None else ranked[0] if ranked else None
        )
        if choice is None:
            return result(CoverOutcome.KEEP, None, tuple(issues))
        # An unreadable cover cannot be retained for Put back, so it is never
        # switched automatically (#1363 S7).
        automatic = (
            protection is not CoverProtection.PROPOSALS_ONLY
            and CoverIssue.UNREADABLE not in issues
        )
        return result(
            CoverOutcome.REPLACE if automatic else CoverOutcome.SUGGEST,
            choice,
            tuple(issues),
        )

    if prefer_edition and current.identity == "verified_work_match":
        edition = next(
            (
                c.reference
                for c in ranked_candidates
                if c.identity == "verified_manifestation_match"
                and c.reference not in current_art
            ),
            None,
        )
        if edition is not None:
            outcome = (
                CoverOutcome.REPLACE
                if protection is CoverProtection.UNPROTECTED
                else CoverOutcome.SUGGEST
            )
            return result(outcome, edition, edition_preferred=True)
    return result(CoverOutcome.KEEP, None)


__all__ = [
    "COLOUR_BINS",
    "COVER_FLOOR_SHORT_EDGE",
    "LAYOUT_VALUES",
    "MEDIA",
    "SAME_ART_MAX_ASPECT_DISTANCE",
    "SAME_ART_MAX_COLOUR_DISTANCE",
    "SAME_ART_MAX_DHASH_DISTANCE",
    "SAME_ART_MAX_LAYOUT_DISTANCE",
    "SAME_ART_MAX_PHASH_DISTANCE",
    "SHARPER_COPY_MIN_AREA_RATIO",
    "SQUARE_MAX_ASPECT",
    "SQUARE_MIN_ASPECT",
    "CoverExclusion",
    "CoverIssue",
    "CoverMeasures",
    "CoverOutcome",
    "CoverProtection",
    "CoverRuleCandidate",
    "CurrentCover",
    "MeasuredCoverElection",
    "SameArtGroup",
    "elect_measured_covers",
    "same_art",
]
