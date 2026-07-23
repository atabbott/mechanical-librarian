"""Synchronous report orchestration and its narrow application-owned ports."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from mechanical_librarian.application.configuration import (
    LibraryConfiguration,
    MediaMountConfiguration,
    ReportLimits,
    TargetConnectionConfiguration,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderObservation,
    ProviderStatus,
    TargetFacts,
    normalize_title,
)


class MetadataReportError(RuntimeError):
    """A value-free operational failure at the command boundary."""


class UnsupportedMediaError(MetadataReportError):
    pass


class CurrentnessError(MetadataReportError):
    pass


class CredentialError(MetadataReportError):
    pass


@dataclass(frozen=True, slots=True)
class TargetFile:
    identity: str
    relative_path: str
    format: str


@dataclass(frozen=True, slots=True)
class TargetSnapshot:
    media_kind: str
    title: str
    files: tuple[TargetFile, ...]


@dataclass(frozen=True, slots=True)
class InspectionResult:
    content_digest: str
    claims: tuple[LocalClaim, ...]


class TargetReader(Protocol):
    def read(self, item: str) -> TargetSnapshot: ...


class EpubInspector(Protocol):
    def inspect(self, mount: MediaMountConfiguration, relative_path: str, maximum_bytes: int) -> InspectionResult: ...
    def unchanged(self, mount: MediaMountConfiguration, relative_path: str, expected_digest: str, maximum_bytes: int) -> bool: ...


class MetadataProvider(Protocol):
    def observe(self, local_claims: tuple[LocalClaim, ...]) -> ProviderObservation: ...


class ReportRepository(Protocol):
    def publish(self, report: MetadataReport, currentness: Callable[[], bool]) -> str: ...
    def get(self, report_id: str) -> str: ...
    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ProviderBinding:
    name: str
    enabled: bool
    factory: Callable[[], MetadataProvider]


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
    ) -> None:
        self._target = target
        self._inspector = inspector
        self._repository_factory = repository_factory
        self._providers = providers
        self._connection = connection
        self._library = library
        self._mount = mount
        self._limits = limits

    def run(self, item: str, file_index: int) -> str:
        if self._library.media_kind != "ebook" or self._mount is None:
            raise UnsupportedMediaError("unsupported media")
        initial = self._safe_read(item)
        if (
            initial.media_kind != "ebook"
            or type(file_index) is not int
            or not 0 <= file_index < len(initial.files)
        ):
            raise UnsupportedMediaError("unsupported media")
        selected = initial.files[file_index]
        if selected.format != "epub":
            raise UnsupportedMediaError("unsupported media")

        try:
            local = self._inspector.inspect(
                self._mount, selected.relative_path, self._limits.source_archive_bytes
            )
        except MetadataReportError:
            raise
        except Exception as error:
            raise MetadataReportError("EPUB inspection failed") from error

        target_title_claim = LocalClaim.create(
            kind=ClaimKind.TITLE,
            observed=initial.title,
            canonical=normalize_title(initial.title),
            excerpt=initial.title,
            applicability="not_applicable",
            basis="target_metadata",
            checksum="not_applicable",
            applicability_basis="not_applicable",
        )
        combined_claims = tuple(
            sorted((target_title_claim, *local.claims), key=lambda value: value.reference)
        )
        observations: list[ProviderObservation] = []
        for binding in self._providers:
            if not binding.enabled:
                observations.append(
                    ProviderObservation(binding.name, ProviderStatus.DISABLED)
                )
                continue
            try:
                provider = binding.factory()
            except CredentialError:
                raise
            except Exception as error:
                raise CredentialError("provider credential unavailable") from error
            try:
                observation = provider.observe(combined_claims)
            except Exception:
                observation = ProviderObservation(
                    binding.name, ProviderStatus.UNAVAILABLE
                )
            if observation.provider != binding.name:
                raise MetadataReportError("provider result is invalid")
            observations.append(observation)

        reasons = tuple(
            sorted(
                {f"opening_{claim.kind.value}" for claim in local.claims}
                or {"no_opening_claims"}
            )
        )
        report = MetadataReport.create(
            target=TargetFacts(
                self._connection.name, self._library.name, item, initial.title
            ),
            file=FileFacts(
                file_index, selected.identity, local.content_digest
            ),
            local_claims=combined_claims,
            local_reasons=reasons,
            providers=tuple(sorted(observations, key=lambda value: value.provider)),
        )
        repository: ReportRepository | None = None
        try:
            repository = self._repository_factory()
            persisted = repository.publish(
                report,
                lambda: self._is_current(
                    item,
                    initial,
                    selected,
                    local.content_digest,
                ),
            )
            if persisted != report.to_json() or repository.get(report.report_id) != persisted:
                raise MetadataReportError("persisted report readback failed")
            return persisted
        except MetadataReportError:
            raise
        except Exception as error:
            raise MetadataReportError("report publication failed") from error
        finally:
            if repository is not None:
                repository.close()

    def _is_current(
        self,
        item: str,
        initial: TargetSnapshot,
        selected: TargetFile,
        expected_digest: str,
    ) -> bool:
        final = self._safe_read(item)
        matches = tuple(
            value for value in final.files if value.identity == selected.identity
        )
        if (
            final.media_kind != "ebook"
            or final.title != initial.title
            or len(matches) != 1
            or matches[0].format != "epub"
            or matches[0].relative_path != selected.relative_path
            or not self._inspector.unchanged(
                self._mount,
                selected.relative_path,
                expected_digest,
                self._limits.source_archive_bytes,
            )
        ):
            raise CurrentnessError("target or media changed")
        return True

    def _safe_read(self, item: str) -> TargetSnapshot:
        try:
            value = self._target.read(item)
        except MetadataReportError:
            raise
        except Exception as error:
            raise MetadataReportError("target read failed") from error
        if type(value) is not TargetSnapshot:
            raise MetadataReportError("target response is invalid")
        return value


__all__ = [
    "CredentialError", "CurrentnessError", "EpubInspector", "GenerateMetadataReport",
    "InspectionResult", "MetadataProvider", "MetadataReportError", "ProviderBinding",
    "ReportRepository", "TargetFile", "TargetReader", "TargetSnapshot",
    "UnsupportedMediaError",
]
