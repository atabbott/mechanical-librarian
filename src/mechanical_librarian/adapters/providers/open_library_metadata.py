"""Bounded, report-only Open Library metadata observation."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import httpx

from mechanical_librarian.application.metadata_report import (
    MetadataDiscoveryPlan,
    MetadataLookup,
    ProviderCoverDescriptor,
    ProviderLaneResult,
    merge_provider_cover_descriptors,
    provider_isbn_applicability,
    provider_observation,
    qualify_provider_observation,
)
from mechanical_librarian.domain.cover_evidence import (
    CoverContext,
    CoverContextField,
    CoverDesignation,
    CoverEvidenceError,
    CoverProvenance,
    CoverSourceKind,
    CoverSourceState,
    cover_source_id,
)
from mechanical_librarian.domain.evidence_bounds import (
    EvidenceValueError,
    MAX_FIELD_VALUE_LENGTH,
    validate_projection_text,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    LocalClaim,
    MAX_DESCRIPTION_CHARACTERS,
    MAX_PROVIDER_EVIDENCE,
    MAX_SCALAR_CHARACTERS,
    ProviderCandidate,
    ProviderClaim,
    ProviderEvidence,
    ProviderEvidenceKind,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    comparison_links,
    isbn_family,
    normalize_title,
)


OPEN_LIBRARY_ORIGIN = "https://openlibrary.org"
OPEN_LIBRARY_SEARCH_URL = OPEN_LIBRARY_ORIGIN + "/search.json"
OPEN_LIBRARY_COVERS_ORIGIN = "https://covers.openlibrary.org"
_EDITION_KEY = re.compile(r"^/books/OL[1-9][0-9]*M$")
_WORK_KEY = re.compile(r"^/works/OL[1-9][0-9]*W$")
_AUTHOR_KEY = re.compile(r"^/authors/OL[1-9][0-9]*A$")
_LANGUAGE_KEY = re.compile(r"^/languages/([a-z]{2,3})$")
_MAX_SEARCH_ROWS = 3
_MAX_VALUES = 8
_MAX_AGGREGATE_RESPONSE_BYTES = 2_097_152


@dataclass(frozen=True, slots=True)
class _CoverId:
    value: int | None
    terminal_state: CoverSourceState | None = None


class OpenLibraryPacer:
    """Keep request starts at least one second apart for one adapter run."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._last_started: float | None = None

    def wait(self, deadline: float | None = None) -> bool:
        now = self._clock()
        if self._last_started is not None:
            delay = max(0.0, self._last_started + 1.0 - now)
            if deadline is not None and now + delay >= deadline:
                return False
            if delay:
                self._sleep(delay)
                now = self._clock()
        if deadline is not None and now >= deadline:
            return False
        self._last_started = now
        return True


class OpenLibraryMetadataProvider:
    """Fetch one exact ISBN-family observation through the fixed public API."""

    def __init__(
        self,
        name: str,
        client: httpx.Client,
        maximum_bytes: int,
        timeout_seconds: float = 5.0,
        *,
        pacer: OpenLibraryPacer | None = None,
    ) -> None:
        if (
            type(maximum_bytes) is not int
            or isinstance(maximum_bytes, bool)
            or maximum_bytes < 1
        ):
            raise ValueError("provider response bound is invalid")
        if (
            type(timeout_seconds) not in {int, float}
            or isinstance(timeout_seconds, bool)
            or not 0 < timeout_seconds <= 60
        ):
            raise ValueError("provider timeout is invalid")
        self._name = name
        self._client = client
        self._maximum_bytes = maximum_bytes
        self._remaining_response_bytes = min(
            maximum_bytes * 45, _MAX_AGGREGATE_RESPONSE_BYTES
        )
        self._timeout_seconds = float(timeout_seconds)
        self._pacer = pacer if pacer is not None else OpenLibraryPacer()
        self._called = False
        self.cover_descriptors: tuple[ProviderCoverDescriptor, ...] = ()
        self.cover_pacer = self._pacer
        self.cover_kind = CoverSourceKind.OPEN_LIBRARY
        self.cover_allow_redirects = False
        self.cover_user_agent = client.headers["User-Agent"]

    def observe(
        self,
        plan: MetadataDiscoveryPlan | MetadataLookup,
        *,
        deadline: float | None = None,
    ) -> ProviderObservation:
        if self._called:
            return ProviderObservation(
                self._name,
                ProviderStatus.UNAVAILABLE,
                query_kind=(
                    plan.kind
                    if type(plan) is MetadataLookup
                    else ProviderQueryKind.DISCOVERY
                ),
            )
        self._called = True
        if type(plan) is MetadataLookup:
            try:
                return self._observe_lookup(plan, deadline=deadline)
            finally:
                self._client.close()
        if type(plan) is not MetadataDiscoveryPlan:
            raise ValueError("provider lookup is invalid")
        if plan.overflow:
            self._client.close()
            return ProviderObservation(
                self._name, ProviderStatus.QUERY_LIMIT_EXCEEDED
            )
        results: list[ProviderLaneResult] = []
        descriptors: tuple[ProviderCoverDescriptor, ...] = ()
        try:
            for lane in plan.lanes:
                self.cover_descriptors = ()
                lookup = MetadataLookup(
                    lane.kind,
                    plan.local_claims,
                    lane.title,
                    lane.authors,
                    lane.matched_family,
                )
                observation = qualify_provider_observation(
                    self._observe_lookup(lookup, deadline=deadline), lookup
                )
                results.append(
                    ProviderLaneResult(
                        lane, observation.status, observation.candidates
                    )
                )
                merged = merge_provider_cover_descriptors(
                    descriptors,
                    (
                        self.cover_descriptors
                        if observation.status is ProviderStatus.CANDIDATES
                        else ()
                    ),
                )
                if merged is None:
                    results[-1] = ProviderLaneResult(
                        lane, ProviderStatus.MALFORMED
                    )
                else:
                    descriptors = merged
        finally:
            self._client.close()
        self.cover_descriptors = descriptors
        return provider_observation(
            self._name,
            _canonical_discovery_results(self._name, tuple(results)),
        )

    def _observe_lookup(
        self,
        lookup: MetadataLookup,
        *,
        deadline: float | None,
    ) -> ProviderObservation:
        local_claims = lookup.local_claims
        usable = tuple(
            sorted(
                (
                    claim
                    for claim in local_claims
                    if claim.kind is ClaimKind.ISBN
                    and claim.canonical is not None
                    and (
                        (
                            lookup.matched_family is None
                            and claim.applicability in {"ebook", "unknown"}
                            and claim.basis == "opening_matter"
                            and claim.rear_provenance is None
                        )
                        or (
                            lookup.matched_family is not None
                            and isbn_family(claim.canonical)
                            == lookup.matched_family
                        )
                    )
                ),
                key=lambda claim: (claim.canonical or "", claim.reference),
            )
        )
        families = {
            family
            for claim in usable
            if (family := isbn_family(claim.canonical)) is not None
        }
        if lookup.kind is ProviderQueryKind.ISBN and len(families) != 1:
            self._client.close()
            return ProviderObservation(
                self._name,
                ProviderStatus.INSUFFICIENT_INPUT,
                query_kind=lookup.kind,
            )
        if lookup.kind is ProviderQueryKind.ISBN:
            family: str | None = next(iter(families))
            references = tuple(
                sorted(
                    claim.reference
                    for claim in usable
                    if isbn_family(claim.canonical) == family
                )
            )
        elif lookup.kind is ProviderQueryKind.WORK:
            family = None
            references = ()
        else:
            self._client.close()
            return ProviderObservation(
                self._name,
                ProviderStatus.INSUFFICIENT_INPUT,
                query_kind=lookup.kind,
            )
        def result(
            status: ProviderStatus,
            candidates: tuple[ProviderCandidate, ...] = (),
        ) -> ProviderObservation:
            return ProviderObservation(
                self._name,
                status,
                references,
                candidates,
                lookup.kind,
            )
        try:
            with nullcontext():
                if lookup.kind is ProviderQueryKind.ISBN:
                    search = self._request(
                        OPEN_LIBRARY_SEARCH_URL,
                        params={
                            "q": f"isbn:{family}",
                            "fields": "key,editions,editions.key,editions.isbn",
                            "limit": str(_MAX_SEARCH_ROWS),
                        },
                        deadline=deadline,
                    )
                    admitted = tuple(
                        (edition, work, ())
                        for edition, work in _admitted_candidates(
                            search, family or ""
                        )
                    )
                else:
                    search = self._request(
                        OPEN_LIBRARY_SEARCH_URL,
                        params={
                            "title": lookup.title or "",
                            "author": " ".join(lookup.authors),
                            "fields": "key,author_name,editions,editions.key",
                            "limit": str(_MAX_SEARCH_ROWS),
                        },
                        deadline=deadline,
                    )
                    admitted = _admitted_work_candidates(search)
                if len(admitted) > 2:
                    return result(ProviderStatus.CANDIDATE_LIMIT_EXCEEDED)
                if not admitted:
                    return result(ProviderStatus.NO_MATCH)
                work_documents: dict[str, dict[str, Any]] = {}
                candidates: list[ProviderCandidate] = []
                cover_candidates: list[
                    tuple[
                        str,
                        _CoverId,
                        str,
                        _CoverId,
                        dict[str, Any],
                        dict[str, Any],
                    ]
                ] = []
                for edition_key, work_key, work_authors in admitted:
                    edition = self._request(
                        OPEN_LIBRARY_ORIGIN + edition_key + ".json",
                        deadline=deadline,
                    )
                    if work_key not in work_documents:
                        work_documents[work_key] = self._request(
                            OPEN_LIBRARY_ORIGIN + work_key + ".json",
                            deadline=deadline,
                        )
                    candidate, edition_cover, work_cover = _candidate(
                        self._name,
                        edition_key,
                        work_key,
                        edition,
                        work_documents[work_key],
                        local_claims,
                        family,
                        work_authors,
                    )
                    candidates.append(candidate)
                    cover_candidates.append(
                        (
                            edition_key,
                            edition_cover,
                            work_key,
                            work_cover,
                            edition,
                            work_documents[work_key],
                        )
                    )
                self.cover_descriptors = _cover_descriptors(
                    self._name,
                    lookup.kind,
                    cover_candidates,
                    self._pacer.wait,
                    self.cover_user_agent,
                )
        except _ProviderFailure as error:
            return result(error.status)
        except (
            EvidenceValueError,
            KeyError,
            TypeError,
            ValueError,
            UnicodeError,
            RecursionError,
            json.JSONDecodeError,
        ):
            return result(ProviderStatus.MALFORMED)
        return result(
            ProviderStatus.CANDIDATES,
            tuple(sorted(candidates, key=lambda candidate: candidate.identity)),
        )

    def _request(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        deadline: float | None,
    ) -> dict[str, Any]:
        if self._remaining_response_bytes <= 0:
            raise _ProviderFailure(ProviderStatus.OVERSIZED)
        if not self._pacer.wait(deadline):
            raise _ProviderFailure(ProviderStatus.UNAVAILABLE)
        remaining = self._timeout_seconds
        if deadline is not None:
            remaining = min(remaining, deadline - time.monotonic())
        if remaining <= 0:
            raise _ProviderFailure(ProviderStatus.UNAVAILABLE)
        try:
            with self._client.stream(
                "GET",
                url,
                params=params,
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                },
                timeout=remaining,
            ) as response:
                if response.status_code == 429:
                    raise _ProviderFailure(ProviderStatus.RATE_LIMITED)
                if response.status_code != 200:
                    raise _ProviderFailure(ProviderStatus.UNAVAILABLE)
                if (
                    response.headers.get("content-type", "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                    != "application/json"
                    or response.headers.get("content-encoding", "identity")
                    .strip()
                    .lower()
                    not in {"", "identity"}
                ):
                    raise _ProviderFailure(ProviderStatus.MALFORMED)
                payload = _body(
                    response,
                    min(
                        self._maximum_bytes,
                        self._remaining_response_bytes,
                    ),
                    deadline=deadline,
                )
                self._remaining_response_bytes -= len(payload)
        except _ProviderFailure as error:
            if error.status is ProviderStatus.OVERSIZED:
                self._remaining_response_bytes = 0
            raise
        except (httpx.HTTPError, OSError, ValueError):
            raise _ProviderFailure(ProviderStatus.UNAVAILABLE) from None
        try:
            value = json.loads(
                payload.decode("utf-8", "strict"),
                object_pairs_hook=_unique_object,
                parse_constant=_reject_number,
                parse_float=Decimal,
            )
        except (
            TypeError,
            ValueError,
            UnicodeError,
            RecursionError,
            json.JSONDecodeError,
        ):
            raise _ProviderFailure(ProviderStatus.MALFORMED) from None
        if type(value) is not dict:
            raise _ProviderFailure(ProviderStatus.MALFORMED)
        return value


def _admitted_candidates(
    source: dict[str, Any], family: str
) -> tuple[tuple[str, str], ...]:
    docs = source.get("docs")
    if type(docs) is not list or len(docs) > _MAX_SEARCH_ROWS:
        raise ValueError("search result is malformed")
    admitted: dict[str, str] = {}
    for raw_work in docs:
        work = _mapping(raw_work)
        work_key = _key(work.get("key"), _WORK_KEY)
        editions = _mapping(work.get("editions"))
        edition_docs = editions.get("docs")
        if type(edition_docs) is not list:
            raise ValueError("search editions are malformed")
        for raw_edition in edition_docs:
            edition = _mapping(raw_edition)
            edition_key = _key(edition.get("key"), _EDITION_KEY)
            families = _search_isbn_families(edition.get("isbn"))
            if family not in families:
                continue
            previous = admitted.setdefault(edition_key, work_key)
            if previous != work_key:
                raise ValueError("search edition work is ambiguous")
            if len(admitted) > 2:
                return tuple(sorted(admitted.items()))
    return tuple(sorted(admitted.items()))


def _admitted_work_candidates(
    source: dict[str, Any],
) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    docs = source.get("docs")
    if type(docs) is not list or len(docs) > _MAX_SEARCH_ROWS:
        raise ValueError("search result is malformed")
    admitted: dict[tuple[str, str], tuple[str, ...]] = {}
    for raw_work in docs:
        work = _mapping(raw_work)
        work_key = _key(work.get("key"), _WORK_KEY)
        authors = _author_list(work.get("author_name", []), _MAX_VALUES)
        editions = _mapping(work.get("editions"))
        edition_docs = editions.get("docs")
        if type(edition_docs) is not list:
            raise ValueError("search editions are malformed")
        for raw_edition in edition_docs:
            edition = _mapping(raw_edition)
            edition_key = _key(edition.get("key"), _EDITION_KEY)
            previous = admitted.setdefault((edition_key, work_key), authors)
            if previous != authors:
                raise ValueError("search work authors are ambiguous")
            if len(admitted) > 2:
                break
    return tuple(
        (edition, work, authors)
        for (edition, work), authors in sorted(admitted.items())
    )


def _candidate(
    provider: str,
    edition_key: str,
    work_key: str,
    edition: dict[str, Any],
    work: dict[str, Any],
    local_claims: tuple[LocalClaim, ...],
    requested_family: str | None,
    work_authors: tuple[str, ...] = (),
) -> tuple[ProviderCandidate, _CoverId, _CoverId]:
    if _key(edition.get("key"), _EDITION_KEY) != edition_key:
        raise ValueError("edition identity is malformed")
    if _key(work.get("key"), _WORK_KEY) != work_key:
        raise ValueError("work identity is malformed")
    relations = tuple(
        _key(_mapping(value).get("key"), _WORK_KEY)
        for value in _object_list(edition.get("works"), 8)
    )
    if relations != (work_key,):
        raise ValueError("edition work relation is malformed")
    identity = "open-library-edition-" + edition_key.removeprefix("/books/")
    work_identity = "open-library-work-" + work_key.removeprefix("/works/")
    claims: list[ProviderClaim] = []

    def add(
        kind: ClaimKind,
        value: object,
        scope: ClaimScope,
        applicability: str = "not_applicable",
        role: str | None = None,
        state: FieldState | None = None,
    ) -> None:
        claims.append(
            _claim(
                provider,
                identity,
                kind,
                value,
                scope,
                applicability,
                role,
                local_claims,
                len(claims),
                state,
            )
        )

    edition_isbns: list[str] = []
    for key in ("isbn_10", "isbn_13"):
        for value in _string_list(edition.get(key, []), _MAX_VALUES):
            compact = "".join(
                character
                for character in value
                if character.isdigit() or character in "Xx"
            ).upper()
            if isbn_family(compact) is None:
                raise ValueError("edition ISBN is malformed")
            edition_isbns.append(compact)
    if requested_family is not None and requested_family not in {
        family
        for value in edition_isbns
        if (family := isbn_family(value)) is not None
    }:
        raise ValueError("edition ISBN family is missing")
    for compact in sorted(set(edition_isbns)):
        add(
            ClaimKind.ISBN,
            compact,
            ClaimScope.EDITION,
            applicability=provider_isbn_applicability(compact, local_claims),
        )
    add(ClaimKind.ASIN, None, ClaimScope.EDITION, state=FieldState.UNSUPPORTED)
    add(ClaimKind.TITLE, edition.get("title"), ClaimScope.EDITION)
    add(ClaimKind.SUBTITLE, edition.get("subtitle"), ClaimScope.EDITION)
    add(
        ClaimKind.PUBLICATION_DATE,
        edition.get("publish_date"),
        ClaimScope.EDITION,
    )
    for publisher in _string_list(edition.get("publishers", []), _MAX_VALUES):
        add(ClaimKind.PUBLISHER, publisher, ClaimScope.EDITION)
    if not edition.get("publishers"):
        add(ClaimKind.PUBLISHER, None, ClaimScope.EDITION)
    add(
        ClaimKind.IMPRINT,
        None,
        ClaimScope.EDITION,
        state=FieldState.UNSUPPORTED,
    )
    for language in _object_list(edition.get("languages", []), _MAX_VALUES):
        match = _LANGUAGE_KEY.fullmatch(_key(language.get("key"), _LANGUAGE_KEY))
        if match is None:
            raise ValueError("edition language is malformed")
        add(ClaimKind.LANGUAGE, match.group(1), ClaimScope.EDITION)
    if not edition.get("languages"):
        add(ClaimKind.LANGUAGE, None, ClaimScope.EDITION)
    add(
        ClaimKind.CREATOR,
        None,
        ClaimScope.EDITION,
        state=FieldState.UNSUPPORTED,
    )
    add(
        ClaimKind.CONTRIBUTOR,
        None,
        ClaimScope.EDITION,
        state=FieldState.UNSUPPORTED,
    )

    add(ClaimKind.TITLE, work.get("title"), ClaimScope.WORK)
    add(ClaimKind.DESCRIPTION, _description(work.get("description")), ClaimScope.WORK)
    if work_authors:
        for author in work_authors:
            add(
                ClaimKind.CREATOR,
                author,
                ClaimScope.WORK,
                role="author",
            )
    else:
        add(
            ClaimKind.CREATOR,
            None,
            ClaimScope.WORK,
            state=FieldState.UNSUPPORTED,
        )
    add(
        ClaimKind.CONTRIBUTOR,
        None,
        ClaimScope.WORK,
        state=FieldState.UNSUPPORTED,
    )
    for kind in (
        ClaimKind.SERIES,
        ClaimKind.SERIES_SEQUENCE,
        ClaimKind.TAG,
        ClaimKind.GENRE,
    ):
        add(kind, None, ClaimScope.WORK, state=FieldState.UNSUPPORTED)

    evidence: set[ProviderEvidence] = {
        ProviderEvidence(
            ProviderEvidenceKind.EDITION_WORK_RELATION,
            work_identity,
            ClaimScope.EDITION,
        )
    }
    _provenance(evidence, edition, ClaimScope.EDITION)
    _provenance(evidence, work, ClaimScope.WORK)
    for author in _object_list(edition.get("authors", []), _MAX_VALUES):
        evidence.add(
            ProviderEvidence(
                ProviderEvidenceKind.AUTHOR_IDENTITY,
                _author_identity(_key(author.get("key"), _AUTHOR_KEY)),
                ClaimScope.EDITION,
            )
        )
    for contributor in _object_list(work.get("authors", []), _MAX_VALUES):
        author = _mapping(contributor.get("author"))
        role = _mapping(contributor.get("type")).get("key")
        if role != "/type/author_role":
            raise ValueError("work contributor role is malformed")
        evidence.add(
            ProviderEvidence(
                ProviderEvidenceKind.CONTRIBUTOR_IDENTITY,
                _author_identity(_key(author.get("key"), _AUTHOR_KEY)),
                ClaimScope.WORK,
                "author",
            )
        )
    for field, kind in (
        ("oclc_numbers", ProviderEvidenceKind.OCLC),
        ("lccn", ProviderEvidenceKind.LCCN),
        ("dewey_decimal_class", ProviderEvidenceKind.DEWEY_CLASSIFICATION),
        ("lc_classifications", ProviderEvidenceKind.LC_CLASSIFICATION),
        ("publish_places", ProviderEvidenceKind.PUBLICATION_PLACE),
    ):
        _string_evidence(evidence, edition, field, kind, ClaimScope.EDITION)
    for field, kind in (
        ("subjects", ProviderEvidenceKind.SUBJECT),
        ("subject_people", ProviderEvidenceKind.PERSON),
        ("subject_places", ProviderEvidenceKind.PLACE),
        ("subject_times", ProviderEvidenceKind.TIME),
    ):
        _string_evidence(evidence, work, field, kind, ClaimScope.WORK)
    _identifier_evidence(evidence, edition, ClaimScope.EDITION)
    _identifier_evidence(evidence, work, ClaimScope.WORK)
    for field, kind in (
        ("physical_format", ProviderEvidenceKind.PHYSICAL_FORMAT),
        ("pagination", ProviderEvidenceKind.PAGINATION),
        ("copyright_date", ProviderEvidenceKind.COPYRIGHT_DATE),
    ):
        if field in edition and edition[field] is not None:
            evidence.add(
                ProviderEvidence(kind, _text(edition[field]), ClaimScope.EDITION)
            )
    if "number_of_pages" in edition and edition["number_of_pages"] is not None:
        pages = edition["number_of_pages"]
        if type(pages) is not int or isinstance(pages, bool) or not 1 <= pages <= 100_000:
            raise ValueError("edition page count is malformed")
        evidence.add(
            ProviderEvidence(
                ProviderEvidenceKind.PAGE_COUNT,
                str(pages),
                ClaimScope.EDITION,
            )
        )
    if "first_publish_date" in work and work["first_publish_date"] is not None:
        evidence.add(
            ProviderEvidence(
                ProviderEvidenceKind.FIRST_PUBLICATION_DATE,
                _text(work["first_publish_date"]),
                ClaimScope.WORK,
            )
        )
    edition_cover = _cover_id(edition)
    work_cover = _cover_id(work)
    if edition_cover.value is not None:
        evidence.add(
            ProviderEvidence(
                ProviderEvidenceKind.EDITION_COVER_ID,
                str(edition_cover.value),
                ClaimScope.EDITION,
            )
        )
    if work_cover.value is not None:
        evidence.add(
            ProviderEvidence(
                ProviderEvidenceKind.WORK_COVER_ID,
                str(work_cover.value),
                ClaimScope.WORK,
            )
        )
    canonical_evidence = tuple(
        sorted(
            evidence,
            key=lambda value: (
                value.scope.value,
                value.kind.value,
                value.value,
                value.role or "",
            ),
        )
    )
    if len(canonical_evidence) > MAX_PROVIDER_EVIDENCE:
        raise ValueError("provider rich evidence exceeds its bound")
    return (
        ProviderCandidate(
            identity,
            work_identity,
            tuple(claims),
            canonical_evidence,
        ),
        edition_cover,
        work_cover,
    )


def _canonical_discovery_results(
    provider: str,
    results: tuple[ProviderLaneResult, ...],
) -> tuple[ProviderLaneResult, ...]:
    """Combine only qualified work authors with independently admitted editions."""
    isbn_candidates: dict[str, list[ProviderCandidate]] = {}
    work_candidates: dict[str, ProviderCandidate] = {}
    for result in results:
        for candidate in result.candidates:
            if result.lane.kind is ProviderQueryKind.ISBN:
                isbn_candidates.setdefault(candidate.identity, []).append(candidate)
            elif result.lane.kind is ProviderQueryKind.WORK:
                work_candidates[candidate.identity] = candidate

    merged: dict[str, ProviderCandidate] = {}
    for identity, work in work_candidates.items():
        editions = isbn_candidates.get(identity, [])
        if (
            not editions
            or editions[0].work_identity != work.work_identity
            or any(
                candidate != editions[0]
                or candidate.work_identity != work.work_identity
                for candidate in editions[1:]
            )
        ):
            continue
        edition = editions[0]
        creators = tuple(
            claim
            for claim in work.claims
            if claim.kind is ClaimKind.CREATOR
            and claim.scope is ClaimScope.WORK
            and claim.state is FieldState.VALID
            and claim.value is not None
            and claim.role == "author"
        )
        if not creators or len(creators) != len(
            {
                claim.value.casefold()
                for claim in creators
                if claim.value is not None
            }
        ):
            continue
        work_claim_content = {
            (
                claim.kind,
                claim.value,
                claim.state,
                claim.scope,
                claim.applicability,
                claim.role,
                claim.links,
            )
            for claim in work.claims
            if claim.kind is not ClaimKind.CREATOR
        }
        edition_claim_content = {
            (
                claim.kind,
                claim.value,
                claim.state,
                claim.scope,
                claim.applicability,
                claim.role,
                claim.links,
            )
            for claim in edition.claims
            if claim.kind is not ClaimKind.CREATOR
        }
        if (
            not work_claim_content.issubset(edition_claim_content)
            or not set(work.evidence).issubset(edition.evidence)
        ):
            continue
        claims = [
            claim
            for claim in edition.claims
            if not (
                claim.kind is ClaimKind.CREATOR
                and claim.scope is ClaimScope.WORK
            )
        ]
        raw_order = max((claim.raw_order for claim in claims), default=-1) + 1
        claims.extend(
            ProviderClaim.create(
                provider=provider,
                candidate=identity,
                kind=creator.kind,
                value=creator.value,
                state=creator.state,
                scope=creator.scope,
                applicability=creator.applicability,
                role=creator.role,
                raw_order=raw_order + index,
                links=creator.links,
            )
            for index, creator in enumerate(creators)
        )
        merged[identity] = ProviderCandidate(
            identity,
            edition.work_identity,
            tuple(claims),
            edition.evidence,
        )
    if not merged:
        return results
    return tuple(
        ProviderLaneResult(
            result.lane,
            result.status,
            tuple(
                merged.get(candidate.identity, candidate)
                for candidate in result.candidates
            ),
        )
        for result in results
    )


def _claim(
    provider: str,
    identity: str,
    kind: ClaimKind,
    value: object,
    scope: ClaimScope,
    applicability: str,
    role: str | None,
    local: tuple[LocalClaim, ...],
    raw_order: int,
    state: FieldState | None,
) -> ProviderClaim:
    resolved_state, text = (
        (state, None) if state is not None else _field(kind, value)
    )
    bare = ProviderClaim.create(
        provider=provider,
        candidate=identity,
        kind=kind,
        value=text,
        state=resolved_state,
        scope=scope,
        applicability=applicability,
        role=role,
        raw_order=raw_order,
    )
    return ProviderClaim.create(
        provider=provider,
        candidate=identity,
        kind=kind,
        value=text,
        state=resolved_state,
        scope=scope,
        applicability=applicability,
        role=role,
        raw_order=raw_order,
        links=comparison_links(bare, local),
    )


def _field(kind: ClaimKind, value: object) -> tuple[FieldState, str | None]:
    if value is None:
        return FieldState.ABSENT, None
    maximum = (
        MAX_DESCRIPTION_CHARACTERS
        if kind is ClaimKind.DESCRIPTION
        else MAX_SCALAR_CHARACTERS
    )
    try:
        text = validate_projection_text(value, "provider claim value", maximum)
        if kind is ClaimKind.PUBLICATION_DATE:
            date.fromisoformat(text)
        return FieldState.VALID, text
    except (ValueError, EvidenceValueError):
        return (
            FieldState.OVERSIZED
            if type(value) is str and len(value) > maximum
            else FieldState.MALFORMED,
            None,
        )


def _provenance(
    evidence: set[ProviderEvidence],
    source: dict[str, Any],
    scope: ClaimScope,
) -> None:
    revision = source.get("revision", source.get("latest_revision"))
    if revision is not None:
        if (
            type(revision) is not int
            or isinstance(revision, bool)
            or not 1 <= revision <= 2_147_483_647
        ):
            raise ValueError("provider revision is malformed")
        evidence.add(
            ProviderEvidence(
                ProviderEvidenceKind.REVISION, str(revision), scope
            )
        )
    modified = source.get("last_modified")
    if modified is not None:
        item = _mapping(modified)
        if item.get("type") != "/type/datetime":
            raise ValueError("provider last-modified value is malformed")
        evidence.add(
            ProviderEvidence(
                ProviderEvidenceKind.LAST_MODIFIED,
                _text(item.get("value")),
                scope,
            )
        )


def _string_evidence(
    evidence: set[ProviderEvidence],
    source: dict[str, Any],
    field: str,
    kind: ProviderEvidenceKind,
    scope: ClaimScope,
) -> None:
    for value in _string_list(source.get(field, []), _MAX_VALUES):
        evidence.add(ProviderEvidence(kind, value, scope))


def _identifier_evidence(
    evidence: set[ProviderEvidence],
    source: dict[str, Any],
    scope: ClaimScope,
) -> None:
    if "identifiers" not in source or source["identifiers"] is None:
        return
    identifiers = _mapping(source["identifiers"])
    if "wikidata" in identifiers:
        for value in _string_list(identifiers["wikidata"], _MAX_VALUES):
            evidence.add(
                ProviderEvidence(ProviderEvidenceKind.WIKIDATA, value, scope)
            )


def _cover_id(source: dict[str, Any]) -> _CoverId:
    if "covers" not in source or source["covers"] is None:
        return _CoverId(None)
    covers = source["covers"]
    if type(covers) is not list:
        return _CoverId(None, CoverSourceState.REJECTED)
    values = tuple(
        sorted(
            {
                value
                for value in covers
                if type(value) is int
                and not isinstance(value, bool)
                and 0 < value <= 2_147_483_647
            }
        )
    )
    if len(values) != len(covers):
        return _CoverId(None, CoverSourceState.REJECTED)
    return _CoverId(values[0] if values else None)


def _cover_descriptors(
    provider: str,
    query_kind: ProviderQueryKind,
    values: list[
        tuple[
            str,
            _CoverId,
            str,
            _CoverId,
            dict[str, Any],
            dict[str, Any],
        ]
    ],
    wait: Callable[[float | None], bool],
    user_agent: str,
) -> tuple[ProviderCoverDescriptor, ...]:
    descriptors: list[ProviderCoverDescriptor] = []
    for edition_key, cover, work_key, _, edition, _ in values:
        provenance, terminal_state = _cover_provenance(
            provider,
            (
                CoverDesignation.EXACT_EDITION
                if query_kind is ProviderQueryKind.ISBN
                else CoverDesignation.RELATED_EDITION
            ),
            "open-library-work-" + work_key.removeprefix("/works/"),
            "open-library-edition-" + edition_key.removeprefix("/books/"),
            edition,
        )
        descriptors.append(
            ProviderCoverDescriptor(
                provider,
                CoverSourceKind.OPEN_LIBRARY,
                cover_source_id(
                    CoverSourceKind.OPEN_LIBRARY,
                    f"{provider}:edition:{edition_key}",
                ),
                (
                    None
                    if cover.value is None
                    else f"{OPEN_LIBRARY_COVERS_ORIGIN}/b/id/{cover.value}-L.jpg?default=false"
                ),
                provenance,
                wait,
                False,
                user_agent,
                (
                    cover.terminal_state
                    if cover.terminal_state is not None
                    else terminal_state
                ),
            )
        )
    seen_works: set[str] = set()
    for _, _, work_key, cover, _, work in values:
        if work_key in seen_works:
            continue
        seen_works.add(work_key)
        provenance, terminal_state = _cover_provenance(
            provider,
            CoverDesignation.WORK,
            "open-library-work-" + work_key.removeprefix("/works/"),
            None,
            work,
        )
        descriptors.append(
            ProviderCoverDescriptor(
                provider,
                CoverSourceKind.OPEN_LIBRARY,
                cover_source_id(
                    CoverSourceKind.OPEN_LIBRARY,
                    f"{provider}:work:{work_key}",
                ),
                (
                    None
                    if cover.value is None
                    else f"{OPEN_LIBRARY_COVERS_ORIGIN}/b/id/{cover.value}-L.jpg?default=false"
                ),
                provenance,
                wait,
                False,
                user_agent,
                (
                    cover.terminal_state
                    if cover.terminal_state is not None
                    else terminal_state
                ),
            )
        )
    return tuple(descriptors)


def _cover_provenance(
    provider: str,
    designation: CoverDesignation,
    work_identity: str,
    edition_identity: str | None,
    source: dict[str, Any],
) -> tuple[CoverProvenance, CoverSourceState | None]:
    try:
        return (
            CoverProvenance(
                provider,
                designation,
                work_identity,
                edition_identity,
                _open_library_cover_context(source),
            ),
            None,
        )
    except CoverEvidenceError:
        pass
    return (
        CoverProvenance(
            provider,
            designation,
            work_identity,
            edition_identity,
        ),
        CoverSourceState.OVERSIZED,
    )


def _open_library_cover_context(
    source: dict[str, Any],
) -> tuple[CoverContext, ...]:
    values: set[CoverContext] = set()
    for key in ("isbn_10", "isbn_13"):
        for value in _string_list(source.get(key, []), _MAX_VALUES):
            values.add(CoverContext(CoverContextField.ISBN, value))
    for item in _object_list(source.get("languages", []), _MAX_VALUES):
        match = _LANGUAGE_KEY.fullmatch(_key(item.get("key"), _LANGUAGE_KEY))
        if match is None:
            raise ValueError("cover language is malformed")
        values.add(CoverContext(CoverContextField.LANGUAGE, match.group(1)))
    for value in _string_list(source.get("publishers", []), _MAX_VALUES):
        values.add(CoverContext(CoverContextField.PUBLISHER, value))
    for value in _string_list(source.get("publish_places", []), _MAX_VALUES):
        values.add(CoverContext(CoverContextField.PUBLICATION_PLACE, value))
    if source.get("publish_country") is not None:
        values.add(
            CoverContext(
                CoverContextField.COUNTRY,
                _cover_context_text(source["publish_country"]),
            )
        )
    for field in ("publish_date", "first_publish_date"):
        if source.get(field) is not None:
            values.add(
                CoverContext(
                    CoverContextField.PUBLICATION_DATE,
                    _text(source[field]),
                )
            )
    if source.get("physical_format") is not None:
        values.add(
            CoverContext(
                CoverContextField.FORMAT,
                _text(source["physical_format"]),
            )
        )
    return tuple(
        sorted(values, key=lambda value: (value.field.value, value.value))
    )


def _cover_context_text(value: object) -> str:
    try:
        return _text(value)
    except EvidenceValueError as error:
        if not str(error).endswith(" exceeds its size limit"):
            raise
        validate_projection_text(
            value,
            "provider evidence value",
            MAX_FIELD_VALUE_LENGTH,
        )
        raise CoverEvidenceError("cover context is oversized") from None


def _body(
    response: httpx.Response,
    maximum: int,
    *,
    deadline: float | None,
) -> bytes:
    length = response.headers.get("content-length")
    if length is not None:
        try:
            declared = int(length)
            if declared < 0:
                raise _ProviderFailure(ProviderStatus.MALFORMED)
            if declared > maximum:
                raise _ProviderFailure(ProviderStatus.OVERSIZED)
        except ValueError:
            raise _ProviderFailure(ProviderStatus.MALFORMED) from None
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        if deadline is not None and time.monotonic() >= deadline:
            raise _ProviderFailure(ProviderStatus.UNAVAILABLE)
        total += len(chunk)
        if total > maximum:
            raise _ProviderFailure(ProviderStatus.OVERSIZED)
        chunks.append(chunk)
    return b"".join(chunks)


def _description(value: object) -> object:
    if value is None or type(value) is str:
        return value
    item = _mapping(value)
    if set(item) != {"type", "value"} or item["type"] != "/type/text":
        raise ValueError("provider description is malformed")
    return item["value"]


def _mapping(value: object) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError("provider object is malformed")
    return value


def _object_list(value: object, maximum: int) -> list[dict[str, Any]]:
    if type(value) is not list or len(value) > maximum:
        raise ValueError("provider object list is malformed")
    return [_mapping(item) for item in value]


def _string_list(value: object, maximum: int) -> tuple[str, ...]:
    if type(value) is not list:
        raise ValueError("provider string list is malformed")
    values = tuple(_text(item) for item in value)
    return tuple(sorted(set(values)))[:maximum]


def _author_list(value: object, maximum: int) -> tuple[str, ...]:
    if type(value) is not list or len(value) > maximum:
        raise ValueError("provider author list is malformed")
    values = tuple(_text(item) for item in value)
    normalized = tuple(normalize_title(item).casefold() for item in values)
    if len(set(normalized)) != len(normalized):
        raise ValueError("provider author list is malformed")
    return values


def _search_isbn_families(value: object) -> set[str]:
    if type(value) is not list:
        raise ValueError("search ISBN list is malformed")
    return {
        family
        for raw in value
        if (family := isbn_family(_text(raw))) is not None
    }


def _key(value: object, pattern: re.Pattern[str]) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 128
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in value)
    ):
        raise ValueError("provider key is malformed")
    text = value
    if pattern.fullmatch(text) is None:
        raise ValueError("provider key is malformed")
    return text


def _author_identity(key: str) -> str:
    return "open-library-author-" + key.removeprefix("/authors/")


def _text(value: object, maximum: int = MAX_SCALAR_CHARACTERS) -> str:
    return validate_projection_text(value, "provider evidence value", maximum)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("provider object contains a duplicate field")
        result[key] = value
    return result


def _reject_number(_: str) -> float:
    raise ValueError("provider number is invalid")


class _ProviderFailure(Exception):
    def __init__(self, status: ProviderStatus) -> None:
        super().__init__(status.value)
        self.status = status


__all__ = [
    "OPEN_LIBRARY_COVERS_ORIGIN",
    "OPEN_LIBRARY_ORIGIN",
    "OPEN_LIBRARY_SEARCH_URL",
    "OpenLibraryMetadataProvider",
    "OpenLibraryPacer",
]
