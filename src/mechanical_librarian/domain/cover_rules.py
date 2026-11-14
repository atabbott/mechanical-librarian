"""Measured cover rules over one consideration's identity-eligible set (ADR-0093).

One pure stage, per medium. It reads no reviewer observation and makes no model
or provider call. In order:

1. **Shape.** An ebook never takes square art. An audiobook takes portrait art
   only when square art at or above the floor exists, among the candidates or
   as the current cover. Landscape art is not a shape fault for either medium.
2. **Same-art collapse.** Two images are the same art when every bound below
   holds between them. The current cover's art is the candidates that match it
   directly; the other candidates of this one call group into connected
   components of the relation. Collapse removes copies only: it never admits a
   candidate or establishes identity, because publisher templates collide
   across works.
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

The current cover's read is typed (ADR-0094). A readable cover takes part
whether or not its identity is confirmed. An unknown read (a transient
Audiobookshelf answer or an unusable target value) decides nothing and keeps the
cover. An unreadable cover takes the first eligible candidate, automatically
only when its bytes can be kept for Put back or there is nothing to keep. A
current cover whose own sources name another work holds selection in every
mode, overriding any fault.

A readable current cover with no detectable issue is kept. When the fault is
the art's own (wrong shape, edition language), the first candidate in that order
outside its art is taken; only when there is none may a sharper copy of the art
be taken, recorded as ``sharper_copy``. Otherwise a sharper copy is taken when
one exists, an undersized cover may take any copy that clears the floor, and
anything else takes the first candidate outside its art. ``current_cover_protection`` then decides
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
from typing import cast

from mechanical_librarian.domain.validation import (
    boolean,
    bounded_integer,
    closed_document,
    matching,
    non_negative_integer,
    nonempty_text,
    one_of,
)

# Near-duplicate study (2026-10-24), level L2.
SAME_ART_MAX_PHASH_DISTANCE = 8
"""Largest pHash Hamming distance (of 64 bits) between two copies of one art."""

# Near-duplicate study (2026-10-24), level L2.
SAME_ART_MAX_DHASH_DISTANCE = 8
"""Largest dHash Hamming distance (of 64 bits) between two copies of one art."""

# Near-duplicate study (2026-10-24), level L2.
SAME_ART_MAX_COLOUR_DISTANCE = 0.25
"""Largest total-variation distance between two 64-bin RGB histograms."""

# Near-duplicate study (2026-10-24), level L2.
SAME_ART_MAX_LAYOUT_DISTANCE = 0.06
"""Largest mean absolute difference between two 4x4 mean-RGB layouts, 0 to 1."""

# Near-duplicate study (2026-10-24), level L2.
SAME_ART_MAX_ASPECT_DISTANCE = 0.05
"""Largest absolute log ratio between two height-to-width ratios."""

# The near-duplicate study's 1.5x area cut (owner lane decision, 2026-11-07).
SHARPER_COPY_MIN_AREA_RATIO = 1.5
"""How many times the current cover's pixel area a copy must have to replace it."""

# From the 2026-10-24 cover replay's square band.
SQUARE_MIN_ASPECT = 0.9
"""Smallest height-to-width ratio of square art."""

# From the 2026-10-24 cover replay's square band.
SQUARE_MAX_ASPECT = 1.1
"""Largest height-to-width ratio of square art; anything taller is portrait."""

COVER_FLOOR_SHORT_EDGE = 300
"""ADR-0093's floor: an image under this many pixels on its short edge fails."""

COLOUR_BINS = 64
LAYOUT_VALUES = 48

_IDENTITY_TIERS = {"verified_manifestation_match": 0, "verified_work_match": 1}
MEDIA = ("ebook", "audiobook")

_hash = matching(re.compile(r"[0-9a-f]{16}"))
_identity = one_of(*_IDENTITY_TIERS)
_medium = one_of(*MEDIA)
_dimension = bounded_integer(1)
_layout_value = bounded_integer(0, 255)
_measures_document = closed_document("colour", "dhash", "layout", "phash")


class CoverProtection(Enum):
    """The closed values of ``review.cover_current_protection``."""

    PROTECTED = "protected"
    UNPROTECTED = "unprotected"
    PROPOSALS_ONLY = "proposals_only"


class CoverIssue(Enum):
    """A detectable issue with the current cover, in reason priority order."""

    TITLE_NAMES_OTHER_WORK = "title_names_other_work"
    CURRENT_CONTRADICTED = "current_contradicted"
    UNREADABLE = "unreadable"
    WRONG_SHAPE = "wrong_shape"
    BELOW_FLOOR = "below_floor"
    EDITION_LANGUAGE = "edition_language"
    SHARPER_COPY = "sharper_copy"


EDITION_PREFERENCE = "edition_preference"
"""The reason recorded for a switch to the edition's cover, which no issue names."""

STAGE_REASONS = frozenset({*(issue.value for issue in CoverIssue), EDITION_PREFERENCE})
"""Every reason a stage outcome or an automatic cover decision may record."""


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
        # The tuple shapes and the non-empty histogram are cover-specific, so
        # they stay local; each value goes through the shared vocabulary.
        if (
            not _hash(self.phash)
            or not _hash(self.dhash)
            or type(self.colour) is not tuple
            or len(self.colour) != COLOUR_BINS
            or not all(non_negative_integer(value) for value in self.colour)
            or sum(self.colour) < 1
            or type(self.layout) is not tuple
            or len(self.layout) != LAYOUT_VALUES
            or not all(_layout_value(value) for value in self.layout)
        ):
            raise ValueError("cover measures are invalid")

    def to_data(self) -> dict[str, object]:
        return {
            "colour": list(self.colour),
            "dhash": self.dhash,
            "layout": list(self.layout),
            "phash": self.phash,
        }

    @classmethod
    def from_data(cls, value: object) -> "CoverMeasures":
        """Read the exact ``to_data`` shape; anything else is a ``ValueError``."""
        if not _measures_document(value):
            raise ValueError("cover measures are invalid")
        data = cast("dict[str, object]", value)
        colour, layout = data["colour"], data["layout"]
        if type(colour) is not list or type(layout) is not list:
            raise ValueError("cover measures are invalid")
        # The field types are checked by ``__post_init__``.
        return cls(
            data["phash"],  # type: ignore[arg-type]  # reason: __post_init__ refuses a non-str
            data["dhash"],  # type: ignore[arg-type]  # reason: __post_init__ refuses a non-str
            tuple(colour),
            tuple(layout),
        )


def _valid_dimensions(width: object, height: object) -> bool:
    return _dimension(width) and _dimension(height)


@dataclass(frozen=True, slots=True)
class CoverRuleCandidate:
    """One identity-eligible candidate with the facts the rules read."""

    reference: str
    identity: str
    width: int
    height: int
    packet_ordinal: int
    measures: CoverMeasures | None = None
    edition_outside_library_languages: bool = False
    omnibus_volume: bool = False
    title_differs: bool = False

    def __post_init__(self) -> None:
        if (
            not nonempty_text(self.reference)
            or not _identity(self.identity)
            or not _valid_dimensions(self.width, self.height)
            or not non_negative_integer(self.packet_ordinal)
            or (self.measures is not None and type(self.measures) is not CoverMeasures)
            or not boolean(self.edition_outside_library_languages)
            or not boolean(self.omnibus_volume)
            or not boolean(self.title_differs)
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


class CurrentCoverRead(Enum):
    """What reading the current cover settled (ADR-0094).

    ``UNREADABLE`` is only a terminal answer: Audiobookshelf had no image for a
    declared cover, or the decoder refused the bytes it served. ``UNKNOWN`` is
    a read that settled nothing, such as a timeout or a throttled request.
    """

    READABLE = "readable"
    UNREADABLE = "unreadable"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class CurrentCover:
    """The target's actual current cover; only a readable one has pixels.

    ``retainable`` says a replacement can keep what it replaces for Put back:
    always for a readable cover, and for an unreadable one only when its exact
    bytes are bound or there is nothing to keep. ``contradicted`` says a
    readable cover's own identity row names another work.
    """

    reference: str
    identity: str | None
    read: CurrentCoverRead
    retainable: bool
    width: int | None = None
    height: int | None = None
    measures: CoverMeasures | None = None
    edition_outside_library_languages: bool = False
    contradicted: bool = False

    def __post_init__(self) -> None:
        if (
            not nonempty_text(self.reference)
            or (self.identity is not None and not _identity(self.identity))
            or type(self.read) is not CurrentCoverRead
            or not boolean(self.retainable)
            or (self.measures is not None and type(self.measures) is not CoverMeasures)
            or not boolean(self.edition_outside_library_languages)
            or not boolean(self.contradicted)
        ):
            raise ValueError("current cover is invalid")
        if self.read is CurrentCoverRead.READABLE:
            if not _valid_dimensions(self.width, self.height) or not self.retainable:
                raise ValueError("current cover is invalid")
        elif (
            self.width is not None
            or self.height is not None
            or self.measures is not None
            or self.edition_outside_library_languages
            or self.contradicted
            or (self.read is CurrentCoverRead.UNKNOWN and self.retainable)
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
    # The issue recorded as the decision's reason: the first in priority order,
    # except that a sharper copy taken for a cover whose art has a fault is
    # recorded as ``sharper_copy``.
    reason: CoverIssue | None

    def __post_init__(self) -> None:
        if (self.reason is None) != (not self.issues) or (
            self.reason is not None and self.reason not in self.issues
        ):
            raise ValueError("cover rule reason is inconsistent")


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


def _collapse(
    eligible: list[CoverRuleCandidate],
    readable: CurrentCover | None,
) -> tuple[
    set[str], list[CoverRuleCandidate], list[SameArtGroup], CoverRuleCandidate | None
]:
    """Collapse same-art copies around a readable current cover, if any.

    Returns the candidates of the current cover's art, the sharpest copy of each
    art in rank order, the groups of two or more, and the current cover's sharper
    copy. Kept apart from ``elect_measured_covers`` so neither function passes
    the repository's 200-line limit.
    """
    current_size = None if readable is None else readable.size
    # The current cover's art is only what matches it directly, so a chain of
    # near matches never makes another art its copy.
    current_art: set[str] = set()
    if readable is not None and current_size is not None:
        current_measures = readable.measures
        current_art = {
            c.reference
            for c in eligible
            if current_measures is not None
            and c.measures is not None
            and same_art(
                current_measures, current_size, c.measures, (c.width, c.height)
            )
        }
    direct = [c for c in eligible if c.reference in current_art]
    others = [c for c in eligible if c.reference not in current_art]

    # The other candidates collapse into connected components among themselves.
    parent = list(range(len(others)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for left in range(len(others)):
        for right in range(left + 1, len(others)):
            left_measures = others[left].measures
            right_measures = others[right].measures
            if (
                left_measures is not None
                and right_measures is not None
                and same_art(
                    left_measures,
                    (others[left].width, others[left].height),
                    right_measures,
                    (others[right].width, others[right].height),
                )
            ):
                parent[find(left)] = find(right)
    components: dict[int, list[CoverRuleCandidate]] = {}
    for index, candidate in enumerate(others):
        components.setdefault(find(index), []).append(candidate)

    def sharpest(copies: list[CoverRuleCandidate]) -> CoverRuleCandidate:
        return min(copies, key=lambda c: (-(c.width * c.height), c.rank_key))

    ranked_candidates: list[CoverRuleCandidate] = []
    groups: list[SameArtGroup] = []
    for copies in components.values():
        best = sharpest(copies)
        ranked_candidates.append(best)
        if len(copies) > 1:
            groups.append(
                SameArtGroup(tuple(sorted(c.reference for c in copies)), best.reference)
            )
    sharper: CoverRuleCandidate | None = None
    if direct and readable is not None and current_size is not None:
        best = sharpest(direct)
        ranked_candidates.append(best)
        if best.width * best.height >= (
            SHARPER_COPY_MIN_AREA_RATIO * current_size[0] * current_size[1]
        ):
            sharper = best
        groups.append(
            SameArtGroup(
                tuple(sorted([readable.reference, *current_art])),
                readable.reference if sharper is None else sharper.reference,
            )
        )
    ranked_candidates.sort(key=lambda c: c.rank_key)
    groups.sort(key=lambda group: group.members)
    return current_art, ranked_candidates, groups, sharper


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
        or not _medium(medium)
        or (current is not None and type(current) is not CurrentCover)
        or type(protection) is not CoverProtection
        or not boolean(prefer_edition)
        or not boolean(title_conflict)
        or not boolean(omnibus)
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
        elif candidate.edition_outside_library_languages:
            excluded[candidate.reference] = CoverExclusion.EDITION_LANGUAGE
        elif omnibus and candidate.omnibus_volume:
            excluded[candidate.reference] = CoverExclusion.OMNIBUS_VOLUME
    takeable = [c for c in candidates if c.reference not in excluded]
    # Square art counts only when it is at or above the floor, on either side.
    square_exists = any(_is_square(c.width, c.height) for c in takeable) or (
        current_size is not None
        and _is_square(*current_size)
        and not _below_floor(*current_size)
    )

    def wrong_shape(width: int, height: int) -> bool:
        if medium == "ebook":
            return _is_square(width, height)
        return square_exists and _is_portrait(width, height)

    for candidate in takeable:
        if wrong_shape(candidate.width, candidate.height):
            excluded[candidate.reference] = CoverExclusion.WRONG_SHAPE
    eligible = [c for c in takeable if c.reference not in excluded]

    current_art, ranked_candidates, groups, sharper = _collapse(eligible, readable)
    ranked = tuple(c.reference for c in ranked_candidates)
    exclusions = tuple(sorted(excluded.items()))

    def result(
        outcome: CoverOutcome,
        choice: str | None,
        issues: tuple[CoverIssue, ...] = (),
        edition_preferred: bool = False,
        reason: CoverIssue | None = None,
    ) -> MeasuredCoverElection:
        ordered = tuple(sorted(issues, key=list(CoverIssue).index))
        return MeasuredCoverElection(
            outcome,
            choice,
            ordered,
            edition_preferred,
            ranked,
            tuple(groups),
            exclusions,
            reason if reason is not None else ordered[0] if ordered else None,
        )

    contradicted = current is not None and current.contradicted
    if title_conflict and eligible:
        differing = sum(1 for c in eligible if c.title_differs)
        if 2 * differing > len(eligible):
            held: tuple[CoverIssue, ...] = (CoverIssue.TITLE_NAMES_OTHER_WORK,)
            if contradicted:
                held = (*held, CoverIssue.CURRENT_CONTRADICTED)
            return result(CoverOutcome.HOLD, None, held)

    if current is None:
        if not ranked:
            return result(CoverOutcome.EMPTY, None)
        return result(CoverOutcome.FILL, ranked[0])

    if contradicted:
        # The current cover's own sources name another work: the owner
        # decides, whatever its faults (ADR-0094).
        return result(CoverOutcome.HOLD, None, (CoverIssue.CURRENT_CONTRADICTED,))
    if current.read is CurrentCoverRead.UNKNOWN:
        # A read that settled nothing decides nothing.
        return result(CoverOutcome.KEEP, None)
    if current.read is CurrentCoverRead.UNREADABLE:
        # Every eligible candidate is the same work by tier, so the first is
        # taken; automatically only when the replaced bytes can be kept.
        unreadable = (CoverIssue.UNREADABLE,)
        if not ranked:
            return result(CoverOutcome.KEEP, None, unreadable)
        automatic = (
            protection is not CoverProtection.PROPOSALS_ONLY and current.retainable
        )
        return result(
            CoverOutcome.REPLACE if automatic else CoverOutcome.SUGGEST,
            ranked[0],
            unreadable,
        )

    issues: list[CoverIssue] = []
    if readable is not None and current_size is not None:
        if wrong_shape(*current_size):
            issues.append(CoverIssue.WRONG_SHAPE)
        if _below_floor(*current_size):
            issues.append(CoverIssue.BELOW_FLOOR)
        if readable.edition_outside_library_languages:
            issues.append(CoverIssue.EDITION_LANGUAGE)
        if sharper is not None:
            issues.append(CoverIssue.SHARPER_COPY)

    if issues:
        reason: CoverIssue | None = None
        choice: str | None = None
        other_art = next((ref for ref in ranked if ref not in current_art), None)
        if {CoverIssue.WRONG_SHAPE, CoverIssue.EDITION_LANGUAGE} & set(issues):
            # A fault of the art itself: another copy of it does not cure it, so
            # eligible other art comes first. Only without any may a sharper
            # copy be taken, recorded as such; the art fault stays recorded.
            if other_art is not None:
                choice = other_art
                if sharper is not None:
                    issues.remove(CoverIssue.SHARPER_COPY)
            elif sharper is not None:
                choice, reason = sharper.reference, CoverIssue.SHARPER_COPY
        elif sharper is not None:
            choice = sharper.reference
        elif issues == [CoverIssue.BELOW_FLOOR]:
            # An undersized cover is cured by any copy that clears the floor.
            choice = ranked[0] if ranked else None
        else:
            choice = other_art
        if choice is None:
            return result(CoverOutcome.KEEP, None, tuple(issues))
        automatic = protection is not CoverProtection.PROPOSALS_ONLY
        return result(
            CoverOutcome.REPLACE if automatic else CoverOutcome.SUGGEST,
            choice,
            tuple(issues),
            reason=reason,
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
    "STAGE_REASONS",
    "EDITION_PREFERENCE",
    "CoverExclusion",
    "CoverIssue",
    "CoverMeasures",
    "CoverOutcome",
    "CoverProtection",
    "CoverRuleCandidate",
    "CurrentCover",
    "CurrentCoverRead",
    "MeasuredCoverElection",
    "SameArtGroup",
    "elect_measured_covers",
    "same_art",
]
