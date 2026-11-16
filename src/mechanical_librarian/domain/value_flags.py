"""Shapes of a current value that are usually a mistake, whatever sources say.

A closed, versioned list of rules over a report's current title, authors and
narrators (#1334). Each finding names its rule and destination, never a value.
Nothing here corrects, proposes or asks: the Library lists the findings and
the owner decides. Precision comes before recall, so every rule reuses the
grammar the owner-value suggestions and the proposal refusals already apply,
narrowed where a real title could take the same shape.

Changing any rule changes what is stored for every report, so it bumps
`VALUE_FLAG_RULES_VERSION`, and the stored version refuses startup until a
clean rebuild re-derives the flags.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final, Literal

from mechanical_librarian.domain.field_value_comparison import (
    canonical_text,
    contributor_credit_prefixed,
    promotional_announcement,
    title_identity_key,
)
from mechanical_librarian.domain.human_decision import (
    PART_TOKEN,
    _cased,
    _letter,
    roman_numeral,
    single_case,
    split_author,
    split_trailing_part,
)
from mechanical_librarian.domain.metadata_report import (
    ReportValueError,
    TargetDestination,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import _TITLE_PART_BOUNDARY

VALUE_FLAG_RULES_VERSION: Final = "value-flag-rules-v1"
ValueFlagRule = Literal[
    "title_part_number",
    "title_disc_number",
    "title_is_author",
    "title_has_author",
    "title_all_capitals",
    "title_all_lower_case",
    "title_announcement",
    "credit_label",
]
ValueFlagDestination = Literal["title", "authors", "narrators"]
VALUE_FLAG_RULES: Final[tuple[ValueFlagRule, ...]] = (
    "title_part_number",
    "title_disc_number",
    "title_is_author",
    "title_has_author",
    "title_all_capitals",
    "title_all_lower_case",
    "title_announcement",
    "credit_label",
)
ValueFlag = tuple[ValueFlagDestination, ValueFlagRule]

# The whole title is one disc or part token, optionally in matching brackets.
_WHOLE_PART: Final = re.compile(
    r"(?P<open>[(\[])?" + PART_TOKEN + r"(?P<close>[)\]])?", re.IGNORECASE
)
_DISC_WORDS: Final = frozenset({"disc", "disk", "cd"})
_BRACKETS: Final = {"(": ")", "[": "]", None: None}


def _normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split())


def report_source_titles(providers: object) -> tuple[str, ...]:
    """The title every provider candidate of one report states, normalized.

    `providers` is a report document's `providers` list. A claim counts when
    its kind is `title` and its value is text; anything else is skipped. The
    stored flags (through the SQL function) and the Library row read the same
    titles through this one rule, so they cannot drift.
    """
    titles: list[str] = []
    if type(providers) is not list:
        return ()
    for provider in providers:
        candidates = provider.get("candidates") if type(provider) is dict else None
        if type(candidates) is not list:
            continue
        for candidate in candidates:
            claims = candidate.get("claims") if type(candidate) is dict else None
            if type(claims) is not list:
                continue
            for claim in claims:
                if (
                    type(claim) is dict
                    and claim.get("kind") == "title"
                    and type(value := claim.get("value")) is str
                ):
                    titles.append(_normalized(value))
    return tuple(sorted(set(titles)))


def _same_key(title: str, author: str) -> bool:
    try:
        return title_identity_key(title) == canonical_text(
            TargetDestination.AUTHORS, author
        )
    except ReportValueError:
        # Normalizing may lengthen a value past the comparison bound; such a
        # value names no author.
        return False


def _credit_prefixed(member: str) -> bool:
    try:
        return contributor_credit_prefixed(member)
    except ReportValueError:
        return False


def _qualifying_words(title: str) -> int:
    """Space-separated words with two or more cased letters, not a numeral."""
    count = 0
    for word in title.split(" "):
        letters = "".join(character for character in word if _cased(character))
        if len(letters) >= 2 and not roman_numeral(letters):
            count += 1
    return count


def _title_flags(
    title: str, authors: tuple[str, ...], source_titles: frozenset[str]
) -> set[ValueFlagRule]:
    found: set[ValueFlagRule] = set()
    whole = _WHOLE_PART.fullmatch(title)
    if whole is not None and _BRACKETS[whole.group("open")] == whole.group("close"):
        found.add("title_part_number")
    trailing = split_trailing_part(title)
    if trailing is not None and trailing[1] in _DISC_WORDS:
        found.add("title_disc_number")
    # A title a source spells the same way may really be the author's name or
    # really be published in lower case (D8). Capitals have no such exception.
    stated = title in source_titles
    if (
        not stated
        and _letter(title)
        and any(_same_key(title, author) for author in authors)
    ):
        found.add("title_is_author")
    if split_author(title, authors) is not None:
        found.add("title_has_author")
    case = single_case(title)
    if case is not None and _qualifying_words(title) >= 2:
        if case == "Lu":
            found.add("title_all_capitals")
        elif not stated:
            found.add("title_all_lower_case")
    parts = [part for part in _TITLE_PART_BOUNDARY.split(title) if part.strip()]
    if len(parts) >= 2 and any(promotional_announcement(part) for part in parts[1:]):
        found.add("title_announcement")
    return found


def value_flags(
    fields: tuple[TargetField, ...], source_titles: tuple[str, ...]
) -> tuple[ValueFlag, ...]:
    """Every (destination, rule) a report's current values meet, in rule order.

    Reads only a `title`, `authors` or `narrators` field whose state is
    `value`, and `source_titles`, the titles `report_source_titles` collects
    from the same report. Text is compared in its NFC, whitespace-collapsed
    form. At most one finding per destination and rule, so at most nine.
    """
    if type(fields) is not tuple or any(
        type(field) is not TargetField for field in fields
    ):
        raise ValueError("value flag fields are invalid")
    if type(source_titles) is not tuple or any(
        type(title) is not str for title in source_titles
    ):
        raise ValueError("value flag source titles are invalid")
    values: dict[TargetDestination, object] = {
        field.destination: field.value
        for field in fields
        if field.state is TargetFieldState.VALUE
    }
    title = values.get(TargetDestination.TITLE)
    contributors: dict[ValueFlagDestination, tuple[str, ...]] = {}
    lists: tuple[tuple[TargetDestination, ValueFlagDestination], ...] = (
        (TargetDestination.AUTHORS, "authors"),
        (TargetDestination.NARRATORS, "narrators"),
    )
    for destination, name in lists:
        members = values.get(destination)
        if type(members) is tuple:
            contributors[name] = tuple(
                _normalized(member) for member in members if type(member) is str
            )
    found: set[ValueFlag] = set()
    if type(title) is str:
        found.update(
            ("title", rule)
            for rule in _title_flags(
                _normalized(title),
                contributors.get("authors", ()),
                frozenset(_normalized(value) for value in source_titles),
            )
        )
    for name, members in contributors.items():
        if any(_credit_prefixed(member) for member in members):
            found.add((name, "credit_label"))
    destinations: tuple[ValueFlagDestination, ...] = ("title", "authors", "narrators")
    return tuple(
        sorted(
            found,
            key=lambda flag: (
                VALUE_FLAG_RULES.index(flag[1]),
                destinations.index(flag[0]),
            ),
        )
    )
