"""Credential-bearing composition root for the sole metadata report command."""

from __future__ import annotations

import json
import math
import os
import threading
import time
from collections.abc import Sequence
from pathlib import Path

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
from mechanical_librarian.adapters.library_services.audiobookshelf_catalogue import (
    AudiobookshelfCatalogueReader,
)
from mechanical_librarian.adapters.library_services.audiobookshelf_item import (
    AudiobookshelfItemReader,
)
from mechanical_librarian.adapters.persistence.catalogue import open_catalogue
from mechanical_librarian.adapters.persistence.batch_manifest import create as create_batch_manifest, read as read_batch_manifest
from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.adapters.persistence.metadata_reports import (
    open_repository,
    open_shadow_preview_repository,
)
from mechanical_librarian.adapters.providers.hardcover_metadata import (
    HardcoverMetadataProvider,
    HardcoverPacer,
)
from mechanical_librarian.adapters.providers.google_books_metadata import (
    GoogleBooksMetadataProvider,
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
from mechanical_librarian.adapters.reviewers.codex_shadow import (
    CodexShadowReviewer,
    current_process_identity,
    load_qualification_profile,
    process_identity_alive,
    process_identity_matches,
)
from mechanical_librarian.application.batch import BatchError, BatchSelection, batch_fingerprint, catalogue_fingerprint as batch_catalogue_fingerprint, manifest as batch_manifest, parse_manifest, select
from mechanical_librarian.application.batch_run import ForegroundBatchRun
from mechanical_librarian.application.catalogue import CatalogueError, RefreshCurrentCatalogue
from mechanical_librarian.application.configuration import (
    ConfigurationError,
    MediaMountConfiguration,
    ProviderConnectionConfiguration,
)
from mechanical_librarian.application.metadata_report import (
    CoverReadError,
    CoverSourceChangedError,
    CoverTimeoutError,
    CurrentnessError,
    ItemInspectionError,
    ArtifactAuthorityError,
    GenerateMetadataReport,
    InspectionResult,
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
    provider_failure_disposition,
)
from mechanical_librarian.application.shadow_reviews import (
    execute_shadow_review,
    inspect_shadow_review,
    preview_shadow_decision,
    preview_shadow_review,
)
from mechanical_librarian.domain.metadata_report import MetadataReport
from mechanical_librarian.domain.metadata_policy import MetadataPolicyProfile, resolve_policy
from mechanical_librarian.domain.proposal_set import ProposalSet
from mechanical_librarian.domain.review_case import ReviewAdmissionResult
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
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    LocalClaim,
    ProviderObservation,
    ProviderStatus,
    RearClaimProvenance,
    claim_scope,
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
from mechanical_librarian.infrastructure.security.review_tls import (
    REJECT_ENCRYPTED_PRIVATE_KEY_PASSWORD,
    open_review_tls_identity,
)
from mechanical_librarian.infrastructure.security.review_protocol import (
    BoundedReviewH11Protocol,
    MAX_REVIEW_CONNECTIONS,
    REVIEW_TLS_CIPHERS,
)
from mechanical_librarian.interfaces.cli import main as cli_main
from mechanical_librarian.interfaces.web import create_review_app


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
                    rear_provenance=None if fact.rear_provenance is None else RearClaimProvenance(**fact.rear_provenance.to_data()),
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


def _provider_recovery_delay(
    provider_instances: dict[str, object], report: MetadataReport
) -> int:
    transient_providers = {
        provider.provider
        for provider in report.providers
        if any(
            status in {ProviderStatus.RATE_LIMITED, ProviderStatus.UNAVAILABLE}
            for status in (
                provider.status,
                *(lane.status for lane in provider.lanes),
            )
        )
    }
    if not transient_providers:
        return 0
    delays = tuple(
        record.delay_seconds
        for name, provider in provider_instances.items()
        if name in transient_providers
        for record in getattr(provider, "attempt_records", ())
        if record.outcome == "exhausted"
        if type(record.delay_seconds) in {int, float}
        and math.isfinite(record.delay_seconds)
        and record.delay_seconds > 0
    )
    return min(86_400, math.ceil(max(delays, default=0)))


def _provider_statuses(report: MetadataReport) -> tuple[ProviderStatus, ...]:
    return tuple(
        status
        for provider in report.providers
        for status in (
            provider.status,
            *(lane.status for lane in provider.lanes),
        )
    )


class _NonClosingRepository:
    """Let one composed service borrow a pipeline-owned repository connection."""

    def __init__(self, repository) -> None:
        self._repository = repository

    def __getattr__(self, name: str):
        return getattr(self._repository, name)

    def close(self) -> None:
        return None


def run_metadata_report_loaded(configuration, database_path: Path, artifact_store_path: Path, connection_name: str, library_name: str, item: str, file_index: int, expected_file_identity: str | None = None, credentials: dict[str, str] | None = None, repository_identity=None, artifact_identity=None, open_library_pacer: OpenLibraryPacer | None = None, hardcover_pacer: HardcoverPacer | None = None, review_action: PipelineAction | None = None, recover_artifacts: bool = True, repository_authority: object | None = None, pipeline_repository=None, review_claim_next: bool = False) -> str:
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
    provider_instances: dict[str, object] = {}
    for provider in provider_configs:
        def factory(value=provider):
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
            provider_instances[value.name] = instance
            return instance
        bindings.append(
            ProviderBinding(
                provider.name, provider.enabled, factory, provider.adapter
            )
        )
    def repository_factory():
        repository = (
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
            else lambda repository, report: repository.complete_review_extraction_publication(
                review_action,
                report.report_id,
                int(time.time()),
                _provider_recovery_delay(provider_instances, report),
                (
                    None
                    if (disposition := provider_failure_disposition(
                        _provider_statuses(report)
                    )) is None
                    else disposition[0]
                ),
                review_claim_next,
            )
        ),
        require_singular_target=review_action is not None,
    )
    return service.run(item, file_index, expected_file_identity)

def _require_report_current_loaded(configuration, artifacts, report) -> bool:
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
    configuration,
    database_path: Path,
    artifact_store_path: Path,
    report_id: str,
    repository_authority: object | None = None,
    pipeline_repository=None,
) -> str:
    artifacts = CoverArtifactStore(artifact_store_path, create=False)
    repository = (
        _NonClosingRepository(pipeline_repository)
        if pipeline_repository is not None
        else open_repository(
            database_path, create=False, authority=repository_authority
        )
    )

    def current(report) -> bool:
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
    configuration,
    database_path: Path,
    artifact_store_path: Path,
    proposal_set_id: str,
    repository_authority: object | None = None,
    pipeline_repository=None,
) -> str:
    artifacts = CoverArtifactStore(artifact_store_path, create=False)
    repository = (
        _NonClosingRepository(pipeline_repository)
        if pipeline_repository is not None
        else open_repository(
            database_path, create=False, authority=repository_authority
        )
    )

    def current(proposal) -> bool:
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


def _current_resolved_policy(configuration, repository, library: str):
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


def create_review_pipeline(
    configuration,
    database_path: Path,
    catalogue_path: Path,
    artifact_store_path: Path,
    database_authority: object | None = None,
    timing=None,
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

    def repository_factory():
        return open_repository(
            database_path, create=False, authority=database_authority
        )

    def discover(connection: str, library: str):
        with discovery_lock:
            repository = open_catalogue(catalogue_path, create=True)
            try:
                service = RefreshCurrentCatalogue(reader, repository)
                service.refresh_library(configuration, connection, library)
                snapshot = repository.snapshot(
                    batch_catalogue_fingerprint(configuration)
                )
                return snapshot.generation, snapshot.fingerprint, snapshot.entries
            except CatalogueError as error:
                raise ReviewPipelineError("transient") from error
            finally:
                repository.close()

    def extract(
        pipeline_repository, action: PipelineAction, claim_next: bool
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
                pipeline_repository=pipeline_repository,
                review_claim_next=claim_next,
            )
        except (CurrentnessError, TargetNotFoundError) as error:
            raise ReviewPipelineError("stale") from error
        value = MetadataReport.from_json(serialized)
        return value.report_id, True

    def providers(pipeline_repository, report_id: str):
        value = MetadataReport.from_json(pipeline_repository.get(report_id))
        return _provider_statuses(value)

    def propose(pipeline_repository, report_id: str) -> str:
        try:
            serialized = create_proposal_set_loaded(
                configuration,
                database_path,
                artifact_store_path,
                report_id,
                database_authority,
                pipeline_repository,
            )
        except ProposalCurrentnessError as error:
            raise ReviewPipelineError("stale") from error
        return ProposalSet.from_json(serialized).proposal_set_id

    def admit(pipeline_repository, proposal_set_id: str) -> tuple[str, str | None]:
        serialized = admit_review_case_loaded(
            configuration,
            database_path,
            artifact_store_path,
            proposal_set_id,
            database_authority,
            pipeline_repository,
        )
        result = ReviewAdmissionResult.from_json(serialized)
        if result.status == "stale":
            raise ReviewPipelineError("stale")
        return result.status, result.review_case_id

    def project(pipeline_repository, review_case_id: str) -> bool:
        try:
            outcome = pipeline_repository.persist_shadow_outcome(
                review_case_id, int(time.time())
            )
            return "stale" if outcome == "stale" else "complete"
        except Exception as error:
            raise ReviewPipelineError("projection") from error

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
        timing=timing,
    )
    return ReviewPreparationSupervisor(
        pipeline,
        polling_interval_seconds=configuration.review.library_polling_interval_seconds,
        graceful_shutdown_seconds=configuration.review.graceful_shutdown_seconds,
    )


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


def run_reviewer_auth(profile_path: Path, action: str) -> str:
    profile = load_qualification_profile(profile_path)
    return CodexShadowReviewer(profile).auth(action)


def preview_shadow_decision_case(
    database_path: Path, review_case_id: str
) -> str:
    repository = open_shadow_preview_repository(database_path)
    try:
        return preview_shadow_decision(repository, review_case_id).to_json()
    finally:
        repository.close()


def preview_shadow_review_case(
    database_path: Path, review_case_id: str, profile_path: Path
) -> str:
    profile = load_qualification_profile(profile_path)
    repository = open_repository(database_path, create=False)
    try:
        return preview_shadow_review(
            repository, review_case_id, profile.identity
        )
    finally:
        repository.close()


def request_shadow_review(
    database_path: Path, review_case_id: str, profile_path: Path
) -> str:
    profile = load_qualification_profile(profile_path)
    repository = open_repository(database_path, create=False)
    try:
        return execute_shadow_review(
            repository,
            review_case_id,
            profile.identity,
            CodexShadowReviewer(profile),
            claimant=current_process_identity(),
        )
    finally:
        repository.close()


def inspect_shadow_review_request(
    database_path: Path, review_request_id: str, profile_path: Path
) -> str:
    profile = load_qualification_profile(profile_path)
    repository = open_repository(database_path, create=False)
    try:
        return inspect_shadow_review(
            repository, review_request_id, profile.identity
        )
    finally:
        repository.close()


def reevaluate_shadow_review(
    database_path: Path, review_request_id: str, profile_path: Path
) -> str:
    profile = load_qualification_profile(profile_path)
    repository = open_repository(database_path, create=False)
    try:
        return execute_shadow_review(
            repository,
            review_request_id,
            profile.identity,
            CodexShadowReviewer(profile),
            claimant=current_process_identity(),
            reevaluate=True,
            process_alive=process_identity_alive,
            claim_alive=process_identity_matches,
        )
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
    value = parse_manifest(raw)
    return json.dumps({"id": value["id"], "entries": len(selections), **value["call_maxima"]}, separators=(",", ":"))


def inspect_batch(manifest_path: Path) -> str:
    value = parse_manifest(read_batch_manifest(manifest_path))
    return json.dumps({"id": value["id"], "entries": len(value["entries"]), **value["call_maxima"]}, separators=(",", ":"))


def run_batch(config_path: Path, catalogue_path: Path, manifest_path: Path, manifest_id: str, database_path: Path, artifact_store_path: Path, emit) -> bool:
    """Run the immutable foreground list serially; no resume or hidden state."""
    value = parse_manifest(read_batch_manifest(manifest_path))
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
    try: repository_identity = repository.identity
    finally: repository.close()
    selected_connections = {selection.connection for selection in selections}
    selected_provider_names = set(value["providers"])
    credentials = _load_batch_credentials(configuration, selected_connections, selected_provider_names)
    open_library_pacer = (
        OpenLibraryPacer() if "open_library" in actual_adapters else None
    )
    hardcover_pacer = (
        HardcoverPacer() if "hardcover" in actual_adapters else None
    )
    def current_entry(generation: int, fingerprint: str, key: tuple[str, str, str, str]):
        repository = open_catalogue(catalogue_path, create=False)
        try:
            return repository.current_entry(generation, fingerprint, key)
        finally:
            repository.close()
    def report(entry):
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


def _batch_provider_names(configuration, selections: tuple[BatchSelection, ...]) -> tuple[str, ...]:
    return tuple(sorted({name for selection in selections for library in configuration.libraries if library.name == selection.library and library.connection == selection.connection for name in library.provider_connections if next(provider for provider in configuration.providers if provider.name == name).enabled}))


def _batch_provider_adapters(
    configuration,
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
    configuration,
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


def _load_batch_credentials(configuration, selected_connections: set[str], selected_provider_names: set[str]) -> dict[str, str]:
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
        repository = open_repository(database_path, create=False, cross_thread=True)
        try:
            from mechanical_librarian.adapters.operational_evidence import (
                LocalTimingStore, SupportBundle,
            )

            timing_store = LocalTimingStore(
                database_path.parent / "private" / "stage-timings.json",
                enabled=review.diagnostic_timings_enabled,
            )
            from mechanical_librarian.application.operations import StageTimer

            timing = StageTimer(timing_store)
            preparation = create_review_pipeline(
                configuration,
                database_path,
                catalogue_path,
                artifact_store_path,
                repository.authority,
                timing,
            )
            support_bundle = SupportBundle(((
                "runtime",
                lambda: {
                    "deployment": "single_container",
                    "schema_state": "exact_current",
                    "timing_records": len(timing_store.list()),
                },
            ),))
            app = create_review_app(
                repository,
                review,
                close_repository=True,
                review_preparation=preparation,
                policy_libraries=tuple(
                    (library.name, library.policy_override)
                    for library in getattr(configuration, "libraries", ())
                ),
                operational_timings=timing_store,
                operational_support=support_bundle,
                operational_timer=timing,
            )
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


def main(argv: Sequence[str] | None = None) -> int:
    return cli_main(
        argv,
        runner=run_metadata_report,
        reopen_runner=reopen_metadata_report,
        current_reopen_runner=reopen_current_metadata_report,
        preview_runner=reopen_cover_preview,
        catalogue_runner=run_catalogue,
        batch_preview_runner=preview_batch,
        batch_inspect_runner=inspect_batch,
        batch_run_runner=run_batch,
        proposal_create_runner=create_proposal_set,
        proposal_get_runner=reopen_proposal_set,
        review_admit_runner=admit_review_case,
        review_get_runner=reopen_review_case,
        review_serve_runner=run_review_server,
        reviewer_auth_runner=run_reviewer_auth,
        shadow_preview_runner=preview_shadow_review_case,
        shadow_request_runner=request_shadow_review,
        shadow_inspect_runner=inspect_shadow_review_request,
        shadow_reevaluate_runner=reevaluate_shadow_review,
        shadow_decision_runner=preview_shadow_decision_case,
    )


__all__ = [
    "create_proposal_set", "inspect_batch", "main", "preview_batch",
    "reopen_cover_preview", "reopen_current_metadata_report",
    "reopen_metadata_report", "reopen_proposal_set", "admit_review_case", "reopen_review_case",
    "run_reviewer_auth", "preview_shadow_decision_case", "preview_shadow_review_case", "request_shadow_review",
    "inspect_shadow_review_request", "reevaluate_shadow_review", "run_batch",
    "run_catalogue", "run_metadata_report",
]
