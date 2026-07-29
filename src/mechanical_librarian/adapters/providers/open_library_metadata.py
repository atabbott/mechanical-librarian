"""Bounded, report-only Open Library metadata observation."""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

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
    normalize_date_value,
    normalize_title,
    provider_access_time,
)


OPEN_LIBRARY_ORIGIN = "https://openlibrary.org"
OPEN_LIBRARY_SEARCH_URL = OPEN_LIBRARY_ORIGIN + "/search.json"
OPEN_LIBRARY_COVERS_ORIGIN = "https://covers.openlibrary.org"
_EDITION_KEY = re.compile(r"^/books/OL[1-9][0-9]*M$")
_WORK_KEY = re.compile(r"^/works/OL[1-9][0-9]*W$")
_AUTHOR_KEY = re.compile(r"^/authors/OL[1-9][0-9]*A$")
_LANGUAGE_KEY = re.compile(r"^/languages/([a-z]{2,3})$")
_MAX_SEARCH_ROWS = 3
_ISBN_SEARCH_PROJECTION = "key,editions,editions.key,editions.isbn"
_WORK_SEARCH_PROJECTION = "key,author_name,author_key,publisher,publish_date,language,edition_key,editions,editions.key"


@dataclass(frozen=True, slots=True)
class _DetailResult:
    payload: object | None
    status: ProviderStatus | None


def _freeze_detail(value: object) -> object:
    if type(value) is dict:
        return ("mapping", tuple((key, _freeze_detail(item)) for key, item in value.items()))
    if type(value) is list:
        return ("sequence", tuple(_freeze_detail(item) for item in value))
    return value


def _thaw_detail(value: object | None) -> dict[str, Any]:
    thawed = _thaw_detail_value(value)
    if type(thawed) is not dict:
        raise ValueError("detail replay is malformed")
    return thawed


def _thaw_detail_value(value: object) -> object:
    if type(value) is tuple and len(value) == 2 and value[0] == "mapping":
        items = value[1]
        if type(items) is not tuple or any(type(item) is not tuple or len(item) != 2 or type(item[0]) is not str for item in items):
            raise ValueError("detail replay is malformed")
        return {key: _thaw_detail_value(item) for key, item in items}
    if type(value) is tuple and len(value) == 2 and value[0] == "sequence":
        items = value[1]
        if type(items) is not tuple:
            raise ValueError("detail replay is malformed")
        return [_thaw_detail_value(item) for item in items]
    if type(value) is tuple:
        raise ValueError("detail replay is malformed")
    return value
_MAX_VALUES = 8
_MAX_AGGREGATE_RESPONSE_BYTES = 2_097_152
_MAX_ATTEMPTS_PER_REQUEST = 2
_MAX_RETRIES_PER_OBSERVATION = 4
_MAX_ATTEMPTS_PER_OBSERVATION = 49


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
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
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
            maximum_bytes * _MAX_ATTEMPTS_PER_OBSERVATION,
            _MAX_AGGREGATE_RESPONSE_BYTES,
        )
        self._timeout_seconds = float(timeout_seconds)
        self._pacer = pacer if pacer is not None else OpenLibraryPacer()
        self._clock = clock
        self._wall_clock = wall_clock
        self._sleep = sleep
        self._jitter = jitter
        self._attempt_count = 0
        self._retry_count = 0
        self._attempt_records: list[ProviderAttemptRecord] = []
        self._incomplete_causes: list[ProviderIncompleteCauseRecord] = []
        self._accessed_at: str | None = None
        self._called = False
        self._schema_rejections = 0
        self._ledger = _DiscoveryRequestLedger("open_library", "search-v1")
        self.cover_descriptors: tuple[ProviderCoverDescriptor, ...] = ()
        self.cover_pacer = self._pacer
        self.cover_kind = CoverSourceKind.OPEN_LIBRARY
        self.cover_allow_redirects = False
        self.cover_user_agent = client.headers["User-Agent"]

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
            )
        self._called = True
        if type(plan) is MetadataLookup:
            ledger = _DiscoveryRequestLedger("open_library", "search-v1")
            projection = (_ISBN_SEARCH_PROJECTION if plan.kind is ProviderQueryKind.ISBN else _WORK_SEARCH_PROJECTION)
            ledger.register(kind=plan.kind.value, matched_family=plan.matched_family, title=plan.title, authors=plan.authors, projection=projection)
            self._ledger = ledger
            try:
                value, _ = ledger.resolve(kind=plan.kind.value, matched_family=plan.matched_family, title=plan.title, authors=plan.authors, projection=projection, execute=lambda: self._observe_lookup(plan, deadline=deadline))
                return replace(value, request_accounting=ledger.accounting())
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
        ledger = _DiscoveryRequestLedger("open_library", "search-v1")
        for lane in plan.lanes:
            ledger.register(kind=lane.kind.value, matched_family=lane.matched_family, title=lane.title, authors=lane.authors, projection=(_ISBN_SEARCH_PROJECTION if lane.kind is ProviderQueryKind.ISBN else _WORK_SEARCH_PROJECTION))
        self._ledger = ledger
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
                physical, _replayed = ledger.resolve(
                    kind=lookup.kind.value,
                    matched_family=lookup.matched_family,
                    title=lookup.title,
                    authors=lookup.authors,
                    projection=(
                        _ISBN_SEARCH_PROJECTION
                        if lookup.kind is ProviderQueryKind.ISBN
                        else _WORK_SEARCH_PROJECTION
                    ),
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
                        lane,
                        ProviderStatus.MALFORMED,
                        accessed_at=observation.accessed_at,
                    )
                else:
                    descriptors = merged
        finally:
            self._client.close()
        self.cover_descriptors = descriptors
        return provider_observation(
            self._name,
            _canonical_discovery_results(self._name, tuple(results)),
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
                accessed_at=self._accessed_at,
                diagnostics=self._schema_diagnostics(),
            )
        try:
            with nullcontext():
                if lookup.kind is ProviderQueryKind.ISBN:
                    search = self._request(
                        OPEN_LIBRARY_SEARCH_URL,
                        params={
                            "q": f"isbn:{family}",
                            "fields": _ISBN_SEARCH_PROJECTION,
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
                            "fields": _WORK_SEARCH_PROJECTION,
                            "limit": str(_MAX_SEARCH_ROWS),
                        },
                        deadline=deadline,
                    )
                    admitted = _admitted_work_candidates(search)
                if len(admitted) > 2:
                    return result(ProviderStatus.CANDIDATE_LIMIT_EXCEEDED)
                if not admitted:
                    return result(ProviderStatus.NO_MATCH)
                # The complete, bounded detail set is known from the search
                # response. Reserve it before a detail retry can consume the
                # observation's remaining dispatch authority.
                detail_keys = {
                    identity
                    for edition_key, work_key, _ in admitted
                    for identity in (edition_key, work_key)
                }
                for identity in sorted(detail_keys):
                    self._ledger.register_detail(identity)

                def detail(identity: str) -> dict[str, Any]:
                    def execute() -> _DetailResult:
                        try:
                            value = self._request(
                                OPEN_LIBRARY_ORIGIN + identity + ".json",
                                deadline=deadline,
                            )
                        except _ProviderFailure as error:
                            return _DetailResult(None, error.status)
                        return _DetailResult(_freeze_detail(value), None)

                    replay, _ = self._ledger.resolve_detail(
                        identity, execute=execute
                    )
                    if replay.status is not None:
                        raise _ProviderFailure(replay.status)
                    return _thaw_detail(replay.payload)

                work_documents: dict[str, dict[str, Any]] = {}
                diagnosed_documents: set[int] = set()
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
                    edition = detail(edition_key)
                    if work_key not in work_documents:
                        work_documents[work_key] = detail(work_key)
                    candidate, edition_cover, work_cover = _candidate(
                        self._name,
                        edition_key,
                        work_key,
                        edition,
                        work_documents[work_key],
                        local_claims,
                        family,
                        work_authors,
                        _search_work_document(search, work_key),
                    )
                    search_work = _search_work_document(search, work_key)
                    for document, search_projection in (
                        (edition, False),
                        (work_documents[work_key], False),
                        (search_work, True),
                    ):
                        if document is not None and id(document) not in diagnosed_documents:
                            diagnosed_documents.add(id(document))
                            self._reject_schema_members(
                                _unknown_projection_rejections(document)
                                + _parser_optional_rejections(
                                    document,
                                    search_projection=search_projection,
                                )
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
            self._ledger.finalize_pending_details()
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
            self._ledger.finalize_pending_details()
            return result(ProviderStatus.MALFORMED)
        return result(
            ProviderStatus.CANDIDATES,
            tuple(sorted(candidates, key=lambda candidate: candidate.identity)),
        )

    def _reject_schema_members(self, count: int) -> None:
        if count > 0:
            self._schema_rejections = min(
                MAX_DIAGNOSTIC_COUNT, self._schema_rejections + count
            )

    def _schema_diagnostics(self) -> tuple[FailureDiagnostic, ...]:
        return () if not self._schema_rejections else (
            FailureDiagnostic(
                FailureCategory.SCHEMA_CONSTRUCTION,
                FailureComponent.PROVIDER,
                FailureStage.SCHEMA,
                count=self._schema_rejections,
            ),
        )

    def _request(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        deadline: float | None,
    ) -> dict[str, Any]:
        for retry_index in range(_MAX_ATTEMPTS_PER_REQUEST):
            started = self._clock()
            attempts_before_dispatch = self._attempt_count
            try:
                value = self._request_once(
                    url,
                    params=params,
                    deadline=deadline,
                    is_retry=retry_index > 0,
                )
            except (asyncio.CancelledError, KeyboardInterrupt):
                if self._attempt_count != attempts_before_dispatch:
                    self._record_attempt(
                        started,
                        category="cancellation",
                        delay=None,
                        outcome="cancelled",
                    )
                raise
            except _ProviderFailure as error:
                if self._attempt_count == attempts_before_dispatch:
                    raise
                can_retry = (
                    error.retryable
                    and retry_index + 1 < _MAX_ATTEMPTS_PER_REQUEST
                    and self._retry_count < _MAX_RETRIES_PER_OBSERVATION
                    and self._attempt_count
                    < _MAX_ATTEMPTS_PER_OBSERVATION
                    and self._ledger.allows_retry(self._attempt_count, _MAX_ATTEMPTS_PER_OBSERVATION)
                )
                delay = (
                    (
                        error.retry_after
                        if error.retry_after is not None
                        else self._retry_delay()
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
                        delay=delay,
                        outcome="cancelled",
                    )
                    raise
                if deadline is not None and self._clock() >= deadline:
                    self._record_attempt(
                        started,
                        category=error.category,
                        delay=delay,
                        outcome="deadline_exhausted",
                    )
                    raise _ProviderFailure(ProviderStatus.UNAVAILABLE)
                self._record_attempt(
                    started,
                    category=error.category,
                    delay=delay,
                    outcome="retry",
                )
                continue
            self._record_attempt(
                started,
                category="none",
                delay=None,
                outcome="response_received",
            )
            return value
        raise _ProviderFailure(ProviderStatus.UNAVAILABLE)

    def _request_once(
        self,
        url: str,
        *,
        params: dict[str, str] | None,
        deadline: float | None,
        is_retry: bool,
    ) -> dict[str, Any]:
        if self._remaining_response_bytes <= 0:
            raise self._incomplete(ProviderIncompleteCause.AGGREGATE_BYTES)
        if self._attempt_count >= _MAX_ATTEMPTS_PER_OBSERVATION:
            raise self._incomplete(ProviderIncompleteCause.DISPATCH_COUNT)
        if is_retry and self._retry_count >= _MAX_RETRIES_PER_OBSERVATION:
            raise _ProviderFailure(ProviderStatus.UNAVAILABLE)
        if not self._pacer.wait(deadline):
            raise self._incomplete(ProviderIncompleteCause.DEADLINE)
        remaining = self._timeout_seconds
        if deadline is not None:
            remaining = min(remaining, deadline - self._clock())
        if remaining <= 0:
            raise self._incomplete(ProviderIncompleteCause.DEADLINE)
        try:
            if deadline is not None and self._clock() >= deadline:
                raise self._incomplete(ProviderIncompleteCause.DEADLINE)
            if self._accessed_at is None:
                self._accessed_at = provider_access_time(self._wall_clock())
            self._ledger.dispatch(retry=is_retry)
            self._attempt_count += 1
            if is_retry:
                self._retry_count += 1
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
                retry_after = bounded_retry_after(
                    response.headers.get("retry-after"),
                    now=self._wall_clock(),
                )
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
                    min(
                        self._maximum_bytes,
                        self._remaining_response_bytes,
                    ),
                    deadline=deadline,
                    clock=self._clock,
                    consume=self._consume_response_bytes,
                )
                if not identity_encoding:
                    raise _ProviderFailure(ProviderStatus.MALFORMED)
                if response.status_code == 429:
                    raise _ProviderFailure(
                        ProviderStatus.RATE_LIMITED,
                        category="http_429",
                    )
                if response.status_code in {500, 502, 503, 504}:
                    raise _ProviderFailure(
                        ProviderStatus.UNAVAILABLE,
                        category=f"http_{response.status_code}",
                        retryable=True,
                        retry_after=(
                            retry_after
                            if response.status_code == 503
                            else None
                        ),
                    )
                if response.status_code != 200:
                    raise _ProviderFailure(
                        ProviderStatus.UNAVAILABLE,
                        category="http_other",
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

    def _retry_delay(self) -> float:
        jitter = self._jitter()
        if (
            type(jitter) not in {int, float}
            or isinstance(jitter, bool)
            or not 0.0 <= float(jitter) < 1.0
        ):
            jitter = 0.0
        return min(1.0 + float(jitter), 16.0)

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


def _admitted_candidates(
    source: dict[str, Any], family: str
) -> tuple[tuple[str, str], ...]:
    docs = source.get("docs")
    if type(docs) is not list or len(docs) > _MAX_SEARCH_ROWS:
        raise ValueError("search result is malformed")
    admitted: dict[str, str] = {}
    seen_work_keys: set[str] = set()
    seen_identities: set[tuple[str, str]] = set()
    for raw_work in docs:
        work = _mapping(raw_work)
        work_key = _key(work.get("key"), _WORK_KEY)
        if work_key in seen_work_keys:
            raise ValueError("search work identity is duplicated")
        seen_work_keys.add(work_key)
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
            if (edition_key, work_key) in seen_identities:
                raise ValueError("search provider identity is duplicated")
            seen_identities.add((edition_key, work_key))
            previous = admitted.setdefault(edition_key, work_key)
            if previous != work_key:
                raise ValueError("search edition work is ambiguous")
            if len(admitted) > 2:
                return tuple(sorted(admitted.items()))
    return tuple(sorted(admitted.items()))


def _admitted_work_candidates(
    source: dict[str, Any],
) -> tuple[tuple[str, str, tuple[tuple[str, str | None], ...]], ...]:
    docs = source.get("docs")
    if type(docs) is not list or len(docs) > _MAX_SEARCH_ROWS:
        raise ValueError("search result is malformed")
    admitted: dict[tuple[str, str], tuple[tuple[str, str | None], ...]] = {}
    seen_work_keys: set[str] = set()
    seen_identities: set[tuple[str, str]] = set()
    for raw_work in docs:
        work = _mapping(raw_work)
        work_key = _key(work.get("key"), _WORK_KEY)
        if work_key in seen_work_keys:
            raise ValueError("search work identity is duplicated")
        seen_work_keys.add(work_key)
        authors = _search_authors(
            work.get("author_name", []), work.get("author_key", [])
        )
        editions = _mapping(work.get("editions"))
        edition_docs = editions.get("docs")
        if type(edition_docs) is not list:
            raise ValueError("search editions are malformed")
        for raw_edition in edition_docs:
            edition = _mapping(raw_edition)
            edition_key = _key(edition.get("key"), _EDITION_KEY)
            if (edition_key, work_key) in seen_identities:
                raise ValueError("search provider identity is duplicated")
            seen_identities.add((edition_key, work_key))
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
    work_authors: tuple[tuple[str, str | None], ...] = (),
    search_work: dict[str, Any] | None = None,
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
    publication_date_text: str | None = None
    publication_date = edition.get("publish_date")
    if publication_date is None:
        add(ClaimKind.PUBLICATION_DATE, None, ClaimScope.EDITION)
    else:
        state, text = _field(ClaimKind.PUBLICATION_DATE, publication_date)
        if state is FieldState.VALID:
            add(ClaimKind.PUBLICATION_DATE, publication_date, ClaimScope.EDITION)
        elif text is None:
            try:
                publication_date_text = _text(publication_date)
            except (ValueError, EvidenceValueError):
                pass
    for value in _optional_string_list(edition.get("alternate_titles", []), _MAX_VALUES):
        add(ClaimKind.TITLE, value, ClaimScope.EDITION)
    for value in _optional_string_list(edition.get("alternative_titles", []), _MAX_VALUES):
        add(ClaimKind.TITLE, value, ClaimScope.EDITION)
    for publisher in _optional_string_list(edition.get("publishers", []), _MAX_VALUES):
        add(ClaimKind.PUBLISHER, publisher, ClaimScope.EDITION)
    if not edition.get("publishers"):
        add(ClaimKind.PUBLISHER, None, ClaimScope.EDITION)
    for imprint in _optional_string_list(edition.get("imprints", []), _MAX_VALUES):
        add(ClaimKind.IMPRINT, imprint, ClaimScope.EDITION)
    if isinstance(edition.get("imprint"), str):
        add(ClaimKind.IMPRINT, edition["imprint"], ClaimScope.EDITION)
    if not edition.get("imprints"):
        add(ClaimKind.IMPRINT, None, ClaimScope.EDITION, state=FieldState.UNSUPPORTED)
    for language in _optional_object_list(edition.get("languages", []), _MAX_VALUES):
        try:
            match = _LANGUAGE_KEY.fullmatch(_key(language.get("key"), _LANGUAGE_KEY))
            if match is not None:
                add(ClaimKind.LANGUAGE, match.group(1), ClaimScope.EDITION)
        except ValueError:
            pass
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
    for contribution in _optional_string_list(edition.get("contributions", []), _MAX_VALUES):
        add(ClaimKind.CONTRIBUTOR, contribution, ClaimScope.EDITION)

    add(ClaimKind.TITLE, work.get("title"), ClaimScope.WORK)
    add(ClaimKind.SUBTITLE, work.get("subtitle"), ClaimScope.WORK)
    for value in _optional_string_list(work.get("alternate_titles", []), _MAX_VALUES):
        add(ClaimKind.TITLE, value, ClaimScope.WORK)
    try:
        work_description = _description(work.get("description"))
    except (ValueError, EvidenceValueError):
        add(
            ClaimKind.DESCRIPTION,
            None,
            ClaimScope.WORK,
            state=FieldState.MALFORMED,
        )
    else:
        add(ClaimKind.DESCRIPTION, work_description, ClaimScope.WORK)
    if work_authors:
        for author, _ in work_authors:
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
    for value in _optional_string_list(work.get("series", []), _MAX_VALUES):
        add(ClaimKind.SERIES, value, ClaimScope.WORK)
    for value in _optional_string_list(work.get("series_number", []), _MAX_VALUES):
        add(ClaimKind.SERIES_SEQUENCE, value, ClaimScope.WORK)

    evidence: list[ProviderEvidence] = [
        ProviderEvidence(
            ProviderEvidenceKind.EDITION_WORK_RELATION,
            work_identity,
            ClaimScope.EDITION,
        )
    ]
    if publication_date_text is not None:
        evidence.append(ProviderEvidence(ProviderEvidenceKind.PUBLICATION_DATE_TEXT, publication_date_text, ClaimScope.EDITION))
    _provenance(evidence, edition, ClaimScope.EDITION)
    _provenance(evidence, work, ClaimScope.WORK)
    for value in _optional_string_list(edition.get("by_statement", []), _MAX_VALUES):
        evidence.append(ProviderEvidence(ProviderEvidenceKind.RESPONSIBILITY_STATEMENT, value, ClaimScope.EDITION))
    if isinstance(edition.get("by_statement"), str):
        try:
            by_statement = _text(edition["by_statement"])
        except (ValueError, EvidenceValueError):
            pass
        else:
            evidence.append(
                ProviderEvidence(
                    ProviderEvidenceKind.RESPONSIBILITY_STATEMENT,
                    by_statement,
                    ClaimScope.EDITION,
                )
            )
    for value in _optional_string_list(edition.get("distributors", []), _MAX_VALUES):
        evidence.append(ProviderEvidence(ProviderEvidenceKind.DISTRIBUTOR, value, ClaimScope.EDITION))
    for field, kind in (("edition_name", ProviderEvidenceKind.EDITION_STATEMENT), ("edition_statement", ProviderEvidenceKind.EDITION_STATEMENT), ("translation_of", ProviderEvidenceKind.TRANSLATION_STATEMENT)):
        if isinstance(edition.get(field), str):
            try:
                evidence.append(ProviderEvidence(kind, _text(edition[field]), ClaimScope.EDITION))
            except (ValueError, EvidenceValueError):
                pass
    _search_work_evidence(evidence, search_work)
    # Detail authors are independent work-scoped identity evidence.  They do
    # not manufacture a creator claim (the detail link has no display name),
    # and a malformed optional link is local to that member.
    detail_author_keys = _work_author_keys(work)
    for author in _optional_object_list(edition.get("authors", []), _MAX_VALUES):
        try:
            evidence.append(
                ProviderEvidence(
                    ProviderEvidenceKind.AUTHOR_IDENTITY,
                    _author_identity(_key(author.get("key"), _AUTHOR_KEY)),
                    ClaimScope.WORK,
                )
            )
        except ValueError:
            pass
    for author_key in dict.fromkeys(
        (*detail_author_keys, *(key for _, key in work_authors if key is not None))
    ):
        if author_key is not None:
            evidence.append(
                ProviderEvidence(
                    ProviderEvidenceKind.CONTRIBUTOR_IDENTITY,
                    _author_identity(author_key),
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
        _optional_string_evidence(evidence, edition, field, kind, ClaimScope.EDITION)
    for field, kind in (
        ("subjects", ProviderEvidenceKind.SUBJECT),
        ("subject_people", ProviderEvidenceKind.PERSON),
        ("subject_places", ProviderEvidenceKind.PLACE),
        ("subject_times", ProviderEvidenceKind.TIME),
    ):
        _optional_string_evidence(evidence, work, field, kind, ClaimScope.WORK)
    _optional_identifier_evidence(evidence, edition, ClaimScope.EDITION)
    _optional_identifier_evidence(evidence, work, ClaimScope.WORK)
    for field, kind, scope in (("description", ProviderEvidenceKind.EDITION_DESCRIPTION, ClaimScope.EDITION),):
        if field in edition and edition[field] is not None:
            try:
                evidence.append(ProviderEvidence(kind, _description(edition[field]), scope))
            except (ValueError, EvidenceValueError):
                pass
    for field, kind in (
        ("physical_format", ProviderEvidenceKind.PHYSICAL_FORMAT),
        ("pagination", ProviderEvidenceKind.PAGINATION),
        ("copyright_date", ProviderEvidenceKind.COPYRIGHT_DATE),
    ):
        if field in edition and edition[field] is not None:
            try:
                evidence.append(ProviderEvidence(kind, _text(edition[field]), ClaimScope.EDITION))
            except (ValueError, EvidenceValueError):
                pass
    if "number_of_pages" in edition and edition["number_of_pages"] is not None:
        pages = edition["number_of_pages"]
        if type(pages) is not int or isinstance(pages, bool) or not 1 <= pages <= 100_000:
            pages = None
        if pages is not None:
            evidence.append(ProviderEvidence(ProviderEvidenceKind.PAGE_COUNT, str(pages), ClaimScope.EDITION))
    if "first_publish_date" in work and work["first_publish_date"] is not None:
        try:
            evidence.append(ProviderEvidence(ProviderEvidenceKind.FIRST_PUBLICATION_DATE, _text(work["first_publish_date"]), ClaimScope.WORK))
        except (ValueError, EvidenceValueError):
            pass
    edition_cover = _cover_id(edition)
    work_cover = _cover_id(work)
    if edition_cover.value is not None:
        evidence.append(
            ProviderEvidence(
                ProviderEvidenceKind.EDITION_COVER_ID,
                str(edition_cover.value),
                ClaimScope.EDITION,
            )
        )
    if work_cover.value is not None:
        evidence.append(
            ProviderEvidence(
                ProviderEvidenceKind.WORK_COVER_ID,
                str(work_cover.value),
                ClaimScope.WORK,
            )
        )
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
            result.accessed_at,
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
            normalize_date_value(text)
        return FieldState.VALID, text
    except (ValueError, EvidenceValueError):
        return (
            FieldState.OVERSIZED
            if type(value) is str and len(value) > maximum
            else FieldState.MALFORMED,
            None,
        )


def _provenance(
    evidence: list[ProviderEvidence],
    source: dict[str, Any],
    scope: ClaimScope,
) -> None:
    revision, _ = _revision_projection(source)
    if revision is not None:
        evidence.append(
            ProviderEvidence(
                ProviderEvidenceKind.REVISION, revision, scope
            )
        )
    try:
        modified = _last_modified_value(source)
    except (ValueError, EvidenceValueError):
        modified = None
    if modified is not None:
        evidence.append(
            ProviderEvidence(
                ProviderEvidenceKind.LAST_MODIFIED,
                modified,
                scope,
            )
        )


def _revision_projection(source: dict[str, Any]) -> tuple[str | None, int]:
    """Validate both recognized aliases while retaining one revision value."""
    valid: list[str] = []
    rejected = 0
    for field in ("revision", "latest_revision"):
        revision = source.get(field)
        if revision is None:
            continue
        if (
            type(revision) is not int
            or isinstance(revision, bool)
            or not 1 <= revision <= 2_147_483_647
        ):
            rejected += 1
            continue
        valid.append(str(revision))
    return (valid[0] if valid else None), rejected


def _last_modified_value(source: dict[str, Any]) -> str | None:
    modified = source.get("last_modified")
    if modified is None:
        return None
    item = _mapping(modified)
    if item.get("type") != "/type/datetime":
        raise ValueError("provider last-modified value is malformed")
    return _text(item.get("value"))


def _optional_string_evidence(
    evidence: list[ProviderEvidence],
    source: dict[str, Any],
    field: str,
    kind: ProviderEvidenceKind,
    scope: ClaimScope,
) -> None:
    for value in _optional_string_list(source.get(field, []), _MAX_VALUES):
        evidence.append(ProviderEvidence(kind, value, scope))


def _optional_identifier_evidence(
    evidence: list[ProviderEvidence],
    source: dict[str, Any],
    scope: ClaimScope,
) -> None:
    if "identifiers" not in source or source["identifiers"] is None:
        return
    try:
        identifiers = _mapping(source["identifiers"])
    except ValueError:
        return
    if "wikidata" in identifiers:
        for value in _optional_string_list(identifiers["wikidata"], _MAX_VALUES):
            evidence.append(
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
    if type(value) is not list or len(value) > maximum:
        raise ValueError("provider string list is malformed")
    values = tuple(_text(item) for item in value)
    return values


def _optional_string_list(value: object, maximum: int) -> tuple[str, ...]:
    """Validate the collection bound before retaining valid source members."""
    if type(value) is not list or len(value) > maximum:
        return ()
    values: list[str] = []
    for item in value:
        try:
            values.append(_text(item))
        except (ValueError, EvidenceValueError):
            continue
    return tuple(values)


def _optional_object_list(value: object, maximum: int) -> list[dict[str, Any]]:
    if type(value) is not list or len(value) > maximum:
        return []
    return [item for raw in value if type(raw) is dict for item in [raw]]


def _unknown_projection_rejections(*documents: dict[str, Any]) -> int:
    """Count unknown additive members once per observed response document."""
    known = {
        "key", "works", "isbn_10", "isbn_13", "isbn", "title", "subtitle",
        "publish_date", "publishers", "languages", "authors", "description",
        "alternate_titles", "alternative_titles", "imprints", "imprint", "distributors",
        "by_statement", "edition_name", "edition_statement", "translation_of",
        "contributions", "series", "series_number",
        "publisher", "language", "edition_key", "editions", "docs",
        "author_name", "author_key",
        "revision", "latest_revision", "last_modified", "oclc_numbers", "lccn",
        "dewey_decimal_class", "lc_classifications", "publish_places", "subjects",
        "subject_people", "subject_places", "subject_times", "identifiers",
        "physical_format", "pagination", "copyright_date", "number_of_pages",
        "first_publish_date", "covers",
    }
    return min(
        sum(len(set(document) - known) for document in documents),
        MAX_DIAGNOSTIC_COUNT,
    )


def _parser_optional_rejections(
    document: dict[str, Any], *, search_projection: bool = False
) -> int:
    """Account at the optional parsing boundary without retaining bad values."""
    list_fields = {
        "publishers", "languages", "authors", "oclc_numbers", "lccn",
        "dewey_decimal_class", "lc_classifications", "publish_places", "subjects",
        "subject_people", "subject_places", "subject_times", "covers",
        "alternate_titles", "alternative_titles", "imprints", "distributors",
        "contributions", "series", "series_number", "publisher", "language",
        "edition_key", "author_name", "author_key",
    }
    if search_projection:
        list_fields.add("publish_date")
    object_lists = {"works", "languages", "authors"}
    scalar_fields = {
        "publish_date", "physical_format", "pagination", "by_statement", "imprint",
        "edition_name", "edition_statement", "translation_of", "description",
        "copyright_date", "number_of_pages", "first_publish_date",
    }
    if search_projection:
        scalar_fields.remove("publish_date")
    count = 0
    for field in list_fields & set(document):
        value = document[field]
        if type(value) is not list or len(value) > _MAX_VALUES:
            count += 1
            continue
        expected = int if field == "covers" else (dict if field in object_lists else str)
        for item in value:
            if type(item) is not expected:
                count += 1
            elif field == "covers" and not 0 < item <= 2_147_483_647:
                count += 1
    for field in scalar_fields & set(document):
        value = document[field]
        if field == "description":
            valid = type(value) is str or (
                type(value) is dict
                and set(value) == {"type", "value"}
                and value.get("type") == "/type/text"
                and type(value.get("value")) is str
            )
        elif field == "number_of_pages":
            valid = type(value) is int and not isinstance(value, bool) and 1 <= value <= 100_000
        else:
            try:
                _text(value)
                valid = True
            except (ValueError, EvidenceValueError):
                valid = False
        if not valid:
            count += 1
    for field, pattern in (("languages", _LANGUAGE_KEY), ("authors", _AUTHOR_KEY)):
        value = document.get(field)
        if type(value) is not list or len(value) > _MAX_VALUES:
            continue
        for member in value:
            if type(member) is not dict:
                continue
            key = member.get("key")
            if type(key) is str and pattern.fullmatch(key):
                continue
            if field == "authors" and _work_author_keys({"authors": [member]}):
                continue
            count += 1
    author_keys = document.get("author_key")
    if type(author_keys) is list and len(author_keys) <= _MAX_VALUES:
        count += sum(
            1
            for key in author_keys
            if type(key) is not str or _AUTHOR_KEY.fullmatch(key) is None
        )
    identifiers = document.get("identifiers")
    if identifiers is not None:
        if type(identifiers) is not dict:
            count += 1
        else:
            for namespace, values in identifiers.items():
                if namespace != "wikidata" or type(values) is not list or len(values) > _MAX_VALUES:
                    count += 1
                else:
                    count += sum(1 for value in values if type(value) is not str)
    _, revision_rejections = _revision_projection(document)
    count += revision_rejections
    try:
        _last_modified_value(document)
    except (ValueError, EvidenceValueError):
        count += 1
    return min(count, MAX_DIAGNOSTIC_COUNT)


# Compatibility for private parser tests; semantic accounting lives in the
# parser-owned function above while this boundary remains unknown-key-only.
def _optional_projection_rejections(
    *documents: dict[str, Any], search_projection: bool = False
) -> int:
    return min(
        sum(
            _unknown_projection_rejections(document)
            + _parser_optional_rejections(document, search_projection=search_projection)
            for document in documents
        ),
        MAX_DIAGNOSTIC_COUNT,
    )


def _search_work_document(source: dict[str, Any], work_key: str) -> dict[str, Any] | None:
    """Find the exact search document without making optional fields identity-bearing."""
    docs = source.get("docs")
    if type(docs) is not list or len(docs) > _MAX_SEARCH_ROWS:
        return None
    matched: dict[str, Any] | None = None
    for raw in docs:
        if type(raw) is not dict:
            continue
        try:
            key = _key(raw.get("key"), _WORK_KEY)
        except ValueError:
            continue
        if key == work_key:
            if matched is not None:
                raise ValueError("provider search identity is duplicated")
            matched = raw
    return matched


def _search_work_evidence(
    evidence: list[ProviderEvidence], source: dict[str, Any] | None
) -> None:
    """Search projection is work-scoped context, never a manifestation claim."""
    if source is None:
        return
    for field, kind in (
        ("publisher", ProviderEvidenceKind.SEARCH_PUBLISHER),
        ("publish_date", ProviderEvidenceKind.SEARCH_PUBLICATION_DATE),
        ("language", ProviderEvidenceKind.SEARCH_LANGUAGE),
        ("edition_key", ProviderEvidenceKind.SEARCH_EDITION),
    ):
        for value in _optional_string_list(source.get(field, []), _MAX_VALUES):
            evidence.append(ProviderEvidence(kind, value, ClaimScope.WORK))


def _search_authors(
    names: object, keys: object
) -> tuple[tuple[str, str | None], ...]:
    """Retain safe search names, binding identities only by source index.

    Search result author fields are optional evidence.  A malformed sibling
    must not make the admitted edition/work identity unsafe, but an ID can
    never be associated with a name after sorting, filtering, or truncation.
    """
    if type(names) is not list or type(keys) is not list:
        return ()
    if len(names) > _MAX_VALUES or len(keys) > _MAX_VALUES:
        return ()
    result: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for index, raw_name in enumerate(names):
        try:
            name = _text(raw_name)
            normalized = normalize_title(name).casefold()
        except (ValueError, EvidenceValueError):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        author_key: str | None = None
        if index < len(keys):
            try:
                author_key = _key(keys[index], _AUTHOR_KEY)
            except ValueError:
                pass
        result.append((name, author_key))
    return tuple(result)


def _work_author_keys(source: dict[str, Any]) -> tuple[str, ...]:
    """Retain only exact nested work-author identities, in source order.

    Open Library's work detail uses role links rather than author names.  A
    malformed optional link cannot invalidate the already-verified edition /
    work relation, and an edition-author link must never substitute for it.
    """
    value = source.get("authors", [])
    if type(value) is not list or len(value) > _MAX_VALUES:
        return ()
    keys: list[str] = []
    seen: set[str] = set()
    for member in value:
        if type(member) is not dict:
            continue
        author = member.get("author")
        role = member.get("type")
        if type(author) is not dict or type(role) is not dict:
            continue
        try:
            key = _key(author.get("key"), _AUTHOR_KEY)
        except ValueError:
            continue
        role_key = role.get("key")
        if role_key != "/type/author_role":
            continue
        if key not in seen:
            seen.add(key)
            keys.append(key)
    return tuple(keys)


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


__all__ = [
    "OPEN_LIBRARY_COVERS_ORIGIN",
    "OPEN_LIBRARY_ORIGIN",
    "OPEN_LIBRARY_SEARCH_URL",
    "OpenLibraryMetadataProvider",
    "OpenLibraryPacer",
]
