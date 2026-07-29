"""Bounded, report-only Google Books metadata observation."""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
from dataclasses import replace
from decimal import Decimal
from typing import Any, Callable

import httpx

from mechanical_librarian.adapters.providers.retry_safety import (
    ProviderAttemptRecord,
    ProviderIncompleteCause,
    ProviderIncompleteCauseRecord,
    _DiscoveryRequestLedger,
    bounded_attempt_elapsed,
    bounded_retry_after,
    transient_transport_failure,
)
from mechanical_librarian.application.metadata_report import (
    MetadataDiscoveryPlan,
    MetadataLookup,
    ProviderLaneResult,
    provider_isbn_applicability,
    provider_observation,
    qualify_provider_observation,
)
from mechanical_librarian.domain.evidence_bounds import (
    EvidenceValueError,
    validate_projection_text,
)
from mechanical_librarian.domain.failure_diagnostics import (
    FailureCategory,
    FailureComponent,
    FailureDiagnostic,
    FailureStage,
    MAX_DIAGNOSTIC_COUNT,
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
    provider_access_time,
)


GOOGLE_BOOKS_URL = "https://www.googleapis.com/books/v1/volumes"
_VOLUME_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MAX_RESULTS = 5
_MAX_RETURNED_RESULTS = _MAX_RESULTS + 1
_MAX_VALUES = 8
_MAX_AGGREGATE_RESPONSE_BYTES = 2_097_152
_MAX_ATTEMPTS_PER_QUERY = 6
_MAX_ATTEMPTS_PER_OBSERVATION = 54
_FIELDS = (
    "totalItems,items(id,volumeInfo(title,subtitle,authors,publisher,"
    "publishedDate,description,industryIdentifiers,pageCount,"
    "printType,categories,language,contentVersion))"
)


class GoogleBooksMetadataProvider:
    """Observe Google Books volume records without treating them as works."""

    def __init__(
        self,
        name: str,
        client: httpx.Client,
        maximum_bytes: int,
        timeout_seconds: float = 5.0,
        *,
        api_key: str | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        if (
            type(maximum_bytes) is not int
            or isinstance(maximum_bytes, bool)
            or maximum_bytes < 1
            or type(timeout_seconds) not in {int, float}
            or isinstance(timeout_seconds, bool)
            or not 0 < timeout_seconds <= 60
        ):
            raise ValueError("provider bounds are invalid")
        if api_key is not None and (
            type(api_key) is not str
            or not 1 <= len(api_key) <= 2048
            or any(not 0x21 <= ord(value) <= 0x7E for value in api_key)
        ):
            raise ValueError("provider credential is invalid")
        self._name = name
        self._client = client
        self._maximum_bytes = maximum_bytes
        self._remaining_response_bytes = min(
            maximum_bytes * 9, _MAX_AGGREGATE_RESPONSE_BYTES
        )
        self._timeout_seconds = float(timeout_seconds)
        self._api_key = api_key
        self._clock = clock
        self._wall_clock = wall_clock
        self._sleep = sleep
        self._jitter = jitter
        self._attempt_count = 0
        self._attempt_records: list[ProviderAttemptRecord] = []
        self._incomplete_causes: list[ProviderIncompleteCauseRecord] = []
        self._accessed_at: str | None = None
        self._called = False
        self._schema_rejections = 0
        self._ledger = _DiscoveryRequestLedger("google_books", "volumes-v1")

    @property
    def attempt_records(self) -> tuple[ProviderAttemptRecord, ...]:
        return tuple(self._attempt_records)

    @property
    def incomplete_causes(self) -> tuple[ProviderIncompleteCauseRecord, ...]:
        return tuple(self._incomplete_causes)

    @property
    def accessed_at(self) -> str | None:
        return self._accessed_at

    def observe(
        self,
        plan: MetadataDiscoveryPlan | MetadataLookup,
        *,
        deadline: float | None = None,
    ) -> ProviderObservation:
        self._schema_rejections = 0
        if self._called:
            return ProviderObservation(
                self._name,
                ProviderStatus.UNAVAILABLE,
                query_kind=(
                    plan.kind
                    if type(plan) is MetadataLookup
                    else ProviderQueryKind.DISCOVERY
                ),
                authority="google_books",
            )
        self._called = True
        if type(plan) is MetadataLookup:
            ledger = _DiscoveryRequestLedger("google_books", "volumes-v1")
            ledger.register(kind=plan.kind.value, matched_family=plan.matched_family, title=plan.title, authors=plan.authors, projection=_FIELDS)
            self._ledger = ledger
            try:
                value, _ = ledger.resolve(kind=plan.kind.value, matched_family=plan.matched_family, title=plan.title, authors=plan.authors, projection=_FIELDS, execute=lambda: self._observe_lookup(plan, deadline=deadline))
                return replace(value, request_accounting=ledger.accounting())
            finally:
                self._client.close()
        if type(plan) is not MetadataDiscoveryPlan:
            raise ValueError("provider lookup is invalid")
        if plan.overflow:
            self._client.close()
            return ProviderObservation(
                self._name,
                ProviderStatus.QUERY_LIMIT_EXCEEDED,
                authority="google_books",
            )
        results: list[ProviderLaneResult] = []
        ledger = _DiscoveryRequestLedger("google_books", "volumes-v1")
        for lane in plan.lanes:
            ledger.register(kind=lane.kind.value, matched_family=lane.matched_family, title=lane.title, authors=lane.authors, projection=_FIELDS)
        self._ledger = ledger
        try:
            for lane in plan.lanes:
                lookup = MetadataLookup(
                    lane.kind,
                    plan.local_claims,
                    lane.title,
                    lane.authors,
                    lane.matched_family,
                )
                physical, _replayed = ledger.resolve(
                    kind=lookup.kind.value,
                    matched_family=lookup.matched_family,
                    title=lookup.title,
                    authors=lookup.authors,
                    projection=_FIELDS,
                    execute=lambda: self._observe_lookup(lookup, deadline=deadline),
                )
                observation = qualify_provider_observation(physical, lookup)
                results.append(
                    ProviderLaneResult(
                        lane,
                        observation.status,
                        observation.candidates,
                        observation.accessed_at,
                    )
                )
        finally:
            self._client.close()
        return provider_observation(
            self._name,
            tuple(results),
            authority="google_books",
            diagnostics=self._schema_diagnostics(),
            request_accounting=ledger.accounting(),
        )

    def _observe_lookup(
        self,
        lookup: MetadataLookup,
        *,
        deadline: float | None,
    ) -> ProviderObservation:
        local_claims = lookup.local_claims
        if lookup.kind is ProviderQueryKind.ISBN:
            family = lookup.matched_family
            if family is None:
                families = {
                    value
                    for claim in local_claims
                    if claim.kind is ClaimKind.ISBN
                    and claim.canonical is not None
                    and claim.applicability in {"ebook", "unknown"}
                    and claim.basis == "opening_matter"
                    and claim.rear_provenance is None
                    and (value := isbn_family(claim.canonical)) is not None
                }
                if len(families) != 1:
                    return self._result(
                        lookup, ProviderStatus.INSUFFICIENT_INPUT
                    )
                family = next(iter(families))
            references = tuple(
                sorted(
                    claim.reference
                    for claim in local_claims
                    if claim.kind is ClaimKind.ISBN
                    and claim.canonical is not None
                    and isbn_family(claim.canonical) == family
                )
            )
            query = f"isbn:{family}"
        elif (
            lookup.kind is ProviderQueryKind.WORK
            and lookup.title is not None
            and lookup.authors
        ):
            family = None
            references = ()
            query = " ".join(
                [f'intitle:"{lookup.title}"']
                + [f'inauthor:"{author}"' for author in lookup.authors]
            )
        else:
            return self._result(lookup, ProviderStatus.INSUFFICIENT_INPUT)
        try:
            payload = self._request(query, deadline=deadline)
            candidates = _candidates(
                self._name,
                payload,
                local_claims,
                requested_family=family,
                schema_rejections=lambda count: self._reject_schema_members(count),
            )
        except _ProviderFailure as error:
            return self._result(lookup, error.status, references=references)
        except (
            EvidenceValueError,
            KeyError,
            TypeError,
            ValueError,
            UnicodeError,
            RecursionError,
        ):
            return self._result(
                lookup, ProviderStatus.MALFORMED, references=references
            )
        if len(candidates) > _MAX_RESULTS:
            return self._result(
                lookup,
                ProviderStatus.CANDIDATE_LIMIT_EXCEEDED,
                references=references,
            )
        if not candidates:
            return self._result(
                lookup, ProviderStatus.NO_MATCH, references=references
            )
        return self._result(
            lookup,
            ProviderStatus.CANDIDATES,
            candidates,
            references=references,
        )

    def _reject_schema_members(self, count: int = 1) -> None:
        if count <= 0:
            return
        self._schema_rejections = min(
            MAX_DIAGNOSTIC_COUNT,
            self._schema_rejections + count,
        )

    def _schema_diagnostics(self) -> tuple[FailureDiagnostic, ...]:
        if not self._schema_rejections:
            return ()
        return (
            FailureDiagnostic(
                FailureCategory.SCHEMA_CONSTRUCTION,
                FailureComponent.PROVIDER,
                FailureStage.SCHEMA,
                count=self._schema_rejections,
            ),
        )

    def _result(
        self,
        lookup: MetadataLookup,
        status: ProviderStatus,
        candidates: tuple[ProviderCandidate, ...] = (),
        *,
        references: tuple[str, ...] = (),
    ) -> ProviderObservation:
        return ProviderObservation(
            self._name,
            status,
            references,
            candidates,
            lookup.kind,
            "google_books",
            accessed_at=self._accessed_at,
            diagnostics=self._schema_diagnostics(),
        )

    def _request(
        self, query: str, *, deadline: float | None
    ) -> dict[str, Any]:
        for retry_index in range(_MAX_ATTEMPTS_PER_QUERY):
            started = self._clock()
            attempts_before_dispatch = self._attempt_count
            try:
                value = self._request_once(
                    query, deadline=deadline, is_retry=retry_index > 0
                )
            except (asyncio.CancelledError, KeyboardInterrupt):
                if self._attempt_count != attempts_before_dispatch:
                    self._record_attempt(
                        started,
                        category="cancellation",
                        retryable=False,
                        delay=None,
                        outcome="cancelled",
                    )
                raise
            except _ProviderFailure as error:
                if self._attempt_count == attempts_before_dispatch:
                    raise
                can_retry = (
                    error.retryable
                    and retry_index + 1 < _MAX_ATTEMPTS_PER_QUERY
                    and self._attempt_count < _MAX_ATTEMPTS_PER_OBSERVATION
                    and self._ledger.allows_retry(self._attempt_count, _MAX_ATTEMPTS_PER_OBSERVATION)
                )
                delay = (
                    (
                        error.retry_after
                        if error.retry_after is not None
                        else self._retry_delay(retry_index)
                    )
                    if can_retry
                    else None
                )
                if delay is not None and not self._delay_fits(
                    delay, deadline=deadline
                ):
                    delay = None
                    can_retry = False
                if not can_retry or delay is None:
                    self._record_attempt(
                        started,
                        category=error.category,
                        retryable=error.retryable,
                        delay=delay,
                        outcome=(
                            "exhausted"
                            if error.retryable
                            else "terminal"
                        ),
                    )
                    raise
                try:
                    self._sleep(delay)
                except (asyncio.CancelledError, KeyboardInterrupt):
                    self._record_attempt(
                        started,
                        category="cancellation",
                        retryable=False,
                        delay=delay,
                        outcome="cancelled",
                    )
                    raise
                if deadline is not None and self._clock() >= deadline:
                    self._record_attempt(
                        started,
                        category=error.category,
                        retryable=error.retryable,
                        delay=delay,
                        outcome="deadline_exhausted",
                    )
                    raise _ProviderFailure(ProviderStatus.UNAVAILABLE)
                self._record_attempt(
                    started,
                    category=error.category,
                    retryable=error.retryable,
                    delay=delay,
                    outcome="retry",
                )
                continue
            self._record_attempt(
                started,
                category="none",
                retryable=False,
                delay=None,
                outcome="response_received",
            )
            return value
        raise _ProviderFailure(ProviderStatus.UNAVAILABLE)

    def _request_once(
        self, query: str, *, deadline: float | None, is_retry: bool
    ) -> dict[str, Any]:
        if self._remaining_response_bytes <= 0:
            raise self._incomplete(ProviderIncompleteCause.AGGREGATE_BYTES)
        remaining = self._timeout_seconds
        if deadline is not None:
            remaining = min(remaining, deadline - self._clock())
        if remaining <= 0:
            raise self._incomplete(ProviderIncompleteCause.DEADLINE)
        if self._attempt_count >= _MAX_ATTEMPTS_PER_OBSERVATION:
            raise self._incomplete(ProviderIncompleteCause.DISPATCH_COUNT)
        params = {
            "fields": _FIELDS,
            "maxResults": str(_MAX_RETURNED_RESULTS),
            "printType": "books",
            "projection": "full",
            "q": query,
            "startIndex": "0",
        }
        if self._api_key is not None:
            params["key"] = self._api_key
        try:
            if deadline is not None and self._clock() >= deadline:
                raise self._incomplete(ProviderIncompleteCause.DEADLINE)
            if self._accessed_at is None:
                self._accessed_at = provider_access_time(self._wall_clock())
            self._ledger.dispatch(retry=is_retry)
            self._attempt_count += 1
            with self._client.stream(
                "GET",
                GOOGLE_BOOKS_URL,
                params=params,
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                },
                timeout=remaining,
            ) as response:
                identity_encoding = (
                    response.headers.get("content-encoding", "identity")
                    .strip()
                    .lower()
                    in {"", "identity"}
                )
                if response.is_stream_consumed and not identity_encoding:
                    raise _ProviderFailure(ProviderStatus.MALFORMED)
                payload = _body(
                    response,
                    min(self._maximum_bytes, self._remaining_response_bytes),
                    deadline=deadline,
                    clock=self._clock,
                    consume=self._consume_response_bytes,
                )
                if not identity_encoding:
                    raise _ProviderFailure(ProviderStatus.MALFORMED)
                if response.status_code == 503:
                    raise _ProviderFailure(
                        ProviderStatus.UNAVAILABLE,
                        category="http_503",
                        retryable=True,
                        retry_after=bounded_retry_after(
                            response.headers.get("retry-after"),
                            now=self._wall_clock(),
                        ),
                    )
                if response.status_code != 200:
                    raise _ProviderFailure(
                        _error_status(response.status_code, payload)
                    )
                if (
                    response.headers.get("content-type", "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                    != "application/json"
                ):
                    raise _ProviderFailure(ProviderStatus.MALFORMED)
        except _ProviderFailure as error:
            if error.status is ProviderStatus.OVERSIZED:
                self._remaining_response_bytes = 0
            raise
        except httpx.HTTPError as error:
            category = transient_transport_failure(error)
            if category is not None:
                raise _ProviderFailure(
                    ProviderStatus.UNAVAILABLE,
                    category=category,
                    retryable=True,
                ) from None
            raise _ProviderFailure(ProviderStatus.UNAVAILABLE) from None
        except (OSError, ValueError):
            raise _ProviderFailure(ProviderStatus.UNAVAILABLE) from None
        try:
            value = _decode(payload)
        except (TypeError, ValueError, UnicodeError, RecursionError):
            raise _ProviderFailure(ProviderStatus.MALFORMED) from None
        if type(value) is not dict:
            raise _ProviderFailure(ProviderStatus.MALFORMED)
        return value

    def _consume_response_bytes(self, amount: int) -> None:
        self._remaining_response_bytes -= amount
        if self._remaining_response_bytes < 0:
            self._remaining_response_bytes = 0
            raise _ProviderFailure(ProviderStatus.OVERSIZED)

    def _incomplete(
        self, cause: ProviderIncompleteCause
    ) -> _ProviderFailure:
        self._incomplete_causes.append(ProviderIncompleteCauseRecord(cause))
        return _ProviderFailure(ProviderStatus.INCOMPLETE)

    def _retry_delay(self, retry_index: int) -> float:
        jitter = self._jitter()
        if (
            type(jitter) not in {int, float}
            or isinstance(jitter, bool)
            or not 0.0 <= float(jitter) < 1.0
        ):
            jitter = 0.0
        return min(float(2**retry_index) + float(jitter), 16.0)

    def _delay_fits(
        self, delay: float, *, deadline: float | None
    ) -> bool:
        if deadline is None:
            return True
        return self._clock() + delay < deadline

    def _record_attempt(
        self,
        started: float,
        *,
        category: str,
        retryable: bool,
        delay: float | None,
        outcome: str,
    ) -> None:
        self._attempt_records.append(
            ProviderAttemptRecord(
                provider=self._name,
                ordinal=self._attempt_count,
                category=category,
                delay_seconds=delay,
                elapsed_seconds=bounded_attempt_elapsed(
                    started, self._clock()
                ),
                outcome=outcome,
            )
        )


def _candidates(
    provider: str,
    payload: dict[str, Any],
    local_claims: tuple[LocalClaim, ...],
    *,
    requested_family: str | None,
    schema_rejections: Callable[[int], None],
) -> tuple[ProviderCandidate, ...]:
    if set(payload) - {"items", "totalItems"}:
        raise ValueError("provider response is malformed")
    total = payload.get("totalItems")
    if type(total) is not int or isinstance(total, bool) or total < 0:
        raise ValueError("provider result count is malformed")
    items = payload.get("items", [])
    if type(items) is not list or len(items) > _MAX_RETURNED_RESULTS:
        raise ValueError("provider candidate list is malformed")
    if (total == 0) != (not items):
        raise ValueError("provider result count is inconsistent")
    admitted: list[ProviderCandidate] = []
    for item in items:
        if type(item) is not dict or set(item) != {"id", "volumeInfo"}:
            raise ValueError("provider candidate is malformed")
        volume_id = item["id"]
        if type(volume_id) is not str or _VOLUME_ID.fullmatch(volume_id) is None:
            raise ValueError("provider volume identity is malformed")
        info = item["volumeInfo"]
        if type(info) is not dict:
            raise ValueError("provider volume is malformed")
        candidate = _candidate(
            provider,
            volume_id,
            info,
            local_claims,
            requested_family=requested_family,
            schema_rejections=schema_rejections,
        )
        if candidate is not None:
            admitted.append(candidate)
    identities = [value.identity for value in admitted]
    if len(set(identities)) != len(identities):
        raise ValueError("provider candidates are duplicated")
    return tuple(sorted(admitted, key=lambda value: value.identity))


def _candidate(
    provider: str,
    volume_id: str,
    info: dict[str, Any],
    local_claims: tuple[LocalClaim, ...],
    *,
    requested_family: str | None,
    schema_rejections: Callable[[int], None],
) -> ProviderCandidate | None:
    allowed = {
        "authors",
        "categories",
        "contentVersion",
        "description",
        "industryIdentifiers",
        "language",
        "pageCount",
        "printType",
        "publishedDate",
        "publisher",
        "subtitle",
        "title",
    }
    # Google may add optional volumeInfo fields.  They carry no identity or
    # required join material, so ignore them rather than discarding the safely
    # ISBN-anchored record.  Identity and ISBN binding below remain strict.
    # Deliberately do not retain or project unknown optional values.
    schema_rejections(len(set(info) - allowed))
    identity = "google-books-volume-" + volume_id
    identifiers = info.get("industryIdentifiers", [])
    if type(identifiers) is not list or len(identifiers) > _MAX_VALUES:
        raise ValueError("provider identifiers are malformed")
    isbns: list[str] = []
    for identifier in identifiers:
        if type(identifier) is not dict or set(identifier) != {
            "identifier",
            "type",
        }:
            raise ValueError("provider identifier is malformed")
        identifier_type = _text(identifier["type"])
        identifier_value = _text(identifier["identifier"])
        if identifier_type not in {"ISBN_10", "ISBN_13"}:
            continue
        compact = "".join(
            value
            for value in identifier_value
            if value.isdigit() or value in "Xx"
        ).upper()
        if isbn_family(compact) is None:
            raise ValueError("provider ISBN is malformed")
        isbns.append(compact)
    returned_families = {
        value for isbn in isbns if (value := isbn_family(isbn)) is not None
    }
    if requested_family is not None and requested_family not in returned_families:
        return None

    claims: list[ProviderClaim] = []

    def add(
        kind: ClaimKind,
        value: object,
        scope: ClaimScope,
        *,
        applicability: str = "not_applicable",
        role: str | None = None,
    ) -> None:
        state, exact = _field(kind, value)
        bare = ProviderClaim.create(
            provider=provider,
            candidate=identity,
            kind=kind,
            value=exact,
            state=state,
            scope=scope,
            applicability=applicability,
            role=role,
            raw_order=len(claims),
        )
        claims.append(
            ProviderClaim.create(
                provider=provider,
                candidate=identity,
                kind=kind,
                value=exact,
                state=state,
                scope=scope,
                applicability=applicability,
                role=role,
                raw_order=len(claims),
                links=comparison_links(bare, local_claims),
            )
        )

    for isbn in sorted(set(isbns)):
        add(
            ClaimKind.ISBN,
            isbn,
            ClaimScope.EDITION,
            applicability=provider_isbn_applicability(isbn, local_claims),
        )
    add(ClaimKind.TITLE, info.get("title"), ClaimScope.WORK)
    add(ClaimKind.SUBTITLE, info.get("subtitle"), ClaimScope.WORK)
    authors = _optional_string_list(
        info.get("authors", []), _MAX_VALUES, schema_rejections
    )
    for author in authors:
        add(
            ClaimKind.CREATOR,
            author,
            ClaimScope.WORK,
            role="author",
        )
    if not authors:
        add(ClaimKind.CREATOR, None, ClaimScope.WORK, role="author")
    add(ClaimKind.PUBLISHER, info.get("publisher"), ClaimScope.EDITION)
    add(
        ClaimKind.DESCRIPTION,
        info.get("description"),
        ClaimScope.WORK,
    )
    add(ClaimKind.LANGUAGE, info.get("language"), ClaimScope.EDITION)

    evidence = [
        ProviderEvidence(
            ProviderEvidenceKind.EDITION_WORK_RELATION,
            "unresolved",
            ClaimScope.EDITION,
        )
    ]
    for field, kind in (
        ("printType", ProviderEvidenceKind.PHYSICAL_FORMAT),
        ("contentVersion", ProviderEvidenceKind.REVISION),
        ("publishedDate", ProviderEvidenceKind.PUBLICATION_DATE_TEXT),
    ):
        if info.get(field) is not None:
            try:
                evidence.append(
                    ProviderEvidence(kind, _text(info[field]), ClaimScope.EDITION)
                )
            except (ValueError, EvidenceValueError):
                pass
    pages = info.get("pageCount")
    if pages is not None:
        if (
            type(pages) is not int
            or isinstance(pages, bool)
            or not 1 <= pages <= 100_000
        ):
            pages = None
        if pages is not None:
            evidence.append(
                ProviderEvidence(
                    ProviderEvidenceKind.PAGE_COUNT,
                    str(pages),
                    ClaimScope.EDITION,
                )
            )
    for category in _optional_string_list(
        info.get("categories", []), _MAX_VALUES, schema_rejections
    ):
        evidence.append(
            ProviderEvidence(
                ProviderEvidenceKind.SUBJECT,
                category,
                ClaimScope.WORK,
            )
        )
    # Keep each returned occurrence.  Source ordinals are allocated before
    # canonical ordering so repeated provider values remain independently
    # attributable to this candidate.
    canonical_evidence = tuple(
        sorted(
            (replace(value, source_ordinal=index) for index, value in enumerate(evidence)),
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
    return ProviderCandidate(
        identity,
        "unresolved",
        tuple(claims),
        canonical_evidence,
    )


def _string_list(value: object, maximum: int) -> tuple[str, ...]:
    if type(value) is not list or len(value) > maximum:
        raise ValueError("provider string list is malformed")
    values = tuple(_text(item) for item in value)
    normalized = tuple(normalize_title(item).casefold() for item in values)
    if len(set(normalized)) != len(normalized):
        raise ValueError("provider string list is duplicated")
    return values


def _optional_string_list(
    value: object, maximum: int, schema_rejections: Callable[[int], None] | None = None
) -> tuple[str, ...]:
    """Keep valid optional members in source order without weakening IDs."""
    if type(value) is not list:
        if schema_rejections is not None:
            schema_rejections(1)
        return ()
    if len(value) > maximum:
        if schema_rejections is not None:
            schema_rejections(len(value) - maximum)
        return ()
    values: list[str] = []
    for item in value:
        try:
            text = _text(item)
        except (ValueError, EvidenceValueError):
            if schema_rejections is not None:
                schema_rejections(1)
            continue
        values.append(text)
    return tuple(values)


def _field(
    kind: ClaimKind, value: object
) -> tuple[FieldState, str | None]:
    if value is None:
        return FieldState.ABSENT, None
    maximum = (
        MAX_DESCRIPTION_CHARACTERS
        if kind is ClaimKind.DESCRIPTION
        else MAX_SCALAR_CHARACTERS
    )
    try:
        return FieldState.VALID, _text(value, maximum)
    except (ValueError, EvidenceValueError):
        return (
            FieldState.OVERSIZED
            if type(value) is str and len(value) > maximum
            else FieldState.MALFORMED,
            None,
        )


def _text(value: object, maximum: int = MAX_SCALAR_CHARACTERS) -> str:
    return validate_projection_text(value, "provider evidence value", maximum)


def _body(
    response: httpx.Response,
    maximum: int,
    *,
    deadline: float | None,
    clock: Callable[[], float],
    consume: Callable[[int], None],
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
    raw_chunks = (
        (response.content,)
        if response.is_stream_consumed
        else response.iter_raw()
    )
    for chunk in raw_chunks:
        consume(len(chunk))
        total += len(chunk)
        if total > maximum:
            raise _ProviderFailure(ProviderStatus.OVERSIZED)
        chunks.append(chunk)
        if deadline is not None and clock() >= deadline:
            raise _ProviderFailure(ProviderStatus.UNAVAILABLE)
    return b"".join(chunks)


def _decode(payload: bytes) -> object:
    return json.loads(
        payload.decode("utf-8", "strict"),
        object_pairs_hook=_unique_object,
        parse_constant=_reject_number,
        parse_float=Decimal,
    )


def _error_status(status_code: int, payload: bytes) -> ProviderStatus:
    if status_code == 401:
        return ProviderStatus.AUTHENTICATION
    if status_code == 429:
        return ProviderStatus.RATE_LIMITED
    try:
        value = _decode(payload)
        reasons = {
            item.get("reason")
            for item in value.get("error", {}).get("errors", [])
            if type(item) is dict
        }
    except (AttributeError, TypeError, ValueError, UnicodeError, RecursionError):
        reasons = set()
    if reasons & {
        "dailyLimitExceeded",
        "dailyLimitExceededUnreg",
        "rateLimitExceeded",
        "userRateLimitExceeded",
        "quotaExceeded",
    }:
        return ProviderStatus.RATE_LIMITED
    if reasons & {"keyInvalid", "ipRefererBlocked"}:
        return ProviderStatus.AUTHENTICATION
    if status_code == 403:
        return ProviderStatus.AUTHORIZATION
    return ProviderStatus.UNAVAILABLE


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
    def __init__(
        self,
        status: ProviderStatus,
        *,
        category: str = "provider_failure",
        retryable: bool = False,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(status.value)
        self.status = status
        self.category = category
        self.retryable = retryable
        self.retry_after = retry_after


__all__ = ["GOOGLE_BOOKS_URL", "GoogleBooksMetadataProvider"]
