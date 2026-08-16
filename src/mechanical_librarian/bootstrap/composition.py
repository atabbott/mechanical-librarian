"""Credential-bearing composition root for the sole metadata report command."""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, TypedDict, cast

import uvicorn

from mechanical_librarian.adapters.cover_artifacts import (
    CoverDecodeRejected,
    CoverDecodeTimeout,
    decode_cover,
)
from mechanical_librarian.adapters.evidence_extractors.epub_cover import (
    read_declared_cover,
)
from mechanical_librarian.adapters.evidence_extractors.process import (
    run_epub_inspector_child,
)
from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.adapters.field_effect_runtime import (
    DurableBackupReadiness,
    PersistedEffectConsent,
    PersistedEffectDependencies,
    PersistedForwardReevaluation,
)
from mechanical_librarian.adapters.library_services.audiobookshelf_catalogue import (
    AudiobookshelfCatalogueReader,
)
from mechanical_librarian.adapters.library_services.audiobookshelf_field_effect import (
    AudiobookshelfFieldEffectCapabilities,
    AudiobookshelfFieldEffectTarget,
)
from mechanical_librarian.adapters.library_services.audiobookshelf_item import (
    AudiobookshelfItemReader,
)
from mechanical_librarian.adapters.persistence.batch_manifest import (
    create as create_batch_manifest,
)
from mechanical_librarian.adapters.persistence.batch_manifest import (
    read as read_batch_manifest,
)
from mechanical_librarian.adapters.persistence.catalogue import open_catalogue
from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.adapters.persistence.metadata_reports import (
    open_repository,
    open_shadow_preview_repository,
)
from mechanical_librarian.adapters.persistence.provider_recovery import (
    ProviderRecoveryStoreError,
    SQLiteProviderRecoveryStore,
)
from mechanical_librarian.adapters.private_recovery import (
    PrivateCoverRecoveryStore,
    require_private_directory,
)
from mechanical_librarian.adapters.providers.google_books_metadata import (
    GoogleBooksMetadataProvider,
)
from mechanical_librarian.adapters.providers.hardcover_metadata import (
    HardcoverMetadataProvider,
    HardcoverPacer,
)
from mechanical_librarian.adapters.providers.open_library_metadata import (
    OpenLibraryMetadataProvider,
    OpenLibraryPacer,
)
from mechanical_librarian.adapters.providers.remote_cover import (
    CoverDeadline,
    RemoteCoverError,
    RemoteCoverTimeout,
    fetch_remote_cover,
)
from mechanical_librarian.adapters.reviewers.contained_reviewer import (
    ContainedReviewerProcessError,
    read_contained_reviewer_profile,
)
from mechanical_librarian.application.batch import (
    BatchError,
    BatchSelection,
    batch_fingerprint,
    parse_manifest,
    select,
)
from mechanical_librarian.application.batch import (
    catalogue_fingerprint as batch_catalogue_fingerprint,
)
from mechanical_librarian.application.batch import manifest as batch_manifest
from mechanical_librarian.application.batch_run import ForegroundBatchRun
from mechanical_librarian.application.catalogue import (
    CatalogueError,
    RefreshCurrentCatalogue,
    TargetConnectionError,
)
from mechanical_librarian.application.configuration import (
    ConfigurationError,
    MediaMountConfiguration,
    ProviderConnectionConfiguration,
)
from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.application.cover_consideration import (
    CoverConsiderationService,
)
from mechanical_librarian.application.cover_policy import CoverPolicy
from mechanical_librarian.application.decision_preview import preview_shadow_decision
from mechanical_librarian.application.field_effects import GuardedFieldApplication
from mechanical_librarian.application.metadata_report import (
    ArtifactAuthorityError,
    CoverReadError,
    CoverSourceChangedError,
    CoverTimeoutError,
    CredentialError,
    CurrentnessError,
    GenerateMetadataReport,
    InspectionResult,
    ItemInspectionError,
    MetadataReportError,
    ProviderBinding,
    ProviderCoverDescriptor,
    TargetCoverReader,
    TargetNotFoundError,
    TargetSnapshot,
    UnsupportedMediaError,
    require_report_current,
)
from mechanical_librarian.application.proposal_sets import (
    CreateProposalSet,
    GetProposalSet,
    ProposalCurrentnessError,
)
from mechanical_librarian.application.provider_recovery_runner import (
    ScopedProviderRecoveryRunner,
)
from mechanical_librarian.application.provider_retry_successor import (
    ConfiguredProviderRetry,
    GenerateProviderRetrySuccessor,
    ProviderRetryDisabled,
    ProviderRetrySharedFailure,
    ProviderRetrySuccessorError,
)
from mechanical_librarian.application.review_application import (
    ReviewApplicationError,
    compose_review_application,
    prepare_cover_consideration,
)
from mechanical_librarian.application.review_cases import (
    AdmitReviewCase,
    GetReviewCase,
    ReviewCaseError,
)
from mechanical_librarian.application.review_pipeline import (
    PipelineAction,
    ReviewPipelineError,
    ReviewPreparationPipeline,
    ReviewPreparationSupervisor,
)
from mechanical_librarian.bootstrap.contained_reviewer import run_cover_packet_reviewer
from mechanical_librarian.domain.cover_evidence import (
    CoverDesignation,
    CoverProvenance,
    CoverSource,
    CoverSourceKind,
    CoverSourceState,
    CoverTruncationReason,
    cover_source_id,
)
from mechanical_librarian.domain.epub_inspection import (
    DateEvent,
    EpubInspectionOutcome,
)
from mechanical_librarian.domain.epub_opening import EpubOpeningLimits
from mechanical_librarian.domain.identifiers import Fingerprint, SchemaVersion
from mechanical_librarian.domain.metadata_policy import (
    MetadataPolicyProfile,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    LocalClaim,
    MetadataReport,
    ProviderObservation,
    ProviderStatus,
    RearClaimProvenance,
    claim_scope,
)
from mechanical_librarian.domain.proposal_set import ProposalSet
from mechanical_librarian.domain.provider_recovery import ProviderRecoveryJob
from mechanical_librarian.domain.review_case import (
    ReviewAdmissionResult,
    ReviewAdmissionStaleError,
)
from mechanical_librarian.infrastructure.configuration import load_configuration_file
from mechanical_librarian.infrastructure.local_media import (
    LocalMediaError,
    open_local_media,
    sha256_descriptor,
)
from mechanical_librarian.infrastructure.security.credentials import (
    google_books_client,
    hardcover_client,
    hardcover_client_token,
    open_library_client,
    read_secret,
    target_client,
    target_client_token,
)
from mechanical_librarian.infrastructure.security.review_protocol import (
    MAX_REVIEW_CONNECTIONS,
    REVIEW_TLS_CIPHERS,
    BoundedReviewH11Protocol,
)
from mechanical_librarian.infrastructure.security.review_tls import (
    REJECT_ENCRYPTED_PRIVATE_KEY_PASSWORD,
    open_review_tls_identity,
)
from mechanical_librarian.interfaces.web import (
    create_review_app,
    load_owner_password_verifier,
)

if TYPE_CHECKING:
    from mechanical_librarian.adapters.persistence.catalogue import (
        SQLiteCatalogueRepository,
    )
    from mechanical_librarian.adapters.persistence.metadata_reports import (
        SQLiteMetadataReportRepository,
    )
    from mechanical_librarian.application.catalogue import CatalogueEntry
    from mechanical_librarian.application.configuration import (
        ApplicationConfiguration,
        ReviewWebConfiguration,
    )
    from mechanical_librarian.application.continuous_field_effects import (
        ContinuousFieldEffectRepository,
    )
    from mechanical_librarian.application.field_effects import CapabilityObserver
    from mechanical_librarian.application.metadata_policy import PolicyTransition
    from mechanical_librarian.application.metadata_report import (
        MetadataProvider,
        ReportRepository,
    )
    from mechanical_librarian.application.operations import StageTimer
    from mechanical_librarian.application.provider_recovery_runner import RecoveryRun
    from mechanical_librarian.application.review_application import SerializedGate
    from mechanical_librarian.application.review_pipeline import (
        ReviewPipelineRepository,
    )
    from mechanical_librarian.application.review_web import (
        CurrentReviewCaseSnapshot,
        ReviewAttentionSnapshot,
        ReviewLibrarySnapshot,
    )
    from mechanical_librarian.domain.human_decision import HumanDecision
    from mechanical_librarian.domain.metadata_policy import ResolvedMetadataPolicy
    from mechanical_librarian.domain.provider_recovery import ProviderRecovery


class _PublicationReceiptRepository(Protocol):
    """The publication-receipt capability the concrete report repository adds.

    ``ReportRepository`` deliberately stays narrow, but the repository the
    composition root's own factory produces always carries these bound writes,
    and both are only ever reached through that factory.
    """

    def complete_review_extraction_publication(
        self,
        action: PipelineAction,
        report_id: str,
        completed_at: int,
        recovery_delay_seconds: int = 0,
        provider_failure_code: str | None = None,
        claim_next: bool = False,
        provider_failure_root: str | None = None,
    ) -> None: ...

    def stage_provider_retry_successor(
        self, source_report_id: str, report: MetadataReport, staged_at: int
    ) -> str: ...


class _ManifestEntry(TypedDict):
    """One ``batch-manifest-v5`` entry, exactly as ``parse_manifest`` validates it."""

    connection: str
    library: str
    library_id: str
    item: str
    file_identity: str | None
    classification: str
    eligible: bool


class _Manifest(TypedDict):
    """The closed ``batch-manifest-v5`` document ``parse_manifest`` returns.

    ``parse_manifest`` rejects every document whose key set, value types, entry
    shape, and derived call maxima do not match this exactly, so reading it back
    at this shape restates a checked invariant.
    """

    schema: str
    id: str
    catalogue_generation: int
    catalogue_fingerprint: str
    batch_config_fingerprint: str
    entries: list[_ManifestEntry]
    providers: list[str]
    provider_adapters: list[str]
    call_maxima: dict[str, int]


class _RearProvenanceFields(TypedDict):
    """The exact keyword shape ``EpubRearProvenance.to_data`` emits."""

    citation: str
    marker: str
    resource_identity: str
    spine_ordinal: int
    tail_slot: int


def compose_audiobookshelf_field_effect_target(
    configuration: ApplicationConfiguration, artifact_store_path: Path,
    connection_name: str, library_name: str,
    *, credentials: dict[str, str] | None = None,
    artifact_identity: tuple[int, int] | None = None,
) -> AudiobookshelfFieldEffectTarget:
    """Compose one field-local ABS target from an already validated snapshot.

    The continuous runtime registers this target behind the guarded application,
    authority, consent, backup, currentness, and capability gates. Qualification
    callers may compose the same target independently without bypassing them.
    """
    connection, library, _mount, _providers = configuration.select(
        connection_name, library_name
    )
    artifacts = CoverArtifactStore(
        artifact_store_path, create=False, expected_identity=artifact_identity
    )
    return AudiobookshelfFieldEffectTarget(
        client_factory=lambda: (
            target_client(
                connection.endpoint, connection.secret_file,
                configuration.limits.timeout_seconds,
            )
            if credentials is None
            else target_client_token(
                connection.endpoint, credentials["target:" + connection.name],
                configuration.limits.timeout_seconds,
            )
        ),
        endpoint=connection.endpoint,
        library_id=library.library_id,
        artifacts=artifacts,
        maximum_response_bytes=configuration.limits.target_response_bytes,
    )


def compose_audiobookshelf_field_effect_capabilities(
    configuration: ApplicationConfiguration, connection_name: str, library_name: str,
    *, credentials: dict[str, str] | None = None,
) -> AudiobookshelfFieldEffectCapabilities:
    connection, library, _mount, _providers = configuration.select(connection_name, library_name)
    return AudiobookshelfFieldEffectCapabilities(
        client_factory=lambda: target_client(connection.endpoint, connection.secret_file, configuration.limits.timeout_seconds) if credentials is None else target_client_token(connection.endpoint, credentials["target:" + connection.name], configuration.limits.timeout_seconds),
        endpoint=connection.endpoint, library_id=library.library_id,
        maximum_response_bytes=configuration.limits.target_response_bytes,
    )


def compose_continuous_guarded_application(
    configuration: ApplicationConfiguration,
    repository: SQLiteMetadataReportRepository,
    database_path: Path, artifact_store_path: Path,
    preparation: ReviewPreparationSupervisor, private_root: Path | None = None,
    provider_recovery: SQLiteProviderRecoveryStore | None = None,
) -> ContinuousGuardedApplication:
    """Activate one configured Audiobookshelf instance across its libraries."""
    configured = tuple(
        (library.connection, library.name)
        for library in configuration.libraries
    )
    if not configured:
        raise ConfigurationError("guarded application has no configured library")
    artifact_identity = CoverArtifactStore(
        artifact_store_path, create=True
    ).identity
    targets = {
        key: compose_audiobookshelf_field_effect_target(
            configuration, artifact_store_path, *key,
            artifact_identity=artifact_identity,
        )
        for key in configured
    }
    capabilities: dict[tuple[str, str], CapabilityObserver] = {
        key: compose_audiobookshelf_field_effect_capabilities(
            configuration, *key
        )
        for key in configured
    }
    private_root = _private_root(database_path) if private_root is None else private_root
    recovery_root = private_root / "cover-recovery"
    recovery_root.mkdir(mode=0o700, exist_ok=True)
    recovery = PrivateCoverRecoveryStore(
        recovery_root,
        lambda plan: targets[(plan.connection, plan.library)].read_cover_bytes(plan),
    )
    authority = PersistedFieldEffectAuthority(repository)
    consent = PersistedEffectConsent(repository)
    dependencies = PersistedEffectDependencies(repository)
    backup = DurableBackupReadiness(repository)
    reevaluation = PersistedForwardReevaluation(
        lambda connection, library: preparation.request(connection, library)
    )
    applications = {
        key: GuardedFieldApplication(
            repository,
            targets[key],
            backup,
            capabilities[key],
            authority,
            dependencies,
            consent,
            recovery,
            reevaluation,
            lambda: int(time.time()),
        )
        for key in configured
    }
    if provider_recovery is None:
        raise ConfigurationError("provider recovery store is unavailable")
    return ContinuousGuardedApplication(
        # The concrete repository implements every member of this contract; the
        # protocol declares several of them with ``*values``/``**values``
        # placeholders that no exact implementation can structurally satisfy.
        cast("ContinuousFieldEffectRepository", repository),
        applications,
        capabilities,
        authority,
        configured,
        lambda: int(time.time()),
        preparation.request,
        # Callers reject an absent review configuration before composing.
        graceful_shutdown_seconds=cast(
            "ReviewWebConfiguration", configuration.review
        ).graceful_shutdown_seconds,
        scan_progress=preparation.progress,
        provider_recovery_snapshot=provider_recovery.activity_snapshot,
    )


def compose_field_effect_activity_projection(
    configuration: ApplicationConfiguration,
    repository: SQLiteMetadataReportRepository,
    provider_recovery: SQLiteProviderRecoveryStore | None = None,
) -> ContinuousGuardedApplication:
    """Compose the shared Activity projection without write-capable adapters."""
    configured = tuple(
        (library.connection, library.name)
        for library in configuration.libraries
    )
    if not configured:
        raise ConfigurationError("guarded application has no configured library")
    return ContinuousGuardedApplication(
        # See ``compose_continuous_guarded_application`` for why this contract
        # cannot be satisfied structurally.
        cast("ContinuousFieldEffectRepository", repository),
        {},
        {},
        PersistedFieldEffectAuthority(repository),
        configured,
        lambda: int(time.time()),
        lambda _connection, _library: None,
        # Callers reject an absent review configuration before composing.
        graceful_shutdown_seconds=cast(
            "ReviewWebConfiguration", configuration.review
        ).graceful_shutdown_seconds,
        activity_only=True,
        provider_recovery_snapshot=(
            provider_recovery.activity_snapshot
            if provider_recovery is not None
            else None
        ),
    )


class _IsolatedEpubInspector:
    def __init__(self, artifacts: CoverArtifactStore) -> None:
        self._artifacts = artifacts

    def inspect(self, mount: MediaMountConfiguration, relative_path: str, maximum_bytes: int) -> InspectionResult:
        opened = None
        try:
            opened = open_local_media(mount.container_path, relative_path)
            digest = sha256_descriptor(opened.descriptor, opened.length, maximum=maximum_bytes)
            source = Fingerprint("sha256", SchemaVersion("local-file-v1"), digest)
            report = run_epub_inspector_child(
                opened.descriptor,
                source,
                EpubOpeningLimits(),
                logical_identity=digest,
            )
            inspection = report
            if inspection.outcome not in {EpubInspectionOutcome.SUCCESS, EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE}:
                raise ItemInspectionError("EPUB inspection failed")
            claims: list[LocalClaim] = []
            for occurrence, fact in enumerate(inspection.facts, 1):
                claim = LocalClaim.create(
                    kind=ClaimKind(fact.field.value),
                    observed=fact.observed_value,
                    canonical=fact.canonical_value,
                    excerpt=fact.supporting_context,
                    applicability=fact.media,
                    basis=fact.basis,
                    checksum=fact.checksum,
                    applicability_basis=fact.classification_basis,
                    scope=(
                        ClaimScope.WORK
                        if fact.date_event
                        in {
                            DateEvent.WORK_FIRST_PUBLICATION,
                            DateEvent.CREATION,
                        }
                        else claim_scope(ClaimKind(fact.field.value))
                    ),
                    state=FieldState(fact.state),
                    role=fact.role,
                    # ``EpubRearProvenance.to_data`` emits exactly this dataclass'
                    # keyword shape, and ``__post_init__`` revalidates every value.
                    rear_provenance=None if fact.rear_provenance is None else RearClaimProvenance(**cast("_RearProvenanceFields", fact.rear_provenance.to_data())),
                    occurrence=occurrence,
                    date_event=fact.date_event,
                    date_precision=fact.date_precision,
                )
                claims.append(claim)
            declared = read_declared_cover(opened.descriptor, digest)
            if declared.state is None:
                try:
                    decoded = decode_cover(declared.data or b"")
                    try:
                        self._artifacts.stage(decoded.artifact, declared.data or b"", decoded.preview)
                        covers = (CoverSource(declared.source_id, CoverSourceKind.EPUB, CoverSourceState.RETRIEVED, decoded.artifact),)
                    except ArtifactAuthorityError:
                        raise
                    except MetadataReportError:
                        covers = (CoverSource(declared.source_id, CoverSourceKind.EPUB, CoverSourceState.UNAVAILABLE),)
                except CoverDecodeRejected as error:
                    covers = (CoverSource(declared.source_id, CoverSourceKind.EPUB, error.state),)
                except CoverDecodeTimeout:
                    covers = (
                        CoverSource(
                            declared.source_id,
                            CoverSourceKind.EPUB,
                            CoverSourceState.TIMEOUT,
                            timeout_scope="attempted",
                        ),
                    )
            else:
                assert declared.state is not None
                covers = (CoverSource(declared.source_id, CoverSourceKind.EPUB, declared.state),)
            return InspectionResult(
                digest,
                tuple(claims),
                covers,
                inspection.rear_resource_limit,
            )
        except ArtifactAuthorityError:
            raise
        except MetadataReportError:
            raise
        except (LocalMediaError, OSError, ValueError) as error:
            raise ItemInspectionError("EPUB inspection failed") from error
        finally:
            if opened is not None:
                os.close(opened.descriptor)

    def unchanged(self, mount: MediaMountConfiguration, relative_path: str, expected_digest: str, maximum_bytes: int) -> bool:
        opened = None
        try:
            opened = open_local_media(mount.container_path, relative_path)
            return sha256_descriptor(opened.descriptor, opened.length, maximum=maximum_bytes) == expected_digest
        except (LocalMediaError, OSError, ValueError):
            return False
        finally:
            if opened is not None:
                os.close(opened.descriptor)


class _CoverEvidenceObserver:
    """Acquire the one declared ABS cover inside the slice's bounded envelope.

    EPUB results are already collected by the isolated media inspector.  This
    deliberately has no selection policy: each source merely records its own
    acquisition or validation outcome.
    """

    def __init__(self, target: TargetCoverReader, artifacts: CoverArtifactStore, providers: tuple[ProviderConnectionConfiguration, ...] = ()) -> None:
        self._target = target
        self._artifacts = artifacts
        self._providers = {value.name: value for value in providers}

    def observe(self, target: TargetSnapshot, providers: tuple[ProviderObservation, ...], local_sources: tuple[CoverSource, ...], descriptors: tuple[ProviderCoverDescriptor, ...] = (), aggregate_deadline: float | None = None) -> tuple[CoverSource, ...]:
        now = time.monotonic()
        aggregate = now + 60.0 if aggregate_deadline is None else aggregate_deadline
        deadline = CoverDeadline(aggregate, min(now + 20.0, aggregate))
        if target.cover is None:
            source = CoverSource(cover_source_id(CoverSourceKind.ABS, "absent"), CoverSourceKind.ABS, CoverSourceState.ABSENT)
            return (source, *local_sources, *self._provider_covers(providers, descriptors, deadline=deadline))
        source_id = cover_source_id(CoverSourceKind.ABS, target.cover.identity)
        try:
            original = self._target.read_cover(target.cover, deadline=deadline.aggregate_deadline)
            if original is None:
                source = CoverSource(source_id, CoverSourceKind.ABS, CoverSourceState.ABSENT)
            else:
                decoded = decode_cover(
                    original,
                    deadline=deadline.aggregate_deadline,
                )
                self._artifacts.stage(decoded.artifact, original, decoded.preview)
                source = CoverSource(source_id, CoverSourceKind.ABS, CoverSourceState.RETRIEVED, decoded.artifact)
        except CoverTimeoutError:
            source = CoverSource(source_id, CoverSourceKind.ABS, CoverSourceState.TIMEOUT, timeout_scope="attempted")
        except CoverDecodeTimeout:
            source = CoverSource(source_id, CoverSourceKind.ABS, CoverSourceState.TIMEOUT, timeout_scope="attempted")
        except CoverSourceChangedError:
            source = CoverSource(source_id, CoverSourceKind.ABS, CoverSourceState.SOURCE_CHANGED)
        except CoverDecodeRejected as error:
            source = CoverSource(source_id, CoverSourceKind.ABS, error.state)
        except CoverReadError as error:
            source = CoverSource(source_id, CoverSourceKind.ABS, CoverSourceState(error.state))
        except ArtifactAuthorityError:
            raise
        except MetadataReportError:
            source = CoverSource(source_id, CoverSourceKind.ABS, CoverSourceState.UNAVAILABLE)
        return (source, *local_sources, *self._provider_covers(providers, descriptors, deadline=deadline))

    def _provider_covers(self, observations: tuple[ProviderObservation, ...], descriptors: tuple[ProviderCoverDescriptor, ...], *, deadline: CoverDeadline) -> tuple[CoverSource, ...]:
        admitted, truncations = self._admit_provider_descriptors(
            observations, descriptors
        )
        results: list[CoverSource] = []
        for provider in sorted(admitted):
            values = admitted[provider]
            if time.monotonic() >= deadline.aggregate_deadline:
                results.extend(
                    CoverSource(
                        value.source_id,
                        value.kind,
                        CoverSourceState.TIMEOUT,
                        timeout_scope="not_started_due_to_aggregate_deadline",
                        provenance=value.provenance,
                    )
                    for value in values
                )
                continue
            for value in values:
                results.append(
                    self._remote(value, deadline)
                )
        return (*results, *truncations)

    def _admit_provider_descriptors(
        self,
        observations: tuple[ProviderObservation, ...],
        descriptors: tuple[ProviderCoverDescriptor, ...],
    ) -> tuple[
        dict[str, tuple[ProviderCoverDescriptor, ...]],
        tuple[CoverSource, ...],
    ]:
        works = {
            observation.provider: {
                candidate.work_identity for candidate in observation.candidates
            }
            for observation in observations
            if observation.status is ProviderStatus.CANDIDATES
        }
        values: dict[str, list[ProviderCoverDescriptor]] = {}
        seen_ids: set[str] = set()
        for descriptor in descriptors:
            configuration = self._providers.get(descriptor.provider)
            if (
                configuration is None
                or not configuration.cover_enabled
                or descriptor.provenance is None
                or descriptor.provenance.provider != descriptor.provider
                or descriptor.provenance.work_identity
                not in works.get(descriptor.provider, set())
            ):
                continue
            if descriptor.source_id in seen_ids:
                raise MetadataReportError("provider cover result is invalid")
            seen_ids.add(descriptor.source_id)
            values.setdefault(descriptor.provider, []).append(descriptor)

        priority = {
            CoverDesignation.EXACT_EDITION: 0,
            CoverDesignation.DEFAULT: 1,
            CoverDesignation.WORK: 2,
            CoverDesignation.RELATED_EDITION: 3,
        }
        admitted: dict[str, tuple[ProviderCoverDescriptor, ...]] = {}
        truncations: list[CoverSource] = []
        remaining = 8
        for provider in sorted(values):
            ordered = tuple(
                sorted(
                    values[provider],
                    key=lambda value: (
                        priority.get(value.provenance.designation, 99)
                        if value.provenance is not None
                        else 99,
                        value.source_id,
                    ),
                )
            )
            provider_values = ordered[:4]
            kind = ordered[0].kind
            if len(ordered) > 4:
                truncations.append(
                    self._truncation_source(
                        provider,
                        kind,
                        CoverTruncationReason.PROVIDER_LIMIT,
                    )
                )
            selected = provider_values[:remaining]
            admitted[provider] = selected
            if len(provider_values) > len(selected):
                truncations.append(
                    self._truncation_source(
                        provider,
                        kind,
                        CoverTruncationReason.GALLERY_LIMIT,
                    )
                )
            remaining -= len(selected)
        return admitted, tuple(truncations)

    @staticmethod
    def _truncation_source(
        provider: str,
        kind: CoverSourceKind,
        reason: CoverTruncationReason,
    ) -> CoverSource:
        return CoverSource(
            cover_source_id(kind, f"truncation:{provider}:{reason.value}"),
            kind,
            CoverSourceState.TRUNCATED,
            provenance=CoverProvenance(
                provider,
                CoverDesignation.TRUNCATION,
                truncation_reason=reason,
            ),
        )

    def _remote(self, descriptor: ProviderCoverDescriptor, deadline: CoverDeadline) -> CoverSource:
        source_id = descriptor.source_id
        kind = descriptor.kind
        if descriptor.terminal_state is not None:
            return CoverSource(
                source_id,
                kind,
                descriptor.terminal_state,
                provenance=descriptor.provenance,
            )
        if descriptor.url is None:
            return CoverSource(
                source_id,
                kind,
                CoverSourceState.ABSENT,
                provenance=descriptor.provenance,
            )
        try:
            if descriptor.wait is not None and not descriptor.wait(deadline.aggregate_deadline):
                return CoverSource(
                    source_id,
                    kind,
                    CoverSourceState.TIMEOUT,
                    timeout_scope="not_started_due_to_aggregate_deadline",
                    provenance=descriptor.provenance,
                )
            attempt = deadline.next_attempt()
            remote = fetch_remote_cover(
                descriptor.url,
                deadline=attempt,
                allow_redirects=descriptor.allow_redirects,
                user_agent=descriptor.user_agent,
            )
            decoded = decode_cover(
                remote.content,
                deadline=deadline.aggregate_deadline,
            )
            self._artifacts.stage(decoded.artifact, remote.content, decoded.preview)
            return CoverSource(
                source_id,
                kind,
                CoverSourceState.RETRIEVED,
                decoded.artifact,
                provenance=descriptor.provenance,
            )
        except RemoteCoverTimeout:
            return CoverSource(
                source_id,
                kind,
                CoverSourceState.TIMEOUT,
                timeout_scope="attempted",
                provenance=descriptor.provenance,
            )
        except CoverDecodeTimeout:
            return CoverSource(
                source_id,
                kind,
                CoverSourceState.TIMEOUT,
                timeout_scope="attempted",
                provenance=descriptor.provenance,
            )
        except RemoteCoverError as error:
            message = str(error)
            state = CoverSourceState.UNSAFE_REMOTE if "unsafe" in message else CoverSourceState.UNSUPPORTED_FORMAT if "format" in message else CoverSourceState.OVERSIZED if "oversized" in message else CoverSourceState.UNAVAILABLE
            return CoverSource(
                source_id, kind, state, provenance=descriptor.provenance
            )
        except CoverDecodeRejected as error:
            return CoverSource(
                source_id,
                kind,
                error.state,
                provenance=descriptor.provenance,
            )
        except ArtifactAuthorityError:
            raise
        except MetadataReportError:
            return CoverSource(
                source_id,
                kind,
                CoverSourceState.UNAVAILABLE,
                provenance=descriptor.provenance,
            )

def run_metadata_report(config_path: Path, database_path: Path, artifact_store_path: Path, connection_name: str, library_name: str, item: str, file_index: int, expected_file_identity: str | None = None) -> str:
    # Validate the separately authorized private store before credentials or
    # network clients are constructed.  It must already be operator-created.
    configuration = load_configuration_file(config_path)
    return run_metadata_report_loaded(configuration, database_path, artifact_store_path, connection_name, library_name, item, file_index, expected_file_identity)


def _provider_statuses(report: MetadataReport) -> tuple[ProviderStatus, ...]:
    return tuple(
        status
        for provider in report.providers
        for status in (
            provider.status,
            *(lane.status for lane in provider.lanes),
        )
    )


def _record_provider_recovery_observations(
    store: SQLiteProviderRecoveryStore,
    report: MetadataReport,
    *,
    adapters: dict[str, str],
    scope: str,
    generation: int,
    now: int,
) -> None:
    """Persist only terminal F22 provider classifications; never retry them."""
    failures = {
        ProviderStatus.AUTHENTICATION: "authentication", ProviderStatus.AUTHORIZATION: "authorization",
        ProviderStatus.RATE_LIMITED: "rate_limited", ProviderStatus.UNAVAILABLE: "transient",
        ProviderStatus.INCOMPLETE: "transient",
    }
    for observation in report.providers:
        statuses = (observation.status, *(lane.status for lane in observation.lanes))
        terminal = tuple(failures[status] for status in statuses if status in failures)
        failure = (
            next((value for value in terminal if value in {"authentication", "authorization", "configuration"}), None)
            or next((value for value in terminal if value in {"rate_limited", "transient"}), None)
        )
        if failure is not None:
            adapter = adapters.get(observation.provider)
            if adapter is None:
                raise ReviewPipelineError("configuration")
            store.observe_failure(
                connection=observation.provider, adapter=adapter, scope=scope,
                generation=generation, failure=failure, now=now,
            )


def _provider_recovery_jobs(
    report: MetadataReport, *, package: tuple[str | None, str, str | None, int | None, str],
    configuration: ApplicationConfiguration, scope: str,
) -> tuple[ProviderRecoveryJob, ...]:
    """Create bounded retry work only from a published terminal package."""
    adapters = {provider.name: provider.adapter for provider in configuration.providers}
    retryable = {
        ProviderStatus.AUTHENTICATION, ProviderStatus.AUTHORIZATION,
        ProviderStatus.RATE_LIMITED, ProviderStatus.UNAVAILABLE,
        ProviderStatus.INCOMPLETE,
    }
    jobs = []
    for observation in report.providers:
        lane_ids = tuple(sorted(lane.identity for lane in observation.lanes if lane.status in retryable))
        if not lane_ids:
            continue
        adapter = adapters.get(observation.provider)
        if adapter is None:
            raise ReviewPipelineError("configuration")
        jobs.append(ProviderRecoveryJob(
            observation.provider, adapter, scope,
            configuration.provider_recovery_generation, report.report_id,
            report.target.connection, report.target.library, report.target.item,
            report.file.identity, report.report_id, *package,
            json.dumps(lane_ids, separators=(",", ":")),
        ))
    return tuple(sorted(jobs, key=lambda job: (job.connection, job.adapter, job.scope, job.generation, job.member_id)))


class _NonClosingRepository:
    """Let one composed service borrow a pipeline-owned repository connection."""

    def __init__(self, repository: SQLiteMetadataReportRepository) -> None:
        self._repository = repository

    # A delegating proxy resolves every other member dynamically, so ``Any`` is
    # the only description of what borrowing a member actually yields.
    def __getattr__(self, name: str) -> Any:
        return getattr(self._repository, name)

    def close(self) -> None:
        return None


# The six review ports below used to receive one object: the whole SQLite
# repository, whose public surface is now the compatibility face of thirteen
# aggregate modules. These facades give each port exactly the members its
# protocol names, so the type checker can no longer let an application service
# reach a capability its port never declared.
#
# Each facade forwards to the composing repository rather than to the aggregate
# behind it, and that is deliberate. Repository-level substitution is how a
# dozen tests force an interleaving, a stale read or a disk failure at an exact
# statement; dispatching straight to an aggregate would step around every one of
# them and leave those tests passing while testing nothing. Narrowing what the
# application can see is the point here -- not shortening the call chain.


class _BrowserStateOwnerPort:
    """Expose only the lifecycle and status members `BrowserStateOwner` names."""

    def __init__(self, repository: SQLiteMetadataReportRepository) -> None:
        self._repository = repository

    def close(self) -> None:
        self._repository.close()

    def operational_snapshot(self) -> dict[str, object]:
        return self._repository.operational_snapshot()

    def field_effect_capability_suspension(
        self, connection: str, destination: str
    ) -> str | None:
        return self._repository.field_effect_capability_suspension(
            connection, destination
        )


class _ReviewSessionPort:
    """Expose only the five members `ReviewSessionStore` names."""

    def __init__(self, repository: SQLiteMetadataReportRepository) -> None:
        self._repository = repository

    def begin_login(
        self, now: int, window_seconds: int, maximum_failures: int
    ) -> bool:
        return self._repository.begin_login(now, window_seconds, maximum_failures)

    def create_review_session(
        self,
        session_digest: str,
        csrf_digest: str,
        created_at: int,
        expires_at: int,
    ) -> None:
        self._repository.create_review_session(
            session_digest, csrf_digest, created_at, expires_at
        )

    def authenticate_review_session(
        self, session_digest: str, now: int
    ) -> tuple[str, str, int]:
        return self._repository.authenticate_review_session(session_digest, now)

    def rotate_review_session_csrf(
        self, session_digest: str, csrf_digest: str, now: int
    ) -> str:
        return self._repository.rotate_review_session_csrf(
            session_digest, csrf_digest, now
        )

    def revoke_review_session(
        self, session_digest: str, csrf_digest: str, now: int
    ) -> str:
        return self._repository.revoke_review_session(
            session_digest, csrf_digest, now
        )


class _CurrentReviewCasePort:
    """Expose only the four members `CurrentReviewCaseStore` names."""

    def __init__(self, repository: SQLiteMetadataReportRepository) -> None:
        self._repository = repository

    def review_case_exists(self, review_case_id: str) -> bool:
        return self._repository.review_case_exists(review_case_id)

    def get_review_case(self, review_case_id: str) -> str:
        return self._repository.get_review_case(review_case_id)

    def review_case_ready_at(self, review_case_id: str) -> int | None:
        return self._repository.review_case_ready_at(review_case_id)

    def read_current_review_case(
        self, review_case_id: str, history_limit: int | None
    ) -> "CurrentReviewCaseSnapshot":
        return self._repository.read_current_review_case(
            review_case_id, history_limit
        )


class _HumanDecisionPort(_CurrentReviewCasePort):
    """Add only the two writes `HumanDecisionStore` names to the case port."""

    def find_human_decision(self, idempotency_digest: str) -> str | None:
        return self._repository.find_human_decision(idempotency_digest)

    def record_human_decision(
        self,
        decision: "HumanDecision",
        *,
        cover_consideration_id: str | None = None,
    ) -> str:
        return self._repository.record_human_decision(
            decision, cover_consideration_id=cover_consideration_id
        )


class _ReviewLibraryPort:
    """Expose only the two projections `ReviewLibraryStore` names."""

    def __init__(self, repository: SQLiteMetadataReportRepository) -> None:
        self._repository = repository

    def read_review_library(
        self,
        search: str | None,
        limit: int,
        offset: int,
        attention: bool | None,
    ) -> "ReviewLibrarySnapshot":
        return self._repository.read_review_library(
            search, limit, offset, attention
        )

    def read_review_attention(
        self, search: str | None, limit: int, offset: int
    ) -> "ReviewAttentionSnapshot":
        return self._repository.read_review_attention(search, limit, offset)


class _PolicyImpactPort:
    """Expose only the policy and posture members `PolicyImpactRepository` names."""

    def __init__(self, repository: SQLiteMetadataReportRepository) -> None:
        self._repository = repository

    def current_unapplied_count(self, library: str) -> int:
        return self._repository.current_unapplied_count(library)

    def current_policy_impact_pairs(
        self, library: str, limit: int
    ) -> tuple[int, tuple[tuple[MetadataReport, ProposalSet], ...]]:
        return self._repository.current_policy_impact_pairs(library, limit)

    def create_metadata_policy_draft(self, document: str) -> str:
        return self._repository.create_metadata_policy_draft(document)

    def get_metadata_policy_draft(self, policy_id: str) -> str:
        return self._repository.get_metadata_policy_draft(policy_id)

    def active_metadata_policy(self) -> str:
        return self._repository.active_metadata_policy()

    def activate_metadata_policy(
        self,
        policy_id: str,
        expected_active_policy_id: str,
        resolutions: tuple[tuple[str, str, str], ...],
        activated_at: int,
    ) -> "PolicyTransition | None":
        return self._repository.activate_metadata_policy(
            policy_id, expected_active_policy_id, resolutions, activated_at
        )

    def metadata_policy_transition(self) -> "PolicyTransition | None":
        return self._repository.metadata_policy_transition()

    def retry_metadata_policy_transition(
        self, transition_id: str, retried_at: int
    ) -> "PolicyTransition":
        return self._repository.retry_metadata_policy_transition(
            transition_id, retried_at
        )

    def acknowledge_metadata_policy_transition(
        self, transition_id: str, acknowledged_at: int
    ) -> "PolicyTransition":
        return self._repository.acknowledge_metadata_policy_transition(
            transition_id, acknowledged_at
        )

    def current_resolved_policy_id(self, library: str) -> str:
        return self._repository.current_resolved_policy_id(library)

    def current_resolved_metadata_policy(
        self, library: str
    ) -> "ResolvedMetadataPolicy | None":
        return self._repository.current_resolved_metadata_policy(library)

    def metadata_policy_resolution_for_preview(
        self, library: str
    ) -> "ResolvedMetadataPolicy | None":
        return self._repository.metadata_policy_resolution_for_preview(library)

    def metadata_policy_preview_baseline(
        self, libraries: tuple[str, ...]
    ) -> tuple[str, tuple[tuple[str, "ResolvedMetadataPolicy | None"], ...]]:
        return self._repository.metadata_policy_preview_baseline(libraries)

    def shadow_posture(self) -> str:
        return self._repository.shadow_posture()

    def enter_shadow_preview(self, updated_at: int) -> str:
        return self._repository.enter_shadow_preview(updated_at)

    def activate_qualified_automatic(self, updated_at: int) -> str:
        return self._repository.activate_qualified_automatic(updated_at)

    def list_shadow_outcomes(self, limit: int) -> tuple[tuple[str, int], ...]:
        return self._repository.list_shadow_outcomes(limit)


def run_metadata_report_loaded(configuration: ApplicationConfiguration, database_path: Path, artifact_store_path: Path, connection_name: str, library_name: str, item: str, file_index: int, expected_file_identity: str | None = None, credentials: dict[str, str] | None = None, repository_identity: tuple[int, int] | None = None, artifact_identity: tuple[int, int] | None = None, open_library_pacer: OpenLibraryPacer | None = None, hardcover_pacer: HardcoverPacer | None = None, review_action: PipelineAction | None = None, recover_artifacts: bool = True, repository_authority: object | None = None, pipeline_repository: SQLiteMetadataReportRepository | None = None, review_claim_next: bool = False, timing: StageTimer | None = None) -> str:
    """Compose one report solely from an already validated configuration snapshot."""
    connection, library, mount, provider_configs = configuration.select(connection_name, library_name)
    if mount is None:
        raise UnsupportedMediaError("unsupported media")
    artifacts = CoverArtifactStore(artifact_store_path, create=False, expected_identity=artifact_identity)
    target = AudiobookshelfItemReader(
        client_factory=lambda: target_client(connection.endpoint, connection.secret_file, configuration.limits.timeout_seconds) if credentials is None else target_client_token(connection.endpoint, credentials["target:" + connection.name], configuration.limits.timeout_seconds),
        endpoint=connection.endpoint,
        library_id=library.library_id,
        service_path=mount.service_path,
        maximum_bytes=configuration.limits.target_response_bytes,
        timeout_seconds=configuration.limits.timeout_seconds,
    )
    if open_library_pacer is None and any(
        provider.enabled and provider.adapter == "open_library"
        for provider in provider_configs
    ):
        open_library_pacer = OpenLibraryPacer()
    if hardcover_pacer is None and any(
        provider.enabled and provider.adapter == "hardcover"
        for provider in provider_configs
    ):
        hardcover_pacer = HardcoverPacer()
    bindings: list[ProviderBinding] = []
    for provider in provider_configs:
        def factory(
            value: ProviderConnectionConfiguration = provider,
        ) -> MetadataProvider:
            instance: MetadataProvider
            if value.adapter == "google_books":
                api_key = (
                    None
                    if value.secret_file is None
                    else (
                        read_secret(value.secret_file)
                        if credentials is None
                        else credentials["provider:" + value.name]
                    )
                )
                instance = GoogleBooksMetadataProvider(
                    value.name,
                    google_books_client(configuration.limits.timeout_seconds),
                    configuration.limits.provider_response_bytes,
                    configuration.limits.timeout_seconds,
                    api_key=api_key,
                )
            elif value.adapter == "hardcover":
                if value.secret_file is None:
                    raise MetadataReportError("provider credential unavailable")
                instance = HardcoverMetadataProvider(
                    value.name,
                    hardcover_client(value.secret_file, configuration.limits.timeout_seconds) if credentials is None else hardcover_client_token(credentials["provider:" + value.name], configuration.limits.timeout_seconds),
                    configuration.limits.provider_response_bytes,
                    configuration.limits.timeout_seconds,
                    pacer=hardcover_pacer,
                )
            elif value.adapter == "open_library" and value.contact_email is not None:
                instance = OpenLibraryMetadataProvider(
                    value.name,
                    open_library_client(value.contact_email, configuration.limits.timeout_seconds),
                    configuration.limits.provider_response_bytes,
                    configuration.limits.timeout_seconds,
                    pacer=open_library_pacer,
                )
            else:
                raise MetadataReportError("provider configuration is invalid")
            return instance
        bindings.append(
            ProviderBinding(
                provider.name, provider.enabled, factory, provider.adapter
            )
        )
    def repository_factory() -> ReportRepository:
        repository: _NonClosingRepository | SQLiteMetadataReportRepository = (
            _NonClosingRepository(pipeline_repository)
            if pipeline_repository is not None
            else open_repository(
                database_path,
                expected_identity=repository_identity,
                authority=repository_authority,
            )
        )
        try:
            if recover_artifacts:
                artifacts.recover_unreferenced(repository.artifact_is_referenced)
            # The adapter implements every ``ReportRepository`` member; only its
            # ``publish`` hook parameter is declared as ``object`` where the
            # protocol names itself, which is a variance mismatch in the seam
            # rather than a missing capability.
            return cast("ReportRepository", repository)
        except Exception:
            repository.close()
            raise

    service = GenerateMetadataReport(
        target=target,
        inspector=_IsolatedEpubInspector(artifacts),
        repository_factory=repository_factory,
        providers=tuple(bindings),
        connection=connection,
        library=library,
        mount=mount,
        limits=configuration.limits,
        cover_observer=_CoverEvidenceObserver(target, artifacts, tuple(provider_configs)),
        publication_authority_guard=artifacts.require_current,
        publication_receipt=(
            None
            if review_action is None
            else lambda repository, report: cast(
                "_PublicationReceiptRepository", repository
            ).complete_review_extraction_publication(
                review_action,
                report.report_id,
                int(time.time()),
                0,
                None,
                review_claim_next,
                None,
            )
        ),
        require_singular_target=review_action is not None,
        timing=timing,
    )
    return service.run(item, file_index, expected_file_identity)

def _require_report_current_loaded(
    configuration: ApplicationConfiguration,
    artifacts: CoverArtifactStore,
    report: MetadataReport,
) -> bool:
    connection, library, mount, _ = configuration.select(
        report.target.connection, report.target.library
    )
    if mount is None:
        raise CurrentnessError("target or media changed")
    target = AudiobookshelfItemReader(
        client_factory=lambda: target_client(
            connection.endpoint,
            connection.secret_file,
            configuration.limits.timeout_seconds,
        ),
        endpoint=connection.endpoint,
        library_id=library.library_id,
        service_path=mount.service_path,
        maximum_bytes=configuration.limits.target_response_bytes,
        timeout_seconds=configuration.limits.timeout_seconds,
    )
    valid = require_report_current(
        target,
        _IsolatedEpubInspector(artifacts),
        mount,
        configuration.limits,
        report,
    )
    for source in report.cover_sources:
        if source.artifact is not None:
            artifacts.require_artifact(source.artifact)
    artifacts.require_current()
    return valid


def create_proposal_set_loaded(
    configuration: ApplicationConfiguration,
    database_path: Path,
    artifact_store_path: Path,
    report_id: str,
    repository_authority: object | None = None,
    pipeline_repository: SQLiteMetadataReportRepository | None = None,
) -> str:
    artifacts = CoverArtifactStore(artifact_store_path, create=False)
    repository: _NonClosingRepository | SQLiteMetadataReportRepository = (
        _NonClosingRepository(pipeline_repository)
        if pipeline_repository is not None
        else open_repository(
            database_path, create=False, authority=repository_authority
        )
    )

    def current(report: MetadataReport) -> bool:
        try:
            return _require_report_current_loaded(
                configuration, artifacts, report
            )
        except (CurrentnessError, TargetNotFoundError) as error:
            raise ProposalCurrentnessError("proposal report is stale") from error

    try:
        return CreateProposalSet(
            repository,
            current,
            lambda report: _current_resolved_policy(
                configuration, repository, report.target.library
            ),
        ).run(report_id)
    finally:
        repository.close()


def admit_review_case_loaded(
    configuration: ApplicationConfiguration,
    database_path: Path,
    artifact_store_path: Path,
    proposal_set_id: str,
    repository_authority: object | None = None,
    pipeline_repository: SQLiteMetadataReportRepository | None = None,
) -> str:
    artifacts = CoverArtifactStore(artifact_store_path, create=False)
    repository: _NonClosingRepository | SQLiteMetadataReportRepository = (
        _NonClosingRepository(pipeline_repository)
        if pipeline_repository is not None
        else open_repository(
            database_path, create=False, authority=repository_authority
        )
    )

    def current(proposal: ProposalSet) -> bool:
        try:
            report = MetadataReport.from_json(repository.get(proposal.report_id))
        except Exception as error:
            raise ReviewCaseError("persistence") from error
        try:
            if not _require_report_current_loaded(configuration, artifacts, report):
                return False
            return proposal.resolved_policy_id == _current_resolved_policy(
                configuration, repository, report.target.library
            ).resolved_id
        except (CurrentnessError, TargetNotFoundError):
            return False

    try:
        return AdmitReviewCase(repository, current).run(proposal_set_id)
    finally:
        repository.close()


def _current_resolved_policy(
    configuration: ApplicationConfiguration,
    repository: _NonClosingRepository | SQLiteMetadataReportRepository,
    library: str,
) -> ResolvedMetadataPolicy:
    persisted = repository.current_resolved_metadata_policy(library)
    profile = MetadataPolicyProfile.from_data(
        json.loads(repository.active_metadata_policy())
    )
    configured = next(
        (value for value in configuration.libraries if value.name == library),
        None,
    )
    if configured is None:
        raise ReviewCaseError("configuration")
    configured_resolution = resolve_policy(
        profile, library, configured.policy_override
    )
    if persisted is not None:
        if persisted != configured_resolution:
            raise ReviewCaseError("configuration")
        return persisted
    bound_id = repository.bind_initial_metadata_policy_resolution(
        configured_resolution.canonical_json
    )
    if bound_id != configured_resolution.resolved_id:
        raise ReviewCaseError("configuration")
    return configured_resolution


class _TargetConnectionHeld(CurrentnessError):
    """Private runner signal: retain provider recovery unchanged for a target hold."""


def _classify_provider_recovery_failure(error: Exception) -> tuple[str, int | None]:
    if isinstance(error, _TargetConnectionHeld):
        return "target_held", None
    if isinstance(error, CredentialError) or (
        isinstance(error, ProviderRetrySuccessorError)
        and isinstance(error.__cause__, CredentialError)
    ):
        return "authentication", None
    if isinstance(error, ProviderRetryDisabled):
        return "disabled", None
    if isinstance(error, ProviderRetrySharedFailure):
        failure = (
            "transient" if error.failure in {"unavailable", "incomplete"}
            else error.failure
        )
        return failure, error.retry_after
    if isinstance(error, ProviderRetrySuccessorError) and "configuration" in str(error):
        return "configuration", None
    if isinstance(error, ProviderRetrySuccessorError):
        return "terminal", None
    if isinstance(error, (CurrentnessError, TargetNotFoundError)):
        return "stale", None
    return "transient", None


def create_review_pipeline(
    configuration: ApplicationConfiguration,
    database_path: Path,
    catalogue_path: Path,
    artifact_store_path: Path,
    database_authority: object | None = None,
    timing: StageTimer | None = None,
    private_root: Path | None = None,
    provider_recovery: SQLiteProviderRecoveryStore | None = None,
) -> ReviewPreparationSupervisor:
    artifact_recovery_lock = threading.Lock()
    artifacts_recovered = False

    def recover_artifacts_once() -> None:
        nonlocal artifacts_recovered
        with artifact_recovery_lock:
            if artifacts_recovered:
                return
            artifacts = CoverArtifactStore(artifact_store_path, create=False)
            recovery_repository = open_repository(
                database_path,
                create=False,
                authority=database_authority,
            )
            try:
                artifacts.recover_unreferenced(
                    recovery_repository.artifact_is_referenced
                )
            finally:
                recovery_repository.close()
            artifacts_recovered = True
    mounts = {item.name: item for item in configuration.mounts}
    clients = {item.name: item for item in configuration.connections}
    reader = AudiobookshelfCatalogueReader(
        client_factory=lambda name: target_client(
            clients[name].endpoint,
            clients[name].secret_file,
            configuration.limits.timeout_seconds,
        ),
        mounts=mounts,
        maximum_bytes=configuration.limits.target_response_bytes,
        timeout_seconds=configuration.limits.timeout_seconds,
    )
    open_library_pacer = (
        OpenLibraryPacer()
        if any(
            provider.enabled and provider.adapter == "open_library"
            for provider in configuration.providers
        )
        else None
    )
    hardcover_pacer = (
        HardcoverPacer()
        if any(
            provider.enabled and provider.adapter == "hardcover"
            for provider in configuration.providers
        )
        else None
    )
    discovery_lock = threading.Lock()
    if provider_recovery is None:
        raise ConfigurationError("provider recovery store is unavailable")
    def repository_factory() -> SQLiteMetadataReportRepository:
        return open_repository(
            database_path, create=False, authority=database_authority
        )

    def retry_resolver(
        connection_name: str, library_name: str, name: str, adapter: str,
        generation: int,
    ) -> ConfiguredProviderRetry | None:
        if generation != configuration.provider_recovery_generation:
            return None
        try:
            _, _, _, configured = configuration.select(connection_name, library_name)
        except ConfigurationError:
            return None
        provider = next((value for value in configured if value.name == name and value.adapter == adapter), None)
        if provider is None:
            return None
        if not provider.enabled:
            raise ProviderRetryDisabled("provider retry is disabled")

        def factory(
            value: ProviderConnectionConfiguration = provider,
        ) -> MetadataProvider:
            if value.adapter == "google_books":
                api_key = None if value.secret_file is None else read_secret(value.secret_file)
                return GoogleBooksMetadataProvider(value.name, google_books_client(configuration.limits.timeout_seconds), configuration.limits.provider_response_bytes, configuration.limits.timeout_seconds, api_key=api_key)
            if value.adapter == "hardcover" and value.secret_file is not None:
                return HardcoverMetadataProvider(value.name, hardcover_client(value.secret_file, configuration.limits.timeout_seconds), configuration.limits.provider_response_bytes, configuration.limits.timeout_seconds, pacer=hardcover_pacer)
            if value.adapter == "open_library" and value.contact_email is not None:
                return OpenLibraryMetadataProvider(value.name, open_library_client(value.contact_email, configuration.limits.timeout_seconds), configuration.limits.provider_response_bytes, configuration.limits.timeout_seconds, pacer=open_library_pacer)
            raise ProviderRetrySuccessorError("provider retry configuration changed")

        return ConfiguredProviderRetry(ProviderBinding(name, True, factory, adapter), adapter, generation)

    pipeline_holder: dict[str, ReviewPreparationPipeline] = {}

    def retry_member(
        recovery_state: ProviderRecovery, job: ProviderRecoveryJob,
        heartbeat: Callable[[], None],
    ) -> None:
        staged_report_id: str | None = None
        repository = repository_factory()
        try:
            completed_report_id = repository.completed_provider_retry_successor(
                job.source_report_id
            )
            if completed_report_id is not None:
                return
            package = repository.current_review_package(job.target_connection, job.library, job.item)
            if package != (job.package_proposal_id, job.package_report_id, job.package_case_id, job.package_checkpoint, job.package_outcome):
                raise CurrentnessError("target or media changed")
            current_document = repository.current_optional(
                job.target_connection, job.library, job.item, job.file_identity,
            )
            if current_document is not None:
                current_report_id = MetadataReport.from_json(current_document).report_id
                if current_report_id != job.source_report_id:
                    try:
                        repository.provider_retry_work_id(current_report_id)
                    except MetadataReportError:
                        raise CurrentnessError("target or media changed") from None
                    staged_report_id = current_report_id
        finally:
            repository.close()
        pipeline = pipeline_holder.get("pipeline")
        if staged_report_id is not None:
            if pipeline is None or not pipeline.run_recovery_successor(
                staged_report_id, heartbeat
            ):
                raise ProviderRetrySuccessorError("provider retry package incomplete")
            repository = repository_factory()
            try:
                package = repository.current_review_package(
                    job.target_connection, job.library, job.item
                )
                if package is None or package[1] != staged_report_id:
                    raise ProviderRetrySuccessorError("provider retry package incomplete")
            finally:
                repository.close()
            return
        connection, library, mount, _ = configuration.select(job.target_connection, job.library)
        if mount is None:
            raise CurrentnessError("target or media changed")
        artifacts = CoverArtifactStore(artifact_store_path, create=False)
        target = AudiobookshelfItemReader(
            client_factory=lambda: target_client(connection.endpoint, connection.secret_file, configuration.limits.timeout_seconds),
            endpoint=connection.endpoint, library_id=library.library_id,
            service_path=mount.service_path,
            maximum_bytes=configuration.limits.target_response_bytes,
            timeout_seconds=configuration.limits.timeout_seconds,
        )
        try:
            serialized = GenerateProviderRetrySuccessor(
                target=target, inspector=_IsolatedEpubInspector(artifacts),
                repository_factory=cast(
                    "Callable[[], ReportRepository]", repository_factory
                ),
                provider_resolver=retry_resolver,
                mount=mount, limits=configuration.limits,
                # The staged work id is deliberately discarded here; the seam
                # types the hook as returning nothing.
                after_publication=cast(
                    "Callable[[ReportRepository, MetadataReport], None]",
                    lambda bound, report: cast(
                        "_PublicationReceiptRepository", bound
                    ).stage_provider_retry_successor(
                        job.source_report_id, report, int(time.time())
                    ),
                ),
                heartbeat=heartbeat,
            ).run(
                job.source_report_id, tuple(json.loads(job.lane_ids)),
                provider_name=job.connection, adapter=job.adapter,
                generation=job.generation,
                generation_woken=recovery_state.attempt == 0,
            )
        except TargetConnectionError as error:
            if pipeline is not None:
                pipeline.hold_connection(job.target_connection, error.code)
            # This is a target hold, not provider evidence: retain the
            # completed provider observation and stop successor backoff.
            raise _TargetConnectionHeld("target connection is held") from None
        except CurrentnessError as error:
            # Currentness deliberately hides target details at the report
            # boundary, but retains the closed target condition as its cause.
            # Recover that safe taxonomy here so this successor cannot be
            # mistaken for a provider transient/backoff condition.
            cause = error.__cause__
            while cause is not None and not isinstance(cause, TargetConnectionError):
                cause = cause.__cause__
            if isinstance(cause, TargetConnectionError):
                if pipeline is not None:
                    pipeline.hold_connection(job.target_connection, cause.code)
                raise _TargetConnectionHeld("target connection is held") from None
            raise
        successor = MetadataReport.from_json(serialized)
        if pipeline is None or not pipeline.run_recovery_successor(
            successor.report_id, heartbeat
        ):
            raise ProviderRetrySuccessorError("provider retry package incomplete")
        repository = repository_factory()
        try:
            package = repository.current_review_package(
                job.target_connection, job.library, job.item
            )
            if package is None or package[1] != successor.report_id:
                raise ProviderRetrySuccessorError("provider retry package incomplete")
        finally:
            repository.close()

    recovery_cancel = threading.Event()
    recovery_runner = ScopedProviderRecoveryRunner(
        store=provider_recovery, generation=configuration.provider_recovery_generation,
        retry=retry_member, classify_failure=_classify_provider_recovery_failure,
        clock=lambda: int(time.time()), cancelled=recovery_cancel.is_set,
        blocked_targets=lambda: frozenset(
            condition.connection for condition in pipeline_holder["pipeline"].connection_conditions()
            if condition.state in {"paused", "checking"}
        ),
    )

    def drain_provider_recovery_outbox(
        repository: SQLiteMetadataReportRepository | None = None,
    ) -> None:
        """Deliver exact package cutovers idempotently into recovery state."""
        owns_repository = repository is None
        if repository is None:
            repository = repository_factory()
        try:
            while rows := repository.pending_provider_recovery_outbox(limit=100):
                for row in rows:
                    # The outbox query projects exactly this column order and
                    # nullability; see ``pending_provider_recovery_outbox``.
                    (
                        outbox_id, target_connection, library_name, item_id,
                        proposal_id, report_id, case_id, checkpoint, outcome,
                        created_at,
                    ) = cast(
                        "tuple[str, str, str, str, str | None, str, str | None, int | None, str, int]",
                        row,
                    )
                    package = (proposal_id, report_id, case_id, checkpoint, outcome)
                    current = repository.current_review_package(
                        target_connection, library_name, item_id,
                    )
                    if current == package:
                        report = MetadataReport.from_json(repository.get(report_id))
                        if (
                            report.target.connection != target_connection
                            or report.target.library != library_name
                            or report.target.item != item_id
                        ):
                            raise ReviewPipelineError("projection")
                        jobs = _provider_recovery_jobs(
                            report, package=package, configuration=configuration,
                            scope=report.target.library,
                        )
                        if jobs:
                            recovery_runner.enqueue(jobs)
                    repository.complete_provider_recovery_outbox(
                        outbox_id, delivered_at=max(int(time.time()), created_at),
                    )
        finally:
            if owns_repository:
                repository.close()

    def recover_provider(now: int) -> RecoveryRun:
        try:
            drain_provider_recovery_outbox()
            return recovery_runner.run_one(now=now)
        except ProviderRecoveryStoreError as error:
            raise ReviewPipelineError("recovery") from error

    def discover(
        connection: str, library: str
    ) -> tuple[int, str, tuple[CatalogueEntry, ...]]:
        with discovery_lock:
            repository = open_catalogue(catalogue_path, create=True)
            try:
                service = RefreshCurrentCatalogue(reader, repository)
                service.refresh_library(configuration, connection, library)
                snapshot = repository.snapshot(
                    batch_catalogue_fingerprint(configuration)
                )
                return snapshot.generation, snapshot.fingerprint, snapshot.entries
            except TargetConnectionError:
                raise
            except CatalogueError as error:
                raise ReviewPipelineError("transient") from error
            finally:
                repository.close()

    # The pipeline hands every stage callback back exactly the repository
    # ``repository_factory`` above created, so each callback narrows the seam's
    # ``ReviewPipelineRepository`` to that concrete adapter where it needs a
    # capability the seam does not name.
    def extract(
        pipeline_repository: ReviewPipelineRepository, action: PipelineAction,
        claim_next: bool,
    ) -> tuple[str, bool]:
        catalogue = open_catalogue(catalogue_path, create=False)
        try:
            entry = catalogue.latest_entry(
                action.catalogue_fingerprint or "",
                (
                    action.connection,
                    action.library,
                    action.library_id,
                    action.item_id or "",
                ),
            )
        except CatalogueError as error:
            raise ReviewPipelineError("stale") from error
        finally:
            catalogue.close()
        if (
            entry is None
            or entry.file_identity != action.file_identity
            or entry.source_fingerprint != action.source_fingerprint
            or not entry.eligible
        ):
            raise ReviewPipelineError("stale")
        recover_artifacts_once()
        try:
            serialized = run_metadata_report_loaded(
                configuration,
                database_path,
                artifact_store_path,
                action.connection,
                action.library,
                action.item_id or "",
                0,
                expected_file_identity=action.file_identity,
                open_library_pacer=open_library_pacer,
                hardcover_pacer=hardcover_pacer,
                review_action=action,
                recover_artifacts=False,
                repository_authority=database_authority,
                pipeline_repository=cast("SQLiteMetadataReportRepository", pipeline_repository),
                review_claim_next=claim_next,
                timing=timing,
            )
        except CredentialError as error:
            if error.provider is None:
                raise ReviewPipelineError("authentication") from error
            provider = next(
                (value for value in configuration.providers if value.name == error.provider),
                None,
            )
            if provider is None:
                raise ReviewPipelineError("configuration") from error
            provider_recovery.observe_failure(
                connection=provider.name, adapter=provider.adapter,
                scope=action.library,
                generation=configuration.provider_recovery_generation,
                failure="authentication", now=int(time.time()),
            )
            raise ReviewPipelineError(
                "authentication", root=error.provider
            ) from error
        except (CurrentnessError, TargetNotFoundError) as error:
            raise ReviewPipelineError("stale") from error
        value = MetadataReport.from_json(serialized)
        return value.report_id, True

    def providers(
        pipeline_repository: ReviewPipelineRepository, report_id: str
    ) -> tuple[ProviderStatus, ...]:
        value = MetadataReport.from_json(cast("SQLiteMetadataReportRepository", pipeline_repository).get(report_id))
        _record_provider_recovery_observations(
            provider_recovery, value,
            adapters={provider.name: provider.adapter for provider in configuration.providers},
            scope=value.target.library,
            generation=configuration.provider_recovery_generation, now=int(time.time()),
        )
        return _provider_statuses(value)

    def propose(
        pipeline_repository: ReviewPipelineRepository, report_id: str
    ) -> str:
        try:
            serialized = create_proposal_set_loaded(
                configuration,
                database_path,
                artifact_store_path,
                report_id,
                database_authority,
                cast("SQLiteMetadataReportRepository", pipeline_repository),
            )
        except ProposalCurrentnessError as error:
            raise ReviewPipelineError("stale") from error
        return ProposalSet.from_json(serialized).proposal_set_id

    def admit(
        pipeline_repository: ReviewPipelineRepository, proposal_set_id: str
    ) -> tuple[str, str | None]:
        serialized = admit_review_case_loaded(
            configuration,
            database_path,
            artifact_store_path,
            proposal_set_id,
            database_authority,
            cast("SQLiteMetadataReportRepository", pipeline_repository),
        )
        result = ReviewAdmissionResult.from_json(serialized)
        if result.status == "stale":
            raise ReviewPipelineError("stale")
        return result.status, result.review_case_id

    def project(
        pipeline_repository: ReviewPipelineRepository,
        action: PipelineAction | str,
    ) -> str:
        try:
            if type(action) is str:
                outcome = pipeline_repository.persist_shadow_outcome(
                    action, int(time.time())
                )
                return "stale" if outcome == "stale" else "complete"
            # The branch above already returned for an exact ``str``; mypy keeps
            # ``str`` in the union here only because a ``str`` subclass would
            # fail that exact test, which this seam never supplies.
            staged = cast("PipelineAction", action)
            if staged.review_case_id is None:
                raise ReviewPipelineError("state")
            outcome = pipeline_repository.persist_shadow_outcome(
                staged.review_case_id, int(time.time()), staged_action=staged
            )
            if outcome == "stale":
                return "stale"
            return "complete"
        except Exception as error:
            raise ReviewPipelineError("projection") from error

    def prepare_cover(
        pipeline_repository: ReviewPipelineRepository, action: PipelineAction
    ) -> None:
        if action.review_case_id is None:
            return
        try:
            def snapshot() -> tuple[str, str, bool] | None:
                return cast(
                    "SQLiteMetadataReportRepository", pipeline_repository
                ).read_staged_cover_preparation(action)

            if snapshot() is None:
                return
            considerations = _compose_cover_considerations(
                cast("SQLiteMetadataReportRepository", pipeline_repository),
                artifact_store_path,
                # Callers reject an absent review configuration before composing.
                cast("ReviewWebConfiguration", configuration.review),
            )
            prepare_cover_consideration(
                None,
                considerations,
                action.review_case_id,
                serialized=lambda call: call(),
                snapshot=snapshot,
                # The adapter's gate is not written generically, so it cannot
                # structurally satisfy the return-type-preserving protocol.
                finalize=cast(
                    "SerializedGate",
                    cast(
                        "SQLiteMetadataReportRepository", pipeline_repository
                    ).atomic_cover_preparation,
                ),
            )
        except ReviewAdmissionStaleError as error:
            raise ReviewPipelineError("stale") from error
        except ReviewApplicationError as error:
            raise ReviewPipelineError(error.code) from error
        except Exception as error:
            raise ReviewPipelineError("persistence") from error

    def after_advance(
        pipeline_repository: ReviewPipelineRepository, action: PipelineAction,
        output_id: str | None,
    ) -> None:
        """Drain the durable cutover outbox after every final advancement."""
        if not (
            action.stage == "projection"
            or (action.stage == "admission" and output_id is None)
        ) or action.report_id is None:
            return
        drain_provider_recovery_outbox(
            cast("SQLiteMetadataReportRepository", pipeline_repository)
        )

    def check_connection(connection: str) -> None:
        if connection not in clients:
            raise ReviewPipelineError("library")
        reader.probe_connection(
            connection,
            tuple(value for value in configuration.libraries if value.connection == connection),
        )

    pipeline = ReviewPreparationPipeline(
        repository_factory=repository_factory,
        libraries=tuple(
            (
                library.connection,
                library.name,
                library.library_id,
                batch_fingerprint(
                    configuration,
                    (
                        BatchSelection(
                            library.connection,
                            library.name,
                            library.library_id,
                            "producer",
                            None,
                            "producer",
                            False,
                        ),
                    ),
                ),
            )
            for library in configuration.libraries
        ),
        discover=discover,
        extract=extract,
        providers=providers,
        propose=propose,
        admit=admit,
        project=project,
        before_projection=prepare_cover,
        after_advance=after_advance,
        check_connection=check_connection,
        timing=timing,
    )
    pipeline_holder["pipeline"] = pipeline
    provider_recovery.require_generation_compatible(
        generation=configuration.provider_recovery_generation
    )
    drain_provider_recovery_outbox()
    provider_recovery.reconcile_generation(
        generation=configuration.provider_recovery_generation, now=int(time.time())
    )
    # This composition root reads the review configuration unconditionally, so
    # an absent one is already a configuration failure before this point.
    review_configuration = cast("ReviewWebConfiguration", configuration.review)
    return ReviewPreparationSupervisor(
        pipeline,
        polling_interval_seconds=review_configuration.library_polling_interval_seconds,
        graceful_shutdown_seconds=review_configuration.graceful_shutdown_seconds,
        # The supervisor discards the run summary this returns.
        recovery_wake=cast("Callable[[int], None]", recover_provider),
        recovery_drain=recovery_cancel.set,
        recovery_resume=recovery_cancel.clear,
        on_stop=provider_recovery.close,
    )


def _private_root(database_path: Path) -> Path:
    root = database_path.parent / "private"
    require_private_directory(root, "provider recovery unavailable")
    return root


def reopen_metadata_report(database_path: Path, report_id: str) -> str:
    """Read one exact persisted report without loading operational configuration."""
    repository = open_repository(database_path, create=False)
    try:
        return repository.get(report_id)
    finally:
        repository.close()


def reopen_current_metadata_report(database_path: Path, connection: str, library: str, item: str, file_identity: str) -> str:
    repository = open_repository(database_path, create=False)
    try:
        return repository.current(connection, library, item, file_identity)
    finally:
        repository.close()


def reopen_cover_preview(database_path: Path, artifact_store_path: Path, report_id: str, artifact_id: str, preview_variant: str) -> bytes:
    """Read the one report-bound preview; originals and discovery stay unavailable."""
    repository = open_repository(database_path, create=False)
    try:
        digest = repository.preview_digest(report_id, artifact_id, preview_variant)
        return CoverArtifactStore(artifact_store_path, create=False).read_preview_digest(digest)
    finally:
        repository.close()


def create_proposal_set(
    config_path: Path,
    database_path: Path,
    artifact_store_path: Path,
    report_id: str,
) -> str:
    """Create one ProposalSet without provider calls or write authority."""
    return create_proposal_set_loaded(
        load_configuration_file(config_path),
        database_path,
        artifact_store_path,
        report_id,
    )


def reopen_proposal_set(
    database_path: Path, proposal_set_id: str
) -> str:
    repository = open_repository(database_path, create=False)
    try:
        return GetProposalSet(repository).run(proposal_set_id)
    finally:
        repository.close()


def admit_review_case(
    config_path: Path,
    database_path: Path,
    artifact_store_path: Path,
    proposal_set_id: str,
) -> str:
    """Admit one exact current ProposalSet; no decision or target mutation occurs."""
    return admit_review_case_loaded(
        load_configuration_file(config_path),
        database_path,
        artifact_store_path,
        proposal_set_id,
    )


def reopen_review_case(database_path: Path, review_case_id: str) -> str:
    repository = open_repository(database_path, create=False)
    try:
        return GetReviewCase(repository).run(review_case_id)
    finally:
        repository.close()


def preview_shadow_decision_case(
    database_path: Path, review_case_id: str
) -> str:
    repository = open_shadow_preview_repository(database_path)
    try:
        return preview_shadow_decision(repository, review_case_id).to_json()
    finally:
        repository.close()


def run_catalogue(config_path: Path, database_path: Path, action: str, connection: str | None, library: str | None, library_id: str | None, selector: str | None, limit: int = 100) -> str:
    """Refresh or inspect the one current catalogue through explicit composition."""
    configuration = load_configuration_file(config_path)
    mounts = {item.name: item for item in configuration.mounts}
    clients = {item.name: item for item in configuration.connections}
    reader = AudiobookshelfCatalogueReader(
        client_factory=lambda name: target_client(clients[name].endpoint, clients[name].secret_file, configuration.limits.timeout_seconds),
        mounts=mounts,
        maximum_bytes=configuration.limits.target_response_bytes,
        timeout_seconds=configuration.limits.timeout_seconds,
    )
    repository = open_catalogue(database_path, create=action == "refresh")
    try:
        service = RefreshCurrentCatalogue(reader, repository)
        if action == "refresh":
            return json.dumps({"generation": service.refresh(configuration)}, separators=(",", ":"))
        exact = None if action == "list" else (connection or "", library or "", library_id or "", selector or "")
        entries = service.inspect(configuration, exact=exact, facet=selector if action == "list" else None, limit=limit)
        values = [{"connection": item.connection, "library": item.library, "library_id": item.library_id, "item": item.item_id, "title": item.title, "authors": list(item.authors), "ebook": item.has_ebook, "audio": item.has_audio, "classification": item.classification, "eligible": item.eligible} for item in entries]
        if action == "get" and values:
            values[0].update({"updated_at": entries[0].updated_at, "file_identity": entries[0].file_identity, "format": entries[0].format, "service_path": entries[0].service_path, "mount": entries[0].mount})
        return json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    finally:
        repository.close()


def preview_batch(config_path: Path, catalogue_path: Path, manifest_path: Path, requested: tuple[tuple[str, str, str, str], ...], all_eligible: bool) -> str:
    """Freeze one selected current-catalogue view before any report authority."""
    configuration = load_configuration_file(config_path)
    fingerprint = batch_catalogue_fingerprint(configuration)
    repository = open_catalogue(catalogue_path, create=False)
    try:
        snapshot = repository.snapshot(fingerprint)
    finally:
        repository.close()
    selections = select(snapshot, requested, all_eligible)
    _require_open_library_batch_envelope(
        configuration, selections, all_eligible=all_eligible
    )
    providers = _batch_provider_names(configuration, selections)
    provider_adapters = _batch_provider_adapters(
        configuration, selections, providers
    )
    raw = batch_manifest(
        snapshot,
        batch_fingerprint(configuration, selections),
        selections,
        providers,
        provider_adapters,
    )
    create_batch_manifest(manifest_path, raw)
    value = cast("_Manifest", parse_manifest(raw))
    return json.dumps({"id": value["id"], "entries": len(selections), **value["call_maxima"]}, separators=(",", ":"))


def inspect_batch(manifest_path: Path) -> str:
    value = cast("_Manifest", parse_manifest(read_batch_manifest(manifest_path)))
    return json.dumps({"id": value["id"], "entries": len(value["entries"]), **value["call_maxima"]}, separators=(",", ":"))


def run_batch(config_path: Path, catalogue_path: Path, manifest_path: Path, manifest_id: str, database_path: Path, artifact_store_path: Path, emit: Callable[[dict[str, object]], None]) -> bool:
    """Run the immutable foreground list serially; no resume or hidden state."""
    value = cast("_Manifest", parse_manifest(read_batch_manifest(manifest_path)))
    if value["id"] != manifest_id:
        raise BatchError("batch manifest is unavailable")
    configuration = load_configuration_file(config_path)
    selections = tuple(BatchSelection(raw["connection"], raw["library"], raw["library_id"], raw["item"], raw["file_identity"], raw["classification"], raw["eligible"]) for raw in value["entries"])
    if batch_fingerprint(configuration, selections) != value["batch_config_fingerprint"]:
        raise BatchError("batch configuration changed")
    actual_providers = _batch_provider_names(configuration, selections)
    if actual_providers != tuple(value["providers"]):
        raise BatchError("batch configuration changed")
    actual_adapters = _batch_provider_adapters(
        configuration, selections, actual_providers
    )
    if actual_adapters != tuple(value["provider_adapters"]):
        raise BatchError("batch configuration changed")
    _require_open_library_batch_envelope(configuration, selections)
    repository: SQLiteCatalogueRepository | SQLiteMetadataReportRepository
    repository = open_catalogue(catalogue_path, create=False)
    try:
        snapshot = repository.snapshot(batch_catalogue_fingerprint(configuration))
    finally:
        repository.close()
    if snapshot.generation != value["catalogue_generation"] or snapshot.fingerprint != value["catalogue_fingerprint"]:
        raise BatchError("catalogue requires refresh")
    artifacts = CoverArtifactStore(artifact_store_path, create=False)
    artifact_identity = artifacts.identity
    repository = open_repository(database_path)
    try:
        repository_identity = repository.identity
    finally:
        repository.close()
    selected_connections = {selection.connection for selection in selections}
    selected_provider_names = set(value["providers"])
    credentials = _load_batch_credentials(configuration, selected_connections, selected_provider_names)
    open_library_pacer = (
        OpenLibraryPacer() if "open_library" in actual_adapters else None
    )
    hardcover_pacer = (
        HardcoverPacer() if "hardcover" in actual_adapters else None
    )
    def current_entry(
        generation: int, fingerprint: str, key: tuple[str, str, str, str]
    ) -> CatalogueEntry | None:
        repository = open_catalogue(catalogue_path, create=False)
        try:
            return repository.current_entry(generation, fingerprint, key)
        finally:
            repository.close()
    def report(entry: CatalogueEntry) -> tuple[str, tuple[ProviderStatus, ...]]:
        serialized = run_metadata_report_loaded(
            configuration,
            database_path,
            artifact_store_path,
            entry.connection,
            entry.library,
            entry.item_id,
            0,
            expected_file_identity=entry.file_identity,
            credentials=credentials,
            repository_identity=repository_identity,
            artifact_identity=artifact_identity,
            open_library_pacer=open_library_pacer,
            hardcover_pacer=hardcover_pacer,
        )
        from mechanical_librarian.domain.metadata_report import MetadataReport
        report = MetadataReport.from_json(serialized)
        return report.report_id, tuple(item.status for item in report.providers)
    return ForegroundBatchRun(snapshot=snapshot, selections=selections, projection=lambda: batch_fingerprint(configuration, selections), expected_projection=value["batch_config_fingerprint"], current_entry=current_entry, report=report, emit=emit).run()


def _batch_provider_names(configuration: ApplicationConfiguration, selections: tuple[BatchSelection, ...]) -> tuple[str, ...]:
    return tuple(sorted({name for selection in selections for library in configuration.libraries if library.name == selection.library and library.connection == selection.connection for name in library.provider_connections if next(provider for provider in configuration.providers if provider.name == name).enabled}))


def _batch_provider_adapters(
    configuration: ApplicationConfiguration,
    selections: tuple[BatchSelection, ...],
    providers: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    names = (
        _batch_provider_names(configuration, selections)
        if providers is None
        else providers
    )
    values = {provider.name: provider.adapter for provider in configuration.providers}
    return tuple(values[name] for name in names)


def _require_open_library_batch_envelope(
    configuration: ApplicationConfiguration,
    selections: tuple[BatchSelection, ...],
    *,
    all_eligible: bool = False,
) -> None:
    providers = {provider.name: provider for provider in configuration.providers}
    if all_eligible:
        enabled = any(
            providers[name].enabled and providers[name].adapter == "open_library"
            for library in configuration.libraries
            for name in library.provider_connections
        )
    else:
        enabled = "open_library" in _batch_provider_adapters(
            configuration, selections
        )
    if enabled and (all_eligible or len(selections) > 5):
        raise BatchError("Open Library batch selection is invalid")


def _load_batch_credentials(configuration: ApplicationConfiguration, selected_connections: set[str], selected_provider_names: set[str]) -> dict[str, str]:
    values = {"target:" + item.name: read_secret(item.secret_file) for item in configuration.connections if item.name in selected_connections}
    values.update({"provider:" + item.name: read_secret(item.secret_file) for item in configuration.providers if item.name in selected_provider_names and item.secret_file is not None})
    return values


def run_review_server(
    config_path: Path,
    database_path: Path,
    catalogue_path: Path,
    artifact_store_path: Path,
    host: str,
    port: int,
) -> None:
    configuration = load_configuration_file(config_path)
    if configuration.review is None:
        raise ReviewCaseError("configuration")
    review = configuration.review
    if review.tls_certificate_file is None or review.tls_private_key_file is None:
        raise ConfigurationError("review TLS configuration is unavailable")
    with open_review_tls_identity(
        review.tls_certificate_file, review.tls_private_key_file
    ) as tls:
        private_root = _private_root(database_path)
        repository = open_repository(database_path, create=False, cross_thread=True)
        try:
            from mechanical_librarian.adapters.operational_evidence import (
                LocalTimingStore,
                SupportBundle,
            )

            timing_store = LocalTimingStore(
                private_root / "stage-timings.json",
                enabled=review.diagnostic_timings_enabled,
            )
            from mechanical_librarian.application.operations import StageTimer

            timing = StageTimer(timing_store)
            provider_recovery = SQLiteProviderRecoveryStore.open(private_root / "provider-recovery.sqlite")
            preparation = create_review_pipeline(
                configuration,
                database_path,
                catalogue_path,
                artifact_store_path,
                repository.authority,
                timing, private_root, provider_recovery,
            )
            field_application = (
                compose_continuous_guarded_application(
                    configuration,
                    repository,
                    database_path,
                    artifact_store_path,
                    preparation, private_root, provider_recovery,
                )
                if getattr(configuration, "libraries", ())
                else None
            )
            support_bundle = SupportBundle(((
                "runtime",
                lambda: {
                    "deployment": "single_container",
                    "schema_state": "exact_current",
                    "timing_records": len(timing_store.list()),
                },
            ),))
            cover_considerations = _compose_cover_considerations(
                repository, artifact_store_path, review
            )
            review_application = compose_review_application(
                state_owner=_BrowserStateOwnerPort(repository),
                session_store=_ReviewSessionPort(repository),
                case_store=_CurrentReviewCasePort(repository),
                decision_store=_HumanDecisionPort(repository),
                library_store=_ReviewLibraryPort(repository),
                policy_store=_PolicyImpactPort(repository),
                verify_password=load_owner_password_verifier(
                    Path(review.owner_password_hash_file)
                ),
                clock=lambda: int(time.time()),
                session_lifetime_seconds=review.session_lifetime_seconds,
                graceful_shutdown_seconds=review.graceful_shutdown_seconds,
                close_state_owner=True,
                preparation=preparation,
                policy_libraries=tuple(
                    (library.name, library.policy_override)
                    for library in getattr(configuration, "libraries", ())
                ),
                operational_timings=timing_store,
                operational_support=support_bundle,
                operational_timer=timing,
                field_application=field_application,
                cover_considerations=cover_considerations,
            )
            app = create_review_app(review_application, review)
            uvicorn.run(
                app,
                host=host,
                port=port,
                proxy_headers=False,
                server_header=False,
                date_header=False,
                access_log=False,
                http=BoundedReviewH11Protocol,
                limit_concurrency=MAX_REVIEW_CONNECTIONS,
                backlog=MAX_REVIEW_CONNECTIONS,
                h11_max_incomplete_event_size=16_384,
                ssl_certfile=tls.certificate_file,
                ssl_keyfile=tls.private_key_file,
                ssl_keyfile_password=REJECT_ENCRYPTED_PRIVATE_KEY_PASSWORD,
                ssl_ciphers=REVIEW_TLS_CIPHERS,
            )
        except Exception:
            repository.close()
            raise


def _compose_cover_considerations(
    repository: SQLiteMetadataReportRepository, artifact_store_path: Path,
    review: ReviewWebConfiguration,
) -> CoverConsiderationService:
    artifacts: CoverArtifactStore | None = None

    def artifact_store() -> CoverArtifactStore:
        nonlocal artifacts
        if artifacts is None:
            artifacts = CoverArtifactStore(artifact_store_path, create=False)
        return artifacts

    try:
        reviewer_profile = (
            None
            if review.cover_reviewer_profile_file is None
            else read_contained_reviewer_profile(Path(review.cover_reviewer_profile_file))
        )
    except ContainedReviewerProcessError:
        # Never dispatch an unqualified profile. The shared consideration
        # operation records the manual-comparison-unavailable terminal state.
        reviewer_profile = None
    policy = CoverPolicy(
        avoid_promotional_text=review.cover_avoid_promotional_text,
        avoid_tie_in_branding=review.cover_avoid_tie_in_branding,
        prefer_minimal_composition=review.cover_prefer_minimal_composition,
    )
    repository.bind_current_cover_policy(policy.reference)
    service = CoverConsiderationService(
        repository,
        read_preview=lambda artifact_id, variant: artifact_store().read_preview_digest(
            repository.preview_digest_for_artifact(artifact_id, variant)
        ),
        read_preview_digest=lambda digest: artifact_store().read_preview_digest(digest),
        clock=lambda: int(time.time()),
        policy=policy,
        reviewer_profile=reviewer_profile,
        reviewer=None if reviewer_profile is None else run_cover_packet_reviewer,
    )
    repository.bind_current_cover_replay(service.replay_reference)
    return service


def run_cover_review(
    config_path: Path,
    database_path: Path,
    artifact_store_path: Path,
    review_case_id: str,
    action: str,
    artifact_id: str | None,
    idempotency_key: str | None,
) -> dict[str, object]:
    """Project or record one cover outcome through the installed application seams."""
    configuration = load_configuration_file(config_path)
    if configuration.review is None:
        raise ConfigurationError("review configuration is unavailable")
    repository = open_repository(database_path, create=False)
    try:
        considerations = _compose_cover_considerations(
            repository, artifact_store_path, configuration.review
        )
        application = compose_review_application(
            state_owner=_BrowserStateOwnerPort(repository),
            session_store=_ReviewSessionPort(repository),
            case_store=_CurrentReviewCasePort(repository),
            decision_store=_HumanDecisionPort(repository),
            library_store=_ReviewLibraryPort(repository),
            policy_store=_PolicyImpactPort(repository),
            verify_password=lambda _: False,
            clock=lambda: int(time.time()),
            session_lifetime_seconds=1,
            graceful_shutdown_seconds=1,
            close_state_owner=False,
            cover_considerations=considerations,
        )
        if action == "history":
            return application.history(review_case_id)
        if action == "show":
            return application.prepare_cover_consideration(review_case_id)
        detail = application.case_detail(review_case_id).to_data()
        field = next(
            # ``CaseDetailView.to_data`` always projects "fields" from its own
            # ``tuple[dict[str, object], ...]`` attribute.
            value for value in cast("list[dict[str, object]]", detail["fields"])
            if value["destination"] == "cover"
        )
        consideration = field["cover_consideration"]
        assert type(consideration) is dict
        outcome = {
            "keep": "keep_current",
            "select": "accept",
            "no-action": "reject",
        }[action]
        selected_value: dict[str, object] | None = (
            None
            if action != "select"
            else {"kind": "cover", "value": artifact_id}
        )
        if idempotency_key is None:
            raise ValueError("idempotency key is required")
        acknowledgement = application.record_decision(
            review_case_id,
            proposal_id=str(field["proposal_id"]),
            target_fingerprint=str(field["target_fingerprint"]),
            destination="cover",
            actor="owner",
            outcome=outcome,
            selected_value=selected_value,
            idempotency_key=idempotency_key,
            consideration_id=str(consideration["consideration_id"]),
        )
        return {
            "consideration_id": consideration["consideration_id"],
            "decision": acknowledgement,
            "schema_version": "cover-review-cli-v1",
        }
    finally:
        repository.close()


def run_review_activity(
    config_path: Path,
    database_path: Path,
) -> dict[str, object]:
    """Read installed guarded application and recovery history through CLI."""
    configuration = load_configuration_file(config_path)
    if configuration.review is None:
        raise ConfigurationError("review configuration is unavailable")
    repository = open_repository(database_path, create=False)
    provider_recovery = None
    try:
        recovery_path = database_path.parent / "private" / "provider-recovery.sqlite"
        if recovery_path.is_file():
            provider_recovery = SQLiteProviderRecoveryStore.open(recovery_path)
            projection = compose_field_effect_activity_projection(
                configuration, repository, provider_recovery
            )
        else:
            projection = compose_field_effect_activity_projection(
                configuration, repository
            )
        return projection.activity()
    finally:
        if provider_recovery is not None:
            provider_recovery.close()
        repository.close()


def run_provider_recovery_once(
    config_path: Path, database_path: Path, catalogue_path: Path, artifact_store_path: Path
) -> None:
    """Run the same one-claim recovery path used by the preparation supervisor."""
    configuration = load_configuration_file(config_path)
    if configuration.review is None:
        raise ConfigurationError("review configuration is unavailable")
    store = SQLiteProviderRecoveryStore.open(
        _private_root(database_path) / "provider-recovery.sqlite"
    )
    supervisor = create_review_pipeline(
        configuration, database_path, catalogue_path, artifact_store_path,
        provider_recovery=store,
    )
    try:
        supervisor.recover_provider_once()
    finally:
        supervisor.stop()


def run_policy_preview(
    config_path: Path, database_path: Path, document: dict[str, object]
) -> dict[str, object]:
    """Compose the existing read-only policy notebook for one CLI preview."""
    from mechanical_librarian.domain.metadata_policy import MetadataPolicyProfile

    configuration = load_configuration_file(config_path)
    repository = open_shadow_preview_repository(database_path)
    try:
        # LATENT DEFECT (typing-only unit: not repaired here).
        # ``SQLiteShadowPreviewRepository`` implements only the read-only policy
        # preview surface. It really does lack every session, review-case,
        # decision, library, browser-state, and policy-mutation member these
        # parameters name, so these are not variance mismatches: any composed
        # operation other than ``preview_policy``/``policy.rich_qualification``
        # below would raise ``AttributeError`` at runtime. Composition only
        # reaches those two here, and ``operational_timings`` is omitted so the
        # ``state_owner.operational_snapshot`` closure is never built.
        application = compose_review_application(
            state_owner=repository,  # type: ignore[arg-type]  # reason: see the note above; shadow preview lacks BrowserStateOwner
            session_store=repository,  # type: ignore[arg-type]  # reason: see the note above; shadow preview lacks ReviewSessionStore
            case_store=repository,  # type: ignore[arg-type]  # reason: see the note above; shadow preview lacks CurrentReviewCaseStore
            decision_store=repository,  # type: ignore[arg-type]  # reason: see the note above; shadow preview lacks HumanDecisionStore
            library_store=repository,  # type: ignore[arg-type]  # reason: see the note above; shadow preview lacks ReviewLibraryStore
            policy_store=repository,  # type: ignore[arg-type]  # reason: see the note above; shadow preview lacks most of PolicyImpactRepository
            verify_password=lambda _: False,
            clock=lambda: int(time.time()),
            session_lifetime_seconds=1,
            graceful_shutdown_seconds=1,
            close_state_owner=False,
            policy_libraries=tuple(
                (library.name, library.policy_override)
                for library in getattr(configuration, "libraries", ())
            ),
        )
        profile = MetadataPolicyProfile.from_data(document)
        _, preview = application.preview_policy(
            profile.name, dict(profile.options), dict(profile.destinations)
        )
        return {
            "libraries": [
                {
                    "affected_categories": list(item.affected_categories),
                    "cases": [case.to_data() for case in item.cases],
                    "cases_total": item.cases_total,
                    "cases_truncated": item.cases_truncated,
                    "current_unapplied_count": item.current_unapplied_count,
                    "library": item.library,
                    "resolved_policy_id": item.resolved_policy_id,
                }
                for item in preview.libraries
            ],
            "policy_id": profile.policy_id,
            "rich_qualification": list(application.policy.rich_qualification(profile)),
            "schema_version": "metadata-policy-preview-v1",
        }
    finally:
        repository.close()


def main(argv: Sequence[str] | None = None) -> int:
    from mechanical_librarian.interfaces.cli import main as runtime_main
    return runtime_main(
        argv, review_serve_runner=run_review_server,
        policy_preview_runner=run_policy_preview,
        cover_review_runner=run_cover_review,
        review_activity_runner=run_review_activity,
        provider_recovery_runner=run_provider_recovery_once,
    )


__all__ = [
    "create_proposal_set", "inspect_batch", "main", "preview_batch",
    "reopen_cover_preview", "reopen_current_metadata_report",
    "reopen_metadata_report", "reopen_proposal_set", "admit_review_case", "reopen_review_case",
    "preview_shadow_decision_case", "run_batch",
    "run_catalogue", "run_cover_packet_reviewer",
    "run_metadata_report",
]
