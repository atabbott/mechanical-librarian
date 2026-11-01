"""Parent-side audiobook inspection: open tracks and sidecars for the children."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass

from mechanical_librarian.adapters.evidence_extractors.audio_local import (
    sidecar_surface,
)
from mechanical_librarian.adapters.evidence_extractors.local_media import (
    LocalMediaError,
    LocalMediaLimitError,
    list_local_media_siblings,
    open_local_media,
    sha256_descriptor,
)
from mechanical_librarian.adapters.evidence_extractors.process import (
    run_audio_inspector_child,
)
from mechanical_librarian.application.configuration import MediaMountConfiguration
from mechanical_librarian.application.metadata_report import (
    AudioInspectionResult,
    AudioItemInspectionError,
    MetadataReportError,
    TargetFile,
)
from mechanical_librarian.domain.audio_composition import (
    InspectedTrack,
    attach_claim_references,
    compose_audio_inspections,
)
from mechanical_librarian.domain.audio_inspection import (
    MAX_AUDIO_TRACKS,
    MULTI_FILE_WALL_SECONDS,
    AudioInspectionLimits,
    AudioInspectionOutcome,
    AudioSidecarDigest,
    AudioTrackDigest,
    project_track_name_detail,
)
from mechanical_librarian.domain.audio_inspection import MAX_FACTS as MAX_AUDIO_FACTS
from mechanical_librarian.domain.audio_inspection import (
    MAX_PUBLIC_REPORT_BYTES as MAX_AUDIO_BYTES,
)
from mechanical_librarian.domain.identifiers import Fingerprint, SchemaVersion
from mechanical_librarian.domain.metadata_report import (
    AudioDiscoveryTitle,
    AudioFacts,
    ClaimKind,
    FieldState,
    LocalClaim,
    ReportValueError,
    audio_set_content_digest,
    canonical_json,
    surviving_discovery_titles,
)
from mechanical_librarian.domain.work_identity import discovery_title_candidate


class IsolatedAudioInspector:
    """Compose F43's audiobook capability behind one ``inspect`` call.

    The mount, the sibling listing, and every descriptor are opened here; the
    probe and the interpretation both happen in isolated children, and this
    object never reads a media or sidecar byte itself.

    An item is inspected one track at a time, each in its own pair of children
    under F43's own per-file wall, and the whole item under one count, one
    summed byte ceiling, and one clock.  Nothing accumulates across tracks
    except the closed values each child returned.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock

    def inspect(
        self,
        mount: MediaMountConfiguration,
        tracks: tuple[TargetFile, ...],
        maximum_bytes: int,
    ) -> AudioInspectionResult:
        if not 1 <= len(tracks) <= MAX_AUDIO_TRACKS:
            raise AudioItemInspectionError(
                AudioInspectionOutcome.MULTI_FILE_BUDGET_EXCEEDED
            )
        sidecars: list[_OpenedSidecar] = []
        try:
            # The folder is one surface for the whole item, so its documents
            # are derived once and interpreted once -- by the first track's
            # child.  Deriving them per track would multiply one sidecar's
            # evidence by the number of files beside it.  An item whose tracks
            # do not share one directory has no such surface, and is handed
            # none of it.
            folder = _audio_item_folder(tuple(value.relative_path for value in tracks))
            sidecars = (
                []
                if folder is None
                else open_audio_sidecars(
                    mount,
                    tracks[0].relative_path,
                    tuple(value.relative_path.rsplit("/", 1)[-1] for value in tracks),
                )
            )
            inspected = self._inspect_tracks(
                mount, tracks, maximum_bytes, sidecars, folder
            )
            composed = compose_audio_inspections(inspected)
            if len(composed.facts) > MAX_AUDIO_FACTS:
                raise AudioItemInspectionError(
                    AudioInspectionOutcome.MULTI_FILE_BUDGET_EXCEEDED
                )
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
                for occurrence, fact in enumerate(composed.facts, 1)
            )
            audio = AudioFacts(
                composed.chapters,
                composed.chapters_truncated,
                composed.embedded_cover,
                sidecar_manifest(sidecars),
                composed.composition,
                attach_claim_references(
                    composed, tuple(claim.reference for claim in claims)
                ),
                composed.observations,
                composed.chapter_rows_omitted,
                # The folder leaf, then a lone track's own title tag, each read
                # through the hostile-name grammar. The drops that need the
                # target -- its title, authors and series -- happen when the
                # report is planned (#1255).
                surviving_discovery_titles(
                    tuple(
                        AudioDiscoveryTitle(basis, title)
                        for basis, text in (
                            (
                                "path_leaf",
                                folder.rsplit("/", 1)[-1] if folder else None,
                            ),
                            (
                                "title_tag",
                                inspected[0].inspection.title
                                if len(inspected) == 1
                                else None,
                            ),
                        )
                        if text is not None
                        and (title := discovery_title_candidate(text)) is not None
                    ),
                    (),
                ),
            )
            if len(canonical_json(audio.to_data()).encode("utf-8")) > MAX_AUDIO_BYTES:
                # An audio section the retained report cannot hold is evidence
                # that can never be published, and half of it is not the item's
                # evidence set.
                raise AudioItemInspectionError(
                    AudioInspectionOutcome.MULTI_FILE_BUDGET_EXCEEDED
                )
            return AudioInspectionResult(
                audio_set_content_digest(
                    tuple(value.digest for value in composed.tracks)
                ),
                claims,
                audio,
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
            _close_sidecars(sidecars)

    def _inspect_tracks(
        self,
        mount: MediaMountConfiguration,
        tracks: tuple[TargetFile, ...],
        maximum_bytes: int,
        sidecars: list[_OpenedSidecar],
        folder: str | None,
    ) -> tuple[InspectedTrack, ...]:
        """Run one bounded child pair per track under the item's own budget."""
        ceiling = _audio_source_bytes(maximum_bytes)
        limits = AudioInspectionLimits(source_bytes=ceiling)
        multi = len(tracks) > 1
        deadline = self._clock() + MULTI_FILE_WALL_SECONDS

        def expired() -> bool:
            # A one-track item is bounded by F43's own per-file wall alone, so
            # the item clock is a multi-file bound and never narrows the case
            # that already passed.
            return multi and self._clock() >= deadline

        inspected: list[InspectedTrack] = []
        total = 0
        for ordinal, track in enumerate(tracks, 1):
            components = track.relative_path.split("/")
            named = ordinal if multi else None
            try:
                opened = open_local_media(mount.container_path, track.relative_path)
            except (LocalMediaError, OSError) as error:
                # A file that could not be opened is one file of many, so the
                # refusal is raised here rather than at the item boundary,
                # which has no ordinal left to name.
                raise _track_boundary_failure(error, named) from error
            try:
                if opened.length == 0:
                    # An empty file has no container to refuse. Handed to the
                    # probe it comes back a parse failure, which reaches the
                    # owner as `parser_refused` and asks them to supply a file
                    # within the supported parsing limits -- advice about a
                    # file with no bytes in it. It is an unavailable source,
                    # and naming it here keeps the ordinal and spawns no child.
                    raise AudioItemInspectionError(
                        AudioInspectionOutcome.UNAVAILABLE_SOURCE, named
                    )
                if expired():
                    raise AudioItemInspectionError(
                        AudioInspectionOutcome.MULTI_FILE_BUDGET_EXCEEDED
                    )
                if multi and (
                    opened.length > ceiling
                    or total + opened.length > ceiling * len(tracks)
                ):
                    # A one-track item keeps F43's own per-file refusal, which
                    # the digest below still raises; the summed bound is a
                    # multi-file bound and names itself as one.
                    raise AudioItemInspectionError(
                        AudioInspectionOutcome.MULTI_FILE_BUDGET_EXCEEDED
                    )
                total += opened.length
                try:
                    digest = sha256_descriptor(
                        opened.descriptor, opened.length, maximum=ceiling
                    )
                except (LocalMediaError, OSError, ValueError) as error:
                    raise _track_boundary_failure(error, named) from error
                inspection = run_audio_inspector_child(
                    opened.descriptor,
                    Fingerprint("sha256", SchemaVersion("local-file-v1"), digest),
                    limits,
                    sidecars=(
                        tuple(
                            (value.surface, value.name, value.descriptor)
                            for value in sidecars
                        )
                        if ordinal == 1
                        else ()
                    ),
                    path_file=components[-1],
                    path_folder=(
                        folder.rsplit("/", 1)[-1] if ordinal == 1 and folder else None
                    ),
                    cancelled=expired,
                )
            finally:
                os.close(opened.descriptor)
            if not inspection.outcome.succeeded:
                # An item is one evidence set: a track this build could not
                # read leaves the item with no closed set at all, reported as
                # what stopped that track rather than as a partial success.
                raise AudioItemInspectionError(
                    AudioInspectionOutcome.MULTI_FILE_BUDGET_EXCEEDED
                    if expired()
                    else inspection.outcome,
                    ordinal if multi else None,
                )
            # The manifest carries the projection, never the raw name: a
            # mounted filesystem states names the retained document refuses,
            # and an item is not denied its evidence over one.  The child above
            # is still handed the file's own name, because what it reads there
            # is evidence about the file.
            projection = project_track_name_detail(components[-1], ordinal)
            inspected.append(
                InspectedTrack(
                    ordinal,
                    track.identity,
                    digest,
                    track.format,
                    projection.name,
                    inspection,
                    track.sequence,
                    projection.synthesized,
                    projection.repaired,
                )
            )
        return tuple(inspected)

    def unchanged(
        self,
        mount: MediaMountConfiguration,
        relative_paths: tuple[str, ...],
        expected_digest: str,
        maximum_bytes: int,
        expected_sidecars: tuple[AudioSidecarDigest, ...] = (),
        expected_tracks: tuple[AudioTrackDigest, ...] = (),
    ) -> bool:
        """Re-prove every audio file *and* the sidecar set they were read with.

        Digesting the selected file alone proved only one of the two surfaces
        an audiobook's evidence comes from.  The candidate set is re-derived
        through the same function `inspect` uses, so a sidecar added, removed,
        or edited since publication is a change here rather than a silently
        stale report.  Every track is re-digested in manifest order under the
        same summed ceiling, so an edit to any one of them is a change too.

        Re-digesting hundreds of files is the same unbounded work inspecting
        them was, so it runs under the same item wall: a revalidation that
        outlasts it is the item budget, raised as the typed terminal rather
        than reported as a media change nobody made.
        """
        sidecars: list[_OpenedSidecar] = []
        try:
            if not 1 <= len(relative_paths) <= MAX_AUDIO_TRACKS or (
                expected_tracks and len(expected_tracks) != len(relative_paths)
            ):
                return False
            ceiling = _audio_source_bytes(maximum_bytes)
            multi = len(relative_paths) > 1
            deadline = self._clock() + MULTI_FILE_WALL_SECONDS
            digests: list[str] = []
            total = 0
            for ordinal, path in enumerate(relative_paths, 1):
                if multi and self._clock() >= deadline:
                    # One file was bounded by F43's own per-file wall before
                    # this unit existed, so the item clock is armed only for
                    # the sets it was added for.
                    raise AudioItemInspectionError(
                        AudioInspectionOutcome.MULTI_FILE_BUDGET_EXCEEDED, ordinal
                    )
                opened = open_local_media(mount.container_path, path)
                try:
                    total += opened.length
                    if total > ceiling * len(relative_paths):
                        return False
                    digests.append(
                        sha256_descriptor(
                            opened.descriptor, opened.length, maximum=ceiling
                        )
                    )
                finally:
                    os.close(opened.descriptor)
            if expected_tracks and tuple(digests) != tuple(
                value.digest for value in expected_tracks
            ):
                return False
            if audio_set_content_digest(tuple(digests)) != expected_digest:
                return False
            if _audio_item_folder(relative_paths) is None:
                # The tracks span directories, so inspection interpreted no
                # folder surface and published an empty manifest.  Deriving a
                # candidate set from one of those directories here would make
                # the report current only while it disagreed with how it was
                # read.
                return not expected_sidecars
            sidecars = open_audio_sidecars(
                mount,
                relative_paths[0],
                tuple(value.rsplit("/", 1)[-1] for value in relative_paths),
            )
            return sidecar_manifest(sidecars) == tuple(expected_sidecars)
        except (LocalMediaError, OSError, ValueError, ReportValueError):
            return False
        finally:
            _close_sidecars(sidecars)


@dataclass(frozen=True)
class _OpenedSidecar:
    """One recognized sidecar, open and already digested by the parent."""

    surface: str
    name: str
    descriptor: int
    digest: str


def sidecar_manifest(
    sidecars: list[_OpenedSidecar],
) -> tuple[AudioSidecarDigest, ...]:
    return tuple(
        AudioSidecarDigest(value.name, value.digest)
        for value in sorted(sidecars, key=lambda value: value.name)
    )


def _close_sidecars(sidecars: list[_OpenedSidecar]) -> None:
    for value in sidecars:
        try:
            os.close(value.descriptor)
        except OSError:
            pass


def _track_boundary_failure(
    error: BaseException, ordinal: int | None
) -> AudioItemInspectionError:
    """Name the outcome, and the track, of one file the parent could not read.

    ``inspect`` maps these same three faults at the item boundary, where the
    ordinal has already been lost.  Mapping them here as well is what lets an
    owner be told which of three hundred files is missing rather than that the
    item is unreadable.
    """
    if isinstance(error, LocalMediaLimitError):
        outcome = AudioInspectionOutcome.RESOURCE_LIMIT
    elif isinstance(error, OSError):
        outcome = AudioInspectionOutcome.UNAVAILABLE_SOURCE
    else:
        outcome = AudioInspectionOutcome.INTERNAL_FAILURE
    return AudioItemInspectionError(outcome, ordinal)


def _audio_item_folder(relative_paths: tuple[str, ...]) -> str | None:
    """Name the one directory an item's audio lives in, or none at all.

    Folder evidence is item-level: the sidecars beside the files and the
    folder name itself are interpreted once, from the first track's parent.
    A set spread over `CD1/` and `CD2/` has no such parent, so the whole item
    is handed no folder surface rather than the first disc's -- withheld
    evidence is absent, and absent is never wrong.  ``""`` is the mount root,
    which is one shared directory and carries no folder name.
    """
    directories = {
        value.rsplit("/", 1)[0] if "/" in value else "" for value in relative_paths
    }
    return directories.pop() if len(directories) == 1 else None


def _audio_source_bytes(maximum_bytes: int) -> int:
    """Hold the configured audio ceiling inside the domain's immutable cap."""
    caps = AudioInspectionLimits.HARD_CAPS["source_bytes"]
    return max(1, min(int(maximum_bytes), caps))


def open_audio_sidecars(
    mount: MediaMountConfiguration,
    relative_path: str,
    media_names: tuple[str, ...],
) -> list[_OpenedSidecar]:
    """Derive, open, and digest the supported sidecars beside one item.

    This is the single candidate-set derivation: inspection passes the
    descriptors to the isolated child, and revalidation compares the digests.
    Two derivations would be two answers to "which documents is this evidence
    made of", and only one of them could be right.

    A sidecar that cannot be opened, read, or projected is simply absent
    evidence: the audiobook's own container is the primary surface, and one
    unreadable neighbour must not deny the owner the rest of the set. It is
    absent identically on both paths because both call this.
    """
    limits = AudioInspectionLimits()
    directory = relative_path.rsplit("/", 1)[0] if "/" in relative_path else ""
    try:
        names = list_local_media_siblings(mount.container_path, relative_path)
    except LocalMediaError:
        return []
    opened: list[_OpenedSidecar] = []
    for name in names:
        if len(opened) >= limits.sidecar_documents:
            break
        # Libation names its document after the track it exported, so a set is
        # matched against every track's name rather than against the first
        # one's; every other grammar is folder-scoped and answers the same for
        # all of them.
        surface = next(
            (
                value
                for media_name in media_names
                if (value := sidecar_surface(name, media_name)) is not None
            ),
            None,
        )
        if surface is None:
            continue
        reference = f"{directory}/{name}" if directory else name
        try:
            sibling = open_local_media(mount.container_path, reference)
        except LocalMediaError:
            continue
        try:
            digest = sha256_descriptor(
                sibling.descriptor, sibling.length, maximum=limits.sidecar_bytes
            )
            entry = _OpenedSidecar(surface.value, name, sibling.descriptor, digest)
        except (LocalMediaError, OSError, ValueError):
            os.close(sibling.descriptor)
            continue
        try:
            AudioSidecarDigest(entry.name, entry.digest)
        except ValueError:
            # A name the retained report cannot project is a name the manifest
            # cannot carry, so the document is not part of the candidate set on
            # either path rather than on one of them.
            os.close(sibling.descriptor)
            continue
        opened.append(entry)
    return opened
