"""Synchronous report orchestration and its narrow application-owned ports."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol, cast

from mechanical_librarian.application.catalogue import TargetConnectionError
from mechanical_librarian.application.configuration import (
    LibraryConfiguration,
    MediaMountConfiguration,
    ReportLimits,
    TargetConnectionConfiguration,
)
from mechanical_librarian.application.failure_diagnostics import (
    FailureDiagnostic,
    merge_provider_diagnostics,
    provider_attempt_diagnostics,
)
from mechanical_librarian.application.operations import StageTimer
from mechanical_librarian.domain.cover_evidence import (
    CoverDesignation,
    CoverProvenance,
    CoverSource,
    CoverSourceKind,
    CoverSourceState,
)
from mechanical_librarian.domain.failure_diagnostics import (
    FailureCategory,
    FailureComponent,
    FailureStage,
    diagnostics_are_canonical,
)
from mechanical_librarian.domain.metadata_report import (
    MAX_CANDIDATES,
    ClaimKind,
    ClaimScope,
    DateLinkConfidence,
    EditionEnrichmentOverflow,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderEvidenceKind,
    ProviderLaneCandidate,
    ProviderLaneObservation,
    ProviderObservation,
    ProviderQueryKind,
    ProviderRequestAccounting,
    ProviderStatus,
    PublicationDateRelation,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    canonical_json,
    complete_creator_identity_bindings,
    default_target_fields,
    derive_field_results,
    isbn_family,
    normalize_title,
    provider_candidate_work_identity_admission,
    provider_lane_candidate_reference,
    validate_provider_access_time,
)
from mechanical_librarian.domain.work_identity import (
    WorkDispatchLineage,
    WorkTitleTransformationLineage,
    replay_provider_work_identity_admission,
    work_title_has_variant_marker,
)


class MetadataReportError(RuntimeError):
    """A value-free operational failure at the command boundary."""


class ReportNotFoundError(MetadataReportError):
    """The requested immutable report is absent from retained storage."""


class ProposalNotFoundError(MetadataReportError):
    """The requested immutable proposal is absent from retained storage."""


class UnsupportedMediaError(MetadataReportError):
    pass


class ItemInspectionError(MetadataReportError):
    def __init__(self, outcome: object = None) -> None:
        super().__init__("EPUB inspection failed")
        self.outcome = outcome


class ArtifactAuthorityError(MetadataReportError):
    pass


class CurrentnessError(MetadataReportError):
    pass


class TargetNotFoundError(MetadataReportError):
    """The exact target item no longer exists at the supported service API."""


class CredentialError(MetadataReportError):
    def __init__(self, message: str, *, provider: str | None = None) -> None:
        super().__init__(message)
        self.provider = provider


class CoverTimeoutError(MetadataReportError):
    """The bounded cover-acquisition aggregate deadline elapsed."""


class CoverSourceChangedError(MetadataReportError):
    """The target's declared cover changed during its raw acquisition bracket."""


class CoverReadError(MetadataReportError):
    """A raw target cover reached a closed terminal acquisition outcome."""

    def __init__(self, state: str) -> None:
        self.state = state
        super().__init__("target cover is unavailable")


@dataclass(frozen=True, slots=True)
class TargetFile:
    identity: str
    relative_path: str
    format: str


@dataclass(frozen=True, slots=True)
class TargetCover:
    """Opaque target cover tuple retained solely for a before/after drift check."""

    identity: str
    path: str
    item: str = ""


@dataclass(frozen=True, slots=True)
class TargetSnapshot:
    media_kind: str
    title: str | None
    files: tuple[TargetFile, ...]
    cover: TargetCover | None = None
    fields: tuple[TargetField, ...] = ()

    def __post_init__(self) -> None:
        if not self.fields:
            object.__setattr__(self, "fields", default_target_fields(self.title))
        facts = TargetFacts("snapshot", "snapshot", "snapshot", self.fields)
        if self.cover is None and facts.fields[-1].state is TargetFieldState.VALUE:
            raise MetadataReportError("target cover field is inconsistent")


@dataclass(frozen=True, slots=True)
class MetadataLookup:
    """One closed provider lookup, independent of provider wire shape."""

    kind: ProviderQueryKind
    local_claims: tuple[LocalClaim, ...]
    title: str | None = None
    authors: tuple[str, ...] = ()
    matched_family: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.kind) is not ProviderQueryKind
            or type(self.local_claims) is not tuple
            or any(type(value) is not LocalClaim for value in self.local_claims)
            or type(self.authors) is not tuple
            or any(type(value) is not str for value in self.authors)
        ):
            raise MetadataReportError("provider lookup is invalid")
        if self.kind is ProviderQueryKind.ISBN:
            if self.title is not None or self.authors:
                raise MetadataReportError("provider lookup is invalid")
        elif self.kind is ProviderQueryKind.WORK:
            if self.title is None or not self.authors:
                raise MetadataReportError("provider lookup is invalid")
            normalize_title(self.title)
            for author in self.authors:
                normalize_title(author)
        elif self.title is not None or self.authors or self.matched_family is not None:
            raise MetadataReportError("provider lookup is invalid")


MAX_ISBN_DISCOVERY_LANES = 8


@dataclass(frozen=True, slots=True)
class MetadataDiscoveryLane:
    identity: str
    kind: ProviderQueryKind
    input_references: tuple[str, ...]
    matched_family: str | None = None
    canonical_isbns: tuple[str, ...] = ()
    title: str | None = None
    authors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            not self.identity.startswith("lane_")
            or len(self.identity) != 69
            or any(
                character not in "0123456789abcdef"
                for character in self.identity.removeprefix("lane_")
            )
            or type(self.kind) is not ProviderQueryKind
            or self.kind not in {ProviderQueryKind.ISBN, ProviderQueryKind.WORK}
            or type(self.input_references) is not tuple
            or tuple(sorted(set(self.input_references))) != self.input_references
        ):
            raise MetadataReportError("provider discovery lane is invalid")
        if self.kind is ProviderQueryKind.ISBN:
            if (
                self.matched_family is None
                or not self.canonical_isbns
                or self.title is not None
                or self.authors
            ):
                raise MetadataReportError("provider discovery lane is invalid")
        elif (
            self.matched_family is not None
            or self.canonical_isbns
            or self.title is None
            or not self.authors
        ):
            raise MetadataReportError("provider discovery lane is invalid")


@dataclass(frozen=True, slots=True)
class MetadataDiscoveryPlan:
    local_claims: tuple[LocalClaim, ...]
    lanes: tuple[MetadataDiscoveryLane, ...]
    overflow: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.local_claims) is not tuple
            or any(type(value) is not LocalClaim for value in self.local_claims)
            or type(self.lanes) is not tuple
            or any(type(value) is not MetadataDiscoveryLane for value in self.lanes)
            or len(self.lanes) > MAX_ISBN_DISCOVERY_LANES + 1
            or tuple(value.identity for value in self.lanes)
            != tuple(sorted({value.identity for value in self.lanes}))
            or type(self.overflow) is not bool
            or (self.overflow and self.lanes)
        ):
            raise MetadataReportError("provider discovery plan is invalid")
        known = {value.reference for value in self.local_claims}
        if (
            any(not set(lane.input_references).issubset(known) for lane in self.lanes)
            or (
                len(
                    {
                        lane.matched_family
                        for lane in self.lanes
                        if lane.kind is ProviderQueryKind.ISBN
                    }
                )
                != sum(lane.kind is ProviderQueryKind.ISBN for lane in self.lanes)
            )
            or sum(lane.kind is ProviderQueryKind.WORK for lane in self.lanes) > 1
        ):
            raise MetadataReportError("provider discovery plan is invalid")


@dataclass(frozen=True, slots=True)
class ProviderLaneResult:
    lane: MetadataDiscoveryLane
    status: ProviderStatus
    candidates: tuple[ProviderCandidate, ...] = ()
    accessed_at: str | None = None
    edition_enrichment_overflow: EditionEnrichmentOverflow | None = None
    diagnostics: tuple[FailureDiagnostic, ...] = ()
    dispatch_lineage: WorkDispatchLineage | None = None
    transformation_lineage: WorkTitleTransformationLineage | None = None

    def __post_init__(self) -> None:
        if (
            type(self.lane) is not MetadataDiscoveryLane
            or type(self.status) is not ProviderStatus
            or type(self.candidates) is not tuple
            or any(type(value) is not ProviderCandidate for value in self.candidates)
            or (self.status is ProviderStatus.CANDIDATES) != bool(self.candidates)
            or not diagnostics_are_canonical(self.diagnostics)
            or any(
                value.component is not FailureComponent.PROVIDER
                or value.stage
                not in {
                    FailureStage.LOOKUP,
                    FailureStage.REQUEST,
                    FailureStage.RESPONSE,
                }
                or value.current is not None
                for value in self.diagnostics
            )
            or (
                self.dispatch_lineage is not None
                and (
                    type(self.dispatch_lineage) is not WorkDispatchLineage
                    or self.lane.kind is not ProviderQueryKind.WORK
                    or self.status
                    not in {
                        ProviderStatus.CANDIDATES,
                        ProviderStatus.CANDIDATE_LIMIT_EXCEEDED,
                    }
                )
            )
            or (
                self.transformation_lineage is not None
                and (
                    type(self.transformation_lineage)
                    is not WorkTitleTransformationLineage
                    or self.dispatch_lineage is None
                    or self.lane.kind is not ProviderQueryKind.WORK
                )
            )
            or (
                self.edition_enrichment_overflow is not None
                and (
                    type(self.edition_enrichment_overflow)
                    is not EditionEnrichmentOverflow
                    or self.status is not ProviderStatus.CANDIDATE_LIMIT_EXCEEDED
                    or self.candidates
                )
            )
        ):
            raise MetadataReportError("provider lane result is invalid")
        if self.accessed_at is not None:
            validate_provider_access_time(self.accessed_at)


def _lane_identity(payload: dict[str, object]) -> str:
    return "lane_" + hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def metadata_discovery_plan(
    target: TargetSnapshot, local_claims: tuple[LocalClaim, ...]
) -> MetadataDiscoveryPlan:
    families: dict[str, list[LocalClaim]] = {}
    for claim in local_claims:
        if (
            claim.kind is ClaimKind.ISBN
            and claim.canonical is not None
            and (family := isbn_family(claim.canonical)) is not None
        ):
            families.setdefault(family, []).append(claim)
    if len(families) > MAX_ISBN_DISCOVERY_LANES:
        return MetadataDiscoveryPlan(local_claims, (), True)

    lanes: list[MetadataDiscoveryLane] = []
    for family, claims in sorted(families.items()):
        references = tuple(sorted(claim.reference for claim in claims))
        canonical_isbns = tuple(
            sorted({claim.canonical for claim in claims if claim.canonical is not None})
        )
        identity = _lane_identity(
            {
                "canonical_isbns": list(canonical_isbns),
                "input_references": list(references),
                "kind": ProviderQueryKind.ISBN.value,
                "matched_family": family,
            }
        )
        lanes.append(
            MetadataDiscoveryLane(
                identity,
                ProviderQueryKind.ISBN,
                references,
                family,
                canonical_isbns,
            )
        )

    fields = {field.destination: field for field in target.fields}
    title = fields[TargetDestination.TITLE]
    authors = fields[TargetDestination.AUTHORS]
    complete_work = (
        title.state is TargetFieldState.VALUE
        and type(title.value) is str
        and authors.state is TargetFieldState.VALUE
        and type(authors.value) is tuple
        and bool(authors.value)
        and all(type(value) is str for value in authors.value)
        and len(
            {normalize_title(cast("str", value)).casefold() for value in authors.value}
        )
        == len(authors.value)
    )
    if complete_work:
        # ``complete_work`` is exactly the conjunction that proved the title is
        # a ``str`` and the authors are a non-empty tuple of ``str``.
        normalized_title = normalize_title(cast("str", title.value))
        normalized_authors = tuple(
            normalize_title(value) for value in cast("tuple[str, ...]", authors.value)
        )
        title_references = tuple(
            sorted(
                claim.reference
                for claim in local_claims
                if claim.kind is ClaimKind.TITLE and claim.basis == "target_metadata"
            )
        )
        if title_references:
            identity = _lane_identity(
                {
                    "authors": list(normalized_authors),
                    "input_references": list(title_references),
                    "kind": ProviderQueryKind.WORK.value,
                    "title": normalized_title,
                }
            )
            lanes.append(
                MetadataDiscoveryLane(
                    identity,
                    ProviderQueryKind.WORK,
                    title_references,
                    title=normalized_title,
                    authors=normalized_authors,
                )
            )
    return MetadataDiscoveryPlan(
        local_claims, tuple(sorted(lanes, key=lambda value: value.identity))
    )


def provider_observation(
    provider: str,
    results: tuple[ProviderLaneResult, ...],
    *,
    authority: str | None = None,
    diagnostics: tuple[FailureDiagnostic, ...] = (),
    request_accounting: ProviderRequestAccounting = ProviderRequestAccounting(),
) -> ProviderObservation:
    access_times = {
        result.accessed_at for result in results if result.accessed_at is not None
    }
    if len(access_times) > 1:
        raise MetadataReportError("provider lane access times disagree")
    accessed_at = next(iter(access_times), None)
    candidates_by_identity: dict[str, ProviderCandidate] = {}
    conflict = False
    aggregate_failure: ProviderStatus | None = None
    overflow_results = tuple(
        result for result in results if result.edition_enrichment_overflow is not None
    )
    for result in results:
        for candidate in result.candidates:
            previous = candidates_by_identity.get(candidate.identity)
            if previous is None:
                candidates_by_identity[candidate.identity] = candidate
                continue
            if previous == candidate:
                continue
            if previous.work_identity != candidate.work_identity:
                conflict = True
                continue
            previous_claims = set(previous.claims)
            candidate_claims = set(candidate.claims)
            previous_evidence = set(previous.evidence)
            candidate_evidence = set(candidate.evidence)
            if previous_claims.issubset(
                candidate_claims
            ) and previous_evidence.issubset(candidate_evidence):
                candidates_by_identity[candidate.identity] = candidate
            elif not (
                candidate_claims.issubset(previous_claims)
                and candidate_evidence.issubset(previous_evidence)
            ):
                conflict = True
    if conflict or len(candidates_by_identity) > MAX_CANDIDATES:
        aggregate_failure = (
            ProviderStatus.MALFORMED
            if conflict
            else ProviderStatus.CANDIDATE_LIMIT_EXCEEDED
        )
        results = tuple(
            (
                ProviderLaneResult(
                    result.lane,
                    aggregate_failure,
                    accessed_at=result.accessed_at,
                    diagnostics=(
                        merge_provider_diagnostics(
                            result.diagnostics,
                            (
                                FailureDiagnostic(
                                    FailureCategory.MALFORMED_OVER_BOUND_RESPONSE,
                                    FailureComponent.PROVIDER,
                                    FailureStage.RESPONSE,
                                    retryable=False,
                                ),
                            ),
                        )
                        if aggregate_failure is ProviderStatus.MALFORMED
                        else result.diagnostics
                    ),
                )
                if result.status is ProviderStatus.CANDIDATES
                else result
            )
            for result in results
        )
        candidates_by_identity = {}

    lanes: list[ProviderLaneObservation] = []
    for result in results:
        lane_candidates = tuple(
            sorted(
                (
                    ProviderLaneCandidate(
                        provider_lane_candidate_reference(
                            provider,
                            result.lane.identity,
                            candidate.identity,
                            candidate.work_identity,
                        ),
                        candidate.identity,
                        candidate.work_identity,
                    )
                    for candidate in result.candidates
                ),
                key=lambda value: value.reference,
            )
        )
        lanes.append(
            ProviderLaneObservation(
                result.lane.identity,
                result.lane.kind,
                result.status,
                result.lane.input_references,
                result.lane.matched_family,
                lane_candidates,
                result.diagnostics,
                result.dispatch_lineage,
                result.transformation_lineage,
            )
        )

    candidates = tuple(
        sorted(candidates_by_identity.values(), key=lambda value: value.identity)
    )
    if aggregate_failure is not None:
        aggregate_status = aggregate_failure
    elif candidates:
        aggregate_status = ProviderStatus.CANDIDATES
    elif results:
        priorities = (
            ProviderStatus.QUERY_LIMIT_EXCEEDED,
            ProviderStatus.CANDIDATE_LIMIT_EXCEEDED,
            ProviderStatus.MALFORMED,
            ProviderStatus.OVERSIZED,
            ProviderStatus.AUTHENTICATION,
            ProviderStatus.AUTHORIZATION,
            ProviderStatus.RATE_LIMITED,
            ProviderStatus.INCOMPLETE,
            ProviderStatus.UNAVAILABLE,
            ProviderStatus.NO_MATCH,
        )
        aggregate_status = next(
            (
                status
                for status in priorities
                if any(result.status is status for result in results)
            ),
            ProviderStatus.INSUFFICIENT_INPUT,
        )
    else:
        aggregate_status = ProviderStatus.INSUFFICIENT_INPUT
    overflow = (
        overflow_results[0].edition_enrichment_overflow
        if len(overflow_results) == 1
        and overflow_results[0].lane.kind is ProviderQueryKind.WORK
        and overflow_results[0].status is ProviderStatus.CANDIDATE_LIMIT_EXCEEDED
        and aggregate_status is ProviderStatus.CANDIDATE_LIMIT_EXCEEDED
        and not candidates
        and not any(result.candidates for result in results)
        else None
    )
    references = tuple(
        sorted(
            {
                reference
                for result in results
                for reference in result.lane.input_references
            }
        )
    )
    kind = (
        ProviderQueryKind.NONE
        if not results
        else (
            results[0].lane.kind if len(results) == 1 else ProviderQueryKind.DISCOVERY
        )
    )
    return ProviderObservation(
        provider,
        aggregate_status,
        references,
        candidates,
        kind,
        authority,
        tuple(sorted(lanes, key=lambda value: value.identity)),
        accessed_at=accessed_at,
        diagnostics=diagnostics,
        request_accounting=request_accounting,
        edition_enrichment_overflow=overflow,
    )


@dataclass(frozen=True, slots=True)
class InspectionResult:
    content_digest: str
    claims: tuple[LocalClaim, ...]
    cover_sources: tuple[CoverSource, ...] = ()
    rear_resource_limit: bool = False


@dataclass(frozen=True, slots=True)
class ProviderCoverDescriptor:
    provider: str
    kind: CoverSourceKind
    source_id: str
    url: str | None
    provenance: CoverProvenance | None = None
    wait: Callable[[float | None], bool] | None = None
    allow_redirects: bool = True
    user_agent: str | None = None
    terminal_state: CoverSourceState | None = None


def merge_provider_cover_descriptor(
    previous: ProviderCoverDescriptor,
    current: ProviderCoverDescriptor,
) -> ProviderCoverDescriptor | None:
    """Merge one lane-neutral cover source or reject conflicting source facts."""
    if previous == current:
        return previous
    if (
        previous.source_id != current.source_id
        or previous.provenance is None
        or current.provenance is None
        or replace(previous, provenance=None) != replace(current, provenance=None)
        or {
            previous.provenance.designation,
            current.provenance.designation,
        }
        != {
            CoverDesignation.EXACT_EDITION,
            CoverDesignation.RELATED_EDITION,
        }
        or replace(
            previous.provenance,
            designation=CoverDesignation.RELATED_EDITION,
        )
        != replace(
            current.provenance,
            designation=CoverDesignation.RELATED_EDITION,
        )
    ):
        return None
    return replace(
        previous,
        provenance=replace(
            previous.provenance,
            designation=CoverDesignation.EXACT_EDITION,
        ),
    )


def merge_provider_cover_descriptors(
    retained: tuple[ProviderCoverDescriptor, ...],
    lane: tuple[ProviderCoverDescriptor, ...],
) -> tuple[ProviderCoverDescriptor, ...] | None:
    """Atomically admit one lane's cover descriptors into the aggregate."""
    descriptors = {value.source_id: value for value in retained}
    if len(descriptors) != len(retained):
        return None
    for descriptor in lane:
        previous = descriptors.get(descriptor.source_id)
        if previous is None:
            descriptors[descriptor.source_id] = descriptor
            continue
        merged = merge_provider_cover_descriptor(previous, descriptor)
        if merged is None:
            return None
        descriptors[descriptor.source_id] = merged
    return tuple(descriptors[key] for key in sorted(descriptors))


def provider_isbn_applicability(
    value: object,
    local_claims: tuple[LocalClaim, ...],
) -> str:
    """Resolve one returned ISBN family without borrowing another lane's scope."""
    family = isbn_family(value) if type(value) is str else None
    applicability = {
        claim.applicability
        for claim in local_claims
        if claim.kind is ClaimKind.ISBN
        and claim.canonical is not None
        and isbn_family(claim.canonical) == family
    }
    if "ebook" in applicability:
        return "ebook"
    if applicability == {"print"}:
        return "print"
    if "ambiguous" in applicability:
        return "ambiguous"
    return "unknown"


class TargetReader(Protocol):
    def read(self, item: str) -> TargetSnapshot: ...


class TargetCoverReader(TargetReader, Protocol):
    def read_cover(
        self, cover: TargetCover, *, deadline: float | None = None
    ) -> bytes | None: ...


class EpubInspector(Protocol):
    def inspect(
        self, mount: MediaMountConfiguration, relative_path: str, maximum_bytes: int
    ) -> InspectionResult: ...
    def unchanged(
        self,
        mount: MediaMountConfiguration,
        relative_path: str,
        expected_digest: str,
        maximum_bytes: int,
    ) -> bool: ...


class MetadataProvider(Protocol):
    def observe(
        self,
        plan: MetadataDiscoveryPlan,
        *,
        deadline: float | None = None,
    ) -> ProviderObservation: ...


class CoverObserver(Protocol):
    """Collect bounded report-only cover outcomes before final publication."""

    def observe(
        self,
        target: TargetSnapshot,
        providers: tuple[ProviderObservation, ...],
        local_sources: tuple[CoverSource, ...],
        descriptors: tuple[ProviderCoverDescriptor, ...] = (),
        aggregate_deadline: float | None = None,
    ) -> tuple[CoverSource, ...]: ...


class ReportRepository(Protocol):
    def publish(
        self,
        report: MetadataReport,
        currentness: Callable[[], bool],
        expected_current: str | None = None,
        after_publication: Callable[[ReportRepository, MetadataReport], None]
        | None = None,
        *,
        expected_package: tuple[str | None, str, str | None, int | None, str]
        | None = None,
    ) -> str: ...
    def get(self, report_id: str) -> str: ...
    def current(
        self, connection: str, library: str, item: str, file_identity: str
    ) -> str: ...
    def current_optional(
        self, connection: str, library: str, item: str, file_identity: str
    ) -> str | None: ...
    def current_review_package(
        self, connection: str, library: str, item: str
    ) -> tuple[str | None, str, str | None, int | None, str] | None: ...
    def current_review_package_report(
        self, connection: str, library: str, item: str
    ) -> str | None: ...
    def pending_provider_recovery_outbox(
        self, *, limit: int = 100, after: tuple[int, str] | None = None
    ) -> tuple[tuple[object, ...], ...]: ...
    def complete_provider_recovery_outbox(
        self, outbox_id: str, *, delivered_at: int
    ) -> None: ...
    def close(self) -> None: ...


def _release_repository(
    repository: ReportRepository | None, failure: BaseException
) -> None:
    """Release the report handle without letting cleanup replace ``failure``."""
    if repository is None:
        return
    try:
        repository.close()
    except Exception as error:
        # ``ReportRepository`` declares no error type, so breadth is what
        # guarantees the caller still sees the failure they asked about. The
        # release failure is not discarded: it is noted on that failure, which
        # the caller raises with its identity and cause intact.
        failure.add_note(f"report repository release failed: {error!r}")


@dataclass(frozen=True, slots=True)
class ProviderBinding:
    name: str
    enabled: bool
    factory: Callable[[], MetadataProvider]
    authority: str | None = None


def metadata_lookup(
    target: TargetSnapshot, local_claims: tuple[LocalClaim, ...]
) -> MetadataLookup:
    usable = tuple(
        claim
        for claim in local_claims
        if claim.kind is ClaimKind.ISBN
        and claim.canonical is not None
        and claim.applicability in {"ebook", "unknown"}
        and claim.basis == "opening_matter"
        and claim.rear_provenance is None
        and isbn_family(claim.canonical) is not None
    )
    families = {isbn_family(claim.canonical) for claim in usable}
    if len(families) == 1:
        return MetadataLookup(ProviderQueryKind.ISBN, local_claims)
    if families:
        return MetadataLookup(ProviderQueryKind.NONE, local_claims)
    fields = {field.destination: field for field in target.fields}
    title = fields[TargetDestination.TITLE]
    authors = fields[TargetDestination.AUTHORS]
    if (
        title.state is not TargetFieldState.VALUE
        or type(title.value) is not str
        or authors.state is not TargetFieldState.VALUE
        or type(authors.value) is not tuple
        or not authors.value
        or any(type(value) is not str for value in authors.value)
        or len(
            {
                # Reached only once every author entry is a ``str``.
                normalize_title(cast("str", value)).casefold()
                for value in authors.value
            }
        )
        != len(authors.value)
    ):
        return MetadataLookup(ProviderQueryKind.NONE, local_claims)
    return MetadataLookup(
        ProviderQueryKind.WORK,
        local_claims,
        normalize_title(title.value),
        tuple(normalize_title(cast("str", value)) for value in authors.value),
    )


def qualify_provider_observation(
    observation: ProviderObservation, lookup: MetadataLookup
) -> ProviderObservation:
    if lookup.kind is not ProviderQueryKind.WORK:
        return ProviderObservation(
            observation.provider,
            observation.status,
            observation.input_references,
            observation.candidates,
            lookup.kind,
            observation.authority,
            accessed_at=observation.accessed_at,
            diagnostics=observation.diagnostics,
            request_accounting=observation.request_accounting,
            # ISBN lanes cannot establish the exact work title and author set.
            edition_enrichment_overflow=None,
        )
    if observation.edition_enrichment_overflow is not None:
        witness = observation.edition_enrichment_overflow
        admission = replay_provider_work_identity_admission(
            local_title=lookup.title or "",
            local_authors=lookup.authors,
            provider_title=witness.title,
            provider_authors=tuple(author.value or "" for author in witness.authors),
            provider_work_identity=witness.work_identity,
            dispatch_lineage=observation.dispatch_lineage,
        )
        qualified_witness = admission.admitted
        return ProviderObservation(
            observation.provider,
            ProviderStatus.CANDIDATE_LIMIT_EXCEEDED,
            (),
            (),
            ProviderQueryKind.WORK,
            observation.authority,
            accessed_at=observation.accessed_at,
            diagnostics=observation.diagnostics,
            request_accounting=observation.request_accounting,
            edition_enrichment_overflow=witness if qualified_witness else None,
            dispatch_lineage=(
                observation.dispatch_lineage if qualified_witness else None
            ),
            transformation_lineage=(
                admission.transformation if qualified_witness else None
            ),
        )
    if observation.status is not ProviderStatus.CANDIDATES:
        return ProviderObservation(
            observation.provider,
            observation.status,
            (),
            (),
            ProviderQueryKind.WORK,
            observation.authority,
            accessed_at=observation.accessed_at,
            diagnostics=observation.diagnostics,
            request_accounting=observation.request_accounting,
        )
    qualified: dict[
        str, list[tuple[ProviderCandidate, WorkTitleTransformationLineage | None]]
    ] = {}
    blocked_work_identities: set[str] = set()
    for candidate in observation.candidates:
        qualification_identity = (
            candidate.identity
            if candidate.work_identity == "unresolved"
            else candidate.work_identity
        )
        admission = provider_candidate_work_identity_admission(
            candidate,
            lookup.title or "",
            lookup.authors,
            observation.dispatch_lineage,
        )
        disqualifying_roles = {
            claim.role
            for claim in candidate.claims
            if claim.scope is ClaimScope.WORK
            and claim.kind is ClaimKind.CONTRIBUTOR
            and claim.role in {"abridger", "adapter", "translator"}
        }
        variant_marker = any(
            work_title_has_variant_marker(claim.value)
            for claim in candidate.claims
            if claim.kind in {ClaimKind.TITLE, ClaimKind.SUBTITLE}
            and claim.state is FieldState.VALID
            and claim.value is not None
        )
        if disqualifying_roles or variant_marker:
            blocked_work_identities.add(qualification_identity)
        if admission.admitted and not disqualifying_roles and not variant_marker:
            qualified.setdefault(qualification_identity, []).append(
                (candidate, admission.transformation)
            )
    for identity in blocked_work_identities:
        qualified.pop(identity, None)
    lineages = {
        transformation
        for values in qualified.values()
        for _candidate, transformation in values
    }
    if len(qualified) != 1 or len(lineages) != 1:
        return ProviderObservation(
            observation.provider,
            ProviderStatus.NO_MATCH,
            (),
            (),
            ProviderQueryKind.WORK,
            observation.authority,
            accessed_at=observation.accessed_at,
            diagnostics=observation.diagnostics,
            request_accounting=observation.request_accounting,
        )
    selected_values = next(iter(qualified.values()))
    candidate, transformation_lineage = min(
        selected_values, key=lambda value: value[0].identity
    )
    neutral_creators = tuple(
        claim
        for claim in candidate.claims
        if claim.scope is ClaimScope.WORK
        and claim.kind is ClaimKind.CREATOR
        and claim.role is None
    )
    neutral_bindings = complete_creator_identity_bindings(candidate, neutral_creators)
    reject_neutral_creators = neutral_bindings is not None and len(
        {binding.identity for binding in neutral_bindings}
    ) != len(neutral_bindings)
    reject_mixed_author_creators = _mixed_work_creator_roles(candidate)
    reject_creator_authority = reject_neutral_creators or reject_mixed_author_creators
    rejected_creator_references = (
        {claim.reference for claim in neutral_creators}
        if reject_neutral_creators
        else set()
    )
    if reject_mixed_author_creators:
        rejected_creator_references.update(
            claim.reference
            for claim in candidate.claims
            if claim.scope is ClaimScope.WORK and claim.kind is ClaimKind.CREATOR
        )
    allowed_claims = {
        ClaimKind.TITLE,
        ClaimKind.CREATOR,
        ClaimKind.SERIES,
        ClaimKind.SERIES_SEQUENCE,
        ClaimKind.DESCRIPTION,
        ClaimKind.GENRE,
        ClaimKind.TAG,
    }
    neutral_rejection_sibling_claims = {
        ClaimKind.ISBN,
        ClaimKind.ASIN,
        ClaimKind.PUBLICATION_DATE,
    }
    allowed_evidence = {
        ProviderEvidenceKind.AUTHOR_IDENTITY,
        ProviderEvidenceKind.CONTRIBUTOR_IDENTITY,
        ProviderEvidenceKind.FIRST_PUBLICATION_DATE,
        ProviderEvidenceKind.WORK_COVER_ID,
        ProviderEvidenceKind.TAXONOMY,
        ProviderEvidenceKind.DESCRIPTION_TRANSFORMATION,
    }
    filtered_claims = tuple(
        claim
        for claim in candidate.claims
        if claim.reference not in rejected_creator_references
        and (
            (claim.scope is ClaimScope.WORK and claim.kind in allowed_claims)
            or (
                reject_creator_authority
                and claim.scope is ClaimScope.EDITION
                and claim.kind in neutral_rejection_sibling_claims
                and claim.state is FieldState.VALID
            )
        )
    )
    filtered_references = {claim.reference for claim in filtered_claims}
    filtered = ProviderCandidate(
        candidate.identity,
        candidate.work_identity,
        filtered_claims,
        tuple(
            value
            for value in candidate.evidence
            if (
                not (
                    reject_creator_authority
                    and value.kind
                    in {
                        ProviderEvidenceKind.AUTHOR_IDENTITY,
                        ProviderEvidenceKind.CONTRIBUTOR_IDENTITY,
                    }
                )
                and (value.scope is ClaimScope.WORK and value.kind in allowed_evidence)
                or (
                    value.scope is ClaimScope.EDITION
                    and value.kind is ProviderEvidenceKind.PUBLICATION_DATE_TEXT
                    and value.date_normalized is not None
                    and value.date_confidence is DateLinkConfidence.UNRESOLVED
                    and (
                        not value.links
                        or any(
                            link.publication_date_relation
                            is PublicationDateRelation.MANIFESTATION_INCOMPATIBLE
                            for link in value.links
                        )
                    )
                )
            )
        ),
        tuple(
            binding
            for binding in candidate.identity_bindings
            if binding.claim_reference in filtered_references
        ),
    )
    return ProviderObservation(
        observation.provider,
        ProviderStatus.CANDIDATES,
        (),
        (filtered,),
        ProviderQueryKind.WORK,
        observation.authority,
        accessed_at=observation.accessed_at,
        diagnostics=observation.diagnostics,
        request_accounting=observation.request_accounting,
        dispatch_lineage=observation.dispatch_lineage,
        transformation_lineage=transformation_lineage,
    )


def _complete_work_creators(
    candidate: ProviderCandidate, role: str | None
) -> tuple[str, ...] | None:
    """Return one closed work creator set without inferring an omitted role."""
    creators = tuple(
        claim
        for claim in candidate.claims
        if claim.scope is ClaimScope.WORK and claim.kind is ClaimKind.CREATOR
    )
    if (
        not creators
        or any(
            claim.role != role
            or claim.state is not FieldState.VALID
            or claim.value is None
            for claim in creators
        )
        or any(
            claim.scope is ClaimScope.WORK and claim.kind is ClaimKind.CONTRIBUTOR
            for claim in candidate.claims
        )
    ):
        return None
    # The guard above returned for any creator whose value is None.
    return tuple(
        normalize_title(cast("str", claim.value)).casefold() for claim in creators
    )


def _mixed_work_creator_roles(candidate: ProviderCandidate) -> bool:
    """A typed author set cannot be projected from a mixed creator collection."""
    roles = {
        claim.role
        for claim in candidate.claims
        if claim.scope is ClaimScope.WORK and claim.kind is ClaimKind.CREATOR
    }
    return "author" in roles and len(roles) > 1


class GenerateMetadataReport:
    """Coordinate one fresh, report-only observation and durable publication."""

    def __init__(
        self,
        *,
        target: TargetReader,
        inspector: EpubInspector,
        repository_factory: Callable[[], ReportRepository],
        providers: tuple[ProviderBinding, ...],
        connection: TargetConnectionConfiguration,
        library: LibraryConfiguration,
        mount: MediaMountConfiguration | None,
        limits: ReportLimits,
        cover_observer: CoverObserver | None = None,
        publication_authority_guard: Callable[[], None] | None = None,
        publication_receipt: Callable[[ReportRepository, MetadataReport], None]
        | None = None,
        require_singular_target: bool = False,
        timing: StageTimer | None = None,
    ) -> None:
        self._target = target
        self._inspector = inspector
        self._repository_factory = repository_factory
        self._providers = providers
        self._connection = connection
        self._library = library
        self._mount = mount
        self._limits = limits
        self._cover_observer = cover_observer
        self._publication_authority_guard = publication_authority_guard
        self._publication_receipt = publication_receipt
        self._require_singular_target = require_singular_target
        self._timing = timing or StageTimer(None)

    def run(
        self, item: str, file_index: int, expected_file_identity: str | None = None
    ) -> str:
        if self._mount is None:
            raise UnsupportedMediaError("unsupported media")
        initial = self._safe_read(item)
        if (
            initial.media_kind != "ebook"
            or type(file_index) is not int
            or not 0 <= file_index < len(initial.files)
        ):
            raise UnsupportedMediaError("unsupported media")
        selected = initial.files[file_index]
        if (
            expected_file_identity is not None
            and selected.identity != expected_file_identity
        ):
            raise CurrentnessError("target file changed")
        if selected.format != "epub":
            raise UnsupportedMediaError("unsupported media")

        repository: ReportRepository | None = None
        try:
            repository = self._repository_factory()
            current_document = repository.current_optional(
                self._connection.name, self._library.name, item, selected.identity
            )
            expected_current = (
                None
                if current_document is None
                else MetadataReport.from_json(current_document).report_id
            )

            try:
                with self._timing.measure("extraction", "none"):
                    local = self._inspector.inspect(
                        self._mount,
                        selected.relative_path,
                        self._limits.source_archive_bytes,
                    )
            except MetadataReportError:
                raise
            except Exception as error:
                # The inspector reads owner-supplied media off a mount, so its
                # failures are archive, filesystem, and decoding faults it does
                # not declare. All of them mean the same thing to a caller --
                # this item could not be inspected -- which is a per-item
                # outcome that leaves the rest of a batch runnable.
                raise ItemInspectionError("EPUB inspection failed") from error
        except MetadataReportError as error:
            _release_repository(repository, error)
            raise
        except Exception as error:
            # Covers the repository handle and the current-report read as well
            # as the inspection above; neither seam declares an error type, and
            # this layer owes its caller a ``MetadataReportError`` rather than a
            # raw adapter fault. The repository is released on every path.
            failure = MetadataReportError("EPUB inspection failed")
            _release_repository(repository, failure)
            raise failure from error

        target_title_claim = (
            ()
            if initial.title is None
            else (
                LocalClaim.create(
                    kind=ClaimKind.TITLE,
                    observed=initial.title,
                    canonical=normalize_title(initial.title),
                    excerpt=initial.title,
                    applicability="not_applicable",
                    basis="target_metadata",
                    checksum="not_applicable",
                    applicability_basis="not_applicable",
                    scope=ClaimScope.WORK,
                    occurrence=0,
                ),
            )
        )
        # Claims are evidence, not a set: retain the target observation first and
        # then the inspector's DOM/source occurrence order.  Content-addressed
        # references are deliberately not an ordering key.
        combined_claims = (*target_title_claim, *local.claims)
        plan = metadata_discovery_plan(initial, combined_claims)
        observations: list[ProviderObservation] = []
        descriptors: list[ProviderCoverDescriptor] = []
        cover_aggregate_deadline = time.monotonic() + 60.0
        for binding in self._providers:
            if not binding.enabled:
                disabled_lanes = tuple(
                    ProviderLaneObservation(
                        lane.identity,
                        lane.kind,
                        ProviderStatus.DISABLED,
                        lane.input_references,
                        lane.matched_family,
                    )
                    for lane in plan.lanes
                )
                observations.append(
                    ProviderObservation(
                        binding.name,
                        ProviderStatus.DISABLED,
                        query_kind=(
                            disabled_lanes[0].kind
                            if len(disabled_lanes) == 1
                            else (
                                ProviderQueryKind.DISCOVERY
                                if disabled_lanes
                                else ProviderQueryKind.NONE
                            )
                        ),
                        authority=binding.authority,
                        lanes=disabled_lanes,
                    )
                )
                continue
            if plan.overflow:
                observations.append(
                    ProviderObservation(
                        binding.name,
                        ProviderStatus.QUERY_LIMIT_EXCEEDED,
                        authority=binding.authority,
                        diagnostics=provider_attempt_diagnostics(
                            (),
                            status=ProviderStatus.QUERY_LIMIT_EXCEEDED.value,
                        ),
                    )
                )
                continue
            try:
                provider = binding.factory()
            except CredentialError as error:
                failure = CredentialError(
                    "provider credential unavailable", provider=binding.name
                )
                _release_repository(repository, failure)
                raise failure from error
            except Exception as error:
                # The branch above already carries every credential failure the
                # factory declares. What reaches here is a factory that broke
                # for some other reason, and calling that a credential failure
                # named a provider's stored secret as the cause, marked the
                # preparation non-retryable on ``authentication``, and recorded
                # a durable authentication fault against the provider's
                # recovery health. Report it as what it is: the provider could
                # not be constructed.
                failure = MetadataReportError("provider construction failed")
                _release_repository(repository, failure)
                raise failure from error
            try:
                with self._timing.measure("provider_search", "provider"):
                    observation = provider.observe(
                        plan, deadline=cover_aggregate_deadline
                    )
            except Exception:
                # ``MetadataProvider`` is a protocol over remote adapters and
                # declares no error type, so a provider that fails in any way
                # is one unavailable lane rather than a failed report. Breadth
                # is the contract here: the report must carry one terminal
                # observation for every configured lane, and a provider allowed
                # to abort the run would let one adapter deny the owner a
                # package the remaining providers can still support.
                #
                # The access time is an optional diagnostic an adapter may
                # expose as a computed property. ``getattr``'s default absorbs
                # only ``AttributeError``, and the value it returns still has to
                # satisfy the domain's access-time rule, so both the read and
                # the check are guarded here: a provider whose property raises,
                # or answers with something that is not an access time, must not
                # take the unavailable lane this handler exists to produce.
                # ``None`` is the recorded "not obtainable" value, as below.
                accessed_at: str | None
                try:
                    reading = getattr(provider, "accessed_at", None)
                    accessed_at = (
                        None
                        if reading is None
                        else validate_provider_access_time(reading)
                    )
                except Exception:
                    accessed_at = None
                observation = provider_observation(
                    binding.name,
                    tuple(
                        ProviderLaneResult(
                            lane,
                            ProviderStatus.UNAVAILABLE,
                            accessed_at=accessed_at,
                        )
                        for lane in plan.lanes
                    ),
                    authority=binding.authority,
                )
            if observation.provider != binding.name:
                failure = MetadataReportError("provider result is invalid")
                _release_repository(repository, failure)
                raise failure
            # Both reads are optional diagnostics an adapter may expose as a
            # computed property. A missing attribute already yields the default,
            # so only a property that itself fails reaches these handlers, and
            # its raisable set is the adapter's. ``None`` is the recorded "not
            # obtainable" value and is distinct from the empty default, so the
            # diagnostics stay truthful and the observation still stands.
            try:
                attempt_records = getattr(provider, "attempt_records", ())
            except Exception:
                attempt_records = None
            try:
                incomplete_causes = getattr(provider, "incomplete_causes", ())
            except Exception:
                incomplete_causes = None
            observation = ProviderObservation(
                observation.provider,
                observation.status,
                observation.input_references,
                observation.candidates,
                observation.query_kind,
                binding.authority,
                observation.lanes,
                observation.convergence,
                observation.accessed_at,
                merge_provider_diagnostics(
                    observation.diagnostics,
                    provider_attempt_diagnostics(
                        attempt_records,
                        status=observation.status.value,
                        incomplete_causes=incomplete_causes,
                    ),
                ),
                observation.request_accounting,
                observation.edition_enrichment_overflow,
            )
            observations.append(observation)
            candidate_descriptors = getattr(provider, "cover_descriptors", ())
            if type(candidate_descriptors) is not tuple or any(
                type(value) is not ProviderCoverDescriptor
                or value.provider != binding.name
                for value in candidate_descriptors
            ):
                failure = MetadataReportError("provider cover result is invalid")
                _release_repository(repository, failure)
                raise failure
            descriptors.extend(candidate_descriptors)

        reasons = tuple(
            sorted(
                {f"{claim.basis}_{claim.kind.value}" for claim in local.claims}
                | ({"rear_resource_limit"} if local.rear_resource_limit else set())
                or {"no_opening_claims"}
            )
        )
        try:
            cover_sources = (
                local.cover_sources
                if self._cover_observer is None
                else self._cover_observer.observe(
                    initial,
                    tuple(observations),
                    local.cover_sources,
                    tuple(descriptors),
                    cover_aggregate_deadline,
                )
            )
            report = MetadataReport.create(
                target=TargetFacts(
                    self._connection.name,
                    self._library.name,
                    item,
                    initial.fields,
                ),
                file=FileFacts(file_index, selected.identity, local.content_digest),
                local_claims=combined_claims,
                local_reasons=reasons,
                providers=tuple(sorted(observations, key=lambda value: value.provider)),
                field_results=derive_field_results(combined_claims),
                cover_sources=cover_sources,
            )
        except Exception as error:
            # Cleanup only: the report assembly spans domain construction over
            # everything the providers returned, and this handler exists to
            # release the repository handle rather than to classify anything.
            # The failure is re-raised unchanged, and a ``close`` that itself
            # fails no longer displaces it: the release failure is noted on it
            # instead.
            _release_repository(repository, error)
            raise
        try:

            def currentness() -> bool:
                if self._publication_authority_guard is not None:
                    self._publication_authority_guard()
                return require_report_current(
                    self._target,
                    self._inspector,
                    self._mount,
                    self._limits,
                    report,
                    require_singular=self._require_singular_target,
                )

            persisted = (
                repository.publish(report, currentness, expected_current)
                if self._publication_receipt is None
                else repository.publish(
                    report,
                    currentness,
                    expected_current,
                    self._publication_receipt,
                )
            )
            if (
                persisted != report.to_json()
                or repository.get(report.report_id) != persisted
            ):
                raise MetadataReportError("persisted report readback failed")
            return persisted
        except TargetConnectionError:
            raise
        except MetadataReportError as error:
            if str(error) in {"target or media changed", "current report changed"}:
                raise CurrentnessError("target or media changed") from error
            raise
        except Exception as error:
            # Publication runs the currentness guard, the injected receipt, and
            # the durable write and read-back through one undeclared repository
            # seam. The typed outcomes callers act on -- a lost connection and
            # the two currentness messages -- are already re-raised above, so
            # what remains is a publication that did not complete, whatever
            # stopped it, and the cause is chained.
            raise MetadataReportError("report publication failed") from error
        finally:
            if repository is not None:
                repository.close()

    def _safe_read(self, item: str) -> TargetSnapshot:
        try:
            value = self._target.read(item)
        except (MetadataReportError, TargetConnectionError):
            raise
        except Exception as error:
            # The target is a protocol over a remote library server and declares
            # no error type beyond the connection failure re-raised above, so
            # transport, decoding, and shape faults all arrive untyped. They are
            # one outcome to every caller: the target could not be read.
            raise MetadataReportError("target read failed") from error
        if type(value) is not TargetSnapshot:
            raise MetadataReportError("target response is invalid")
        return value


def require_report_current(
    target: TargetReader,
    inspector: EpubInspector,
    mount: MediaMountConfiguration | None,
    limits: ReportLimits,
    report: MetadataReport,
    *,
    require_singular: bool = False,
) -> bool:
    """Revalidate the exact target, media, and ABS cover bound to a report."""
    if (
        mount is None
        or type(report) is not MetadataReport
        or type(limits) is not ReportLimits
    ):
        raise CurrentnessError("target or media changed")
    try:
        final = target.read(report.target.item)
    except (MetadataReportError, TargetConnectionError):
        raise
    except Exception as error:
        # Same undeclared target seam as ``_safe_read``. Breadth is what keeps
        # this revalidation fail-closed: a read that did not succeed cannot
        # demonstrate currentness, so it must raise rather than return.
        raise MetadataReportError("target read failed") from error
    if type(final) is not TargetSnapshot:
        raise MetadataReportError("target response is invalid")
    matches = tuple(
        value for value in final.files if value.identity == report.file.identity
    )
    if (
        final.media_kind != "ebook"
        or final.title != report.target.current_title
        or final.fields != report.target.fields
        or (require_singular and len(final.files) != 1)
        or len(matches) != 1
        or matches[0].format != "epub"
        or not inspector.unchanged(
            mount,
            matches[0].relative_path,
            report.file.content_digest,
            limits.source_archive_bytes,
        )
    ):
        raise CurrentnessError("target or media changed")
    expected_cover = next(
        (
            source.artifact.original_digest
            for source in report.cover_sources
            if source.kind is CoverSourceKind.ABS and source.artifact is not None
        ),
        None,
    )
    expected_absent = any(
        source.kind is CoverSourceKind.ABS and source.state is CoverSourceState.ABSENT
        for source in report.cover_sources
    )
    if expected_cover is not None or expected_absent:
        reader = getattr(target, "read_cover", None)
        try:
            current_cover = (
                reader(final.cover)
                if callable(reader) and final.cover is not None
                else None
            )
        except (MetadataReportError, OSError, ValueError):
            raise CurrentnessError("target or media changed") from None
        if expected_absent and current_cover is not None:
            raise CurrentnessError("target or media changed")
        if expected_cover is not None and (
            type(current_cover) is not bytes
            or hashlib.sha256(current_cover).hexdigest() != expected_cover
        ):
            raise CurrentnessError("target or media changed")
    return True


__all__ = [
    "CoverObserver",
    "CoverReadError",
    "CoverSourceChangedError",
    "CoverTimeoutError",
    "CredentialError",
    "CurrentnessError",
    "EpubInspector",
    "GenerateMetadataReport",
    "ArtifactAuthorityError",
    "InspectionResult",
    "ItemInspectionError",
    "MetadataDiscoveryLane",
    "MetadataDiscoveryPlan",
    "MetadataLookup",
    "MetadataProvider",
    "MetadataReportError",
    "ProviderBinding",
    "ProviderCoverDescriptor",
    "ProviderLaneResult",
    "ReportRepository",
    "ReportNotFoundError",
    "TargetFile",
    "TargetNotFoundError",
    "TargetReader",
    "TargetSnapshot",
    "UnsupportedMediaError",
    "merge_provider_cover_descriptor",
    "merge_provider_cover_descriptors",
    "merge_provider_diagnostics",
    "metadata_discovery_plan",
    "metadata_lookup",
    "provider_isbn_applicability",
    "provider_observation",
    "qualify_provider_observation",
    "require_report_current",
]
