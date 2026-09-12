"""Bounded, report-only Audnexus per-ASIN enrichment observation.

Audnexus is ADR-0061's enrichment layer: it answers about one product, never
about a search, so this adapter observes ASIN lanes and states
`INSUFFICIENT_INPUT` for anything else.  Its `REGION_UNAVAILABLE` error is the
one structured signal that an ASIN is simply scoped to a different marketplace,
so it advances the region walk instead of ending it.
"""

from __future__ import annotations

import random
import time
from dataclasses import replace
from typing import Any, Callable

import httpx

from mechanical_librarian.adapters.providers.retry_safety import (
    ProviderFailure,
    ProviderRequestPacer,
    _DiscoveryRequestLedger,
    bounded_retry_after,
)
from mechanical_librarian.adapters.providers.sdk import (
    BoundedProviderSession,
    ProviderClocks,
    ProviderResponseFacts,
    decode_json,
    provider_bounds,
    provider_client_lifetime,
)
from mechanical_librarian.adapters.providers.visible_text import (
    HTML_VISIBLE_TEXT_VERSION,
    projected_description,
)
from mechanical_librarian.application.metadata_report import (
    MetadataDiscoveryLane,
    MetadataDiscoveryPlan,
    MetadataLookup,
    ProviderFailureSnapshot,
    ProviderLaneResult,
    ProviderResult,
    provider_observation,
    qualify_provider_observation,
)
from mechanical_librarian.application.provider_evidence import (
    ProviderOperationalEvidence,
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
    AUDIOBOOK_MARKETPLACES,
    MAX_DESCRIPTION_CHARACTERS,
    MAX_PROVIDER_CHAPTER_DURATIONS,
    MAX_PROVIDER_EVIDENCE,
    MAX_SCALAR_CHARACTERS,
    ClaimKind,
    ClaimScope,
    FieldState,
    LocalClaim,
    ProviderCandidate,
    ProviderChapterStructure,
    ProviderClaim,
    ProviderEvidence,
    ProviderEvidenceKind,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    canonical_asin,
    claim_scope,
    comparison_links,
    finalize_candidate_comparisons,
)

AUDNEXUS_ORIGIN = "https://api.audnex.us"
# The one origin this adapter may reach, and the two paths it may build under
# it. Every URL below is constructed here from a canonical ASIN, so no provider
# value ever becomes part of a host.
AUDNEXUS_BOOKS_URL = AUDNEXUS_ORIGIN + "/books"
_MAX_VALUES = 8
# A book record is around 3.5 KB and a chapter document around 3 KB, so nine of
# the caller's per-response ceiling is a whole observation's aggregate.
_MAX_AGGREGATE_RESPONSE_BYTES = 1_048_576
_MAX_ATTEMPTS_PER_QUERY = 3
# Four ASIN lanes, each walking a marketplace order and then asking once for
# chapters, plus retries.
_MAX_ATTEMPTS_PER_OBSERVATION = 20
_MAX_RUNTIME_SECONDS = 604_800
_BOOK_KEYS = frozenset(
    {
        "asin",
        "authors",
        "copyright",
        "description",
        "formatType",
        "genres",
        "image",
        "isAdult",
        "isbn",
        "language",
        "literatureType",
        "narrators",
        "publisherName",
        "rating",
        "region",
        "releaseDate",
        "runtimeLengthMin",
        "seriesPrimary",
        "seriesSecondary",
        "subtitle",
        "summary",
        "title",
    }
)
# The region query parameter names a non-default marketplace. `us` is the
# provider's own default and is deliberately sent without one, so a US lookup
# stays byte-identical to the anonymous request the provider documents.
_DEFAULT_REGION = "us"
_REGION_UNAVAILABLE = "REGION_UNAVAILABLE"
_UNPROJECTABLE = object()


class AudnexusMetadataProvider:
    """Observe one Audnexus book, and its chapter vector, per ASIN."""

    def __init__(
        self,
        name: str,
        client: httpx.Client,
        maximum_bytes: int,
        timeout_seconds: float = 5.0,
        *,
        marketplaces: tuple[str, ...] = (_DEFAULT_REGION,),
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
        if (
            type(marketplaces) is not tuple
            or not marketplaces
            or len(marketplaces) > len(AUDIOBOOK_MARKETPLACES)
            or len(set(marketplaces)) != len(marketplaces)
            or any(value not in AUDIOBOOK_MARKETPLACES for value in marketplaces)
        ):
            raise ValueError("provider marketplaces are invalid")
        self._name = name
        self._client = client
        self._marketplaces = marketplaces
        self._session = BoundedProviderSession(
            name,
            client,
            bounds,
            _DiscoveryRequestLedger("audnexus", "books-v1"),
            pacer=pacer,
            clocks=ProviderClocks(clock, wall_clock, sleep, jitter),
        )
        self._called = False
        self._schema_rejections = 0

    @property
    def marketplaces(self) -> tuple[str, ...]:
        return self._marketplaces

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
    ) -> ProviderResult:
        failure: BaseException | None = None
        try:
            observation = self._observe(plan, deadline=deadline)
        except BaseException as error:
            failure = error
        try:
            snapshot = ProviderFailureSnapshot(
                (),
                ProviderOperationalEvidence(
                    self._session.accessed_at,
                    self._session.attempt_records,
                    self._session.incomplete_causes,
                ),
            )
        except BaseException:
            if failure is not None:
                raise failure from None
            raise
        if failure is not None:
            snapshot.raise_from(failure)
        return ProviderResult(
            observation, snapshot.cover_descriptors, snapshot.evidence
        )

    def _observe(
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
                authority="audnexus",
            )
        self._called = True
        if type(plan) is not MetadataDiscoveryPlan:
            raise ValueError("provider lookup is invalid")
        if plan.overflow:
            self._client.close()
            return ProviderObservation(
                self._name,
                ProviderStatus.QUERY_LIMIT_EXCEEDED,
                authority="audnexus",
            )
        lanes = tuple(
            lane
            for lane in plan.lanes
            if lane.kind is ProviderQueryKind.ASIN and lane.canonical_asin is not None
        )
        if not lanes:
            self._client.close()
            # Audnexus answers about a product, never about a search, so a plan
            # with no ASIN lane names nothing this provider can be asked.
            return ProviderObservation(
                self._name,
                ProviderStatus.INSUFFICIENT_INPUT,
                authority="audnexus",
            )
        ledger = _DiscoveryRequestLedger("audnexus", "books-v1")
        for lane in lanes:
            ledger.register_detail(_lane_asin(lane))
        self._session.ledger = ledger
        results: list[ProviderLaneResult] = []
        with provider_client_lifetime(self._client):
            for lane in lanes:
                lookup = _lane_lookup(lane, plan)
                physical = self._resolve(ledger, lane, lookup, deadline=deadline)
                observation = qualify_provider_observation(physical, lookup)
                results.append(
                    ProviderLaneResult(
                        lane,
                        observation.status,
                        observation.candidates,
                        observation.accessed_at,
                        recording_lookup_candidates=observation.recording_lookup_candidates,
                    )
                )
        return provider_observation(
            self._name,
            tuple(results),
            authority="audnexus",
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
        physical, _replayed = ledger.resolve_detail(
            _lane_asin(lane),
            execute=lambda: self._observe_asin(lane, lookup, deadline=deadline),
        )
        return physical

    def _observe_asin(
        self,
        lane: MetadataDiscoveryLane,
        lookup: MetadataLookup,
        *,
        deadline: float | None,
    ) -> ProviderObservation:
        asin = _lane_asin(lane)
        for index, region in enumerate(self._marketplaces):
            try:
                payload = self._request(
                    AUDNEXUS_BOOKS_URL + "/" + asin,
                    region,
                    deadline=deadline,
                    # One ASIN is one logical lookup however many regions it
                    # takes to resolve, so every dispatch after the first is
                    # accounted as an extra dispatch rather than a new request.
                    extra_dispatch=index > 0,
                )
            except ProviderFailure as error:
                if error.status is ProviderStatus.NO_MATCH:
                    continue
                return self._result(lookup, error.status)
            try:
                candidate = _candidate(
                    self._name,
                    payload,
                    lookup.local_claims,
                    marketplace=region,
                    chapters=self._chapters(asin, region, deadline=deadline),
                    schema_rejections=self._reject_schema_members,
                )
            except (
                EvidenceValueError,
                KeyError,
                TypeError,
                ValueError,
                UnicodeError,
                RecursionError,
            ):
                return self._result(lookup, ProviderStatus.MALFORMED)
            return self._result(lookup, ProviderStatus.CANDIDATES, (candidate,))
        return self._result(lookup, ProviderStatus.NO_MATCH)

    def _chapters(
        self, asin: str, region: str, *, deadline: float | None
    ) -> ProviderChapterStructure | None:
        """Ask once for the recording's structure, and tolerate not getting it.

        The chapter vector is optional enrichment: a book that resolved is a
        candidate whether or not its structure is published, so a failure here
        removes one evidence value rather than the candidate.
        """
        try:
            payload = self._request(
                AUDNEXUS_BOOKS_URL + "/" + asin + "/chapters",
                region,
                deadline=deadline,
                extra_dispatch=True,
            )
        except ProviderFailure:
            return None
        try:
            return _chapter_structure(payload, self._reject_schema_members)
        except (TypeError, ValueError, UnicodeError, RecursionError):
            self._reject_schema_members(1)
            return None

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
    ) -> ProviderObservation:
        return ProviderObservation(
            self._name,
            status,
            (),
            candidates,
            lookup.kind,
            "audnexus",
            accessed_at=self._session.accessed_at,
            diagnostics=self._schema_diagnostics(),
        )

    def _request(
        self,
        url: str,
        region: str,
        *,
        deadline: float | None,
        extra_dispatch: bool = False,
    ) -> dict[str, Any]:
        return self._session.request_json(
            url,
            params=({} if region == _DEFAULT_REGION else {"region": region}),
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
            deadline=deadline,
            query_retry=extra_dispatch,
            classify=_classify,
        )


def _lane_asin(lane: MetadataDiscoveryLane) -> str:
    if lane.canonical_asin is None:
        raise ValueError("provider lookup is invalid")
    return lane.canonical_asin


def _lane_lookup(
    lane: MetadataDiscoveryLane, plan: MetadataDiscoveryPlan
) -> MetadataLookup:
    return MetadataLookup(
        lane.kind,
        plan.local_claims,
        lane.title if lane.kind is ProviderQueryKind.WORK else None,
        lane.authors if lane.kind is ProviderQueryKind.WORK else (),
        audio=plan.audio,
        work_title=plan.work_title,
        work_authors=plan.work_authors,
    )


def _classify(facts: ProviderResponseFacts) -> ProviderFailure | None:
    """Read Audnexus's own status and error-code vocabulary off one response.

    429 is named explicitly because the session treats an unrecognised status as
    terminal, and a throttled adapter that never retries reads as an outage.
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
        # An ASIN is marketplace-scoped, so "not in this region" is a fact
        # about the region rather than about the product.
        return ProviderFailure(
            ProviderStatus.NO_MATCH,
            category=(
                "region_unavailable"
                if _error_code(facts.payload) == _REGION_UNAVAILABLE
                else "http_404"
            ),
        )
    if facts.status_code == 401:
        return ProviderFailure(ProviderStatus.AUTHENTICATION, category="http_401")
    if facts.status_code == 403:
        return ProviderFailure(ProviderStatus.AUTHORIZATION, category="http_403")
    return None


def _error_code(payload: bytes) -> str | None:
    """Project one bounded error code out of a structured error body."""
    try:
        value = decode_json(payload)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        return None
    if type(value) is not dict:
        return None
    error = value.get("error")
    if type(error) is not dict:
        return None
    code = error.get("code")
    return code if type(code) is str and len(code) <= 64 else None


def _candidate(
    provider: str,
    payload: dict[str, Any],
    local_claims: tuple[LocalClaim, ...],
    *,
    marketplace: str,
    chapters: ProviderChapterStructure | None,
    schema_rejections: Callable[[int], None],
) -> ProviderCandidate:
    schema_rejections(len(set(payload) - _BOOK_KEYS))
    # Required identity. A book without a canonical ASIN names nothing that can
    # be joined or resolved again.
    asin = canonical_asin(payload.get("asin"))
    if asin is None:
        raise ValueError("provider book identity is malformed")
    identity = "audnexus-book-" + asin

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

    add(ClaimKind.ASIN, asin)
    add(ClaimKind.TITLE, payload.get("title"))
    add(ClaimKind.SUBTITLE, payload.get("subtitle"))
    authors = _names(payload.get("authors"), schema_rejections)
    for author in authors:
        add(ClaimKind.CREATOR, author, role="author")
    if not authors:
        add(ClaimKind.CREATOR, None, role="author")
    narrators = _names(payload.get("narrators"), schema_rejections)
    for narrator in narrators:
        add(ClaimKind.CONTRIBUTOR, narrator, role="narrator")
    if not narrators:
        add(ClaimKind.CONTRIBUTOR, None, role="narrator")
    series, sequence = _series(payload.get("seriesPrimary"), schema_rejections)
    add(ClaimKind.SERIES, series)
    add(ClaimKind.SERIES_SEQUENCE, sequence)
    # Audnexus publishes a plain `description` and an HTML `summary` of the
    # same text. The plain one is preferred; the summary is read only when the
    # provider states no plain one, and the shared renderer makes it visible
    # text rather than a value the report cannot project.
    description, description_transformed = projected_description(
        payload.get("description"), payload.get("summary")
    )
    add(ClaimKind.DESCRIPTION, description)
    add(ClaimKind.LANGUAGE, payload.get("language"))
    add(
        ClaimKind.DURATION,
        _duration(payload.get("runtimeLengthMin"), schema_rejections),
    )

    evidence = [
        ProviderEvidence(
            ProviderEvidenceKind.EDITION_WORK_RELATION,
            "unresolved",
            ClaimScope.EDITION,
        ),
        ProviderEvidence(
            ProviderEvidenceKind.ASIN_MARKETPLACE,
            marketplace,
            ClaimScope.EDITION,
        ),
    ]
    if description_transformed:
        evidence.append(
            ProviderEvidence(
                ProviderEvidenceKind.DESCRIPTION_TRANSFORMATION,
                HTML_VISIBLE_TEXT_VERSION,
                ClaimScope.WORK,
            )
        )
    if chapters is not None:
        evidence.append(
            ProviderEvidence(
                ProviderEvidenceKind.CHAPTER_STRUCTURE,
                str(chapters.runtime_seconds),
                ClaimScope.EDITION,
                chapters=chapters,
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


def _chapter_structure(
    payload: dict[str, Any], schema_rejections: Callable[[int], None]
) -> ProviderChapterStructure | None:
    """Read one bounded whole-second chapter vector, or nothing at all."""
    chapters = payload.get("chapters")
    if type(chapters) is not list:
        schema_rejections(1)
        return None
    durations: list[int] = []
    total = 0
    for chapter in chapters:
        if type(chapter) is not dict:
            return None
        length = chapter.get("lengthMs")
        if (
            type(length) is not int
            or isinstance(length, bool)
            or not 0 <= length <= _MAX_RUNTIME_SECONDS * 1000
        ):
            return None
        total += length
        if len(durations) < MAX_PROVIDER_CHAPTER_DURATIONS:
            durations.append(length // 1000)
    runtime = payload.get("runtimeLengthSec")
    if (
        type(runtime) is not int
        or isinstance(runtime, bool)
        or not 0 <= runtime <= _MAX_RUNTIME_SECONDS
    ):
        schema_rejections(1)
        runtime = min(total // 1000, _MAX_RUNTIME_SECONDS)
    truncated = len(chapters) > MAX_PROVIDER_CHAPTER_DURATIONS
    return ProviderChapterStructure(
        len(chapters),
        runtime,
        tuple(durations),
        truncated,
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


def _names(value: object, schema_rejections: Callable[[int], None]) -> tuple[str, ...]:
    if value is None:
        return ()
    if type(value) is not list or len(value) > _MAX_VALUES:
        schema_rejections(1)
        return ()
    names: list[str] = []
    for item in value:
        if type(item) is not dict:
            schema_rejections(1)
            continue
        try:
            names.append(_text(item.get("name")))
        except (ValueError, EvidenceValueError):
            schema_rejections(1)
    return tuple(names)


def _series(
    value: object, schema_rejections: Callable[[int], None]
) -> tuple[object, object]:
    if value is None:
        return None, None
    if type(value) is not dict:
        schema_rejections(1)
        return None, None
    name = value.get("name")
    if type(name) is not str:
        schema_rejections(1)
        return None, None
    position = value.get("position")
    if type(position) is int and not isinstance(position, bool):
        position = str(position)
    return name, position


def _duration(value: object, schema_rejections: Callable[[int], None]) -> object:
    if value is None:
        return None
    if type(value) is not int or isinstance(value, bool) or not 1 <= value <= 10_080:
        schema_rejections(1)
        return _UNPROJECTABLE
    return f"{value * 60}.000"


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


AudnexusPacer = ProviderRequestPacer


__all__ = [
    "AUDNEXUS_BOOKS_URL",
    "AUDNEXUS_ORIGIN",
    "AudnexusMetadataProvider",
    "AudnexusPacer",
]
