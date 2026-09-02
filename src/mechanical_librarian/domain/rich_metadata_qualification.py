"""Public, aggregate-safe F20 qualification envelope.

This module deliberately contains no candidate values or private evidence. It
pins the published F20 candidate and its exact option-level authority.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final, Mapping

F20_QUALIFICATION_CANDIDATE_SHA256: Final = (
    "9c1410dcc6d221478a2b96afdc51da19fd8a54f82d7023034a3063afdedefc37"
)
F20_RICH_FIELDS: Final = (
    "subtitle",
    "series",
    "genres",
    "tags",
    "description_fill",
    "isbn_form",
    "asin",
    "explicit",
)
F20_OPTION_DISPOSITIONS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "subtitle:fill_empty": "passing_automatic",
        "subtitle:corroborated_split": "not_qualified",
        "series:primary_narrative": "passing_automatic",
        "series:source_order": "passing_automatic",
        "series:all_qualified": "passing_automatic",
        "genres:fill_empty": "passing_automatic",
        "genres:add": "passing_automatic",
        "genres:replace": "passing_automatic",
        "tags:off": "policy_only",
        "tags:fill_empty": "passing_automatic",
        "tags:add": "passing_automatic",
        "tags:replace": "passing_automatic",
        "description_fill:safe_plain_text": "passing_automatic",
        "description_fill:google_visible_text": "not_qualified",
        "isbn_form:preserve_exact": "passing_automatic",
        "isbn_form:prefer_isbn13": "passing_automatic",
        "asin:preserve_exact": "insufficient_coverage",
        "asin:ebook_manifestation": "insufficient_coverage",
        "explicit:unsupported": "passing_human_only",
    }
)
F20_PASSING_AUTOMATIC_OPTIONS: Final[frozenset[str]] = frozenset(
    {
        "subtitle:fill_empty",
        "series:primary_narrative",
        "series:source_order",
        "series:all_qualified",
        "genres:fill_empty",
        "genres:add",
        "genres:replace",
        "tags:fill_empty",
        "tags:add",
        "tags:replace",
        "description_fill:safe_plain_text",
        "isbn_form:preserve_exact",
        "isbn_form:prefer_isbn13",
    }
)


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
            "automatic_available": identity in F20_PASSING_AUTOMATIC_OPTIONS,
            "candidate_sha256": F20_QUALIFICATION_CANDIDATE_SHA256,
            "disposition": disposition(*identity.split(":", 1)),
            "field": field,
            "option": option,
        }
        for identity in sorted(F20_OPTION_DISPOSITIONS)
        for field, option in (identity.split(":", 1),)
    )


__all__ = [
    "F20_OPTION_DISPOSITIONS",
    "F20_PASSING_AUTOMATIC_OPTIONS",
    "F20_QUALIFICATION_CANDIDATE_SHA256",
    "F20_RICH_FIELDS",
    "disposition",
    "public_summary",
]
