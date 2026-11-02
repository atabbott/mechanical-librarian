"""One-request, closed-envelope Hardcover metadata observation."""

from __future__ import annotations

import json
import math
import random
import time
import unicodedata
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, cast
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
from mechanical_librarian.adapters.providers.visible_text import stated_description
from mechanical_librarian.application.failure_diagnostics import (
    provider_attempt_diagnostics,
)
from mechanical_librarian.application.metadata_report import (
    MetadataDiscoveryPlan,
    MetadataLookup,
    ProviderCoverDescriptor,
    ProviderFailureSnapshot,
    ProviderLaneResult,
    ProviderResult,
    identifier_suppresses_discovery,
    merge_provider_cover_descriptors,
    provider_discovery_lane_order,
    provider_isbn_applicability,
    provider_observation,
    qualify_provider_observation,
)
from mechanical_librarian.application.provider_evidence import (
    ProviderOperationalEvidence,
)
from mechanical_librarian.domain.audiobook_corroboration import (
    RUNTIME_BAND_TIGHT_SECONDS,
    local_claim_runtime_seconds,
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
from mechanical_librarian.domain.evidence_bounds import (
    EvidenceValueError,
    validate_projection_text,
    validate_role_projection,
)
from mechanical_librarian.domain.failure_diagnostics import (
    MAX_DIAGNOSTIC_COUNT,
    FailureCategory,
    FailureComponent,
    FailureDiagnostic,
    FailureStage,
)
from mechanical_librarian.domain.metadata_report import (
    MAX_AUDIO_EDITIONS,
    MAX_CANDIDATES,
    MAX_CONTRIBUTIONS,
    MAX_DESCRIPTION_CHARACTERS,
    MAX_SCALAR_CHARACTERS,
    MAX_SERIES,
    MAX_TAGS,
    ClaimKind,
    ClaimScope,
    EditionEnrichmentOverflow,
    FieldState,
    LocalClaim,
    ProviderCandidate,
    ProviderClaim,
    ProviderEvidence,
    ProviderEvidenceKind,
    ProviderIdentityBinding,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    canonical_asin,
    canonical_json,
    comma_inverted_name,
    comparison_links,
    finalize_candidate_comparisons,
    isbn_family,
    normalize_date_value,
    normalize_title,
    validate_observed_title,
)
from mechanical_librarian.domain.work_identity import (
    WorkDispatchLineage,
    WorkDispatchRung,
    WorkQuery,
    narrowing_work_query,
    work_query_ladder,
)

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
_WORK_NARROW_QUERY = _WORK_QUERY.replace(
    "$authors:[String!]!", "$author:String!"
).replace("_in:$authors", "_eq:$author")
_QUERY = _ISBN_QUERY
# One admitted book's audio-format editions (#962, ADR-0089). The projection is
# closed: edition id, book id, ASIN, audio runtime and the credited people.
_AUDIO_EDITION_PAGE = 100
_AUDIO_EDITION_PAGES = 2
_AUDIO_EDITION_BOOKS = 2
_AUDIO_READING_FORMAT = 2
_MAX_AUDIO_SECONDS = 604_800
_AUDIO_EDITIONS_QUERY = (
    "query AudioEditions($book:Int!,$offset:Int!){"
    f"editions(where:{{book_id:{{_eq:$book}},reading_format_id:{{_eq:{_AUDIO_READING_FORMAT}}}}},"
    f"order_by:{{id:asc}},limit:{_AUDIO_EDITION_PAGE},offset:$offset)"
    "{id book_id asin audio_seconds contributions(order_by:{id:asc},limit:17)"
    "{id contribution author{id name}}}}"
)


class _OptionalRejections:
    """One bounded, payload-free count for optional projection rejections."""

    def __init__(self) -> None:
        self.count = 0

    def reject(self) -> None:
        self.count = min(MAX_DIAGNOSTIC_COUNT, self.count + 1)


HardcoverPacer = ProviderRequestPacer


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
        bounds = provider_bounds(
            # Clamp only a value the bounds will accept; anything else reaches
            # them unchanged and is refused there.
            min(maximum_bytes, MAX_RESPONSE_BYTES)
            if type(maximum_bytes) is int
            else maximum_bytes,
            timeout_seconds,
            attempts_per_request=_MAX_ATTEMPTS_PER_QUERY,
            attempts_per_observation=_MAX_ATTEMPTS_PER_OBSERVATION,
            aggregate_multiple=9,
            aggregate_ceiling_bytes=MAX_AGGREGATE_RESPONSE_BYTES,
        )
        self._name, self._client = name, client
        self._session = BoundedProviderSession(
            name,
            client,
            bounds,
            _DiscoveryRequestLedger("hardcover", HARDCOVER_SCHEMA_COMMIT),
            pacer=pacer,
            clocks=ProviderClocks(clock, wall_clock, sleep, jitter),
            name_response_failures=True,
            refuse_empty_success=True,
        )
        self._called = False
        self._schema_rejections = 0
        self._optional_rejections = _OptionalRejections()
        self._cover_descriptors: tuple[ProviderCoverDescriptor, ...] = ()

    # The response budget is the session's own state.  It stays readable and
    # settable under its adapter-local name so a caller can position an
    # observation at the bound without reaching past the adapter.
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
        plan: MetadataDiscoveryPlan | MetadataLookup,
        *,
        deadline: float | None = None,
    ) -> ProviderObservation:
        self._schema_rejections = 0
        self._optional_rejections = _OptionalRejections()
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
            ledger = _DiscoveryRequestLedger("hardcover", HARDCOVER_SCHEMA_COMMIT)
            projection = (
                _ISBN_QUERY if plan.kind is ProviderQueryKind.ISBN else _WORK_QUERY
            )
            ledger.register(
                kind=plan.kind.value,
                matched_family=plan.matched_family,
                title=plan.title,
                authors=plan.authors,
                projection=projection,
            )
            self._session.ledger = ledger
            with provider_client_lifetime(self._client):
                value, _ = ledger.resolve(
                    kind=plan.kind.value,
                    matched_family=plan.matched_family,
                    title=plan.title,
                    authors=plan.authors,
                    projection=projection,
                    execute=lambda: self._observe_lookup(plan, deadline=deadline),
                )
                return replace(value, request_accounting=ledger.accounting())
        if type(plan) is not MetadataDiscoveryPlan:
            raise ValueError("provider lookup is invalid")
        if plan.overflow:
            self._client.close()
            return ProviderObservation(self._name, ProviderStatus.QUERY_LIMIT_EXCEEDED)
        # Only ISBN and work lanes are served here: an ASIN lane names a product
        # this provider has no lookup for, so a plan made only of ASIN lanes
        # names nothing it can be asked and sends no request (#1306).
        served = replace(
            plan,
            lanes=tuple(
                lane
                for lane in plan.lanes
                if lane.kind in {ProviderQueryKind.ISBN, ProviderQueryKind.WORK}
            ),
        )
        if not served.lanes:
            self._client.close()
            return ProviderObservation(self._name, ProviderStatus.INSUFFICIENT_INPUT)
        results: list[ProviderLaneResult] = []
        ledger = _DiscoveryRequestLedger("hardcover", HARDCOVER_SCHEMA_COMMIT)
        for lane in served.lanes:
            ledger.register(
                kind=lane.kind.value,
                matched_family=lane.matched_family,
                title=lane.title,
                authors=lane.authors,
                projection=(
                    _ISBN_QUERY if lane.kind is ProviderQueryKind.ISBN else _WORK_QUERY
                ),
            )
        self._session.ledger = ledger
        descriptors: tuple[ProviderCoverDescriptor, ...] = ()
        with provider_client_lifetime(self._client):
            for lane in provider_discovery_lane_order(served):
                if identifier_suppresses_discovery(served, results, lane):
                    ledger.suppress(
                        kind=lane.kind.value,
                        matched_family=lane.matched_family,
                        title=lane.title,
                        authors=lane.authors,
                        projection=_WORK_QUERY,
                    )
                    continue
                self._cover_descriptors = ()
                lookup = MetadataLookup(
                    lane.kind,
                    plan.local_claims,
                    lane.title,
                    lane.authors,
                    lane.matched_family,
                    audio=plan.audio,
                    work_title=plan.work_title,
                    work_authors=plan.work_authors,
                )
                attempts_before = len(self._session.attempt_records)
                incomplete_causes_before = len(self._session.incomplete_causes)
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
                    execute=lambda: self._observe_lookup(lookup, deadline=deadline),  # noqa: B023 - `ledger.resolve` calls `execute` before it returns, so the lambda only ever sees this lane's lookup
                )
                observation = qualify_provider_observation(physical, lookup)
                audio_editions = self._audio_editions(
                    observation, lookup, deadline=deadline
                )
                diagnostics = provider_attempt_diagnostics(
                    self._session.attempt_records[attempts_before:],
                    status=observation.status.value,
                    incomplete_causes=self._session.incomplete_causes[
                        incomplete_causes_before:
                    ],
                )
                results.append(
                    ProviderLaneResult(
                        lane,
                        observation.status,
                        observation.candidates,
                        observation.accessed_at,
                        observation.edition_enrichment_overflow,
                        diagnostics,
                        observation.dispatch_lineage,
                        observation.transformation_lineage,
                        audio_editions=audio_editions,
                    )
                )
                merged = merge_provider_cover_descriptors(
                    descriptors,
                    (
                        self._cover_descriptors
                        if observation.status is ProviderStatus.CANDIDATES
                        else ()
                    ),
                )
                if merged is not None:
                    descriptors = merged
        self._cover_descriptors = descriptors
        return provider_observation(
            self._name,
            tuple(results),
            diagnostics=self._schema_diagnostics(),
            request_accounting=ledger.accounting(),
        )

    def _observe_lookup(
        self,
        lookup: MetadataLookup,
        *,
        deadline: float | None,
        _shaped: WorkQuery | None = None,
    ) -> ProviderObservation:
        if lookup.kind is ProviderQueryKind.WORK and _shaped is None:
            if lookup.title is None or not lookup.authors:
                return ProviderObservation(
                    self._name,
                    ProviderStatus.INSUFFICIENT_INPUT,
                    query_kind=lookup.kind,
                )
            last = ProviderObservation(
                self._name,
                ProviderStatus.NO_MATCH,
                query_kind=lookup.kind,
            )
            work_queries = work_query_ladder(
                lookup.title, lookup.authors, audiobook=lookup.audio is not None
            )
            if not work_queries:
                return ProviderObservation(
                    self._name,
                    ProviderStatus.INSUFFICIENT_INPUT,
                    query_kind=lookup.kind,
                )
            for work_query in work_queries:
                shaped_lookup = replace(
                    lookup,
                    title=work_query.title,
                    authors=work_query.authors,
                )
                last = self._observe_lookup(
                    shaped_lookup,
                    deadline=deadline,
                    _shaped=work_query,
                )
                if lookup.audio is not None:
                    # The bounded book set is the complete work-only response;
                    # neither overflow nor dissent authorizes a narrowing retry.
                    return last
                if last.status is ProviderStatus.CANDIDATES:
                    return last
                if last.status is ProviderStatus.CANDIDATE_LIMIT_EXCEEDED:
                    narrowed = narrowing_work_query(work_query)
                    return self._observe_lookup(
                        replace(
                            lookup,
                            title=narrowed.title,
                            authors=narrowed.authors,
                        ),
                        deadline=deadline,
                        _shaped=narrowed,
                    )
                if last.status is not ProviderStatus.NO_MATCH:
                    return last
            return last
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
                            and isbn_family(claim.canonical) == lookup.matched_family
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
            body: dict[str, object] = {
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
            is_narrowed = (
                _shaped is not None and _shaped.candidate_limit_narrowing_retry
            )
            body = {
                "operationName": "MetadataEvidenceByWork",
                "query": _WORK_NARROW_QUERY if is_narrowed else _WORK_QUERY,
                "variables": {
                    "title": lookup.title,
                    **(
                        {"author": lookup.authors[0]}
                        if is_narrowed
                        else {"authors": list(lookup.authors)}
                    ),
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
            edition_enrichment_overflow: EditionEnrichmentOverflow | None = None,
        ) -> ProviderObservation:
            return ProviderObservation(
                self._name,
                status,
                references,
                candidates,
                lookup.kind,
                accessed_at=self._session.accessed_at,
                diagnostics=self._schema_diagnostics(),
                edition_enrichment_overflow=edition_enrichment_overflow,
                dispatch_lineage=(
                    WorkDispatchLineage.from_query(_shaped)
                    if status
                    in {
                        ProviderStatus.CANDIDATES,
                        ProviderStatus.CANDIDATE_LIMIT_EXCEEDED,
                    }
                    and _shaped is not None
                    else None
                ),
            )

        try:
            payload = self._request(
                body,
                deadline=deadline,
                query_retry=(
                    _shaped is not None
                    and (
                        _shaped.rung is not WorkDispatchRung.NORMALIZED
                        or _shaped.candidate_limit_narrowing_retry
                    )
                ),
            )
        except ProviderFailure as error:
            return result(error.status)
        try:
            if _has_graphql_errors(payload):
                self._session.mark_response_attempt("provider_declared_error")
                return result(ProviderStatus.UNAVAILABLE)
            work_only = (
                lookup.kind is ProviderQueryKind.WORK and lookup.audio is not None
            )
            overflow = (
                None
                if work_only
                else _parse_edition_enrichment_overflow(payload, self._name)
            )
            if overflow is not None:
                self._session.mark_response_attempt("candidate_limit_exceeded")
                return result(
                    ProviderStatus.CANDIDATE_LIMIT_EXCEEDED,
                    edition_enrichment_overflow=overflow,
                )
            candidates = _parse_candidates(
                payload,
                self._name,
                local_claims,
                self._optional_rejections,
                work_only=work_only,
            )
            self._reject_schema_members(_optional_projection_rejections(payload))
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
            self._session.mark_response_attempt("candidate_limit_exceeded")
            return result(ProviderStatus.CANDIDATE_LIMIT_EXCEEDED)
        except (
            ValueError,
            TypeError,
            UnicodeError,
            RecursionError,
            json.JSONDecodeError,
        ):
            self._session.mark_response_attempt("response_malformed")
            return result(ProviderStatus.MALFORMED)
        try:
            self._cover_descriptors = _parse_cover_descriptors(
                payload,
                self._name,
                lookup.kind,
            )
        except (
            ValueError,
            TypeError,
            UnicodeError,
            RecursionError,
            json.JSONDecodeError,
        ):
            self._cover_descriptors = ()
        observation = result(
            ProviderStatus.CANDIDATES if candidates else ProviderStatus.NO_MATCH,
            candidates,
        )
        return observation

    def _audio_editions(
        self,
        observation: ProviderObservation,
        lookup: MetadataLookup,
        *,
        deadline: float | None,
    ) -> tuple[ProviderCandidate, ...]:
        """Read the admitted books' audio editions that witness this recording.

        Only an audiobook WORK lane whose qualified answer admitted one or two
        Hardcover books asks, and only when the recording states one running
        time. An edition is kept when it credits a narrator and its runtime is
        inside the tight band of the recording's (#962, ADR-0089). Any failed
        or malformed page ends the fan-out with no editions: the admitted work
        stands, and a failed read is never a statement that no edition exists.
        """
        local_runtime = local_claim_runtime_seconds(lookup.local_claims)
        books = tuple(
            candidate
            for candidate in observation.candidates
            if candidate.identity == candidate.work_identity
            and candidate.identity.startswith("hardcover-work-")
        )
        if (
            lookup.kind is not ProviderQueryKind.WORK
            or lookup.audio is None
            or observation.status is not ProviderStatus.CANDIDATES
            or local_runtime is None
            or not books
            or len(books) > _AUDIO_EDITION_BOOKS
            or len(books) != len(observation.candidates)
        ):
            return ()
        editions: list[ProviderCandidate] = []
        for book in books:
            book_id = int(book.identity.removeprefix("hardcover-work-"))
            rows: list[dict[str, Any]] = []
            for page in range(_AUDIO_EDITION_PAGES + 1):
                if page == _AUDIO_EDITION_PAGES:
                    # More rows than the bounded pages hold: no prefix is a
                    # fair sample, and a witness over only the other book
                    # would judge a recording this one may contradict.
                    return ()
                offset = page * _AUDIO_EDITION_PAGE

                def execute(
                    book_id: int = book_id, offset: int = offset
                ) -> _AudioEditionPage:
                    try:
                        payload = self._request(
                            {
                                "operationName": "AudioEditions",
                                "query": _AUDIO_EDITIONS_QUERY,
                                "variables": {"book": book_id, "offset": offset},
                            },
                            deadline=deadline,
                        )
                    except ProviderFailure as error:
                        return _AudioEditionPage((), error.status)
                    try:
                        if _has_graphql_errors(payload):
                            self._session.mark_response_attempt(
                                "provider_declared_error"
                            )
                            return _AudioEditionPage((), ProviderStatus.UNAVAILABLE)
                        return _AudioEditionPage(
                            _parse_audio_edition_rows(payload, book_id), None
                        )
                    except (
                        ValueError,
                        TypeError,
                        UnicodeError,
                        RecursionError,
                        json.JSONDecodeError,
                    ):
                        self._session.mark_response_attempt("response_malformed")
                        return _AudioEditionPage((), ProviderStatus.MALFORMED)

                value, _replayed = self._session.ledger.resolve_detail(
                    f"audio-editions/{book_id}/{offset}", execute=execute
                )
                if value.status is not None:
                    return ()
                rows.extend(value.rows)
                if len(value.rows) < _AUDIO_EDITION_PAGE:
                    break
            if len({row["id"] for row in rows}) != len(rows):
                return ()
            for row in rows:
                edition = _audio_edition(
                    self._name, book.identity, row, lookup.local_claims, local_runtime
                )
                if edition is not None:
                    editions.append(edition)
        if len(editions) > MAX_AUDIO_EDITIONS:
            return ()
        return tuple(sorted(editions, key=lambda value: value.identity))

    def _reject_schema_members(self, count: int) -> None:
        if count > 0:
            self._schema_rejections = min(
                MAX_DIAGNOSTIC_COUNT, self._schema_rejections + count
            )

    def _schema_diagnostics(self) -> tuple[FailureDiagnostic, ...]:
        count = min(
            MAX_DIAGNOSTIC_COUNT,
            self._schema_rejections + self._optional_rejections.count,
        )
        return (
            ()
            if not count
            else (
                FailureDiagnostic(
                    FailureCategory.SCHEMA_CONSTRUCTION,
                    FailureComponent.PROVIDER,
                    FailureStage.SCHEMA,
                    count=count,
                ),
            )
        )

    def _request(
        self,
        body: dict[str, object],
        *,
        deadline: float | None,
        query_retry: bool = False,
    ) -> bytes:
        return self._session.request(
            HARDCOVER_URL,
            method="POST",
            content=canonical_json(body).encode(),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Accept-Encoding": "identity",
            },
            deadline=deadline,
            query_retry=query_retry,
            classify=_classify,
            read=_raw,
        )


def _classify(facts: ProviderResponseFacts) -> ProviderFailure:
    """Map Hardcover's non-success statuses; the session owns 503."""
    if facts.status_code == 401:
        return ProviderFailure(ProviderStatus.AUTHENTICATION, category="http_401")
    if facts.status_code == 403:
        return ProviderFailure(ProviderStatus.AUTHORIZATION, category="http_403")
    if facts.status_code == 429:
        throttle = _hardcover_throttle(facts.headers, now=facts.now)
        category, delay = (
            (
                "http_429",
                bounded_retry_after(facts.headers.get("retry-after"), now=facts.now),
            )
            if throttle is None
            else throttle
        )
        return ProviderFailure(
            ProviderStatus.RATE_LIMITED,
            category=category,
            retryable=delay is not None,
            retry_after=delay,
        )
    if facts.status_code in {500, 502, 504}:
        return ProviderFailure(
            ProviderStatus.UNAVAILABLE,
            category=f"http_{facts.status_code}",
            retryable=True,
        )
    return ProviderFailure(ProviderStatus.UNAVAILABLE, category="http_other")


@dataclass(frozen=True, slots=True)
class _AudioEditionPage:
    rows: tuple[dict[str, Any], ...]
    status: ProviderStatus | None


def _parse_audio_edition_rows(
    payload: bytes, book_id: int
) -> tuple[dict[str, Any], ...]:
    """Read one page of a book's audio editions, refusing any foreign row."""
    source = json.loads(
        payload.decode("utf-8", "strict"),
        object_pairs_hook=_unique_object,
        parse_constant=_reject_number,
        parse_float=Decimal,
    )
    data = _closed(_closed(source, {"data"})["data"], {"editions"})
    rows, overflow = _bounded_list(
        data["editions"],
        _AUDIO_EDITION_PAGE,
        {"id", "book_id", "asin", "audio_seconds", "contributions"},
    )
    if overflow:
        raise ValueError("provider audio editions overflow")
    for row in rows:
        _identifier(row["id"])
        if _identifier(row["book_id"]) != book_id:
            raise ValueError("provider audio edition names another book")
    return tuple(rows)


def _audio_edition(
    provider: str,
    work_identity: str,
    row: dict[str, Any],
    local: tuple[LocalClaim, ...],
    local_runtime: int,
) -> ProviderCandidate | None:
    """One audio edition that witnesses the recording, or ``None``.

    It needs a stated runtime inside the tight band of the recording's and at
    least one valid narrator credit. Only its ASIN, runtime and narrators are
    kept; nothing else of the row is read.
    """
    seconds = row["audio_seconds"]
    if (
        type(seconds) is not int
        or isinstance(seconds, bool)
        or not 1 <= seconds <= _MAX_AUDIO_SECONDS
        or abs(seconds - local_runtime) > RUNTIME_BAND_TIGHT_SECONDS
    ):
        return None
    identity = f"hardcover-edition-{row['id']:010d}"
    claims: list[ProviderClaim] = []
    # An audio edition's ASIN may be ISBN-shaped: Audible gives some of its own
    # productions an ISBN-10 as their ASIN, which the work-level rule refuses.
    asin = canonical_asin(row["asin"])
    if asin is not None:
        fields: dict[str, Any] = {
            "provider": provider,
            "candidate": identity,
            "kind": ClaimKind.ASIN,
            "value": asin,
            "state": FieldState.VALID,
            "scope": ClaimScope.EDITION,
            "applicability": "not_applicable",
            "raw_order": len(claims),
        }
        bare = ProviderClaim.create(**fields)
        claims.append(
            ProviderClaim.create(**fields, links=comparison_links(bare, local))
        )
    claims.append(
        _claim(
            provider,
            identity,
            ClaimKind.DURATION,
            f"{seconds}.000",
            ClaimScope.EDITION,
            "not_applicable",
            None,
            local,
            len(claims),
        )
    )
    contributions, overflow, _invalid = _optional_bounded_list(
        row["contributions"], MAX_CONTRIBUTIONS, {"id", "contribution", "author"}
    )
    narrators: list[str] = []
    for item in () if overflow else contributions:
        try:
            role = validate_role_projection(item["contribution"]).casefold()
        except (EvidenceValueError, TypeError):
            continue
        author = _optional_mapping(item["author"])
        if role != "narrator" or author is None:
            continue
        state, name = _field(ClaimKind.CONTRIBUTOR, author.get("name"))
        if state is FieldState.VALID and name is not None and name not in narrators:
            narrators.append(name)
    if not narrators:
        return None
    for name in narrators:
        claims.append(
            _claim(
                provider,
                identity,
                ClaimKind.CONTRIBUTOR,
                name,
                ClaimScope.WORK,
                "not_applicable",
                "narrator",
                local,
                len(claims),
            )
        )
    return finalize_candidate_comparisons(
        ProviderCandidate(identity, work_identity, tuple(claims)), local, provider
    )


def _raw(payload: bytes) -> bytes:
    """Hand the success body to the GraphQL envelope parser unread."""
    return payload


def _hardcover_throttle(
    headers: httpx.Headers, *, now: float
) -> tuple[str, int] | None:
    standing = headers.get("ratelimit")
    if standing is not None:
        entries = _rate_limit_standing(standing)
        if entries is None or set(entries) != {"daily", "rate"}:
            return None
        daily = entries["daily"]
        rate = entries["rate"]
        if daily["r"] == 0 and 1 <= daily["t"] <= 86_400:
            return "quota_exhausted", daily["t"]
        if daily["r"] > 0 and rate["r"] == 0 and 1 <= rate["t"] <= 60:
            return "burst_throttled", rate["t"]
        return None
    daily_remaining = _decimal_header(headers.get("x-ratelimit-daily-remaining"))
    rate_remaining = _decimal_header(headers.get("x-ratelimit-remaining"))
    if daily_remaining is None or rate_remaining is None:
        return None
    if daily_remaining == 0:
        delay = _reset_delay(headers.get("x-ratelimit-daily-reset"), now=now)
        return (
            ("quota_exhausted", delay)
            if delay is not None and delay <= 86_400
            else None
        )
    if rate_remaining == 0:
        delay = _reset_delay(headers.get("x-ratelimit-reset"), now=now)
        return ("burst_throttled", delay) if delay is not None and delay <= 60 else None
    return None


def _rate_limit_standing(value: str) -> dict[str, dict[str, int]] | None:
    if type(value) is not str or not 1 <= len(value) <= 512:
        return None
    result: dict[str, dict[str, int]] = {}
    for index, raw_entry in enumerate(value.split(",")):
        parts = tuple(part.strip() for part in raw_entry.split(";"))
        if (
            len(parts) != 3
            or len(parts[0]) < 3
            or parts[0][0] != '"'
            or parts[0][-1] != '"'
        ):
            return None
        label = parts[0][1:-1]
        attributes: dict[str, int] = {}
        for part in parts[1:]:
            key, separator, raw = part.partition("=")
            parsed = _decimal_header(raw)
            if separator != "=" or key not in {"r", "t"} or parsed is None:
                return None
            attributes[key] = parsed
        if set(attributes) != {"r", "t"}:
            return None
        key = "daily" if label == "daily" else "rate" if index == 0 else ""
        if not key or key in result:
            return None
        result[key] = attributes
    return result


def _decimal_header(value: str | None) -> int | None:
    if value is None:
        return None
    stripped = value.strip()
    if not 1 <= len(stripped) <= 16 or not stripped.isascii() or not stripped.isdigit():
        return None
    parsed = int(stripped)
    return parsed if parsed <= 2_147_483_647 else None


def _reset_delay(value: str | None, *, now: float) -> int | None:
    reset = _decimal_header(value)
    if (
        reset is None
        or type(now) not in {int, float}
        or isinstance(now, bool)
        or not math.isfinite(now)
        or reset <= now
    ):
        return None
    return math.ceil(reset - now)


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
    if type(errors) is not list or not errors or len(errors) > MAX_GRAPHQL_ERRORS:
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
            if type(locations) is not list or len(locations) > MAX_GRAPHQL_ERRORS:
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
    rejected: _OptionalRejections | None = None,
    *,
    work_only: bool = False,
) -> tuple[ProviderCandidate, ...]:
    rejected = rejected or _OptionalRejections()
    source = json.loads(
        payload.decode("utf-8", "strict"),
        object_pairs_hook=_unique_object,
        parse_constant=_reject_number,
        parse_float=Decimal,
    )
    root = _closed(source, {"data"})
    data = _closed(root["data"], {"editions", "books", "tags"})
    rows, books, tags = data["editions"], data["books"], data["tags"]
    if type(rows) is not list or type(books) is not list:
        raise ValueError("provider rows invalid")
    if len(rows) > MAX_CANDIDATES + int(work_only) or len(books) > MAX_CANDIDATES:
        raise _CandidateLimit
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
    # Tags are optional enrichment.  A broken taxonomy row or link must not
    # invalidate the edition/work identity and independently valid fields.
    taxonomy_unavailable = False
    taxonomy_oversized = False
    indexed_tags: dict[int, dict[str, Any]] = {}
    ambiguous_tag_ids: set[int] = set()
    if type(tags) is not list:
        taxonomy_unavailable = True
        rejected.reject()
    elif len(tags) > _MAX_TAG_ROWS:
        taxonomy_oversized = True
        rejected.reject()
    else:
        for raw_tag in tags:
            try:
                tag_id = _identifier(
                    raw_tag.get("id") if type(raw_tag) is dict else None
                )
            except (TypeError, ValueError):
                rejected.reject()
                continue
            try:
                if tag_id in indexed_tags or tag_id in ambiguous_tag_ids:
                    indexed_tags.pop(tag_id, None)
                    ambiguous_tag_ids.add(tag_id)
                    raise ValueError("duplicate tag")
                tag = _closed(raw_tag, {"id", "tag", "tag_category"})
                _text(tag["tag"], MAX_SCALAR_CHARACTERS)
                category = _closed(
                    tag["tag_category"],
                    {"id", "category"},
                )
                _identifier(category["id"])
                _text(category["category"], MAX_SCALAR_CHARACTERS)
            except (TypeError, ValueError):
                indexed_tags.pop(tag_id, None)
                ambiguous_tag_ids.add(tag_id)
                rejected.reject()
                continue
            indexed_tags[tag_id] = tag
    indexed: dict[int, dict[str, Any]] = {}
    for raw_book in books:
        # Only the work identity is part of the anchor.  Every other work
        # member is independently projected below.
        book = _anchored_projection(raw_book, {"id"}, work_keys)
        book = {key: book.get(key) for key in work_keys}
        raw_taggings = book["taggings"]
        taggings: list[dict[str, Any]] = []
        book_taxonomy_issue = taxonomy_unavailable
        book_taxonomy_oversized = taxonomy_oversized
        if type(raw_taggings) is not list:
            book_taxonomy_issue = True
            rejected.reject()
        elif len(raw_taggings) > MAX_TAGS:
            book_taxonomy_oversized = True
            rejected.reject()
        else:
            for raw_tagging in raw_taggings:
                try:
                    tagging = _closed(raw_tagging, {"id", "tag_id"})
                    _identifier(tagging["id"])
                    tag_id = _identifier(tagging["tag_id"])
                    tag = indexed_tags[tag_id]
                except (KeyError, TypeError, ValueError):
                    book_taxonomy_issue = True
                    rejected.reject()
                    continue
                taggings.append({"id": tagging["id"], "tag": tag})
        book = {
            **book,
            "taggings": taggings,
            "_taxonomy_issue": book_taxonomy_issue,
            "_taxonomy_oversized": book_taxonomy_oversized,
        }
        book_id = _identifier(book["id"])
        if book_id in indexed:
            raise ValueError("provider work invalid")
        # These optional values are otherwise consumed only by neutral
        # evidence and cover projections. Diagnose them here, once for the
        # source work, rather than once for every joined edition/projection.
        _field(ClaimKind.PUBLICATION_DATE, book["release_date"], rejected)
        _cover_url(book["image"], rejected)
        indexed[book_id] = book
    if len(indexed) != len(books):
        raise ValueError("provider work invalid")
    candidates: list[ProviderCandidate] = []
    used: set[int] = set()
    diagnosed_works: set[int] = set()
    edition_ids: set[int] = set()
    for row_value in rows:
        if type(row_value) is dict and "image" not in row_value:
            row_value = {**row_value, "image": None}
        row = _anchored_projection(
            row_value,
            {"id", "book_id"},
            {
                "id",
                "book_id",
                "isbn_10",
                "isbn_13",
                "asin",
                "title",
                "subtitle",
                "release_date",
                "image",
                "publisher",
                "language",
                "contributions",
            },
        )
        row = {
            key: row.get(key)
            for key in {
                "id",
                "book_id",
                "isbn_10",
                "isbn_13",
                "asin",
                "title",
                "subtitle",
                "release_date",
                "image",
                "publisher",
                "language",
                "contributions",
            }
        }
        edition_id, book_id = _identifier(row["id"]), _identifier(row["book_id"])
        if book_id not in indexed:
            raise ValueError("provider unreferenced work")
        if edition_id in edition_ids:
            raise ValueError("provider edition identity duplicated")
        edition_ids.add(edition_id)
        used.add(book_id)
        if work_only:
            continue
        _cover_url(row["image"], rejected)
        publisher = _optional_mapping(row["publisher"])
        if publisher is not None and publisher.get("parent_id") is not None:
            try:
                _identifier(publisher["parent_id"])
            except ValueError:
                rejected.reject()
        identity = f"hardcover-edition-{edition_id:010d}"
        work_identity = f"hardcover-work-{book_id:010d}"
        claims = _edition_claims(
            provider,
            identity,
            row,
            local_claims,
            rejected,
        )
        identity_bindings: list[ProviderIdentityBinding] = []
        work_rejections = rejected if book_id not in diagnosed_works else None
        claims.extend(
            _work_claims(
                provider,
                identity,
                indexed[book_id],
                local_claims,
                len(claims),
                work_rejections,
                identity_bindings,
            )
        )
        diagnosed_works.add(book_id)
        candidates.append(
            finalize_candidate_comparisons(
                ProviderCandidate(
                    identity,
                    work_identity,
                    tuple(claims),
                    _work_evidence(indexed[book_id]),
                    tuple(
                        sorted(
                            identity_bindings,
                            key=lambda value: (
                                value.scope.value,
                                value.role or "",
                                value.source_ordinal,
                                value.identity,
                                value.claim_reference,
                            ),
                        )
                    ),
                ),
                local_claims,
                provider,
            )
        )
    if work_only:
        for book_id, book in sorted(indexed.items()):
            identity = f"hardcover-work-{book_id:010d}"
            bindings: list[ProviderIdentityBinding] = []
            claims = _work_claims(
                provider, identity, book, local_claims, 0, rejected, bindings
            )
            candidates.append(
                finalize_candidate_comparisons(
                    ProviderCandidate(
                        identity,
                        identity,
                        tuple(claims),
                        _work_evidence(book),
                        tuple(
                            sorted(
                                bindings,
                                key=lambda b: (
                                    b.scope.value,
                                    b.role or "",
                                    b.source_ordinal,
                                    b.identity,
                                    b.claim_reference,
                                ),
                            )
                        ),
                    ),
                    local_claims,
                    provider,
                )
            )
    if (not work_only and used != set(indexed)) or len(
        {candidate.identity for candidate in candidates}
    ) != len(candidates):
        raise ValueError("provider join invalid")
    return tuple(sorted(candidates, key=lambda candidate: candidate.identity))


def _parse_edition_enrichment_overflow(
    payload: bytes,
    provider: str,
) -> EditionEnrichmentOverflow | None:
    """Retain one work-only witness, never an edition candidate, at the probe cap."""
    source = json.loads(
        payload.decode("utf-8", "strict"),
        object_pairs_hook=_unique_object,
        parse_constant=_reject_number,
        parse_float=Decimal,
    )
    data = _closed(_closed(source, {"data"})["data"], {"editions", "books", "tags"})
    rows, books = data["editions"], data["books"]
    if type(rows) is not list or type(books) is not list:
        raise ValueError("provider rows invalid")
    if len(rows) <= MAX_CANDIDATES:
        return None
    if len(rows) != MAX_CANDIDATES + 1 or len(books) != 1:
        raise _CandidateLimit
    book = _anchored_projection(
        books[0], {"id", "title", "contributions"}, {"id", "title", "contributions"}
    )
    book_id = _identifier(book["id"])
    edition_ids: set[int] = set()
    for value in rows:
        row = _anchored_projection(value, {"id", "book_id"}, {"id", "book_id"})
        edition_id, linked_book_id = _identifier(row["id"]), _identifier(row["book_id"])
        if linked_book_id != book_id or edition_id in edition_ids:
            raise ValueError("provider overflow linkage invalid")
        edition_ids.add(edition_id)
    work_identity = f"hardcover-work-{book_id:010d}"
    title = _claim(
        provider,
        work_identity,
        ClaimKind.TITLE,
        book["title"],
        ClaimScope.WORK,
        "not_applicable",
        None,
        (),
        0,
    )
    contributions = book["contributions"]
    if (
        type(contributions) is not list
        or not contributions
        or len(contributions) > MAX_CONTRIBUTIONS
    ):
        raise ValueError("provider overflow work is incomplete")
    contribution_ids: set[int] = set()
    author_ids: dict[int, str] = {}
    author_names: set[str] = set()
    authors: list[ProviderClaim] = []
    bindings: list[ProviderIdentityBinding] = []
    for ordinal, raw in enumerate(contributions):
        contribution = _closed(raw, {"id", "contribution", "author"})
        contribution_id = _identifier(contribution["id"])
        author = _closed(contribution["author"], {"id", "name"})
        author_id = _identifier(author["id"])
        _text(author["name"], MAX_SCALAR_CHARACTERS)
        if (
            contribution_id in contribution_ids
            or author_id in author_ids
            or normalize_title(author["name"]).casefold() in author_names
            or validate_role_projection(contribution["contribution"]).casefold()
            != "author"
        ):
            raise ValueError("provider overflow authors are contradictory")
        contribution_ids.add(contribution_id)
        author_ids[author_id] = author["name"]
        author_names.add(normalize_title(author["name"]).casefold())
        claim = _claim(
            provider,
            work_identity,
            ClaimKind.CREATOR,
            _author_name(author["name"], ()),
            ClaimScope.WORK,
            "not_applicable",
            "author",
            (),
            ordinal,
        )
        if claim.state is not FieldState.VALID:
            raise ValueError("provider overflow work is incomplete")
        authors.append(claim)
        bindings.append(
            ProviderIdentityBinding(
                claim.reference,
                f"hardcover-author-{author_id:010d}",
                ClaimScope.WORK,
                "author",
                ordinal,
            )
        )
    if title.state is not FieldState.VALID or title.value is None:
        raise ValueError("provider overflow work is incomplete")
    return EditionEnrichmentOverflow(
        work_identity, title.value, tuple(authors), tuple(bindings)
    )


def _parse_cover_descriptors(
    payload: bytes,
    provider: str,
    query_kind: ProviderQueryKind,
    rejected: _OptionalRejections | None = None,
) -> tuple[ProviderCoverDescriptor, ...]:
    rejected = rejected or _OptionalRejections()
    source = json.loads(
        payload.decode("utf-8", "strict"),
        object_pairs_hook=_unique_object,
        parse_constant=_reject_number,
        parse_float=Decimal,
    )
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
        or len(books) > MAX_CANDIDATES
    ):
        raise ValueError("provider rows invalid")
    descriptors: list[ProviderCoverDescriptor] = []
    for row_value in rows:
        if type(row_value) is dict and "image" not in row_value:
            row_value = {**row_value, "image": None}
        row = _anchored_projection(
            row_value,
            {"id", "book_id"},
            {
                "id",
                "book_id",
                "isbn_10",
                "isbn_13",
                "asin",
                "title",
                "subtitle",
                "release_date",
                "image",
                "publisher",
                "language",
                "contributions",
            },
        )
        row = {
            key: row.get(key)
            for key in {
                "id",
                "book_id",
                "isbn_10",
                "isbn_13",
                "asin",
                "title",
                "subtitle",
                "release_date",
                "image",
                "publisher",
                "language",
                "contributions",
            }
        }
        url, terminal_state = _cover_url(row["image"], rejected)
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
                    _edition_cover_context(row, rejected),
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
        book = _anchored_projection(raw_book, {"id"}, work_keys)
        book = {key: book.get(key) for key in work_keys}
        book_id = _identifier(book["id"])
        url, terminal_state = _cover_url(book["image"], rejected)
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
                    context=_work_cover_context(book, rejected),
                ),
                terminal_state=terminal_state,
            )
        )
    return tuple(descriptors)


def _cover_url(
    value: object, rejected: _OptionalRejections | None = None
) -> tuple[str | None, CoverSourceState | None]:
    try:
        image = _closed_or_none(value, {"url"})
    except (TypeError, ValueError):
        if rejected:
            rejected.reject()
        return None, CoverSourceState.UNSAFE_REMOTE
    if image is None:
        return None, None
    url = image["url"]
    if type(url) is not str or not url:
        if rejected:
            rejected.reject()
        return None, CoverSourceState.UNSAFE_REMOTE
    if len(url) > 2048:
        if rejected:
            rejected.reject()
        return None, CoverSourceState.OVERSIZED
    try:
        parts = urlsplit(url)
        _ = parts.hostname, parts.port
    except ValueError:
        parts = None
    if (
        parts is None
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in url)
        or "\\" in url
        or parts.scheme != "https"
        or not parts.hostname
        or parts.port not in (None, 443)
        or parts.username is not None
        or parts.password is not None
        or bool(parts.fragment)
    ):
        if rejected:
            rejected.reject()
        return None, CoverSourceState.UNSAFE_REMOTE
    return url, None


def _work_cover_context(
    book: dict[str, Any], rejected: _OptionalRejections | None = None
) -> tuple[CoverContext, ...]:
    if book["release_date"] is None:
        return ()
    try:
        value = _text(book["release_date"], MAX_SCALAR_CHARACTERS)
        normalize_date_value(value)
    except ValueError:
        if rejected:
            rejected.reject()
        return ()
    return (CoverContext(CoverContextField.PUBLICATION_DATE, value),)


def _edition_cover_context(
    row: dict[str, Any], rejected: _OptionalRejections | None = None
) -> tuple[CoverContext, ...]:
    values: set[CoverContext] = set()
    for key in ("isbn_10", "isbn_13"):
        if row[key] is not None:
            try:
                values.add(
                    CoverContext(
                        CoverContextField.ISBN, _text(row[key], MAX_SCALAR_CHARACTERS)
                    )
                )
            except ValueError:
                if rejected:
                    rejected.reject()
                pass
    language = _optional_mapping(row["language"], rejected)
    if language is not None:
        for key in ("code2", "code3"):
            try:
                value = _text(language.get(key), MAX_SCALAR_CHARACTERS)
            except ValueError:
                continue
            values.add(CoverContext(CoverContextField.LANGUAGE, value))
            break
    try:
        publisher = _text(
            _optional_member(row["publisher"], "name"), MAX_SCALAR_CHARACTERS
        )
    except ValueError:
        if rejected:
            rejected.reject()
        publisher = None
    if publisher is not None:
        values.add(CoverContext(CoverContextField.PUBLISHER, publisher))
    if row["release_date"] is not None:
        try:
            value = _text(row["release_date"], MAX_SCALAR_CHARACTERS)
            normalize_date_value(value)
            values.add(CoverContext(CoverContextField.PUBLICATION_DATE, value))
        except ValueError:
            if rejected:
                rejected.reject()
            pass
    return tuple(sorted(values, key=lambda value: (value.field.value, value.value)))


def _edition_claims(
    provider: str,
    identity: str,
    row: dict[str, Any],
    local: tuple[LocalClaim, ...],
    rejected: _OptionalRejections | None = None,
) -> list[ProviderClaim]:
    claims: list[ProviderClaim] = []
    isbn_applicability = tuple(
        provider_isbn_applicability(row[key], local)
        for key in ("isbn_10", "isbn_13")
        if row[key] is not None
    )
    edition_applicability = _edition_applicability(isbn_applicability)

    def add(
        kind: ClaimKind,
        value: object,
        scope: ClaimScope = ClaimScope.EDITION,
        applicability: str = "not_applicable",
    ) -> None:
        claims.append(
            _claim(
                provider,
                identity,
                kind,
                value,
                scope,
                applicability,
                None,
                local,
                len(claims),
                rejected,
            )
        )

    for key, kind in (
        ("isbn_10", ClaimKind.ISBN),
        ("isbn_13", ClaimKind.ISBN),
        ("asin", ClaimKind.ASIN),
        ("title", ClaimKind.TITLE),
        ("subtitle", ClaimKind.SUBTITLE),
        ("release_date", ClaimKind.PUBLICATION_DATE),
    ):
        add(
            kind,
            row[key],
            ClaimScope.EDITION,
            (
                provider_isbn_applicability(row[key], local)
                if kind is ClaimKind.ISBN
                else edition_applicability
                if kind in {ClaimKind.ASIN, ClaimKind.SUBTITLE}
                else "not_applicable"
            ),
        )
    add(ClaimKind.PUBLISHER, _optional_member(row["publisher"], "name", rejected))
    claims.append(
        _state_claim(
            provider,
            identity,
            ClaimKind.IMPRINT,
            FieldState.UNSUPPORTED,
            ClaimScope.EDITION,
            local,
            len(claims),
        )
    )
    language = _optional_mapping(row["language"], rejected)
    # Code siblings are optional alternatives: a bad code2 cannot discard a
    # valid code3 (or the inverse).
    language_value = None
    if language is not None:
        for key in ("code2", "code3"):
            if language.get(key) is not None:
                state, candidate = _field(
                    ClaimKind.LANGUAGE, language.get(key), rejected
                )
                if state is FieldState.VALID:
                    language_value = candidate
                    break
    add(ClaimKind.LANGUAGE, language_value)
    claims.extend(
        _contributions(
            provider,
            identity,
            row["contributions"],
            ClaimScope.EDITION,
            local,
            len(claims),
            rejected,
        )
    )
    return claims


def _edition_applicability(values: tuple[str, ...]) -> str:
    """Reduce only the exact edition's ISBN lanes without strengthening doubt."""
    if not values or "unknown" in values:
        return "unknown"
    if "ambiguous" in values or len(set(values)) != 1:
        return "ambiguous"
    return values[0]


def _work_claims(
    provider: str,
    identity: str,
    book: dict[str, Any],
    local: tuple[LocalClaim, ...],
    offset: int,
    rejected: _OptionalRejections | None = None,
    identity_bindings: list[ProviderIdentityBinding] | None = None,
) -> list[ProviderClaim]:
    # `series` names the bounded series list and then, inside the loop over
    # that list, the closed per-item series object.
    series: list[dict[str, Any]] | dict[str, Any] | None
    claims = [
        _claim(
            provider,
            identity,
            ClaimKind.TITLE,
            book["title"],
            ClaimScope.WORK,
            "not_applicable",
            None,
            local,
            offset,
            rejected,
        ),
        _claim(
            provider,
            identity,
            ClaimKind.DESCRIPTION,
            book["description"],
            ClaimScope.WORK,
            "not_applicable",
            None,
            local,
            offset + 1,
            rejected,
        ),
    ]
    claims.extend(
        _contributions(
            provider,
            identity,
            book["contributions"],
            ClaimScope.WORK,
            local,
            len(claims) + offset,
            rejected,
            identity_bindings,
        )
    )
    series, series_overflow, series_invalid = _optional_bounded_list(
        book["book_series"], MAX_SERIES, {"id", "position", "series"}, rejected
    )
    if series_overflow:
        claims.extend(
            (
                _state_claim(
                    provider,
                    identity,
                    ClaimKind.SERIES,
                    FieldState.OVERSIZED,
                    ClaimScope.WORK,
                    local,
                    len(claims) + offset,
                ),
                _state_claim(
                    provider,
                    identity,
                    ClaimKind.SERIES_SEQUENCE,
                    FieldState.OVERSIZED,
                    ClaimScope.WORK,
                    local,
                    len(claims) + offset + 1,
                ),
            )
        )
        series = []
    for item in series:
        series = _optional_closed(item["series"], {"id", "name"}, rejected)
        claims.append(
            _claim(
                provider,
                identity,
                ClaimKind.SERIES,
                None if series is None else series["name"],
                ClaimScope.WORK,
                "not_applicable",
                None,
                local,
                len(claims) + offset,
                rejected,
            )
        )
        claims.append(
            _claim(
                provider,
                identity,
                ClaimKind.SERIES_SEQUENCE,
                item["position"],
                ClaimScope.WORK,
                "not_applicable",
                None,
                local,
                len(claims) + offset,
                rejected,
            )
        )
    if series_invalid:
        claims.extend(
            _state_claim(
                provider,
                identity,
                kind,
                FieldState.MALFORMED,
                ClaimScope.WORK,
                local,
                len(claims) + offset + index,
            )
            for index, kind in enumerate((ClaimKind.SERIES, ClaimKind.SERIES_SEQUENCE))
        )
    if book.get("_taxonomy_oversized"):
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
    if book.get("_taxonomy_issue"):
        claims.extend(
            _state_claim(
                provider,
                identity,
                kind,
                FieldState.MALFORMED,
                ClaimScope.WORK,
                local,
                len(claims) + offset + index,
            )
            for index, kind in enumerate((ClaimKind.GENRE, ClaimKind.TAG))
        )
    taggings, taggings_overflow, taggings_invalid = _optional_bounded_list(
        book["taggings"], MAX_TAGS, {"id", "tag"}, rejected
    )
    taxonomy: list[tuple[str, str]] = []
    categorized: list[tuple[ClaimKind, str]] = []
    seen: dict[str, ClaimKind] = {}
    malformed_taxonomy = False
    for item in taggings:
        tag = _optional_closed(item["tag"], {"id", "tag", "tag_category"}, rejected)
        if tag is None:
            malformed_taxonomy = True
            continue
        try:
            _identifier(tag["id"])
        except ValueError:
            if rejected:
                rejected.reject()
            malformed_taxonomy = True
            continue
        category = _optional_closed(tag["tag_category"], {"id", "category"}, rejected)
        if category is None:
            malformed_taxonomy = True
            continue
        try:
            _identifier(category["id"])
            name = _text(tag["tag"], MAX_SCALAR_CHARACTERS)
            category_name = _text(category["category"], MAX_SCALAR_CHARACTERS)
        except ValueError:
            if rejected:
                rejected.reject()
            malformed_taxonomy = True
            continue
        taxonomy_identity = " ".join(
            unicodedata.normalize("NFC", f"{category_name}\0{name}").split()
        ).casefold()
        taxonomy.append((category_name, name))
        if taxonomy_identity not in seen:
            seen[taxonomy_identity] = ClaimKind.TAG
            if category_name == "Genre":
                categorized.append((ClaimKind.GENRE, name))
            elif category_name in {"Mood", "ContentWarning"}:
                categorized.append((ClaimKind.TAG, name))
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
    if malformed_taxonomy or taggings_invalid:
        claims.extend(
            _state_claim(
                provider,
                identity,
                kind,
                FieldState.MALFORMED,
                ClaimScope.WORK,
                local,
                len(claims) + offset + index,
            )
            for index, kind in enumerate((ClaimKind.GENRE, ClaimKind.TAG))
        )
    terminal = {
        claim.kind
        for claim in claims
        if claim.kind in {ClaimKind.GENRE, ClaimKind.TAG}
        and claim.state
        in {FieldState.VALID, FieldState.MALFORMED, FieldState.OVERSIZED}
    }
    for kind in (ClaimKind.GENRE, ClaimKind.TAG):
        if kind not in terminal:
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
    book["_taxonomy"] = tuple(taxonomy)
    return claims


def _work_evidence(book: dict[str, Any]) -> tuple[ProviderEvidence, ...]:
    evidence: list[ProviderEvidence] = []
    for taxonomy_type, value in book.get("_taxonomy", ()):
        evidence.append(
            ProviderEvidence(
                ProviderEvidenceKind.TAXONOMY,
                value,
                ClaimScope.WORK,
                taxonomy_type=taxonomy_type,
            )
        )
    release_date = book["release_date"]
    if release_date is not None:
        try:
            value = _text(release_date, MAX_SCALAR_CHARACTERS)
            normalize_date_value(value)
        except ValueError:
            # Work release dates are optional supporting evidence.  Their
            # malformed lexical form cannot safely enter evidence and cannot
            # invalidate an independently anchored edition/work candidate.
            pass
        else:
            evidence.append(
                ProviderEvidence(
                    ProviderEvidenceKind.FIRST_PUBLICATION_DATE,
                    value,
                    ClaimScope.WORK,
                )
            )
    contributions, overflow, _invalid = _optional_bounded_list(
        book["contributions"],
        MAX_CONTRIBUTIONS,
        {"id", "contribution", "author"},
    )
    if overflow:
        return tuple(
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
                    value.taxonomy_type or "",
                ),
            )
        )
    for contribution in contributions:
        try:
            role = validate_role_projection(contribution["contribution"]).casefold()
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
            evidence.append(
                ProviderEvidence(
                    ProviderEvidenceKind.AUTHOR_IDENTITY,
                    f"hardcover-author-{author_id:010d}",
                    ClaimScope.WORK,
                )
            )
        else:
            evidence.append(
                ProviderEvidence(
                    ProviderEvidenceKind.CONTRIBUTOR_IDENTITY,
                    f"hardcover-author-{author_id:010d}",
                    ClaimScope.WORK,
                    role,
                )
            )
    return tuple(
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
                value.taxonomy_type or "",
            ),
        )
    )


def _contributions(
    provider: str,
    identity: str,
    value: object,
    scope: ClaimScope,
    local: tuple[LocalClaim, ...],
    offset: int,
    rejected: _OptionalRejections | None = None,
    identity_bindings: list[ProviderIdentityBinding] | None = None,
) -> list[ProviderClaim]:
    claims: list[ProviderClaim] = []
    contributions, overflow, invalid = _optional_bounded_list(
        value, MAX_CONTRIBUTIONS, {"id", "contribution", "author"}, rejected
    )
    if overflow:
        # No prefix can determine which roles occur in an unadmitted source
        # collection, so expose the two supported contribution fields only.
        return [
            _state_claim(
                provider,
                identity,
                kind,
                FieldState.OVERSIZED,
                scope,
                local,
                offset + index,
            )
            for index, kind in enumerate((ClaimKind.CREATOR, ClaimKind.CONTRIBUTOR))
        ]
    for item in contributions:
        raw_role = item["contribution"]
        if raw_role is None:
            role = None
        else:
            try:
                role = validate_role_projection(raw_role).casefold()
            except EvidenceValueError:
                if rejected:
                    rejected.reject()
                claims.append(
                    _state_claim(
                        provider,
                        identity,
                        ClaimKind.CONTRIBUTOR,
                        FieldState.MALFORMED,
                        scope,
                        local,
                        offset + len(claims),
                    )
                )
                continue
        kind = ClaimKind.CREATOR if role in {None, "author"} else ClaimKind.CONTRIBUTOR
        author = _field_local_author(item["author"], rejected)
        if author is None:
            claims.append(
                _state_claim(
                    provider,
                    identity,
                    kind,
                    FieldState.MALFORMED,
                    scope,
                    local,
                    offset + len(claims),
                )
            )
            continue
        try:
            author_id = _identifier(author["id"])
        except ValueError:
            author_id = None
            if rejected:
                rejected.reject()
        claim = _claim(
            provider,
            identity,
            kind,
            _author_name(author["name"], local)
            if kind is ClaimKind.CREATOR
            else author["name"],
            scope,
            "not_applicable",
            role,
            local,
            offset + len(claims),
            rejected,
        )
        claims.append(claim)
        if (
            identity_bindings is not None
            and author_id is not None
            and kind is ClaimKind.CREATOR
            and scope is ClaimScope.WORK
            and claim.state is FieldState.VALID
        ):
            identity_bindings.append(
                ProviderIdentityBinding(
                    claim.reference,
                    f"hardcover-author-{author_id:010d}",
                    ClaimScope.WORK,
                    role,
                    len(identity_bindings),
                )
            )
    if invalid:
        claims.append(
            _state_claim(
                provider,
                identity,
                ClaimKind.CONTRIBUTOR,
                FieldState.MALFORMED,
                scope,
                local,
                offset + len(claims),
            )
        )
    return claims


def _author_name(value: object, local: tuple[LocalClaim, ...]) -> object:
    """Read one Hardcover author name in display order (#1497).

    Whitespace runs collapse. A name equal to one of the book's own author
    names is kept as it is. Otherwise it is reordered only when Hardcover marks
    it inverted, as one personal `Family, Given` comma. A name that is one of
    the book's own only with its words rotated keeps its order here: it stays
    creator evidence as stated, and the proposal set just does not offer it.
    Every value another source states is left as it is.
    """
    if type(value) is not str or len(value) > MAX_SCALAR_CHARACTERS:
        return value
    name = " ".join(value.split())
    local_names = {
        " ".join(claim.canonical.split()).casefold()
        for claim in local
        if claim.kind is ClaimKind.CREATOR
        and claim.role == "author"
        and claim.state is FieldState.VALID
        and type(claim.canonical) is str
    }
    if name.casefold() in local_names:
        return name
    if comma_inverted_name(name) is not None:
        family, given = (part.strip() for part in name.split(",", 1))
        return f"{given} {family}"
    return name


def _field_local_author(
    value: object, rejected: _OptionalRejections | None = None
) -> dict[str, Any] | None:
    if type(value) is not dict or set(value) != {"id", "name"}:
        if rejected:
            rejected.reject()
        return None
    return value


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
    rejected: _OptionalRejections | None = None,
) -> ProviderClaim:
    state, text = _field(kind, value, rejected)
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
        links=comparison_links(bare, local),
    )


def _state_claim(
    provider: str,
    identity: str,
    kind: ClaimKind,
    state: FieldState,
    scope: ClaimScope,
    local: tuple[LocalClaim, ...],
    raw_order: int,
) -> ProviderClaim:
    bare = ProviderClaim.create(
        provider=provider,
        candidate=identity,
        kind=kind,
        value=None,
        state=state,
        scope=scope,
        applicability="not_applicable",
        raw_order=raw_order,
    )
    return ProviderClaim.create(
        provider=provider,
        candidate=identity,
        kind=kind,
        value=None,
        state=state,
        scope=scope,
        applicability="not_applicable",
        raw_order=raw_order,
        links=comparison_links(bare, local),
    )


def _field(
    kind: ClaimKind, value: object, rejected: _OptionalRejections | None = None
) -> tuple[FieldState, str | None]:
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
        if (
            kind is ClaimKind.SERIES_SEQUENCE
            and type(value) in {Decimal, int}
            and type(value) is not bool
        ):
            # The guard above admits only exact `Decimal` and `int` values.
            return FieldState.VALID, _position(cast("Decimal | int", value))
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
        if kind in {ClaimKind.TITLE, ClaimKind.SUBTITLE}:
            validate_observed_title(text)
        return FieldState.VALID, text
    except (ValueError, EvidenceValueError):
        if rejected:
            rejected.reject()
        return (
            FieldState.OVERSIZED
            if type(value) is str and len(value) > maximum
            else FieldState.MALFORMED
        ), None


def _bounded_list(
    value: object, maximum: int, keys: set[str]
) -> tuple[list[dict[str, Any]], bool]:
    if type(value) is not list:
        raise ValueError("provider list invalid")
    values = [_closed(item, keys) for item in value]
    return values, len(values) > maximum


def _optional_bounded_list(
    value: object,
    maximum: int,
    keys: set[str],
    rejected: _OptionalRejections | None = None,
) -> tuple[list[dict[str, Any]], bool, bool]:
    """Retain valid optional members only after a whole-field cap check."""
    if type(value) is not list:
        if rejected:
            rejected.reject()
        return [], False, True
    if len(value) > maximum:
        # Cardinality is authoritative: do not inspect or retain a prefix.
        if rejected:
            rejected.reject()
        return [], True, False
    values: list[dict[str, Any]] = []
    invalid = False
    for item in value:
        try:
            values.append(_closed(item, keys))
        except ValueError:
            if rejected:
                rejected.reject()
            invalid = True
    return values, False, invalid


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


def _anchored_projection(
    value: object, required: set[str], allowed: set[str]
) -> dict[str, Any]:
    """Retain a safely anchored provider row while discarding additive fields."""
    if type(value) is not dict or not required <= set(value):
        raise ValueError("provider object invalid")
    return {key: value[key] for key in allowed & set(value)}


def _closed_or_none(value: object, keys: set[str]) -> dict[str, Any] | None:
    return None if value is None else _closed(value, keys)


def _optional_closed(
    value: object, keys: set[str], rejected: _OptionalRejections | None = None
) -> dict[str, Any] | None:
    try:
        return _closed_or_none(value, keys)
    except ValueError:
        if rejected:
            rejected.reject()
        return None


def _optional_mapping(
    value: object, rejected: _OptionalRejections | None = None
) -> dict[str, Any] | None:
    """An optional parent is not an anchor; validate its children locally."""
    if type(value) is dict:
        return value
    if value is not None and rejected:
        rejected.reject()
    return None


def _optional_member(
    value: object, key: str, rejected: _OptionalRejections | None = None
) -> object:
    mapping = _optional_mapping(value, rejected)
    return None if mapping is None else mapping.get(key)


def _optional_projection_rejections(payload: bytes) -> int:
    """Count unknown additive keys only at safely traversable parser seams.

    Strict anchor and join keys are deliberately exempt: their validation is
    fatal in the candidate parser, rather than an optional diagnostic. Nested
    bounded collections are owned by their field parsers and are not traversed
    here, so an over-cap field remains one rejection with no prefix inspection.
    """
    try:
        source = json.loads(payload.decode("utf-8", "strict"))
        data = source.get("data") if type(source) is dict else None
    except (UnicodeError, ValueError, json.JSONDecodeError):
        return 0
    if type(data) is not dict:
        return 0
    count = 0
    editions = data.get("editions")
    if type(editions) is list and len(editions) <= MAX_CANDIDATES:
        edition_keys = {
            "id",
            "book_id",
            "isbn_10",
            "isbn_13",
            "asin",
            "title",
            "subtitle",
            "release_date",
            "image",
            "publisher",
            "language",
            "contributions",
        }
        for row in editions:
            if type(row) is not dict:
                continue
            count += len(set(row) - edition_keys)
            publisher = row.get("publisher")
            if type(publisher) is dict:
                count += len(set(publisher) - {"id", "name", "parent_id"})
            language = row.get("language")
            if type(language) is dict:
                count += len(set(language) - {"id", "code2", "code3", "language"})
    books = data.get("books")
    if type(books) is list and len(books) <= MAX_CANDIDATES:
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
        for book in books:
            if type(book) is dict:
                count += len(set(book) - work_keys)
    return min(count, MAX_DIAGNOSTIC_COUNT)


def _identifier(value: object) -> int:
    if type(value) is not int or type(value) is bool or not 1 <= value <= 2_147_483_647:
        raise ValueError("provider identity invalid")
    return value


def _text(value: object, maximum: int) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > maximum
        or value.strip() != value
        or "\x00" in value
        or any(
            unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value
        )
    ):
        raise ValueError("provider text invalid")
    return value


def _isbn(value: str, length: int) -> str:
    text = _text(value, 32).replace("-", "").replace(" ", "").upper()
    if (
        length not in {10, 13}
        or len(text) != length
        or any(
            not character.isdigit()
            and not (length == 10 and character == "X" and index == 9)
            for index, character in enumerate(text)
        )
    ):
        raise ValueError("provider ISBN invalid")
    if (
        length == 13
        and int(text[-1])
        != (
            10
            - sum(
                (1 if index % 2 == 0 else 3) * int(character)
                for index, character in enumerate(text[:12])
            )
            % 10
        )
        % 10
    ):
        raise ValueError("provider ISBN invalid")
    if (
        length == 10
        and sum(
            (10 - index) * (10 if character == "X" else int(character))
            for index, character in enumerate(text)
        )
        % 11
    ):
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
    # `DecimalTuple.exponent` is only the "n"/"N"/"F" sentinel for NaN and
    # infinity, which the `is_finite` guard above has already rejected.
    exponent = cast(int, source.exponent)
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


__all__ = [
    "HARDCOVER_SCHEMA_COMMIT",
    "HARDCOVER_URL",
    "HardcoverMetadataProvider",
    "HardcoverPacer",
]
