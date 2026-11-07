"""ADR-0096's work-identity levels as pure title and creator predicates.

`review.work_identity` chooses how strictly title and creator agreement binds a
provider record to the work. Each level above `identifier` adds one closed
predicate over a target's and a record's already-admitted title and creators:

- `title_and_creator_exact`: the titles are equal under ADR-0018's closed title
  comparison and the record's creator set equals the target's;
- `title_and_creator`: the titles are equal under ADR-0067's one-step
  decoration grammar and the two share at least one creator.

A side with no title or no creator never meets either predicate. Creators are
compared on their stored forms under the report's closed creator comparison
key: no comma inversion and no initials folding (#1780), so "J. R. Example"
and "J R Example" are different creators here.

The predicates cover only title and creator. Callers keep every other refusal
ADR-0096 names, at every level: an omnibus or box set against one of its
volumes, any record an existing contradiction or veto refuses (identifier,
edition, work, manifestation and creator contradictions, ADR-0018's
translation, adaptation, abridgement and variant-marker vetoes, an audiobook
recording variant conflict), and an inverted creator form only the query used
(#1380). `level_admission` takes the first two as caller-decided flags; the
creator form is the caller's to exclude before building the reference.

Callers pass length-bounded text: the stored claim or association projections
the product already bounds. The decoration grammar's patterns are proven only
on such text, and nothing here bounds it again.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from mechanical_librarian.domain.metadata_report import creator_comparison_key
from mechanical_librarian.domain.validation import boolean, exact_type
from mechanical_librarian.domain.work_identity import (
    _closed_equal as _closed_title_equal,
)
from mechanical_librarian.domain.work_identity import (
    direct_work_title_election_matches,
    work_title_has_variant_marker,
)


class WorkIdentityLevel(StrEnum):
    """The closed `review.work_identity` levels, from strictest to widest."""

    IDENTIFIER = "identifier"
    TITLE_AND_CREATOR_EXACT = "title_and_creator_exact"
    TITLE_AND_CREATOR = "title_and_creator"


@dataclass(frozen=True, slots=True)
class IdentityReference:
    """One side's stored title and creators, as the predicates compare them."""

    title: str | None
    creators: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            self.title is not None
            and type(self.title) is not str
            or type(self.creators) is not tuple
            or any(type(value) is not str for value in self.creators)
        ):
            raise ValueError("identity reference is invalid")


@dataclass(frozen=True, slots=True)
class LevelAdmission:
    """The level whose predicate admitted a record, and its lineage rule."""

    level: WorkIdentityLevel
    rule: str

    def __post_init__(self) -> None:
        if (
            not exact_type(WorkIdentityLevel)(self.level)
            or self.level is WorkIdentityLevel.IDENTIFIER
            or self.rule != f"work_identity:{self.level.value}"
        ):
            raise ValueError("work identity level admission is invalid")


def _stated_title(reference: IdentityReference) -> str | None:
    if reference.title is None or not reference.title.strip():
        return None
    return reference.title


def _creator_keys(reference: IdentityReference) -> frozenset[str]:
    return frozenset(
        creator_comparison_key(value) for value in reference.creators if value.strip()
    )


def _titles_closed_equal(left: str, right: str) -> bool:
    # ADR-0018: a variant, translation, adaptation or abridgement marker gains
    # no non-exact agreement, so such a title agrees only byte for byte.
    if work_title_has_variant_marker(left) or work_title_has_variant_marker(right):
        return left == right
    return _closed_title_equal(left, right)


def _titles_decoration_equal(left: str, right: str) -> bool:
    if _titles_closed_equal(left, right):
        return True
    if work_title_has_variant_marker(left) or work_title_has_variant_marker(right):
        return False
    for decorated, plain in ((left, right), (right, left)):
        matches = direct_work_title_election_matches(decorated, ())
        # One closed step only, as ADR-0067's equivalence class: a title the
        # grammar reads more than one way is stacked and never collapses.
        if len(matches) == 1 and _closed_title_equal(matches[0].clean_title, plain):
            return True
    return False


def title_and_creator_exact(
    target: IdentityReference, record: IdentityReference
) -> bool:
    """Closed-equal titles and equal creator sets, both sides stating each."""
    target_title, record_title = _stated_title(target), _stated_title(record)
    target_creators, record_creators = _creator_keys(target), _creator_keys(record)
    return (
        target_title is not None
        and record_title is not None
        and bool(target_creators)
        and target_creators == record_creators
        and _titles_closed_equal(target_title, record_title)
    )


def title_and_creator(target: IdentityReference, record: IdentityReference) -> bool:
    """Decoration-equal titles and at least one shared creator."""
    target_title, record_title = _stated_title(target), _stated_title(record)
    return (
        target_title is not None
        and record_title is not None
        and bool(_creator_keys(target) & _creator_keys(record))
        and _titles_decoration_equal(target_title, record_title)
    )


def admitting_level(
    level: WorkIdentityLevel, target: IdentityReference, record: IdentityReference
) -> WorkIdentityLevel | None:
    """Return the strictest level at or below `level` the pair meets.

    `identifier` adds no predicate, so it never admits here.
    """
    if not exact_type(WorkIdentityLevel)(level):
        raise ValueError("work identity level is invalid")
    if level is WorkIdentityLevel.IDENTIFIER:
        return None
    if title_and_creator_exact(target, record):
        return WorkIdentityLevel.TITLE_AND_CREATOR_EXACT
    if level is WorkIdentityLevel.TITLE_AND_CREATOR and title_and_creator(
        target, record
    ):
        return WorkIdentityLevel.TITLE_AND_CREATOR
    return None


def level_admission(
    level: WorkIdentityLevel,
    target_title: str | None,
    target_creators: tuple[str, ...],
    record_title: str | None,
    record_creators: tuple[str, ...],
    *,
    omnibus_against_volume: bool,
    contradicted: bool,
) -> LevelAdmission | None:
    """Apply the caller's refusals, then `admitting_level`, with its rule."""
    if (
        not exact_type(WorkIdentityLevel)(level)
        or not boolean(omnibus_against_volume)
        or not boolean(contradicted)
    ):
        raise ValueError("work identity level admission input is invalid")
    if omnibus_against_volume or contradicted:
        return None
    admitted = admitting_level(
        level,
        IdentityReference(target_title, target_creators),
        IdentityReference(record_title, record_creators),
    )
    if admitted is None:
        return None
    return LevelAdmission(admitted, f"work_identity:{admitted.value}")


__all__ = [
    "IdentityReference",
    "LevelAdmission",
    "WorkIdentityLevel",
    "admitting_level",
    "level_admission",
    "title_and_creator",
    "title_and_creator_exact",
]
