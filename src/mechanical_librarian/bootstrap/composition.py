"""Credential-bearing composition root for the sole metadata report command."""

from __future__ import annotations

import os
import time
from collections.abc import Sequence
from pathlib import Path

from mechanical_librarian.adapters.evidence_extractors.process import run_epub_inspector_child
from mechanical_librarian.adapters.evidence_extractors.epub_cover import read_declared_cover
from mechanical_librarian.adapters.cover_artifacts import CoverDecodeRejected, decode_cover
from mechanical_librarian.adapters.library_services.audiobookshelf_item import AudiobookshelfItemReader
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.adapters.providers.hardcover_metadata import HardcoverMetadataProvider
from mechanical_librarian.adapters.providers.remote_cover import RemoteCoverError, RemoteCoverTimeout, fetch_remote_cover
from mechanical_librarian.application.configuration import ConfigurationError, MediaMountConfiguration, ProviderConnectionConfiguration
from mechanical_librarian.application.metadata_report import (
    GenerateMetadataReport,
    InspectionResult,
    MetadataReportError,
    ProviderBinding,
    CoverTimeoutError,
    TargetCoverReader,
    TargetSnapshot,
    UnsupportedMediaError,
)
from mechanical_librarian.domain.epub_inspection import EpubInspection, EpubInspectionOutcome
from mechanical_librarian.domain.epub_opening import EpubOpeningLimits
from mechanical_librarian.domain.identifiers import Fingerprint, SchemaVersion
from mechanical_librarian.domain.metadata_report import ClaimKind, ClaimScope, FieldState, LocalClaim, ProviderObservation, ProviderStatus, RearClaimProvenance, claim_scope
from mechanical_librarian.domain.cover_evidence import CoverSource, CoverSourceKind, CoverSourceState, cover_source_id
from mechanical_librarian.infrastructure.configuration import load_configuration_file
from mechanical_librarian.infrastructure.local_media import (
    LocalMediaError,
    open_local_media,
    sha256_descriptor,
)
from mechanical_librarian.infrastructure.security.credentials import hardcover_client, target_client
from mechanical_librarian.interfaces.cli import main as cli_main


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
                raise MetadataReportError("EPUB inspection failed")
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
                    scope=claim_scope(ClaimKind(fact.field.value)),
                    state=FieldState(fact.state),
                    role=fact.role,
                    rear_provenance=None if fact.rear_provenance is None else RearClaimProvenance(**fact.rear_provenance.to_data()),
                    occurrence=occurrence,
                )
                claims.append(claim)
            declared = read_declared_cover(opened.descriptor)
            if declared.state is CoverSourceState.DECLARED:
                try:
                    decoded = decode_cover(declared.data or b"")
                    self._artifacts.stage(decoded.artifact, declared.data or b"", decoded.preview)
                    covers = (CoverSource(declared.source_id, CoverSourceKind.EPUB, CoverSourceState.RETRIEVED, decoded.artifact),)
                except CoverDecodeRejected:
                    covers = (CoverSource(declared.source_id, CoverSourceKind.EPUB, CoverSourceState.DECODE_FAILED),)
            else:
                covers = (CoverSource(declared.source_id, CoverSourceKind.EPUB, declared.state),)
            return InspectionResult(digest, tuple(claims), covers)
        except MetadataReportError:
            raise
        except (LocalMediaError, OSError, ValueError) as error:
            raise MetadataReportError("EPUB inspection failed") from error
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

    def observe(self, target: TargetSnapshot, providers: tuple[ProviderObservation, ...], local_sources: tuple[CoverSource, ...], descriptors: tuple[tuple[str, str], ...] = ()) -> tuple[CoverSource, ...]:
        if target.cover is None:
            source = CoverSource(cover_source_id(CoverSourceKind.ABS, "absent"), CoverSourceKind.ABS, CoverSourceState.ABSENT)
            return (source, *local_sources, *self._hardcover(providers, descriptors, deadline=time.monotonic() + 60.0))
        source_id = cover_source_id(CoverSourceKind.ABS, target.cover.identity)
        deadline = time.monotonic() + 60.0
        try:
            original = self._target.read_cover(target.cover, deadline=deadline)
            if original is None:
                source = CoverSource(source_id, CoverSourceKind.ABS, CoverSourceState.ABSENT)
            else:
                decoded = decode_cover(original)
                self._artifacts.stage(decoded.artifact, original, decoded.preview)
                source = CoverSource(source_id, CoverSourceKind.ABS, CoverSourceState.RETRIEVED, decoded.artifact)
        except CoverTimeoutError:
            source = CoverSource(source_id, CoverSourceKind.ABS, CoverSourceState.TIMEOUT, timeout_scope="attempted")
        except CoverDecodeRejected:
            source = CoverSource(source_id, CoverSourceKind.ABS, CoverSourceState.DECODE_FAILED)
        except MetadataReportError:
            source = CoverSource(source_id, CoverSourceKind.ABS, CoverSourceState.UNAVAILABLE)
        return (source, *local_sources, *self._hardcover(providers, descriptors, deadline=deadline))

    def _hardcover(self, observations: tuple[ProviderObservation, ...], descriptors: tuple[tuple[str, str], ...], *, deadline: float) -> tuple[CoverSource, ...]:
        results: list[CoverSource] = []
        for observation in observations:
            configuration = self._providers.get(observation.provider)
            if configuration is None:
                continue
            if not configuration.cover_enabled:
                results.append(CoverSource(cover_source_id(CoverSourceKind.HARDCOVER, observation.provider), CoverSourceKind.HARDCOVER, CoverSourceState.DISABLED))
                continue
            values = tuple(descriptors)
            if not values:
                continue
            if time.monotonic() >= deadline:
                results.extend(CoverSource(source_id, CoverSourceKind.HARDCOVER, CoverSourceState.TIMEOUT, timeout_scope="not_started_due_to_aggregate_deadline") for source_id, _ in values)
                continue
            for source_id, url in values:
                results.append(self._remote(source_id, url, deadline))
        return tuple(results)

    def _remote(self, source_id: str, url: str, deadline: float) -> CoverSource:
        try:
            remote = fetch_remote_cover(url, deadline=deadline)
            decoded = decode_cover(remote.content)
            self._artifacts.stage(decoded.artifact, remote.content, decoded.preview)
            return CoverSource(source_id, CoverSourceKind.HARDCOVER, CoverSourceState.RETRIEVED, decoded.artifact)
        except RemoteCoverTimeout:
            return CoverSource(source_id, CoverSourceKind.HARDCOVER, CoverSourceState.TIMEOUT, timeout_scope="attempted")
        except RemoteCoverError as error:
            message = str(error)
            state = CoverSourceState.UNSAFE_REMOTE if "unsafe" in message else CoverSourceState.UNSUPPORTED_FORMAT if "format" in message else CoverSourceState.OVERSIZED if "oversized" in message else CoverSourceState.UNAVAILABLE
            return CoverSource(source_id, CoverSourceKind.HARDCOVER, state)
        except CoverDecodeRejected:
            return CoverSource(source_id, CoverSourceKind.HARDCOVER, CoverSourceState.DECODE_FAILED)
        except MetadataReportError:
            return CoverSource(source_id, CoverSourceKind.HARDCOVER, CoverSourceState.UNAVAILABLE)

def run_metadata_report(config_path: Path, database_path: Path, artifact_store_path: Path, connection_name: str, library_name: str, item: str, file_index: int) -> str:
    # Validate the separately authorized private store before credentials or
    # network clients are constructed.  It must already be operator-created.
    configuration = load_configuration_file(config_path)
    connection, library, mount, provider_configs = configuration.select(connection_name, library_name)
    if library.media_kind != "ebook" or mount is None:
        raise UnsupportedMediaError("unsupported media")
    artifacts = CoverArtifactStore(artifact_store_path, create=False)
    target = AudiobookshelfItemReader(
        client_factory=lambda: target_client(connection.endpoint, connection.secret_file, configuration.limits.timeout_seconds),
        endpoint=connection.endpoint,
        library_id=library.library_id,
        service_path=mount.service_path,
        maximum_bytes=configuration.limits.target_response_bytes,
    )
    bindings: list[ProviderBinding] = []
    for provider in provider_configs:
        def factory(value=provider):
            if value.secret_file is None:
                raise MetadataReportError("provider credential unavailable")
            return HardcoverMetadataProvider(
                value.name,
                hardcover_client(value.secret_file, configuration.limits.timeout_seconds),
                configuration.limits.provider_response_bytes,
            )
        bindings.append(ProviderBinding(provider.name, provider.enabled, factory))
    def repository_factory():
        repository = open_repository(database_path)
        try:
            artifacts.recover_unreferenced(repository.referenced_artifact_digests())
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
    )
    return service.run(item, file_index)


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


def main(argv: Sequence[str] | None = None) -> int:
    return cli_main(
        argv,
        runner=run_metadata_report,
        reopen_runner=reopen_metadata_report,
        current_reopen_runner=reopen_current_metadata_report,
        preview_runner=reopen_cover_preview,
    )


__all__ = ["main", "reopen_cover_preview", "reopen_current_metadata_report", "reopen_metadata_report", "run_metadata_report"]
