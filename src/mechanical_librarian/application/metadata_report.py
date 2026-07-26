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
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderEvidenceKind,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    default_target_fields,
    derive_field_results,
    isbn_family,
    normalize_title,
)
from mechanical_librarian.domain.cover_evidence import (
    CoverProvenance,
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
    title: str | None
    files: tuple[TargetFile, ...]
    cover: TargetCover | None = None
    fields: tuple[TargetField, ...] = ()

    def __post_init__(self) -> None:
        if not self.fields:
            object.__setattr__(self, "fields", default_target_fields(self.title))
        facts = TargetFacts("snapshot", "snapshot", "snapshot", self.fields)
        if (
            self.cover is None
            and facts.fields[-1].state is TargetFieldState.VALUE
        ):
            raise MetadataReportError("target cover field is inconsistent")


@dataclass(frozen=True, slots=True)
class MetadataLookup:
    """One closed provider lookup, independent of provider wire shape."""

    kind: ProviderQueryKind
    local_claims: tuple[LocalClaim, ...]
    title: str | None = None
    authors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.kind) is not ProviderQueryKind
            or type(self.local_claims) is not tuple
            or any(type(value) is not LocalClaim for value in self.local_claims)
            or type(self.authors) is not tuple
            or any(type(value) is not str for value in self.authors)
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
        elif self.title is not None or self.authors:
            raise MetadataReportError("provider lookup is invalid")


@dataclass(frozen=True, slots=True)
class InspectionResult:
    content_digest: str
    claims: tuple[LocalClaim, ...]
    cover_sources: tuple[CoverSource, ...] = ()
    rear_resource_limit: bool = False


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


class TargetReader(Protocol):
    def read(self, item: str) -> TargetSnapshot: ...


class TargetCoverReader(TargetReader, Protocol):
    def read_cover(self, cover: TargetCover, *, deadline: float | None = None) -> bytes | None: ...


class EpubInspector(Protocol):
    def inspect(self, mount: MediaMountConfiguration, relative_path: str, maximum_bytes: int) -> InspectionResult: ...
    def unchanged(self, mount: MediaMountConfiguration, relative_path: str, expected_digest: str, maximum_bytes: int) -> bool: ...


class MetadataProvider(Protocol):
    def observe(self, lookup: MetadataLookup) -> ProviderObservation: ...


class CoverObserver(Protocol):
    """Collect bounded report-only cover outcomes before final publication."""

    def observe(
        self,
        target: TargetSnapshot,
        providers: tuple[ProviderObservation, ...],
        local_sources: tuple[CoverSource, ...],
        descriptors: tuple[ProviderCoverDescriptor, ...] = (),
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
    authority: str | None = None


def metadata_lookup(
    target: TargetSnapshot, local_claims: tuple[LocalClaim, ...]
) -> MetadataLookup:
    usable = tuple(
        claim
        for claim in local_claims
        if claim.kind is ClaimKind.ISBN
        and claim.canonical is not None
        and claim.applicability in {"ebook", "unknown"}
        and claim.basis == "opening_matter"
        and claim.rear_provenance is None
        and isbn_family(claim.canonical) is not None
    )
    families = {isbn_family(claim.canonical) for claim in usable}
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
                normalize_title(value).casefold()
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
        tuple(normalize_title(value) for value in authors.value),
    )


def qualify_provider_observation(
    observation: ProviderObservation, lookup: MetadataLookup
) -> ProviderObservation:
    if lookup.kind is not ProviderQueryKind.WORK:
        return ProviderObservation(
            observation.provider,
            observation.status,
            observation.input_references,
            observation.candidates,
            lookup.kind,
            observation.authority,
        )
    if observation.status is not ProviderStatus.CANDIDATES:
        return ProviderObservation(
            observation.provider,
            observation.status,
            (),
            (),
            ProviderQueryKind.WORK,
            observation.authority,
        )
    expected_title = normalize_title(lookup.title or "").casefold()
    expected_authors = tuple(
        normalize_title(value).casefold() for value in lookup.authors
    )
    qualified: dict[str, list[ProviderCandidate]] = {}
    blocked_work_identities: set[str] = set()
    for candidate in observation.candidates:
        titles = tuple(
            normalize_title(claim.value).casefold()
            for claim in candidate.claims
            if claim.scope is ClaimScope.WORK
            and claim.kind is ClaimKind.TITLE
            and claim.state is FieldState.VALID
            and claim.value is not None
        )
        authors = tuple(
            normalize_title(claim.value).casefold()
            for claim in candidate.claims
            if claim.scope is ClaimScope.WORK
            and claim.kind is ClaimKind.CREATOR
            and claim.role == "author"
            and claim.state is FieldState.VALID
            and claim.value is not None
        )
        disqualifying_roles = {
            claim.role
            for claim in candidate.claims
            if claim.scope is ClaimScope.WORK
            and claim.kind is ClaimKind.CONTRIBUTOR
            and claim.role in {"abridger", "adapter", "translator"}
        }
        variant_marker = any(
            _variant_marker(claim.value)
            for claim in candidate.claims
            if claim.kind in {ClaimKind.TITLE, ClaimKind.SUBTITLE}
            and claim.state is FieldState.VALID
            and claim.value is not None
        )
        if disqualifying_roles or variant_marker:
            blocked_work_identities.add(candidate.work_identity)
        if (
            titles == (expected_title,)
            and len(authors) == len(expected_authors)
            and set(authors) == set(expected_authors)
            and not disqualifying_roles
            and not variant_marker
        ):
            qualified.setdefault(candidate.work_identity, []).append(candidate)
    for identity in blocked_work_identities:
        qualified.pop(identity, None)
    if len(qualified) != 1:
        return ProviderObservation(
            observation.provider,
            ProviderStatus.NO_MATCH,
            (),
            (),
            ProviderQueryKind.WORK,
            observation.authority,
        )
    candidate = min(next(iter(qualified.values())), key=lambda value: value.identity)
    allowed_claims = {
        ClaimKind.TITLE,
        ClaimKind.CREATOR,
        ClaimKind.SERIES,
        ClaimKind.SERIES_SEQUENCE,
        ClaimKind.DESCRIPTION,
        ClaimKind.GENRE,
        ClaimKind.TAG,
    }
    allowed_evidence = {
        ProviderEvidenceKind.AUTHOR_IDENTITY,
        ProviderEvidenceKind.CONTRIBUTOR_IDENTITY,
        ProviderEvidenceKind.FIRST_PUBLICATION_DATE,
        ProviderEvidenceKind.WORK_COVER_ID,
    }
    filtered = ProviderCandidate(
        candidate.identity,
        candidate.work_identity,
        tuple(
            claim
            for claim in candidate.claims
            if claim.scope is ClaimScope.WORK and claim.kind in allowed_claims
        ),
        tuple(
            value
            for value in candidate.evidence
            if value.scope is ClaimScope.WORK and value.kind in allowed_evidence
        ),
    )
    return ProviderObservation(
        observation.provider,
        ProviderStatus.CANDIDATES,
        (),
        (filtered,),
        ProviderQueryKind.WORK,
        observation.authority,
    )


def _variant_marker(value: str) -> bool:
    tokens = {
        "".join(character for character in token if character.isalnum())
        for token in normalize_title(value).casefold().split()
    }
    return bool(
        tokens
        & {
            "abridgement",
            "abridgment",
            "abridged",
            "adaptation",
            "adapted",
            "translation",
            "translated",
        }
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
        combined_claims = (*target_title_claim, *local.claims)
        lookup = metadata_lookup(initial, combined_claims)
        observations: list[ProviderObservation] = []
        descriptors: list[ProviderCoverDescriptor] = []
        cover_aggregate_deadline = time.monotonic() + 60.0
        for binding in self._providers:
            if not binding.enabled:
                observations.append(
                    ProviderObservation(
                        binding.name, ProviderStatus.DISABLED,
                        authority=binding.authority,
                    )
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
                    provider.observe(lookup, deadline=cover_aggregate_deadline)
                    if hasattr(provider, "cover_descriptors")
                    else provider.observe(lookup)
                )
            except Exception:
                observation = ProviderObservation(
                    binding.name, ProviderStatus.UNAVAILABLE,
                    authority=binding.authority,
                )
            if observation.provider != binding.name:
                repository.close()
                raise MetadataReportError("provider result is invalid")
            observation = qualify_provider_observation(observation, lookup)
            observation = ProviderObservation(
                observation.provider,
                observation.status,
                observation.input_references,
                observation.candidates,
                observation.query_kind,
                binding.authority,
            )
            observations.append(observation)
            candidate_descriptors = getattr(provider, "cover_descriptors", ())
            if type(candidate_descriptors) is not tuple or any(
                type(value) is not ProviderCoverDescriptor
                or value.provider != binding.name
                for value in candidate_descriptors
            ):
                repository.close()
                raise MetadataReportError("provider cover result is invalid")
            descriptors.extend(candidate_descriptors)

        reasons = tuple(
            sorted(
                {f"{claim.basis}_{claim.kind.value}" for claim in local.claims}
                | ({"rear_resource_limit"} if local.rear_resource_limit else set())
                or {"no_opening_claims"}
            )
        )
        try:
            cover_sources = local.cover_sources if self._cover_observer is None else self._cover_observer.observe(
                initial, tuple(observations), local.cover_sources, tuple(descriptors), cover_aggregate_deadline
            )
            report = MetadataReport.create(
                target=TargetFacts(
                    self._connection.name,
                    self._library.name,
                    item,
                    initial.fields,
                ),
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
                lambda: (
                    self._publication_authority_guard()
                    if self._publication_authority_guard is not None
                    else None
                )
                is None
                and require_report_current(
                    self._target,
                    self._inspector,
                    self._mount,
                    self._limits,
                    report,
                ),
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


def require_report_current(
    target: TargetReader,
    inspector: EpubInspector,
    mount: MediaMountConfiguration | None,
    limits: ReportLimits,
    report: MetadataReport,
) -> bool:
    """Revalidate the exact target, media, and ABS cover bound to a report."""
    if (
        mount is None
        or type(report) is not MetadataReport
        or type(limits) is not ReportLimits
    ):
        raise CurrentnessError("target or media changed")
    try:
        final = target.read(report.target.item)
    except MetadataReportError:
        raise
    except Exception as error:
        raise MetadataReportError("target read failed") from error
    if type(final) is not TargetSnapshot:
        raise MetadataReportError("target response is invalid")
    matches = tuple(
        value for value in final.files if value.identity == report.file.identity
    )
    if (
        final.media_kind != "ebook"
        or final.title != report.target.current_title
        or final.fields != report.target.fields
        or len(matches) != 1
        or matches[0].format != "epub"
        or not inspector.unchanged(
            mount,
            matches[0].relative_path,
            report.file.content_digest,
            limits.source_archive_bytes,
        )
    ):
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
        source.kind is CoverSourceKind.ABS
        and source.state is CoverSourceState.ABSENT
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
    "CoverObserver", "CoverReadError", "CoverSourceChangedError", "CoverTimeoutError", "CredentialError", "CurrentnessError", "EpubInspector", "GenerateMetadataReport",
    "ArtifactAuthorityError", "InspectionResult", "ItemInspectionError",
    "MetadataLookup", "MetadataProvider", "MetadataReportError",
    "ProviderBinding", "ProviderCoverDescriptor",
    "ReportRepository", "TargetFile", "TargetReader", "TargetSnapshot",
    "UnsupportedMediaError",
    "metadata_lookup", "qualify_provider_observation", "require_report_current",
]
