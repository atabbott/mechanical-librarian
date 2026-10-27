"""Bounded, report-only AudioSilo corroboration observation.

AudioSilo is ADR-0061's corroboration and degraded-mode layer: it states one
work, its recordings, and each recording's per-region product identifiers, so a
match here is a *join* -- a work identity plus the recording the lookup named --
and never a work record on its own.

The service answers an unknown API path with an HTML document rather than a
structured error.  Nothing in this module parses one: the session's strict JSON
content-type check rejects an HTML `200`, and an HTML `404` is classified as a
no-match from its status alone, so the body is never read as data.
"""

from __future__ import annotations

import random
import re
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
    provider_bounds,
    provider_client_lifetime,
)
from mechanical_librarian.adapters.providers.visible_text import stated_description
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
    validate_observed_title,
)

AUDIOSILO_ORIGIN = "https://meta.audiosilo.app"
AUDIOSILO_BASE_URL = AUDIOSILO_ORIGIN + "/api/v1"
# A work or recording identifier only ever reaches a URL through this shape, so
# a hostile identifier cannot leave the fixed origin or the fixed base path.
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_VALUES = 8
_MAX_RECORDINGS = 16
_MAX_REGIONAL_IDENTIFIERS = len(AUDIOBOOK_MARKETPLACES)
# A work document is around 2 KB and a chapter document around 2 KB.
_MAX_AGGREGATE_RESPONSE_BYTES = 1_048_576
_MAX_ATTEMPTS_PER_QUERY = 3
# Four ASIN lanes, each spending a lookup, a work read, and at most one chapter
# read, plus retries.
_MAX_ATTEMPTS_PER_OBSERVATION = 18
_MAX_RUNTIME_SECONDS = 604_800
_UNPROJECTABLE = object()


class AudioSiloMetadataProvider:
    """Observe one AudioSilo work-and-recording join per ASIN."""

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
            _DiscoveryRequestLedger("audiosilo", "api-v1"),
            pacer=pacer,
            clocks=ProviderClocks(clock, wall_clock, sleep, jitter),
        )
        self._called = False
        self._schema_rejections = 0

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
                authority="audiosilo",
            )
        self._called = True
        if type(plan) is not MetadataDiscoveryPlan:
            raise ValueError("provider lookup is invalid")
        if plan.overflow:
            self._client.close()
            return ProviderObservation(
                self._name,
                ProviderStatus.QUERY_LIMIT_EXCEEDED,
                authority="audiosilo",
            )
        lanes = tuple(
            lane
            for lane in plan.lanes
            if lane.kind is ProviderQueryKind.ASIN and lane.canonical_asin is not None
        )
        if not lanes:
            self._client.close()
            # A match here is a work-and-recording join and a title search names
            # no recording, so a plan with no ASIN lane has no input for it.
            return ProviderObservation(
                self._name,
                ProviderStatus.INSUFFICIENT_INPUT,
                authority="audiosilo",
            )
        ledger = _DiscoveryRequestLedger("audiosilo", "api-v1")
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
            authority="audiosilo",
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
        try:
            resolved = self._request(
                AUDIOSILO_BASE_URL + "/lookup", {"asin": asin}, deadline=deadline
            )
            work_id, recording_id = _join(resolved)
            work = self._request(
                AUDIOSILO_BASE_URL + "/works/" + work_id,
                {},
                deadline=deadline,
                # One ASIN is one logical lookup however many documents the
                # join costs, so every dispatch after the first is accounted as
                # an extra dispatch rather than a new request.
                extra_dispatch=True,
            )
            recording = _recording(work, recording_id, self._reject_schema_members)
            candidate = _candidate(
                self._name,
                work,
                recording,
                lookup.local_claims,
                queried_asin=asin,
                chapters=self._chapters(
                    work_id, recording_id, recording, deadline=deadline
                ),
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
        return self._result(lookup, ProviderStatus.CANDIDATES, (candidate,))

    def _chapters(
        self,
        work_id: str,
        recording_id: str,
        recording: dict[str, Any],
        *,
        deadline: float | None,
    ) -> ProviderChapterStructure | None:
        """Spend the extra request only when the recording says it has chapters.

        The chapter document is one whole additional request per lane, so a
        recording that states no chapters is not asked about at all.
        """
        count = recording.get("chapter_count")
        if type(count) is not int or isinstance(count, bool) or count < 1:
            return None
        try:
            payload = self._request(
                AUDIOSILO_BASE_URL
                + "/works/"
                + work_id
                + "/recordings/"
                + recording_id
                + "/chapters",
                {},
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
            "audiosilo",
            accessed_at=self._session.accessed_at,
            diagnostics=self._schema_diagnostics(),
        )

    def _request(
        self,
        url: str,
        params: dict[str, str],
        *,
        deadline: float | None,
        extra_dispatch: bool = False,
    ) -> dict[str, Any]:
        return self._session.request_json(
            url,
            params=params,
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
        requested_asin=lane.canonical_asin,
    )


def _classify(facts: ProviderResponseFacts) -> ProviderFailure | None:
    """Read AudioSilo's status vocabulary off one response.

    A `404` is a no-match whatever the body is: the service answers an unknown
    API path with an HTML document, and reading that body as evidence is exactly
    what must not happen.  429 is named explicitly because the session treats an
    unrecognised status as terminal.
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


def _join(payload: dict[str, Any]) -> tuple[str, str]:
    """Read the required work-and-recording join, or fail closed."""
    work = payload.get("work")
    if type(work) is not dict:
        raise ValueError("provider work is malformed")
    work_id = work.get("id")
    recording_id = payload.get("recording_id")
    if (
        type(work_id) is not str
        or _IDENTIFIER.fullmatch(work_id) is None
        or type(recording_id) is not str
        or _IDENTIFIER.fullmatch(recording_id) is None
    ):
        raise ValueError("provider work identity is malformed")
    return work_id, recording_id


def _recording(
    work: dict[str, Any],
    recording_id: str,
    schema_rejections: Callable[[int], None],
) -> dict[str, Any]:
    """Return the one recording the lookup named, or fail closed.

    A work whose recording list does not contain the recording the lookup just
    resolved is contradicting itself, and half of a join is not a candidate.
    """
    recordings = work.get("recordings")
    if type(recordings) is not list or len(recordings) > _MAX_RECORDINGS:
        raise ValueError("provider recordings are malformed")
    for recording in recordings:
        if type(recording) is not dict:
            schema_rejections(1)
            continue
        if recording.get("id") == recording_id:
            return recording
    raise ValueError("provider recording join is malformed")


def _candidate(
    provider: str,
    work: dict[str, Any],
    recording: dict[str, Any],
    local_claims: tuple[LocalClaim, ...],
    *,
    queried_asin: str,
    chapters: ProviderChapterStructure | None,
    schema_rejections: Callable[[int], None],
) -> ProviderCandidate:
    work_id = work["id"]
    recording_id = recording["id"]
    if type(work_id) is not str or _IDENTIFIER.fullmatch(work_id) is None:
        raise ValueError("provider work identity is malformed")
    identity = f"audiosilo-recording-{work_id}-{recording_id}"

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

    regional = _regional_asins(recording.get("asin"), schema_rejections)
    for asin in sorted(set(regional.values())):
        add(ClaimKind.ASIN, asin)
    if not regional:
        add(ClaimKind.ASIN, None)
    add(ClaimKind.TITLE, work.get("title"))
    authors = _names(work.get("authors"), schema_rejections)
    for author in authors:
        add(ClaimKind.CREATOR, author, role="author")
    if not authors:
        add(ClaimKind.CREATOR, None, role="author")
    narrators = _names(recording.get("narrators"), schema_rejections)
    for narrator in narrators:
        add(ClaimKind.CONTRIBUTOR, narrator, role="narrator")
    if not narrators:
        add(ClaimKind.CONTRIBUTOR, None, role="narrator")
    for series, sequence in _series(work.get("series"), schema_rejections):
        add(ClaimKind.SERIES, series)
        add(ClaimKind.SERIES_SEQUENCE, sequence)
    add(ClaimKind.LANGUAGE, work.get("language"))
    add(ClaimKind.DURATION, _duration(recording.get("runtime_min"), schema_rejections))

    evidence = [
        ProviderEvidence(
            ProviderEvidenceKind.EDITION_WORK_RELATION,
            "unresolved",
            ClaimScope.EDITION,
        )
    ]
    marketplace = next(
        (code for code, value in sorted(regional.items()) if value == queried_asin),
        None,
    )
    if marketplace is not None:
        evidence.append(
            ProviderEvidence(
                ProviderEvidenceKind.ASIN_MARKETPLACE,
                marketplace,
                ClaimScope.EDITION,
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


def _regional_asins(
    value: object, schema_rejections: Callable[[int], None]
) -> dict[str, str]:
    """Read the per-region identifier map, keeping only admitted marketplaces."""
    if value is None:
        return {}
    if type(value) is not list or len(value) > _MAX_REGIONAL_IDENTIFIERS:
        schema_rejections(1)
        return {}
    regional: dict[str, str] = {}
    for item in value:
        if type(item) is not dict:
            schema_rejections(1)
            continue
        region = item.get("region")
        asin = canonical_asin(item.get("asin"))
        if region not in AUDIOBOOK_MARKETPLACES or asin is None or region in regional:
            schema_rejections(1)
            continue
        regional[region] = asin
    return regional


def _chapter_structure(
    payload: dict[str, Any], schema_rejections: Callable[[int], None]
) -> ProviderChapterStructure | None:
    chapters = payload.get("chapters")
    if type(chapters) is not list:
        schema_rejections(1)
        return None
    durations: list[int] = []
    total = 0
    for chapter in chapters:
        if type(chapter) is not dict:
            return None
        length = chapter.get("length_ms")
        if (
            type(length) is not int
            or isinstance(length, bool)
            or not 0 <= length <= _MAX_RUNTIME_SECONDS * 1000
        ):
            return None
        total += length
        if len(durations) < MAX_PROVIDER_CHAPTER_DURATIONS:
            durations.append(length // 1000)
    return ProviderChapterStructure(
        len(chapters),
        min(total // 1000, _MAX_RUNTIME_SECONDS),
        tuple(durations),
        len(chapters) > MAX_PROVIDER_CHAPTER_DURATIONS,
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


def _names(
    value: object, schema_rejections: Callable[[int], None]
) -> tuple[object, ...]:
    if value is None:
        return ()
    if type(value) is not list or len(value) > _MAX_VALUES:
        schema_rejections(1)
        return (_UNPROJECTABLE,)
    names: list[object] = []
    for item in value:
        if type(item) is not dict:
            schema_rejections(1)
            names.append(_UNPROJECTABLE)
            continue
        try:
            names.append(_text(item.get("name")))
        except (ValueError, EvidenceValueError):
            schema_rejections(1)
            names.append(_UNPROJECTABLE)
    return tuple(names)


def _series(
    value: object, schema_rejections: Callable[[int], None]
) -> tuple[tuple[object, object], ...]:
    if value is None or value == []:
        return ((None, None),)
    if type(value) is not list or len(value) > _MAX_VALUES:
        schema_rejections(1)
        return ((_UNPROJECTABLE, _UNPROJECTABLE),)
    pairs: list[tuple[object, object]] = []
    for item in value:
        if type(item) is not dict:
            schema_rejections(1)
            pairs.append((_UNPROJECTABLE, _UNPROJECTABLE))
            continue
        name = item.get("name", _UNPROJECTABLE)
        if type(name) is not str:
            schema_rejections(1)
            name = _UNPROJECTABLE
        sequence = item.get("sequence")
        if type(sequence) is int:
            sequence = str(sequence)
        elif sequence is not None and type(sequence) is not str:
            schema_rejections(1)
            sequence = _UNPROJECTABLE
        pairs.append((name, sequence))
    return tuple(pairs)


def _duration(value: object, schema_rejections: Callable[[int], None]) -> object:
    if value is None:
        return None
    if type(value) is not int or isinstance(value, bool) or not 1 <= value <= 10_080:
        schema_rejections(1)
        return _UNPROJECTABLE
    return f"{value * 60}.000"


def _field(kind: ClaimKind, value: object) -> tuple[FieldState, str | None]:
    if kind is ClaimKind.DESCRIPTION:
        # Read once, as every description is (#1424).
        value = stated_description(value)
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


AudioSiloPacer = ProviderRequestPacer


__all__ = [
    "AUDIOSILO_BASE_URL",
    "AUDIOSILO_ORIGIN",
    "AudioSiloMetadataProvider",
    "AudioSiloPacer",
]
