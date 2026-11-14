"""The owner's cover taste profile: four sections of bounded, numbered rules.

A profile is data the builder model wrote from the owner's recorded cover
choices (ADR-0095). It is refused, never truncated or repaired, when any bound
fails, so an admitted profile is always exactly what the model returned.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Final, Literal, cast, get_args

from mechanical_librarian.domain.evidence_bounds import (
    EvidenceValueError,
    validate_projection_text,
)
from mechanical_librarian.domain.validation import owner_text_value

CoverTasteProfileSchemaVersion = Literal["cover-taste-profile-v1"]
COVER_TASTE_PROFILE_SCHEMA: Final[CoverTasteProfileSchemaVersion] = (
    "cover-taste-profile-v1"
)

TasteSection = Literal["drawn_to", "avoid", "trade_offs", "keep_or_switch"]
TASTE_SECTIONS: Final[tuple[TasteSection, ...]] = get_args(TasteSection)
# PR 1 writes model rules only; owner and edited provenance arrive with v2.
TasteProvenance = Literal["model"]
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

# Cadence and input bounds (ADR-0095, #1372 S2-S4, S9, R1, R2).
TASTE_FIRST_BUILD_DECISIONS: Final = 25
TASTE_REBUILD_DECISIONS: Final = 10
TASTE_SCHEDULED_BUILDS_PER_DAY: Final = 4
TASTE_MAX_DECISIONS: Final = 50
TASTE_PREVIEWS_PER_DECISION: Final = 4
TASTE_MAX_IMAGES: Final = 100
TASTE_MAX_REQUEST_BYTES: Final = 24_000_000
TASTE_MIN_ARTS: Final = 2


class CoverTasteError(ValueError):
    """A value-free refusal of a profile or its builder output."""


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
        texts = [rule.text for rule in self.rules]
        if (
            type(self.rules) is not tuple
            or len(self.rules) > MAX_TASTE_RULES
            or any(type(rule) is not TasteRule for rule in self.rules)
            or [rule.number for rule in self.rules]
            != list(range(1, len(self.rules) + 1))
            or [rule.section for rule in self.rules]
            != sorted((rule.section for rule in self.rules), key=TASTE_SECTIONS.index)
            or any(rule.provenance != "model" for rule in self.rules)
            or not all(taste_rule_text(text) for text in texts)
            or sum(len(text.split()) for text in texts) > MAX_TASTE_PROFILE_WORDS
            or sum(len(text) for text in texts) > MAX_TASTE_PROFILE_CODE_POINTS
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
        """Read stored rules back, refusing anything a build could not write."""
        if type(value) is not list or any(
            type(rule) is not dict
            or set(rule) != {"number", "provenance", "section", "text"}
            or type(rule["number"]) is not int
            or rule["section"] not in TASTE_SECTIONS
            or type(rule["text"]) is not str
            or rule["provenance"] != "model"
            for rule in value
        ):
            raise CoverTasteError("taste profile rules are invalid")
        return cls(
            tuple(
                TasteRule(
                    cast(int, rule["number"]),
                    cast(TasteSection, rule["section"]),
                    cast(str, rule["text"]),
                )
                for rule in cast("list[dict[str, object]]", value)
            )
        )

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


__all__ = [
    "COVER_TASTE_PROFILE_SCHEMA",
    "MAX_TASTE_PROFILE_CODE_POINTS",
    "MAX_TASTE_PROFILE_WORDS",
    "MAX_TASTE_RULES",
    "MAX_TASTE_RULE_CODE_POINTS",
    "TASTE_SECTIONS",
    "CoverTasteError",
    "TasteBuildFailure",
    "TasteProfile",
    "TasteRule",
    "TasteSection",
    "taste_rule_text",
]
