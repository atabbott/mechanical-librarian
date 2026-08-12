"""Public, aggregate-safe F20 qualification envelope.

This module deliberately contains no candidate values, corpus material, or
automatic authority.  It pins the sole published F20 candidate so selection
and presentation surfaces can explain why a rich-field option remains manual.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final, Mapping


F20_QUALIFICATION_CANDIDATE_SHA256: Final = (
    "dde53f9aaad52f8186aa1d25859ebb83b1d6b9c6f8125b15789729831de0b0f9"
)
F20_RICH_FIELDS: Final = (
    "subtitle", "series", "genres", "tags", "description_fill",
    "isbn_form", "asin", "explicit",
)
F20_OPTION_DISPOSITIONS: Final[Mapping[str, str]] = MappingProxyType({
    "subtitle:fill_empty": "insufficient_coverage",
    "subtitle:corroborated_split": "insufficient_coverage",
    "series:primary_narrative": "insufficient_coverage",
    "series:source_order": "insufficient_coverage",
    "series:all_qualified": "insufficient_coverage",
    "genres:fill_empty": "insufficient_coverage",
    "genres:add": "insufficient_coverage",
    "genres:replace": "insufficient_coverage",
    "tags:off": "policy_only",
    "tags:fill_empty": "insufficient_coverage",
    "tags:add": "insufficient_coverage",
    "tags:replace": "insufficient_coverage",
    "description_fill:safe_plain_text": "insufficient_coverage",
    "description_fill:google_visible_text": "insufficient_coverage",
    "isbn_form:preserve_exact": "insufficient_coverage",
    "isbn_form:prefer_isbn13": "insufficient_coverage",
    "asin:preserve_exact": "insufficient_coverage",
    "asin:ebook_manifestation": "insufficient_coverage",
    "explicit:unsupported": "passing_human_only",
})
F20_PASSING_AUTOMATIC_OPTIONS: Final[frozenset[str]] = frozenset()


def disposition(field: object, option: object) -> str:
    """Return the terminal public disposition for one closed F20 option."""
    if type(field) is not str or type(option) is not str:
        raise ValueError("rich qualification option is invalid")
    try:
        return F20_OPTION_DISPOSITIONS[f"{field}:{option}"]
    except KeyError as error:
        raise ValueError("rich qualification option is unavailable") from error


def public_summary() -> tuple[dict[str, object], ...]:
    """Describe every closed F20 option without disclosing qualification cases."""
    return tuple(
        {
            "automatic_available": False,
            "candidate_sha256": F20_QUALIFICATION_CANDIDATE_SHA256,
            "disposition": disposition(*identity.split(":", 1)),
            "field": field,
            "option": option,
        }
        for identity in sorted(F20_OPTION_DISPOSITIONS)
        for field, option in (identity.split(":", 1),)
    )


__all__ = [
    "F20_OPTION_DISPOSITIONS", "F20_PASSING_AUTOMATIC_OPTIONS",
    "F20_QUALIFICATION_CANDIDATE_SHA256", "F20_RICH_FIELDS", "disposition",
    "public_summary",
]
