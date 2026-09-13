"""Closed, replayable metadata-policy profiles.

Profiles express an owner's *intent* only.  They do not evaluate evidence,
choose a proposal, enqueue work, or grant automatic/write authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Final, Mapping

from mechanical_librarian.domain.audiobook_decision_qualification import (
    F46_PASSING_AUTOMATIC_OPTIONS,
)
from mechanical_librarian.domain.rich_metadata_qualification import (
    F20_PASSING_AUTOMATIC_OPTIONS,
    F20_RICH_FIELDS,
)


POLICY_SCHEMA_VERSION: Final = "metadata-policy-profile-v3"
DESTINATIONS: Final = ("ask", "ignore", "automatic", "unsupported")

# The product catalogue is deliberately closed.  A destination is an owner
# preference, not an authority: automatic is retained only as visibly
# unavailable intent until a later qualified policy can consume it.
OPTION_CATALOGUE: Final[dict[str, tuple[str, ...]]] = {
    "title_layout": ("preserve", "title_and_subtitle"),
    "date_precision": ("year", "full_date"),
    "language_rendering": ("preserve", "display_name"),
    "contributor_names": ("preserve", "canonical"),
    "publisher_preference": ("publisher", "imprint"),
    "subtitle": ("fill_empty", "corroborated_split"),
    "series": ("primary_narrative", "source_order", "all_qualified", "add_qualified", "replace_qualified"),
    "genres": ("fill_empty", "add", "replace"),
    "tags": ("off", "fill_empty", "add", "replace"),
    "description_fill": ("safe_plain_text", "google_visible_text", "replace_safe_plain_text"),
    "isbn_form": ("preserve_exact", "prefer_isbn13"),
    "cover_order": ("fixed_source_order",),
    "asin": ("preserve_exact", "ebook_manifestation"),
    "explicit": ("unsupported",),
}

DEFAULT_OPTIONS: Final = {
    "title_layout": "title_and_subtitle",
    "date_precision": "year",
    "language_rendering": "display_name",
    "contributor_names": "canonical",
    "publisher_preference": "publisher",
    "subtitle": "fill_empty",
    "series": "primary_narrative",
    "genres": "fill_empty",
    "tags": "off",
    "description_fill": "safe_plain_text",
    "isbn_form": "preserve_exact",
    "cover_order": "fixed_source_order",
    "asin": "preserve_exact",
    "explicit": "unsupported",
}

# The shipped qualification envelope is deliberately closed.  This is a
# category-level availability hint for the notebook; authority is admitted
# only by the exact option/bundle manifest in ``automatic_decision``.
QUALIFIED_AUTOMATIC: Final[frozenset[str]] = frozenset({
    "title_layout", "date_precision", "language_rendering",
    "contributor_names", "publisher_preference",
}) | frozenset(
    identity.split(":", 1)[0] for identity in F20_PASSING_AUTOMATIC_OPTIONS
) | frozenset(
    identity.split(":", 1)[0] for identity in F46_PASSING_AUTOMATIC_OPTIONS
)
_POLICY_ID: Final = re.compile(r"^policy_[0-9a-f]{64}$")


class MetadataPolicyError(ValueError):
    """A profile or override is malformed or cannot be replayed."""


def human_review_destinations(
    policy: "ResolvedMetadataPolicy",
) -> dict[str, str]:
    """Keep Preview automatic intent from changing the supported human queue."""
    destinations = dict(policy.destinations)
    if "automatic" not in destinations.values():
        return destinations
    return {
        category: "ask" if destination == "automatic" else destination
        for category, destination in destinations.items()
    }


def _canonical_document(value: Mapping[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _closed_options(value: object, *, sparse: bool) -> dict[str, str]:
    if type(value) is not dict:
        raise MetadataPolicyError("policy options are invalid")
    expected = set(OPTION_CATALOGUE)
    if not set(value).issubset(expected):
        raise MetadataPolicyError("policy option is unavailable")
    if not sparse and set(value) != expected:
        raise MetadataPolicyError("policy options are incomplete")
    result: dict[str, str] = {}
    for field, option in value.items():
        if type(field) is not str or type(option) is not str or option not in OPTION_CATALOGUE[field]:
            raise MetadataPolicyError("policy option is unavailable")
        result[field] = option
    return result


def _closed_destinations(value: object, *, sparse: bool) -> dict[str, str]:
    if type(value) is not dict:
        raise MetadataPolicyError("policy destinations are invalid")
    expected = set(OPTION_CATALOGUE)
    if not set(value).issubset(expected):
        raise MetadataPolicyError("policy destination is unavailable")
    if not sparse and set(value) != expected:
        raise MetadataPolicyError("policy destinations are incomplete")
    result: dict[str, str] = {}
    for field, destination in value.items():
        if type(field) is not str or type(destination) is not str or destination not in DESTINATIONS:
            raise MetadataPolicyError("policy destination is unavailable")
        if field == "explicit" and destination != "unsupported":
            raise MetadataPolicyError("explicit policy is unsupported")
        result[field] = destination
    return result


@dataclass(frozen=True, slots=True)
class MetadataPolicyProfile:
    """One immutable named draft or active profile, identified by its content."""

    name: str
    options: tuple[tuple[str, str], ...]
    destinations: tuple[tuple[str, str], ...]
    schema_version: str = POLICY_SCHEMA_VERSION

    @classmethod
    def builtin(cls) -> "MetadataPolicyProfile":
        # A clean installation enters decision-only automatic handling for
        # exactly the sealed defaults. Everything outside that envelope stays
        # an explicit human-review preference.
        return cls.create("Built-in policy", DEFAULT_OPTIONS, {
            field: (
                "automatic"
                if field in QUALIFIED_AUTOMATIC
                and (
                    field not in F20_RICH_FIELDS
                    or f"{field}:{DEFAULT_OPTIONS[field]}"
                    in F20_PASSING_AUTOMATIC_OPTIONS
                )
                else
                "unsupported" if field == "explicit" else "ask"
            )
            for field in OPTION_CATALOGUE
        })

    @classmethod
    def create(
        cls, name: object, options: object, destinations: object
    ) -> "MetadataPolicyProfile":
        if type(name) is not str or not 1 <= len(name) <= 80 or name.strip() != name:
            raise MetadataPolicyError("policy name is invalid")
        parsed_options = _closed_options(options, sparse=False)
        parsed_destinations = _closed_destinations(destinations, sparse=False)
        return cls(name, tuple(sorted(parsed_options.items())), tuple(sorted(parsed_destinations.items())))

    @property
    def policy_id(self) -> str:
        return "policy_" + hashlib.sha256(self.canonical_json.encode("utf-8")).hexdigest()

    @property
    def canonical_json(self) -> str:
        return _canonical_document(self.to_data())

    def to_data(self) -> dict[str, object]:
        return {"destinations": dict(self.destinations), "name": self.name, "options": dict(self.options), "schema_version": self.schema_version}

    @classmethod
    def from_data(cls, value: object) -> "MetadataPolicyProfile":
        if type(value) is not dict or set(value) != {"destinations", "name", "options", "schema_version"} or value.get("schema_version") != POLICY_SCHEMA_VERSION:
            raise MetadataPolicyError("policy document is invalid")
        profile = cls.create(value["name"], value["options"], value["destinations"])
        if profile.to_data() != value:
            raise MetadataPolicyError("policy document is not canonical")
        return profile


@dataclass(frozen=True, slots=True)
class ResolvedMetadataPolicy:
    """A profile after one sparse, read-only library override is applied."""

    profile_id: str
    library: str
    options: tuple[tuple[str, str], ...]
    destinations: tuple[tuple[str, str], ...]

    @property
    def resolved_id(self) -> str:
        return "resolved_policy_" + hashlib.sha256(self.canonical_json.encode("utf-8")).hexdigest()

    @property
    def canonical_json(self) -> str:
        return _canonical_document(self.to_data())

    def to_data(self) -> dict[str, object]:
        return {"destinations": dict(self.destinations), "library": self.library, "options": dict(self.options), "profile_id": self.profile_id, "schema_version": POLICY_SCHEMA_VERSION}

    @classmethod
    def from_data(cls, value: object) -> "ResolvedMetadataPolicy":
        if (
            type(value) is not dict
            or set(value) != {"destinations", "library", "options", "profile_id", "schema_version"}
            or value.get("schema_version") != POLICY_SCHEMA_VERSION
            or type(value.get("profile_id")) is not str
            or _POLICY_ID.fullmatch(str(value["profile_id"])) is None
            or type(value.get("library")) is not str
            or not 1 <= len(str(value["library"])) <= 64
        ):
            raise MetadataPolicyError("resolved policy document is invalid")
        options = _closed_options(value["options"], sparse=False)
        destinations = _closed_destinations(value["destinations"], sparse=False)
        resolved = cls(
            str(value["profile_id"]),
            str(value["library"]),
            tuple(sorted(options.items())),
            tuple(sorted(destinations.items())),
        )
        if resolved.to_data() != value:
            raise MetadataPolicyError("resolved policy document is not canonical")
        return resolved

    def qualification(self, field: str) -> str:
        if field not in OPTION_CATALOGUE:
            raise MetadataPolicyError("policy field is unavailable")
        destination = dict(self.destinations)[field]
        if destination == "automatic":
            option = dict(self.options)[field]
            available = (
                f"{field}:{option}" in F20_PASSING_AUTOMATIC_OPTIONS
                if field in F20_RICH_FIELDS
                else field in QUALIFIED_AUTOMATIC
            )
            return "available" if available else "unavailable"
        return "manual_only" if destination in {"ask", "ignore"} else "unsupported"


def resolve_policy(profile: MetadataPolicyProfile, library: object, override: object = None) -> ResolvedMetadataPolicy:
    if type(profile) is not MetadataPolicyProfile or type(library) is not str or not 1 <= len(library) <= 64:
        raise MetadataPolicyError("resolved policy is invalid")
    if override is None:
        raw_options: dict[str, str] = {}
        raw_destinations: dict[str, str] = {}
    else:
        if type(override) is not dict or set(override) != {"options", "destinations"}:
            raise MetadataPolicyError("library policy override is invalid")
        raw_options = _closed_options(override["options"], sparse=True)
        raw_destinations = _closed_destinations(override["destinations"], sparse=True)
    options = dict(profile.options)
    destinations = dict(profile.destinations)
    options.update(raw_options)
    destinations.update(raw_destinations)
    if destinations["explicit"] != "unsupported":
        raise MetadataPolicyError("explicit policy is unsupported")
    return ResolvedMetadataPolicy(profile.policy_id, library, tuple(sorted(options.items())), tuple(sorted(destinations.items())))


__all__ = ["DEFAULT_OPTIONS", "DESTINATIONS", "MetadataPolicyError", "MetadataPolicyProfile", "OPTION_CATALOGUE", "POLICY_SCHEMA_VERSION", "ResolvedMetadataPolicy", "resolve_policy"]
