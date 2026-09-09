"""Bounded, credential-free Inventaire metadata observation."""

from __future__ import annotations

import random
import re
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
from mechanical_librarian.application.metadata_report import (
    MetadataDiscoveryLane,
    MetadataDiscoveryPlan,
    MetadataLookup,
    ProviderCoverDescriptor,
    ProviderLaneResult,
    identifier_suppresses_discovery,
    merge_provider_cover_descriptors,
    provider_discovery_lane_order,
    provider_isbn_applicability,
    provider_observation,
    qualify_provider_observation,
)
from mechanical_librarian.domain.cover_evidence import (
    CoverDesignation,
    CoverProvenance,
    CoverSourceKind,
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
    WorkIdentityContradiction,
    comparison_links,
    finalize_candidate_comparisons,
    isbn_family,
    normalize_date_value,
    provider_candidate_work_identity_contradiction,
)
from mechanical_librarian.domain.work_identity import (
    WorkDispatchLineage,
    WorkDispatchRung,
    narrowing_work_query,
    work_query_ladder,
)

INVENTAIRE_ORIGIN = "https://inventaire.io"
INVENTAIRE_ENTITIES_URL = INVENTAIRE_ORIGIN + "/api/entities/by-uris"
INVENTAIRE_SEARCH_URL = INVENTAIRE_ORIGIN + "/api/search"
INVENTAIRE_USER_AGENT = (
    "MechanicalLibrarian/0.19.0 (+https://github.com/atabbott/mechanical-librarian)"
)

_ENTITY_URI = re.compile(r"^(?:inv|wd):[A-Za-z0-9_-]{1,96}$")
_IMAGE_PATH = re.compile(r"^/img/entities/[0-9a-f]{40}$")
_ISBN_URI = re.compile(r"^isbn:[0-9X]{10,13}$")
_ENTITY_ATTRIBUTES = ("claims", "labels", "image")
_MAX_ENTITY_BATCH = 8
_MAX_SEARCH_ROWS = 3
_MAX_CANDIDATES = 2
_MAX_AGGREGATE_RESPONSE_BYTES = 2_097_152
_MAX_ATTEMPTS_PER_REQUEST = 3
_MAX_ATTEMPTS_PER_OBSERVATION = 27


class InventairePacer(ProviderRequestPacer):
    """One shared conservative request-start clock for Inventaire."""

    def __init__(
        self,
        minimum_interval: float = 1.0,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(minimum_interval, clock=clock, sleep=sleep)


class InventaireMetadataProvider:
    """Observe edition claims first and use work search only as fallback."""

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
        active_pacer = pacer if pacer is not None else InventairePacer()
        self._name = name
        self._client = client
        self._session = BoundedProviderSession(
            name,
            client,
            provider_bounds(
                maximum_bytes,
                timeout_seconds,
                attempts_per_request=_MAX_ATTEMPTS_PER_REQUEST,
                attempts_per_observation=_MAX_ATTEMPTS_PER_OBSERVATION,
                aggregate_multiple=9,
                aggregate_ceiling_bytes=_MAX_AGGREGATE_RESPONSE_BYTES,
            ),
            _DiscoveryRequestLedger("inventaire", "entities-v5.1.1"),
            pacer=active_pacer,
            clocks=ProviderClocks(clock, wall_clock, sleep, jitter),
        )
        self._called = False
        self._schema_rejections = 0
        self._pacer = active_pacer
        self.cover_descriptors: tuple[ProviderCoverDescriptor, ...] = ()
        self.cover_pacer = active_pacer
        self.cover_kind = CoverSourceKind.INVENTAIRE
        self.cover_allow_redirects = False
        self.cover_user_agent = INVENTAIRE_USER_AGENT

    @property
    def attempt_records(self) -> tuple[ProviderAttemptRecord, ...]:
        return self._session.attempt_records

    @property
    def incomplete_causes(self) -> tuple[ProviderIncompleteCauseRecord, ...]:
        return self._session.incomplete_causes

    @property
    def accessed_at(self) -> str | None:
        return self._session.accessed_at

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
                authority="inventaire",
            )
        self._called = True
        if type(plan) is MetadataLookup:
            ledger = _DiscoveryRequestLedger("inventaire", "entities-v5.1.1")
            self._register(ledger, plan)
            self._session.ledger = ledger
            try:
                observation, _ = ledger.resolve(
                    kind=plan.kind.value,
                    matched_family=plan.matched_family,
                    title=plan.title,
                    authors=plan.authors,
                    projection="entities-v5.1.1",
                    execute=lambda: self._observe_lookup(plan, deadline=deadline),
                )
                return replace(
                    observation,
                    diagnostics=self._schema_diagnostics(),
                    request_accounting=ledger.accounting(),
                )
            finally:
                self._client.close()
        if type(plan) is not MetadataDiscoveryPlan:
            raise ValueError("provider lookup is invalid")
        if plan.overflow:
            self._client.close()
            return ProviderObservation(
                self._name,
                ProviderStatus.QUERY_LIMIT_EXCEEDED,
                authority="inventaire",
            )
        ledger = _DiscoveryRequestLedger("inventaire", "entities-v5.1.1")
        for lane in plan.lanes:
            self._register(ledger, lane)
        self._session.ledger = ledger
        results: list[ProviderLaneResult] = []
        try:
            for lane in provider_discovery_lane_order(plan):
                if identifier_suppresses_discovery(plan, results, lane):
                    ledger.suppress(
                        kind=lane.kind.value,
                        matched_family=lane.matched_family,
                        title=lane.title,
                        authors=lane.authors,
                        projection="entities-v5.1.1",
                    )
                    continue
                lookup = MetadataLookup(
                    lane.kind,
                    plan.local_claims,
                    lane.title,
                    lane.authors,
                    lane.matched_family,
                )
                physical, _ = ledger.resolve(
                    kind=lookup.kind.value,
                    matched_family=lookup.matched_family,
                    title=lookup.title,
                    authors=lookup.authors,
                    projection="entities-v5.1.1",
                    execute=lambda: self._observe_lookup(lookup, deadline=deadline),  # noqa: B023 - resolve executes before the loop advances
                )
                qualified = qualify_provider_observation(physical, lookup)
                results.append(
                    ProviderLaneResult(
                        lane,
                        qualified.status,
                        qualified.candidates,
                        qualified.accessed_at,
                        dispatch_lineage=qualified.dispatch_lineage,
                        transformation_lineage=qualified.transformation_lineage,
                    )
                )
        finally:
            self._client.close()
        return provider_observation(
            self._name,
            tuple(results),
            authority="inventaire",
            diagnostics=self._schema_diagnostics(),
            request_accounting=ledger.accounting(),
        )

    @staticmethod
    def _register(
        ledger: _DiscoveryRequestLedger,
        lookup: MetadataLookup | MetadataDiscoveryLane,
    ) -> None:
        ledger.register(
            kind=lookup.kind.value,
            matched_family=lookup.matched_family,
            title=lookup.title,
            authors=lookup.authors,
            projection="entities-v5.1.1",
        )

    def _observe_lookup(
        self, lookup: MetadataLookup, *, deadline: float | None
    ) -> ProviderObservation:
        if lookup.kind is ProviderQueryKind.ISBN:
            family = lookup.matched_family or _single_isbn_family(lookup.local_claims)
            if family is None:
                return self._result(lookup, ProviderStatus.INSUFFICIENT_INPUT)
            references = tuple(
                sorted(
                    claim.reference
                    for claim in lookup.local_claims
                    if claim.kind is ClaimKind.ISBN
                    and claim.canonical is not None
                    and isbn_family(claim.canonical) == family
                )
            )
            try:
                editions = self._entities(
                    ("isbn:" + family,), deadline=deadline, detail=False
                )
                candidates = self._edition_candidates(
                    editions,
                    lookup.local_claims,
                    family,
                    lookup.kind,
                    deadline=deadline,
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
                return self._result(
                    lookup, ProviderStatus.MALFORMED, references=references
                )
            return self._candidates_result(lookup, candidates, references=references)
        if (
            lookup.kind is not ProviderQueryKind.WORK
            or lookup.title is None
            or not lookup.authors
        ):
            return self._result(lookup, ProviderStatus.INSUFFICIENT_INPUT)
        queries = work_query_ladder(lookup.title, lookup.authors)
        if not queries:
            return self._result(lookup, ProviderStatus.INSUFFICIENT_INPUT)
        last = self._result(lookup, ProviderStatus.NO_MATCH)
        for query in queries:
            last = self._observe_work_query(
                lookup,
                query.title,
                query.authors,
                WorkDispatchLineage.from_query(query),
                deadline=deadline,
                query_retry=query.rung is not WorkDispatchRung.NORMALIZED,
            )
            if last.status is ProviderStatus.CANDIDATES:
                return last
            if last.status is ProviderStatus.CANDIDATE_LIMIT_EXCEEDED:
                narrowed = narrowing_work_query(query)
                return self._observe_work_query(
                    lookup,
                    narrowed.title,
                    narrowed.authors,
                    WorkDispatchLineage.from_query(narrowed),
                    deadline=deadline,
                    query_retry=True,
                )
            if last.status is not ProviderStatus.NO_MATCH:
                return last
        return last

    def _observe_work_query(
        self,
        lookup: MetadataLookup,
        title: str,
        authors: tuple[str, ...],
        lineage: WorkDispatchLineage,
        *,
        deadline: float | None,
        query_retry: bool,
    ) -> ProviderObservation:
        try:
            payload = self._request(
                INVENTAIRE_SEARCH_URL,
                {
                    "search": " ".join((title, *authors)),
                    "types": "works",
                    "limit": str(_MAX_SEARCH_ROWS),
                    "lang": "en",
                },
                deadline=deadline,
                query_retry=query_retry,
            )
            uris = _search_work_uris(payload, reject=self._reject_schema_members)
            works = self._entities(uris, deadline=deadline) if uris else ()
            labels = self._resolve_labels(works, deadline=deadline)
            pairs = tuple(
                (
                    _work_candidate(self._name, work, lookup.local_claims, labels),
                    _work_cover_descriptor(self._name, work, self._pacer.wait),
                )
                for work in works
            )
            pairs = tuple(
                (candidate, descriptor)
                for candidate, descriptor in pairs
                if provider_candidate_work_identity_contradiction(
                    candidate, lookup.title or "", lookup.authors
                )
                is not WorkIdentityContradiction.EXPLICIT
            )
            merged = merge_provider_cover_descriptors(
                self.cover_descriptors,
                tuple(value for _, value in pairs if value is not None),
            )
            if merged is None:
                raise ValueError("provider cover descriptors conflict")
            self.cover_descriptors = merged
            candidates = tuple(value for value, _ in pairs)
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
        return self._candidates_result(lookup, candidates, dispatch_lineage=lineage)

    def _edition_candidates(
        self,
        editions: tuple[dict[str, Any], ...],
        local_claims: tuple[LocalClaim, ...],
        family: str,
        query_kind: ProviderQueryKind,
        *,
        deadline: float | None,
    ) -> tuple[ProviderCandidate, ...]:
        admitted = tuple(
            edition for edition in editions if family in _isbn_families(edition)
        )
        if len(admitted) > _MAX_CANDIDATES:
            return tuple(
                _overflow_candidate(index) for index in range(_MAX_CANDIDATES + 1)
            )
        work_uris = _unique_uris(
            uri for edition in admitted for uri in _claim_strings(edition, "wdt:P629")
        )
        works = self._entities(work_uris, deadline=deadline) if work_uris else ()
        work_by_uri = {_entity_uri(work): work for work in works}
        labels = self._resolve_labels((*admitted, *works), deadline=deadline)
        pairs: list[tuple[ProviderCandidate, ProviderCoverDescriptor | None]] = []
        for edition in admitted:
            work_uri = _one_optional_uri(edition, "wdt:P629")
            work = None if work_uri is None else work_by_uri.get(work_uri)
            candidate = _edition_candidate(
                self._name, edition, work, local_claims, labels
            )
            pairs.append(
                (
                    candidate,
                    _cover_descriptor(
                        self._name,
                        edition,
                        work,
                        query_kind,
                        self._pacer.wait,
                    ),
                )
            )
        merged = merge_provider_cover_descriptors(
            self.cover_descriptors,
            tuple(value for _, value in pairs if value is not None),
        )
        if merged is None:
            raise ValueError("provider cover descriptors conflict")
        self.cover_descriptors = merged
        return tuple(value for value, _ in pairs)

    def _resolve_labels(
        self,
        entities: tuple[dict[str, Any], ...],
        *,
        deadline: float | None,
    ) -> dict[str, str]:
        direct: dict[str, str] = {}
        for entity in entities:
            label = _optional_entity_label(entity, self._reject_schema_members)
            if label is not None:
                direct[_entity_uri(entity)] = label
        references = _unique_uris(
            (
                value
                for entity in entities
                for key in ("wdt:P50", "wdt:P123", "wdt:P179", "wdt:P407")
                for value in _claim_strings(entity, key)
                if value not in direct
            ),
            maximum=_MAX_ENTITY_BATCH * _MAX_ENTITY_BATCH,
        )
        for offset in range(0, len(references), _MAX_ENTITY_BATCH):
            try:
                optional_entities = self._optional_label_entities(
                    references[offset : offset + _MAX_ENTITY_BATCH], deadline=deadline
                )
            except ProviderFailure:
                continue
            for entity in optional_entities:
                label = _optional_entity_label(entity, self._reject_schema_members)
                if label is not None:
                    direct[_entity_uri(entity)] = label
        return direct

    def _optional_label_entities(
        self,
        uris: tuple[str, ...],
        *,
        deadline: float | None,
        detail: bool = True,
    ) -> tuple[dict[str, Any], ...]:
        if (
            not uris
            or len(uris) > _MAX_ENTITY_BATCH
            or any(_ENTITY_URI.fullmatch(uri) is None for uri in uris)
        ):
            raise ValueError("provider label identities are malformed")
        if detail:
            result, _ = self._session.ledger.resolve_detail(
                "labels|" + "|".join(uris),
                execute=lambda: self._optional_label_entities(
                    uris, deadline=deadline, detail=False
                ),
            )
            return result
        params = httpx.QueryParams(
            [
                *(("uris", uri) for uri in uris),
                ("attributes", "labels"),
                ("lang", "en"),
            ]
        )
        payload = self._request(INVENTAIRE_ENTITIES_URL, params, deadline=deadline)
        entities = payload.get("entities")
        redirects = payload.get("redirects")
        if (
            type(entities) is not dict
            or len(entities) > _MAX_ENTITY_BATCH
            or type(redirects) is not dict
            or redirects
        ):
            self._reject_schema_members()
            return ()
        requested = frozenset(uris)
        self._reject_schema_members(len(requested - set(entities)))
        admitted: list[dict[str, Any]] = []
        for response_uri, value in entities.items():
            if type(value) is not dict:
                self._reject_schema_members()
                continue
            try:
                entity_uri = _entity_uri(value)
            except (EvidenceValueError, TypeError, ValueError):
                self._reject_schema_members()
                continue
            if response_uri not in requested or entity_uri != response_uri:
                self._reject_schema_members()
                continue
            admitted.append(value)
        return tuple(admitted)

    def _entities(
        self,
        uris: tuple[str, ...],
        *,
        deadline: float | None,
        detail: bool = True,
    ) -> tuple[dict[str, Any], ...]:
        if len(uris) > _MAX_ENTITY_BATCH or any(
            not _valid_lookup_uri(uri) for uri in uris
        ):
            raise ValueError("provider entity identity is malformed")
        if not uris:
            return ()
        if detail:
            result, _ = self._session.ledger.resolve_detail(
                "|".join(uris),
                execute=lambda: self._entities(uris, deadline=deadline, detail=False),
            )
            return result
        params = httpx.QueryParams(
            [
                *(("uris", uri) for uri in uris),
                *(("attributes", value) for value in _ENTITY_ATTRIBUTES),
                ("lang", "en"),
            ]
        )
        payload = self._request(INVENTAIRE_ENTITIES_URL, params, deadline=deadline)
        entities = payload.get("entities")
        if type(entities) is not dict or len(entities) > _MAX_ENTITY_BATCH:
            raise ValueError("provider entities are malformed")
        redirects = payload.get("redirects")
        if type(redirects) is not dict or len(redirects) > _MAX_ENTITY_BATCH:
            raise ValueError("provider redirects are malformed")
        isbn_uris = tuple(uri for uri in uris if uri.startswith("isbn:"))
        if isbn_uris:
            if "notFound" in payload:
                missing = payload["notFound"]
                if (
                    type(missing) is not list
                    or len(missing) > _MAX_ENTITY_BATCH
                    or any(type(uri) is not str for uri in missing)
                    or len(set(missing)) != len(missing)
                    or (
                        missing
                        and (
                            entities
                            or redirects
                            or set(missing) != set(isbn_uris)
                            or len(isbn_uris) != len(uris)
                        )
                    )
                ):
                    raise ValueError("provider ISBN absence is malformed")
                if missing:
                    return ()
            if len(isbn_uris) != len(uris) or set(redirects) != set(isbn_uris):
                raise ValueError("provider ISBN redirects are malformed")
            targets = tuple(redirects.values())
            if (
                len(targets) != 1
                or type(targets[0]) is not str
                or _ENTITY_URI.fullmatch(targets[0]) is None
                or set(entities) != {targets[0]}
            ):
                raise ValueError("provider ISBN redirect target is malformed")
        elif redirects:
            raise ValueError("provider entity redirects are malformed")
        elif set(entities) != set(uris):
            raise ValueError("provider entity result is incomplete")
        admitted: list[dict[str, Any]] = []
        requested = frozenset(uris if not isbn_uris else redirects.values())
        for response_uri, value in entities.items():
            if type(value) is not dict:
                raise ValueError("provider entity is malformed")
            entity_uri = _entity_uri(value)
            if response_uri not in requested or entity_uri != response_uri:
                raise ValueError("provider entity identity is inconsistent")
            admitted.append(value)
        if isbn_uris and len(admitted) != 1:
            raise ValueError("provider ISBN redirect target is unavailable")
        return tuple(admitted)

    def _request(
        self,
        url: str,
        params: dict[str, str] | httpx.QueryParams,
        *,
        deadline: float | None,
        query_retry: bool = False,
    ) -> dict[str, Any]:
        return self._session.request_json(
            url,
            params=params,
            headers={"Accept": "application/json", "Accept-Encoding": "identity"},
            deadline=deadline,
            query_retry=query_retry,
            classify=_classify_response,
        )

    def _candidates_result(
        self,
        lookup: MetadataLookup,
        candidates: tuple[ProviderCandidate, ...],
        *,
        references: tuple[str, ...] = (),
        dispatch_lineage: WorkDispatchLineage | None = None,
    ) -> ProviderObservation:
        if len(candidates) > _MAX_CANDIDATES:
            status = ProviderStatus.CANDIDATE_LIMIT_EXCEEDED
            candidates = ()
        elif not candidates:
            status = ProviderStatus.NO_MATCH
        else:
            status = ProviderStatus.CANDIDATES
        return self._result(
            lookup,
            status,
            candidates,
            references=references,
            dispatch_lineage=(
                dispatch_lineage
                if status
                in {ProviderStatus.CANDIDATES, ProviderStatus.CANDIDATE_LIMIT_EXCEEDED}
                else None
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
            "inventaire",
            accessed_at=self._session.accessed_at,
            diagnostics=self._schema_diagnostics(),
            dispatch_lineage=dispatch_lineage,
        )

    def _reject_schema_members(self, count: int = 1) -> None:
        self._schema_rejections = min(
            MAX_DIAGNOSTIC_COUNT, self._schema_rejections + max(0, count)
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


def _classify_response(facts: ProviderResponseFacts) -> ProviderFailure | None:
    if facts.status_code == 429:
        retry_after = bounded_retry_after(
            facts.headers.get("retry-after"), now=facts.now
        )
        return ProviderFailure(
            ProviderStatus.RATE_LIMITED,
            category="http_429",
            retryable=retry_after is not None,
            retry_after=retry_after,
        )
    if facts.status_code in {500, 502, 503, 504}:
        return ProviderFailure(
            ProviderStatus.UNAVAILABLE,
            category=f"http_{facts.status_code}",
            retryable=True,
        )
    if facts.status_code == 404:
        return ProviderFailure(ProviderStatus.NO_MATCH, category="http_404")
    return None


def _single_isbn_family(claims: tuple[LocalClaim, ...]) -> str | None:
    families = {
        family
        for claim in claims
        if claim.kind is ClaimKind.ISBN
        and claim.canonical is not None
        and claim.applicability in {"ebook", "unknown"}
        and claim.basis == "opening_matter"
        and claim.rear_provenance is None
        and (family := isbn_family(claim.canonical)) is not None
    }
    return next(iter(families)) if len(families) == 1 else None


def _valid_lookup_uri(value: str) -> bool:
    return (
        _ENTITY_URI.fullmatch(value) is not None
        or _ISBN_URI.fullmatch(value) is not None
    )


def _entity_uri(entity: dict[str, Any]) -> str:
    value = entity.get("uri")
    if type(value) is not str or _ENTITY_URI.fullmatch(value) is None:
        raise ValueError("provider entity identity is malformed")
    return value


def _optional_entity_label(
    entity: dict[str, Any], reject: Callable[[int], None]
) -> str | None:
    labels = entity.get("labels")
    if type(labels) is not dict or not labels or len(labels) > _MAX_ENTITY_BATCH:
        reject(1)
        return None
    valid: dict[str, str] = {}
    rejected = False
    for key, value in labels.items():
        if type(key) is not str or type(value) is not str:
            reject(1)
            rejected = True
            continue
        try:
            valid[key] = validate_projection_text(
                value, "provider entity label", MAX_SCALAR_CHARACTERS
            )
        except (EvidenceValueError, ValueError):
            reject(1)
            rejected = True
    for key in (
        "en",
        "fromclaims",
        *(key for key in sorted(valid) if key not in {"en", "fromclaims"}),
    ):
        if key in valid:
            return valid[key]
    if not rejected:
        reject(1)
    return None


def _claims(entity: dict[str, Any]) -> dict[str, Any]:
    value = entity.get("claims")
    if type(value) is not dict or len(value) > 64:
        raise ValueError("provider claims are malformed")
    return value


def _claim_strings(entity: dict[str, Any], key: str) -> tuple[str, ...]:
    value = _claims(entity).get(key)
    if value is None:
        return ()
    if type(value) is not list or len(value) > _MAX_ENTITY_BATCH:
        raise ValueError("provider claim values are malformed")
    values: list[str] = []
    for member in value:
        if type(member) is not str:
            raise ValueError("provider claim value is malformed")
        values.append(
            validate_projection_text(
                member, "provider claim value", MAX_SCALAR_CHARACTERS
            )
        )
    return tuple(values)


def _one_optional_uri(entity: dict[str, Any], key: str) -> str | None:
    values = _claim_strings(entity, key)
    if len(values) > 1 or any(_ENTITY_URI.fullmatch(value) is None for value in values):
        raise ValueError("provider relation is malformed")
    return values[0] if values else None


def _unique_uris(values: Any, *, maximum: int = _MAX_ENTITY_BATCH) -> tuple[str, ...]:
    admitted = tuple(dict.fromkeys(values))
    if len(admitted) > maximum or any(
        _ENTITY_URI.fullmatch(value) is None for value in admitted
    ):
        raise ValueError("provider relation set is malformed")
    return admitted


def _isbn_families(entity: dict[str, Any]) -> frozenset[str]:
    values = (*_claim_strings(entity, "wdt:P212"), *_claim_strings(entity, "wdt:P957"))
    return frozenset(
        family for value in values if (family := isbn_family(value)) is not None
    )


def _search_work_uris(
    source: dict[str, Any], *, reject: Callable[[int], None]
) -> tuple[str, ...]:
    rows = source.get("results")
    if type(rows) is not list or len(rows) > _MAX_SEARCH_ROWS:
        raise ValueError("provider search results are malformed")
    uris: list[str] = []
    for row in rows:
        if type(row) is not dict or row.get("type") != "works":
            reject(1)
            continue
        uri = row.get("uri")
        if type(uri) is not str or _ENTITY_URI.fullmatch(uri) is None:
            reject(1)
            continue
        uris.append(uri)
    return tuple(dict.fromkeys(uris))


def _label_or_literal(value: str, labels: dict[str, str]) -> str | None:
    if _ENTITY_URI.fullmatch(value) is not None:
        return labels.get(value)
    return value


def _claim(
    provider: str,
    identity: str,
    kind: ClaimKind,
    value: object,
    scope: ClaimScope,
    local_claims: tuple[LocalClaim, ...],
    raw_order: int,
    *,
    applicability: str = "not_applicable",
    role: str | None = None,
) -> ProviderClaim:
    state, text = _field(kind, value)
    bare = ProviderClaim.create(
        provider=provider,
        candidate=identity,
        kind=kind,
        value=text,
        state=state,
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
        state=state,
        scope=scope,
        applicability=applicability,
        role=role,
        raw_order=raw_order,
        links=comparison_links(bare, local_claims),
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
        text = validate_projection_text(value, "provider evidence value", maximum)
        if kind is ClaimKind.PUBLICATION_DATE:
            text = normalize_date_value(text)[0]
        return FieldState.VALID, text
    except (ValueError, EvidenceValueError):
        return (
            FieldState.OVERSIZED
            if type(value) is str and len(value) > maximum
            else FieldState.MALFORMED,
            None,
        )


def _add_values(
    claims: list[ProviderClaim],
    provider: str,
    identity: str,
    entity: dict[str, Any],
    key: str,
    kind: ClaimKind,
    scope: ClaimScope,
    local_claims: tuple[LocalClaim, ...],
    labels: dict[str, str],
    *,
    applicability: Callable[[str], str] | None = None,
    role: str | None = None,
) -> None:
    values = _claim_strings(entity, key)
    if not values:
        claims.append(
            _claim(
                provider,
                identity,
                kind,
                None,
                scope,
                local_claims,
                len(claims),
                role=role,
            )
        )
        return
    for value in values:
        text = _label_or_literal(value, labels)
        if text is None and role != "author":
            continue
        claims.append(
            _claim(
                provider,
                identity,
                kind,
                text,
                scope,
                local_claims,
                len(claims),
                applicability=(
                    "not_applicable"
                    if applicability is None or text is None
                    else applicability(text)
                ),
                role=role,
            )
        )


def _edition_candidate(
    provider: str,
    edition: dict[str, Any],
    work: dict[str, Any] | None,
    local_claims: tuple[LocalClaim, ...],
    labels: dict[str, str],
) -> ProviderCandidate:
    uri = _entity_uri(edition)
    identity = "inventaire-edition-" + uri.replace(":", "-")
    work_uri = _one_optional_uri(edition, "wdt:P629")
    work_identity = (
        "unresolved"
        if work_uri is None
        else "inventaire-work-" + work_uri.replace(":", "-")
    )
    if work is not None and _entity_uri(work) != work_uri:
        raise ValueError("provider work relation is inconsistent")
    claims: list[ProviderClaim] = []
    for value in sorted(
        {
            _normalized_isbn(value)
            for value in (
                *_claim_strings(edition, "wdt:P212"),
                *_claim_strings(edition, "wdt:P957"),
            )
        }
    ):
        claims.append(
            _claim(
                provider,
                identity,
                ClaimKind.ISBN,
                value,
                ClaimScope.EDITION,
                local_claims,
                len(claims),
                applicability=provider_isbn_applicability(value, local_claims),
            )
        )
    for key, kind in (
        ("wdt:P1476", ClaimKind.TITLE),
        ("wdt:P1680", ClaimKind.SUBTITLE),
        ("wdt:P407", ClaimKind.LANGUAGE),
        ("wdt:P123", ClaimKind.PUBLISHER),
        ("wdt:P577", ClaimKind.PUBLICATION_DATE),
    ):
        _add_values(
            claims,
            provider,
            identity,
            edition,
            key,
            kind,
            ClaimScope.EDITION,
            local_claims,
            labels,
        )
    if work is not None:
        for key, kind in (
            ("wdt:P179", ClaimKind.SERIES),
            ("wdt:P1545", ClaimKind.SERIES_SEQUENCE),
        ):
            _add_values(
                claims,
                provider,
                identity,
                work,
                key,
                kind,
                ClaimScope.WORK,
                local_claims,
                labels,
            )
    evidence = (
        ()
        if work_uri is None
        else (
            ProviderEvidence(
                ProviderEvidenceKind.EDITION_WORK_RELATION,
                work_identity,
                ClaimScope.EDITION,
            ),
        )
    )
    if len(evidence) > MAX_PROVIDER_EVIDENCE:
        raise ValueError("provider evidence is excessive")
    return finalize_candidate_comparisons(
        ProviderCandidate(identity, work_identity, tuple(claims), evidence),
        local_claims,
        provider,
    )


def _work_candidate(
    provider: str,
    work: dict[str, Any],
    local_claims: tuple[LocalClaim, ...],
    labels: dict[str, str],
) -> ProviderCandidate:
    uri = _entity_uri(work)
    identity = "inventaire-work-" + uri.replace(":", "-")
    claims: list[ProviderClaim] = []
    title_values = _claim_strings(work, "wdt:P1476")
    title = title_values[0] if title_values else labels.get(uri)
    claims.append(
        _claim(
            provider,
            identity,
            ClaimKind.TITLE,
            title,
            ClaimScope.WORK,
            local_claims,
            len(claims),
        )
    )
    for key, kind in (
        ("wdt:P50", ClaimKind.CREATOR),
        ("wdt:P179", ClaimKind.SERIES),
        ("wdt:P1545", ClaimKind.SERIES_SEQUENCE),
    ):
        _add_values(
            claims,
            provider,
            identity,
            work,
            key,
            kind,
            ClaimScope.WORK,
            local_claims,
            labels,
            role="author" if key == "wdt:P50" else None,
        )
    return finalize_candidate_comparisons(
        ProviderCandidate(identity, identity, tuple(claims)), local_claims, provider
    )


def _normalized_isbn(value: str) -> str:
    compact = "".join(
        character for character in value if character.isdigit() or character in "Xx"
    ).upper()
    if isbn_family(compact) is None:
        raise ValueError("provider ISBN is malformed")
    return compact


def _cover_descriptor(
    provider: str,
    edition: dict[str, Any],
    work: dict[str, Any] | None,
    query_kind: ProviderQueryKind,
    wait: Callable[[float | None], bool],
) -> ProviderCoverDescriptor | None:
    value = _image_path(edition)
    if value is None:
        return None
    edition_uri = _entity_uri(edition)
    work_uri = _one_optional_uri(edition, "wdt:P629")
    if work_uri is None or work is None:
        return None
    return ProviderCoverDescriptor(
        provider,
        CoverSourceKind.INVENTAIRE,
        cover_source_id(
            CoverSourceKind.INVENTAIRE,
            f"{provider}:edition:{edition_uri}",
        ),
        INVENTAIRE_ORIGIN + value,
        CoverProvenance(
            provider,
            (
                CoverDesignation.EXACT_EDITION
                if query_kind is ProviderQueryKind.ISBN
                else CoverDesignation.RELATED_EDITION
            ),
            "inventaire-work-" + work_uri.replace(":", "-"),
            "inventaire-edition-" + edition_uri.replace(":", "-"),
        ),
        wait,
        False,
        INVENTAIRE_USER_AGENT,
    )


def _work_cover_descriptor(
    provider: str,
    work: dict[str, Any],
    wait: Callable[[float | None], bool],
) -> ProviderCoverDescriptor | None:
    value = _image_path(work)
    if value is None:
        return None
    uri = _entity_uri(work)
    identity = "inventaire-work-" + uri.replace(":", "-")
    return ProviderCoverDescriptor(
        provider,
        CoverSourceKind.INVENTAIRE,
        cover_source_id(
            CoverSourceKind.INVENTAIRE,
            f"{provider}:work:{uri}",
        ),
        INVENTAIRE_ORIGIN + value,
        CoverProvenance(provider, CoverDesignation.WORK, identity),
        wait,
        False,
        INVENTAIRE_USER_AGENT,
    )


def _image_path(entity: dict[str, Any]) -> str | None:
    image = entity.get("image")
    if image is None:
        return None
    if type(image) is not dict or set(image) != {"url"}:
        raise ValueError("provider cover descriptor is malformed")
    value = image["url"]
    if type(value) is not str or _IMAGE_PATH.fullmatch(value) is None:
        raise ValueError("provider cover path is malformed")
    return value


def _overflow_candidate(index: int) -> ProviderCandidate:
    identity = f"inventaire-overflow-{index}"
    return ProviderCandidate(identity, "unresolved", ())


__all__ = [
    "INVENTAIRE_ENTITIES_URL",
    "INVENTAIRE_ORIGIN",
    "INVENTAIRE_SEARCH_URL",
    "INVENTAIRE_USER_AGENT",
    "InventaireMetadataProvider",
    "InventairePacer",
]
