"""Owner identity links: the record an owner said is a work's book (ADR-0104).

An owner links one catalogue item, keyed by connection, library and item, to
one source record as This book (`work`), This edition (`edition`, an ebook's
ISBN) or This recording (`recording`, an audiobook's ASIN). The link is the
owner's authority about identity, read again at every preparation; it never
fills an identifier field and never decides anything by itself.

This module holds the closed vocabularies, the record-identity grammar for
each scope and source, the link digest the report binds, and the one
predicate that decides which scopes anchor the other sources' search.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from mechanical_librarian.domain.evidence_bounds import (
    EvidenceValueError,
    validate_projection_text,
)
from mechanical_librarian.domain.metadata_report import (
    OWNER_LINK_CLAIM_BASIS,
    ClaimKind,
    ClaimScope,
    LocalClaim,
    canonical_asin,
    canonical_json,
    isbn_family,
)


class OwnerLinkError(ValueError):
    """A link value outside its closed grammar."""


class OwnerLinkScope(StrEnum):
    WORK = "work"
    EDITION = "edition"
    RECORDING = "recording"


class OwnerLinkAction(StrEnum):
    LINK = "link"
    REPLACE = "replace"
    WITHDRAW = "withdraw"


class OwnerLinkOutcome(StrEnum):
    """What one preparation made of the current link (`report.owner_link`)."""

    ADMITTED = "admitted"
    # The linked source did not answer in a way recovery may retry.
    UNAVAILABLE = "unavailable"
    # The source no longer has the record.
    MISSING = "missing"
    # The source merged the record into another; the pointer is never followed.
    MERGED = "merged"
    # The record answered but failed the edition or recording re-check.
    CONTRADICTED = "contradicted"
    SOURCE_DISABLED = "source_disabled"
    MEDIUM_CHANGED = "medium_changed"


class OwnerLinkContradiction(StrEnum):
    """Why an edition or recording link failed its re-check."""

    FORMAT = "format"
    RUNTIME = "runtime"
    NARRATORS = "narrators"
    VARIANT = "variant"


OWNER_LINK_SOURCES: Final = frozenset({"search"})
OWNER_LINK_MEDIA: Final = frozenset({"ebook", "audiobook"})
OWNER_LINK_PROVIDERS: Final = frozenset(
    {"hardcover", "open_library", "google_books", "inventaire", "audible_catalog"}
)
# The sources a link may name per medium (#1477): the ones with a search shape.
LINK_SOURCES_BY_MEDIUM: Final = {
    "ebook": frozenset({"hardcover", "open_library", "google_books", "inventaire"}),
    "audiobook": frozenset({"audible_catalog", "hardcover", "open_library"}),
}
# A work link read by key rather than by an identifier lane.
DIRECT_LOOKUP_PROVIDERS: Final = frozenset({"hardcover", "open_library", "inventaire"})
# The identifier lanes an edition or recording link is sent through: every
# source of the medium's dispatch set that has that identifier lane.
EDITION_LINK_AUTHORITIES: Final = frozenset(
    {"hardcover", "open_library", "google_books", "inventaire"}
)
RECORDING_LINK_AUTHORITIES: Final = frozenset(
    {"audible_catalog", "audnexus", "audiosilo"}
)
MAX_DISPLAY_CREATORS: Final = 10
MAX_DISPLAY_CHARACTERS: Final = 512
# Display creators are bounded by count and characters; the anchor reads the
# record again, so a snapshot is never evidence. The anchor's own bound,
# `MAX_ANCHOR_CREATORS`, lives with the report that carries it.

_HARDCOVER_BOOK = re.compile(r"[1-9][0-9]{0,9}")
_OPEN_LIBRARY_KEY = re.compile(
    r"/(?:works/OL[1-9][0-9]{0,11}W|books/OL[1-9][0-9]{0,11}M)"
)
_INVENTAIRE_URI = re.compile(r"(?:wd:Q[1-9][0-9]{0,11}|inv:[0-9a-f]{32})")
_LINK_ROW_ID = re.compile(r"owner_link_[0-9a-f]{64}")
OWNER_LINK_ROW_ID_PATTERN: Final = _LINK_ROW_ID


def owner_link_anchors(scope: OwnerLinkScope) -> bool:
    """Whether a link of this scope anchors the other sources' search.

    The owner chose that every link steers (#1477, Q4): an edition or
    recording link anchors exactly as a work link does. This is the one place
    that decides it.
    """
    return scope in {
        OwnerLinkScope.WORK,
        OwnerLinkScope.EDITION,
        OwnerLinkScope.RECORDING,
    }


def owner_isbn(value: object) -> str:
    """An owner-typed ISBN: 10 or 13 characters, no separators, checksum-valid.

    Digits only, with `X` allowed as an ISBN-10's last character. The value
    is refused, never repaired, and returned as its ISBN-13 family.
    """
    if (
        type(value) is not str
        or len(value) not in {10, 13}
        or not value.isascii()
        or not (
            value.isdigit()
            or (len(value) == 10 and value[:9].isdigit() and value[9] == "X")
        )
    ):
        raise OwnerLinkError("owner ISBN is invalid")
    family = isbn_family(value)
    if family is None:
        raise OwnerLinkError("owner ISBN is invalid")
    return family


def owner_asin(value: object) -> str:
    """An owner-typed ASIN, already in its canonical spelling."""
    if type(value) is not str or canonical_asin(value) != value:
        raise OwnerLinkError("owner ASIN is invalid")
    return value


def record_identity_valid(
    scope: OwnerLinkScope, provider: str, identity: object
) -> bool:
    """Whether `identity` follows the closed grammar of its scope and source."""
    if type(identity) is not str or provider not in OWNER_LINK_PROVIDERS:
        return False
    if scope is OwnerLinkScope.EDITION:
        return provider in EDITION_LINK_AUTHORITIES and _isbn13(identity)
    if scope is OwnerLinkScope.RECORDING:
        return provider == "audible_catalog" and canonical_asin(identity) == identity
    if provider == "hardcover":
        return _HARDCOVER_BOOK.fullmatch(identity) is not None
    if provider == "open_library":
        return _OPEN_LIBRARY_KEY.fullmatch(identity) is not None
    if provider == "inventaire":
        return _INVENTAIRE_URI.fullmatch(identity) is not None
    if provider == "google_books":
        return _isbn13(identity)
    return canonical_asin(identity) == identity


def _isbn13(value: str) -> bool:
    return len(value) == 13 and isbn_family(value) == value


def link_digest(
    medium: str, scope: OwnerLinkScope, identity: str, provider: str
) -> str:
    """SHA-256 of the link's canonical identity (S5).

    The source is part of it only for a work link: an edition or recording is
    the same lookup whichever source the owner found it through, and neither
    the row, the actor nor the display snapshot changes what is prepared.
    """
    value: dict[str, str] = {
        "identity": identity,
        "medium": medium,
        "scope": scope.value,
    }
    if scope is OwnerLinkScope.WORK:
        value["provider"] = provider
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class OwnerLinkTarget:
    """What one link or replace row records: the record and its snapshot."""

    scope: OwnerLinkScope
    provider: str
    identity: str
    display_title: str
    display_creators: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.scope) is not OwnerLinkScope or not record_identity_valid(
            self.scope, self.provider, self.identity
        ):
            raise OwnerLinkError("owner link target is invalid")
        try:
            validate_projection_text(
                self.display_title, "link title", MAX_DISPLAY_CHARACTERS
            )
            if (
                type(self.display_creators) is not tuple
                or len(self.display_creators) > MAX_DISPLAY_CREATORS
            ):
                raise OwnerLinkError("owner link creators are invalid")
            for value in self.display_creators:
                validate_projection_text(value, "link creator", MAX_DISPLAY_CHARACTERS)
        except EvidenceValueError as error:
            raise OwnerLinkError("owner link display is invalid") from error


@dataclass(frozen=True, slots=True)
class OwnerLinkLookup:
    """What preparation reads a link by: the digest's four values.

    The source is kept only for a work link; an edition or recording is the
    same lookup whichever source the owner found it through.
    """

    medium: str
    scope: OwnerLinkScope
    provider: str | None
    identity: str

    def __post_init__(self) -> None:
        if (
            self.medium not in OWNER_LINK_MEDIA
            or type(self.scope) is not OwnerLinkScope
            or (self.provider is None) != (self.scope is not OwnerLinkScope.WORK)
            or not record_identity_valid(
                self.scope,
                self.provider
                or (
                    "audible_catalog"
                    if self.scope is OwnerLinkScope.RECORDING
                    else "open_library"
                ),
                self.identity,
            )
            or (self.scope is OwnerLinkScope.EDITION and self.medium != "ebook")
            or (self.scope is OwnerLinkScope.RECORDING and self.medium != "audiobook")
            or (
                self.provider is not None
                and self.provider not in LINK_SOURCES_BY_MEDIUM[self.medium]
            )
        ):
            raise OwnerLinkError("owner link lookup is invalid")

    @property
    def digest(self) -> str:
        return link_digest(self.medium, self.scope, self.identity, self.provider or "")

    @property
    def claim(self) -> LocalClaim | None:
        return owner_link_claim(self.scope, self.provider, self.identity)

    @property
    def authorities(self) -> frozenset[str]:
        return link_authorities(self.scope, self.provider)


@dataclass(frozen=True, slots=True)
class OwnerLink:
    """An item's current link row, as the store reads it."""

    row_id: str
    medium: str
    scope: OwnerLinkScope
    provider: str
    identity: str

    def __post_init__(self) -> None:
        if (
            type(self.row_id) is not str
            or _LINK_ROW_ID.fullmatch(self.row_id) is None
            or self.medium not in OWNER_LINK_MEDIA
            or type(self.scope) is not OwnerLinkScope
            or not record_identity_valid(self.scope, self.provider, self.identity)
        ):
            raise OwnerLinkError("owner link is invalid")

    @property
    def digest(self) -> str:
        return link_digest(self.medium, self.scope, self.identity, self.provider)

    @property
    def lookup(self) -> OwnerLinkLookup:
        return OwnerLinkLookup(
            self.medium,
            self.scope,
            self.provider if self.scope is OwnerLinkScope.WORK else None,
            self.identity,
        )


def link_identifier_kind(
    scope: OwnerLinkScope, provider: str | None
) -> ClaimKind | None:
    """The identifier lane a link is read through, or None for a key lookup."""
    if scope is OwnerLinkScope.EDITION or (
        scope is OwnerLinkScope.WORK and provider == "google_books"
    ):
        return ClaimKind.ISBN
    if scope is OwnerLinkScope.RECORDING or (
        scope is OwnerLinkScope.WORK and provider == "audible_catalog"
    ):
        return ClaimKind.ASIN
    return None


def owner_link_claim(
    scope: OwnerLinkScope, provider: str | None, identity: str
) -> LocalClaim | None:
    """The lookup input an identifier link is sent through its lanes with.

    Like a companion EPUB's claims (#1468), it is a lane input only: it never
    enters a report's local claims, so it is never a field value, never
    compared and never offered (S25).
    """
    kind = link_identifier_kind(scope, provider)
    if kind is None:
        return None
    if kind is ClaimKind.ISBN:
        return LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed=identity,
            canonical=identity,
            excerpt=identity,
            applicability="ebook",
            basis=OWNER_LINK_CLAIM_BASIS,
            checksum="valid_isbn_13",
            applicability_basis="ebook",
            scope=ClaimScope.EDITION,
        )
    return LocalClaim.create(
        kind=ClaimKind.ASIN,
        observed=identity,
        canonical=identity,
        excerpt=identity,
        applicability="not_applicable",
        basis=OWNER_LINK_CLAIM_BASIS,
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.EDITION,
    )


def link_authorities(scope: OwnerLinkScope, provider: str | None) -> frozenset[str]:
    """The adapters a link lane is dispatched to (S18)."""
    if scope is OwnerLinkScope.EDITION:
        return EDITION_LINK_AUTHORITIES
    if scope is OwnerLinkScope.RECORDING:
        return RECORDING_LINK_AUTHORITIES
    return frozenset() if provider is None else frozenset({provider})


def link_record_identity(lookup: OwnerLinkLookup) -> str | None:
    """The candidate identity a key lookup's record must carry (S19).

    A Hardcover book or an Open Library work is named by the candidate's work
    identity, an Open Library edition key by its own identity, an Inventaire
    entity by its work identity. An identifier link has none: it is named by
    the ISBN or ASIN the record states.
    """
    if lookup.scope is not OwnerLinkScope.WORK:
        return None
    if lookup.provider == "hardcover":
        return f"hardcover-work-{int(lookup.identity):010d}"
    if lookup.provider == "open_library":
        if lookup.identity.startswith("/works/"):
            return "open-library-work-" + lookup.identity.removeprefix("/works/")
        return "open-library-edition-" + lookup.identity.removeprefix("/books/")
    if lookup.provider == "inventaire":
        return "inventaire-work-" + lookup.identity.replace(":", "-")
    return None
