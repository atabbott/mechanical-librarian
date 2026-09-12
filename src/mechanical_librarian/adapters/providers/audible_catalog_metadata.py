"""Bounded, report-only Audible catalog observation.

The anonymous catalog is ADR-0061's primary audiobook discovery and enrichment
surface.  An ASIN is marketplace-scoped, so a product lookup walks the
configured marketplace order and the marketplace that actually resolved travels
with the candidate as `ASIN_MARKETPLACE` evidence; a search runs against the
first configured marketplace only, because a search is discovery rather than
resolution and repeating it ten times buys nothing.
"""

from __future__ import annotations

import random
import time
from collections.abc import Mapping
from dataclasses import replace
from types import MappingProxyType
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
from mechanical_librarian.application.metadata_report import (
    MetadataDiscoveryLane,
    MetadataDiscoveryPlan,
    MetadataLookup,
    ProviderCoverDescriptor,
    ProviderFailureSnapshot,
    ProviderLaneResult,
    ProviderResult,
    merge_provider_cover_descriptors,
    provider_observation,
    qualify_provider_observation,
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
    canonical_asin,
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

# The ten marketplaces ADR-0061 admits, and nothing else.  Every request URL in
# this module is constructed from this mapping, so a marketplace code that is
# not a key here cannot become a host: the closed set is the confinement, in the
# `GOOGLE_BOOKS_URL` idiom, and the keyless client cannot follow a redirect out
# of it either.
AUDIBLE_MARKETPLACE_HOSTS: Mapping[str, str] = MappingProxyType(
    {
        "us": "api.audible.com",
        "uk": "api.audible.co.uk",
        "de": "api.audible.de",
        "fr": "api.audible.fr",
        "au": "api.audible.com.au",
        "in": "api.audible.in",
        "ca": "api.audible.ca",
        "jp": "api.audible.co.jp",
        "it": "api.audible.it",
        "es": "api.audible.es",
    }
)
AUDIBLE_CATALOG_URLS: Mapping[str, str] = MappingProxyType(
    {
        code: f"https://{host}/1.0/catalog/products"
        for code, host in AUDIBLE_MARKETPLACE_HOSTS.items()
    }
)
_RESPONSE_GROUPS = (
    "contributors,media,product_attrs,product_desc,product_extended_attrs,series"
)
_MAX_RESULTS = 5
_MAX_RETURNED_RESULTS = _MAX_RESULTS + 1
_MAX_VALUES = 8
# One product record is around 7 KB and one search page of six is around 40 KB.
# Nine of the larger of those is the aggregate a whole observation may spend.
_MAX_AGGREGATE_RESPONSE_BYTES = 1_048_576
_MAX_ATTEMPTS_PER_QUERY = 3
# Four ASIN lanes walking a marketplace order, or one work lane walking its
# query ladder, plus retries, all inside one observation.
_MAX_ATTEMPTS_PER_OBSERVATION = 24
_SEARCH_TOP_LEVEL = frozenset(
    {"product_filters", "products", "response_groups", "total_results"}
)
_LOOKUP_TOP_LEVEL = frozenset({"product", "response_groups"})
# One value the provider stated that this adapter will not project. It is not
# an absence: the claim keeps a `MALFORMED` state so the report still records
# that the provider said something here.
_UNPROJECTABLE = object()
# Every audiobook candidate this adapter states names a product, never a work:
# the catalog has no work identifier at all, so the report carries the same
# unresolved work sentinel on the candidate and on its cover provenance.
_UNRESOLVED_WORK = "unresolved"
# The one origin Audible publishes product art from.  The cover transport is
# address-pinned and refuses a redirect, so this closed set is the whole origin
# contract for this provider's cover bytes: a URL naming any other host is
# refused where the descriptor is built rather than at the socket.
AUDIBLE_COVER_HOSTS: frozenset[str] = frozenset({"m.media-amazon.com"})
_MAX_COVER_URL_CHARACTERS = 2048


class AudibleCatalogMetadataProvider:
    """Observe Audible catalog products without treating them as works."""

    def __init__(
        self,
        name: str,
        client: httpx.Client,
        maximum_bytes: int,
        timeout_seconds: float = 5.0,
        *,
        marketplaces: tuple[str, ...] = ("us",),
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
            or len(marketplaces) > len(AUDIBLE_MARKETPLACE_HOSTS)
            or len(set(marketplaces)) != len(marketplaces)
            or any(value not in AUDIBLE_CATALOG_URLS for value in marketplaces)
        ):
            raise ValueError("provider marketplaces are invalid")
        self._name = name
        self._client = client
        self._marketplaces = marketplaces
        self._session = BoundedProviderSession(
            name,
            client,
            bounds,
            _DiscoveryRequestLedger("audible_catalog", "catalog-1.0"),
            pacer=pacer,
            clocks=ProviderClocks(clock, wall_clock, sleep, jitter),
        )
        self._called = False
        self._schema_rejections = 0
        self._pacer = pacer
        self._cover_descriptors: tuple[ProviderCoverDescriptor, ...] = ()

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
        """Observe one audiobook discovery plan's ASIN and work lanes.

        Only a plan is accepted.  An ASIN lane's identity lives on the lane and
        `MetadataLookup` carries no ASIN, so a lookup-shaped call could not name
        the product it was asking about.
        """
        self._schema_rejections = 0
        if self._called:
            return ProviderObservation(
                self._name,
                ProviderStatus.UNAVAILABLE,
                authority="audible_catalog",
            )
        self._called = True
        if type(plan) is not MetadataDiscoveryPlan:
            raise ValueError("provider lookup is invalid")
        if plan.overflow:
            self._client.close()
            return ProviderObservation(
                self._name,
                ProviderStatus.QUERY_LIMIT_EXCEEDED,
                authority="audible_catalog",
            )
        ledger = _DiscoveryRequestLedger("audible_catalog", "catalog-1.0")
        for lane in plan.lanes:
            if lane.kind is ProviderQueryKind.ASIN and lane.canonical_asin is not None:
                ledger.register_detail(lane.canonical_asin)
            else:
                ledger.register(
                    kind=lane.kind.value,
                    matched_family=lane.matched_family,
                    title=lane.title,
                    authors=lane.authors,
                    projection=_RESPONSE_GROUPS,
                )
        self._session.ledger = ledger
        results: list[ProviderLaneResult] = []
        descriptors: tuple[ProviderCoverDescriptor, ...] = ()
        with provider_client_lifetime(self._client):
            for lane in plan.lanes:
                self._cover_descriptors = ()
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
                        dispatch_lineage=observation.dispatch_lineage,
                        transformation_lineage=observation.transformation_lineage,
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
            authority="audible_catalog",
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
        if lane.kind is ProviderQueryKind.ASIN:
            physical, _replayed = ledger.resolve_detail(
                _lane_asin(lane),
                execute=lambda: self._observe_asin(lane, lookup, deadline=deadline),
            )
            return physical
        physical, _replayed = ledger.resolve(
            kind=lane.kind.value,
            matched_family=lane.matched_family,
            title=lane.title,
            authors=lane.authors,
            projection=_RESPONSE_GROUPS,
            execute=lambda: self._observe_work(lookup, deadline=deadline),
        )
        return physical

    def _observe_asin(
        self,
        lane: MetadataDiscoveryLane,
        lookup: MetadataLookup,
        *,
        deadline: float | None,
    ) -> ProviderObservation:
        """Resolve one ASIN in the first configured marketplace that has it.

        A marketplace that does not carry the product is not a failure: an ASIN
        is marketplace-scoped, so absence in one marketplace says nothing about
        the next.  Any other outcome is terminal for the lane, because a quota
        or service failure must never be reclassified as a no-match.
        """
        asin = _lane_asin(lane)
        for index, code in enumerate(self._marketplaces):
            try:
                payload = self._request(
                    AUDIBLE_CATALOG_URLS[code] + "/" + asin,
                    {"response_groups": _RESPONSE_GROUPS},
                    deadline=deadline,
                    # One ASIN is one logical lookup however many marketplaces
                    # it takes to resolve, so every dispatch after the first is
                    # accounted as an extra dispatch rather than a new request.
                    query_retry=index > 0,
                )
                product = _lookup_product(payload, self._reject_schema_members)
                candidate = _candidate(
                    self._name,
                    product,
                    lookup.local_claims,
                    marketplace=code,
                    schema_rejections=self._reject_schema_members,
                )
                self._cover_descriptors = _cover_descriptors(
                    self._name,
                    [product],
                    ProviderQueryKind.ASIN,
                    self._cover_wait,
                )
            except ProviderFailure as error:
                if error.status is ProviderStatus.NO_MATCH:
                    continue
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
        return self._result(lookup, ProviderStatus.NO_MATCH)

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
        # Discovery economy: a search resolves nothing marketplace-scoped, so
        # repeating it across marketplaces would spend the observation's whole
        # dispatch budget to return the same catalogue.
        marketplace = self._marketplaces[0]
        try:
            payload = self._request(
                AUDIBLE_CATALOG_URLS[marketplace],
                {
                    "author": " ".join(query.authors),
                    "num_results": str(_MAX_RETURNED_RESULTS),
                    "response_groups": _RESPONSE_GROUPS,
                    "title": query.title,
                },
                deadline=deadline,
                query_retry=query.rung is not WorkDispatchRung.NORMALIZED
                or query.candidate_limit_narrowing_retry,
            )
            candidates = _candidates(
                self._name,
                payload,
                lookup.local_claims,
                marketplace=marketplace,
                schema_rejections=self._reject_schema_members,
            )
            self._cover_descriptors = _cover_descriptors(
                self._name,
                payload.get("products", []),
                ProviderQueryKind.WORK,
                self._cover_wait,
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

    def _cover_wait(self, deadline: float | None = None) -> bool:
        """Pace a cover fetch on the same shared start pacer the API uses."""
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
    ) -> ProviderObservation:
        return ProviderObservation(
            self._name,
            status,
            (),
            candidates,
            lookup.kind,
            "audible_catalog",
            accessed_at=self._session.accessed_at,
            diagnostics=self._schema_diagnostics(),
            dispatch_lineage=dispatch_lineage,
        )

    def _request(
        self,
        url: str,
        params: dict[str, str],
        *,
        deadline: float | None,
        query_retry: bool = False,
    ) -> dict[str, Any]:
        return self._session.request_json(
            url,
            params=params,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
            deadline=deadline,
            query_retry=query_retry,
            classify=_classify,
        )


def _retained_cover_descriptors(
    descriptors: tuple[ProviderCoverDescriptor, ...],
    observation: ProviderObservation,
) -> tuple[ProviderCoverDescriptor, ...]:
    """Keep only the covers of the candidates this lane's report retains.

    A work search states up to five products but the work-identity admission
    retains one, so acquiring the other four would spend the cover budget on
    candidates no reader will ever see.
    """
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
    products: object,
    query_kind: ProviderQueryKind,
    wait: Callable[[float | None], bool],
) -> tuple[ProviderCoverDescriptor, ...]:
    """State one cover source for each product this response named.

    A product with no art states an absent cover rather than none at all: the
    report records that the authority was asked and had nothing.
    """
    if type(products) is not list:
        return ()
    descriptors: list[ProviderCoverDescriptor] = []
    for product in products:
        if type(product) is not dict:
            continue
        asin = canonical_asin(product.get("asin"))
        if asin is None:
            continue
        url, terminal_state = _cover_url(product.get("product_images"))
        descriptors.append(
            ProviderCoverDescriptor(
                provider,
                CoverSourceKind.AUDIBLE_CATALOG,
                cover_source_id(
                    CoverSourceKind.AUDIBLE_CATALOG,
                    f"{provider}:product:{asin}",
                ),
                url,
                CoverProvenance(
                    provider,
                    (
                        CoverDesignation.EXACT_EDITION
                        if query_kind is ProviderQueryKind.ASIN
                        else CoverDesignation.RELATED_EDITION
                    ),
                    _UNRESOLVED_WORK,
                    "audible-product-" + asin,
                ),
                wait,
                False,
                None,
                terminal_state,
            )
        )
    return tuple(descriptors)


def _cover_url(value: object) -> tuple[str | None, CoverSourceState | None]:
    """Admit one product image URL against this provider's closed host set.

    The catalog states its art under integer width keys, so the widest key is
    the one asked for.  Anything outside `AUDIBLE_COVER_HOSTS` is refused here
    with a typed state rather than dropped, because a provider that offered a
    cover this build would not fetch is a fact the report states.
    """
    if value is None:
        return None, None
    if type(value) is not dict or not value:
        return None, CoverSourceState.INVALID_DECLARATION
    widths = [
        (width, image)
        for key, image in value.items()
        if type(key) is str and key.isdigit() and (width := int(key)) > 0
    ]
    if not widths:
        return None, CoverSourceState.INVALID_DECLARATION
    url = max(widths, key=lambda item: item[0])[1]
    if type(url) is not str or not url:
        return None, CoverSourceState.INVALID_DECLARATION
    if len(url) > _MAX_COVER_URL_CHARACTERS:
        return None, CoverSourceState.OVERSIZED
    return _admitted_cover_url(url, AUDIBLE_COVER_HOSTS)


def _admitted_cover_url(
    url: str, hosts: frozenset[str]
) -> tuple[str | None, CoverSourceState | None]:
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
    except ValueError:
        return None, CoverSourceState.UNSAFE_REMOTE
    if (
        any(ord(character) < 0x21 or ord(character) > 0x7E for character in url)
        or "\\" in url
        or parts.scheme != "https"
        or hostname is None
        or hostname not in hosts
        or parts.port not in (None, 443)
        or parts.username is not None
        or parts.password is not None
        or bool(parts.fragment)
    ):
        return None, CoverSourceState.UNSAFE_REMOTE
    return url, None


def _lane_asin(lane: MetadataDiscoveryLane) -> str:
    """Return one ASIN lane's canonical identifier, which the lane guarantees."""
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
    """Read Audible's status vocabulary off one response.

    429 is stated explicitly because the session treats an unrecognised status
    as terminal, and a throttled adapter that never retries reads as an outage.
    Only the `Retry-After` value is projected out of the headers; the header
    object itself is never retained.
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


def _lookup_product(
    payload: dict[str, Any], schema_rejections: Callable[[int], None]
) -> dict[str, Any]:
    schema_rejections(len(set(payload) - _LOOKUP_TOP_LEVEL))
    product = payload.get("product")
    if type(product) is not dict:
        raise ValueError("provider product is malformed")
    return product


def _candidates(
    provider: str,
    payload: dict[str, Any],
    local_claims: tuple[LocalClaim, ...],
    *,
    marketplace: str,
    schema_rejections: Callable[[int], None],
) -> tuple[ProviderCandidate, ...]:
    schema_rejections(len(set(payload) - _SEARCH_TOP_LEVEL))
    products = payload.get("products", [])
    if type(products) is not list or len(products) > _MAX_RETURNED_RESULTS:
        raise ValueError("provider candidate list is malformed")
    admitted = [
        _candidate(
            provider,
            product,
            local_claims,
            marketplace=marketplace,
            schema_rejections=schema_rejections,
        )
        for product in products
    ]
    identities = [value.identity for value in admitted]
    if len(set(identities)) != len(identities):
        raise ValueError("provider candidates are duplicated")
    return tuple(sorted(admitted, key=lambda value: value.identity))


def _candidate(
    provider: str,
    product: object,
    local_claims: tuple[LocalClaim, ...],
    *,
    marketplace: str,
    schema_rejections: Callable[[int], None],
) -> ProviderCandidate:
    if type(product) is not dict:
        raise ValueError("provider product is malformed")
    # Required identity.  A product without a canonical ASIN names nothing that
    # can be joined, compared, or resolved again, so it fails closed rather than
    # becoming an anonymous candidate.
    asin = canonical_asin(product.get("asin"))
    if asin is None:
        raise ValueError("provider product identity is malformed")
    identity = "audible-product-" + asin

    claims: list[ProviderClaim] = []

    def add(
        kind: ClaimKind,
        value: object,
        *,
        role: str | None = None,
    ) -> None:
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
    add(ClaimKind.TITLE, product.get("title"))
    add(ClaimKind.SUBTITLE, product.get("subtitle"))
    authors = _names(product.get("authors"), schema_rejections)
    for author in authors:
        add(ClaimKind.CREATOR, author, role="author")
    if not authors:
        add(ClaimKind.CREATOR, None, role="author")
    narrators = _names(product.get("narrators"), schema_rejections)
    for narrator in narrators:
        add(ClaimKind.CONTRIBUTOR, narrator, role="narrator")
    if not narrators:
        add(ClaimKind.CONTRIBUTOR, None, role="narrator")
    series, sequence = _series(product.get("series"), schema_rejections)
    add(ClaimKind.SERIES, series)
    add(ClaimKind.SERIES_SEQUENCE, sequence)
    description, description_transformed = projected_description(
        product.get("short_description"), product.get("publisher_summary")
    )
    add(ClaimKind.DESCRIPTION, description)
    add(ClaimKind.LANGUAGE, product.get("language"))
    add(
        ClaimKind.DURATION,
        _duration(product.get("runtime_length_min"), schema_rejections),
    )

    evidence = [
        ProviderEvidence(
            ProviderEvidenceKind.EDITION_WORK_RELATION,
            "unresolved",
            ClaimScope.EDITION,
        ),
        # An ASIN without the marketplace it resolved in names no identity, so
        # the resolving code is evidence on every candidate this adapter makes.
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


def _canonical_evidence(
    evidence: list[ProviderEvidence],
) -> tuple[ProviderEvidence, ...]:
    """Order one candidate's evidence exactly as the report document requires.

    Source ordinals are allocated before canonical ordering so a repeated
    provider value stays independently attributable to this candidate.
    """
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
    """Read a contributor list, keeping every member the projection admits."""
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
    """Read the first stated series name and its sequence, or two absences."""
    if value is None:
        return None, None
    if type(value) is not list or len(value) > _MAX_VALUES:
        schema_rejections(1)
        return None, None
    for item in value:
        if type(item) is not dict:
            schema_rejections(1)
            continue
        title = item.get("title")
        if type(title) is not str:
            schema_rejections(1)
            continue
        sequence = item.get("sequence")
        if type(sequence) is int and not isinstance(sequence, bool):
            sequence = str(sequence)
        return title, sequence
    return None, None


def _duration(value: object, schema_rejections: Callable[[int], None]) -> object:
    """Turn a stated whole-minute runtime into canonical decimal seconds."""
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


AudibleCatalogPacer = ProviderRequestPacer


__all__ = [
    "AUDIBLE_CATALOG_URLS",
    "AUDIBLE_MARKETPLACE_HOSTS",
    "AudibleCatalogMetadataProvider",
    "AudibleCatalogPacer",
]
