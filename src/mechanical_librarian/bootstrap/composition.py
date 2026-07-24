"""Credential-bearing composition root for the sole metadata report command."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Sequence
from pathlib import Path

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
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.providers.hardcover_metadata import (
    HardcoverMetadataProvider,
)
from mechanical_librarian.adapters.providers.remote_cover import (
    CoverDeadline,
    RemoteCoverError,
    RemoteCoverTimeout,
    fetch_remote_cover,
)
from mechanical_librarian.application.batch import BatchError, BatchSelection, batch_fingerprint, catalogue_fingerprint as batch_catalogue_fingerprint, manifest as batch_manifest, parse_manifest, select
from mechanical_librarian.application.batch_run import ForegroundBatchRun
from mechanical_librarian.application.catalogue import CatalogueError, RefreshCurrentCatalogue
from mechanical_librarian.application.configuration import (
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
    TargetCoverReader,
    TargetSnapshot,
    UnsupportedMediaError,
)
from mechanical_librarian.domain.cover_evidence import (
    CoverSource,
    CoverSourceKind,
    CoverSourceState,
    cover_source_id,
)
from mechanical_librarian.domain.epub_inspection import (
    EpubInspectionOutcome,
)
from mechanical_librarian.domain.epub_opening import EpubOpeningLimits
from mechanical_librarian.domain.identifiers import Fingerprint, SchemaVersion
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    FieldState,
    LocalClaim,
    ProviderObservation,
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
    hardcover_client,
    hardcover_client_token,
    read_secret,
    target_client,
    target_client_token,
)
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
                    scope=claim_scope(ClaimKind(fact.field.value)),
                    state=FieldState(fact.state),
                    role=fact.role,
                    rear_provenance=None if fact.rear_provenance is None else RearClaimProvenance(**fact.rear_provenance.to_data()),
                    occurrence=occurrence,
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
            return InspectionResult(digest, tuple(claims), covers)
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

    def observe(self, target: TargetSnapshot, providers: tuple[ProviderObservation, ...], local_sources: tuple[CoverSource, ...], descriptors: tuple[tuple[str, str, str | None], ...] = (), aggregate_deadline: float | None = None) -> tuple[CoverSource, ...]:
        now = time.monotonic()
        aggregate = now + 60.0 if aggregate_deadline is None else aggregate_deadline
        deadline = CoverDeadline(aggregate, min(now + 20.0, aggregate))
        if target.cover is None:
            source = CoverSource(cover_source_id(CoverSourceKind.ABS, "absent"), CoverSourceKind.ABS, CoverSourceState.ABSENT)
            return (source, *local_sources, *self._hardcover(providers, descriptors, deadline=deadline))
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
        return (source, *local_sources, *self._hardcover(providers, descriptors, deadline=deadline))

    def _hardcover(self, observations: tuple[ProviderObservation, ...], descriptors: tuple[tuple[str, str, str | None], ...], *, deadline: CoverDeadline) -> tuple[CoverSource, ...]:
        results: list[CoverSource] = []
        for observation in observations:
            configuration = self._providers.get(observation.provider)
            if configuration is None:
                continue
            if not configuration.cover_enabled:
                continue
            values = tuple((source_id, url) for provider, source_id, url in descriptors if provider == observation.provider)
            if not values:
                continue
            if time.monotonic() >= deadline.aggregate_deadline:
                results.extend(CoverSource(source_id, CoverSourceKind.HARDCOVER, CoverSourceState.TIMEOUT, timeout_scope="not_started_due_to_aggregate_deadline") for source_id, _ in values)
                continue
            for source_id, url in values:
                results.append(
                    CoverSource(source_id, CoverSourceKind.HARDCOVER, CoverSourceState.ABSENT)
                    if url is None
                    else self._remote(source_id, url, deadline)
                )
        return tuple(results)

    def _remote(self, source_id: str, url: str, deadline: CoverDeadline) -> CoverSource:
        try:
            attempt = deadline.next_attempt()
            remote = fetch_remote_cover(url, deadline=attempt)
            decoded = decode_cover(
                remote.content,
                deadline=deadline.aggregate_deadline,
            )
            self._artifacts.stage(decoded.artifact, remote.content, decoded.preview)
            return CoverSource(source_id, CoverSourceKind.HARDCOVER, CoverSourceState.RETRIEVED, decoded.artifact)
        except RemoteCoverTimeout:
            return CoverSource(source_id, CoverSourceKind.HARDCOVER, CoverSourceState.TIMEOUT, timeout_scope="attempted")
        except CoverDecodeTimeout:
            return CoverSource(source_id, CoverSourceKind.HARDCOVER, CoverSourceState.TIMEOUT, timeout_scope="attempted")
        except RemoteCoverError as error:
            message = str(error)
            state = CoverSourceState.UNSAFE_REMOTE if "unsafe" in message else CoverSourceState.UNSUPPORTED_FORMAT if "format" in message else CoverSourceState.OVERSIZED if "oversized" in message else CoverSourceState.UNAVAILABLE
            return CoverSource(source_id, CoverSourceKind.HARDCOVER, state)
        except CoverDecodeRejected as error:
            return CoverSource(source_id, CoverSourceKind.HARDCOVER, error.state)
        except ArtifactAuthorityError:
            raise
        except MetadataReportError:
            return CoverSource(source_id, CoverSourceKind.HARDCOVER, CoverSourceState.UNAVAILABLE)

def run_metadata_report(config_path: Path, database_path: Path, artifact_store_path: Path, connection_name: str, library_name: str, item: str, file_index: int, expected_file_identity: str | None = None) -> str:
    # Validate the separately authorized private store before credentials or
    # network clients are constructed.  It must already be operator-created.
    configuration = load_configuration_file(config_path)
    return run_metadata_report_loaded(configuration, database_path, artifact_store_path, connection_name, library_name, item, file_index, expected_file_identity)


def run_metadata_report_loaded(configuration, database_path: Path, artifact_store_path: Path, connection_name: str, library_name: str, item: str, file_index: int, expected_file_identity: str | None = None, credentials: dict[str, str] | None = None, repository_identity=None, artifact_identity=None) -> str:
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
    bindings: list[ProviderBinding] = []
    for provider in provider_configs:
        def factory(value=provider):
            if value.secret_file is None:
                raise MetadataReportError("provider credential unavailable")
            return HardcoverMetadataProvider(
                value.name,
                hardcover_client(value.secret_file, configuration.limits.timeout_seconds) if credentials is None else hardcover_client_token(credentials["provider:" + value.name], configuration.limits.timeout_seconds),
                configuration.limits.provider_response_bytes,
                configuration.limits.timeout_seconds,
            )
        bindings.append(ProviderBinding(provider.name, provider.enabled, factory))
    def repository_factory():
        repository = open_repository(database_path, expected_identity=repository_identity)
        try:
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
    )
    return service.run(item, file_index, expected_file_identity)


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
    providers = _batch_provider_names(configuration, selections)
    raw = batch_manifest(snapshot, batch_fingerprint(configuration, selections), selections, providers)
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
    def current_entry(generation: int, fingerprint: str, key: tuple[str, str, str, str]):
        repository = open_catalogue(catalogue_path, create=False)
        try:
            return repository.current_entry(generation, fingerprint, key)
        finally:
            repository.close()
    def report(entry):
        serialized = run_metadata_report_loaded(configuration, database_path, artifact_store_path, entry.connection, entry.library, entry.item_id, 0, entry.file_identity, credentials, repository_identity, artifact_identity)
        from mechanical_librarian.domain.metadata_report import MetadataReport
        report = MetadataReport.from_json(serialized)
        return report.report_id, tuple(item.status for item in report.providers)
    return ForegroundBatchRun(snapshot=snapshot, selections=selections, projection=lambda: batch_fingerprint(configuration, selections), expected_projection=value["batch_config_fingerprint"], current_entry=current_entry, report=report, emit=emit).run()


def _batch_provider_names(configuration, selections: tuple[BatchSelection, ...]) -> tuple[str, ...]:
    return tuple(sorted({name for selection in selections for library in configuration.libraries if library.name == selection.library and library.connection == selection.connection for name in library.provider_connections if next(provider for provider in configuration.providers if provider.name == name).enabled}))


def _load_batch_credentials(configuration, selected_connections: set[str], selected_provider_names: set[str]) -> dict[str, str]:
    values = {"target:" + item.name: read_secret(item.secret_file) for item in configuration.connections if item.name in selected_connections}
    values.update({"provider:" + item.name: read_secret(item.secret_file) for item in configuration.providers if item.name in selected_provider_names and item.secret_file is not None})
    return values


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
    )


__all__ = ["inspect_batch", "main", "preview_batch", "reopen_cover_preview", "reopen_current_metadata_report", "reopen_metadata_report", "run_batch", "run_catalogue", "run_metadata_report"]
