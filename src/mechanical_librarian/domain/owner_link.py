"""Owner identity links: the record an owner said is a work's book (ADR-0104).

An owner links one catalogue item, keyed by connection, library and item, to
one source record as This book (`work`), This edition (`edition`, an ebook's
ISBN) or This recording (`recording`, an audiobook's ASIN). The link is the
owner's authority about identity, read again at every preparation; it never
fills an identifier field and never decides anything by itself.

This module holds the closed vocabularies, the record-identity grammar for
each scope and source, the link digest the report binds, the one predicate
that decides which scopes anchor the other sources' search, the owner search
card with its contradictions and scope eligibility (S15), and the link state
a work reads (S29).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Final, Literal

from mechanical_librarian.domain.audio_inspection import AudioInspectionField
from mechanical_librarian.domain.audiobook_corroboration import (
    candidate_narrator_disjoint,
    candidate_recording_runtime_veto,
    candidate_runtime_seconds,
    local_claim_runtime_seconds,
)
from mechanical_librarian.domain.evidence_bounds import (
    EvidenceValueError,
    validate_projection_text,
)
from mechanical_librarian.domain.field_outcomes import language_identity
from mechanical_librarian.domain.metadata_report import (
    ASSERTED_ASIN_BASES,
    AUDIO_FILE_FORMATS,
    DURATION_AGREEMENT_BAND_SECONDS,
    OWNER_LINK_CLAIM_BASIS,
    AudioFacts,
    ClaimKind,
    ClaimScope,
    FieldState,
    LocalClaim,
    MetadataReport,
    OwnerLinkFacts,
    ProviderCandidate,
    canonical_asin,
    canonical_json,
    creator_comparison_key,
    echoed_target_values,
    edition_marketplace_evidence,
    identifier_only_candidate,
    isbn_family,
    manifestation_publication_contradicted,
    recording_variant_conflict,
    target_work_identity_context,
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


def owner_link_contradiction(
    scope: OwnerLinkScope,
    candidate: ProviderCandidate,
    *,
    link_claim: LocalClaim | None,
    local_claims: tuple[LocalClaim, ...],
    work_title: str,
    library_languages: tuple[str, ...] = (),
    echoed_languages: tuple[str, ...] = (),
    echoed_narrators: tuple[str, ...] = (),
) -> OwnerLinkContradiction | None:
    """Why a linked edition or recording is not this item's, if it is not.

    The owner chose the record; the file still has a say. An edition link's
    record must not state a print format for the linked ISBN, and a recording
    link's product must state a runtime inside the band of the file's own
    (its lane already demoted one outside it), of no other variant, crediting a
    narrator the recording's own credits name. A work link is never
    re-checked here: its record is the work, whatever the file's edition.
    """
    if scope is OwnerLinkScope.EDITION:
        if link_claim is not None and manifestation_publication_contradicted(
            candidate, (link_claim,)
        ):
            return OwnerLinkContradiction.FORMAT
        return None
    if scope is not OwnerLinkScope.RECORDING:
        return None
    if recording_variant_conflict(
        candidate, local_claims, work_title, library_languages, echoed_languages
    ):
        return OwnerLinkContradiction.VARIANT
    # The runtime must agree positively: a product that states no runtime, or
    # a file that states none, corroborates nothing, so the link is not
    # confirmed and reads contradicted on its runtime (ADR-0104 decision 4).
    if (
        identifier_only_candidate(candidate)
        or not edition_marketplace_evidence(candidate)
        or candidate_runtime_seconds(candidate) is None
        or local_claim_runtime_seconds(local_claims) is None
    ):
        return OwnerLinkContradiction.RUNTIME
    if candidate_narrator_disjoint(candidate, local_claims, echoed_narrators):
        return OwnerLinkContradiction.NARRATORS
    return None


# Owner search (#1477 S8-S15). A search asks one source for at most ten cards
# and a sentinel; each card is projected from the source's own record and then
# judged here against the work's current report, so the confirmation needs no
# second request. Search results are never evidence and are never persisted.

OwnerSearchLane = Literal["title_authors", "isbn", "asin"]
OWNER_SEARCH_LANES: Final = ("title_authors", "isbn", "asin")
# The request asks for one row more than a page; an eleventh row means More.
OWNER_SEARCH_PAGE: Final = 10
OWNER_SEARCH_REQUEST_LIMIT: Final = OWNER_SEARCH_PAGE + 1
MAX_OWNER_SEARCH_AUTHORS: Final = 4
MAX_CARD_PEOPLE: Final = 10


class OwnerSearchFailure(StrEnum):
    """Why one owner search answered nothing (S11 steps 9 and 11)."""

    RATE_LIMITED = "rate_limited"
    QUOTA_EXHAUSTED = "quota_exhausted"
    KEY = "key"
    UNAVAILABLE = "unavailable"
    UNREADABLE = "unreadable"


class OwnerSearchContradiction(StrEnum):
    IDENTIFIER = "identifier"
    COMPILATION = "compilation"
    LANGUAGE = "language"
    CREATORS_DISJOINT = "creators_disjoint"
    RUNTIME = "runtime"


class WithheldScopeReason(StrEnum):
    """Why This edition or This recording is not offered on a card (S15)."""

    NO_ISBN = "no_isbn"
    FORMAT = "format"
    NO_RUNTIME = "no_runtime"
    RUNTIME = "runtime"
    NARRATORS = "narrators"
    VARIANT = "variant"


@dataclass(frozen=True, slots=True)
class OwnerSearchQuery:
    """One owner search as its source is asked it, already validated.

    `isbn` is the owner's ISBN as its ISBN-13 family; `marketplace` is the
    Audible marketplace the search is sent to, and only Audible has one.
    """

    lane: OwnerSearchLane
    title: str | None = None
    authors: tuple[str, ...] = ()
    isbn: str | None = None
    asin: str | None = None
    marketplace: str | None = None

    def __post_init__(self) -> None:
        if (
            self.lane not in OWNER_SEARCH_LANES
            or (self.lane == "title_authors")
            != (self.title is not None and bool(self.authors))
            or len(self.authors) > MAX_OWNER_SEARCH_AUTHORS
            or (self.lane == "isbn") != (self.isbn is not None)
            or (self.lane == "asin") != (self.asin is not None)
            or (self.isbn is not None and not _isbn13(self.isbn))
            or (self.asin is not None and canonical_asin(self.asin) != self.asin)
        ):
            raise OwnerLinkError("owner search query is invalid")


@dataclass(frozen=True, slots=True)
class OwnerSearchCover:
    """Where a card's preview may be fetched: an adapter-admitted URL.

    Process-local: it is held with the search and never sent to the browser.
    `archive_redirects` is Open Library's one fixed redirect route.
    """

    url: str
    archive_redirects: bool = False
    user_agent: str | None = None


@dataclass(frozen=True, slots=True)
class OwnerSearchRecord:
    """One result row as its adapter projected it, before it is judged.

    `identity` is the This book identity (S9), or `None` when the record has
    no lookup identity (a Google Books volume with no ISBN): such a card is
    shown and cannot be chosen. `isbn` is the edition ISBN-13 the card states
    when the adapter knows it better than the candidate's claims do.
    """

    candidate: ProviderCandidate
    identity: str | None
    isbn: str | None = None
    compilation: bool = False
    cover: OwnerSearchCover | None = None


@dataclass(frozen=True, slots=True)
class OwnerSearchAnswer:
    """What one owner search's single request found, or why it found nothing.

    `requested` is whether a request left the host: only those count against
    the hourly limit. `dropped` counts rows that failed their projection or
    identity grammar, and `more` is the sentinel row.
    """

    records: tuple[OwnerSearchRecord, ...] = ()
    more: bool = False
    dropped: int = 0
    failure: OwnerSearchFailure | None = None
    retry_after: int | None = None
    requested: bool = True

    def __post_init__(self) -> None:
        if (
            len(self.records) > OWNER_SEARCH_PAGE
            or not 0 <= self.dropped <= OWNER_SEARCH_REQUEST_LIMIT
            or (self.failure is not None and (self.records or self.more))
        ):
            raise OwnerLinkError("owner search answer is invalid")


def owner_search_failure(
    status: str, category: str, retry_after: float | None = None
) -> OwnerSearchAnswer:
    """Name a failed owner search from its provider status and category."""
    failure = {
        "rate_limited": OwnerSearchFailure.RATE_LIMITED,
        "authentication": OwnerSearchFailure.KEY,
        "authorization": OwnerSearchFailure.KEY,
        "malformed": OwnerSearchFailure.UNREADABLE,
        "oversized": OwnerSearchFailure.UNREADABLE,
        "candidate_limit_exceeded": OwnerSearchFailure.UNREADABLE,
        "query_limit_exceeded": OwnerSearchFailure.UNREADABLE,
    }.get(status, OwnerSearchFailure.UNAVAILABLE)
    if failure is OwnerSearchFailure.RATE_LIMITED and category == "quota_exhausted":
        failure = OwnerSearchFailure.QUOTA_EXHAUSTED
    return OwnerSearchAnswer(
        failure=failure,
        retry_after=None if retry_after is None else max(1, int(retry_after)),
    )


@dataclass(frozen=True, slots=True)
class OwnerSearchWork:
    """What a card is judged against: the work's current report (S15)."""

    medium: str
    title: str | None
    authors: tuple[str, ...]
    isbn: str | None
    asin: str | None
    runtime_seconds: int | None
    library_languages: tuple[str, ...]
    local_claims: tuple[LocalClaim, ...]
    audio: AudioFacts | None = None
    echoed_languages: tuple[str, ...] = ()
    echoed_narrators: tuple[str, ...] = ()

    @classmethod
    def from_report(cls, report: MetadataReport) -> OwnerSearchWork:
        context = target_work_identity_context(report.target)
        local = tuple(
            claim
            for claim in report.local_claims
            if claim.state is FieldState.VALID
            and claim.canonical is not None
            and claim.basis not in ASSERTED_ASIN_BASES
        )
        isbns = {
            family
            for claim in local
            if claim.kind is ClaimKind.ISBN
            and (family := isbn_family(claim.canonical or "")) is not None
        }
        asins = {
            value
            for claim in local
            if claim.kind is ClaimKind.ASIN
            and (value := canonical_asin(claim.canonical)) is not None
        }
        audio = report.audio
        fields = report.target.fields
        return cls(
            "audiobook" if report.file.format in AUDIO_FILE_FORMATS else "ebook",
            None if context is None else context[0],
            () if context is None else context[1],
            isbns.pop() if len(isbns) == 1 else None,
            asins.pop() if len(asins) == 1 else None,
            local_claim_runtime_seconds(report.local_claims),
            report.library_languages,
            report.local_claims,
            audio,
            echoed_target_values(audio, fields, AudioInspectionField.LANGUAGE),
            echoed_target_values(audio, fields, AudioInspectionField.CONTRIBUTOR),
        )


@dataclass(frozen=True, slots=True)
class OwnerSearchCard:
    """One card the owner may choose, judged against the work (S9, S15).

    `contradictions` carries each applicable contradiction with the values its
    line names; `withheld` names why This edition or This recording is not
    offered. The record identities stay here, server-side: the browser sends
    only a position and a scope.
    """

    position: int
    provider: str
    title: str
    subtitle: str | None
    creators: tuple[str, ...]
    narrators: tuple[str, ...]
    year: int | None
    publisher: str | None
    language: str | None
    isbn: str | None
    asin: str | None
    runtime_seconds: int | None
    identity: str | None
    scopes: tuple[OwnerLinkScope, ...]
    withheld: tuple[tuple[OwnerLinkScope, WithheldScopeReason], ...] = ()
    contradictions: tuple[tuple[OwnerSearchContradiction, dict[str, object]], ...] = ()
    cover: OwnerSearchCover | None = field(default=None, compare=False)

    @property
    def choosable(self) -> bool:
        return self.identity is not None

    def target(self, scope: OwnerLinkScope) -> OwnerLinkTarget:
        """The link target choosing this card as `scope` records."""
        if scope not in self.scopes or self.identity is None:
            raise OwnerLinkError("owner link scope is not offered")
        identity = {
            OwnerLinkScope.WORK: self.identity,
            OwnerLinkScope.EDITION: self.isbn,
            OwnerLinkScope.RECORDING: self.asin,
        }[scope]
        return OwnerLinkTarget(
            scope, self.provider, identity or "", self.title, self.creators
        )

    def to_data(self) -> dict[str, object]:
        """The card as the browser reads it: no identity, URL or path."""
        return {
            "position": self.position,
            "title": self.title,
            "subtitle": self.subtitle,
            "creators": list(self.creators),
            "narrators": list(self.narrators),
            "year": self.year,
            "publisher": self.publisher,
            "language": self.language,
            "isbn": self.isbn,
            "asin": self.asin,
            "runtime_seconds": self.runtime_seconds,
            "choosable": self.choosable,
            "scopes": [value.value for value in self.scopes],
            "withheld": [
                {"scope": scope.value, "reason": reason.value}
                for scope, reason in self.withheld
            ],
            "contradictions": [
                {"code": code.value, **values} for code, values in self.contradictions
            ],
            "preview": self.cover is not None,
        }


def owner_search_cards(
    provider: str,
    records: tuple[OwnerSearchRecord, ...],
    work: OwnerSearchWork,
) -> tuple[tuple[OwnerSearchCard, ...], int]:
    """Judge one answer's records into cards, dropping and counting failures.

    A record whose identity fails its grammar, that repeats an earlier
    record's identity, or that states no title is dropped and counted (S9).
    """
    cards: list[OwnerSearchCard] = []
    seen: set[str] = set()
    dropped = 0
    for record in records:
        try:
            card = _owner_search_card(provider, len(cards), record, work)
        except (OwnerLinkError, ValueError):
            dropped += 1
            continue
        if card.identity is not None and card.identity in seen:
            dropped += 1
            continue
        if card.identity is not None:
            seen.add(card.identity)
        cards.append(card)
    return tuple(cards), dropped


def _owner_search_card(
    provider: str, position: int, record: OwnerSearchRecord, work: OwnerSearchWork
) -> OwnerSearchCard:
    if record.identity is not None and not record_identity_valid(
        OwnerLinkScope.WORK, provider, record.identity
    ):
        raise OwnerLinkError("owner search identity is invalid")
    candidate = record.candidate
    title = _stated(candidate, ClaimKind.TITLE)
    if title is None:
        raise OwnerLinkError("owner search card has no title")
    isbn = record.isbn or next(
        (
            family
            for value in _stated_all(candidate, ClaimKind.ISBN)
            if (family := isbn_family(value)) is not None
        ),
        None,
    )
    if isbn is not None and not _isbn13(isbn):
        raise OwnerLinkError("owner search ISBN is invalid")
    asin = next(
        (
            value
            for raw in _stated_all(candidate, ClaimKind.ASIN)
            if (value := canonical_asin(raw)) is not None
        ),
        None,
    )
    creators = _people(candidate, ClaimKind.CREATOR, None)
    date = _stated(candidate, ClaimKind.PUBLICATION_DATE)
    card = OwnerSearchCard(
        position=position,
        provider=provider,
        title=title,
        subtitle=_stated(candidate, ClaimKind.SUBTITLE),
        creators=creators,
        narrators=_people(candidate, ClaimKind.CONTRIBUTOR, "narrator"),
        year=int(date[:4]) if date is not None and date[:4].isdigit() else None,
        publisher=_stated(candidate, ClaimKind.PUBLISHER),
        language=_stated(candidate, ClaimKind.LANGUAGE),
        isbn=isbn,
        asin=asin,
        runtime_seconds=candidate_runtime_seconds(candidate),
        identity=record.identity,
        scopes=(),
        cover=record.cover,
    )
    scopes, withheld = _card_scopes(card, candidate, work)
    return replace(
        card,
        scopes=scopes,
        withheld=withheld,
        contradictions=_card_contradictions(card, record, work),
    )


def _stated_all(candidate: ProviderCandidate, kind: ClaimKind) -> tuple[str, ...]:
    return tuple(
        claim.value
        for claim in candidate.claims
        if claim.kind is kind
        and claim.state is FieldState.VALID
        and claim.value is not None
    )


def _stated(candidate: ProviderCandidate, kind: ClaimKind) -> str | None:
    values = _stated_all(candidate, kind)
    return values[0] if values else None


def _people(
    candidate: ProviderCandidate, kind: ClaimKind, role: str | None
) -> tuple[str, ...]:
    names: dict[str, str] = {}
    for claim in candidate.claims:
        if (
            claim.kind is kind
            and claim.state is FieldState.VALID
            and claim.value is not None
            and (role is None or claim.role == role)
        ):
            names.setdefault(claim.value.casefold(), claim.value)
    return tuple(names.values())[:MAX_CARD_PEOPLE]


def _card_scopes(
    card: OwnerSearchCard, candidate: ProviderCandidate, work: OwnerSearchWork
) -> tuple[
    tuple[OwnerLinkScope, ...], tuple[tuple[OwnerLinkScope, WithheldScopeReason], ...]
]:
    """This book always; This edition or This recording only when it passes."""
    if card.identity is None:
        return (), ()
    if work.medium == "ebook" and card.provider in EDITION_LINK_AUTHORITIES:
        reason = _edition_withheld(card, candidate)
        scope = OwnerLinkScope.EDITION
    elif work.medium == "audiobook" and card.provider == "audible_catalog":
        reason = _recording_withheld(card, candidate, work)
        scope = OwnerLinkScope.RECORDING
    else:
        return (OwnerLinkScope.WORK,), ()
    if reason is None:
        return (OwnerLinkScope.WORK, scope), ()
    return (OwnerLinkScope.WORK,), ((scope, reason),)


def _edition_withheld(
    card: OwnerSearchCard, candidate: ProviderCandidate
) -> WithheldScopeReason | None:
    if card.isbn is None or not record_identity_valid(
        OwnerLinkScope.EDITION, card.provider, card.isbn
    ):
        return WithheldScopeReason.NO_ISBN
    claim = owner_link_claim(OwnerLinkScope.EDITION, None, card.isbn)
    if claim is not None and manifestation_publication_contradicted(
        candidate, (claim,)
    ):
        return WithheldScopeReason.FORMAT
    return None


def _recording_withheld(
    card: OwnerSearchCard, candidate: ProviderCandidate, work: OwnerSearchWork
) -> WithheldScopeReason | None:
    """ADR-0088's re-check, as preparation will run it on the linked product."""
    if card.runtime_seconds is None:
        return WithheldScopeReason.NO_RUNTIME
    if candidate_recording_runtime_veto(candidate, work.local_claims, work.audio):
        return WithheldScopeReason.RUNTIME
    reason = owner_link_contradiction(
        OwnerLinkScope.RECORDING,
        candidate,
        link_claim=None,
        local_claims=work.local_claims,
        work_title=work.title or "",
        library_languages=work.library_languages,
        echoed_languages=work.echoed_languages,
        echoed_narrators=work.echoed_narrators,
    )
    return None if reason is None else WithheldScopeReason(reason.value)


def _card_contradictions(
    card: OwnerSearchCard, record: OwnerSearchRecord, work: OwnerSearchWork
) -> tuple[tuple[OwnerSearchContradiction, dict[str, object]], ...]:
    """The confirmation's contradiction lines, each only when it applies."""
    found: list[tuple[OwnerSearchContradiction, dict[str, object]]] = []
    for name, stated, own in (
        ("isbn", card.isbn, work.isbn),
        ("asin", card.asin, work.asin),
    ):
        if stated is not None and own is not None and stated != own:
            found.append(
                (
                    OwnerSearchContradiction.IDENTIFIER,
                    {"identifier": name, "record": stated, "work": own},
                )
            )
    if record.compilation:
        found.append((OwnerSearchContradiction.COMPILATION, {}))
    if _language_outside(card.language, work.library_languages):
        found.append((OwnerSearchContradiction.LANGUAGE, {"language": card.language}))
    if (
        card.creators
        and work.authors
        and not (
            {creator_comparison_key(value) for value in card.creators}
            & {creator_comparison_key(value) for value in work.authors}
        )
    ):
        found.append((OwnerSearchContradiction.CREATORS_DISJOINT, {}))
    if (
        work.medium == "audiobook"
        and card.runtime_seconds is not None
        and work.runtime_seconds is not None
        and abs(card.runtime_seconds - work.runtime_seconds)
        > DURATION_AGREEMENT_BAND_SECONDS
    ):
        found.append(
            (
                OwnerSearchContradiction.RUNTIME,
                {"record": card.runtime_seconds, "work": work.runtime_seconds},
            )
        )
    return tuple(found)


def _language_outside(language: str | None, library: tuple[str, ...]) -> bool:
    """A known language outside every declared one; unknown always passes."""
    stated = None if language is None else language_identity(language)
    declared = {
        identity.base
        for value in library
        if (identity := language_identity(value)) is not None
    }
    return (
        stated is not None
        and stated.base != "und"
        and bool(declared)
        and stated.base not in declared
    )


# Link state (S29): what a selected work's link reads, derived per work from
# its current link, its current report's link facts, the paused flag and the
# next automatic retry recovery has scheduled.

OwnerLinkStateName = Literal[
    "linked", "preparing", "waiting", "contradicted", "needs_replacing"
]
_NEEDS_REPLACING = frozenset(
    {
        OwnerLinkOutcome.MISSING,
        OwnerLinkOutcome.MERGED,
        OwnerLinkOutcome.SOURCE_DISABLED,
        OwnerLinkOutcome.MEDIUM_CHANGED,
    }
)


@dataclass(frozen=True, slots=True)
class OwnerLinkState:
    state: OwnerLinkStateName
    reason: str | None = None
    retry_at: int | None = None
    paused: bool = False


def link_state(
    link: OwnerLink,
    facts: OwnerLinkFacts | None,
    *,
    paused: bool,
    next_retry_at: int | None = None,
) -> OwnerLinkState:
    """The state of a work's current link (S29).

    `preparing` while no current report binds the link's digest (Q5),
    whatever the paused flag says; the flag travels so the line can say
    preparation waits. A report that binds it reads its outcome: `linked`
    when the record was admitted, `waiting` with recovery's next retry when
    the source did not answer, `contradicted` with the re-check's reason, and
    `needs_replacing` with the outcome that makes the link unusable.
    """
    if facts is None or facts.digest != link.digest:
        return OwnerLinkState("preparing", paused=paused)
    outcome = OwnerLinkOutcome(facts.outcome)
    if outcome is OwnerLinkOutcome.ADMITTED:
        return OwnerLinkState("linked", paused=paused)
    if outcome is OwnerLinkOutcome.UNAVAILABLE:
        return OwnerLinkState("waiting", retry_at=next_retry_at, paused=paused)
    if outcome is OwnerLinkOutcome.CONTRADICTED:
        return OwnerLinkState("contradicted", facts.reason, paused=paused)
    if outcome in _NEEDS_REPLACING:
        return OwnerLinkState("needs_replacing", outcome.value, paused=paused)
    raise OwnerLinkError("owner link outcome is unknown")
