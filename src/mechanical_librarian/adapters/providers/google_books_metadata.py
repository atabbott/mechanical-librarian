"""Bounded, report-only Google Books metadata observation."""

from __future__ import annotations

import math
import random
import re
import time
from dataclasses import replace
from datetime import datetime, timedelta
from datetime import time as datetime_time
from typing import Any, Callable, cast
from zoneinfo import ZoneInfo

import httpx

from mechanical_librarian.adapters.providers.retry_safety import (
    ProviderAttemptRecord,
    ProviderFailure,
    ProviderIncompleteCauseRecord,
    ProviderRequestPacer,
    _DiscoveryRequestLedger,
)
from mechanical_librarian.adapters.providers.sdk import (
    BoundedProviderSession,
    ProviderClocks,
    ProviderResponseFacts,
    decode_json,
    provider_bounds,
)
from mechanical_librarian.adapters.providers.visible_text import (
    DESCRIPTION_OVERSIZED as _DESCRIPTION_OVERSIZED,
)
from mechanical_librarian.adapters.providers.visible_text import (
    visible_description as _visible_description,
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
    MAX_DIAGNOSTIC_COUNT,
    FailureCategory,
    FailureComponent,
    FailureDiagnostic,
    FailureStage,
)
from mechanical_librarian.domain.metadata_report import (
    MAX_DESCRIPTION_CHARACTERS,
    MAX_PROVIDER_EVIDENCE,
    MAX_SCALAR_CHARACTERS,
    ClaimKind,
    ClaimScope,
    FieldState,
    LocalClaim,
    ProviderCandidate,
    ProviderClaim,
    ProviderEvidence,
    ProviderEvidenceKind,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    comparison_links,
    finalize_candidate_comparisons,
    isbn_family,
    normalize_title,
)
from mechanical_librarian.domain.work_identity import (
    WorkDispatchLineage,
    WorkDispatchRung,
    WorkQuery,
    narrowing_work_query,
    work_query_ladder,
)

GOOGLE_BOOKS_URL = "https://www.googleapis.com/books/v1/volumes"
_VOLUME_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MAX_RESULTS = 5
_MAX_RETURNED_RESULTS = _MAX_RESULTS + 1
_MAX_VALUES = 8
_MAX_AGGREGATE_RESPONSE_BYTES = 2_097_152
_MAX_ATTEMPTS_PER_QUERY = 6
_MAX_ATTEMPTS_PER_OBSERVATION = 54
_BURST_WINDOW_SECONDS = 100
_PACIFIC = ZoneInfo("America/Los_Angeles")
GOOGLE_BOOKS_HTML_VISIBLE_TEXT_VERSION = "google-books-html-visible-text-v1"
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
        pacer: ProviderRequestPacer | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        bounds = provider_bounds(
            maximum_bytes,
            timeout_seconds,
            attempts_per_request=_MAX_ATTEMPTS_PER_QUERY,
            attempts_per_observation=_MAX_ATTEMPTS_PER_OBSERVATION,
            aggregate_multiple=9,
            aggregate_ceiling_bytes=_MAX_AGGREGATE_RESPONSE_BYTES,
        )
        if api_key is not None and (
            type(api_key) is not str
            or not 1 <= len(api_key) <= 2048
            or any(not 0x21 <= ord(value) <= 0x7E for value in api_key)
        ):
            raise ValueError("provider credential is invalid")
        self._name = name
        self._client = client
        self._api_key = api_key
        self._session = BoundedProviderSession(
            name,
            client,
            bounds,
            _DiscoveryRequestLedger("google_books", "volumes-v1"),
            pacer=pacer,
            clocks=ProviderClocks(clock, wall_clock, sleep, jitter),
        )
        self._called = False
        self._schema_rejections = 0

    @property
    def attempt_records(self) -> tuple[ProviderAttemptRecord, ...]:
        return self._session.attempt_records

    @property
    def incomplete_causes(self) -> tuple[ProviderIncompleteCauseRecord, ...]:
        return self._session.incomplete_causes

    @property
    def accessed_at(self) -> str | None:
        return self._session.accessed_at

    # The two budgets below are the session's own state.  They stay readable
    # and settable under their adapter-local names so a caller can position an
    # observation at a bound without reaching past the adapter.
    @property
    def _attempt_count(self) -> int:
        return self._session.attempt_count

    @_attempt_count.setter
    def _attempt_count(self, value: int) -> None:
        self._session.attempt_count = value

    @property
    def _remaining_response_bytes(self) -> int:
        return self._session.remaining_response_bytes

    @_remaining_response_bytes.setter
    def _remaining_response_bytes(self, value: int) -> None:
        self._session.remaining_response_bytes = value

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
            ledger.register(
                kind=plan.kind.value,
                matched_family=plan.matched_family,
                title=plan.title,
                authors=plan.authors,
                projection=_FIELDS,
            )
            self._session.ledger = ledger
            try:
                value, _ = ledger.resolve(
                    kind=plan.kind.value,
                    matched_family=plan.matched_family,
                    title=plan.title,
                    authors=plan.authors,
                    projection=_FIELDS,
                    execute=lambda: self._observe_lookup(plan, deadline=deadline),
                )
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
            ledger.register(
                kind=lane.kind.value,
                matched_family=lane.matched_family,
                title=lane.title,
                authors=lane.authors,
                projection=_FIELDS,
            )
        self._session.ledger = ledger
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
                    execute=lambda: self._observe_lookup(lookup, deadline=deadline),  # noqa: B023 - `ledger.resolve` calls `execute` before it returns, so the lambda only ever sees this lane's lookup
                )
                observation = qualify_provider_observation(physical, lookup)
                results.append(
                    ProviderLaneResult(
                        lane,
                        observation.status,
                        observation.candidates,
                        observation.accessed_at,
                        dispatch_lineage=observation.dispatch_lineage,
                        transformation_lineage=observation.transformation_lineage,
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
                    return self._result(lookup, ProviderStatus.INSUFFICIENT_INPUT)
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
            work_queries = work_query_ladder(lookup.title, lookup.authors)
            if not work_queries:
                return self._result(lookup, ProviderStatus.INSUFFICIENT_INPUT)
        else:
            return self._result(lookup, ProviderStatus.INSUFFICIENT_INPUT)
        if lookup.kind is ProviderQueryKind.WORK:
            last = self._result(lookup, ProviderStatus.NO_MATCH)
            for work_query in work_queries:
                last = self._observe_work_query(
                    lookup,
                    work_query,
                    local_claims,
                    deadline=deadline,
                )
                if last.status is ProviderStatus.CANDIDATES:
                    return last
                if last.status is ProviderStatus.CANDIDATE_LIMIT_EXCEEDED:
                    return self._observe_work_query(
                        lookup,
                        narrowing_work_query(work_query),
                        local_claims,
                        deadline=deadline,
                        query_retry=True,
                    )
                if last.status is not ProviderStatus.NO_MATCH:
                    return last
            return last
        try:
            payload = self._request(query, deadline=deadline)
            candidates = _candidates(
                self._name,
                payload,
                local_claims,
                requested_family=family,
                schema_rejections=lambda count: self._reject_schema_members(count),
            )
        except ProviderFailure as error:
            return self._result(lookup, error.status, references=references)
        except (
            EvidenceValueError,
            KeyError,
            TypeError,
            ValueError,
            UnicodeError,
            RecursionError,
        ):
            return self._result(lookup, ProviderStatus.MALFORMED, references=references)
        if len(candidates) > _MAX_RESULTS:
            return self._result(
                lookup,
                ProviderStatus.CANDIDATE_LIMIT_EXCEEDED,
                references=references,
            )
        if not candidates:
            return self._result(lookup, ProviderStatus.NO_MATCH, references=references)
        return self._result(
            lookup,
            ProviderStatus.CANDIDATES,
            candidates,
            references=references,
        )

    def _observe_work_query(
        self,
        lookup: MetadataLookup,
        work_query: WorkQuery,
        local_claims: tuple[LocalClaim, ...],
        *,
        deadline: float | None,
        query_retry: bool = False,
    ) -> ProviderObservation:
        query = _google_books_work_query(work_query)
        try:
            payload = self._request(
                query,
                deadline=deadline,
                query_retry=query_retry
                or work_query.rung is not WorkDispatchRung.NORMALIZED
                or work_query.candidate_limit_narrowing_retry,
            )
            candidates = _candidates(
                self._name,
                payload,
                local_claims,
                requested_family=None,
                schema_rejections=lambda count: self._reject_schema_members(count),
            )
        except ProviderFailure as error:
            return self._result(lookup, error.status)
        except (
            EvidenceValueError,
            KeyError,
            TypeError,
            ValueError,
            UnicodeError,
            RecursionError,
        ):
            return self._result(lookup, ProviderStatus.MALFORMED)
        if len(candidates) > _MAX_RESULTS:
            return self._result(lookup, ProviderStatus.CANDIDATE_LIMIT_EXCEEDED)
        if not candidates:
            return self._result(lookup, ProviderStatus.NO_MATCH)
        return self._result(
            lookup,
            ProviderStatus.CANDIDATES,
            candidates,
            dispatch_lineage=WorkDispatchLineage.from_query(work_query),
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
        dispatch_lineage: WorkDispatchLineage | None = None,
    ) -> ProviderObservation:
        return ProviderObservation(
            self._name,
            status,
            references,
            candidates,
            lookup.kind,
            "google_books",
            accessed_at=self._session.accessed_at,
            diagnostics=self._schema_diagnostics(),
            dispatch_lineage=dispatch_lineage,
        )

    def _request(
        self,
        query: str,
        *,
        deadline: float | None,
        query_retry: bool = False,
    ) -> dict[str, Any]:
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
        return self._session.request_json(
            GOOGLE_BOOKS_URL,
            params=params,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
            deadline=deadline,
            query_retry=query_retry,
            classify=_classify,
        )


def _classify(facts: ProviderResponseFacts) -> ProviderFailure:
    """Read Google's own status and reason vocabulary off one response."""
    return _provider_error(facts.status_code, facts.payload, now=facts.now)


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
            value for value in identifier_value if value.isdigit() or value in "Xx"
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
    description = info.get("description")
    transformed_description = _visible_description(description)
    description_transformed = (
        type(description) is str
        and ("<" in description or ">" in description)
        and type(transformed_description) is str
    )
    if description_transformed:
        add(ClaimKind.DESCRIPTION, transformed_description, ClaimScope.WORK)
    elif transformed_description is _DESCRIPTION_OVERSIZED:
        add(
            ClaimKind.DESCRIPTION,
            "x" * (MAX_DESCRIPTION_CHARACTERS + 1),
            ClaimScope.WORK,
        )
    elif type(description) is str and ("<" in description or ">" in description):
        # Do not turn hostile markup into an apparent absence.
        add(
            ClaimKind.DESCRIPTION,
            description if len(description) > MAX_DESCRIPTION_CHARACTERS else object(),
            ClaimScope.WORK,
        )
    else:
        add(ClaimKind.DESCRIPTION, description, ClaimScope.WORK)
    add(ClaimKind.LANGUAGE, info.get("language"), ClaimScope.EDITION)

    evidence = [
        ProviderEvidence(
            ProviderEvidenceKind.EDITION_WORK_RELATION,
            "unresolved",
            ClaimScope.EDITION,
        )
    ]
    if description_transformed:
        evidence.append(
            ProviderEvidence(
                ProviderEvidenceKind.DESCRIPTION_TRANSFORMATION,
                GOOGLE_BOOKS_HTML_VISIBLE_TEXT_VERSION,
                ClaimScope.WORK,
            )
        )
    for field, kind in (
        ("contentVersion", ProviderEvidenceKind.REVISION),
        ("publishedDate", ProviderEvidenceKind.PUBLICATION_DATE_TEXT),
    ):
        if info.get(field) is not None:
            try:
                evidence.append(
                    ProviderEvidence(kind, _text(info[field]), ClaimScope.EDITION)
                )
            except (ValueError, EvidenceValueError):
                schema_rejections(1)
    print_type = info.get("printType")
    if print_type is not None:
        try:
            # Google documents BOOK, MAGAZINE and ALL; only BOOK is usable as
            # physical-format evidence for this edition-level observation.
            normalized_print_type = _text(print_type)
            if normalized_print_type == "BOOK":
                evidence.append(
                    ProviderEvidence(
                        ProviderEvidenceKind.PHYSICAL_FORMAT,
                        normalized_print_type,
                        ClaimScope.EDITION,
                    )
                )
            elif normalized_print_type not in {"MAGAZINE", "ALL"}:
                schema_rejections(1)
        except (ValueError, EvidenceValueError):
            schema_rejections(1)
    pages = info.get("pageCount")
    if pages is not None:
        if (
            type(pages) is not int
            or isinstance(pages, bool)
            or not 1 <= pages <= 100_000
        ):
            schema_rejections(1)
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
            (
                replace(value, source_ordinal=index)
                for index, value in enumerate(evidence)
            ),
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
    return finalize_candidate_comparisons(
        ProviderCandidate(
            identity,
            "unresolved",
            tuple(claims),
            canonical_evidence,
        ),
        local_claims,
        provider,
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
            # A rejected collection is one field-level diagnostic.  Its
            # members are deliberately not inspected or retained.
            schema_rejections(1)
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


def _field(kind: ClaimKind, value: object) -> tuple[FieldState, str | None]:
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


def _provider_error(status_code: int, payload: bytes, *, now: float) -> ProviderFailure:
    if status_code == 401:
        return ProviderFailure(ProviderStatus.AUTHENTICATION, category="http_401")
    reasons = _error_reasons(payload)
    daily = reasons & {"dailyLimitExceeded", "dailyLimitExceededUnreg"}
    burst = reasons & {"rateLimitExceeded", "userRateLimitExceeded"}
    if daily and not burst and reasons <= daily:
        local = datetime.fromtimestamp(now, tz=_PACIFIC)
        reset = datetime.combine(
            local.date() + timedelta(days=1), datetime_time.min, tzinfo=_PACIFIC
        )
        return ProviderFailure(
            ProviderStatus.RATE_LIMITED,
            category="quota_exhausted",
            retry_after=max(1, math.ceil(reset.timestamp() - now)),
        )
    if burst and not daily and reasons <= burst:
        return ProviderFailure(
            ProviderStatus.RATE_LIMITED,
            category="burst_throttled",
            retry_after=_BURST_WINDOW_SECONDS,
        )
    if status_code == 429 or reasons & {
        "dailyLimitExceeded",
        "dailyLimitExceededUnreg",
        "rateLimitExceeded",
        "userRateLimitExceeded",
        "quotaExceeded",
    }:
        return ProviderFailure(ProviderStatus.RATE_LIMITED, category="http_429")
    if reasons & {"keyInvalid", "ipRefererBlocked"}:
        return ProviderFailure(ProviderStatus.AUTHENTICATION, category="http_403")
    if status_code == 403:
        return ProviderFailure(ProviderStatus.AUTHORIZATION, category="http_403")
    return ProviderFailure(ProviderStatus.UNAVAILABLE, category=f"http_{status_code}")


def _error_reasons(payload: bytes) -> frozenset[str]:
    try:
        value = decode_json(payload)
        return frozenset(
            cast(str, item.get("reason"))
            for item in value.get("error", {}).get("errors", [])  # type: ignore[attr-defined]  # reason: the decoded payload is deliberately duck-typed here; a non-mapping body raises AttributeError, which this try block already catches.
            if type(item) is dict and type(item.get("reason")) is str
        )
    except (AttributeError, TypeError, ValueError, UnicodeError, RecursionError):
        return frozenset()


def _google_books_work_query(value: WorkQuery) -> str:
    """Render the documented Google Books q grammar without changing identity."""
    title = value.title.replace('"', " ")
    authors = tuple(author.replace('"', " ") for author in value.authors)
    if value.candidate_limit_narrowing_retry:
        return f'intitle:"{title}" inauthor:"{" ".join(authors)}"'
    return " ".join(
        [f'intitle:"{title}"'] + [f'inauthor:"{author}"' for author in authors]
    )


GoogleBooksPacer = ProviderRequestPacer


__all__ = ["GOOGLE_BOOKS_URL", "GoogleBooksMetadataProvider", "GoogleBooksPacer"]
