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
from urllib.parse import urlsplit

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
    provider_bounds,
    provider_client_lifetime,
)
from mechanical_librarian.adapters.providers.visible_text import (
    HTML_VISIBLE_TEXT_VERSION,
    projected_description,
)
from mechanical_librarian.application.failure_diagnostics import (
    merge_provider_diagnostics,
)
from mechanical_librarian.application.metadata_report import (
    MetadataDiscoveryLane,
    MetadataDiscoveryPlan,
    MetadataLookup,
    ProviderCoverDescriptor,
    ProviderFailureSnapshot,
    ProviderLaneResult,
    ProviderResult,
    QualifiedLaneObservation,
    merge_provider_cover_descriptors,
    observe_work_ladder,
    provider_observation,
)
from mechanical_librarian.application.provider_evidence import (
    ProviderOperationalEvidence,
)
from mechanical_librarian.domain.cover_evidence import (
    CoverDesignation,
    CoverProvenance,
    CoverSourceKind,
    CoverSourceState,
    cover_source_id,
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
    validate_observed_title,
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
# The store states a collection, never a work, so the candidate and its cover
# provenance carry the same unresolved work sentinel.
_UNRESOLVED_WORK = "unresolved"
# One page naming the same collection twice with claims that disagree states an
# ambiguous identity rather than a response this adapter could not read, so the
# lane says which of the two it was.
_IDENTITY_CONFLICT_DIAGNOSTIC = FailureDiagnostic(
    FailureCategory.IDENTITY,
    FailureComponent.PROVIDER,
    FailureStage.IDENTITY,
    retryable=False,
)


class _AmbiguousIdentity(ValueError):
    """One page states one collection identifier twice with different claims."""


# The artwork shards the store serves cover art from.  One capture names one of
# them, but the store balances a result across the set, so pinning the single
# observed shard would silently drop the art of every result answered by
# another.  The cover transport is address-pinned and follows no redirect, so
# this closed set is the whole origin contract for this provider's cover bytes.
ITUNES_ARTWORK_HOSTS: frozenset[str] = frozenset(
    f"is{shard}-ssl.mzstatic.com" for shard in range(1, 6)
)
_MAX_COVER_URL_CHARACTERS = 2048
# The store states a 100-pixel thumbnail and encodes the rendition it wants in
# the last path segment.  Substituting that one exact segment asks the same
# origin for the same asset at a legible size; anything that is not that exact
# segment is left alone, because a URL this adapter cannot read is a URL it may
# not rewrite.  This is Open Library's constructed fixed-origin cover URL in the
# one shape iTunes publishes.
_STATED_ARTWORK_SEGMENT = "/100x100bb.jpg"
_CONSTRUCTED_ARTWORK_SEGMENT = "/600x600bb.jpg"


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
        self._pacer = pacer
        self._cover_descriptors: tuple[ProviderCoverDescriptor, ...] = ()

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
                self._cover_descriptors,
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
        # Only the asserted work lane: an audiobook's alternative title is
        # never sent to the store, whose twelve-attempt budget it would share
        # (#1255; a second iTunes lane is #1254's question).
        lanes = tuple(
            lane
            for lane in plan.lanes
            if lane.kind is ProviderQueryKind.WORK and lane.title_basis is None
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
        descriptors: tuple[ProviderCoverDescriptor, ...] = ()
        conflicts: tuple[FailureDiagnostic, ...] = ()
        with provider_client_lifetime(self._client):
            for lane in lanes:
                self._cover_descriptors = ()
                lookup = _lane_lookup(lane, plan)
                qualified = self._resolve(ledger, lane, lookup, deadline=deadline)
                observation = qualified.observation
                # Schema rejections are counted across the whole observation and
                # stated once below; an ambiguous identity belongs to the one
                # lane that read the page, and is restated for the observation.
                identity = tuple(
                    value
                    for value in observation.diagnostics
                    if value.category is FailureCategory.IDENTITY
                )
                conflicts = merge_provider_diagnostics(conflicts, identity)
                results.append(
                    ProviderLaneResult(
                        lane,
                        observation.status,
                        observation.candidates,
                        observation.accessed_at,
                        recording_lookup_candidates=observation.recording_lookup_candidates,
                        diagnostics=merge_provider_diagnostics(
                            identity, qualified.diagnostics
                        ),
                        dispatch_lineage=observation.dispatch_lineage,
                        transformation_lineage=observation.transformation_lineage,
                        exhausted_dispatch=qualified.exhausted_dispatch,
                    )
                )
                merged = merge_provider_cover_descriptors(
                    descriptors,
                    _retained_cover_descriptors(self._cover_descriptors, observation),
                )
                if merged is None:
                    results[-1] = ProviderLaneResult(
                        lane,
                        ProviderStatus.MALFORMED,
                        accessed_at=observation.accessed_at,
                    )
                else:
                    descriptors = merged
        self._cover_descriptors = descriptors
        return provider_observation(
            self._name,
            tuple(results),
            authority="itunes_search",
            diagnostics=merge_provider_diagnostics(
                self._schema_diagnostics(), conflicts
            ),
            request_accounting=ledger.accounting(),
        )

    def _resolve(
        self,
        ledger: _DiscoveryRequestLedger,
        lane: MetadataDiscoveryLane,
        lookup: MetadataLookup,
        *,
        deadline: float | None,
    ) -> QualifiedLaneObservation:
        """Dispatch one lane's work through the ledger's replay boundary.

        The dispatch lives outside the lane loop so the closure below binds this
        one lane rather than whichever lane the loop reached last. The ladder
        walk qualifies every rung it dispatches, so its result arrives already
        qualified.
        """
        qualified, _replayed = ledger.resolve(
            kind=lane.kind.value,
            matched_family=lane.matched_family,
            title=lane.title,
            authors=lane.authors,
            projection=_MEDIA,
            execute=lambda: self._observe_work(lookup, deadline=deadline),
        )
        return qualified

    def _observe_work(
        self,
        lookup: MetadataLookup,
        *,
        deadline: float | None,
    ) -> QualifiedLaneObservation:
        if (
            lookup.title is None
            or not lookup.authors
            or not work_query_ladder(
                lookup.title, lookup.authors, audiobook=lookup.audio is not None
            )
        ):
            return QualifiedLaneObservation(
                self._result(lookup, ProviderStatus.INSUFFICIENT_INPUT)
            )
        return observe_work_ladder(
            lookup,
            lambda query: self._observe_work_query(lookup, query, deadline=deadline),
        )

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
            self._cover_descriptors = _cover_descriptors(
                self._name, payload.get("results", []), self._cover_wait
            )
        except ProviderFailure as error:
            return self._result(lookup, error.status)
        except _AmbiguousIdentity:
            return self._result(
                lookup, ProviderStatus.MALFORMED, identity_conflict=True
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

    def _cover_wait(self, deadline: float | None = None) -> bool:
        """Pace a cover fetch on the same shared start pacer the search uses."""
        return True if self._pacer is None else self._pacer.wait(deadline)

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
        identity_conflict: bool = False,
    ) -> ProviderObservation:
        diagnostics = self._schema_diagnostics()
        if identity_conflict:
            diagnostics = merge_provider_diagnostics(
                diagnostics, (_IDENTITY_CONFLICT_DIAGNOSTIC,)
            )
        return ProviderObservation(
            self._name,
            status,
            (),
            candidates,
            lookup.kind,
            "itunes_search",
            accessed_at=self._session.accessed_at,
            diagnostics=diagnostics,
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
    lane: MetadataDiscoveryLane, plan: MetadataDiscoveryPlan
) -> MetadataLookup:
    return MetadataLookup(
        lane.kind,
        plan.local_claims,
        lane.title,
        lane.authors,
        audio=plan.audio,
        work_title=plan.work_title,
        work_authors=plan.work_authors,
    )


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
    if type(results) is not list:
        raise ValueError("provider candidate list is malformed")
    if len(results) > _MAX_RETURNED_RESULTS:
        # The store reads `limit` as approximate and overshoots it, so a page
        # wider than the one asked for states a match set that is too broad --
        # not a response missing required identity.  ADR-0061's vocabulary
        # already names that outcome, and it is stated before any result is
        # parsed so the count decides the lane rather than a member of it.
        raise ProviderFailure(
            ProviderStatus.CANDIDATE_LIMIT_EXCEEDED,
            category="candidate_limit_exceeded",
        )
    admitted = [
        _candidate(
            provider,
            result,
            local_claims,
            schema_rejections=schema_rejections,
        )
        for result in results
    ]
    by_identity: dict[str, ProviderCandidate] = {}
    duplicated = False
    for value in admitted:
        previous = by_identity.get(value.identity)
        if previous is None:
            by_identity[value.identity] = value
        elif previous == value:
            # A page that merely repeats a result states nothing new and
            # nothing contradictory: it is a malformed page, as it was before.
            duplicated = True
        else:
            raise _AmbiguousIdentity("provider candidate identity is ambiguous")
    if duplicated:
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
            _UNRESOLVED_WORK,
            tuple(claims),
            _canonical_evidence(evidence),
        ),
        local_claims,
        provider,
    )


def _retained_cover_descriptors(
    descriptors: tuple[ProviderCoverDescriptor, ...],
    observation: ProviderObservation,
) -> tuple[ProviderCoverDescriptor, ...]:
    """Keep only the covers of the candidates this lane's report retains."""
    if observation.status is not ProviderStatus.CANDIDATES:
        return ()
    identities = {candidate.identity for candidate in observation.candidates}
    return tuple(
        value
        for value in descriptors
        if value.provenance is not None
        and value.provenance.edition_identity in identities
    )


def _cover_descriptors(
    provider: str,
    results: object,
    wait: Callable[[float | None], bool],
) -> tuple[ProviderCoverDescriptor, ...]:
    """State one cover source for each collection this search named.

    Every iTunes candidate is discovered rather than resolved from an
    identifier, so its art is a related edition's and never an exact one.
    """
    if type(results) is not list:
        return ()
    descriptors: list[ProviderCoverDescriptor] = []
    for result in results:
        if type(result) is not dict:
            continue
        collection = result.get("collectionId")
        if (
            type(collection) is not int
            or isinstance(collection, bool)
            or not 0 < collection <= _MAX_COLLECTION_ID
        ):
            continue
        url, terminal_state = _cover_url(result.get("artworkUrl100"))
        descriptors.append(
            ProviderCoverDescriptor(
                provider,
                CoverSourceKind.ITUNES_SEARCH,
                cover_source_id(
                    CoverSourceKind.ITUNES_SEARCH,
                    f"{provider}:collection:{collection}",
                ),
                url,
                CoverProvenance(
                    provider,
                    CoverDesignation.RELATED_EDITION,
                    _UNRESOLVED_WORK,
                    "itunes-collection-" + str(collection),
                ),
                wait,
                False,
                None,
                terminal_state,
            )
        )
    return tuple(descriptors)


def _artwork_url(value: str) -> str:
    """Ask the stated origin for the same asset at a legible rendition."""
    if not value.endswith(_STATED_ARTWORK_SEGMENT):
        return value
    return value[: -len(_STATED_ARTWORK_SEGMENT)] + _CONSTRUCTED_ARTWORK_SEGMENT


def _cover_url(value: object) -> tuple[str | None, CoverSourceState | None]:
    """Admit one artwork URL against this provider's closed host set."""
    if value is None:
        return None, None
    if type(value) is not str or not value:
        return None, CoverSourceState.INVALID_DECLARATION
    if len(value) > _MAX_COVER_URL_CHARACTERS:
        return None, CoverSourceState.OVERSIZED
    value = _artwork_url(value)
    try:
        parts = urlsplit(value)
        hostname = parts.hostname
    except ValueError:
        return None, CoverSourceState.UNSAFE_REMOTE
    if (
        any(ord(character) < 0x21 or ord(character) > 0x7E for character in value)
        or "\\" in value
        or parts.scheme != "https"
        or hostname is None
        or hostname not in ITUNES_ARTWORK_HOSTS
        or parts.port not in (None, 443)
        or parts.username is not None
        or parts.password is not None
        or bool(parts.fragment)
    ):
        return None, CoverSourceState.UNSAFE_REMOTE
    return value, None


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
        text = _text(value, maximum)
        if kind in {ClaimKind.TITLE, ClaimKind.SUBTITLE}:
            validate_observed_title(text)
        return FieldState.VALID, text
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
