"""Credential-bearing composition root for the sole metadata report command."""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from mechanical_librarian.adapters.evidence_extractors.process import run_epub_inspector_child
from mechanical_librarian.adapters.evidence_extractors.epub_cover import read_declared_cover
from mechanical_librarian.adapters.cover_artifacts import CoverDecodeRejected, decode_cover
from mechanical_librarian.adapters.library_services.audiobookshelf_item import AudiobookshelfItemReader
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.adapters.providers.hardcover_metadata import HardcoverMetadataProvider
from mechanical_librarian.application.configuration import ConfigurationError, MediaMountConfiguration
from mechanical_librarian.application.metadata_report import (
    GenerateMetadataReport,
    InspectionResult,
    MetadataReportError,
    ProviderBinding,
    UnsupportedMediaError,
)
from mechanical_librarian.domain.epub_inspection import EpubInspection, EpubInspectionOutcome
from mechanical_librarian.domain.epub_opening import EpubOpeningLimits
from mechanical_librarian.domain.identifiers import Fingerprint, SchemaVersion
from mechanical_librarian.domain.metadata_report import ClaimKind, ClaimScope, FieldState, LocalClaim, RearClaimProvenance, claim_scope
from mechanical_librarian.domain.cover_evidence import CoverSource, CoverSourceKind, CoverSourceState
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


def run_metadata_report(config_path: Path, database_path: Path, artifact_store_path: Path, connection_name: str, library_name: str, item: str, file_index: int) -> str:
    # Validate the separately authorized private store before credentials or
    # network clients are constructed.  It must already be operator-created.
    CoverArtifactStore(artifact_store_path, create=False)
    configuration = load_configuration_file(config_path)
    connection, library, mount, provider_configs = configuration.select(connection_name, library_name)
    if library.media_kind != "ebook" or mount is None:
        raise UnsupportedMediaError("unsupported media")
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
    service = GenerateMetadataReport(
        target=target,
        inspector=_IsolatedEpubInspector(CoverArtifactStore(artifact_store_path, create=False)),
        repository_factory=lambda: open_repository(database_path),
        providers=tuple(bindings),
        connection=connection,
        library=library,
        mount=mount,
        limits=configuration.limits,
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


def reopen_cover_preview(database_path: Path, artifact_store_path: Path, report_id: str, artifact_id: str) -> bytes:
    """Read the one report-bound preview; originals and discovery stay unavailable."""
    repository = open_repository(database_path, create=False)
    try:
        digest = repository.preview_digest(report_id, artifact_id)
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
