"""Synchronous report orchestration and its narrow application-owned ports."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Never, Protocol, cast

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
    provider_result_diagnostics,
)
from mechanical_librarian.application.operations import StageTimer
from mechanical_librarian.application.provider_evidence import (
    ProviderOperationalEvidence,
)
from mechanical_librarian.domain.audio_inspection import (
    AudioObservation,
    AudioObservationKind,
    AudioSidecarDigest,
    AudioTrackDigest,
)
from mechanical_librarian.domain.audiobook_corroboration import (
    _chapter_candidate_support,
    _local_identifier_asins,
    candidate_recording_runtime_veto,
    runtime_corroborated_candidate,
    runtime_short_of_stated,
)
from mechanical_librarian.domain.audiobook_corroboration import (
    admitted_audiobook_identity as _domain_admitted_audiobook_identity,
)
from mechanical_librarian.domain.audiobook_corroboration import (
    recording_identity_basis as _domain_recording_identity_basis,
)
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
    AUDIO_DISCOVERY_TITLE_BASES,
    AUDIO_FILE_FORMATS,
    AUDIOBOOK_PROVIDER_AUTHORITIES,
    MAX_AUDIO_TRACKS,
    MAX_CANDIDATES,
    AudioFacts,
    ClaimKind,
    ClaimScope,
    DateLinkConfidence,
    EditionEnrichmentOverflow,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderEvidenceKind,
    ProviderLaneCandidate,
    ProviderLaneObservation,
    ProviderObservation,
    ProviderQueryKind,
    ProviderRequestAccounting,
    ProviderStatus,
    PublicationDateRelation,
    ReportValueError,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    WorkIdentityContradiction,
    audio_set_identity,
    canonical_asin,
    complete_creator_identity_bindings,
    default_target_fields,
    derive_field_results,
    discovery_lane_identity,
    isbn_family,
    normalize_title,
    provider_candidate_exact_work_identity_admission,
    provider_candidate_set_exact_work_identity_admission,
    provider_candidate_set_work_identity_admission,
    provider_candidate_work_identity_contradiction,
    provider_lane_candidate_reference,
    recording_variant_conflict,
    surviving_discovery_titles,
    validate_provider_access_time,
    work_discovery_lane_payloads,
    work_ladder_diagnostics,
)
from mechanical_librarian.domain.work_identity import (
    WorkDispatchLineage,
    WorkQuery,
    WorkTitleTransformationLineage,
    replay_provider_work_identity_admission,
    work_query_ladder,
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


class AudioItemInspectionError(ItemInspectionError):
    """One audiobook item could not be inspected, with its closed outcome.

    ``track`` names the ordinal that stopped a composed item, so an owner is
    told which of a hundred files could not be read rather than that the item
    could not be.  It is ``None`` for a single-file item, which has no ordinal
    to name, and for a bound the whole item exceeded.
    """

    def __init__(self, outcome: object = None, track: int | None = None) -> None:
        super().__init__(outcome)
        self.track = track


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
    # The position the target itself declares for this file, where it declares
    # one.  It orders nothing -- the service's own list order is the order --
    # and exists so a set missing a part can be observed rather than assumed.
    sequence: int | None = None


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


def _validate_recording_context(
    audio: AudioFacts | None, title: str | None, authors: tuple[str, ...]
) -> None:
    """Keep discovery and physical lookup context on the same complete boundary."""
    if (
        (audio is not None and type(audio) is not AudioFacts)
        or type(authors) is not tuple
        or any(type(value) is not str for value in authors)
        or (title is not None and type(title) is not str)
        or (title is None) != (not authors)
    ):
        raise MetadataReportError("recording context is invalid")
    if title is not None:
        normalize_title(title)
        for author in authors:
            normalize_title(author)
        if len({normalize_title(value).casefold() for value in authors}) != len(
            authors
        ):
            raise MetadataReportError("recording context is invalid")


@dataclass(frozen=True, slots=True)
class MetadataLookup:
    """One closed provider lookup, independent of provider wire shape."""

    kind: ProviderQueryKind
    local_claims: tuple[LocalClaim, ...]
    title: str | None = None
    authors: tuple[str, ...] = ()
    matched_family: str | None = None

    audio: AudioFacts | None = None
    work_title: str | None = None
    work_authors: tuple[str, ...] = ()
    requested_asin: str | None = None
    # Set only on an audiobook's alternative WORK lane, whose title is a
    # discovery title rather than the target's own (#1255).
    title_basis: str | None = None

    def __post_init__(self) -> None:
        _validate_recording_context(self.audio, self.work_title, self.work_authors)
        if (
            type(self.kind) is not ProviderQueryKind
            or type(self.local_claims) is not tuple
            or any(type(value) is not LocalClaim for value in self.local_claims)
            or type(self.authors) is not tuple
            or any(type(value) is not str for value in self.authors)
            or self.title_basis is not None
            and (
                self.title_basis not in AUDIO_DISCOVERY_TITLE_BASES
                or self.kind is not ProviderQueryKind.WORK
                or self.audio is None
            )
        ):
            raise MetadataReportError("provider lookup is invalid")
        if self.requested_asin is not None and (
            self.kind is not ProviderQueryKind.ASIN
            or type(self.requested_asin) is not str
            or canonical_asin(self.requested_asin) != self.requested_asin
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
MAX_PROVIDER_DISCOVERY_QUERIES = 9
# An audiobook carries far fewer identifier surfaces than an EPUB does, and
# each distinct ASIN costs one product lookup per marketplace tried, so the
# ASIN fan-out is bounded tighter than the ISBN one.
MAX_ASIN_DISCOVERY_LANES = 4


@dataclass(frozen=True, slots=True)
class MetadataDiscoveryLane:
    identity: str
    kind: ProviderQueryKind
    input_references: tuple[str, ...]
    matched_family: str | None = None
    canonical_isbns: tuple[str, ...] = ()
    title: str | None = None
    authors: tuple[str, ...] = ()
    canonical_asin: str | None = None
    title_basis: str | None = None

    def __post_init__(self) -> None:
        if (
            not self.identity.startswith("lane_")
            or len(self.identity) != 69
            or any(
                character not in "0123456789abcdef"
                for character in self.identity.removeprefix("lane_")
            )
            or type(self.kind) is not ProviderQueryKind
            or self.kind
            not in {
                ProviderQueryKind.ISBN,
                ProviderQueryKind.WORK,
                ProviderQueryKind.ASIN,
            }
            or type(self.input_references) is not tuple
            or tuple(sorted(set(self.input_references))) != self.input_references
            or self.title_basis is not None
            and (
                self.title_basis not in AUDIO_DISCOVERY_TITLE_BASES
                or self.kind is not ProviderQueryKind.WORK
            )
        ):
            raise MetadataReportError("provider discovery lane is invalid")
        if self.kind is ProviderQueryKind.ISBN:
            if (
                self.matched_family is None
                or not self.canonical_isbns
                or self.title is not None
                or self.authors
                or self.canonical_asin is not None
            ):
                raise MetadataReportError("provider discovery lane is invalid")
        elif self.kind is ProviderQueryKind.ASIN:
            # One ASIN is one product lookup, so it is one lane. The value is
            # canonical rather than shape-filtered: an ISBN-10-shaped ASIN is a
            # genuine audiobook identifier (ADR-0061).
            if (
                self.canonical_asin is None
                or canonical_asin(self.canonical_asin) != self.canonical_asin
                or self.matched_family is not None
                or self.canonical_isbns
                or self.title is not None
                or self.authors
                or not self.input_references
            ):
                raise MetadataReportError("provider discovery lane is invalid")
        elif (
            self.matched_family is not None
            or self.canonical_isbns
            or self.title is None
            or not self.authors
            or self.canonical_asin is not None
        ):
            raise MetadataReportError("provider discovery lane is invalid")


def _metadata_discovery_query_count(
    lanes: tuple[MetadataDiscoveryLane, ...], *, audiobook: bool = False
) -> int:
    return sum(
        len(work_query_ladder(lane.title, lane.authors, audiobook=audiobook))
        if lane.kind is ProviderQueryKind.WORK and lane.title is not None
        else 1
        for lane in lanes
    )


@dataclass(frozen=True, slots=True)
class MetadataDiscoveryPlan:
    local_claims: tuple[LocalClaim, ...]
    lanes: tuple[MetadataDiscoveryLane, ...]
    overflow: bool = False

    audio: AudioFacts | None = None
    work_title: str | None = None
    work_authors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_recording_context(self.audio, self.work_title, self.work_authors)
        if (
            type(self.local_claims) is not tuple
            or any(type(value) is not LocalClaim for value in self.local_claims)
            or type(self.lanes) is not tuple
            or any(type(value) is not MetadataDiscoveryLane for value in self.lanes)
            or len(self.lanes) > MAX_ISBN_DISCOVERY_LANES + 1
            or _metadata_discovery_query_count(
                self.lanes, audiobook=self.audio is not None
            )
            > MAX_PROVIDER_DISCOVERY_QUERIES
            or tuple(value.identity for value in self.lanes)
            != tuple(sorted({value.identity for value in self.lanes}))
            or type(self.overflow) is not bool
            or (self.overflow and self.lanes)
        ):
            raise MetadataReportError("provider discovery plan is invalid")
        known = {value.reference for value in self.local_claims}
        asin_lanes = tuple(
            lane for lane in self.lanes if lane.kind is ProviderQueryKind.ASIN
        )
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
            # One asserted WORK lane and, on an audiobook plan only, one
            # alternative beside or instead of it.
            or sum(
                lane.kind is ProviderQueryKind.WORK and lane.title_basis is None
                for lane in self.lanes
            )
            > 1
            or sum(lane.title_basis is not None for lane in self.lanes)
            > (0 if self.audio is None else 1)
            or len(asin_lanes) > MAX_ASIN_DISCOVERY_LANES
            or len({lane.canonical_asin for lane in asin_lanes}) != len(asin_lanes)
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

    recording_lookup_candidates: tuple[ProviderCandidate, ...] = ()
    exhausted_dispatch: tuple[WorkDispatchLineage, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.lane) is not MetadataDiscoveryLane
            or type(self.status) is not ProviderStatus
            or type(self.candidates) is not tuple
            or any(type(value) is not ProviderCandidate for value in self.candidates)
            or type(self.recording_lookup_candidates) is not tuple
            or len(self.recording_lookup_candidates) > MAX_CANDIDATES
            or any(
                type(value) is not ProviderCandidate
                for value in self.recording_lookup_candidates
            )
            or (
                self.recording_lookup_candidates
                and (
                    self.lane.kind is not ProviderQueryKind.WORK
                    or self.status is not ProviderStatus.NO_MATCH
                    or self.dispatch_lineage is not None
                    or self.transformation_lineage is not None
                )
            )
            or (self.status is ProviderStatus.CANDIDATES) != bool(self.candidates)
            or not diagnostics_are_canonical(self.diagnostics)
            or any(
                value.component is not FailureComponent.PROVIDER
                or value.stage
                not in {
                    FailureStage.IDENTITY,
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
            or type(self.exhausted_dispatch) is not tuple
            or any(
                type(value) is not WorkDispatchLineage
                for value in self.exhausted_dispatch
            )
            or (
                self.exhausted_dispatch
                and (
                    self.lane.kind is not ProviderQueryKind.WORK
                    or self.status is not ProviderStatus.NO_MATCH
                    or self.dispatch_lineage is not None
                )
            )
        ):
            raise MetadataReportError("provider lane result is invalid")
        if self.accessed_at is not None:
            validate_provider_access_time(self.accessed_at)


def _work_discovery_lanes(
    target_fields: tuple[TargetField, ...],
    local_claims: tuple[LocalClaim, ...],
    audio: AudioFacts | None = None,
) -> tuple[MetadataDiscoveryLane, ...]:
    """Build the WORK lanes both media plans share from one payload set."""
    return tuple(
        MetadataDiscoveryLane(
            payload.identity,
            ProviderQueryKind.WORK,
            payload.input_references,
            title=payload.title,
            authors=payload.authors,
            title_basis=payload.title_basis,
        )
        for payload in work_discovery_lane_payloads(
            target_fields,
            local_claims,
            None if audio is None else audio.discovery_titles,
        )
    )


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
        identity = discovery_lane_identity(
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

    lanes.extend(_work_discovery_lanes(target.fields, local_claims))
    ordered_lanes = tuple(sorted(lanes, key=lambda value: value.identity))
    if _metadata_discovery_query_count(ordered_lanes) > MAX_PROVIDER_DISCOVERY_QUERIES:
        return MetadataDiscoveryPlan(local_claims, (), True)
    return MetadataDiscoveryPlan(local_claims, ordered_lanes)


def provider_discovery_lane_order(
    plan: MetadataDiscoveryPlan,
) -> tuple[MetadataDiscoveryLane, ...]:
    """Schedule one unambiguous ISBN lane before its optional work fallback.

    An audiobook's alternative WORK lane always follows its asserted one,
    whose result decides whether the alternative is dispatched at all.
    """
    eligible = _eligible_isbn_lane(plan)
    if eligible is None and all(lane.title_basis is None for lane in plan.lanes):
        return plan.lanes
    return tuple(
        sorted(
            plan.lanes,
            key=lambda lane: (
                eligible is not None and lane.kind is ProviderQueryKind.WORK,
                eligible is not None and lane is not eligible,
                lane.title_basis is not None,
            ),
        )
    )


def alternative_work_lane_permitted(
    plan: MetadataDiscoveryPlan, asserted: ProviderLaneResult | None
) -> bool:
    """Permit the alternative WORK lane only after an asserted lane found nothing.

    No match -- an empty ladder or one whose every product admission refused
    -- is the only asserted outcome that permits it, and a plan whose series
    collapse dropped the asserted lane permits it outright. A failure, an
    overflow or insufficient input is never reclassified as permission.
    """
    return not any(
        lane.kind is ProviderQueryKind.WORK and lane.title_basis is None
        for lane in plan.lanes
    ) or (asserted is not None and asserted.status is ProviderStatus.NO_MATCH)


def identifier_suppresses_discovery(
    plan: MetadataDiscoveryPlan,
    results: list[ProviderLaneResult],
    lane: MetadataDiscoveryLane,
) -> bool:
    """Keep discovery unspent only after one applicable identifier result.

    An audiobook's alternative WORK lane is always left unspent here: only
    Audible dispatches it, so every adapter that asks suppresses it (#1255).
    """
    eligible = _eligible_isbn_lane(plan)
    work_contexts = tuple(
        (planned.title, planned.authors)
        for planned in plan.lanes
        if planned.kind is ProviderQueryKind.WORK
        and planned.title is not None
        and planned.title_basis is None
    )
    applicable_identifier = (
        eligible is not None
        and len(work_contexts) == 1
        and any(
            result.lane.identity == eligible.identity
            and result.status is ProviderStatus.CANDIDATES
            and bool(result.candidates)
            and all(
                provider_candidate_work_identity_contradiction(
                    candidate, work_contexts[0][0], work_contexts[0][1]
                )
                is WorkIdentityContradiction.NOT_CONTRADICTED
                for candidate in result.candidates
            )
            and provider_candidate_set_exact_work_identity_admission(
                result.candidates,
                work_contexts[0][0],
                work_contexts[0][1],
                audiobook=plan.audio is not None,
            ).admitted
            for result in results
        )
    )
    return lane.title_basis is not None or (
        lane.kind is ProviderQueryKind.WORK
        and eligible is not None
        and applicable_identifier
        and not any(
            result.lane.kind is ProviderQueryKind.ISBN
            and result.lane.identity != eligible.identity
            and result.status in PROVIDER_RECOVERY_ELIGIBLE_STATUSES
            for result in results
        )
    )


def _eligible_isbn_lane(
    plan: MetadataDiscoveryPlan,
) -> MetadataDiscoveryLane | None:
    eligible = tuple(
        lane
        for lane in plan.lanes
        if lane.kind is ProviderQueryKind.ISBN
        and lane.matched_family in usable_isbn_families(plan.local_claims)
    )
    return eligible[0] if len(eligible) == 1 else None


PROVIDER_RECOVERY_ELIGIBLE_STATUSES = frozenset(
    {
        ProviderStatus.AUTHENTICATION,
        ProviderStatus.AUTHORIZATION,
        ProviderStatus.RATE_LIMITED,
        ProviderStatus.UNAVAILABLE,
        ProviderStatus.INCOMPLETE,
    }
)


def audiobook_discovery_plan(
    target: TargetSnapshot,
    local_claims: tuple[LocalClaim, ...],
    *,
    audio: AudioFacts | None = None,
) -> MetadataDiscoveryPlan:
    """Plan one audiobook's discovery from its own identifier surfaces.

    An audiobook's identifier is an ASIN, so each distinct canonical ASIN the
    local surfaces carry becomes one marketplace-resolved product lane. An item
    with no ASIN at all -- 57.6% of the reference library measured on #925 --
    falls back to the title and author lane, which is the only way discovery
    reaches it. The two are alternatives rather than a pair: an item that
    already names its product does not also need a search for it.

    An ASIN the target merely asserts is not one of these surfaces and never
    suppresses the search lane: it is fed to the per-ASIN providers afterwards,
    by `audiobook_asin_feed_plan`, where the runtime band still judges what it
    returns. The alternatives rule above is about *local* identifiers only.

    The search may carry a second, alternative WORK lane from the item's first
    surviving discovery title, dispatched only when the asserted lane finds
    nothing (`alternative_work_lane_permitted`), or alone when the asserted
    title collapses to a series name the target records (#1255).
    """
    context = metadata_lookup(target, ())
    canonical_asins = sorted(_local_identifier_asins(local_claims))
    if len(canonical_asins) > MAX_ASIN_DISCOVERY_LANES:
        return MetadataDiscoveryPlan(
            local_claims, (), True, audio, context.title, context.authors
        )
    lanes: list[MetadataDiscoveryLane] = []
    for asin in canonical_asins:
        references = tuple(
            sorted(
                claim.reference
                for claim in local_claims
                if claim.kind is ClaimKind.ASIN
                and claim.basis != "target_metadata"
                and canonical_asin(claim.canonical) == asin
            )
        )
        lanes.append(_asin_discovery_lane(asin, references))
    if not lanes:
        lanes.extend(_work_discovery_lanes(target.fields, local_claims, audio))
    ordered_lanes = tuple(sorted(lanes, key=lambda value: value.identity))
    if (
        _metadata_discovery_query_count(ordered_lanes, audiobook=audio is not None)
        > MAX_PROVIDER_DISCOVERY_QUERIES
    ):
        return MetadataDiscoveryPlan(
            local_claims, (), True, audio, context.title, context.authors
        )
    return MetadataDiscoveryPlan(
        local_claims,
        ordered_lanes,
        audio=audio,
        work_title=context.title,
        work_authors=context.authors,
    )


def _asin_discovery_lane(
    asin: str, references: tuple[str, ...]
) -> MetadataDiscoveryLane:
    """Build one product lane under the single lane-identity payload.

    Every ASIN lane in this product is keyed on the same three values whatever
    fed it, so a lane the target asserted and a lane a tag carried are the same
    lane when they name one ASIN off one reference set, and are distinct lanes
    otherwise. The origin is derivable from `input_references` and is
    deliberately not part of the key: keying on it would make one product two
    lookups.
    """
    return MetadataDiscoveryLane(
        discovery_lane_identity(
            {
                "canonical_asin": asin,
                "input_references": list(references),
                "kind": ProviderQueryKind.ASIN.value,
            }
        ),
        ProviderQueryKind.ASIN,
        references,
        canonical_asin=asin,
    )


def promoted_asins(
    observations: tuple[ProviderObservation, ...],
) -> tuple[str, ...]:
    """Return the edition ASINs one search pass corroborated for this recording.

    Only a search observation promotes. An audiobook authority whose lanes are
    all work lanes reached its candidates through the title-and-author ladder,
    and edition-scope marketplace corroboration identifies the recording the
    runtime band admitted. Informational ASIN hints alone never promote.
    """
    return tuple(
        sorted(
            {
                value
                for observation in observations
                if observation.authority in AUDIOBOOK_PROVIDER_AUTHORITIES
                and observation.lanes
                and all(
                    lane.kind is ProviderQueryKind.WORK for lane in observation.lanes
                )
                for candidate in observation.candidates
                if any(
                    evidence.kind is ProviderEvidenceKind.ASIN_MARKETPLACE
                    and evidence.scope is ClaimScope.EDITION
                    for evidence in candidate.evidence
                )
                for claim in candidate.claims
                if claim.scope is ClaimScope.EDITION
                and claim.kind is ClaimKind.ASIN
                and claim.state is FieldState.VALID
                and (value := canonical_asin(claim.value)) is not None
            }
        )
    )


def _asserted_asin_references(
    target: TargetSnapshot, local_claims: tuple[LocalClaim, ...]
) -> dict[str, tuple[str, ...]]:
    """Map each target-asserted canonical ASIN to the claim that records it."""
    asserted = {
        value
        for field in target.fields
        if field.destination is TargetDestination.ASIN
        and field.state is TargetFieldState.VALUE
        and type(field.value) is str
        and (value := canonical_asin(field.value)) is not None
    }
    references = {
        asin: tuple(
            sorted(
                claim.reference
                for claim in local_claims
                if claim.kind is ClaimKind.ASIN
                and claim.basis == "target_metadata"
                and canonical_asin(claim.canonical) == asin
            )
        )
        for asin in sorted(asserted)
    }
    # A lane needs an input reference the report retains, so an assertion with
    # no claim beside it feeds nothing rather than being fabricated a source.
    return {asin: value for asin, value in references.items() if value}


def audiobook_asin_feed_plan(
    target: TargetSnapshot,
    plan: MetadataDiscoveryPlan,
    observations: tuple[ProviderObservation, ...],
) -> MetadataDiscoveryPlan | None:
    """Plan the second, identifier-shaped dispatch of one preparation pass.

    Per-ASIN enrichment used to depend on the recording carrying its own ASIN,
    which on the reference library is one item in four. Two more origins name a
    product without being one of the item's surfaces: an ASIN the search pass
    corroborated, and one the target asserts. Both are fed here, promoted
    first, so the order over an item is always local, promoted, asserted.

    The bound is the plan's own `MAX_ASIN_DISCOVERY_LANES`, counted across
    every origin, and there is no second round: this reads phase-one
    observations and is never run over its own results, so no feedback loop
    exists to bound. An item whose phase one already ran ASIN lanes feeds
    nothing -- it has its identifier, and re-asking about the same product
    would spend egress to learn what it already knows.
    """
    if plan.overflow or any(lane.kind is ProviderQueryKind.ASIN for lane in plan.lanes):
        return None
    work_references = next(
        (
            lane.input_references
            for lane in plan.lanes
            if lane.kind is ProviderQueryKind.WORK
        ),
        (),
    )
    asserted = _asserted_asin_references(target, plan.local_claims)
    ordered: dict[str, tuple[str, ...]] = {}
    for asin in promoted_asins(observations):
        ordered[asin] = work_references
    for asin, references in asserted.items():
        # One ASIN promoted *and* asserted is one product, so it is one lane
        # whose references name both origins rather than two competing lanes.
        ordered[asin] = tuple(sorted(set(ordered.get(asin, ())) | set(references)))
    for observation in observations:
        if observation.authority not in AUDIOBOOK_PROVIDER_AUTHORITIES:
            continue
        for lane in observation.lanes:
            if lane.kind is not ProviderQueryKind.WORK:
                continue
            candidates = (
                observation.recording_lookup_candidates
                if lane.status is ProviderStatus.NO_MATCH
                else tuple(
                    candidate
                    for candidate in observation.candidates
                    if any(
                        value.edition_identity == candidate.identity
                        for value in lane.candidates
                    )
                )
            )
            for candidate in candidates:
                for claim in candidate.claims:
                    if (
                        claim.kind is ClaimKind.ASIN
                        and claim.state is FieldState.VALID
                        and (hint_asin := canonical_asin(claim.value)) is not None
                    ):
                        ordered[hint_asin] = tuple(
                            sorted(
                                set(ordered.get(hint_asin, ()))
                                | set(lane.input_references)
                            )
                        )
    room = MAX_ASIN_DISCOVERY_LANES - sum(
        lane.kind is ProviderQueryKind.ASIN for lane in plan.lanes
    )
    lanes = tuple(
        _asin_discovery_lane(asin, references)
        for asin, references in tuple(ordered.items())[:room]
    )
    if not lanes:
        return None
    return MetadataDiscoveryPlan(
        plan.local_claims,
        tuple(sorted(lanes, key=lambda value: value.identity)),
        audio=plan.audio,
        work_title=plan.work_title,
        work_authors=plan.work_authors,
    )


# One provider naming the same edition twice with claims that disagree has
# stated an ambiguous identity, not a malformed or over-bound response, so the
# report says so in the category ADR-0017 already reserves for identity.
_IDENTITY_CONFLICT_DIAGNOSTIC = FailureDiagnostic(
    FailureCategory.IDENTITY,
    FailureComponent.PROVIDER,
    FailureStage.IDENTITY,
    retryable=False,
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
                            (_IDENTITY_CONFLICT_DIAGNOSTIC,),
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
        if conflict:
            diagnostics = merge_provider_diagnostics(
                diagnostics, (_IDENTITY_CONFLICT_DIAGNOSTIC,)
            )

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
                result.exhausted_dispatch,
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
        recording_lookup_candidates=tuple(
            candidate
            for result in results
            for candidate in result.recording_lookup_candidates
        ),
    )


@dataclass(frozen=True, slots=True)
class InspectionResult:
    content_digest: str
    claims: tuple[LocalClaim, ...]
    cover_sources: tuple[CoverSource, ...] = ()
    rear_resource_limit: bool = False


@dataclass(frozen=True, slots=True)
class AudioInspectionResult:
    """One audiobook's closed local evidence set.

    There is deliberately no ``cover_sources`` member. An audiobook file's
    embedded art is recorded as presence inside ``audio`` and nothing here
    acquires, decodes, or elects it.

    The recognized-sidecar manifest travels inside ``audio``: it is what the
    evidence was read from, so it is what currentness has to re-prove.
    """

    content_digest: str
    claims: tuple[LocalClaim, ...]
    audio: AudioFacts


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


@dataclass(frozen=True, slots=True)
class ProviderResult:
    observation: ProviderObservation
    cover_descriptors: tuple[ProviderCoverDescriptor, ...] = ()
    evidence: ProviderOperationalEvidence = ProviderOperationalEvidence()


@dataclass(frozen=True, slots=True)
class ProviderFailureSnapshot:
    cover_descriptors: tuple[ProviderCoverDescriptor, ...] = ()
    evidence: ProviderOperationalEvidence = ProviderOperationalEvidence()

    def raise_from(self, cause: BaseException) -> Never:
        """Retain partial facts without changing cancellation catch semantics."""
        if isinstance(cause, asyncio.CancelledError):
            raise ProviderObservationCancelled(self) from cause
        if isinstance(cause, KeyboardInterrupt):
            raise ProviderObservationInterrupted(self) from cause
        if isinstance(cause, Exception):
            raise ProviderObservationFailure(self) from cause
        raise cause


class ProviderObservationFailure(Exception):
    def __init__(self, snapshot: ProviderFailureSnapshot) -> None:
        super().__init__("provider observation failed")
        self.snapshot = snapshot


class ProviderObservationCancelled(asyncio.CancelledError):
    def __init__(self, snapshot: ProviderFailureSnapshot) -> None:
        super().__init__("provider observation cancelled")
        self.snapshot = snapshot


class ProviderObservationInterrupted(KeyboardInterrupt):
    def __init__(self, snapshot: ProviderFailureSnapshot) -> None:
        super().__init__("provider observation interrupted")
        self.snapshot = snapshot


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


class AudioInspector(Protocol):
    """The one application-visible call behind F43's two isolated children.

    Both calls take the item's ordered tracks rather than one path: an
    audiobook's preparable unit is the item, and a single-file item is the
    one-track case of it rather than a separate shape.
    """

    def inspect(
        self,
        mount: MediaMountConfiguration,
        tracks: tuple[TargetFile, ...],
        maximum_bytes: int,
    ) -> AudioInspectionResult: ...
    def unchanged(
        self,
        mount: MediaMountConfiguration,
        relative_paths: tuple[str, ...],
        expected_digest: str,
        maximum_bytes: int,
        expected_sidecars: tuple[AudioSidecarDigest, ...] = (),
        expected_tracks: tuple[AudioTrackDigest, ...] = (),
    ) -> bool: ...


class MetadataProvider(Protocol):
    """Return complete evidence or raise a declared partial-snapshot failure.

    Cancellation and interruption carriers retain their BaseException semantics.
    """

    def observe(
        self,
        plan: MetadataDiscoveryPlan,
        *,
        deadline: float | None = None,
    ) -> ProviderResult: ...


class CoverObserver(Protocol):
    """Collect bounded report-only cover outcomes before final publication."""

    def observe_current(
        self,
        target: TargetSnapshot,
        local_sources: tuple[CoverSource, ...],
        aggregate_deadline: float | None = None,
    ) -> tuple[CoverSource, ...]: ...

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


def observe_provider(
    provider_name: str,
    authority: str | None,
    provider: MetadataProvider,
    plan: MetadataDiscoveryPlan,
    *,
    deadline: float | None = None,
) -> ProviderResult:
    """Normalize the complete result while retaining initial-report failure policy."""
    try:
        result = provider.observe(plan, deadline=deadline)
    except Exception as error:
        # Unexpected dispatch faults remain unavailable lanes in an initial report.
        snapshot = (
            error.snapshot
            if isinstance(error, ProviderObservationFailure)
            else ProviderFailureSnapshot()
        )
        try:
            reading = snapshot.evidence.accessed_at
            accessed_at = (
                None if reading is None else validate_provider_access_time(reading)
            )
        except Exception:
            accessed_at = None
        observation = provider_observation(
            provider_name,
            tuple(
                ProviderLaneResult(
                    lane, ProviderStatus.UNAVAILABLE, accessed_at=accessed_at
                )
                # An alternative work lane is recorded only once sent, so a
                # retry never replays one the adapter would suppress (#1255).
                for lane in plan.lanes
                if lane.title_basis is None
            ),
            authority=authority,
        )
        result = ProviderResult(
            observation, snapshot.cover_descriptors, snapshot.evidence
        )
    observation = result.observation
    if observation.provider != provider_name:
        raise MetadataReportError("provider result is invalid")
    return replace(
        result,
        observation=replace(
            observation,
            authority=authority,
            diagnostics=provider_result_diagnostics(observation, result.evidence),
        ),
    )


def usable_isbn_families(local_claims: tuple[LocalClaim, ...]) -> frozenset[str]:
    """Return only exact opening ISBN families eligible for ebook lookup."""
    return frozenset(
        family
        for claim in local_claims
        if claim.kind is ClaimKind.ISBN
        and claim.canonical is not None
        and claim.applicability in {"ebook", "unknown"}
        and claim.basis == "opening_matter"
        and claim.rear_provenance is None
        and (family := isbn_family(claim.canonical)) is not None
    )


def metadata_lookup(
    target: TargetSnapshot, local_claims: tuple[LocalClaim, ...]
) -> MetadataLookup:
    families = usable_isbn_families(local_claims)
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


def _identifier_lane_candidates(
    observation: ProviderObservation, lookup: MetadataLookup
) -> tuple[ProviderCandidate, ...]:
    """Corroboration-gate an ASIN lane the item's own surfaces did not name.

    A lane built from an embedded, sidecar, or path ASIN *is* this recording's
    identifier, so what a per-ASIN provider answers about it describes this
    recording by construction and passes through unchanged. A lane fed by a
    target assertion or by a gate promotion carries no such warrant: ADR-0061
    lets an assertion obtain enrichment evidence and never lets it settle
    identity. So the same runtime band that widens a search candidate decides
    here too, and every candidate it does not admit is demoted to exactly the
    work shape an uncorroborated search candidate gets.
    """
    if (
        lookup.kind is not ProviderQueryKind.ASIN
        or observation.status is not ProviderStatus.CANDIDATES
        or observation.authority not in AUDIOBOOK_PROVIDER_AUTHORITIES
    ):
        return observation.candidates
    independent = (
        lookup.requested_asin in _local_identifier_asins(lookup.local_claims)
        if lookup.requested_asin is not None
        else bool(_local_identifier_asins(lookup.local_claims))
    )
    if independent:
        return tuple(
            _shaped_with_runtime_comparison(
                candidate,
                not candidate_recording_runtime_veto(
                    candidate, lookup.local_claims, lookup.audio
                )
                and recording_variant_conflict(
                    candidate, lookup.local_claims, lookup.work_title or ""
                )
                is None,
                lookup.local_claims,
                recording_hints=True,
            )
            for candidate in observation.candidates
        )
    # A requested identifier and equal duration are not evidence of the work.
    runtime_candidates = tuple(
        c
        for c in observation.candidates
        if recording_variant_conflict(c, lookup.local_claims, lookup.work_title or "")
        is None
        and lookup.work_title is not None
        and lookup.work_authors
        and provider_candidate_exact_work_identity_admission(
            c,
            lookup.work_title,
            lookup.work_authors,
            audiobook=lookup.audio is not None,
        ).admitted
    )
    corroborated = runtime_corroborated_candidate(
        runtime_candidates, lookup.local_claims
    )
    admitted = None if corroborated is None else corroborated[0].identity
    return tuple(
        _shaped_with_runtime_comparison(
            candidate,
            candidate.identity == admitted
            or _chapter_candidate_support(
                candidate,
                lookup.audio,
                lookup.local_claims,
                lookup.work_title,
                lookup.work_authors,
                lookup.requested_asin,
            ),
            lookup.local_claims,
            recording_hints=True,
        )
        for candidate in observation.candidates
    )


def qualify_provider_observation(
    observation: ProviderObservation, lookup: MetadataLookup
) -> ProviderObservation:
    if (
        lookup.requested_asin is not None
        and observation.status is ProviderStatus.CANDIDATES
        and any(
            not any(
                claim.kind is ClaimKind.ASIN
                and claim.state is FieldState.VALID
                and claim.value == lookup.requested_asin
                for claim in candidate.claims
            )
            for candidate in observation.candidates
        )
    ):
        observation = replace(
            observation,
            status=ProviderStatus.MALFORMED,
            candidates=(),
            input_references=(),
        )
    if lookup.kind is not ProviderQueryKind.WORK:
        return ProviderObservation(
            observation.provider,
            observation.status,
            observation.input_references,
            _identifier_lane_candidates(observation, lookup),
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
            audiobook=lookup.audio is not None,
        )
        # An alternative lane never carries an overflow witness (#1255).
        qualified_witness = admission.admitted and lookup.title_basis is None
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
    set_admission = provider_candidate_set_work_identity_admission(
        observation.candidates,
        lookup.title or "",
        lookup.authors,
        observation.dispatch_lineage,
        audiobook=lookup.audio is not None,
    )
    if not set_admission.admitted:
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
            recording_lookup_candidates=tuple(
                candidate
                for candidate in observation.candidates
                if provider_candidate_exact_work_identity_admission(
                    candidate,
                    lookup.title or "",
                    lookup.authors,
                    audiobook=lookup.audio is not None,
                ).admitted
            )
            if lookup.audio is not None
            and lookup.title_basis is None
            and observation.authority in AUDIOBOOK_PROVIDER_AUTHORITIES
            else (),
        )
    # Gate order: the work-identity admission above has already decided that
    # this candidate set names the item's title and author. Only then may the
    # runtime band widen one of them from a work statement to an edition one.
    # A band that does not pass falls through to the work-scope shape every
    # other authority gets, so the widening below is dead code for an ebook
    # provider: none of them is an audiobook authority and none states a
    # `DURATION` claim to compare.
    corroborated = (
        runtime_corroborated_candidate(set_admission.candidates, lookup.local_claims)
        if observation.authority in AUDIOBOOK_PROVIDER_AUTHORITIES
        else None
    )
    selected = (
        min(set_admission.candidates, key=lambda value: value.identity)
        if corroborated is None
        else corroborated[0]
    )
    is_audio = observation.authority in AUDIOBOOK_PROVIDER_AUTHORITIES
    if lookup.title_basis is not None and (
        corroborated is None
        or not any(
            evidence.kind is ProviderEvidenceKind.ASIN_MARKETPLACE
            and evidence.scope is ClaimScope.EDITION
            for evidence in corroborated[0].evidence
        )
    ):
        # An alternative lane keeps only a band-corroborated recording with
        # its marketplace evidence; an admitted set without one is a refused
        # rung, and the ladder walks on (#1255).
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
    filtered = tuple(
        _shaped_with_runtime_comparison(
            candidate,
            corroborated is not None and candidate.identity == corroborated[0].identity,
            lookup.local_claims,
            recording_hints=is_audio,
        )
        for candidate in (
            (selected,)
            if lookup.title_basis is not None
            else set_admission.candidates
            if (is_audio and observation.authority != "itunes_search")
            or (
                lookup.audio is not None
                and all(
                    candidate.identity.startswith("hardcover-work-")
                    and candidate.identity == candidate.work_identity
                    for candidate in set_admission.candidates
                )
            )
            else (selected,)
        )
    )
    return ProviderObservation(
        observation.provider,
        ProviderStatus.CANDIDATES,
        (),
        filtered,
        ProviderQueryKind.WORK,
        observation.authority,
        accessed_at=observation.accessed_at,
        diagnostics=observation.diagnostics,
        request_accounting=observation.request_accounting,
        dispatch_lineage=observation.dispatch_lineage,
        transformation_lineage=set_admission.transformation,
    )


@dataclass(frozen=True, slots=True)
class QualifiedLaneObservation:
    """One lane's qualified observation and the ladder facts its result records.

    `exhausted_dispatch` and `diagnostics` belong to the lane result only. The
    provider aggregate lifts the refused-product count from its lanes once, in
    `provider_result_diagnostics`, so an adapter never restates it.
    """

    observation: ProviderObservation
    exhausted_dispatch: tuple[WorkDispatchLineage, ...] = ()
    diagnostics: tuple[FailureDiagnostic, ...] = ()


def observe_work_ladder(
    lookup: MetadataLookup,
    observe_rung: Callable[[WorkQuery], ProviderObservation],
) -> QualifiedLaneObservation:
    """Walk one WORK lane's query ladder, qualifying each rung as it returns.

    A rung with an admitted product ends the walk. So does any terminal
    failure, which returns that rung's own qualified result: a failure is never
    reclassified as a no-match, so the refusals before it are dropped with it.
    On an audiobook plan a rung that is empty, or whose products work-identity
    admission refuses, continues to the next rung, inside the dispatch budget
    the plan already reserved for the whole ladder. When every rung was refused
    or empty, the lane records each rung's lineage in ladder order with the
    empty-rung and refused-product counts, and keeps the recording hints of the
    earliest refused rung that had any. An ebook plan still stops at the first
    rung that returned products.
    """
    exhausted: list[WorkDispatchLineage] = []
    empty_rungs = 0
    refused_products = 0
    hints: tuple[ProviderCandidate, ...] = ()
    observation: ProviderObservation | None = None
    for query in work_query_ladder(
        lookup.title or "", lookup.authors, audiobook=lookup.audio is not None
    ):
        physical = observe_rung(query)
        observation = qualify_provider_observation(physical, lookup)
        if physical.status is ProviderStatus.NO_MATCH:
            empty_rungs += 1
        elif (
            lookup.audio is not None
            and physical.status is ProviderStatus.CANDIDATES
            and observation.status is ProviderStatus.NO_MATCH
        ):
            refused_products += len(physical.candidates)
            hints = hints or observation.recording_lookup_candidates
        else:
            return QualifiedLaneObservation(observation)
        exhausted.append(WorkDispatchLineage.from_query(query))
    if observation is None:
        raise MetadataReportError("work query ladder is empty")
    if lookup.audio is None:
        return QualifiedLaneObservation(observation)
    return QualifiedLaneObservation(
        replace(observation, recording_lookup_candidates=hints),
        tuple(exhausted),
        work_ladder_diagnostics(empty_rungs, refused_products),
    )


def _shaped_with_runtime_comparison(
    candidate: ProviderCandidate,
    corroborated: bool,
    local_claims: tuple[LocalClaim, ...],
    *,
    recording_hints: bool = False,
) -> ProviderCandidate:
    """Preserve the media-health comparison before limiting edition applicability.

    Audio lookup hints retain raw duration statements for later vetoes, but
    media health still records this comparison at its original boundary.
    Corroborated candidates cannot be short within the accepted tight band.
    """
    shaped = _admitted_candidate_shape(
        candidate, corroborated, recording_hints=recording_hints
    )
    if corroborated or not runtime_short_of_stated(candidate, local_claims):
        return shaped
    return replace(shaped, runtime_short_of_local=True)


def _admitted_candidate_shape(
    candidate: ProviderCandidate, corroborated: bool, *, recording_hints: bool = False
) -> ProviderCandidate:
    """Shape one admitted candidate to the scope its evidence earned.

    Admission decides *whether* a candidate describes this item; this
    decides *how much* of what it says the report keeps. Without
    corroboration work statements survive, with informational audio ASIN and
    DURATION hints when requested. These hints grant no edition applicability.
    Corroboration retains the edition-bearing statements ADR-0061 names.

    It is shared by the two lanes that can reach an uncorroborated
    candidate -- the title-and-author search, and an ASIN lane the item's
    own surfaces did not name -- so both demote in exactly one way.
    """
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
        ClaimKind.PUBLICATION_DATE,
        ClaimKind.CONTRIBUTOR,
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
                claim.scope is ClaimScope.EDITION
                and claim.kind in {ClaimKind.TITLE, ClaimKind.CREATOR}
                and candidate.work_identity != "unresolved"
                and any(
                    e.kind is ProviderEvidenceKind.EDITION_WORK_RELATION
                    and e.value == candidate.work_identity
                    for e in candidate.evidence
                )
            )
            or (
                reject_creator_authority
                and claim.scope is ClaimScope.EDITION
                and claim.kind in neutral_rejection_sibling_claims
                and claim.state is FieldState.VALID
            )
            or (
                recording_hints
                and claim.kind
                in {
                    ClaimKind.ASIN,
                    ClaimKind.ISBN,
                    ClaimKind.DURATION,
                    ClaimKind.LANGUAGE,
                }
            )
            or _corroborated_edition_claim(claim, corroborated)
        )
    )
    filtered_references = {claim.reference for claim in filtered_claims}
    return ProviderCandidate(
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
                or value.kind
                in {
                    ProviderEvidenceKind.EDITION_WORK_RELATION,
                    ProviderEvidenceKind.EDITION_WORK_RELATION_CONTRADICTION,
                }
                or (
                    recording_hints
                    and value.kind is ProviderEvidenceKind.EDITION_STATEMENT
                )
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
                or (
                    corroborated
                    and value.scope is ClaimScope.EDITION
                    and value.kind
                    in {
                        ProviderEvidenceKind.ASIN_MARKETPLACE,
                        ProviderEvidenceKind.CHAPTER_STRUCTURE,
                    }
                )
            )
        ),
        tuple(
            binding
            for binding in candidate.identity_bindings
            if binding.claim_reference in filtered_references
        ),
    )


def _corroborated_edition_claim(claim: ProviderClaim, corroborated: bool) -> bool:
    """Whether a corroborated candidate may keep one edition-bearing claim.

    Retain bounded contributor outcomes and roles so incomplete credits cannot
    become an apparently complete narrator ensemble. Their retention grants no
    value authority. Other recording fields require a valid stated value; an
    unlabelled ISBN remains unknown rather than gaining recording applicability.
    """
    if not corroborated:
        return False
    if claim.scope is ClaimScope.WORK and claim.kind is ClaimKind.CONTRIBUTOR:
        return True
    if claim.state is not FieldState.VALID:
        return False
    if claim.scope is ClaimScope.EDITION and claim.kind is ClaimKind.ASIN:
        return canonical_asin(claim.value) is not None
    if claim.scope is ClaimScope.EDITION and claim.kind is ClaimKind.ISBN:
        return claim.applicability == "unknown"
    if claim.scope is ClaimScope.EDITION and claim.kind in {
        ClaimKind.ADULT_PRODUCT,
        ClaimKind.DURATION,
        ClaimKind.LANGUAGE,
        ClaimKind.PUBLISHER,
        ClaimKind.IMPRINT,
        ClaimKind.PUBLICATION_DATE,
    }:
        return True
    return claim.scope is ClaimScope.WORK and claim.kind is ClaimKind.SUBTITLE


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


def _target_asserted_asin_claims(
    target: TargetSnapshot,
) -> tuple[LocalClaim, ...]:
    """Record the ASIN the target asserts as one claim of its own basis.

    The claim exists so the assertion can be cited: a lane fed by it names this
    reference, the report states the surface it came from, and provider
    recovery can rebuild the same lane from the retained document. It is
    deliberately no more than that -- `audiobook_discovery_plan` refuses it as
    an identifier surface, and `_identifier_lane_candidates` still makes the
    runtime band judge everything it obtains (ADR-0061).

    Only a canonical value becomes a claim. The Audiobookshelf projection
    already delivers anything else as a non-`VALUE` field with no value, so
    there is nothing here to report as malformed that the target did not.
    """
    return tuple(
        LocalClaim.create(
            kind=ClaimKind.ASIN,
            observed=field.value,
            canonical=value,
            excerpt=field.value,
            applicability="not_applicable",
            basis="target_metadata",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            scope=ClaimScope.EDITION,
            occurrence=0,
        )
        for field in target.fields
        if field.destination is TargetDestination.ASIN
        and field.state is TargetFieldState.VALUE
        and type(field.value) is str
        and (value := canonical_asin(field.value)) is not None
    )


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
        audio_inspector: AudioInspector | None = None,
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
        self._audio_inspector = audio_inspector
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
            initial.media_kind not in {"ebook", "audiobook"}
            or type(file_index) is not int
            or not 0 <= file_index < len(initial.files)
        ):
            raise UnsupportedMediaError("unsupported media")
        selected = initial.files[file_index]
        audiobook = initial.media_kind == "audiobook"
        if (
            selected.format != "epub"
            if not audiobook
            # F44 admits the item the catalogue admits: one to
            # `MAX_AUDIO_TRACKS` audio files, every one of them in a container
            # format the isolated inspector understands, with an inspector
            # composed for them, selected as the whole ordered set.
            else (
                file_index != 0
                or not 1 <= len(initial.files) <= MAX_AUDIO_TRACKS
                or any(
                    value.format not in AUDIO_FILE_FORMATS for value in initial.files
                )
                or self._audio_inspector is None
            )
        ):
            raise UnsupportedMediaError("unsupported media")
        # An audiobook is prepared as one item, so the identity the catalogue
        # bound the work to is the identity of its ordered track set, which for
        # a one-track item is that track's own.
        item_identity = (
            audio_set_identity(tuple(value.identity for value in initial.files))
            if audiobook
            else selected.identity
        )
        if (
            expected_file_identity is not None
            and item_identity != expected_file_identity
        ):
            raise CurrentnessError("target file changed")

        repository: ReportRepository | None = None
        local: InspectionResult | None = None
        audio: AudioInspectionResult | None = None
        try:
            repository = self._repository_factory()
            current_document = repository.current_optional(
                self._connection.name, self._library.name, item, item_identity
            )
            expected_current = (
                None
                if current_document is None
                else MetadataReport.from_json(current_document).report_id
            )

            try:
                with self._timing.measure("extraction", "none"):
                    if audiobook:
                        # ``self._audio_inspector`` was proved non-``None`` by
                        # the media gate above.
                        audio = cast("AudioInspector", self._audio_inspector).inspect(
                            self._mount,
                            initial.files,
                            self._limits.audio_source_bytes,
                        )
                    else:
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
        extracted = (
            audio.claims
            if audio is not None
            else cast("InspectionResult", local).claims
        )
        combined_claims = (
            (*target_title_claim, *extracted)
            if audio is None
            # The asserted ASIN is retained only on the audiobook path: it is
            # an audiobook identifier, and admitting it beside an EPUB's claims
            # would change every ebook report for a field no ebook lane reads.
            else (
                *target_title_claim,
                *_target_asserted_asin_claims(initial),
                *extracted,
            )
        )
        if audio is not None:
            return self._publish_audio_report(
                repository,
                item,
                file_index,
                item_identity,
                selected,
                initial,
                audio,
                combined_claims,
                expected_current,
            )
        # ``local`` is the extraction result on every remaining path: the
        # audiobook branch returned above and the gate admitted no third kind.
        local = cast("InspectionResult", local)
        plan = metadata_discovery_plan(initial, combined_claims)
        cover_aggregate_deadline = time.monotonic() + 60.0
        try:
            current_sources = (
                local.cover_sources
                if self._cover_observer is None
                else self._cover_observer.observe_current(
                    initial, local.cover_sources, cover_aggregate_deadline
                )
            )
        except BaseException as error:
            # Acquisition owns no publication; preserve cancellation and the
            # original failure while releasing the already-open repository.
            _release_repository(repository, error)
            raise
        observations, descriptors = self._observe_providers(
            repository, plan, cover_aggregate_deadline
        )

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
                    current_sources,
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
        return self._publish(repository, report, expected_current)

    def _observe_providers(
        self,
        repository: ReportRepository,
        plan: MetadataDiscoveryPlan,
        deadline: float,
        bindings: tuple[ProviderBinding, ...] | None = None,
    ) -> tuple[tuple[ProviderObservation, ...], tuple[ProviderCoverDescriptor, ...]]:
        """Dispatch one discovery plan across every configured provider binding.

        Both media branches run this one loop, so an audiobook observation is
        bounded, classified, and diagnosed exactly as an ebook one is. What the
        branches differ in is the plan they hand it -- ISBN and work lanes, or
        ASIN and work lanes. Both hand the descriptors it returns to the same
        cover observer.

        `bindings` narrows the dispatch to a subset of the configured ones,
        which is how the audiobook path runs its ASIN feed over exactly the
        providers that had nothing to answer in phase one. The loop itself is
        unchanged, so a fed observation is bounded and diagnosed identically.
        """
        observations: list[ProviderObservation] = []
        descriptors: list[ProviderCoverDescriptor] = []
        # One name for the two failure types this loop raises: a credential the
        # factory declared missing, and a factory that broke for any other
        # reason. Both are released and re-raised through the same statements.
        failure: MetadataReportError
        for binding in self._providers if bindings is None else bindings:
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
                    if lane.title_basis is None
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
                    result = observe_provider(
                        binding.name,
                        binding.authority,
                        provider,
                        plan,
                        deadline=deadline,
                    )
            except Exception as error:
                _release_repository(repository, error)
                raise
            observations.append(result.observation)
            candidate_descriptors = result.cover_descriptors
            if type(candidate_descriptors) is not tuple or any(
                type(value) is not ProviderCoverDescriptor
                or value.provider != binding.name
                for value in candidate_descriptors
            ):
                failure = MetadataReportError("provider cover result is invalid")
                _release_repository(repository, failure)
                raise failure
            descriptors.extend(candidate_descriptors)
        return tuple(observations), tuple(descriptors)

    def _publish(
        self,
        repository: ReportRepository,
        report: MetadataReport,
        expected_current: str | None,
    ) -> str:
        """Revalidate, write, and read one assembled report back.

        Shared by both media branches so an audiobook report is published under
        exactly the currentness guard, receipt, and read-back an ebook one is.
        """
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
                    audio_inspector=self._audio_inspector,
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

    def _publish_audio_report(
        self,
        repository: ReportRepository,
        item: str,
        file_index: int,
        item_identity: str,
        selected: TargetFile,
        initial: TargetSnapshot,
        audio: AudioInspectionResult,
        combined_claims: tuple[LocalClaim, ...],
        expected_current: str | None,
    ) -> str:
        """Publish one audiobook report over its own discovery plan.

        An audiobook's identifier is an ASIN, so the plan is built by
        ``audiobook_discovery_plan`` rather than the ISBN one, and it runs
        through the same provider loop an ebook does. Its cover descriptors run
        through the same bounded cover observer as well: two of the four
        admitted authorities state a cover asset of their own, and each one's
        `cover_enabled` flag is consulted inside that observer.

        The aggregate cover window is the ebook path's own 60 seconds. There is
        no measured reason for an audiobook's cover acquisition to be bounded
        differently from an ebook's -- the bytes come from the same kind of CDN
        over the same transport -- so it is deliberately the same number.

        Dispatch is two phases rather than one. Phase one is the plan above,
        run over every binding. Phase two runs the ASIN feed -- the ASINs the
        first phase promoted and the one the target asserts -- over exactly the
        bindings whose phase-one observation answered no lane at all, which is
        what a per-ASIN provider records when a plan names no product for it.
        Their phase-one observation is *replaced*, never merged: one provider
        states one observation per report, and merging two would fabricate a
        provider that answered a plan it was never given.
        """
        reasons = tuple(
            sorted(
                {f"{claim.basis}_{claim.kind.value}" for claim in audio.claims}
                or {"no_local_audio_claims"}
            )
        )
        facts = audio.audio
        if facts is not None:
            # Composition holds no target, so the drops that need one -- the
            # asserted title, authors and recorded series -- land here, on the
            # same persisted field the plan and its replay read (#1255).
            facts = replace(
                facts,
                discovery_titles=surviving_discovery_titles(
                    facts.discovery_titles, initial.fields
                ),
            )
        plan = audiobook_discovery_plan(initial, combined_claims, audio=facts)
        cover_aggregate_deadline = time.monotonic() + 60.0
        try:
            current_sources = (
                ()
                if self._cover_observer is None
                else self._cover_observer.observe_current(
                    initial, (), cover_aggregate_deadline
                )
            )
        except BaseException as error:
            # Acquisition owns no publication; preserve cancellation and the
            # original failure while releasing the already-open repository.
            _release_repository(repository, error)
            raise
        observations, descriptors = self._observe_providers(
            repository, plan, cover_aggregate_deadline
        )
        feed = audiobook_asin_feed_plan(initial, plan, observations)
        unanswered = {value.provider for value in observations if not value.lanes}
        targets = tuple(
            binding
            for binding in self._providers
            if binding.enabled and binding.name in unanswered
        )
        if feed is not None and targets:
            fed, fed_descriptors = self._observe_providers(
                repository, feed, cover_aggregate_deadline, targets
            )
            replaced = {value.provider for value in fed}
            observations = (
                tuple(value for value in observations if value.provider not in replaced)
                + fed
            )
            descriptors = (
                tuple(value for value in descriptors if value.provider not in replaced)
                + fed_descriptors
            )
        try:
            # An audiobook has no locally extracted cover: its embedded art is
            # recorded as presence inside ``audio`` and never decoded, so the
            # observer starts from an empty local set.
            cover_sources = (
                ()
                if self._cover_observer is None
                else self._cover_observer.observe(
                    initial,
                    tuple(observations),
                    current_sources,
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
                file=FileFacts(
                    file_index,
                    item_identity,
                    audio.content_digest,
                    selected.format,
                ),
                local_claims=combined_claims,
                local_reasons=reasons,
                providers=tuple(sorted(observations, key=lambda value: value.provider)),
                field_results=derive_field_results(combined_claims),
                cover_sources=cover_sources,
                audio=_audio_with_runtime_observations(facts, tuple(observations)),
            )
        except Exception as error:
            _release_repository(repository, error)
            raise
        return self._publish(repository, report, expected_current)

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


def _audio_with_runtime_observations(
    audio: AudioFacts | None, observations: tuple[ProviderObservation, ...]
) -> AudioFacts | None:
    """Record, once per candidate, the truncation qualification measured.

    Retain the qualification result at assembly so it remains available when
    a published report has no comparable runtime. Informational duration may
    survive demotion; read-time health prefers that retained comparison over
    this fallback and emits only one statement per candidate.

    One observation per candidate identity, in identity order, so two providers
    naming the same edition state it once and a document published twice from
    one preparation is byte-identical.

    Unanimous, or silent. An identity is stated only when every candidate
    naming it is marked short; one authority answering that this edition is an
    hour longer than the recording while another answers that it is not is a
    disagreement between providers, and a disagreement is not evidence of a
    shortfall. This is decided here and not at the shaping site because
    qualification runs once per provider and never sees a second provider's
    answer, so assembly is the first place every candidate naming one edition
    is in hand. A runtime-corroborated candidate is not marked short, so a
    corroborated answer silences the identity here. Retained informational
    duration alone does not change that qualification result.
    """
    if audio is None:
        return None
    verdicts: dict[str, bool] = {}
    for observation in observations:
        for candidate in observation.candidates:
            verdicts[candidate.identity] = (
                verdicts.get(candidate.identity, True)
                and candidate.runtime_short_of_local
            )
    identities = sorted(identity for identity, short in verdicts.items() if short)
    if not identities:
        return audio
    return replace(
        audio,
        observations=(
            *audio.observations,
            *(
                AudioObservation(
                    AudioObservationKind.RUNTIME_SHORT_OF_PROVIDER, candidate=identity
                )
                for identity in identities
            ),
        ),
    )


def _audio_set_identity_or_stale(identities: tuple[str, ...]) -> str | None:
    """Name the current track set, or nothing a retained identity can equal."""
    try:
        return audio_set_identity(identities)
    except ReportValueError:
        return None


def require_report_current(
    target: TargetReader,
    inspector: EpubInspector,
    mount: MediaMountConfiguration | None,
    limits: ReportLimits,
    report: MetadataReport,
    *,
    require_singular: bool = False,
    audio_inspector: AudioInspector | None = None,
) -> bool:
    """Revalidate the exact target, media, and ABS cover bound to a report."""
    if (
        mount is None
        or type(report) is not MetadataReport
        or type(limits) is not ReportLimits
    ):
        raise CurrentnessError("target or media changed")
    audiobook = report.file.format in AUDIO_FILE_FORMATS
    if audiobook and audio_inspector is None:
        # A composition that cannot re-read the media it published a report
        # from cannot demonstrate currentness for it.
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
    if audiobook:
        # An audiobook is bound to its whole ordered track set, so currentness
        # compares the set: its composed identity, its per-track identities and
        # declared positions in order, and then every track's bytes. A track
        # added, removed, reordered, replaced, or renumbered moves one of
        # those. The declared position is compared because the report
        # published it -- as the manifest's `sequence`, and as the gap and
        # disorder observations composed from it -- so a set renumbered on the
        # service is a set this report no longer states.
        retained = report.audio
        identities = tuple(value.identity for value in final.files)
        if (
            final.media_kind != "audiobook"
            or final.title != report.target.current_title
            or final.fields != report.target.fields
            or retained is None
            or not retained.tracks
            or len(final.files) != len(retained.tracks)
            or identities != tuple(value.identity for value in retained.tracks)
            or tuple(value.sequence for value in final.files)
            != tuple(value.sequence for value in retained.tracks)
            or _audio_set_identity_or_stale(identities) != report.file.identity
            or final.files[0].format != report.file.format
        ):
            raise CurrentnessError("target or media changed")
        # An audiobook's evidence is read from the files *and* from every
        # recognized sidecar beside them, so the files' own digests alone prove
        # only half the source. The inspector re-derives the recognized
        # candidate set and re-digests it through the one shared derivation
        # inspection used, and any sidecar added, removed, or edited since
        # publication makes the report stale.
        current = cast("AudioInspector", audio_inspector).unchanged(
            mount,
            tuple(value.relative_path for value in final.files),
            report.file.content_digest,
            limits.audio_source_bytes,
            retained.sidecars,
            retained.tracks,
        )
    else:
        matches = tuple(
            value for value in final.files if value.identity == report.file.identity
        )
        if (
            final.media_kind != "ebook"
            or final.title != report.target.current_title
            or final.fields != report.target.fields
            or (require_singular and len(final.files) != 1)
            or len(matches) != 1
            or matches[0].format != report.file.format
        ):
            raise CurrentnessError("target or media changed")
        current = inspector.unchanged(
            mount,
            matches[0].relative_path,
            report.file.content_digest,
            limits.source_archive_bytes,
        )
    if not current:
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
    "AudioInspectionResult",
    "AudioInspector",
    "AudioItemInspectionError",
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
    "ProviderResult",
    "ProviderFailureSnapshot",
    "ProviderObservationFailure",
    "ProviderObservationCancelled",
    "ProviderObservationInterrupted",
    "ProviderLaneResult",
    "QualifiedLaneObservation",
    "ReportRepository",
    "ReportNotFoundError",
    "TargetFile",
    "TargetNotFoundError",
    "TargetReader",
    "TargetSnapshot",
    "UnsupportedMediaError",
    "admitted_audiobook_identity",
    "alternative_work_lane_permitted",
    "audiobook_asin_feed_plan",
    "audiobook_discovery_plan",
    "merge_provider_cover_descriptor",
    "merge_provider_cover_descriptors",
    "merge_provider_diagnostics",
    "metadata_discovery_plan",
    "metadata_lookup",
    "observe_work_ladder",
    "promoted_asins",
    "provider_isbn_applicability",
    "provider_observation",
    "qualify_provider_observation",
    "require_report_current",
]


def recording_identity_basis(report: MetadataReport) -> dict[str, str] | None:
    if type(report) is not MetadataReport:
        raise MetadataReportError("provider observation is invalid")
    return _domain_recording_identity_basis(report)


def admitted_audiobook_identity(report: MetadataReport) -> bool:
    if type(report) is not MetadataReport:
        raise MetadataReportError("provider observation is invalid")
    return _domain_admitted_audiobook_identity(report)
