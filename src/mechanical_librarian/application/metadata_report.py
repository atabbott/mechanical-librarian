"""Synchronous report orchestration and its narrow application-owned ports."""

from __future__ import annotations

import hashlib
import time
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
    ClaimScope,
    ClaimKind,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderObservation,
    ProviderStatus,
    TargetFacts,
    derive_field_results,
    normalize_title,
)
from mechanical_librarian.domain.cover_evidence import (
    CoverSource,
    CoverSourceKind,
    CoverSourceState,
)


class MetadataReportError(RuntimeError):
    """A value-free operational failure at the command boundary."""


class UnsupportedMediaError(MetadataReportError):
    pass


class ItemInspectionError(MetadataReportError):
    pass


class ArtifactAuthorityError(MetadataReportError):
    pass


class CurrentnessError(MetadataReportError):
    pass


class CredentialError(MetadataReportError):
    pass


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
    title: str
    files: tuple[TargetFile, ...]
    cover: TargetCover | None = None


@dataclass(frozen=True, slots=True)
class InspectionResult:
    content_digest: str
    claims: tuple[LocalClaim, ...]
    cover_sources: tuple[CoverSource, ...] = ()


class TargetReader(Protocol):
    def read(self, item: str) -> TargetSnapshot: ...


class TargetCoverReader(TargetReader, Protocol):
    def read_cover(self, cover: TargetCover, *, deadline: float | None = None) -> bytes | None: ...


class EpubInspector(Protocol):
    def inspect(self, mount: MediaMountConfiguration, relative_path: str, maximum_bytes: int) -> InspectionResult: ...
    def unchanged(self, mount: MediaMountConfiguration, relative_path: str, expected_digest: str, maximum_bytes: int) -> bool: ...


class MetadataProvider(Protocol):
    def observe(self, local_claims: tuple[LocalClaim, ...]) -> ProviderObservation: ...


class CoverObserver(Protocol):
    """Collect bounded report-only cover outcomes before final publication."""

    def observe(
        self,
        target: TargetSnapshot,
        providers: tuple[ProviderObservation, ...],
        local_sources: tuple[CoverSource, ...],
        descriptors: tuple[tuple[str, str, str | None], ...] = (),
        aggregate_deadline: float | None = None,
    ) -> tuple[CoverSource, ...]: ...


class ReportRepository(Protocol):
    def publish(self, report: MetadataReport, currentness: Callable[[], bool], expected_current: str | None = None) -> str: ...
    def get(self, report_id: str) -> str: ...
    def current(self, connection: str, library: str, item: str, file_identity: str) -> str: ...
    def current_optional(self, connection: str, library: str, item: str, file_identity: str) -> str | None: ...
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
        cover_observer: CoverObserver | None = None,
        publication_authority_guard: Callable[[], None] | None = None,
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

    def run(self, item: str, file_index: int, expected_file_identity: str | None = None) -> str:
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
        if expected_file_identity is not None and selected.identity != expected_file_identity:
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
                local = self._inspector.inspect(
                    self._mount, selected.relative_path, self._limits.source_archive_bytes
                )
            except MetadataReportError:
                raise
            except Exception as error:
                raise ItemInspectionError("EPUB inspection failed") from error
        except MetadataReportError:
            if repository is not None:
                repository.close()
            raise
        except Exception as error:
            if repository is not None:
                repository.close()
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
            scope=ClaimScope.WORK,
            occurrence=0,
        )
        # Claims are evidence, not a set: retain the target observation first and
        # then the inspector's DOM/source occurrence order.  Content-addressed
        # references are deliberately not an ordering key.
        combined_claims = (target_title_claim, *local.claims)
        observations: list[ProviderObservation] = []
        descriptors: list[tuple[str, str, str | None]] = []
        cover_aggregate_deadline = time.monotonic() + 60.0
        for binding in self._providers:
            if not binding.enabled:
                observations.append(
                    ProviderObservation(binding.name, ProviderStatus.DISABLED)
                )
                continue
            try:
                provider = binding.factory()
            except CredentialError:
                repository.close()
                raise
            except Exception as error:
                repository.close()
                raise CredentialError("provider credential unavailable") from error
            try:
                observation = (
                    provider.observe(combined_claims, deadline=cover_aggregate_deadline)
                    if hasattr(provider, "cover_descriptors")
                    else provider.observe(combined_claims)
                )
            except Exception:
                observation = ProviderObservation(
                    binding.name, ProviderStatus.UNAVAILABLE
                )
            if observation.provider != binding.name:
                repository.close()
                raise MetadataReportError("provider result is invalid")
            observations.append(observation)
            candidate_descriptors = getattr(provider, "cover_descriptors", ())
            if type(candidate_descriptors) is tuple:
                descriptors.extend((binding.name, source_id, url) for source_id, url in candidate_descriptors)

        reasons = tuple(
            sorted(
                {f"{claim.basis}_{claim.kind.value}" for claim in local.claims}
                or {"no_opening_claims"}
            )
        )
        try:
            cover_sources = local.cover_sources if self._cover_observer is None else self._cover_observer.observe(
                initial, tuple(observations), local.cover_sources, tuple(descriptors), cover_aggregate_deadline
            )
            report = MetadataReport.create(
                target=TargetFacts(self._connection.name, self._library.name, item, initial.title),
                file=FileFacts(file_index, selected.identity, local.content_digest),
                local_claims=combined_claims, local_reasons=reasons,
                providers=tuple(sorted(observations, key=lambda value: value.provider)),
                field_results=derive_field_results(combined_claims),
                cover_sources=cover_sources,
            )
        except Exception:
            repository.close()
            raise
        try:
            persisted = repository.publish(
                report,
                lambda: (self._publication_authority_guard() if self._publication_authority_guard is not None else None) is None and self._is_current(item, initial, selected, local.content_digest, cover_sources),
                expected_current,
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
        cover_sources: tuple[CoverSource, ...],
    ) -> bool:
        final = self._safe_read(item)
        matches = tuple(
            value for value in final.files if value.identity == selected.identity
        )
        if (
            final.media_kind != "ebook"
            or final.title != initial.title
            or final.cover != initial.cover
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
        expected_cover = next(
            (
                source.artifact.original_digest
                for source in cover_sources
                if source.kind is CoverSourceKind.ABS and source.artifact is not None
            ),
            None,
        )
        expected_absent = any(
            source.kind is CoverSourceKind.ABS
            and source.state is CoverSourceState.ABSENT
            for source in cover_sources
        )
        if expected_cover is not None or expected_absent:
            reader = getattr(self._target, "read_cover", None)
            try:
                current_cover = reader(final.cover) if callable(reader) and final.cover is not None else None
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
    "CoverObserver", "CoverReadError", "CoverSourceChangedError", "CoverTimeoutError", "CredentialError", "CurrentnessError", "EpubInspector", "GenerateMetadataReport",
    "ArtifactAuthorityError", "InspectionResult", "ItemInspectionError", "MetadataProvider", "MetadataReportError", "ProviderBinding",
    "ReportRepository", "TargetFile", "TargetReader", "TargetSnapshot",
    "UnsupportedMediaError",
]
