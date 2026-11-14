"""Acquire the declared ABS cover and provider covers in the bounded envelope."""

from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit

from mechanical_librarian.adapters.cover_artifacts import (
    CoverDecodeRejected,
    CoverDecodeTimeout,
    decode_cover,
)
from mechanical_librarian.adapters.providers.remote_cover import (
    CoverDeadline,
    RemoteCoverError,
    RemoteCoverTimeout,
    fetch_remote_cover,
)
from mechanical_librarian.adapters.retained_covers import RetainedCoverStore
from mechanical_librarian.application.configuration import (
    ProviderConnectionConfiguration,
)
from mechanical_librarian.application.metadata_report import (
    ArtifactAuthorityError,
    CoverReadError,
    CoverSourceChangedError,
    CoverTimeoutError,
    MetadataReportError,
    ProviderCoverDescriptor,
    TargetCoverReader,
    TargetSnapshot,
)
from mechanical_librarian.domain.cover_evidence import (
    DECODER_REJECTED_STATES,
    CoverArtifact,
    CoverDesignation,
    CoverEvidenceError,
    CoverFinalHost,
    CoverProvenance,
    CoverSource,
    CoverSourceKind,
    CoverSourceState,
    CoverTruncationReason,
    cover_source_id,
)
from mechanical_librarian.domain.cover_rules import CoverMeasures, same_art
from mechanical_librarian.domain.metadata_report import (
    ProviderObservation,
    ProviderStatus,
)

if TYPE_CHECKING:
    from mechanical_librarian.adapters.persistence.cover_artifacts import (
        CoverArtifactStore,
    )

_DATANODE = re.compile(r"ia[0-9]{1,6}\.us\.archive\.org")
# Every metadata adapter states this one literal when the provider named an
# edition but no work, so two descriptors carrying it name nothing in common.
_UNRESOLVED_WORK_IDENTITY = "unresolved"
# `docs/architecture/security.md` › Provider cover admission: four covers a
# connection, eight for a Hardcover connection, whose book's edition list is
# the one source that offers that many (#1380), and twelve a report.
_CONNECTION_COVERS = 4
_HARDCOVER_CONNECTION_COVERS = 8
_REPORT_COVERS = 12
_DESIGNATION_PRIORITY = {
    CoverDesignation.EXACT_EDITION: 0,
    CoverDesignation.DEFAULT: 1,
    CoverDesignation.WORK: 2,
    CoverDesignation.RELATED_EDITION: 3,
    CoverDesignation.LISTED_EDITION: 4,
}


def _final_host(kind: CoverSourceKind, url: str) -> CoverFinalHost | None:
    """The closed family of the host a retrieved Open Library cover came from.

    The transport admitted ``url`` on Open Library's fixed route, so any other
    host is an internal invariant failure. It raises, and no host persists.
    """
    if kind is not CoverSourceKind.OPEN_LIBRARY:
        return None
    host = urlsplit(url).hostname
    if host == "covers.openlibrary.org":
        return CoverFinalHost.COVERS_OPEN_LIBRARY
    if host == "archive.org":
        return CoverFinalHost.ARCHIVE
    if host is not None and _DATANODE.fullmatch(host) is not None:
        return CoverFinalHost.US_ARCHIVE
    raise CoverEvidenceError("cover final host is invalid")


class CoverEvidenceObserver:
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
        retained_root: Path | None = None,
    ) -> None:
        self._target = target
        self._artifacts = artifacts
        self._providers = {value.name: value for value in providers}
        self._retained_root = retained_root

    def observe_current(
        self,
        target: TargetSnapshot,
        local_sources: tuple[CoverSource, ...],
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
            return (source, *local_sources)
        source_id = cover_source_id(CoverSourceKind.ABS, target.cover.identity)
        if time.monotonic() >= aggregate:
            return (
                CoverSource(
                    source_id,
                    CoverSourceKind.ABS,
                    CoverSourceState.TIMEOUT,
                    timeout_scope="not_started_due_to_aggregate_deadline",
                ),
                *local_sources,
            )
        original: bytes | None = None
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
            # The bytes were read but not decoded: their digest is what a
            # cover write binds and keeps for Put back (#1362).
            source = CoverSource(
                source_id,
                CoverSourceKind.ABS,
                error.state,
                original_digest=(
                    hashlib.sha256(original).hexdigest()
                    if original is not None and error.state in DECODER_REJECTED_STATES
                    else None
                ),
            )
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
        return (source, *local_sources)

    def observe(
        self,
        target: TargetSnapshot,
        providers: tuple[ProviderObservation, ...],
        local_sources: tuple[CoverSource, ...],
        descriptors: tuple[ProviderCoverDescriptor, ...] = (),
        aggregate_deadline: float | None = None,
        retained: tuple[str, str, str] | None = None,
    ) -> tuple[CoverSource, ...]:
        now = time.monotonic()
        aggregate = now + 60.0 if aggregate_deadline is None else aggregate_deadline
        deadline = CoverDeadline(aggregate, min(now + 20.0, aggregate))
        return (
            *local_sources,
            *self._retained_source(retained, deadline),
            *self._provider_covers(providers, descriptors, deadline=deadline),
        )

    def _retained_source(
        self, binding: tuple[str, str, str] | None, deadline: CoverDeadline
    ) -> tuple[CoverSource, ...]:
        """The listed original this work's newest switch replaced, if any.

        ``binding`` is the work's live retained binding (digest, plan and
        decision), read by report assembly from its own repository. The bytes
        go through the same constrained decoder as every other cover, so the
        artifact carries current measures and oriented dimensions. An original
        the decoder refuses is still offered, by the digest the store has just
        verified and with no artifact or preview, so the owner can put it back
        byte for byte (#1362). A copy that is gone, altered, timed out or past
        the deadline is simply not offered (#1363, D1).
        """
        if (
            binding is None
            or self._retained_root is None
            or time.monotonic() >= deadline.aggregate_deadline
        ):
            return ()
        digest, plan_id, _decision_id = binding
        try:
            original = RetainedCoverStore(self._retained_root).read(digest)
            decoded = decode_cover(original, deadline=deadline.aggregate_deadline)
        except CoverDecodeRejected as error:
            if error.state not in DECODER_REJECTED_STATES:
                return ()
            # Nothing is staged: the original stays in the retained store
            # alone, and it has no preview to show.
            return (
                CoverSource(
                    cover_source_id(CoverSourceKind.RETAINED, plan_id),
                    CoverSourceKind.RETAINED,
                    error.state,
                    original_digest=digest,
                ),
            )
        except (ValueError, CoverDecodeTimeout):
            return ()
        # Only its preview is staged: the original stays in the retained store
        # alone, which the guarded write reads it from (D4), so a release
        # leaves no copy of it in the artifact store.
        self._artifacts.stage_preview(decoded.artifact, decoded.preview)
        return (
            CoverSource(
                cover_source_id(CoverSourceKind.RETAINED, plan_id),
                CoverSourceKind.RETAINED,
                CoverSourceState.RETRIEVED,
                decoded.artifact,
            ),
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
            for value in values:
                results.append(self._remote(value, deadline))
        kept, collapsed = self._collapse_same_art(results)
        return (*kept, *truncations, *collapsed)

    def _admit_provider_descriptors(
        self,
        observations: tuple[ProviderObservation, ...],
        descriptors: tuple[ProviderCoverDescriptor, ...],
    ) -> tuple[
        dict[str, tuple[ProviderCoverDescriptor, ...]],
        tuple[CoverSource, ...],
    ]:
        works: dict[str, set[str]] = {}
        editions: dict[str, set[str]] = {}
        for observation in observations:
            if observation.status is not ProviderStatus.CANDIDATES:
                continue
            works[observation.provider] = {
                candidate.work_identity for candidate in observation.candidates
            }
            editions[observation.provider] = {
                candidate.identity for candidate in observation.candidates
            }
        values: dict[str, list[ProviderCoverDescriptor]] = {}
        passed: list[CoverSource] = []
        seen_ids: set[str] = set()
        for descriptor in descriptors:
            configuration = self._providers.get(descriptor.provider)
            provenance = descriptor.provenance
            if (
                configuration is None
                or not configuration.cover_enabled
                or provenance is None
                or provenance.provider != descriptor.provider
            ):
                continue
            if descriptor.source_id in seen_ids:
                raise MetadataReportError("provider cover result is invalid")
            if provenance.designation is CoverDesignation.TRUNCATION:
                # An adapter's own truncation row (#1380) names no work: it
                # records a bound the adapter met, and takes no cover slot.
                seen_ids.add(descriptor.source_id)
                passed.append(
                    CoverSource(
                        descriptor.source_id,
                        descriptor.kind,
                        CoverSourceState.TRUNCATED,
                        provenance=provenance,
                    )
                )
                continue
            if provenance.work_identity == _UNRESOLVED_WORK_IDENTITY:
                # `docs/architecture/security.md`, "Provider cover admission",
                # is the authority for this branch. An unresolved sentinel
                # equals every other unresolved sentinel, so it is no evidence
                # at all. Such a descriptor is this provider's cover only when
                # the edition it names is one the same provider actually
                # observed. A descriptor naming no edition holds no identity
                # either, and the same membership test refuses it. Both are
                # refused here, before any request is constructed.
                if provenance.edition_identity not in editions.get(
                    descriptor.provider, set()
                ):
                    continue
            elif provenance.work_identity not in works.get(descriptor.provider, set()):
                continue
            seen_ids.add(descriptor.source_id)
            values.setdefault(descriptor.provider, []).append(descriptor)

        ordered = {
            provider: tuple(
                sorted(
                    values[provider],
                    key=lambda value: (
                        _DESIGNATION_PRIORITY.get(value.provenance.designation, 99)
                        if value.provenance is not None
                        else 99,
                        value.order,
                        value.source_id,
                    ),
                )
            )
            for provider in sorted(values)
        }
        caps = {
            provider: _HARDCOVER_CONNECTION_COVERS
            if covers[0].kind is CoverSourceKind.HARDCOVER
            else _CONNECTION_COVERS
            for provider, covers in ordered.items()
        }
        # Two passes: every connection's first four in name order, then each
        # Hardcover connection's fifth to eighth into the room left.
        selected: dict[str, list[ProviderCoverDescriptor]] = {}
        remaining = _REPORT_COVERS
        for provider, covers in ordered.items():
            selected[provider] = list(covers[: min(_CONNECTION_COVERS, remaining)])
            remaining -= len(selected[provider])
        for provider, covers in ordered.items():
            extra = covers[_CONNECTION_COVERS : caps[provider]][:remaining]
            selected[provider].extend(extra)
            remaining -= len(extra)
        truncations: list[CoverSource] = []
        for provider, covers in ordered.items():
            if len(covers) > caps[provider]:
                truncations.append(
                    self._truncation_source(
                        provider, covers[0].kind, CoverTruncationReason.PROVIDER_LIMIT
                    )
                )
            if len(selected[provider]) < min(len(covers), caps[provider]):
                truncations.append(
                    self._truncation_source(
                        provider, covers[0].kind, CoverTruncationReason.GALLERY_LIMIT
                    )
                )
        admitted = {provider: tuple(covers) for provider, covers in selected.items()}
        return admitted, (*passed, *truncations)

    @classmethod
    def _collapse_same_art(
        cls, sources: list[CoverSource]
    ) -> tuple[list[CoverSource], tuple[CoverSource, ...]]:
        """Drop a listed edition cover that shows the same art as a larger one.

        Only one connection's retrieved listed-edition covers that both carry
        measures are compared, under ADR-0093's one same-art relation; the
        larger pixel area stays, ties to the lower source id, and one
        `same_art` row records the drop (#1380). Never across connections or
        designations, and it never admits. A dropped cover is not published:
        its staged blobs are orphans the artifact store already reclaims.
        """
        measured = sorted(
            (
                source
                for source in sources
                if source.provenance is not None
                and source.provenance.designation is CoverDesignation.LISTED_EDITION
                and source.artifact is not None
                and source.artifact.measures is not None
            ),
            key=lambda source: (
                source.provenance.provider if source.provenance is not None else "",
                -(source.artifact.width * source.artifact.height)
                if source.artifact is not None
                else 0,
                source.source_id,
            ),
        )
        kept: list[tuple[str, CoverArtifact]] = []
        dropped: set[str] = set()
        collapsed: dict[str, CoverSourceKind] = {}
        for source in measured:
            assert source.provenance is not None and source.artifact is not None
            provider, artifact = source.provenance.provider, source.artifact
            if any(
                provider == kept_provider
                and same_art(
                    cast("CoverMeasures", kept_artifact.measures),
                    (kept_artifact.width, kept_artifact.height),
                    cast("CoverMeasures", artifact.measures),
                    (artifact.width, artifact.height),
                )
                for kept_provider, kept_artifact in kept
            ):
                dropped.add(source.source_id)
                collapsed[provider] = source.kind
            else:
                kept.append((provider, artifact))
        return (
            [source for source in sources if source.source_id not in dropped],
            tuple(
                cls._truncation_source(provider, kind, CoverTruncationReason.SAME_ART)
                for provider, kind in sorted(collapsed.items())
            ),
        )

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
        if time.monotonic() >= deadline.aggregate_deadline:
            return CoverSource(
                source_id,
                kind,
                CoverSourceState.TIMEOUT,
                timeout_scope="not_started_due_to_aggregate_deadline",
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
                redirects=descriptor.redirects,
                user_agent=descriptor.user_agent,
            )
            final_host = _final_host(kind, remote.url)
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
                final_host=final_host,
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
