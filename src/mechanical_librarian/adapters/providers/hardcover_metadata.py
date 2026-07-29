"""One-request, closed-envelope Hardcover metadata observation."""

from __future__ import annotations

import asyncio
import json
import random
import time
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

import httpx

from mechanical_librarian.adapters.providers.retry_safety import (
    ProviderAttemptRecord,
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
    CoverProvenance,
    CoverSourceKind,
    CoverSourceState,
    cover_source_id,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind, ClaimScope, FieldState, LocalClaim, MAX_CANDIDATES,
    MAX_CONTRIBUTIONS, MAX_DESCRIPTION_CHARACTERS, MAX_SCALAR_CHARACTERS,
    MAX_SERIES, MAX_TAGS, ProviderCandidate, ProviderClaim,
    ProviderEvidence, ProviderEvidenceKind, ProviderObservation,
    ProviderQueryKind, ProviderStatus, canonical_json, comparison_links,
    isbn_family, normalize_date_value, provider_access_time,
)
from mechanical_librarian.domain.evidence_bounds import EvidenceValueError, validate_projection_text, validate_role_projection


HARDCOVER_URL = "https://api.hardcover.app/v1/graphql"
HARDCOVER_SCHEMA_COMMIT = "1c5bdbd3244e02b03ccc658a792793e39cba17a8"
MAX_RESPONSE_BYTES = 262_144
MAX_AGGREGATE_RESPONSE_BYTES = 2_097_152
MAX_GRAPHQL_ERRORS = 8
MAX_GRAPHQL_ERROR_MESSAGE_CHARACTERS = 2_048
_MAX_ATTEMPTS_PER_QUERY = 3
_MAX_ATTEMPTS_PER_OBSERVATION = 27
_MAX_TAG_ROWS = (MAX_CANDIDATES + 1) * (MAX_TAGS + 1)
_ISBN_QUERY = (
    "query MetadataEvidenceByIsbn($isbns:[String!]!){editions(where:{_or:["
    f"{{isbn_13:{{_in:$isbns}}}},{{isbn_10:{{_in:$isbns}}}}]}},order_by:{{id:asc}},limit:{MAX_CANDIDATES + 1})"
    "{id book_id isbn_10 isbn_13 asin title subtitle release_date image{url} publisher{id name parent_id} language{id code2 code3 language} contributions(order_by:{id:asc},limit:17){id contribution author{id name}}} "
    "books:books(where:{editions:{_or:[{isbn_13:{_in:$isbns}},{isbn_10:{_in:$isbns}}]}},order_by:{id:asc},limit:6)"
    "{id title description release_date image{url} contributions(order_by:{id:asc},limit:17){id contribution author{id name}} book_series(order_by:{id:asc},limit:17){id position series{id name}} taggings(order_by:{id:asc},limit:33){id tag_id}} "
    f"tags:tags(where:{{taggings:{{book:{{editions:{{_or:[{{isbn_13:{{_in:$isbns}}}},{{isbn_10:{{_in:$isbns}}}}]}}}}}}}},order_by:{{id:asc}},limit:{_MAX_TAG_ROWS + 1})"
    "{id tag tag_category{id category}}}"
)
_WORK_QUERY = (
    "query MetadataEvidenceByWork($title:String!,$authors:[String!]!){"
    f"editions(where:{{book:{{title:{{_eq:$title}},contributions:{{author:{{name:{{_in:$authors}}}}}}}}}},order_by:{{id:asc}},limit:{MAX_CANDIDATES + 1})"
    "{id book_id isbn_10 isbn_13 asin title subtitle release_date image{url} publisher{id name parent_id} language{id code2 code3 language} contributions(order_by:{id:asc},limit:17){id contribution author{id name}}} "
    "books:books(where:{title:{_eq:$title},contributions:{author:{name:{_in:$authors}}}},order_by:{id:asc},limit:6)"
    "{id title description release_date image{url} contributions(order_by:{id:asc},limit:17){id contribution author{id name}} book_series(order_by:{id:asc},limit:17){id position series{id name}} taggings(order_by:{id:asc},limit:33){id tag_id}} "
    f"tags:tags(where:{{taggings:{{book:{{title:{{_eq:$title}},contributions:{{author:{{name:{{_in:$authors}}}}}}}}}}}},order_by:{{id:asc}},limit:{_MAX_TAG_ROWS + 1})"
    "{id tag tag_category{id category}}}"
)
_QUERY = _ISBN_QUERY


class HardcoverPacer:
    """Share Hardcover's one-request-per-second start rate."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._last_start: float | None = None

    def wait(self, *, deadline: float | None) -> bool:
        now = self._clock()
        delay = (
            0.0
            if self._last_start is None
            else max(0.0, 1.0 - (now - self._last_start))
        )
        if deadline is not None and now + delay >= deadline:
            return False
        if delay:
            self._sleep(delay)
        now = self._clock()
        if deadline is not None and now >= deadline:
            return False
        self._last_start = now
        return True


class HardcoverMetadataProvider:
    def __init__(
        self,
        name: str,
        client: httpx.Client,
        maximum_bytes: int,
        timeout_seconds: float = 5.0,
        *,
        pacer: HardcoverPacer | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        if type(maximum_bytes) is not int or isinstance(maximum_bytes, bool) or maximum_bytes < 1:
            raise ValueError("provider response bound is invalid")
        if type(timeout_seconds) not in {int, float} or isinstance(timeout_seconds, bool) or not 0 < timeout_seconds <= 60:
            raise ValueError("provider timeout is invalid")
        self._name, self._client = name, client
        self._maximum_bytes = min(maximum_bytes, MAX_RESPONSE_BYTES)
        self._remaining_response_bytes = min(
            self._maximum_bytes * 9, MAX_AGGREGATE_RESPONSE_BYTES
        )
        self._timeout_seconds = float(timeout_seconds)
        self._pacer = pacer
        self._clock = clock
        self._wall_clock = wall_clock
        self._sleep = sleep
        self._jitter = jitter
        self._attempt_count = 0
        self._attempt_records: list[ProviderAttemptRecord] = []
        self._accessed_at: str | None = None
        self._called = False
        self._ledger = _DiscoveryRequestLedger("hardcover", HARDCOVER_SCHEMA_COMMIT)
        self.cover_descriptors: tuple[ProviderCoverDescriptor, ...] = ()

    @property
    def attempt_records(self) -> tuple[ProviderAttemptRecord, ...]:
        return tuple(self._attempt_records)

    @property
    def accessed_at(self) -> str | None:
        return self._accessed_at

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
        ledger = _DiscoveryRequestLedger("hardcover", HARDCOVER_SCHEMA_COMMIT)
        for lane in plan.lanes:
            ledger.register(kind=lane.kind.value, matched_family=lane.matched_family, title=lane.title, authors=lane.authors, projection=(_ISBN_QUERY if lane.kind is ProviderQueryKind.ISBN else _WORK_QUERY))
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
                        _ISBN_QUERY
                        if lookup.kind is ProviderQueryKind.ISBN
                        else _WORK_QUERY
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
            tuple(results),
            request_accounting=ledger.accounting(
                max(0, self._attempt_count - ledger.initial)
            ),
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
        references: tuple[str, ...] = ()
        if lookup.kind is ProviderQueryKind.ISBN:
            if not usable:
                return ProviderObservation(
                    self._name,
                    ProviderStatus.INSUFFICIENT_INPUT,
                    query_kind=lookup.kind,
                )
            references = tuple(sorted(claim.reference for claim in usable))
            body = {
                "operationName": "MetadataEvidenceByIsbn",
                "query": _ISBN_QUERY,
                "variables": {
                    "isbns": sorted(
                        {
                            claim.canonical
                            for claim in usable
                            if claim.canonical is not None
                        }
                    )
                },
            }
        elif lookup.kind is ProviderQueryKind.WORK:
            body = {
                "operationName": "MetadataEvidenceByWork",
                "query": _WORK_QUERY,
                "variables": {
                    "authors": list(lookup.authors),
                    "title": lookup.title,
                },
            }
        else:
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
            )
        try:
            payload = self._request(body, deadline=deadline)
        except _ProviderFailure as error:
            return result(error.status)
        try:
            if _has_graphql_errors(payload):
                self._mark_response_attempt("provider_declared_error")
                return result(ProviderStatus.UNAVAILABLE)
            candidates = _parse_candidates(
                payload,
                self._name,
                local_claims,
            )
            if (
                lookup.kind is ProviderQueryKind.ISBN
                and lookup.matched_family is not None
            ):
                if any(
                    lookup.matched_family
                    not in {
                        family
                        for claim in candidate.claims
                        if claim.kind is ClaimKind.ISBN
                        and claim.value is not None
                        and (family := isbn_family(claim.value)) is not None
                    }
                    for candidate in candidates
                ):
                    return result(ProviderStatus.MALFORMED)
        except _CandidateLimit:
            self._mark_response_attempt("response_oversized")
            return result(ProviderStatus.CANDIDATE_LIMIT_EXCEEDED)
        except (ValueError, TypeError, UnicodeError, RecursionError, json.JSONDecodeError):
            self._mark_response_attempt("response_malformed")
            return result(ProviderStatus.MALFORMED)
        try:
            self.cover_descriptors = _parse_cover_descriptors(
                payload,
                self._name,
                lookup.kind,
            )
        except (ValueError, TypeError, UnicodeError, RecursionError, json.JSONDecodeError):
            self.cover_descriptors = ()
        observation = result(
            ProviderStatus.CANDIDATES if candidates else ProviderStatus.NO_MATCH,
            candidates,
        )
        return observation

    def _request(
        self, body: dict[str, object], *, deadline: float | None
    ) -> bytes:
        for retry_index in range(_MAX_ATTEMPTS_PER_QUERY):
            started = self._clock()
            attempts_before_dispatch = self._attempt_count
            try:
                payload = self._request_once(body, deadline=deadline)
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
            return payload
        raise _ProviderFailure(ProviderStatus.UNAVAILABLE)

    def _request_once(
        self, body: dict[str, object], *, deadline: float | None
    ) -> bytes:
        if self._remaining_response_bytes <= 0:
            raise _ProviderFailure(ProviderStatus.INCOMPLETE)
        if self._attempt_count >= _MAX_ATTEMPTS_PER_OBSERVATION:
            raise _ProviderFailure(ProviderStatus.INCOMPLETE)
        if self._pacer is not None and not self._pacer.wait(
            deadline=deadline
        ):
            raise _ProviderFailure(ProviderStatus.UNAVAILABLE)
        remaining = self._timeout_seconds
        if deadline is not None:
            remaining = min(remaining, deadline - self._clock())
        if remaining <= 0:
            raise _ProviderFailure(ProviderStatus.UNAVAILABLE)
        try:
            if deadline is not None and self._clock() >= deadline:
                raise _ProviderFailure(ProviderStatus.UNAVAILABLE)
            if self._accessed_at is None:
                self._accessed_at = provider_access_time(self._wall_clock())
            self._attempt_count += 1
            with self._client.stream(
                "POST",
                HARDCOVER_URL,
                content=canonical_json(body).encode(),
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
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
                    raise _ProviderFailure(
                        ProviderStatus.MALFORMED,
                        category="response_malformed",
                    )
                payload = _body(
                    response,
                    min(
                        self._maximum_bytes,
                        self._remaining_response_bytes,
                    ),
                    deadline=deadline,
                    clock=self._clock,
                    consume=self._consume_response_bytes,
                    allow_empty=response.status_code != 200,
                )
                if not identity_encoding:
                    raise _ProviderFailure(
                        ProviderStatus.MALFORMED,
                        category="response_malformed",
                    )
                if response.status_code == 401:
                    raise _ProviderFailure(
                        ProviderStatus.AUTHENTICATION,
                        category="http_401",
                    )
                if response.status_code == 403:
                    raise _ProviderFailure(
                        ProviderStatus.AUTHORIZATION,
                        category="http_403",
                    )
                if response.status_code == 429:
                    raise _ProviderFailure(
                        ProviderStatus.RATE_LIMITED,
                        category="http_429",
                        retryable=retry_after is not None,
                        retry_after=retry_after,
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
                    raise _ProviderFailure(
                        ProviderStatus.MALFORMED,
                        category="response_malformed",
                    )
                return payload
        except _DeadlineExpired:
            raise _ProviderFailure(
                ProviderStatus.UNAVAILABLE,
                category="deadline",
            ) from None
        except _ResponseOversized:
            self._remaining_response_bytes = 0
            raise _ProviderFailure(
                ProviderStatus.OVERSIZED,
                category="response_oversized",
            ) from None
        except _ProviderFailure:
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

    def _consume_response_bytes(self, amount: int) -> None:
        self._remaining_response_bytes -= amount
        if self._remaining_response_bytes < 0:
            self._remaining_response_bytes = 0
            raise _ResponseOversized

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

    def _mark_response_attempt(self, category: str) -> None:
        if not self._attempt_records:
            return
        prior = self._attempt_records[-1]
        if prior.outcome != "response_received":
            return
        self._attempt_records[-1] = ProviderAttemptRecord(
            provider=prior.provider,
            ordinal=prior.ordinal,
            category=category,
            delay_seconds=None,
            elapsed_seconds=prior.elapsed_seconds,
            outcome="terminal",
        )


def _body(
    response: httpx.Response,
    maximum: int,
    *,
    deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    consume: Callable[[int], None] = lambda _: None,
    allow_empty: bool = False,
) -> bytes:
    length = response.headers.get("content-length")
    if length is not None:
        try:
            declared = int(length)
        except ValueError:
            raise ValueError("provider response length is invalid") from None
        if declared < 0:
            raise ValueError("provider response length is invalid")
        if declared > maximum:
            raise _ResponseOversized
    chunks: list[bytes] = []
    size = 0
    raw_chunks = (
        (response.content,)
        if response.is_stream_consumed
        else response.iter_raw()
    )
    for chunk in raw_chunks:
        consume(len(chunk))
        size += len(chunk)
        if size > maximum:
            raise _ResponseOversized
        chunks.append(chunk)
        if deadline is not None and clock() >= deadline:
            raise _DeadlineExpired
    if not chunks and not allow_empty:
        raise ValueError("provider response empty")
    return b"".join(chunks)


def _has_graphql_errors(payload: bytes) -> bool:
    source = json.loads(
        payload.decode("utf-8", "strict"),
        object_pairs_hook=_unique_object,
        parse_constant=_reject_number,
        parse_float=Decimal,
    )
    if type(source) is not dict:
        raise ValueError("provider envelope invalid")
    if "errors" not in source:
        _closed(source, {"data"})
        return False
    root = _closed(
        source,
        {"data", "errors"} if "data" in source else {"errors"},
    )
    errors = root["errors"]
    if (
        type(errors) is not list
        or not errors
        or len(errors) > MAX_GRAPHQL_ERRORS
    ):
        raise ValueError("provider errors invalid")
    for value in errors:
        error = _closed_subset(
            value,
            {"message"},
            {"message", "locations", "path", "extensions"},
        )
        _text(error["message"], MAX_GRAPHQL_ERROR_MESSAGE_CHARACTERS)
        if "locations" in error:
            locations = error["locations"]
            if (
                type(locations) is not list
                or len(locations) > MAX_GRAPHQL_ERRORS
            ):
                raise ValueError("provider error locations invalid")
            for raw_location in locations:
                location = _closed(raw_location, {"line", "column"})
                for coordinate in location.values():
                    if (
                        type(coordinate) is not int
                        or isinstance(coordinate, bool)
                        or not 0 < coordinate <= 2_147_483_647
                    ):
                        raise ValueError("provider error location invalid")
        if "path" in error:
            path = error["path"]
            if type(path) is not list or len(path) > 32:
                raise ValueError("provider error path invalid")
            for segment in path:
                if type(segment) is str:
                    _text(segment, MAX_SCALAR_CHARACTERS)
                elif (
                    type(segment) is not int
                    or isinstance(segment, bool)
                    or not 0 <= segment <= 2_147_483_647
                ):
                    raise ValueError("provider error path segment invalid")
        if "extensions" in error:
            extensions = error["extensions"]
            if type(extensions) is not dict or len(extensions) > 8:
                raise ValueError("provider error extensions invalid")
            for key, extension in extensions.items():
                _text(key, 64)
                if type(extension) is str:
                    _text(extension, MAX_SCALAR_CHARACTERS)
                elif extension is not None and type(extension) not in {
                    int,
                    bool,
                }:
                    raise ValueError("provider error extension invalid")
    return True


def _parse_candidates(
    payload: bytes,
    provider: str,
    local_claims: tuple[LocalClaim, ...],
) -> tuple[ProviderCandidate, ...]:
    source = json.loads(payload.decode("utf-8", "strict"), object_pairs_hook=_unique_object, parse_constant=_reject_number, parse_float=Decimal)
    root = _closed(source, {"data"})
    data = _closed(root["data"], {"editions", "books", "tags"})
    rows, books, tags = data["editions"], data["books"], data["tags"]
    if (
        type(rows) is not list
        or type(books) is not list
        or type(tags) is not list
        or len(rows) > MAX_CANDIDATES
        or len(tags) > _MAX_TAG_ROWS
    ):
        raise _CandidateLimit if type(rows) is list and len(rows) > MAX_CANDIDATES else ValueError("provider rows invalid")
    work_keys = {
        "id",
        "title",
        "description",
        "release_date",
        "image",
        "contributions",
        "book_series",
        "taggings",
    }
    indexed_tags = {
        _identifier(_closed(tag, {"id", "tag", "tag_category"})["id"]): _closed(
            tag, {"id", "tag", "tag_category"}
        )
        for tag in tags
    }
    if len(indexed_tags) != len(tags):
        raise ValueError("provider taxonomy invalid")
    used_tags: set[int] = set()
    indexed: dict[int, dict[str, Any]] = {}
    for raw_book in books:
        book = _closed(raw_book, work_keys)
        raw_taggings = book["taggings"]
        if type(raw_taggings) is not list:
            raise ValueError("provider taxonomy invalid")
        taggings: list[dict[str, Any]] = []
        for raw_tagging in raw_taggings:
            tagging = _closed(raw_tagging, {"id", "tag_id"})
            _identifier(tagging["id"])
            tag_id = _identifier(tagging["tag_id"])
            if tag_id not in indexed_tags:
                raise ValueError("provider taxonomy invalid")
            used_tags.add(tag_id)
            taggings.append({"id": tagging["id"], "tag": indexed_tags[tag_id]})
        book = {**book, "taggings": taggings}
        book_id = _identifier(book["id"])
        if book_id in indexed:
            raise ValueError("provider work invalid")
        indexed[book_id] = book
    if len(indexed) != len(books):
        raise ValueError("provider work invalid")
    if used_tags != set(indexed_tags):
        raise ValueError("provider taxonomy invalid")
    candidates: list[ProviderCandidate] = []
    used: set[int] = set()
    for row_value in rows:
        if type(row_value) is dict and "image" not in row_value:
            row_value = {**row_value, "image": None}
        row = _closed(row_value, {"id", "book_id", "isbn_10", "isbn_13", "asin", "title", "subtitle", "release_date", "image", "publisher", "language", "contributions"})
        edition_id, book_id = _identifier(row["id"]), _identifier(row["book_id"])
        if book_id not in indexed:
            raise ValueError("provider unreferenced work")
        used.add(book_id)
        identity = f"hardcover-edition-{edition_id:010d}"
        work_identity = f"hardcover-work-{book_id:010d}"
        claims = _edition_claims(
            provider,
            identity,
            row,
            local_claims,
        )
        claims.extend(_work_claims(provider, identity, indexed[book_id], local_claims, len(claims)))
        candidates.append(
            ProviderCandidate(
                identity,
                work_identity,
                tuple(claims),
                _work_evidence(indexed[book_id]),
            )
        )
    if used != set(indexed) or len({candidate.identity for candidate in candidates}) != len(candidates):
        raise ValueError("provider join invalid")
    return tuple(sorted(candidates, key=lambda candidate: candidate.identity))


def _parse_cover_descriptors(
    payload: bytes,
    provider: str,
    query_kind: ProviderQueryKind,
) -> tuple[ProviderCoverDescriptor, ...]:
    source = json.loads(payload.decode("utf-8", "strict"), object_pairs_hook=_unique_object, parse_constant=_reject_number, parse_float=Decimal)
    data = _closed(
        _closed(source, {"data"})["data"],
        {"editions", "books", "tags"},
    )
    rows = data["editions"]
    books = data["books"]
    if (
        type(rows) is not list
        or len(rows) > MAX_CANDIDATES
        or type(books) is not list
        or len(books) > MAX_CANDIDATES + 1
    ):
        raise ValueError("provider rows invalid")
    descriptors: list[ProviderCoverDescriptor] = []
    for row_value in rows:
        if type(row_value) is dict and "image" not in row_value:
            row_value = {**row_value, "image": None}
        row = _closed(row_value, {"id", "book_id", "isbn_10", "isbn_13", "asin", "title", "subtitle", "release_date", "image", "publisher", "language", "contributions"})
        url, terminal_state = _cover_url(row["image"])
        edition_id = _identifier(row["id"])
        book_id = _identifier(row["book_id"])
        work_identity = f"hardcover-work-{book_id:010d}"
        edition_identity = f"hardcover-edition-{edition_id:010d}"
        descriptors.append(
            ProviderCoverDescriptor(
                provider,
                CoverSourceKind.HARDCOVER,
                cover_source_id(
                    CoverSourceKind.HARDCOVER,
                    f"{provider}:edition:{edition_id}",
                ),
                url,
                CoverProvenance(
                    provider,
                    (
                        CoverDesignation.EXACT_EDITION
                        if query_kind is ProviderQueryKind.ISBN
                        else CoverDesignation.RELATED_EDITION
                    ),
                    work_identity,
                    edition_identity,
                    _edition_cover_context(row),
                ),
                terminal_state=terminal_state,
            )
        )
    work_keys = {
        "id",
        "title",
        "description",
        "release_date",
        "image",
        "contributions",
        "book_series",
        "taggings",
    }
    for raw_book in books:
        book = _closed(raw_book, work_keys)
        book_id = _identifier(book["id"])
        url, terminal_state = _cover_url(book["image"])
        descriptors.append(
            ProviderCoverDescriptor(
                provider,
                CoverSourceKind.HARDCOVER,
                cover_source_id(
                    CoverSourceKind.HARDCOVER,
                    f"{provider}:work:{book_id}:default",
                ),
                url,
                CoverProvenance(
                    provider,
                    CoverDesignation.DEFAULT,
                    f"hardcover-work-{book_id:010d}",
                    context=_work_cover_context(book),
                ),
                terminal_state=terminal_state,
            )
        )
    return tuple(descriptors)


def _cover_url(value: object) -> tuple[str | None, CoverSourceState | None]:
    try:
        image = _closed_or_none(value, {"url"})
    except (TypeError, ValueError):
        return None, CoverSourceState.UNSAFE_REMOTE
    if image is None:
        return None, None
    url = image["url"]
    if type(url) is not str or not url:
        return None, CoverSourceState.UNSAFE_REMOTE
    if len(url) > 2048:
        return None, CoverSourceState.OVERSIZED
    return url, None


def _work_cover_context(book: dict[str, Any]) -> tuple[CoverContext, ...]:
    if book["release_date"] is None:
        return ()
    value = _text(book["release_date"], MAX_SCALAR_CHARACTERS)
    normalize_date_value(value)
    return (CoverContext(CoverContextField.PUBLICATION_DATE, value),)


def _edition_cover_context(row: dict[str, Any]) -> tuple[CoverContext, ...]:
    values: set[CoverContext] = set()
    for key in ("isbn_10", "isbn_13"):
        if row[key] is not None:
            values.add(
                CoverContext(
                    CoverContextField.ISBN,
                    _text(row[key], MAX_SCALAR_CHARACTERS),
                )
            )
    language = _closed_or_none(
        row["language"], {"id", "code2", "code3", "language"}
    )
    if language is not None and (language["code2"] or language["code3"]):
        values.add(
            CoverContext(
                CoverContextField.LANGUAGE,
                _text(
                    language["code2"] or language["code3"],
                    MAX_SCALAR_CHARACTERS,
                ),
            )
        )
    publisher = _closed_or_none(
        row["publisher"], {"id", "name", "parent_id"}
    )
    if publisher is not None and publisher["name"] is not None:
        values.add(
            CoverContext(
                CoverContextField.PUBLISHER,
                _text(publisher["name"], MAX_SCALAR_CHARACTERS),
            )
        )
    if row["release_date"] is not None:
        value = _text(row["release_date"], MAX_SCALAR_CHARACTERS)
        normalize_date_value(value)
        values.add(
            CoverContext(CoverContextField.PUBLICATION_DATE, value)
        )
    return tuple(
        sorted(values, key=lambda value: (value.field.value, value.value))
    )


def _edition_claims(
    provider: str,
    identity: str,
    row: dict[str, Any],
    local: tuple[LocalClaim, ...],
) -> list[ProviderClaim]:
    claims: list[ProviderClaim] = []
    def add(kind: ClaimKind, value: object, scope: ClaimScope = ClaimScope.EDITION, applicability: str = "not_applicable") -> None:
        claims.append(_claim(provider, identity, kind, value, scope, applicability, None, local, len(claims)))
    for key, kind in (("isbn_10", ClaimKind.ISBN), ("isbn_13", ClaimKind.ISBN), ("asin", ClaimKind.ASIN), ("title", ClaimKind.TITLE), ("subtitle", ClaimKind.SUBTITLE), ("release_date", ClaimKind.PUBLICATION_DATE)):
        add(
            kind,
            row[key],
            ClaimScope.EDITION,
            (
                provider_isbn_applicability(row[key], local)
                if kind is ClaimKind.ISBN
                else "not_applicable"
            ),
        )
    publisher = _closed_or_none(row["publisher"], {"id", "name", "parent_id"})
    add(ClaimKind.PUBLISHER, None if publisher is None else publisher["name"])
    claims.append(_state_claim(provider, identity, ClaimKind.IMPRINT, FieldState.UNSUPPORTED, ClaimScope.EDITION, local, len(claims)))
    language = _closed_or_none(row["language"], {"id", "code2", "code3", "language"})
    add(ClaimKind.LANGUAGE, None if language is None else (language["code2"] or language["code3"]))
    claims.extend(_contributions(provider, identity, row["contributions"], ClaimScope.EDITION, local, len(claims)))
    return claims


def _work_claims(provider: str, identity: str, book: dict[str, Any], local: tuple[LocalClaim, ...], offset: int) -> list[ProviderClaim]:
    claims = [_claim(provider, identity, ClaimKind.TITLE, book["title"], ClaimScope.WORK, "not_applicable", None, local, offset), _claim(provider, identity, ClaimKind.DESCRIPTION, book["description"], ClaimScope.WORK, "not_applicable", None, local, offset + 1)]
    claims.extend(_contributions(provider, identity, book["contributions"], ClaimScope.WORK, local, len(claims) + offset))
    series, series_overflow = _bounded_list(book["book_series"], MAX_SERIES, {"id", "position", "series"})
    for item in series:
        _closed_or_none(item["series"], {"id", "name"})
    if series_overflow:
        claims.extend((_state_claim(provider, identity, ClaimKind.SERIES, FieldState.OVERSIZED, ClaimScope.WORK, local, len(claims) + offset), _state_claim(provider, identity, ClaimKind.SERIES_SEQUENCE, FieldState.OVERSIZED, ClaimScope.WORK, local, len(claims) + offset + 1)))
        series = []
    for item in series:
        series = _closed_or_none(item["series"], {"id", "name"})
        claims.append(_claim(provider, identity, ClaimKind.SERIES, None if series is None else series["name"], ClaimScope.WORK, "not_applicable", None, local, len(claims) + offset))
        claims.append(_claim(provider, identity, ClaimKind.SERIES_SEQUENCE, item["position"], ClaimScope.WORK, "not_applicable", None, local, len(claims) + offset))
    taggings, taggings_overflow = _bounded_list(
        book["taggings"], MAX_TAGS, {"id", "tag"}
    )
    categorized: list[tuple[ClaimKind, str]] = []
    seen: dict[str, ClaimKind] = {}
    for item in taggings:
        tag = _closed_or_none(item["tag"], {"id", "tag", "tag_category"})
        if tag is None:
            raise ValueError("provider taxonomy is malformed")
        _identifier(tag["id"])
        category = _closed_or_none(
            tag["tag_category"], {"id", "category"}
        )
        if category is None:
            raise ValueError("provider taxonomy is malformed")
        _identifier(category["id"])
        name = _text(tag["tag"], MAX_SCALAR_CHARACTERS)
        category_name = _text(category["category"], MAX_SCALAR_CHARACTERS)
        if category_name == "Genre":
            kind = ClaimKind.GENRE
        elif category_name in {"Mood", "ContentWarning"}:
            kind = ClaimKind.TAG
        else:
            raise ValueError("provider taxonomy category is unsupported")
        taxonomy_identity = " ".join(
            unicodedata.normalize("NFC", name).split()
        ).casefold()
        previous = seen.get(taxonomy_identity)
        if previous is not None and previous is not kind:
            raise ValueError("provider taxonomy conflicts")
        if previous is None:
            seen[taxonomy_identity] = kind
            categorized.append((kind, name))
    if taggings_overflow:
        claims.extend(
            _state_claim(
                provider,
                identity,
                kind,
                FieldState.OVERSIZED,
                ClaimScope.WORK,
                local,
                len(claims) + offset + index,
            )
            for index, kind in enumerate((ClaimKind.GENRE, ClaimKind.TAG))
        )
        return claims
    for kind, name in categorized:
        claims.append(
            _claim(
                provider,
                identity,
                kind,
                name,
                ClaimScope.WORK,
                "not_applicable",
                None,
                local,
                len(claims) + offset,
            )
        )
    present = {kind for kind, _ in categorized}
    for kind in (ClaimKind.GENRE, ClaimKind.TAG):
        if kind not in present:
            claims.append(
                _state_claim(
                    provider,
                    identity,
                    kind,
                    FieldState.ABSENT,
                    ClaimScope.WORK,
                    local,
                    len(claims) + offset,
                )
            )
    return claims


def _work_evidence(book: dict[str, Any]) -> tuple[ProviderEvidence, ...]:
    evidence: set[ProviderEvidence] = set()
    release_date = book["release_date"]
    if release_date is not None:
        value = _text(release_date, MAX_SCALAR_CHARACTERS)
        normalize_date_value(value)
        evidence.add(
            ProviderEvidence(
                ProviderEvidenceKind.FIRST_PUBLICATION_DATE,
                value,
                ClaimScope.WORK,
            )
        )
    contributions, overflow = _bounded_list(
        book["contributions"],
        MAX_CONTRIBUTIONS,
        {"id", "contribution", "author"},
    )
    if overflow:
        return tuple(
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
    for contribution in contributions:
        try:
            role = validate_role_projection(
                contribution["contribution"]
            ).casefold()
        except EvidenceValueError:
            # The claim projection records the malformed role. It cannot
            # safely support identity evidence, but it must not discard the
            # otherwise valid candidate.
            continue
        author = _field_local_author(contribution["author"])
        if author is None:
            continue
        try:
            author_id = _identifier(author["id"])
        except ValueError:
            continue
        if role == "author":
            evidence.add(
                ProviderEvidence(
                    ProviderEvidenceKind.AUTHOR_IDENTITY,
                    f"hardcover-author-{author_id:010d}",
                    ClaimScope.WORK,
                )
            )
        else:
            evidence.add(
                ProviderEvidence(
                    ProviderEvidenceKind.CONTRIBUTOR_IDENTITY,
                    f"hardcover-author-{author_id:010d}",
                    ClaimScope.WORK,
                    role,
                )
            )
    return tuple(
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


def _contributions(provider: str, identity: str, value: object, scope: ClaimScope, local: tuple[LocalClaim, ...], offset: int) -> list[ProviderClaim]:
    claims: list[ProviderClaim] = []
    contributions, overflow = _bounded_list(value, MAX_CONTRIBUTIONS, {"id", "contribution", "author"})
    if overflow:
        for item in contributions:
            _closed_or_none(item["author"], {"id", "name"})
        # The admitted max+1 probe preserves every role family present without
        # retaining an unbounded contribution list.
        kinds: set[ClaimKind] = set()
        for probe in contributions:
            role = probe["contribution"]
            if role is None:
                kinds.add(ClaimKind.CONTRIBUTOR)
                continue
            try:
                role = validate_role_projection(role).casefold()
            except EvidenceValueError:
                raise ValueError("provider contribution role invalid") from None
            kinds.add(ClaimKind.CREATOR if role == "author" else ClaimKind.CONTRIBUTOR)
        return [_state_claim(provider, identity, kind, FieldState.OVERSIZED, scope, local, offset + index) for index, kind in enumerate(sorted(kinds, key=lambda value: value.value))]
    for item in contributions:
        try:
            role = validate_role_projection(item["contribution"]).casefold()
        except EvidenceValueError:
            claims.append(_state_claim(provider, identity, ClaimKind.CONTRIBUTOR, FieldState.MALFORMED, scope, local, offset + len(claims)))
            continue
        kind = ClaimKind.CREATOR if role == "author" else ClaimKind.CONTRIBUTOR
        author = _field_local_author(item["author"])
        if author is None:
            claims.append(_state_claim(provider, identity, kind, FieldState.MALFORMED, scope, local, offset + len(claims)))
            continue
        claims.append(_claim(provider, identity, kind, author["name"], scope, "not_applicable", "author" if kind is ClaimKind.CREATOR else role, local, offset + len(claims)))
    return claims


def _field_local_author(value: object) -> dict[str, Any] | None:
    if type(value) is not dict or set(value) != {"id", "name"}:
        return None
    return value


def _claim(provider: str, identity: str, kind: ClaimKind, value: object, scope: ClaimScope, applicability: str, role: str | None, local: tuple[LocalClaim, ...], raw_order: int) -> ProviderClaim:
    state, text = _field(kind, value)
    bare = ProviderClaim.create(provider=provider, candidate=identity, kind=kind, value=text, state=state, scope=scope, applicability=applicability, role=role, raw_order=raw_order)
    return ProviderClaim.create(provider=provider, candidate=identity, kind=kind, value=text, state=state, scope=scope, applicability=applicability, role=role, raw_order=raw_order, links=comparison_links(bare, local))


def _state_claim(provider: str, identity: str, kind: ClaimKind, state: FieldState, scope: ClaimScope, local: tuple[LocalClaim, ...], raw_order: int) -> ProviderClaim:
    bare = ProviderClaim.create(provider=provider, candidate=identity, kind=kind, value=None, state=state, scope=scope, applicability="not_applicable", raw_order=raw_order)
    return ProviderClaim.create(provider=provider, candidate=identity, kind=kind, value=None, state=state, scope=scope, applicability="not_applicable", raw_order=raw_order, links=comparison_links(bare, local))


def _field(kind: ClaimKind, value: object) -> tuple[FieldState, str | None]:
    if value is None:
        return FieldState.ABSENT, None
    maximum = MAX_DESCRIPTION_CHARACTERS if kind is ClaimKind.DESCRIPTION else MAX_SCALAR_CHARACTERS
    try:
        if kind is ClaimKind.SERIES_SEQUENCE and type(value) in {Decimal, int} and type(value) is not bool:
            return FieldState.VALID, _position(value)
        text = validate_projection_text(value, "provider claim value", maximum)
        if kind is ClaimKind.ISBN:
            text = _isbn(text, len(text.replace("-", "").replace(" ", "")))
        if kind is ClaimKind.ASIN:
            text = text.upper()
            if (
                len(text) != 10
                or not text.isascii()
                or not text.isalnum()
                or not text.startswith("B")
            ):
                raise ValueError("provider ASIN invalid")
        if kind is ClaimKind.PUBLICATION_DATE:
            normalize_date_value(text)
        if kind is ClaimKind.SERIES_SEQUENCE:
            text = _position(text)
        return FieldState.VALID, text
    except (ValueError, EvidenceValueError):
        return (FieldState.OVERSIZED if type(value) is str and len(value) > maximum else FieldState.MALFORMED), None


def _bounded_list(value: object, maximum: int, keys: set[str]) -> tuple[list[dict[str, Any]], bool]:
    if type(value) is not list:
        raise ValueError("provider list invalid")
    values = [_closed(item, keys) for item in value]
    return values, len(values) > maximum


def _closed(value: object, keys: set[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise ValueError("provider object invalid")
    return value


def _closed_subset(
    value: object,
    required: set[str],
    allowed: set[str],
) -> dict[str, Any]:
    if (
        type(value) is not dict
        or not required <= set(value)
        or not set(value) <= allowed
    ):
        raise ValueError("provider object invalid")
    return value


def _closed_or_none(value: object, keys: set[str]) -> dict[str, Any] | None:
    return None if value is None else _closed(value, keys)


def _identifier(value: object) -> int:
    if type(value) is not int or type(value) is bool or not 1 <= value <= 2_147_483_647:
        raise ValueError("provider identity invalid")
    return value


def _text(value: object, maximum: int) -> str:
    if type(value) is not str or not value or len(value) > maximum or value.strip() != value or "\x00" in value or any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value):
        raise ValueError("provider text invalid")
    return value


def _isbn(value: str, length: int) -> str:
    text = _text(value, 32).replace("-", "").replace(" ", "").upper()
    if length not in {10, 13} or len(text) != length or any(not character.isdigit() and not (length == 10 and character == "X" and index == 9) for index, character in enumerate(text)):
        raise ValueError("provider ISBN invalid")
    if length == 13 and int(text[-1]) != (10 - sum((1 if index % 2 == 0 else 3) * int(character) for index, character in enumerate(text[:12])) % 10) % 10:
        raise ValueError("provider ISBN invalid")
    if length == 10 and sum((10 - index) * (10 if character == "X" else int(character)) for index, character in enumerate(text)) % 11:
        raise ValueError("provider ISBN invalid")
    return text


def _position(value: int | str | Decimal) -> str:
    try:
        decimal = Decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise ValueError("provider position invalid") from error
    if not decimal.is_finite():
        raise ValueError("provider position invalid")
    source = decimal.as_tuple()
    digits_tuple = source.digits
    exponent = source.exponent
    while len(digits_tuple) > 1 and exponent < 0 and digits_tuple[-1] == 0:
        digits_tuple = digits_tuple[:-1]
        exponent += 1
    normalized = Decimal((source.sign, digits_tuple, exponent))
    if normalized.is_zero():
        return "0"
    digits = len(digits_tuple)
    sign = 1 if source.sign else 0
    if exponent >= 0:
        length = sign + digits + exponent
    elif digits + exponent > 0:
        length = sign + digits + 1
    else:
        length = sign + 2 + -(digits + exponent) + digits
    if not 1 <= length <= MAX_SCALAR_CHARACTERS:
        raise ValueError("provider position invalid")
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("provider field duplicated")
        result[key] = value
    return result


def _reject_number(_: str) -> float:
    raise ValueError("provider number invalid")


class _CandidateLimit(ValueError):
    pass


class _ResponseOversized(ValueError):
    pass


class _DeadlineExpired(Exception):
    pass


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
    "HARDCOVER_SCHEMA_COMMIT",
    "HARDCOVER_URL",
    "HardcoverMetadataProvider",
    "HardcoverPacer",
]
