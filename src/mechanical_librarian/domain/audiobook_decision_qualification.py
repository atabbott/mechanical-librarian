"""Closed field/operation identities for independently qualified audiobook effects.

The registry names candidates, not authority. A record must qualify each actual
operation; an ebook record never authorizes an audiobook. The passing set names only
independently accepted field operations. Public metadata contains no
candidate values or private evidence.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final, Mapping, Sequence, cast

from mechanical_librarian.domain.field_outcomes import (
    language_identity,
    language_is_unknown,
)
from mechanical_librarian.domain.field_value_comparison import values_match
from mechanical_librarian.domain.metadata_report import (
    TargetDestination,
    canonical_asin,
    isbn_family,
    normalize_date_value,
)

F46_QUALIFICATION_MANIFEST_SHA256: Final[str | None] = (
    "6ee296bc750e68009a24b68b5fbc2202370f0d5a33e9cb47eee4cdec00535ebc"
)

_FIELD_OPTIONS: Final = {
    "title": ("title_layout", ("preserve", "title_and_subtitle")),
    "subtitle": (
        "subtitle",
        ("fill_empty", "corroborated_split", "replace_descriptive"),
    ),
    "authors": ("contributor_names", ("preserve", "canonical")),
    "narrators": ("contributor_names", ("canonical",)),
    "series": (
        "series",
        (
            "primary_narrative",
            "source_order",
            "all_qualified",
            "add_qualified",
            "replace_qualified",
        ),
    ),
    "genres": ("genres", ("fill_empty", "add", "replace")),
    "tags": ("tags", ("fill_empty", "add", "replace")),
    "description": ("description_fill", ("safe_plain_text", "replace_safe_plain_text")),
    "publishedDate": ("date_precision", ("year", "full_date")),
    "publisher": ("publisher_preference", ("publisher", "imprint")),
    "language": ("language_rendering", ("preserve", "display_name")),
    "asin": ("asin", ("preserve_exact",)),
    "isbn": ("isbn_form", ("preserve_exact", "prefer_isbn13")),
    "explicit": ("explicit", ("adult_product",)),
    "cover": ("cover_order", ("fixed_source_order", "replace_if_strictly_better")),
}


def _operations(destination: str, option: str) -> tuple[str, ...]:
    if (
        (destination == "cover" and option == "fixed_source_order")
        or (destination == "subtitle" and option != "replace_descriptive")
        or option
        in {
            "fill_empty",
            "primary_narrative",
            "source_order",
            "all_qualified",
            "safe_plain_text",
        }
    ):
        return ("fill", "preserve")
    if option in {"add", "add_qualified"}:
        return ("fill", "preserve", "normalize", "add")
    if destination == "asin":
        return ("fill", "preserve", "normalize", "replace_invalid", "replace_recording")
    return (
        ("fill", "preserve", "normalize", "add", "replace")
        if destination in {"authors", "narrators", "series", "genres", "tags"}
        else ("fill", "preserve", "normalize", "replace")
    )


def _identity(destination: str, category: str, option: str, operation: str) -> str:
    return f"{category}:{option}:audiobook_{destination.lower()}_{operation}"


F46_IDENTITIES_BY_DESTINATION: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        destination: tuple(
            _identity(destination, category, option, operation)
            for option in options
            for operation in _operations(destination, option)
        )
        for destination, (category, options) in _FIELD_OPTIONS.items()
    }
)
F46_AUDIOBOOK_IDENTITIES: Final[frozenset[str]] = frozenset(
    identity
    for identities in F46_IDENTITIES_BY_DESTINATION.values()
    for identity in identities
)
F46_IDENTITY_BY_DESTINATION: Final[Mapping[str, str]] = MappingProxyType(
    {
        destination: identities[0]
        for destination, identities in F46_IDENTITIES_BY_DESTINATION.items()
    }
)
F46_NARRATORS_IDENTITY: Final = _identity(
    "narrators", "contributor_names", "canonical", "fill"
)
F46_SERIES_IDENTITY: Final = _identity("series", "series", "primary_narrative", "fill")
F46_COVER_FILL_IDENTITY: Final = _identity(
    "cover", "cover_order", "fixed_source_order", "fill"
)
F46_ASIN_IDENTITY: Final = _identity("asin", "asin", "preserve_exact", "fill")


def identity_for_effect(
    destination: str, category: str, option: str, current: object, desired: object
) -> str | None:
    """Bind qualification to the actual effect on the retained pre-value."""
    if (
        destination == "explicit"
        and not (current is None and desired is None)
        and (
            type(desired) is not bool
            or (current is not None and type(current) is not bool)
        )
    ):
        return None
    if destination == "publishedDate":
        if option not in {"year", "full_date"} or type(desired) is not str:
            return None
        try:
            normalized, _ = normalize_date_value(desired)
        except ValueError:
            return None
        if normalized != desired:
            return None
    if current == desired or (
        type(current) in {list, tuple}
        and type(desired) in {list, tuple}
        and list(cast(Sequence[object], current))
        == list(cast(Sequence[object], desired))
    ):
        operation = "preserve"
    elif current in (None, "", [], ()) or (
        destination == "language"
        and type(current) is str
        and language_is_unknown(current)
    ):
        operation = "fill"
    elif (
        destination == "isbn"
        and type(current) is str
        and type(desired) is str
        and isbn_family(current) is not None
        and isbn_family(current) == isbn_family(desired)
    ):
        operation = "normalize"
    elif (
        _language_effect_equivalent(current, desired)
        if destination == "language"
        else values_match(TargetDestination(destination), current, desired)
    ):
        operation = "normalize"
    elif (
        destination in {"authors", "narrators", "series", "genres", "tags"}
        and type(current) in {list, tuple}
        and type(desired) in {list, tuple}
        and all(
            member in cast(Sequence[object], desired)
            for member in cast(Sequence[object], current)
        )
    ):
        operation = "add"
    elif destination == "asin":
        operation = (
            "replace_invalid"
            if type(current) is not str or canonical_asin(current) is None
            else "replace_recording"
        )
    else:
        operation = "replace"
    identity = _identity(destination, category, option, operation)
    return identity if identity in F46_AUDIOBOOK_IDENTITIES else None


def _language_effect_equivalent(current: object, desired: object) -> bool:
    """Compare admitted forms without losing a language region or script."""
    if type(current) is not str or type(desired) is not str:
        return False
    if current.casefold() == desired.casefold():
        return True
    left = language_identity("en" if current.casefold() == "english" else current)
    right = language_identity("en" if desired.casefold() == "english" else desired)
    return left is not None and left == right


F46_PASSING_AUTOMATIC_OPTIONS: Final[frozenset[str]] = frozenset(
    {
        "asin:preserve_exact:audiobook_asin_fill",
        "contributor_names:canonical:audiobook_authors_add",
        "contributor_names:canonical:audiobook_authors_fill",
        "contributor_names:canonical:audiobook_authors_normalize",
        "contributor_names:canonical:audiobook_authors_preserve",
        "contributor_names:canonical:audiobook_authors_replace",
        "contributor_names:canonical:audiobook_narrators_add",
        "contributor_names:canonical:audiobook_narrators_fill",
        "contributor_names:canonical:audiobook_narrators_normalize",
        "contributor_names:canonical:audiobook_narrators_preserve",
        "contributor_names:canonical:audiobook_narrators_replace",
        "contributor_names:preserve:audiobook_authors_add",
        "contributor_names:preserve:audiobook_authors_fill",
        "contributor_names:preserve:audiobook_authors_normalize",
        "contributor_names:preserve:audiobook_authors_preserve",
        "contributor_names:preserve:audiobook_authors_replace",
        "cover_order:replace_if_strictly_better:audiobook_cover_replace",
        "date_precision:full_date:audiobook_publisheddate_fill",
        "description_fill:replace_safe_plain_text:audiobook_description_replace",
        "description_fill:safe_plain_text:audiobook_description_fill",
        "explicit:adult_product:audiobook_explicit_fill",
        "explicit:adult_product:audiobook_explicit_preserve",
        "explicit:adult_product:audiobook_explicit_replace",
        "genres:add:audiobook_genres_add",
        "genres:add:audiobook_genres_fill",
        "genres:add:audiobook_genres_preserve",
        "genres:fill_empty:audiobook_genres_fill",
        "genres:fill_empty:audiobook_genres_preserve",
        "genres:replace:audiobook_genres_add",
        "genres:replace:audiobook_genres_fill",
        "genres:replace:audiobook_genres_normalize",
        "genres:replace:audiobook_genres_preserve",
        "genres:replace:audiobook_genres_replace",
        "isbn_form:prefer_isbn13:audiobook_isbn_fill",
        "isbn_form:prefer_isbn13:audiobook_isbn_normalize",
        "isbn_form:prefer_isbn13:audiobook_isbn_replace",
        "isbn_form:preserve_exact:audiobook_isbn_fill",
        "isbn_form:preserve_exact:audiobook_isbn_preserve",
        "language_rendering:display_name:audiobook_language_fill",
        "language_rendering:display_name:audiobook_language_normalize",
        "language_rendering:display_name:audiobook_language_preserve",
        "language_rendering:display_name:audiobook_language_replace",
        "language_rendering:preserve:audiobook_language_fill",
        "language_rendering:preserve:audiobook_language_normalize",
        "language_rendering:preserve:audiobook_language_preserve",
        "language_rendering:preserve:audiobook_language_replace",
        "publisher_preference:publisher:audiobook_publisher_fill",
        "publisher_preference:publisher:audiobook_publisher_preserve",
        "series:add_qualified:audiobook_series_add",
        "series:add_qualified:audiobook_series_fill",
        "series:add_qualified:audiobook_series_preserve",
        "series:all_qualified:audiobook_series_fill",
        "series:all_qualified:audiobook_series_preserve",
        "series:primary_narrative:audiobook_series_fill",
        "series:primary_narrative:audiobook_series_preserve",
        "series:replace_qualified:audiobook_series_fill",
        "series:replace_qualified:audiobook_series_normalize",
        "series:replace_qualified:audiobook_series_preserve",
        "series:replace_qualified:audiobook_series_replace",
        "series:source_order:audiobook_series_fill",
        "series:source_order:audiobook_series_preserve",
        "subtitle:corroborated_split:audiobook_subtitle_fill",
        "subtitle:fill_empty:audiobook_subtitle_fill",
        "subtitle:replace_descriptive:audiobook_subtitle_preserve",
        "subtitle:replace_descriptive:audiobook_subtitle_replace",
        "tags:add:audiobook_tags_add",
        "tags:fill_empty:audiobook_tags_fill",
        "title_layout:preserve:audiobook_title_fill",
        "title_layout:preserve:audiobook_title_preserve",
        "title_layout:preserve:audiobook_title_replace",
        "title_layout:title_and_subtitle:audiobook_title_fill",
        "title_layout:title_and_subtitle:audiobook_title_preserve",
        "title_layout:title_and_subtitle:audiobook_title_replace",
    }
)


class AudiobookQualificationError(ValueError):
    """An audiobook identity is outside the published F46 envelope."""


def audiobook_identity(policy_category: object, policy_option: object) -> str | None:
    """Return the F46 identity one category/option pair states, if any."""
    if type(policy_category) is not str or type(policy_option) is not str:
        return None
    identity = f"{policy_category}:{policy_option}"
    return identity if identity in F46_AUDIOBOOK_IDENTITIES else None


def qualified_policy_bases() -> frozenset[str]:
    """Return the `category:option` bases a record currently qualifies.

    This is what the owner may route to Automatic, so it is read live rather
    than frozen at import: activating a tranche must not require a second
    edit anywhere else.
    """
    return frozenset(
        identity.rsplit(":", 1)[0] for identity in F46_PASSING_AUTOMATIC_OPTIONS
    )


def qualification_manifest_sha256(identity: object) -> str:
    """Return the record digest one qualified audiobook identity binds."""
    digest = F46_QUALIFICATION_MANIFEST_SHA256
    if identity not in F46_PASSING_AUTOMATIC_OPTIONS or type(digest) is not str:
        raise AudiobookQualificationError("audiobook qualification is unavailable")
    return digest


def public_summary() -> tuple[dict[str, object], ...]:
    """Describe every F46 tranche without disclosing qualification cases.

    The identity grammar is this module's, so the split is made here: `field`
    and `option` are the owner's existing policy category and option, which is
    what a routing surface asks about, while `identity` and `destination` name
    the audiobook shape a record qualified and the field it decides. An
    unqualified tranche states itself unavailable and names no digest.
    """
    return tuple(
        {
            "automatic_available": identity in F46_PASSING_AUTOMATIC_OPTIONS,
            "destination": destination,
            "field": field,
            "identity": identity,
            "option": option,
            "operation": identity.split(":", 2)[2].removeprefix(
                f"audiobook_{destination.lower()}_"
            ),
            "record_sha256": (
                F46_QUALIFICATION_MANIFEST_SHA256
                if identity in F46_PASSING_AUTOMATIC_OPTIONS
                else None
            ),
        }
        for destination, identities in sorted(F46_IDENTITIES_BY_DESTINATION.items())
        for identity in identities
        for field, option in (identity.rsplit(":", 1)[0].split(":", 1),)
    )


__all__ = [
    "F46_ASIN_IDENTITY",
    "F46_AUDIOBOOK_IDENTITIES",
    "F46_COVER_FILL_IDENTITY",
    "F46_IDENTITIES_BY_DESTINATION",
    "F46_IDENTITY_BY_DESTINATION",
    "F46_NARRATORS_IDENTITY",
    "F46_PASSING_AUTOMATIC_OPTIONS",
    "F46_QUALIFICATION_MANIFEST_SHA256",
    "F46_SERIES_IDENTITY",
    "AudiobookQualificationError",
    "audiobook_identity",
    "identity_for_effect",
    "public_summary",
    "qualification_manifest_sha256",
    "qualified_policy_bases",
]


F46_TITLE_SUBTITLE_PAIR_IDENTITY = (
    "title_layout:title_and_subtitle:audiobook_title_subtitle_pair"
)
F46_PASSING_PAIRED_OPTIONS: frozenset[str] = frozenset(
    {F46_TITLE_SUBTITLE_PAIR_IDENTITY}
)
