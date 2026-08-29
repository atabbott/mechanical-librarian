"""Credential-bearing composition root for the sole metadata report command."""

from __future__ import annotations

import json
import logging
import os
import stat
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any, Protocol, TypedDict, cast

import uvicorn

from mechanical_librarian.adapters.cover_artifacts import (
    CoverDecodeRejected,
    CoverDecodeTimeout,
    decode_cover,
)
from mechanical_librarian.adapters.evidence_extractors.audio_local import (
    sidecar_surface,
)
from mechanical_librarian.adapters.evidence_extractors.epub_cover import (
    read_declared_cover,
)
from mechanical_librarian.adapters.evidence_extractors.process import (
    run_audio_inspector_child,
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
from mechanical_librarian.adapters.library_services.audiobookshelf_setup import (
    AudiobookshelfSetupProbe,
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
from mechanical_librarian.adapters.persistence.review_sessions import (
    SQLiteReviewSessionRepository,
)
from mechanical_librarian.adapters.private_recovery import (
    PrivateCoverRecoveryStore,
    require_private_directory,
)
from mechanical_librarian.adapters.providers.google_books_metadata import (
    GoogleBooksMetadataProvider,
    GoogleBooksPacer,
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
from mechanical_librarian.adapters.reviewers.codex_device_auth import (
    CodexDeviceAuthError,
    CodexDeviceAuthManager,
)
from mechanical_librarian.adapters.reviewers.contained_reviewer import (
    ContainedReviewerProcessError,
    ContainedReviewerProfile,
    load_contained_reviewer_profile_bytes,
    prepare_contained_device_auth_command,
    read_contained_reviewer_profile,
)
from mechanical_librarian.adapters.reviewers.openrouter import OpenRouterReviewer
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
    RefreshCurrentCatalogue,
)
from mechanical_librarian.application.configuration import (
    ConfigurationError,
    MediaMountConfiguration,
    ProviderConnectionConfiguration,
    ReviewWebConfiguration,
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
from mechanical_librarian.application.metadata_policy import (
    PolicyPreviewApplication,
)
from mechanical_librarian.application.metadata_policy import (
    current_resolved_policy as _current_resolved_policy,
)
from mechanical_librarian.application.metadata_report import (
    ArtifactAuthorityError,
    AudioInspectionResult,
    AudioItemInspectionError,
    CoverReadError,
    CoverSourceChangedError,
    CoverTimeoutError,
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
from mechanical_librarian.application.owner_settings import (
    FileOwnerSettingsRepository,
    OwnerSettingsOperations,
    file_settings_projection,
)
from mechanical_librarian.application.proposal_sets import (
    CreateProposalSet,
    GetProposalSet,
    ProposalCurrentnessError,
)
from mechanical_librarian.application.provider_recovery_activation import (
    ProviderRecoveryActivation,
    ProviderRecoveryRepository,
)
from mechanical_librarian.application.provider_recovery_evidence import (
    classify_provider_recovery_failure as _classify_provider_recovery_failure,
)
from mechanical_librarian.application.provider_recovery_evidence import (  # noqa: F401  # reason: the composition root keeps this established private alias so tests/integration/test_provider_recovery_composition.py reaches the relocated helper unchanged
    provider_recovery_jobs as _provider_recovery_jobs,
)
from mechanical_librarian.application.provider_recovery_evidence import (  # noqa: F401  # reason: the composition root keeps this established private alias so tests/unit/test_provider_recovery.py reaches the relocated helper unchanged
    record_provider_recovery_observations as _record_provider_recovery_observations,
)
from mechanical_librarian.application.provider_recovery_runner import (
    ScopedProviderRecoveryRunner,
)
from mechanical_librarian.application.provider_retry_successor import (
    ConfiguredProviderRetry,
    ProviderRetryDisabled,
    ProviderRetrySuccessorError,
)
from mechanical_librarian.application.review_application import (
    compose_review_application,
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
from mechanical_librarian.application.review_pipeline_stages import (
    ReviewPipelineStages,
)
from mechanical_librarian.application.review_web import OwnerSessionService
from mechanical_librarian.application.reviewer_access import (
    CodexAuth,
    ReviewerAccessOperations,
)
from mechanical_librarian.application.reviewer_contracts import CodexDeviceAuthState
from mechanical_librarian.application.reviewer_host import (
    ReviewerHostStatus,
    reviewer_host_status,
)
from mechanical_librarian.application.setup_connection import (
    SetupConnectionOperation,
    SetupConnectionVerification,
)
from mechanical_librarian.application.setup_journey import SetupJourneyOperation
from mechanical_librarian.bootstrap.contained_reviewer import run_cover_packet_reviewer
from mechanical_librarian.domain.audio_inspection import (
    AudioInspectionLimits,
    AudioInspectionOutcome,
)
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
)
from mechanical_librarian.domain.metadata_report import (
    AUDIO_FILE_FORMATS,
    AudioFacts,
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
from mechanical_librarian.domain.owner_settings import SettingsView
from mechanical_librarian.domain.proposal_set import ProposalSet
from mechanical_librarian.domain.setup_state import (
    SetupLibraryCandidate,
    SetupStateDocument,
)
from mechanical_librarian.domain.validation import OWNER_SESSION_LIFETIME_DEFAULT
from mechanical_librarian.infrastructure.configuration import load_configuration_file
from mechanical_librarian.infrastructure.first_boot import FirstBootMode
from mechanical_librarian.infrastructure.local_media import (
    LocalMediaError,
    LocalMediaLimitError,
    list_local_media_siblings,
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
from mechanical_librarian.infrastructure.settings_authority import (
    ReviewBootstrap,
    load_review_bootstrap,
    store_runtime_configuration,
)
from mechanical_librarian.interfaces.web import (
    create_review_app,
    load_owner_password_binding,
    owner_password_binding,
    owner_password_verifier,
)
from mechanical_librarian.interfaces.web.runtime_dispatch import (
    MutableRuntimeDispatcher,
    RuntimeGeneration,
)
from mechanical_librarian.interfaces.web.setup import create_setup_app

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
        LibraryConfiguration,
        TargetConnectionConfiguration,
    )
    from mechanical_librarian.application.field_effects import CapabilityObserver
    from mechanical_librarian.application.metadata_report import (
        EpubInspector,
        MetadataProvider,
        ReportRepository,
        TargetReader,
    )
    from mechanical_librarian.application.operations import StageTimer
    from mechanical_librarian.application.provider_recovery_runner import RecoveryRun
    from mechanical_librarian.application.review_pipeline import (
        ReviewPipelineRepository,
    )


class _StoreRuntimeActivation:
    """One process-lifetime controller for setup and Settings cutovers."""

    def __init__(
        self,
        bootstrap: ReviewBootstrap,
        database_path: Path,
        catalogue_path: Path,
        artifact_store_path: Path,
        settings_store: Any,
        repository_authority: object,
    ) -> None:
        self._bootstrap = bootstrap
        self._database_path = database_path
        self._catalogue_path = catalogue_path
        self._artifact_store_path = artifact_store_path
        self._settings_store = settings_store
        self._repository_authority = repository_authority
        self._dispatcher: MutableRuntimeDispatcher | None = None
        self._lock = threading.RLock()
        self._codex_auth = _CodexAuthLeaseCoordinator()

    @property
    def dispatcher(self) -> MutableRuntimeDispatcher:
        if self._dispatcher is None:
            raise RuntimeError("runtime dispatcher is unavailable")
        return self._dispatcher

    def bind(self, dispatcher: MutableRuntimeDispatcher) -> None:
        if self._dispatcher is not None:
            raise RuntimeError("runtime dispatcher is already bound")
        self._dispatcher = dispatcher

    def prepare(
        self, document: object, revision: int, secrets: object
    ) -> RuntimeGeneration:
        snapshot = self._settings_store.activation_snapshot(document, revision, secrets)
        candidate, _review = compose_store_runtime_generation(
            self._bootstrap,
            self._database_path,
            self._catalogue_path,
            self._artifact_store_path,
            repository_authority=self._repository_authority,
            authority_snapshot=snapshot,
            activation=self,
        )
        return candidate

    def activate(
        self,
        document: object,
        revision: int,
        secrets: object,
        commit: Callable[[], object],
    ) -> object:
        with self._lock:
            return self._activate(self.prepare(document, revision, secrets), commit)

    def _activate(
        self, candidate: RuntimeGeneration, commit: Callable[[], object]
    ) -> object:
        dispatcher = self.dispatcher
        expected = dispatcher.current()
        dispatcher.prepare(candidate)
        return dispatcher.transition(expected, candidate, commit)

    def activate_setup(
        self,
        snapshot: tuple[SettingsView, dict[str, str], str],
        commit: Callable[[], None],
    ) -> None:
        with self._lock:
            candidate, _review = compose_store_runtime_generation(
                self._bootstrap,
                self._database_path,
                self._catalogue_path,
                self._artifact_store_path,
                repository_authority=self._repository_authority,
                authority_snapshot=snapshot,
                activation=self,
                first_boot=True,
            )
            self._activate(candidate, commit)

    def acknowledge_initial_runtime(self) -> None:
        """Retire compatibility debt only after the initial generation woke."""
        view = self._settings_store.read()
        self._settings_store.acknowledge_runtime(view.document.revision)

    def close(self) -> None:
        self._codex_auth.close()


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


def _required_secret_file(value: str | None) -> str:
    """Keep file-backed credential reads explicit at the composition boundary."""
    if value is None:
        raise ConfigurationError("credential path is unavailable")
    return value


def compose_audiobookshelf_field_effect_target(
    configuration: ApplicationConfiguration,
    artifact_store_path: Path,
    connection_name: str,
    library_name: str,
    *,
    credentials: dict[str, str] | None = None,
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
                connection.endpoint,
                _required_secret_file(connection.secret_file),
                configuration.limits.timeout_seconds,
            )
            if credentials is None
            else target_client_token(
                connection.endpoint,
                credentials["target:" + connection.name],
                configuration.limits.timeout_seconds,
            )
        ),
        endpoint=connection.endpoint,
        library_id=library.library_id,
        artifacts=artifacts,
        maximum_response_bytes=configuration.limits.target_response_bytes,
    )


def compose_audiobookshelf_field_effect_capabilities(
    configuration: ApplicationConfiguration,
    connection_name: str,
    library_name: str,
    *,
    credentials: dict[str, str] | None = None,
) -> AudiobookshelfFieldEffectCapabilities:
    connection, library, _mount, _providers = configuration.select(
        connection_name, library_name
    )
    return AudiobookshelfFieldEffectCapabilities(
        client_factory=lambda: (
            target_client(
                connection.endpoint,
                _required_secret_file(connection.secret_file),
                configuration.limits.timeout_seconds,
            )
            if credentials is None
            else target_client_token(
                connection.endpoint,
                credentials["target:" + connection.name],
                configuration.limits.timeout_seconds,
            )
        ),
        endpoint=connection.endpoint,
        library_id=library.library_id,
        maximum_response_bytes=configuration.limits.target_response_bytes,
    )


def compose_continuous_guarded_application(
    configuration: ApplicationConfiguration,
    repository: SQLiteMetadataReportRepository,
    database_path: Path,
    artifact_store_path: Path,
    preparation: ReviewPreparationSupervisor,
    private_root: Path | None = None,
    provider_recovery: SQLiteProviderRecoveryStore | None = None,
    credentials: dict[str, str] | None = None,
) -> ContinuousGuardedApplication:
    """Activate one configured Audiobookshelf instance across its libraries."""
    configured = tuple(
        (library.connection, library.name) for library in configuration.libraries
    )
    if not configured:
        raise ConfigurationError("guarded application has no configured library")
    artifact_identity = CoverArtifactStore(artifact_store_path, create=True).identity
    targets = {
        key: compose_audiobookshelf_field_effect_target(
            configuration,
            artifact_store_path,
            *key,
            credentials=credentials,
            artifact_identity=artifact_identity,
        )
        for key in configured
    }
    capabilities: dict[tuple[str, str], CapabilityObserver] = {
        key: compose_audiobookshelf_field_effect_capabilities(
            configuration, *key, credentials=credentials
        )
        for key in configured
    }
    private_root = (
        _private_root(database_path) if private_root is None else private_root
    )
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
        repository,
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
        activity_snapshot=repository.field_effect_activity_snapshot,
    )


def compose_field_effect_activity_projection(
    configuration: ApplicationConfiguration,
    repository: SQLiteMetadataReportRepository,
    provider_recovery: SQLiteProviderRecoveryStore | None = None,
) -> ContinuousGuardedApplication:
    """Compose the shared Activity projection without write-capable adapters."""
    configured = tuple(
        (library.connection, library.name) for library in configuration.libraries
    )
    if not configured:
        raise ConfigurationError("guarded application has no configured library")
    return ContinuousGuardedApplication(
        repository,
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
        activity_snapshot=repository.field_effect_activity_snapshot,
    )


def open_field_effect_activity_projection(
    configuration: ApplicationConfiguration,
    database_path: Path,
    authority: object,
    provider_recovery: SQLiteProviderRecoveryStore | None = None,
) -> tuple[ContinuousGuardedApplication, Callable[[], None]]:
    """Open the Activity reader independently of the state-owner connection."""
    repository = open_repository(
        database_path, create=False, authority=authority, cross_thread=True
    )
    try:
        repository.configure_activity_page_reader()
        return (
            compose_field_effect_activity_projection(
                configuration, repository, provider_recovery
            ),
            repository.close,
        )
    except BaseException:
        repository.close()
        raise


class _IsolatedEpubInspector:
    def __init__(self, artifacts: CoverArtifactStore) -> None:
        self._artifacts = artifacts

    def inspect(
        self, mount: MediaMountConfiguration, relative_path: str, maximum_bytes: int
    ) -> InspectionResult:
        opened = None
        try:
            opened = open_local_media(mount.container_path, relative_path)
            digest = sha256_descriptor(
                opened.descriptor, opened.length, maximum=maximum_bytes
            )
            source = Fingerprint("sha256", SchemaVersion("local-file-v1"), digest)
            report = run_epub_inspector_child(
                opened.descriptor,
                source,
                EpubOpeningLimits(),
                logical_identity=digest,
            )
            inspection = report
            if inspection.outcome not in {
                EpubInspectionOutcome.SUCCESS,
                EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE,
            }:
                raise ItemInspectionError(inspection.outcome)
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
                    rear_provenance=None
                    if fact.rear_provenance is None
                    else RearClaimProvenance(
                        **cast("_RearProvenanceFields", fact.rear_provenance.to_data())
                    ),
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
                        self._artifacts.stage(
                            decoded.artifact, declared.data or b"", decoded.preview
                        )
                        covers = (
                            CoverSource(
                                declared.source_id,
                                CoverSourceKind.EPUB,
                                CoverSourceState.RETRIEVED,
                                decoded.artifact,
                            ),
                        )
                    except ArtifactAuthorityError:
                        raise
                    except MetadataReportError:
                        covers = (
                            CoverSource(
                                declared.source_id,
                                CoverSourceKind.EPUB,
                                CoverSourceState.UNAVAILABLE,
                            ),
                        )
                except CoverDecodeRejected as error:
                    covers = (
                        CoverSource(
                            declared.source_id, CoverSourceKind.EPUB, error.state
                        ),
                    )
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
                covers = (
                    CoverSource(
                        declared.source_id, CoverSourceKind.EPUB, declared.state
                    ),
                )
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
        except LocalMediaLimitError as error:
            raise ItemInspectionError(EpubInspectionOutcome.RESOURCE_LIMIT) from error
        except LocalMediaError as error:
            raise ItemInspectionError(
                EpubInspectionOutcome.UNAVAILABLE_SOURCE
            ) from error
        except OSError as error:
            raise ItemInspectionError(
                EpubInspectionOutcome.UNAVAILABLE_SOURCE
            ) from error
        except ValueError as error:
            raise ItemInspectionError("EPUB inspection failed") from error
        finally:
            if opened is not None:
                os.close(opened.descriptor)

    def unchanged(
        self,
        mount: MediaMountConfiguration,
        relative_path: str,
        expected_digest: str,
        maximum_bytes: int,
    ) -> bool:
        opened = None
        try:
            opened = open_local_media(mount.container_path, relative_path)
            return (
                sha256_descriptor(
                    opened.descriptor, opened.length, maximum=maximum_bytes
                )
                == expected_digest
            )
        except (LocalMediaError, OSError, ValueError):
            return False
        finally:
            if opened is not None:
                os.close(opened.descriptor)


class _IsolatedAudioInspector:
    """Compose F43's audiobook capability behind one ``inspect`` call.

    The mount, the sibling listing, and every descriptor are opened here; the
    probe and the interpretation both happen in isolated children, and this
    object never reads a media or sidecar byte itself.
    """

    def inspect(
        self, mount: MediaMountConfiguration, relative_path: str, maximum_bytes: int
    ) -> AudioInspectionResult:
        opened = None
        sidecars: list[tuple[str, str, int]] = []
        try:
            opened = open_local_media(mount.container_path, relative_path)
            digest = sha256_descriptor(
                opened.descriptor, opened.length, maximum=maximum_bytes
            )
            source = Fingerprint("sha256", SchemaVersion("local-file-v1"), digest)
            components = relative_path.split("/")
            sidecars = _open_audio_sidecars(mount, relative_path, components[-1])
            inspection = run_audio_inspector_child(
                opened.descriptor,
                source,
                AudioInspectionLimits(source_bytes=_audio_source_bytes(maximum_bytes)),
                sidecars=tuple(sidecars),
                path_file=components[-1],
                path_folder=components[-2] if len(components) > 1 else None,
            )
            if not inspection.outcome.succeeded:
                raise AudioItemInspectionError(inspection.outcome)
            claims = tuple(
                LocalClaim.create(
                    kind=ClaimKind(fact.field.value),
                    observed=fact.observed_value,
                    canonical=fact.canonical_value,
                    excerpt=fact.supporting_context,
                    applicability="not_applicable",
                    basis=fact.basis,
                    checksum="not_applicable",
                    applicability_basis="not_applicable",
                    state=FieldState(fact.state),
                    role=fact.role,
                    occurrence=occurrence,
                )
                for occurrence, fact in enumerate(inspection.facts, 1)
            )
            return AudioInspectionResult(
                digest,
                claims,
                AudioFacts(
                    inspection.chapters,
                    inspection.chapters_truncated,
                    inspection.embedded_cover,
                ),
            )
        except MetadataReportError:
            raise
        except LocalMediaLimitError as error:
            raise AudioItemInspectionError(
                AudioInspectionOutcome.RESOURCE_LIMIT
            ) from error
        except (LocalMediaError, OSError) as error:
            raise AudioItemInspectionError(
                AudioInspectionOutcome.UNAVAILABLE_SOURCE
            ) from error
        except ValueError as error:
            raise AudioItemInspectionError(
                AudioInspectionOutcome.INTERNAL_FAILURE
            ) from error
        finally:
            for _surface, _name, descriptor in sidecars:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if opened is not None:
                os.close(opened.descriptor)

    def unchanged(
        self,
        mount: MediaMountConfiguration,
        relative_path: str,
        expected_digest: str,
        maximum_bytes: int,
    ) -> bool:
        opened = None
        try:
            opened = open_local_media(mount.container_path, relative_path)
            return (
                sha256_descriptor(
                    opened.descriptor, opened.length, maximum=maximum_bytes
                )
                == expected_digest
            )
        except (LocalMediaError, OSError, ValueError):
            return False
        finally:
            if opened is not None:
                os.close(opened.descriptor)


def _audio_source_bytes(maximum_bytes: int) -> int:
    """Fit the configured EPUB archive ceiling to the audio limit's own cap."""
    caps = AudioInspectionLimits.HARD_CAPS["source_bytes"]
    return max(1, min(int(maximum_bytes), caps))


def _open_audio_sidecars(
    mount: MediaMountConfiguration, relative_path: str, media_name: str
) -> list[tuple[str, str, int]]:
    """Open the supported sidecars beside one audio file, and nothing else.

    A sidecar that cannot be opened is simply absent evidence: the audiobook's
    own container is the primary surface, and one unreadable neighbour must not
    deny the owner the rest of the set.
    """
    directory = relative_path.rsplit("/", 1)[0] if "/" in relative_path else ""
    try:
        names = list_local_media_siblings(mount.container_path, relative_path)
    except LocalMediaError:
        return []
    opened: list[tuple[str, str, int]] = []
    for name in names:
        if len(opened) >= AudioInspectionLimits().sidecar_documents:
            break
        surface = sidecar_surface(name, media_name)
        if surface is None:
            continue
        reference = f"{directory}/{name}" if directory else name
        try:
            sibling = open_local_media(mount.container_path, reference)
        except LocalMediaError:
            continue
        opened.append((surface.value, name, sibling.descriptor))
    return opened


class _CoverEvidenceObserver:
    """Acquire the one declared ABS cover inside the slice's bounded envelope.

    EPUB results are already collected by the isolated media inspector.  This
    deliberately has no selection policy: each source merely records its own
    acquisition or validation outcome.
    """

    def __init__(
        self,
        target: TargetCoverReader,
        artifacts: CoverArtifactStore,
        providers: tuple[ProviderConnectionConfiguration, ...] = (),
    ) -> None:
        self._target = target
        self._artifacts = artifacts
        self._providers = {value.name: value for value in providers}

    def observe(
        self,
        target: TargetSnapshot,
        providers: tuple[ProviderObservation, ...],
        local_sources: tuple[CoverSource, ...],
        descriptors: tuple[ProviderCoverDescriptor, ...] = (),
        aggregate_deadline: float | None = None,
    ) -> tuple[CoverSource, ...]:
        now = time.monotonic()
        aggregate = now + 60.0 if aggregate_deadline is None else aggregate_deadline
        deadline = CoverDeadline(aggregate, min(now + 20.0, aggregate))
        if target.cover is None:
            source = CoverSource(
                cover_source_id(CoverSourceKind.ABS, "absent"),
                CoverSourceKind.ABS,
                CoverSourceState.ABSENT,
            )
            return (
                source,
                *local_sources,
                *self._provider_covers(providers, descriptors, deadline=deadline),
            )
        source_id = cover_source_id(CoverSourceKind.ABS, target.cover.identity)
        try:
            original = self._target.read_cover(
                target.cover, deadline=deadline.aggregate_deadline
            )
            if original is None:
                source = CoverSource(
                    source_id, CoverSourceKind.ABS, CoverSourceState.ABSENT
                )
            else:
                decoded = decode_cover(
                    original,
                    deadline=deadline.aggregate_deadline,
                )
                self._artifacts.stage(decoded.artifact, original, decoded.preview)
                source = CoverSource(
                    source_id,
                    CoverSourceKind.ABS,
                    CoverSourceState.RETRIEVED,
                    decoded.artifact,
                )
        except CoverTimeoutError:
            source = CoverSource(
                source_id,
                CoverSourceKind.ABS,
                CoverSourceState.TIMEOUT,
                timeout_scope="attempted",
            )
        except CoverDecodeTimeout:
            source = CoverSource(
                source_id,
                CoverSourceKind.ABS,
                CoverSourceState.TIMEOUT,
                timeout_scope="attempted",
            )
        except CoverSourceChangedError:
            source = CoverSource(
                source_id, CoverSourceKind.ABS, CoverSourceState.SOURCE_CHANGED
            )
        except CoverDecodeRejected as error:
            source = CoverSource(source_id, CoverSourceKind.ABS, error.state)
        except CoverReadError as error:
            source = CoverSource(
                source_id, CoverSourceKind.ABS, CoverSourceState(error.state)
            )
        except ArtifactAuthorityError:
            raise
        except MetadataReportError:
            source = CoverSource(
                source_id, CoverSourceKind.ABS, CoverSourceState.UNAVAILABLE
            )
        return (
            source,
            *local_sources,
            *self._provider_covers(providers, descriptors, deadline=deadline),
        )

    def _provider_covers(
        self,
        observations: tuple[ProviderObservation, ...],
        descriptors: tuple[ProviderCoverDescriptor, ...],
        *,
        deadline: CoverDeadline,
    ) -> tuple[CoverSource, ...]:
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
                results.append(self._remote(value, deadline))
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

    def _remote(
        self, descriptor: ProviderCoverDescriptor, deadline: CoverDeadline
    ) -> CoverSource:
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
            if descriptor.wait is not None and not descriptor.wait(
                deadline.aggregate_deadline
            ):
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
            state = (
                CoverSourceState.UNSAFE_REMOTE
                if "unsafe" in message
                else CoverSourceState.UNSUPPORTED_FORMAT
                if "format" in message
                else CoverSourceState.OVERSIZED
                if "oversized" in message
                else CoverSourceState.UNAVAILABLE
            )
            return CoverSource(source_id, kind, state, provenance=descriptor.provenance)
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


def run_metadata_report(
    config_path: Path,
    database_path: Path,
    artifact_store_path: Path,
    connection_name: str,
    library_name: str,
    item: str,
    file_index: int,
    expected_file_identity: str | None = None,
) -> str:
    # Validate the separately authorized private store before credentials or
    # network clients are constructed.  It must already be operator-created.
    configuration = load_configuration_file(config_path)
    return run_metadata_report_loaded(
        configuration,
        database_path,
        artifact_store_path,
        connection_name,
        library_name,
        item,
        file_index,
        expected_file_identity,
    )


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


def run_metadata_report_loaded(
    configuration: ApplicationConfiguration,
    database_path: Path,
    artifact_store_path: Path,
    connection_name: str,
    library_name: str,
    item: str,
    file_index: int,
    expected_file_identity: str | None = None,
    credentials: dict[str, str] | None = None,
    repository_identity: tuple[int, int] | None = None,
    artifact_identity: tuple[int, int] | None = None,
    google_books_pacer: GoogleBooksPacer | None = None,
    open_library_pacer: OpenLibraryPacer | None = None,
    hardcover_pacer: HardcoverPacer | None = None,
    review_action: PipelineAction | None = None,
    recover_artifacts: bool = True,
    repository_authority: object | None = None,
    pipeline_repository: SQLiteMetadataReportRepository | None = None,
    review_claim_next: bool = False,
    timing: StageTimer | None = None,
) -> str:
    """Compose one report solely from an already validated configuration snapshot."""
    connection, library, mount, provider_configs = configuration.select(
        connection_name, library_name
    )
    if mount is None:
        raise UnsupportedMediaError("unsupported media")
    artifacts = CoverArtifactStore(
        artifact_store_path, create=False, expected_identity=artifact_identity
    )
    target = AudiobookshelfItemReader(
        client_factory=lambda: (
            target_client(
                connection.endpoint,
                _required_secret_file(connection.secret_file),
                configuration.limits.timeout_seconds,
            )
            if credentials is None
            else target_client_token(
                connection.endpoint,
                credentials["target:" + connection.name],
                configuration.limits.timeout_seconds,
            )
        ),
        endpoint=connection.endpoint,
        library_id=library.library_id,
        service_path=mount.service_path,
        maximum_bytes=configuration.limits.target_response_bytes,
        timeout_seconds=configuration.limits.timeout_seconds,
    )
    if google_books_pacer is None and any(
        provider.enabled and provider.adapter == "google_books"
        for provider in provider_configs
    ):
        google_books_pacer = GoogleBooksPacer()
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
                    credentials.get("provider:" + value.name)
                    if credentials is not None
                    else (
                        None
                        if value.secret_file is None
                        else read_secret(value.secret_file)
                    )
                )
                instance = GoogleBooksMetadataProvider(
                    value.name,
                    google_books_client(configuration.limits.timeout_seconds),
                    configuration.limits.provider_response_bytes,
                    configuration.limits.timeout_seconds,
                    api_key=api_key,
                    pacer=google_books_pacer,
                )
            elif value.adapter == "hardcover":
                if value.secret_file is None and credentials is None:
                    raise MetadataReportError("provider credential unavailable")
                instance = HardcoverMetadataProvider(
                    value.name,
                    hardcover_client(
                        _required_secret_file(value.secret_file),
                        configuration.limits.timeout_seconds,
                    )
                    if credentials is None
                    else hardcover_client_token(
                        credentials["provider:" + value.name],
                        configuration.limits.timeout_seconds,
                    ),
                    configuration.limits.provider_response_bytes,
                    configuration.limits.timeout_seconds,
                    pacer=hardcover_pacer,
                )
            elif value.adapter == "open_library" and value.contact_email is not None:
                instance = OpenLibraryMetadataProvider(
                    value.name,
                    open_library_client(
                        value.contact_email, configuration.limits.timeout_seconds
                    ),
                    configuration.limits.provider_response_bytes,
                    configuration.limits.timeout_seconds,
                    pacer=open_library_pacer,
                )
            else:
                raise MetadataReportError("provider configuration is invalid")
            return instance

        bindings.append(
            ProviderBinding(provider.name, provider.enabled, factory, provider.adapter)
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
            return repository
        except Exception:
            repository.close()
            raise

    service = GenerateMetadataReport(
        target=target,
        inspector=_IsolatedEpubInspector(artifacts),
        audio_inspector=_IsolatedAudioInspector(),
        repository_factory=repository_factory,
        providers=tuple(bindings),
        connection=connection,
        library=library,
        mount=mount,
        limits=configuration.limits,
        cover_observer=_CoverEvidenceObserver(
            target, artifacts, tuple(provider_configs)
        ),
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
                # An audiobook's local evidence is the whole of its preparation:
                # the work ends terminally here, in the same transaction that
                # published the report, rather than queueing stages that have
                # no provider comparison to work from.
                "local_evidence_only"
                if report.file.format in AUDIO_FILE_FORMATS
                else None,
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
    credentials: dict[str, str] | None = None,
) -> bool:
    connection, library, mount, _ = configuration.select(
        report.target.connection, report.target.library
    )
    if mount is None:
        raise CurrentnessError("target or media changed")
    target = AudiobookshelfItemReader(
        client_factory=lambda: (
            target_client(
                connection.endpoint,
                _required_secret_file(connection.secret_file),
                configuration.limits.timeout_seconds,
            )
            if credentials is None
            else target_client_token(
                connection.endpoint,
                credentials["target:" + connection.name],
                configuration.limits.timeout_seconds,
            )
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
        audio_inspector=_IsolatedAudioInspector(),
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
    credentials: dict[str, str] | None = None,
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
                configuration, artifacts, report, credentials
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
    credentials: dict[str, str] | None = None,
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
            if not _require_report_current_loaded(
                configuration, artifacts, report, credentials
            ):
                return False
            return (
                proposal.resolved_policy_id
                == _current_resolved_policy(
                    configuration, repository, report.target.library
                ).resolved_id
            )
        except (CurrentnessError, TargetNotFoundError):
            return False

    try:
        return AdmitReviewCase(repository, current).run(proposal_set_id)
    finally:
        repository.close()


def create_review_pipeline(
    configuration: ApplicationConfiguration,
    database_path: Path,
    catalogue_path: Path,
    artifact_store_path: Path,
    reviewer_access: ReviewerAccessOperations,
    database_authority: object | None = None,
    timing: StageTimer | None = None,
    private_root: Path | None = None,
    provider_recovery: SQLiteProviderRecoveryStore | None = None,
    credentials: dict[str, str] | None = None,
    first_boot: bool = False,
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
        client_factory=lambda name: (
            target_client(
                clients[name].endpoint,
                _required_secret_file(clients[name].secret_file),
                configuration.limits.timeout_seconds,
            )
            if credentials is None
            else target_client_token(
                clients[name].endpoint,
                credentials["target:" + name],
                configuration.limits.timeout_seconds,
            )
        ),
        mounts=mounts,
        maximum_bytes=configuration.limits.target_response_bytes,
        timeout_seconds=configuration.limits.timeout_seconds,
    )
    google_books_pacer = (
        GoogleBooksPacer()
        if any(
            provider.enabled and provider.adapter == "google_books"
            for provider in configuration.providers
        )
        else None
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

    def observation_repository_factory() -> SQLiteMetadataReportRepository:
        return open_repository(
            database_path,
            create=False,
            authority=database_authority,
            cross_thread=True,
        )

    def retry_resolver(
        connection_name: str,
        library_name: str,
        name: str,
        adapter: str,
        generation: int,
    ) -> ConfiguredProviderRetry | None:
        if generation != configuration.provider_recovery_generation:
            return None
        try:
            _, _, _, configured = configuration.select(connection_name, library_name)
        except ConfigurationError:
            return None
        provider = next(
            (
                value
                for value in configured
                if value.name == name and value.adapter == adapter
            ),
            None,
        )
        if provider is None:
            return None
        if not provider.enabled:
            raise ProviderRetryDisabled("provider retry is disabled")

        def factory(
            value: ProviderConnectionConfiguration = provider,
        ) -> MetadataProvider:
            if value.adapter == "google_books":
                api_key = (
                    credentials.get("provider:" + value.name)
                    if credentials is not None
                    else (
                        None
                        if value.secret_file is None
                        else read_secret(value.secret_file)
                    )
                )
                return GoogleBooksMetadataProvider(
                    value.name,
                    google_books_client(configuration.limits.timeout_seconds),
                    configuration.limits.provider_response_bytes,
                    configuration.limits.timeout_seconds,
                    api_key=api_key,
                    pacer=google_books_pacer,
                )
            if value.adapter == "hardcover" and (
                value.secret_file is not None or credentials is not None
            ):
                return HardcoverMetadataProvider(
                    value.name,
                    (
                        hardcover_client(
                            _required_secret_file(value.secret_file),
                            configuration.limits.timeout_seconds,
                        )
                        if credentials is None
                        else hardcover_client_token(
                            credentials["provider:" + value.name],
                            configuration.limits.timeout_seconds,
                        )
                    ),
                    configuration.limits.provider_response_bytes,
                    configuration.limits.timeout_seconds,
                    pacer=hardcover_pacer,
                )
            if value.adapter == "open_library" and value.contact_email is not None:
                return OpenLibraryMetadataProvider(
                    value.name,
                    open_library_client(
                        value.contact_email, configuration.limits.timeout_seconds
                    ),
                    configuration.limits.provider_response_bytes,
                    configuration.limits.timeout_seconds,
                    pacer=open_library_pacer,
                )
            raise ProviderRetrySuccessorError("provider retry configuration changed")

        return ConfiguredProviderRetry(
            ProviderBinding(name, True, factory, adapter), adapter, generation
        )

    pipeline_holder: dict[str, ReviewPreparationPipeline] = {}

    def retry_observers(
        connection: TargetConnectionConfiguration,
        library: LibraryConfiguration,
        mount: MediaMountConfiguration,
    ) -> tuple[TargetReader, EpubInspector]:
        """Build one successor observation's adapters from validated selections."""
        artifacts = CoverArtifactStore(artifact_store_path, create=False)
        target = AudiobookshelfItemReader(
            client_factory=lambda: (
                target_client(
                    connection.endpoint,
                    _required_secret_file(connection.secret_file),
                    configuration.limits.timeout_seconds,
                )
                if credentials is None
                else target_client_token(
                    connection.endpoint,
                    credentials["target:" + connection.name],
                    configuration.limits.timeout_seconds,
                )
            ),
            endpoint=connection.endpoint,
            library_id=library.library_id,
            service_path=mount.service_path,
            maximum_bytes=configuration.limits.target_response_bytes,
            timeout_seconds=configuration.limits.timeout_seconds,
        )
        return target, _IsolatedEpubInspector(artifacts)

    runner_holder: dict[str, ScopedProviderRecoveryRunner] = {}
    activation = ProviderRecoveryActivation(
        configuration=configuration,
        # The concrete repository carries every member of this contract; the
        # ``publish`` hook alone declares its own protocol as ``object``, which
        # is a variance mismatch in the seam rather than a missing capability.
        repository_factory=cast(
            "Callable[[], ProviderRecoveryRepository]", repository_factory
        ),
        observers=retry_observers,
        provider_resolver=retry_resolver,
        pipeline=lambda: pipeline_holder.get("pipeline"),
        enqueue=lambda jobs, enqueued_at: runner_holder["runner"].enqueue(
            jobs, enqueued_at=enqueued_at
        ),
        clock=lambda: int(time.time()),
    )

    recovery_cancel = threading.Event()
    recovery_runner = ScopedProviderRecoveryRunner(
        store=provider_recovery,
        generation=configuration.provider_recovery_generation,
        retry=activation.retry_member,
        classify_failure=_classify_provider_recovery_failure,
        clock=lambda: int(time.time()),
        cancelled=recovery_cancel.is_set,
        blocked_targets=lambda: frozenset(
            condition.connection
            for condition in pipeline_holder["pipeline"].connection_conditions()
            if condition.state in {"paused", "checking"}
        ),
    )

    runner_holder["runner"] = recovery_runner

    def recover_provider(now: int) -> RecoveryRun:
        try:
            failures = activation.drain_outbox()
            recovered = recovery_runner.run_one(now=now)
            if failures:
                raise ReviewPipelineError(failures[0])
            return recovered
        except ProviderRecoveryStoreError as error:
            raise ReviewPipelineError("recovery") from error

    # The pipeline hands every stage callback back exactly the repository
    # ``repository_factory`` above created, so each of these narrows the seam's
    # ``ReviewPipelineRepository`` to that concrete adapter where it needs a
    # capability the seam does not name.
    def catalogue_repository(create: bool) -> SQLiteCatalogueRepository:
        return open_catalogue(catalogue_path, create=create)

    def generate_report(
        action: PipelineAction,
        claim_next: bool,
        pipeline_repository: ReviewPipelineRepository,
    ) -> str:
        return run_metadata_report_loaded(
            configuration,
            database_path,
            artifact_store_path,
            action.connection,
            action.library,
            action.item_id or "",
            0,
            expected_file_identity=action.file_identity,
            google_books_pacer=google_books_pacer,
            open_library_pacer=open_library_pacer,
            hardcover_pacer=hardcover_pacer,
            review_action=action,
            recover_artifacts=False,
            repository_authority=database_authority,
            pipeline_repository=cast(
                "SQLiteMetadataReportRepository", pipeline_repository
            ),
            review_claim_next=claim_next,
            timing=timing,
            credentials=credentials,
        )

    def drain_outbox(repository: ReviewPipelineRepository) -> None:
        failures = activation.drain_outbox(
            cast("ProviderRecoveryRepository", repository)
        )
        if failures:
            raise ReviewPipelineError(failures[0])

    def create_proposal_set_for(
        report_id: str, pipeline_repository: ReviewPipelineRepository
    ) -> str:
        return create_proposal_set_loaded(
            configuration,
            database_path,
            artifact_store_path,
            report_id,
            database_authority,
            cast("SQLiteMetadataReportRepository", pipeline_repository),
            credentials,
        )

    def admit_review_case_for(
        proposal_set_id: str, pipeline_repository: ReviewPipelineRepository
    ) -> str:
        return admit_review_case_loaded(
            configuration,
            database_path,
            artifact_store_path,
            proposal_set_id,
            database_authority,
            cast("SQLiteMetadataReportRepository", pipeline_repository),
            credentials,
        )

    def cover_considerations(
        pipeline_repository: ReviewPipelineRepository,
    ) -> CoverConsiderationService:
        return _compose_cover_considerations(
            cast("SQLiteMetadataReportRepository", pipeline_repository),
            artifact_store_path,
            # Callers reject an absent review configuration before composing.
            cast("ReviewWebConfiguration", configuration.review),
            reviewer_access,
            provider_recovery=provider_recovery,
        )

    stages = ReviewPipelineStages(
        configuration=configuration,
        catalogue=catalogue_repository,
        catalogue_reader=reader,
        probe_connection=reader.probe_connection,
        connections=frozenset(clients),
        repository_factory=repository_factory,
        observation_repository_factory=observation_repository_factory,
        recover_artifacts=recover_artifacts_once,
        generate_report=generate_report,
        create_proposal_set=create_proposal_set_for,
        admit_review_case=admit_review_case_for,
        cover_considerations=cover_considerations,
        recovery_observer=provider_recovery,
        drain_outbox=drain_outbox,
        discovery_lock=discovery_lock,
        clock=lambda: int(time.time()),
    )
    pipeline = stages.pipeline(timing, first_boot=first_boot)
    pipeline_holder["pipeline"] = pipeline
    provider_recovery.require_generation_compatible(
        generation=configuration.provider_recovery_generation
    )
    activation.drain_outbox()
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


def _open_review_session_store(database_path: Path) -> SQLiteReviewSessionRepository:
    """Open the dedicated session database beside the other private stores.

    Session state deliberately does not share the application database or its
    connection: owner sign-in must not queue behind a library scan's write
    transaction (#912).
    """
    return SQLiteReviewSessionRepository.open(
        _private_root(database_path) / "review-sessions.sqlite"
    )


def reopen_metadata_report(database_path: Path, report_id: str) -> str:
    """Read one exact persisted report without loading operational configuration."""
    repository = open_repository(database_path, create=False)
    try:
        return repository.get(report_id)
    finally:
        repository.close()


def reopen_current_metadata_report(
    database_path: Path, connection: str, library: str, item: str, file_identity: str
) -> str:
    repository = open_repository(database_path, create=False)
    try:
        return repository.current(connection, library, item, file_identity)
    finally:
        repository.close()


def reopen_cover_preview(
    database_path: Path,
    artifact_store_path: Path,
    report_id: str,
    artifact_id: str,
    preview_variant: str,
) -> bytes:
    """Read the one report-bound preview; originals and discovery stay unavailable."""
    repository = open_repository(database_path, create=False)
    try:
        digest = repository.preview_digest(report_id, artifact_id, preview_variant)
        return CoverArtifactStore(
            artifact_store_path, create=False
        ).read_preview_digest(digest)
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


def reopen_proposal_set(database_path: Path, proposal_set_id: str) -> str:
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


def preview_shadow_decision_case(database_path: Path, review_case_id: str) -> str:
    repository = open_shadow_preview_repository(database_path)
    try:
        return preview_shadow_decision(repository, review_case_id).to_json()
    finally:
        repository.close()


def run_catalogue(
    config_path: Path,
    database_path: Path,
    action: str,
    connection: str | None,
    library: str | None,
    library_id: str | None,
    selector: str | None,
    limit: int = 100,
) -> str:
    """Refresh or inspect the one current catalogue through explicit composition."""
    configuration = load_configuration_file(config_path)
    mounts = {item.name: item for item in configuration.mounts}
    clients = {item.name: item for item in configuration.connections}
    reader = AudiobookshelfCatalogueReader(
        client_factory=lambda name: target_client(
            clients[name].endpoint,
            _required_secret_file(clients[name].secret_file),
            configuration.limits.timeout_seconds,
        ),
        mounts=mounts,
        maximum_bytes=configuration.limits.target_response_bytes,
        timeout_seconds=configuration.limits.timeout_seconds,
    )
    repository = open_catalogue(database_path, create=action == "refresh")
    try:
        service = RefreshCurrentCatalogue(reader, repository)
        if action == "refresh":
            return json.dumps(
                {"generation": service.refresh(configuration)}, separators=(",", ":")
            )
        exact = (
            None
            if action == "list"
            else (connection or "", library or "", library_id or "", selector or "")
        )
        entries = service.inspect(
            configuration,
            exact=exact,
            facet=selector if action == "list" else None,
            limit=limit,
        )
        values = [
            {
                "connection": item.connection,
                "library": item.library,
                "library_id": item.library_id,
                "item": item.item_id,
                "title": item.title,
                "authors": list(item.authors),
                "ebook": item.has_ebook,
                "audio": item.has_audio,
                "classification": item.classification,
                "eligible": item.eligible,
            }
            for item in entries
        ]
        if action == "get" and values:
            values[0].update(
                {
                    "updated_at": entries[0].updated_at,
                    "file_identity": entries[0].file_identity,
                    "format": entries[0].format,
                    "service_path": entries[0].service_path,
                    "mount": entries[0].mount,
                }
            )
        return json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    finally:
        repository.close()


def preview_batch(
    config_path: Path,
    catalogue_path: Path,
    manifest_path: Path,
    requested: tuple[tuple[str, str, str, str], ...],
    all_eligible: bool,
) -> str:
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
    provider_adapters = _batch_provider_adapters(configuration, selections, providers)
    raw = batch_manifest(
        snapshot,
        batch_fingerprint(configuration, selections),
        selections,
        providers,
        provider_adapters,
    )
    create_batch_manifest(manifest_path, raw)
    value = cast("_Manifest", parse_manifest(raw))
    return json.dumps(
        {"id": value["id"], "entries": len(selections), **value["call_maxima"]},
        separators=(",", ":"),
    )


def inspect_batch(manifest_path: Path) -> str:
    value = cast("_Manifest", parse_manifest(read_batch_manifest(manifest_path)))
    return json.dumps(
        {"id": value["id"], "entries": len(value["entries"]), **value["call_maxima"]},
        separators=(",", ":"),
    )


def run_batch(
    config_path: Path,
    catalogue_path: Path,
    manifest_path: Path,
    manifest_id: str,
    database_path: Path,
    artifact_store_path: Path,
    emit: Callable[[dict[str, object]], None],
) -> bool:
    """Run the immutable foreground list serially; no resume or hidden state."""
    value = cast("_Manifest", parse_manifest(read_batch_manifest(manifest_path)))
    if value["id"] != manifest_id:
        raise BatchError("batch manifest is unavailable")
    configuration = load_configuration_file(config_path)
    selections = tuple(
        BatchSelection(
            raw["connection"],
            raw["library"],
            raw["library_id"],
            raw["item"],
            raw["file_identity"],
            raw["classification"],
            raw["eligible"],
        )
        for raw in value["entries"]
    )
    if (
        batch_fingerprint(configuration, selections)
        != value["batch_config_fingerprint"]
    ):
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
    if (
        snapshot.generation != value["catalogue_generation"]
        or snapshot.fingerprint != value["catalogue_fingerprint"]
    ):
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
    credentials = _load_batch_credentials(
        configuration, selected_connections, selected_provider_names
    )
    google_books_pacer = (
        GoogleBooksPacer() if "google_books" in actual_adapters else None
    )
    open_library_pacer = (
        OpenLibraryPacer() if "open_library" in actual_adapters else None
    )
    hardcover_pacer = HardcoverPacer() if "hardcover" in actual_adapters else None

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
            google_books_pacer=google_books_pacer,
            open_library_pacer=open_library_pacer,
            hardcover_pacer=hardcover_pacer,
        )
        from mechanical_librarian.domain.metadata_report import MetadataReport

        report = MetadataReport.from_json(serialized)
        return report.report_id, tuple(item.status for item in report.providers)

    return ForegroundBatchRun(
        snapshot=snapshot,
        selections=selections,
        projection=lambda: batch_fingerprint(configuration, selections),
        expected_projection=value["batch_config_fingerprint"],
        current_entry=current_entry,
        report=report,
        emit=emit,
    ).run()


def _batch_provider_names(
    configuration: ApplicationConfiguration, selections: tuple[BatchSelection, ...]
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                name
                for selection in selections
                for library in configuration.libraries
                if library.name == selection.library
                and library.connection == selection.connection
                for name in library.provider_connections
                if next(
                    provider
                    for provider in configuration.providers
                    if provider.name == name
                ).enabled
            }
        )
    )


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
        enabled = "open_library" in _batch_provider_adapters(configuration, selections)
    if enabled and (all_eligible or len(selections) > 5):
        raise BatchError("Open Library batch selection is invalid")


def _load_batch_credentials(
    configuration: ApplicationConfiguration,
    selected_connections: set[str],
    selected_provider_names: set[str],
) -> dict[str, str]:
    values = {
        "target:" + item.name: read_secret(_required_secret_file(item.secret_file))
        for item in configuration.connections
        if item.name in selected_connections
    }
    values.update(
        {
            "provider:" + item.name: read_secret(item.secret_file)
            for item in configuration.providers
            if item.name in selected_provider_names and item.secret_file is not None
        }
    )
    return values


def compose_store_runtime_generation(
    bootstrap: ReviewBootstrap,
    database_path: Path,
    catalogue_path: Path,
    artifact_store_path: Path,
    *,
    repository_authority: object,
    authority_snapshot: tuple[SettingsView, dict[str, str], str] | None = None,
    activation: _StoreRuntimeActivation | None = None,
    first_boot: bool = False,
) -> tuple[RuntimeGeneration, object]:
    """Build one dormant, independently owned store runtime generation."""
    private_root = _private_root(database_path)
    repository = open_repository(
        database_path,
        create=True,
        cross_thread=True,
        authority=repository_authority,
    )
    try:
        session_store = _open_review_session_store(database_path)
    except Exception:
        repository.close()
        raise
    try:
        settings_store = repository.owner_settings_store()
        if authority_snapshot is None:
            settings_view, stored_credentials, encoded_verifier = (
                settings_store.activate_runtime()
            )
        else:
            settings_view, stored_credentials, encoded_verifier = authority_snapshot
        credentials = {
            name.replace("connection.", "target:")
            .replace("provider.", "provider:")
            .removesuffix(".key"): value
            for name, value in stored_credentials.items()
        }
        configuration = store_runtime_configuration(settings_view, bootstrap)
        review = configuration.review
        if review is None:
            raise ConfigurationError("review configuration is unavailable")
        from mechanical_librarian.adapters.operational_evidence import (
            LocalTimingStore,
            SupportBundle,
        )
        from mechanical_librarian.application.operations import (
            StageTimer,
            support_preparation_incidents,
        )

        timing_store = LocalTimingStore(
            private_root / "stage-timings.json",
            enabled=review.diagnostic_timings_enabled,
        )
        timing = StageTimer(timing_store)
        provider_recovery = SQLiteProviderRecoveryStore.open(
            private_root / "provider-recovery.sqlite"
        )
        reviewer_access = _compose_reviewer_access(
            repository,
            review,
            None if activation is None else activation._codex_auth,
        )
        first_boot = first_boot or _store_first_preparation(
            "store",
            tuple(settings_view.document.pending_restart),
            repository.has_first_boot_preparation(),
        )
        preparation = create_review_pipeline(
            configuration,
            database_path,
            catalogue_path,
            artifact_store_path,
            reviewer_access,
            repository.authority,
            timing,
            private_root,
            provider_recovery,
            credentials=credentials,
            first_boot=first_boot,
        )
        field_application = (
            compose_continuous_guarded_application(
                configuration,
                repository,
                database_path,
                artifact_store_path,
                preparation,
                private_root,
                provider_recovery,
                credentials=credentials,
            )
            if configuration.libraries
            else None
        )
        activity_projection = None
        close_activity_projection = None
        if field_application is not None:
            activity_projection, close_activity_projection = (
                open_field_effect_activity_projection(
                    configuration,
                    database_path,
                    repository.authority,
                    provider_recovery,
                )
            )
        support_bundle = SupportBundle(
            (
                (
                    "runtime",
                    lambda: {
                        "deployment": "single_container",
                        "schema_state": "exact_current",
                        "timing_records": len(timing_store.list()),
                    },
                ),
                (
                    "operational_incidents",
                    lambda: support_preparation_incidents(
                        repository.operational_snapshot()
                    ),
                ),
                (
                    "browse_failures",
                    lambda: {
                        "exception_classes": repository.browse_unavailable_exception_classes()
                    },
                ),
            )
        )
        cover_considerations = _compose_cover_considerations(
            repository,
            artifact_store_path,
            review,
            reviewer_access,
            provider_recovery,
        )
        try:
            owner_binding = owner_password_binding(encoded_verifier)
            review_application = compose_review_application(
                state_owner=repository,
                session_store=session_store,
                case_store=repository,
                decision_store=repository,
                library_store=repository,
                policy_store=repository,
                verify_password=owner_binding[0],
                session_digest=owner_binding[1],
                clock=lambda: int(time.time()),
                session_lifetime_seconds=review.session_lifetime_seconds,
                graceful_shutdown_seconds=review.graceful_shutdown_seconds,
                close_state_owner=True,
                preparation=preparation,
                policy_libraries=tuple(
                    (
                        library.name,
                        cast("dict[str, object] | None", library.policy_override),
                    )
                    for library in configuration.libraries
                ),
                operational_timings=timing_store,
                operational_support=support_bundle,
                operational_timer=timing,
                field_application=field_application,
                activity_projection=activity_projection,
                close_activity_projection=close_activity_projection,
                cover_considerations=cover_considerations,
                settings=OwnerSettingsOperations(settings_store),
                reviewer_access=reviewer_access,
                settings_activation=None if activation is None else activation.activate,
                first_boot=first_boot,
                acknowledge_first_boot=repository.acknowledge_first_boot_preparation,
            )
        except BaseException:
            if close_activity_projection is not None:
                close_activity_projection()
            raise

        def close_generation() -> None:
            try:
                review_application.stop()
            finally:
                session_store.close()

        def admit_generation() -> None:
            reviewer_access.activate_codex_lease()
            review_application.admit()

        return (
            RuntimeGeneration(
                create_review_app(review_application, review),
                start_dormant=review_application.start_dormant,
                stop_admission=review_application.stop_admission,
                resume_admission=review_application.resume_admission,
                admit=admit_generation,
                begin_drain=review_application.begin_drain,
                close=close_generation,
                shutdown_seconds=review.graceful_shutdown_seconds,
            ),
            review,
        )
    except Exception:
        session_store.close()
        repository.close()
        raise


def _compose_setup_runtime_generation(
    bootstrap: ReviewBootstrap,
    database_path: Path,
    catalogue_path: Path,
    artifact_store_path: Path,
    repository: Any,
    session_store: SQLiteReviewSessionRepository,
    *,
    initialize: bool,
) -> tuple[RuntimeGeneration, _StoreRuntimeActivation]:
    """Build the bounded first-owner target for the ordinary application."""
    verifier = ""
    state = repository.setup_state_store()
    if initialize:
        state.initialize()

    def verify_established_owner(password: str) -> bool:
        return cast(
            "bool",
            state.use_owner_verifier(
                lambda encoded: owner_password_verifier(encoded)(password)
            ),
        )

    def established_owner_session_digest(token: str) -> str:
        return cast(
            "str",
            state.use_owner_verifier(
                lambda encoded: owner_password_binding(encoded)[1](token)
            ),
        )

    sessions = OwnerSessionService(
        session_store,
        verify_established_owner,
        established_owner_session_digest,
        lambda: int(time.time()),
        OWNER_SESSION_LIFETIME_DEFAULT,
    )
    setup_probe = AudiobookshelfSetupProbe(
        lambda endpoint, key: target_client_token(endpoint, key, 30)
    )
    connection = SetupConnectionOperation(
        SetupConnectionVerification(setup_probe), state, verifier
    )

    def discover_setup_libraries(
        document: SetupStateDocument,
    ) -> tuple[SetupLibraryCandidate, ...]:
        if document.connection is None:
            raise ConfigurationError("setup connection is unavailable")
        endpoint = document.connection.endpoint
        return cast(
            "tuple[SetupLibraryCandidate, ...]",
            state.use_secret(
                f"connection.{document.connection.name}.key",
                verifier,
                lambda key: setup_probe.discover(endpoint, key),
            ),
        )

    journey = SetupJourneyOperation(
        state,
        verifier,
        discover_setup_libraries,
        lambda path: _reachable_setup_mount_names(bootstrap, path),
    )
    activation = _StoreRuntimeActivation(
        bootstrap,
        database_path,
        catalogue_path,
        artifact_store_path,
        repository.owner_settings_store(),
        repository.authority,
    )

    def activate_setup(expected_revision: int) -> None:
        journey.validate_kick_off(expected_revision)
        snapshot = state.activation_snapshot(expected_revision)
        activation.activate_setup(
            snapshot,
            lambda: journey.kick_off(expected_revision),
        )

    reviewer_access = _compose_reviewer_access(
        repository,
        ReviewWebConfiguration(
            canonical_origin=bootstrap.canonical_origin,
            owner_password_hash_file="/data/.owner-verifier-store",
            cover_reviewer_profile_file=bootstrap.cover_reviewer_profile_file,
        ),
        activation._codex_auth,
    )
    setup_app = create_setup_app(
        sessions,
        state,
        verifier,
        connection=connection,
        journey=journey,
        reviewer_access=reviewer_access,
        canonical_origin=bootstrap.canonical_origin,
        activate=activate_setup,
    )
    return (
        RuntimeGeneration(
            setup_app,
            admit=reviewer_access.activate_codex_lease,
            close=reviewer_access.close,
            shutdown_seconds=300,
        ),
        activation,
    )


def _reachable_setup_mount_names(
    bootstrap: ReviewBootstrap, service_path: str
) -> tuple[str, ...]:
    """Check only each exact deployment-owned root; never enumerate or read media."""
    names: list[str] = []
    for mount in bootstrap.mounts:
        if mount.service_path != service_path:
            continue
        root = Path(mount.container_path)
        try:
            information = root.stat()
        except OSError:
            continue
        if stat.S_ISDIR(information.st_mode) and os.access(root, os.R_OK | os.X_OK):
            names.append(mount.name)
    return tuple(sorted(names))


def _serve_runtime_dispatcher(
    dispatcher: MutableRuntimeDispatcher,
    host: str,
    port: int,
    certificate_file: str,
    private_key_file: str,
    *,
    after_start: Callable[[], None] | None = None,
) -> None:
    """Bind one mutable generation dispatcher to one outer HTTPS server."""
    config = uvicorn.Config(
        dispatcher,
        host=host,
        port=port,
        proxy_headers=False,
        server_header=False,
        date_header=False,
        access_log=False,
        http=BoundedReviewH11Protocol,
        timeout_graceful_shutdown=dispatcher.current().shutdown_seconds,
        limit_concurrency=MAX_REVIEW_CONNECTIONS,
        backlog=MAX_REVIEW_CONNECTIONS,
        h11_max_incomplete_event_size=16_384,
        ssl_certfile=certificate_file,
        ssl_keyfile=private_key_file,
        ssl_keyfile_password=REJECT_ENCRYPTED_PRIVATE_KEY_PASSWORD,
        ssl_ciphers=REVIEW_TLS_CIPHERS,
    )
    server = uvicorn.Server(config)
    dispatcher.bind_outer_server(
        lambda: setattr(server, "should_exit", True),
        lambda seconds: setattr(config, "timeout_graceful_shutdown", seconds),
    )
    dispatcher.start()
    if after_start is not None:
        after_start()
    server.run()


def run_review_server(
    config_path: Path,
    database_path: Path,
    catalogue_path: Path,
    artifact_store_path: Path,
    host: str,
    port: int,
    *,
    first_boot_mode: FirstBootMode | None = None,
) -> None:
    bootstrap = None
    try:
        configuration = load_configuration_file(config_path)
        authority = "file"
        if configuration.review is None:
            raise ReviewCaseError("configuration")
        review = configuration.review
        tls_certificate_file = review.tls_certificate_file
        tls_private_key_file = review.tls_private_key_file
    except FileNotFoundError:
        configuration = None
        authority = "store"
        bootstrap = load_review_bootstrap(Path("/run/secrets/review-bootstrap.json"))
        review = None
        tls_certificate_file = bootstrap.tls_certificate_file
        tls_private_key_file = bootstrap.tls_private_key_file
    if tls_certificate_file is None or tls_private_key_file is None:
        raise ConfigurationError("review TLS configuration is unavailable")
    with open_review_tls_identity(tls_certificate_file, tls_private_key_file) as tls:
        if authority == "store":
            assert bootstrap is not None
            control = open_repository(database_path, create=True, cross_thread=True)
            setup_session_store: SQLiteReviewSessionRepository | None = None
            try:
                if first_boot_mode in {
                    FirstBootMode.INITIALIZE_SETUP,
                    FirstBootMode.RESUME_SETUP,
                }:
                    setup_session_store = _open_review_session_store(database_path)
                    runtime, activation = _compose_setup_runtime_generation(
                        bootstrap,
                        database_path,
                        catalogue_path,
                        artifact_store_path,
                        control,
                        setup_session_store,
                        initialize=first_boot_mode is FirstBootMode.INITIALIZE_SETUP,
                    )
                    acknowledge = None
                else:
                    activation = _StoreRuntimeActivation(
                        bootstrap,
                        database_path,
                        catalogue_path,
                        artifact_store_path,
                        control.owner_settings_store(),
                        control.authority,
                    )
                    runtime, _ = compose_store_runtime_generation(
                        bootstrap,
                        database_path,
                        catalogue_path,
                        artifact_store_path,
                        repository_authority=control.authority,
                        activation=activation,
                    )
                    acknowledge = activation.acknowledge_initial_runtime
                dispatcher = MutableRuntimeDispatcher(runtime)
                activation.bind(dispatcher)
                try:
                    _serve_runtime_dispatcher(
                        dispatcher,
                        host,
                        port,
                        tls.certificate_file,
                        tls.private_key_file,
                        after_start=acknowledge,
                    )
                except BaseException:
                    runtime.close()
                    raise
            finally:
                activation.close()
                if setup_session_store is not None:
                    setup_session_store.close()
                control.close()
            return
        CoverArtifactStore(artifact_store_path, create=True)
        open_catalogue(catalogue_path, create=True).close()
        private_root = _private_root(database_path)
        repository = open_repository(database_path, create=True, cross_thread=True)
        try:
            session_store = _open_review_session_store(database_path)
        except Exception:
            repository.close()
            raise
        try:
            assert configuration is not None and review is not None
            credentials = _load_batch_credentials(
                configuration,
                {item.name for item in configuration.connections},
                {item.name for item in configuration.providers},
            )
            projection = file_settings_projection(configuration, credentials)
            settings = OwnerSettingsOperations(
                FileOwnerSettingsRepository(projection.document, projection.secrets)
            )
            password_binding = load_owner_password_binding(
                Path(review.owner_password_hash_file)
            )
            assert configuration is not None and review is not None
            logging.getLogger(__name__).info(
                "owner settings authority selected", extra={"authority": authority}
            )
            from mechanical_librarian.adapters.operational_evidence import (
                LocalTimingStore,
                SupportBundle,
            )

            timing_store = LocalTimingStore(
                private_root / "stage-timings.json",
                enabled=review.diagnostic_timings_enabled,
            )
            from mechanical_librarian.application.operations import (
                StageTimer,
                support_preparation_incidents,
            )

            timing = StageTimer(timing_store)
            provider_recovery = SQLiteProviderRecoveryStore.open(
                private_root / "provider-recovery.sqlite"
            )
            reviewer_access = _compose_reviewer_access(repository, review)
            first_boot = _store_first_preparation(
                authority,
                (),
                repository.has_first_boot_preparation(),
            )
            preparation = create_review_pipeline(
                configuration,
                database_path,
                catalogue_path,
                artifact_store_path,
                reviewer_access,
                repository.authority,
                timing,
                private_root,
                provider_recovery,
                credentials=credentials,
                first_boot=first_boot,
            )
            field_application = (
                compose_continuous_guarded_application(
                    configuration,
                    repository,
                    database_path,
                    artifact_store_path,
                    preparation,
                    private_root,
                    provider_recovery,
                    credentials=credentials,
                )
                if getattr(configuration, "libraries", ())
                else None
            )
            activity_projection = None
            close_activity_projection = None
            if field_application is not None:
                activity_projection, close_activity_projection = (
                    open_field_effect_activity_projection(
                        configuration,
                        database_path,
                        repository.authority,
                        provider_recovery,
                    )
                )
            support_bundle = SupportBundle(
                (
                    (
                        "runtime",
                        lambda: {
                            "deployment": "single_container",
                            "schema_state": "exact_current",
                            "timing_records": len(timing_store.list()),
                        },
                    ),
                    (
                        "operational_incidents",
                        lambda: support_preparation_incidents(
                            repository.operational_snapshot()
                        ),
                    ),
                    (
                        "browse_failures",
                        lambda: {
                            "exception_classes": repository.browse_unavailable_exception_classes()
                        },
                    ),
                )
            )
            cover_considerations = _compose_cover_considerations(
                repository,
                artifact_store_path,
                review,
                reviewer_access,
                provider_recovery,
            )
            try:
                review_application = compose_review_application(
                    state_owner=repository,
                    session_store=session_store,
                    case_store=repository,
                    decision_store=repository,
                    library_store=repository,
                    policy_store=repository,
                    verify_password=password_binding[0],
                    session_digest=password_binding[1],
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
                    activity_projection=activity_projection,
                    close_activity_projection=close_activity_projection,
                    cover_considerations=cover_considerations,
                    settings=settings,
                    reviewer_access=reviewer_access,
                    first_boot=first_boot,
                )
            except BaseException:
                if close_activity_projection is not None:
                    close_activity_projection()
                raise
            app = create_review_app(review_application, review)

            def close_generation() -> None:
                try:
                    review_application.stop()
                finally:
                    session_store.close()

            runtime = RuntimeGeneration(
                app,
                start_dormant=review_application.start_dormant,
                stop_admission=review_application.stop_admission,
                resume_admission=review_application.resume_admission,
                admit=review_application.admit,
                begin_drain=review_application.begin_drain,
                close=close_generation,
                shutdown_seconds=review.graceful_shutdown_seconds,
            )
            dispatcher = MutableRuntimeDispatcher(runtime)
            dispatcher.start()
            uvicorn.run(
                dispatcher,
                host=host,
                port=port,
                proxy_headers=False,
                server_header=False,
                date_header=False,
                access_log=False,
                http=BoundedReviewH11Protocol,
                # The owner's configured shutdown budget, applied to draining
                # connections as well as to the background lanes it already
                # governed. A live-update stream is a long HTTP response, and
                # the server waits for in-flight responses before it runs the
                # lifespan shutdown that stops those lanes -- so without a bound
                # here one open browser tab would hold the process open past the
                # point the owner asked it to stop (#641).
                timeout_graceful_shutdown=review.graceful_shutdown_seconds,
                limit_concurrency=MAX_REVIEW_CONNECTIONS,
                backlog=MAX_REVIEW_CONNECTIONS,
                h11_max_incomplete_event_size=16_384,
                ssl_certfile=tls.certificate_file,
                ssl_keyfile=tls.private_key_file,
                ssl_keyfile_password=REJECT_ENCRYPTED_PRIVATE_KEY_PASSWORD,
                ssl_ciphers=REVIEW_TLS_CIPHERS,
            )
        except Exception:
            session_store.close()
            repository.close()
            raise


def _store_first_preparation(
    authority: str,
    pending_restart: tuple[str, ...],
    marked_preparation: bool,
) -> bool:
    """Keep the setup-started presentation across normal-process restarts."""
    return authority == "store" and (
        "setup_complete" in pending_restart or marked_preparation
    )


class _UnavailableCodexAuth:
    def activate_lease(self) -> None:
        return None

    def _state(self) -> CodexDeviceAuthState:
        return CodexDeviceAuthState("failed", failure="permission_or_profile")

    start = status = cancel = clear = current_credential_status = _state
    recheck_credential_status = _state

    def passive_status(self) -> CodexDeviceAuthState:
        return CodexDeviceAuthState("ready")

    def close(self) -> None:
        return None


class _LazyCodexAuth:
    """Do not touch the Codex profile or staging tree until Codex is requested."""

    def __init__(
        self,
        factory: Callable[
            [Callable[[], bool], Callable[[], AbstractContextManager[None]]],
            CodexDeviceAuthManager,
        ],
        coordinator: _CodexAuthLeaseCoordinator,
    ) -> None:
        self._factory = factory
        self._coordinator = coordinator
        self._lock = Lock()
        self._manager: CodexDeviceAuthManager | None = None
        self._lease: int | None = None
        self._retired = False

    def activate_lease(self) -> None:
        with self._lock:
            if self._retired:
                raise CodexDeviceAuthError("lease")
        self._lease = self._coordinator.activate(self)

    def _lease_current(self) -> bool:
        lease = self._lease
        return lease is not None and self._coordinator.current(self, lease)

    def bind(self, loader: Callable[[], tuple[str, bytes]]) -> tuple[str, bytes]:
        """Capture immutable review identity under the current short lease."""
        if not self._lease_current():
            self.activate_lease()
        lease = cast("int", self._lease)
        with self._coordinator.guard(self, lease):
            return loader()

    @contextmanager
    def review_admission(self) -> Iterator[None]:
        """Revalidate the captured generation after taking global activity."""
        lease = cast("int", self._lease)
        with self._coordinator.guard(self, lease):
            yield

    def _get(self) -> CodexDeviceAuthManager:
        if not self._lease_current():
            self.activate_lease()
        with self._lock:
            if self._manager is None:
                self._manager = self._factory(
                    self._lease_current,
                    lambda: self._coordinator.guard(self, cast("int", self._lease)),
                )
            return self._manager

    def _state(self, operation: str) -> CodexDeviceAuthState:
        try:
            return cast("CodexDeviceAuthState", getattr(self._get(), operation)())
        except (
            OSError,
            ValueError,
            CodexDeviceAuthError,
            ContainedReviewerProcessError,
        ):
            return CodexDeviceAuthState("failed", failure="permission_or_profile")

    def start(self) -> CodexDeviceAuthState:
        return self._state("start")

    def status(self) -> CodexDeviceAuthState:
        return self._state("status")

    def passive_status(self) -> CodexDeviceAuthState:
        """Return an existing flow without constructing Codex authority."""
        with self._lock:
            manager = self._manager
        return CodexDeviceAuthState("ready") if manager is None else manager.status()

    def cancel(self) -> CodexDeviceAuthState:
        return self._state("cancel")

    def clear(self) -> CodexDeviceAuthState:
        return self._get().clear()

    def current_credential_status(self) -> CodexDeviceAuthState:
        return self._state("current_credential_status")

    def recheck_credential_status(self) -> CodexDeviceAuthState:
        return self._state("recheck_credential_status")

    def close(self) -> None:
        self._mark_retired()
        self._drain()
        self._coordinator.release(self)

    def _mark_retired(self) -> None:
        with self._lock:
            self._retired = True

    def _drain(self) -> None:
        with self._lock:
            manager = self._manager
        if manager is not None:
            manager.close()


class _CodexAuthLeaseCoordinator:
    """Serialize one process-lifetime owner of a configured auth store."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._transition = Lock()
        self._activity = Lock()
        self._owner: _LazyCodexAuth | None = None
        self._lease = 0
        self._quarantined = False

    def activate(self, owner: _LazyCodexAuth) -> int:
        with self._transition:
            with self._lock:
                if self._quarantined:
                    raise CodexDeviceAuthError("process")
                if self._owner is owner:
                    return self._lease
                predecessor = self._owner
                if predecessor is not None:
                    predecessor._mark_retired()
            if predecessor is not None:
                try:
                    predecessor._drain()
                except Exception:
                    with self._lock:
                        if self._owner is predecessor:
                            self._owner = None
                            self._lease += 1
                    raise
            # A device flow can be pre-cancelled without this gate. A review
            # cannot, so handoff waits for its complete broker tree to reap
            # before changing lease authority.
            with self._activity:
                with self._lock:
                    if self._quarantined:
                        raise CodexDeviceAuthError("process")
                    if self._owner not in {None, predecessor}:
                        raise CodexDeviceAuthError("lease")
                    self._lease += 1
                    self._owner = owner
                    return self._lease

    def current(self, owner: _LazyCodexAuth, lease: int) -> bool:
        with self._lock:
            return (
                not self._quarantined and self._owner is owner and self._lease == lease
            )

    @contextmanager
    def activity(self) -> Iterator[None]:
        """Admit exactly one Codex auth, recheck, or review process tree."""
        with self._activity:
            with self._lock:
                if self._quarantined:
                    raise CodexDeviceAuthError("process")
            yield

    @contextmanager
    def guard(self, owner: _LazyCodexAuth, lease: int) -> Iterator[None]:
        with self._lock:
            if self._quarantined or self._owner is not owner or self._lease != lease:
                raise CodexDeviceAuthError("lease")
            yield

    def quarantine(self) -> None:
        """Permanently reject Codex admission after an unproved process teardown."""
        with self._lock:
            if not self._quarantined:
                self._quarantined = True
                self._lease += 1

    def release(self, owner: _LazyCodexAuth) -> None:
        with self._transition, self._activity, self._lock:
            if self._owner is owner:
                self._owner = None
                self._lease += 1

    def close(self) -> None:
        with self._transition:
            with self._lock:
                owner = self._owner
                if owner is not None:
                    owner._mark_retired()
            if owner is not None:
                owner._drain()
            with self._activity, self._lock:
                if self._owner is owner:
                    self._owner = None
                    self._lease += 1


def _compose_reviewer_access(
    repository: SQLiteMetadataReportRepository,
    review: ReviewWebConfiguration,
    coordinator: _CodexAuthLeaseCoordinator | None = None,
) -> ReviewerAccessOperations:
    codex_auth: CodexAuth
    codex_reviewer = run_cover_packet_reviewer
    profile_file = review.cover_reviewer_profile_file
    if profile_file is None:
        codex_auth = _UnavailableCodexAuth()

        def codex_host() -> ReviewerHostStatus:
            return ReviewerHostStatus(False, "host_evidence_unavailable", None)

        def codex_identity() -> tuple[str, bytes]:
            raise RuntimeError("codex profile unavailable")

    else:
        active_coordinator = coordinator or _CodexAuthLeaseCoordinator()

        def load_profile() -> tuple[bytes, ContainedReviewerProfile]:
            payload = read_contained_reviewer_profile(Path(profile_file))
            return payload, load_contained_reviewer_profile_bytes(payload)

        def manager(
            lease_current: Callable[[], bool],
            lease_guard: Callable[[], AbstractContextManager[None]],
        ) -> CodexDeviceAuthManager:
            _payload, profile = load_profile()
            return CodexDeviceAuthManager(
                profile.auth_store_path,
                lambda staging, action: prepare_contained_device_auth_command(
                    profile, staging, action
                ),
                lease_current=lease_current,
                lease_guard=lease_guard,
                activity_guard=active_coordinator.activity,
                quarantine_activity=active_coordinator.quarantine,
            )

        lazy_codex_auth = _LazyCodexAuth(manager, active_coordinator)
        codex_auth = lazy_codex_auth
        codex_host = reviewer_host_status

        def codex_identity() -> tuple[str, bytes]:
            payload, profile = load_profile()
            return profile.identity.model, payload

        def codex_reviewer(
            profile_payload: bytes,
            instruction_payload: bytes,
            packet_payload: bytes,
            schema_payload: bytes,
            image_payloads: tuple[tuple[str, str, bytes], ...],
        ) -> str:
            with active_coordinator.activity():
                with lazy_codex_auth.review_admission():
                    pass
                return run_cover_packet_reviewer(
                    profile_payload,
                    instruction_payload,
                    packet_payload,
                    schema_payload,
                    image_payloads,
                )

        def codex_binding() -> tuple[
            str,
            bytes,
            Callable[
                [bytes, bytes, bytes, bytes, tuple[tuple[str, str, bytes], ...]],
                str,
            ],
        ]:
            model, payload = lazy_codex_auth.bind(codex_identity)
            return model, payload, codex_reviewer

    return ReviewerAccessOperations(
        repository.reviewer_access_store(),
        codex_auth=codex_auth,
        codex_host=codex_host,
        codex_identity=codex_identity,
        codex_binding=(
            codex_binding
            if profile_file is not None
            else lambda: (*codex_identity(), codex_reviewer)
        ),
        openrouter_factory=lambda key, model: OpenRouterReviewer(key, model),
    )


def _compose_cover_considerations(
    repository: SQLiteMetadataReportRepository,
    artifact_store_path: Path,
    review: ReviewWebConfiguration,
    reviewer_access: ReviewerAccessOperations,
    provider_recovery: SQLiteProviderRecoveryStore | None = None,
) -> CoverConsiderationService:
    artifacts: CoverArtifactStore | None = None

    def artifact_store() -> CoverArtifactStore:
        nonlocal artifacts
        if artifacts is None:
            artifacts = CoverArtifactStore(artifact_store_path, create=False)
        return artifacts

    policy = CoverPolicy(
        avoid_promotional_text=review.cover_avoid_promotional_text,
        avoid_tie_in_branding=review.cover_avoid_tie_in_branding,
        prefer_minimal_composition=review.cover_prefer_minimal_composition,
    )

    def bind_policy(policy_reference: str, replay_reference: str) -> None:
        repository.bind_current_cover_authority(policy_reference, replay_reference)

    service = CoverConsiderationService(
        repository,
        read_preview=lambda artifact_id, variant: artifact_store().read_preview_digest(
            repository.preview_digest_for_artifact(artifact_id, variant)
        ),
        read_preview_digest=lambda digest: artifact_store().read_preview_digest(digest),
        clock=lambda: int(time.time()),
        policy=policy,
        reviewer_dispatch=reviewer_access.dispatch_cover_observation,
        reviewer_identity=reviewer_access.current_cover_observation_identity,
        reviewer_revision_current=reviewer_access.revision_is_current,
        bind_policy=bind_policy,
        provider_fetching=(
            None
            if provider_recovery is None
            else lambda report: provider_recovery.cover_fetching(
                target_connection=report.target.connection,
                library=report.target.library,
                item=report.target.item,
                source_report_id=report.report_id,
            )
        ),
    )
    service.update_policy(policy)
    return service


class _OfflineSessionStore:
    """Fail closed for offline compositions that never serve a session."""

    def _unavailable(self) -> MetadataReportError:
        return MetadataReportError("review sessions are unavailable offline")

    def begin_login(self, now: int, window_seconds: int, maximum_failures: int) -> bool:
        raise self._unavailable()

    def create_review_session(
        self, session_digest: str, csrf_digest: str, created_at: int, expires_at: int
    ) -> None:
        raise self._unavailable()

    def authenticate_review_session(
        self, session_digest: str, now: int
    ) -> tuple[str, str, int]:
        raise self._unavailable()

    def rotate_review_session_csrf(
        self, session_digest: str, csrf_digest: str, now: int
    ) -> str:
        raise self._unavailable()

    def revoke_review_session(
        self, session_digest: str, csrf_digest: str, now: int
    ) -> str:
        raise self._unavailable()


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
    reviewer_access: ReviewerAccessOperations | None = None
    try:
        reviewer_access = _compose_reviewer_access(repository, configuration.review)
        considerations = _compose_cover_considerations(
            repository, artifact_store_path, configuration.review, reviewer_access
        )
        application = compose_review_application(
            state_owner=repository,
            session_store=_OfflineSessionStore(),
            case_store=repository,
            decision_store=repository,
            library_store=repository,
            policy_store=repository,
            verify_password=lambda _: False,
            session_digest=lambda _: "",
            clock=lambda: int(time.time()),
            session_lifetime_seconds=1,
            graceful_shutdown_seconds=1,
            close_state_owner=False,
            cover_considerations=considerations,
            reviewer_access=reviewer_access,
        )
        if action == "history":
            return application.history(review_case_id)
        if action == "show":
            return application.prepare_cover_consideration(review_case_id)
        detail = application.case_detail(review_case_id).to_data()
        field = next(
            # ``CaseDetailView.to_data`` always projects "fields" from its own
            # ``tuple[dict[str, object], ...]`` attribute.
            value
            for value in cast("list[dict[str, object]]", detail["fields"])
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
            None if action != "select" else {"kind": "cover", "value": artifact_id}
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
        if reviewer_access is not None:
            reviewer_access.close()
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
    config_path: Path,
    database_path: Path,
    catalogue_path: Path,
    artifact_store_path: Path,
) -> None:
    """Run the same one-claim recovery path used by the preparation supervisor."""
    configuration = load_configuration_file(config_path)
    if configuration.review is None:
        raise ConfigurationError("review configuration is unavailable")
    store = SQLiteProviderRecoveryStore.open(
        _private_root(database_path) / "provider-recovery.sqlite"
    )
    repository = open_repository(database_path, create=False)
    reviewer_access: ReviewerAccessOperations | None = None
    supervisor: ReviewPreparationSupervisor | None = None
    try:
        reviewer_access = _compose_reviewer_access(repository, configuration.review)
        supervisor = create_review_pipeline(
            configuration,
            database_path,
            catalogue_path,
            artifact_store_path,
            reviewer_access,
            provider_recovery=store,
        )
        supervisor.recover_provider_once()
    finally:
        if supervisor is not None:
            supervisor.stop()
        if reviewer_access is not None:
            reviewer_access.close()
        repository.close()


def run_policy_preview(
    config_path: Path, database_path: Path, document: dict[str, object]
) -> dict[str, object]:
    """Compose the existing read-only policy notebook for one CLI preview."""

    configuration = load_configuration_file(config_path)
    repository = open_shadow_preview_repository(database_path)
    try:
        # ``SQLiteShadowPreviewRepository`` implements the read-only policy
        # preview surface and nothing else, so the preview composes against
        # exactly that contract: no session, review-case, decision, library,
        # browser-state, or policy-mutation operation exists to reach a member
        # it does not have.
        policy_libraries = tuple(
            (library.name, library.policy_override)
            for library in getattr(configuration, "libraries", ())
        )
        application = PolicyPreviewApplication(
            repository,
            tuple(name for name, _ in policy_libraries),
            {name: value for name, value in policy_libraries if value is not None},
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
            "rich_qualification": list(application.rich_qualification(profile)),
            "schema_version": "metadata-policy-preview-v1",
        }
    finally:
        repository.close()


def main(argv: Sequence[str] | None = None) -> int:
    from mechanical_librarian.interfaces.cli import main as runtime_main

    return runtime_main(
        argv,
        review_serve_runner=run_review_server,
        policy_preview_runner=run_policy_preview,
        cover_review_runner=run_cover_review,
        review_activity_runner=run_review_activity,
        provider_recovery_runner=run_provider_recovery_once,
    )


__all__ = [
    "create_proposal_set",
    "inspect_batch",
    "main",
    "preview_batch",
    "reopen_cover_preview",
    "reopen_current_metadata_report",
    "reopen_metadata_report",
    "reopen_proposal_set",
    "admit_review_case",
    "reopen_review_case",
    "preview_shadow_decision_case",
    "run_batch",
    "run_catalogue",
    "run_cover_packet_reviewer",
    "run_metadata_report",
]
