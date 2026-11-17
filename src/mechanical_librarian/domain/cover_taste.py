"""The owner's cover taste profile: four sections of bounded, numbered rules.

A profile is data the builder model wrote from the owner's recorded cover
choices (ADR-0095), with the owner's own rules and edits kept word for word.
It is refused, never truncated or repaired, when any bound fails, so an
admitted model output is always exactly what the model returned.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass, replace
from typing import Final, Literal, cast, get_args

from mechanical_librarian.domain.evidence_bounds import (
    EvidenceValueError,
    validate_projection_text,
)
from mechanical_librarian.domain.validation import owner_text_value

CoverTasteProfileSchemaVersion = Literal["cover-taste-profile-v2"]
COVER_TASTE_PROFILE_SCHEMA: Final[CoverTasteProfileSchemaVersion] = (
    "cover-taste-profile-v2"
)
CoverTasteVerdictSchemaVersion = Literal["cover-taste-verdict-v1"]
COVER_TASTE_VERDICT_SCHEMA: Final[CoverTasteVerdictSchemaVersion] = (
    "cover-taste-verdict-v1"
)

TasteSection = Literal["drawn_to", "avoid", "trade_offs", "keep_or_switch"]
TASTE_SECTIONS: Final[tuple[TasteSection, ...]] = get_args(TasteSection)
# A model rule the owner edited is `edited`; a rule the owner added is `owner`.
TasteProvenance = Literal["model", "edited", "owner"]
_PROVENANCES: Final[tuple[TasteProvenance, ...]] = get_args(TasteProvenance)
# The closed refusals of an owner edit, in precedence order (#1372 PR 2 S10).
TasteEditRefusal = Literal[
    "rule_empty", "rule_too_long", "rule_text", "stale", "rule_limit", "profile_full"
]
TasteEditAction = Literal["edit", "add", "remove"]
# The closed failure codes a build may end with (#1372 S12).
TasteBuildFailure = Literal[
    "unavailable",
    "invalid_output",
    "ceiling",
    "unknown_charge",
    "interrupted",
    "unconfigured",
]

MAX_TASTE_RULES: Final = 40
MAX_TASTE_RULE_CODE_POINTS: Final = 400
MAX_TASTE_PROFILE_WORDS: Final = 1_200
MAX_TASTE_PROFILE_CODE_POINTS: Final = 8_192
MAX_TASTE_RULE_NUMBER: Final = 9_999
# The texts the owner removed or replaced, newest first (#1372 PR 2 S4).
MAX_TASTE_REMOVED: Final = 100
MAX_TASTE_REMOVED_CODE_POINTS: Final = 16_384

# Cadence and input bounds (ADR-0095, #1372 S2-S4, S9, R1, R2).
TASTE_FIRST_BUILD_DECISIONS: Final = 25
TASTE_REBUILD_DECISIONS: Final = 10
TASTE_SCHEDULED_BUILDS_PER_DAY: Final = 4
TASTE_MAX_DECISIONS: Final = 50
TASTE_PREVIEWS_PER_DECISION: Final = 4
TASTE_MAX_IMAGES: Final = 100
TASTE_MAX_REQUEST_BYTES: Final = 24_000_000
TASTE_MIN_ARTS: Final = 2

# The taste judge (#1372 PR 4, J1-J7): one closed verdict per eligible cover
# field and profile version, over the current cover and at most 12 other arts.
TasteJudgeDecision = Literal["keep_current", "choose", "abstain"]
# The closed failure codes a judgement may end with, a subset of S12's.
TasteJudgeFailure = Literal[
    "unavailable", "invalid_output", "unknown_charge", "interrupted"
]
TASTE_JUDGE_MAX_ALTERNATIVES: Final = 12
TASTE_JUDGE_MAX_RULES: Final = 3
# A verdict at or above this confidence is labelled Strong, never admitted by it.
TASTE_STRONG_CONFIDENCE: Final = 90
# A profile version stands this long before it is judged under, so a burst of
# owner edits costs one pass.
TASTE_JUDGE_SETTLE_SECONDS: Final = 120
# After a provider outage judging pauses this long, so it costs a few
# requests an hour rather than a whole pass.
TASTE_JUDGE_OUTAGE_PAUSE_SECONDS: Final = 600
# How often a moving pass tells an open Covers view to read again.
TASTE_JUDGE_NOTICE_SECONDS: Final = 10


class CoverTasteError(ValueError):
    """A value-free refusal of a profile or its builder output."""


class TasteEditError(CoverTasteError):
    """A value-free refusal of one owner edit, named by its closed code."""

    def __init__(self, code: TasteEditRefusal) -> None:
        super().__init__(code)
        self.code: TasteEditRefusal = code


def taste_rule_text(value: object) -> bool:
    """Whether one rule passes the owner-entered text rule within 400 code points."""
    if (
        not owner_text_value(value)
        or len(cast(str, value)) > MAX_TASTE_RULE_CODE_POINTS
    ):
        return False
    try:
        validate_projection_text(value, "taste rule", MAX_TASTE_RULE_CODE_POINTS)
    except EvidenceValueError:
        return False
    return True


def taste_rule_key(text: str) -> str:
    """The key under which two rule texts count as the same rule (S6).

    Stored text is already NFC with whitespace collapsed, so case, width forms
    and a trailing full stop are all that set two spellings apart.
    """
    return unicodedata.normalize("NFKC", text).casefold().rstrip(". ")


def taste_removed_valid(value: object) -> bool:
    """Whether a removed list is within its bounds, each text a rule's (S4)."""
    if type(value) is not tuple and type(value) is not list:
        return False
    texts = cast("tuple[object, ...]", tuple(value))
    return (
        len(texts) <= MAX_TASTE_REMOVED
        and all(taste_rule_text(text) for text in texts)
        and sum(len(cast(str, text)) for text in texts) <= MAX_TASTE_REMOVED_CODE_POINTS
        and len({taste_rule_key(cast(str, text)) for text in texts}) == len(texts)
    )


def taste_budget(texts: tuple[str, ...]) -> dict[str, int]:
    """What the profile's bounds leave once these rules are kept (S8)."""
    return {
        "characters": MAX_TASTE_PROFILE_CODE_POINTS - sum(len(text) for text in texts),
        "rules": MAX_TASTE_RULES - len(texts),
        "words": MAX_TASTE_PROFILE_WORDS - sum(len(text.split()) for text in texts),
    }


@dataclass(frozen=True, slots=True)
class TasteRule:
    number: int
    section: TasteSection
    text: str
    provenance: TasteProvenance = "model"

    def to_data(self) -> dict[str, object]:
        return {
            "number": self.number,
            "provenance": self.provenance,
            "section": self.section,
            "text": self.text,
        }


@dataclass(frozen=True, slots=True)
class TasteProfile:
    """One admitted profile; construct it only through the two readers below."""

    rules: tuple[TasteRule, ...]

    def __post_init__(self) -> None:
        if (
            type(self.rules) is not tuple
            or len(self.rules) > MAX_TASTE_RULES
            or any(type(rule) is not TasteRule for rule in self.rules)
            or not all(taste_rule_text(rule.text) for rule in self.rules)
        ):
            raise CoverTasteError("taste profile is outside its bounds")
        numbers = [rule.number for rule in self.rules]
        budget = taste_budget(tuple(rule.text for rule in self.rules))
        if (
            any(
                type(number) is not int or not 1 <= number <= MAX_TASTE_RULE_NUMBER
                for number in numbers
            )
            or len(set(numbers)) != len(numbers)
            or [rule.section for rule in self.rules]
            != sorted((rule.section for rule in self.rules), key=TASTE_SECTIONS.index)
            or any(rule.provenance not in _PROVENANCES for rule in self.rules)
            or budget["words"] < 0
            or budget["characters"] < 0
        ):
            raise CoverTasteError("taste profile is outside its bounds")

    @classmethod
    def from_output(cls, value: object) -> TasteProfile:
        """Admit the builder's closed output, numbering rules in section order."""
        if (
            type(value) is not dict
            or set(value) != {"sections"}
            or type(value["sections"]) is not dict
            or set(value["sections"]) != set(TASTE_SECTIONS)
        ):
            raise CoverTasteError("taste profile output is not the closed schema")
        sections = cast("dict[str, object]", value["sections"])
        rules: list[TasteRule] = []
        for section in TASTE_SECTIONS:
            texts = sections[section]
            if type(texts) is not list or any(type(text) is not str for text in texts):
                raise CoverTasteError("taste profile output is not the closed schema")
            rules.extend(
                TasteRule(len(rules) + 1, section, text)
                for text in cast("list[str]", texts)
            )
            if len(rules) > MAX_TASTE_RULES:
                raise CoverTasteError("taste profile is outside its bounds")
        return cls(tuple(rules))

    @classmethod
    def from_rules(cls, value: object) -> TasteProfile:
        """Read stored rules back, refusing anything a build or edit could not write."""
        if type(value) is not list or any(
            type(rule) is not dict
            or set(rule) != {"number", "provenance", "section", "text"}
            or type(rule["number"]) is not int
            or rule["section"] not in TASTE_SECTIONS
            or type(rule["text"]) is not str
            or rule["provenance"] not in _PROVENANCES
            for rule in value
        ):
            raise CoverTasteError("taste profile rules are invalid")
        return cls(
            tuple(
                TasteRule(
                    cast(int, rule["number"]),
                    cast(TasteSection, rule["section"]),
                    cast(str, rule["text"]),
                    cast(TasteProvenance, rule["provenance"]),
                )
                for rule in cast("list[dict[str, object]]", value)
            )
        )

    def kept(self) -> tuple[TasteRule, ...]:
        """The owner's and edited rules, which every rebuild keeps as written."""
        return tuple(rule for rule in self.rules if rule.provenance != "model")

    def rules_data(self) -> list[dict[str, object]]:
        return [rule.to_data() for rule in self.rules]

    @property
    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(
                self.rules_data(),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()


def _admit_edit(rules: list[TasteRule]) -> TasteProfile:
    try:
        return TasteProfile(tuple(rules))
    except CoverTasteError:
        raise TasteEditError("profile_full") from None


def _remember(removed: tuple[str, ...], text: str) -> tuple[str, ...]:
    """Put a removed text first, dropping the oldest past the S4 bounds."""
    key = taste_rule_key(text)
    texts = [text, *(entry for entry in removed if taste_rule_key(entry) != key)]
    while (
        len(texts) > MAX_TASTE_REMOVED
        or sum(len(entry) for entry in texts) > MAX_TASTE_REMOVED_CODE_POINTS
    ):
        texts.pop()
    return tuple(texts)


def _forget(removed: tuple[str, ...], text: str) -> tuple[str, ...]:
    """Unlist a removed text the owner writes again, so none is kept and dropped."""
    key = taste_rule_key(text)
    return tuple(entry for entry in removed if taste_rule_key(entry) != key)


def apply_taste_edit(
    profile: TasteProfile | None,
    removed: tuple[str, ...],
    action: TasteEditAction,
    number: int | None,
    section: TasteSection | None,
    text: str | None,
) -> tuple[TasteProfile, tuple[str, ...]] | None:
    """One owner edit of the newest profile; None when the text is unchanged.

    `profile` is None when the edit named a version that is not the newest
    (or there is none), so it is stale. Refusals follow S10's precedence: the
    text first, then stale, the rule limit, and the profile's bounds.
    """
    if action != "remove":
        if text is None or not text.split():
            raise TasteEditError("rule_empty")
        if len(text) > MAX_TASTE_RULE_CODE_POINTS:
            raise TasteEditError("rule_too_long")
        if not taste_rule_text(text):
            raise TasteEditError("rule_text")
    if profile is None:
        raise TasteEditError("stale")
    rules = list(profile.rules)
    if action == "add":
        if len(rules) >= MAX_TASTE_RULES:
            raise TasteEditError("rule_limit")
        numbers = {rule.number for rule in rules}
        new = max(numbers, default=0) + 1
        if new > MAX_TASTE_RULE_NUMBER:
            new = min(set(range(1, MAX_TASTE_RULE_NUMBER + 1)) - numbers)
        order = TASTE_SECTIONS.index(cast(TasteSection, section))
        at = sum(1 for rule in rules if TASTE_SECTIONS.index(rule.section) <= order)
        rules.insert(
            at, TasteRule(new, cast(TasteSection, section), cast(str, text), "owner")
        )
        return _admit_edit(rules), _forget(removed, cast(str, text))
    index = next((i for i, rule in enumerate(rules) if rule.number == number), None)
    if index is None:
        raise TasteEditError("stale")
    old = rules[index]
    if action == "remove":
        del rules[index]
        return _admit_edit(rules), _remember(removed, old.text)
    if text == old.text:
        return None
    rules[index] = replace(
        old,
        text=cast(str, text),
        provenance="owner" if old.provenance == "owner" else "edited",
    )
    listed = (
        removed
        if taste_rule_key(old.text) == taste_rule_key(cast(str, text))
        else _remember(removed, old.text)
    )
    return _admit_edit(rules), _forget(listed, cast(str, text))


def merge_taste_rebuild(
    base: TasteProfile | None, removed: tuple[str, ...], built: TasteProfile
) -> tuple[TasteProfile, int]:
    """A rebuild's profile: the base's kept rules as written, ahead of the
    model's rules in each section, leaving out model rules that repeat a kept
    rule or a removed text (S7). Returns the profile and how many it left out.

    Raises `CoverTasteError` when the merged profile breaks a bound.
    """
    kept = () if base is None else base.kept()
    known = {taste_rule_key(rule.text) for rule in kept} | {
        taste_rule_key(text) for text in removed
    }
    fresh = [rule for rule in built.rules if taste_rule_key(rule.text) not in known]
    merged = [
        rule
        for section in TASTE_SECTIONS
        for rule in (*kept, *fresh)
        if rule.section == section
    ]
    profile = TasteProfile(
        tuple(
            replace(rule, number=number) for number, rule in enumerate(merged, start=1)
        )
    )
    return profile, len(built.rules) - len(fresh)


__all__ = [
    "COVER_TASTE_PROFILE_SCHEMA",
    "COVER_TASTE_VERDICT_SCHEMA",
    "MAX_TASTE_PROFILE_CODE_POINTS",
    "MAX_TASTE_PROFILE_WORDS",
    "MAX_TASTE_REMOVED",
    "MAX_TASTE_REMOVED_CODE_POINTS",
    "MAX_TASTE_RULES",
    "MAX_TASTE_RULE_CODE_POINTS",
    "MAX_TASTE_RULE_NUMBER",
    "TASTE_SECTIONS",
    "CoverTasteError",
    "TasteBuildFailure",
    "TASTE_JUDGE_MAX_ALTERNATIVES",
    "TASTE_JUDGE_MAX_RULES",
    "TASTE_JUDGE_NOTICE_SECONDS",
    "TASTE_JUDGE_OUTAGE_PAUSE_SECONDS",
    "TASTE_JUDGE_SETTLE_SECONDS",
    "TASTE_STRONG_CONFIDENCE",
    "TasteEditAction",
    "TasteEditError",
    "TasteEditRefusal",
    "TasteJudgeDecision",
    "TasteJudgeFailure",
    "TasteProfile",
    "TasteProvenance",
    "TasteRule",
    "TasteSection",
    "apply_taste_edit",
    "merge_taste_rebuild",
    "taste_budget",
    "taste_removed_valid",
    "taste_rule_key",
    "taste_rule_text",
]
