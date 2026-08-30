"""Bounded, report-only iTunes Search audiobook observation.

iTunes Search is ADR-0061's corroboration-only surface.  It answers one
free-text query against the audiobook media type and states a collection, an
artist and a description -- and nothing about who read the recording or how
long it runs.  That blindness is the point: an identity iTunes discovered can
never satisfy the runtime band, so this adapter can corroborate a title and an
author but can never carry an admission on its own.

The store answers `text/javascript` rather than `application/json`, which is
the one media type this adapter admits beyond the default.  Everything else
about the response is read exactly as every other adapter reads it.
"""

from __future__ import annotations

import random
import time
from dataclasses import replace
from typing import Any, Callable

import httpx

from mechanical_librarian.adapters.providers.retry_safety import (
    ProviderAttemptRecord,
    ProviderFailure,
    ProviderIncompleteCauseRecord,
    ProviderRequestPacer,
    _DiscoveryRequestLedger,
    bounded_retry_after,
)
from mechanical_librarian.adapters.providers.sdk import (
    BoundedProviderSession,
    ProviderClocks,
    ProviderResponseFacts,
    provider_bounds,
)
from mechanical_librarian.adapters.providers.visible_text import (
    HTML_VISIBLE_TEXT_VERSION,
    projected_description,
)
from mechanical_librarian.application.metadata_report import (
    MetadataDiscoveryLane,
    MetadataDiscoveryPlan,
    MetadataLookup,
    ProviderLaneResult,
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
    claim_scope,
    comparison_links,
    finalize_candidate_comparisons,
)
from mechanical_librarian.domain.work_identity import (
    WorkDispatchLineage,
    WorkDispatchRung,
    WorkQuery,
    work_query_ladder,
)

ITUNES_SEARCH_ORIGIN = "https://itunes.apple.com"
# The one origin and the one path this adapter may reach.  No provider value
# ever becomes part of a URL: the query travels entirely in `term`.
ITUNES_SEARCH_URL = ITUNES_SEARCH_ORIGIN + "/search"
# The store answers this media type on every successful search.  It is admitted
# by name rather than by falling back, so a response outside the pair is still
# `MALFORMED`.
ITUNES_CONTENT_TYPES = ("application/json", "text/javascript")
_MEDIA = "audiobook"
_MAX_RESULTS = 5
_MAX_RETURNED_RESULTS = _MAX_RESULTS + 1
# One search page of six audiobook records is around 15 KB, so nine of the
# caller's per-response ceiling is a whole observation's aggregate.
_MAX_AGGREGATE_RESPONSE_BYTES = 1_048_576
_MAX_ATTEMPTS_PER_QUERY = 3
# One work lane walking a ladder of at most three queries, plus retries.
_MAX_ATTEMPTS_PER_OBSERVATION = 12
_SEARCH_TOP_LEVEL = frozenset({"resultCount", "results"})
_RESULT_KEYS = frozenset(
    {
        "artistId",
        "artistName",
        "artistViewUrl",
        "artworkUrl60",
        "artworkUrl100",
        "collectionCensoredName",
        "collectionExplicitness",
        "collectionId",
        "collectionName",
        "collectionPrice",
        "collectionViewUrl",
        "copyright",
        "country",
        "currency",
        "description",
        "previewUrl",
        "primaryGenreName",
        "releaseDate",
        "trackCount",
        "wrapperType",
    }
)
# The identifier the store itself names one audiobook collection by.  It is the
# candidate's identity, which is the only surface a report retains a provider's
# native identifier on.
_MAX_COLLECTION_ID = 9_999_999_999_999


class ITunesSearchMetadataProvider:
    """Observe iTunes audiobook search results, and nothing structural."""

    def __init__(
        self,
        name: str,
        client: httpx.Client,
        maximum_bytes: int,
        timeout_seconds: float = 5.0,
        *,
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
        self._name = name
        self._client = client
        self._session = BoundedProviderSession(
            name,
            client,
            bounds,
            _DiscoveryRequestLedger("itunes_search", "search-1"),
            pacer=pacer,
            clocks=ProviderClocks(clock, wall_clock, sleep, jitter),
            accept_content_types=ITUNES_CONTENT_TYPES,
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
        plan: MetadataDiscoveryPlan,
        *,
        deadline: float | None = None,
    ) -> ProviderObservation:
        self._schema_rejections = 0
        if self._called:
            return ProviderObservation(
                self._name,
                ProviderStatus.UNAVAILABLE,
                authority="itunes_search",
            )
        self._called = True
        if type(plan) is not MetadataDiscoveryPlan:
            raise ValueError("provider lookup is invalid")
        if plan.overflow:
            self._client.close()
            return ProviderObservation(
                self._name,
                ProviderStatus.QUERY_LIMIT_EXCEEDED,
                authority="itunes_search",
            )
        lanes = tuple(
            lane for lane in plan.lanes if lane.kind is ProviderQueryKind.WORK
        )
        if not lanes:
            self._client.close()
            # The store searches; it resolves no identifier. A plan built
            # entirely from ASIN lanes names nothing iTunes can be asked, which
            # is the same shape Audnexus states for a plan with no ASIN lane.
            return ProviderObservation(
                self._name,
                ProviderStatus.INSUFFICIENT_INPUT,
                authority="itunes_search",
            )
        ledger = _DiscoveryRequestLedger("itunes_search", "search-1")
        for lane in lanes:
            ledger.register(
                kind=lane.kind.value,
                matched_family=lane.matched_family,
                title=lane.title,
                authors=lane.authors,
                projection=_MEDIA,
            )
        self._session.ledger = ledger
        results: list[ProviderLaneResult] = []
        try:
            for lane in lanes:
                lookup = _lane_lookup(lane, plan.local_claims)
                physical = self._resolve(ledger, lane, lookup, deadline=deadline)
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
            authority="itunes_search",
            diagnostics=self._schema_diagnostics(),
            request_accounting=ledger.accounting(),
        )

    def _resolve(
        self,
        ledger: _DiscoveryRequestLedger,
        lane: MetadataDiscoveryLane,
        lookup: MetadataLookup,
        *,
        deadline: float | None,
    ) -> ProviderObservation:
        """Dispatch one lane's work through the ledger's replay boundary.

        The dispatch lives outside the lane loop so the closure below binds this
        one lane rather than whichever lane the loop reached last.
        """
        physical, _replayed = ledger.resolve(
            kind=lane.kind.value,
            matched_family=lane.matched_family,
            title=lane.title,
            authors=lane.authors,
            projection=_MEDIA,
            execute=lambda: self._observe_work(lookup, deadline=deadline),
        )
        return physical

    def _observe_work(
        self,
        lookup: MetadataLookup,
        *,
        deadline: float | None,
    ) -> ProviderObservation:
        if lookup.title is None or not lookup.authors:
            return self._result(lookup, ProviderStatus.INSUFFICIENT_INPUT)
        queries = work_query_ladder(lookup.title, lookup.authors)
        if not queries:
            return self._result(lookup, ProviderStatus.INSUFFICIENT_INPUT)
        last = self._result(lookup, ProviderStatus.NO_MATCH)
        for query in queries:
            last = self._observe_work_query(lookup, query, deadline=deadline)
            if last.status is not ProviderStatus.NO_MATCH:
                return last
        return last

    def _observe_work_query(
        self,
        lookup: MetadataLookup,
        query: WorkQuery,
        *,
        deadline: float | None,
    ) -> ProviderObservation:
        try:
            payload = self._request(
                {
                    "limit": str(_MAX_RETURNED_RESULTS),
                    "media": _MEDIA,
                    "term": _search_term(query),
                },
                deadline=deadline,
                query_retry=query.rung is not WorkDispatchRung.NORMALIZED
                or query.candidate_limit_narrowing_retry,
            )
            candidates = _candidates(
                self._name,
                payload,
                lookup.local_claims,
                schema_rejections=self._reject_schema_members,
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
            dispatch_lineage=WorkDispatchLineage.from_query(query),
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
        dispatch_lineage: WorkDispatchLineage | None = None,
    ) -> ProviderObservation:
        return ProviderObservation(
            self._name,
            status,
            (),
            candidates,
            lookup.kind,
            "itunes_search",
            accessed_at=self._session.accessed_at,
            diagnostics=self._schema_diagnostics(),
            dispatch_lineage=dispatch_lineage,
        )

    def _request(
        self,
        params: dict[str, str],
        *,
        deadline: float | None,
        query_retry: bool = False,
    ) -> dict[str, Any]:
        return self._session.request_json(
            ITUNES_SEARCH_URL,
            params=params,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
            deadline=deadline,
            query_retry=query_retry,
            classify=_classify,
        )


def _lane_lookup(
    lane: MetadataDiscoveryLane, local_claims: tuple[LocalClaim, ...]
) -> MetadataLookup:
    return MetadataLookup(lane.kind, local_claims, lane.title, lane.authors)


def _search_term(query: WorkQuery) -> str:
    """Render one ladder rung as the single free-text field iTunes accepts."""
    return " ".join((query.title, *query.authors))


def _classify(facts: ProviderResponseFacts) -> ProviderFailure | None:
    """Read the store's status vocabulary off one response.

    429 is stated explicitly because the session treats an unrecognised status
    as terminal, and a throttled adapter that never retries reads as an outage.
    Only the `Retry-After` value is projected out of the headers.
    """
    if facts.status_code == 429:
        return ProviderFailure(
            ProviderStatus.RATE_LIMITED,
            category="http_429",
            retryable=True,
            retry_after=bounded_retry_after(
                facts.headers.get("retry-after"), now=facts.now
            ),
        )
    if facts.status_code == 404:
        return ProviderFailure(ProviderStatus.NO_MATCH, category="http_404")
    if facts.status_code == 401:
        return ProviderFailure(ProviderStatus.AUTHENTICATION, category="http_401")
    if facts.status_code == 403:
        return ProviderFailure(ProviderStatus.AUTHORIZATION, category="http_403")
    return None


def _candidates(
    provider: str,
    payload: dict[str, Any],
    local_claims: tuple[LocalClaim, ...],
    *,
    schema_rejections: Callable[[int], None],
) -> tuple[ProviderCandidate, ...]:
    schema_rejections(len(set(payload) - _SEARCH_TOP_LEVEL))
    results = payload.get("results", [])
    if type(results) is not list or len(results) > _MAX_RETURNED_RESULTS:
        raise ValueError("provider candidate list is malformed")
    admitted = [
        _candidate(
            provider,
            result,
            local_claims,
            schema_rejections=schema_rejections,
        )
        for result in results
    ]
    identities = [value.identity for value in admitted]
    if len(set(identities)) != len(identities):
        raise ValueError("provider candidates are duplicated")
    return tuple(sorted(admitted, key=lambda value: value.identity))


def _candidate(
    provider: str,
    result: object,
    local_claims: tuple[LocalClaim, ...],
    *,
    schema_rejections: Callable[[int], None],
) -> ProviderCandidate:
    if type(result) is not dict:
        raise ValueError("provider result is malformed")
    schema_rejections(len(set(result) - _RESULT_KEYS))
    # Required identity.  The store's collection identifier is the only stable
    # name it gives one audiobook, so a result without one is not a candidate.
    collection = result.get("collectionId")
    if (
        type(collection) is not int
        or isinstance(collection, bool)
        or not 0 < collection <= _MAX_COLLECTION_ID
    ):
        raise ValueError("provider result identity is malformed")
    identity = "itunes-collection-" + str(collection)

    claims: list[ProviderClaim] = []

    def add(kind: ClaimKind, value: object, *, role: str | None = None) -> None:
        state, exact = _field(kind, value)
        scope = claim_scope(kind)
        bare = ProviderClaim.create(
            provider=provider,
            candidate=identity,
            kind=kind,
            value=exact,
            state=state,
            scope=scope,
            applicability="not_applicable",
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
                applicability="not_applicable",
                role=role,
                raw_order=len(claims),
                links=comparison_links(bare, local_claims),
            )
        )

    # Title, author, description.  Nothing else: the store states no narrator
    # and no running time, so this adapter can state neither either. That is
    # what makes an iTunes-discovered identity unable to satisfy the runtime
    # band on its own, which is exactly the posture ADR-0061 gives it.
    add(ClaimKind.TITLE, result.get("collectionName"))
    artist = result.get("artistName")
    add(ClaimKind.CREATOR, artist if type(artist) is str else None, role="author")
    description, transformed = projected_description(result.get("description"))
    add(ClaimKind.DESCRIPTION, description)

    evidence = [
        ProviderEvidence(
            ProviderEvidenceKind.EDITION_WORK_RELATION,
            "unresolved",
            ClaimScope.EDITION,
        )
    ]
    if transformed:
        evidence.append(
            ProviderEvidence(
                ProviderEvidenceKind.DESCRIPTION_TRANSFORMATION,
                HTML_VISIBLE_TEXT_VERSION,
                ClaimScope.WORK,
            )
        )
    return finalize_candidate_comparisons(
        ProviderCandidate(
            identity,
            "unresolved",
            tuple(claims),
            _canonical_evidence(evidence),
        ),
        local_claims,
        provider,
    )


def _canonical_evidence(
    evidence: list[ProviderEvidence],
) -> tuple[ProviderEvidence, ...]:
    """Order one candidate's evidence exactly as the report document requires."""
    ordered = tuple(
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
                "" if value.date_event is None else value.date_event.value,
                "" if value.date_precision is None else value.date_precision.value,
                value.date_normalized or "",
                value.taxonomy_type or "",
                "" if value.date_confidence is None else value.date_confidence.value,
                value.source_ordinal,
            ),
        )
    )
    if len(ordered) > MAX_PROVIDER_EVIDENCE:
        raise ValueError("provider rich evidence exceeds its bound")
    return ordered


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


ITunesSearchPacer = ProviderRequestPacer


__all__ = [
    "ITUNES_CONTENT_TYPES",
    "ITUNES_SEARCH_ORIGIN",
    "ITUNES_SEARCH_URL",
    "ITunesSearchMetadataProvider",
    "ITunesSearchPacer",
]
