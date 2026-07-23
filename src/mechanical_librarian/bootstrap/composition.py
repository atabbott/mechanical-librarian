"""Credential-bearing composition root for the sole metadata report command."""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from mechanical_librarian.adapters.evidence_extractors.process import run_epub_inspector_child
from mechanical_librarian.adapters.library_services.audiobookshelf_item import AudiobookshelfItemReader
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
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
from mechanical_librarian.domain.metadata_report import ClaimKind, FieldState, LocalClaim, RearClaimProvenance
from mechanical_librarian.infrastructure.configuration import load_configuration_file
from mechanical_librarian.infrastructure.local_media import (
    LocalMediaError,
    open_local_media,
    sha256_descriptor,
)
from mechanical_librarian.infrastructure.security.credentials import hardcover_client, target_client
from mechanical_librarian.interfaces.cli import main as cli_main


class _IsolatedEpubInspector:
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
            claims_by_reference: dict[str, LocalClaim] = {}
            for fact in inspection.facts:
                claim = LocalClaim.create(
                    kind=ClaimKind(fact.field.value),
                    observed=fact.observed_value,
                    canonical=fact.canonical_value,
                    excerpt=fact.supporting_context,
                    applicability=fact.media,
                    basis=fact.basis,
                    checksum=fact.checksum,
                    applicability_basis=fact.classification_basis,
                    state=FieldState(fact.state),
                    role=fact.role,
                    rear_provenance=None if fact.rear_provenance is None else RearClaimProvenance(**fact.rear_provenance.to_data()),
                )
                claims_by_reference[claim.reference] = claim
            return InspectionResult(digest, tuple(sorted(claims_by_reference.values(), key=lambda value: value.reference)))
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


def run_metadata_report(config_path: Path, database_path: Path, connection_name: str, library_name: str, item: str, file_index: int) -> str:
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
        inspector=_IsolatedEpubInspector(),
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


def main(argv: Sequence[str] | None = None) -> int:
    return cli_main(
        argv,
        runner=run_metadata_report,
        reopen_runner=reopen_metadata_report,
        current_reopen_runner=reopen_current_metadata_report,
    )


__all__ = ["main", "reopen_current_metadata_report", "reopen_metadata_report", "run_metadata_report"]
