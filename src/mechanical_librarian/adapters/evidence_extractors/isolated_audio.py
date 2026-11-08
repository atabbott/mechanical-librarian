"""Parent-side audiobook inspection: open tracks and sidecars for the children."""

from __future__ import annotations

import dataclasses
import os
import time
from collections.abc import Callable
from dataclasses import dataclass

from mechanical_librarian.adapters.evidence_extractors.audio_local import (
    classify_sidecar,
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
    claim_reference_index,
    compose_audio_inspections,
    fact_value_key,
)
from mechanical_librarian.domain.audio_inspection import (
    MAX_AUDIO_TRACKS,
    MAX_BYTE_ORDER_MARK_BYTES,
    MULTI_FILE_WALL_SECONDS,
    PARENT_SIDECAR_REFUSALS,
    SIDECAR_KIND_SURFACES,
    TERMINAL_SIDECAR_REFUSALS,
    AudioInspectionFact,
    AudioInspectionLimits,
    AudioInspectionOutcome,
    AudioSidecarBinding,
    AudioSidecarByteOrderMark,
    AudioSidecarByteOrderMarkFact,
    AudioSidecarDigest,
    AudioSidecarKind,
    AudioSidecarReading,
    AudioSidecarRefusal,
    AudioSidecarStrippedLimit,
    AudioTrackDigest,
    project_track_name_detail,
)
from mechanical_librarian.domain.audio_inspection import MAX_FACTS as MAX_AUDIO_FACTS
from mechanical_librarian.domain.audio_inspection import (
    MAX_PUBLIC_REPORT_BYTES as MAX_AUDIO_BYTES,
)
from mechanical_librarian.domain.audio_sidecar_association import (
    AudioAssociationContext,
    FileAssociationEvidence,
    SidecarDocument,
    SidecarOutcome,
    associate_sidecars,
    file_association_evidence,
    shared_folder,
)
from mechanical_librarian.domain.identifiers import Fingerprint, SchemaVersion
from mechanical_librarian.domain.metadata_report import (
    AudioDiscoveryTitle,
    AudioFacts,
    ClaimKind,
    FieldState,
    LocalClaim,
    ReportValueError,
    audio_record_rungs,
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
        *,
        association: AudioAssociationContext,
    ) -> AudioInspectionResult:
        if type(association) is not AudioAssociationContext:
            raise AudioItemInspectionError(AudioInspectionOutcome.INTERNAL_FAILURE)
        if not 1 <= len(tracks) <= MAX_AUDIO_TRACKS:
            raise AudioItemInspectionError(
                AudioInspectionOutcome.MULTI_FILE_BUDGET_EXCEEDED
            )
        sidecars: list[_OpenedSidecar] = []
        try:
            # The folder is one surface for the whole item, so its documents
            # are derived once and interpreted once -- by the first track's
            # child.  Deriving them per track would multiply one sidecar's
            # evidence by the number of files beside it.  A document named for
            # a track goes to that track's child instead.  An item whose tracks
            # do not share one directory has no such surface, and is handed
            # none of it.
            media_names = tuple(
                value.relative_path.rsplit("/", 1)[-1] for value in tracks
            )
            folder = _audio_item_folder(tuple(value.relative_path for value in tracks))
            sidecars = (
                []
                if folder is None
                else open_audio_sidecars(
                    mount,
                    tracks[0].relative_path,
                    media_names,
                    association.own_file_names,
                )
            )
            read = self._inspect_tracks(mount, tracks, maximum_bytes, sidecars, folder)
            documents, shadow_documents = _documents(read, sidecars)
            evidence = _file_evidence(read, media_names)
            outcomes = associate_sidecars(association, evidence, documents)
            # The same rules over each reading without a byte-order mark
            # (#1796).  Its outcomes reach the manifest's records and nothing
            # else: claims, refusals and composition are the pass above.
            shadow = (
                outcomes
                if shadow_documents is None
                else associate_sidecars(association, evidence, shadow_documents)
            )
            inspected = _with_associated_facts(read, sidecars, outcomes)
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
                _sidecar_manifest(
                    sidecars,
                    documents,
                    outcomes,
                    shadow,
                    claim_reference_index(
                        composed, tuple(claim.reference for claim in claims)
                    ),
                ),
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
                                read[0].inspection.title if len(read) == 1 else None,
                            ),
                        )
                        if text is not None
                        and (title := discovery_title_candidate(text)) is not None
                    ),
                    (),
                ),
            )
            # An audio section the retained report cannot hold is evidence that
            # can never be published, and half of it is not the item's evidence
            # set.  Records of what readings without a byte-order mark add are
            # given up first, so a mark never denies an item (#1796).
            published = next(
                (
                    rung
                    for rung in audio_record_rungs(audio)
                    if len(canonical_json(rung.to_data()).encode("utf-8"))
                    <= MAX_AUDIO_BYTES
                ),
                None,
            )
            if published is None:
                raise AudioItemInspectionError(
                    AudioInspectionOutcome.MULTI_FILE_BUDGET_EXCEEDED
                )
            return AudioInspectionResult(
                audio_set_content_digest(
                    tuple(value.digest for value in composed.tracks)
                ),
                claims,
                published,
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
                    # The track's own documents first, then, for the first
                    # track, the folder's -- never one the parent refused.
                    sidecars=tuple(
                        (
                            SIDECAR_KIND_SURFACES[value.kind].value,
                            value.name,
                            value.descriptor,
                        )
                        for value in sidecars
                        if value.refusal is None
                        and (
                            value.track == ordinal
                            if value.binding is AudioSidecarBinding.TRACK
                            else ordinal == 1
                        )
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
        *,
        own_file_names: tuple[str, ...] | None = None,
    ) -> bool:
        """Re-prove every audio file *and* the sidecar set they were read with.

        Digesting the selected file alone proved only one of the two surfaces
        an audiobook's evidence comes from.  The candidate set is re-derived
        through the same function `inspect` uses, so a sidecar added, removed,
        or edited since publication is a change here rather than a silently
        stale report.  Every track is re-digested in manifest order under the
        same summed ceiling, so an edit to any one of them is a change too.

        Each document's kind, binding and parent-side refusals are recomputed
        from the current listing and the target's current own-file names, so
        a foreign file added beside a folder-level sidecar is a change as well
        (#1481).

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
                own_file_names,
            )
            # Documents open in priority order (track-bound, the target's
            # record, folder-level) while the manifest lists them by name, so
            # both sides are compared by name (#1481).
            return tuple(
                _parent_view(value)
                for value in sorted(sidecars, key=lambda opened: opened.name)
            ) == tuple(
                (
                    value.name,
                    value.digest,
                    value.kind,
                    value.binding,
                    tuple(
                        refusal
                        for refusal in value.refusals
                        if refusal in PARENT_SIDECAR_REFUSALS
                    ),
                )
                for value in sorted(expected_sidecars, key=lambda entry: entry.name)
            )
        except (LocalMediaError, OSError, ValueError, ReportValueError):
            return False
        finally:
            _close_sidecars(sidecars)


@dataclass(frozen=True)
class _OpenedSidecar:
    """One recognized document, open and already digested by the parent.

    ``track`` is the ordinal of the track a track-bound document is named for.
    ``refusal`` is the parent's own, decided from the listing: such a document
    is digested for currentness and never handed to a child.
    """

    name: str
    kind: AudioSidecarKind
    binding: AudioSidecarBinding
    track: int | None
    descriptor: int
    digest: str
    refusal: AudioSidecarRefusal | None


def _parent_view(
    value: _OpenedSidecar,
) -> tuple[
    str,
    str,
    AudioSidecarKind,
    AudioSidecarBinding,
    tuple[AudioSidecarRefusal, ...],
]:
    return (
        value.name,
        value.digest,
        value.kind,
        value.binding,
        () if value.refusal is None else (value.refusal,),
    )


def _documents(
    read: tuple[InspectedTrack, ...], sidecars: list[_OpenedSidecar]
) -> tuple[tuple[SidecarDocument, ...], tuple[SidecarDocument, ...] | None]:
    """Pair each opened document with what its child read from it, if any.

    The first set decides claims, and its readings carry no reading without a
    byte-order mark, so that pass cannot see one (#1796).  The second carries
    each such reading in place of the plain one, for the records only, and
    exists only when some document has one.
    """
    readings = {
        (track.ordinal, reading.name): reading
        for track in read
        for reading in track.inspection.sidecars
    }
    paired = tuple(
        (value, readings.get((value.track or 1, value.name))) for value in sidecars
    )
    claimed = tuple(
        SidecarDocument(
            value.name,
            value.kind,
            value.binding,
            value.refusal,
            None if reading is None else dataclasses.replace(reading, stripped=None),
        )
        for value, reading in paired
    )
    if all(reading is None or reading.stripped is None for _, reading in paired):
        return claimed, None
    return claimed, tuple(
        SidecarDocument(
            value.name,
            value.kind,
            value.binding,
            value.refusal,
            reading
            if reading is None or reading.stripped is None
            else reading.stripped,
        )
        for value, reading in paired
    )


def _file_evidence(
    read: tuple[InspectedTrack, ...], media_names: tuple[str, ...]
) -> FileAssociationEvidence:
    return file_association_evidence(
        tuple(track.inspection.album for track in read),
        read[0].inspection.title if len(read) == 1 else None,
        tuple(person for track in read for person in track.inspection.creators),
        tuple(fact for track in read for fact in track.inspection.facts),
        media_names,
    )


def _with_associated_facts(
    read: tuple[InspectedTrack, ...],
    sidecars: list[_OpenedSidecar],
    outcomes: tuple[SidecarOutcome, ...],
) -> tuple[InspectedTrack, ...]:
    """Hand composition each track's own facts and its associated sidecars'.

    Association text and readings stop here: the inspections composition sees
    carry none of them.
    """
    own: dict[int, list[AudioInspectionFact]] = {track.ordinal: [] for track in read}
    folder: list[AudioInspectionFact] = []
    for value, outcome in zip(sidecars, outcomes, strict=True):
        if value.binding is AudioSidecarBinding.TRACK and value.track is not None:
            own[value.track].extend(outcome.facts)
        else:
            folder.extend(outcome.facts)
    return tuple(
        dataclasses.replace(
            track,
            inspection=dataclasses.replace(
                track.inspection,
                facts=(*track.inspection.facts, *own[track.ordinal]),
                album=None,
                creators=(),
                sidecars=(),
            ),
            folder_facts=tuple(folder) if track.ordinal == 1 else (),
        )
        for track in read
    )


def _sidecar_manifest(
    sidecars: list[_OpenedSidecar],
    documents: tuple[SidecarDocument, ...],
    outcomes: tuple[SidecarOutcome, ...],
    shadow: tuple[SidecarOutcome, ...],
    references: dict[tuple[str, str, str, str | None, str, str | None], str],
) -> tuple[AudioSidecarDigest, ...]:
    """Record every document's classification, and the claims it contributed.

    A document that started with a byte-order mark also records what reading
    it without the mark adds, from the shadow pass (#1796).
    """
    entries: list[AudioSidecarDigest] = []
    for value, document, outcome, unmarked in zip(
        sidecars, documents, outcomes, shadow, strict=True
    ):
        claims: list[str] = []
        for fact in outcome.facts:
            reference = references.get(fact_value_key(fact))
            if reference is not None and reference not in claims:
                claims.append(reference)
        entries.append(
            AudioSidecarDigest(
                value.name,
                value.digest,
                value.kind,
                value.binding,
                outcome.refusals,
                tuple(claims),
                outcome.disputed,
                outcome.echoed,
                # Shadow evidence is recorded only for a document that was
                # read and not set aside whole.
                ()
                if document.reading is None
                or set(outcome.refusals) & TERMINAL_SIDECAR_REFUSALS
                else document.reading.shadow,
                outcome.echoed_narrators,
                None
                if document.reading is None or not document.reading.byte_order_mark
                else _byte_order_mark_record(document.reading, outcome, unmarked),
            )
        )
    return _capped_records(tuple(sorted(entries, key=lambda entry: entry.name)))


def _byte_order_mark_record(
    reading: AudioSidecarReading, claimed: SidecarOutcome, unmarked: SidecarOutcome
) -> AudioSidecarByteOrderMark:
    """What the shadow pass decided for one document that the claimed did not.

    Each value takes the inspection fact's own bounds; one that fails them is
    dropped and marked omitted, so building a record never fails an item.
    """
    if reading.stripped_limit is AudioSidecarStrippedLimit.CHILD_OUTPUT:
        return AudioSidecarByteOrderMark(omitted=reading.stripped_limit)
    kept = {fact_value_key(fact) for fact in claimed.facts}
    facts: list[AudioSidecarByteOrderMarkFact] = []
    omitted = False
    for fact in unmarked.facts:
        if fact_value_key(fact) in kept:
            continue
        try:
            entry = AudioSidecarByteOrderMarkFact.of(fact)
        except ValueError:
            omitted = True
            continue
        if entry not in facts:
            facts.append(entry)
    try:
        return AudioSidecarByteOrderMark(
            unmarked.refusals,
            tuple(facts[:MAX_AUDIO_FACTS]),
            omitted or len(facts) > MAX_AUDIO_FACTS,
            reading.stripped_limit,
        )
    except ValueError:
        return AudioSidecarByteOrderMark(facts_omitted=True)


def _capped_records(
    entries: tuple[AudioSidecarDigest, ...],
) -> tuple[AudioSidecarDigest, ...]:
    """Hold an item's records to `MAX_FACTS` facts and their byte bound (#1796).

    Filled in manifest order; an entry that loses facts says so. Every
    record's envelope, including one with no facts, is reserved before any
    fact is placed, so a later record can never take the total past the bound.
    """
    facts_left = MAX_AUDIO_FACTS
    bytes_left = MAX_BYTE_ORDER_MARK_BYTES - sum(
        _serialized_bytes(
            # `false` is the longer literal, so this is the larger envelope.
            dataclasses.replace(
                entry.byte_order_mark, facts=(), facts_omitted=False
            ).to_data()
        )
        for entry in entries
        if entry.byte_order_mark is not None
    )
    capped: list[AudioSidecarDigest] = []
    for entry in entries:
        mark = entry.byte_order_mark
        if mark is None or not mark.facts:
            capped.append(entry)
            continue
        kept: list[AudioSidecarByteOrderMarkFact] = []
        for fact in mark.facts:
            size = _serialized_bytes(fact.to_data()) + 1
            if len(kept) >= facts_left or size > bytes_left:
                break
            kept.append(fact)
            bytes_left -= size
        facts_left -= len(kept)
        capped.append(
            entry
            if len(kept) == len(mark.facts)
            else dataclasses.replace(
                entry,
                byte_order_mark=dataclasses.replace(
                    mark, facts=tuple(kept), facts_omitted=True
                ),
            )
        )
    return tuple(capped)


def _serialized_bytes(value: dict[str, object]) -> int:
    return len(canonical_json(value).encode("utf-8"))


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
    own_file_names: tuple[str, ...] | None,
) -> list[_OpenedSidecar]:
    """Derive, open, digest and classify the recognized documents beside an item.

    This is the single candidate-set derivation: inspection passes the
    descriptors to the isolated children, and revalidation compares the
    digests and classifications.  Two derivations would be two answers to
    "which documents is this evidence made of", and only one of them could be
    right.

    Documents named for a track are opened first, then the target's own
    record, then folder-level documents, each group in sorted order, so other
    items' documents in a shared folder cannot crowd out the item's own
    (#1481).  The target's record is refused `target_record`; a folder-level
    document beside another item's audio or ebook file is refused
    `shared_folder`.  Both are digested for currentness and never read.

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
    shared = shared_folder(names, own_file_names)
    classified = [
        (name, value)
        for name in names
        if (value := classify_sidecar(name, media_names)) is not None
    ]
    groups = (
        [
            entry
            for entry in classified
            if entry[1].binding is AudioSidecarBinding.TRACK
        ],
        [
            entry
            for entry in classified
            if entry[1].kind is AudioSidecarKind.AUDIOBOOKSHELF_RECORD
        ],
        [
            entry
            for entry in classified
            if entry[1].binding is AudioSidecarBinding.FOLDER
            and entry[1].kind is not AudioSidecarKind.AUDIOBOOKSHELF_RECORD
        ],
    )
    opened: list[_OpenedSidecar] = []
    for name, value in (entry for group in groups for entry in group):
        if len(opened) >= limits.sidecar_documents:
            break
        reference = f"{directory}/{name}" if directory else name
        try:
            sibling = open_local_media(mount.container_path, reference)
        except LocalMediaError:
            continue
        refusal = (
            AudioSidecarRefusal.TARGET_RECORD
            if value.kind is AudioSidecarKind.AUDIOBOOKSHELF_RECORD
            else AudioSidecarRefusal.SHARED_FOLDER
            if value.binding is AudioSidecarBinding.FOLDER and shared
            else None
        )
        try:
            digest = sha256_descriptor(
                sibling.descriptor, sibling.length, maximum=limits.sidecar_bytes
            )
            entry = _OpenedSidecar(
                name,
                value.kind,
                value.binding,
                value.track,
                sibling.descriptor,
                digest,
                refusal,
            )
        except (LocalMediaError, OSError, ValueError):
            os.close(sibling.descriptor)
            continue
        try:
            AudioSidecarDigest(
                entry.name,
                entry.digest,
                entry.kind,
                entry.binding,
                () if refusal is None else (refusal,),
            )
        except ValueError:
            # A name the retained report cannot project is a name the manifest
            # cannot carry, so the document is not part of the candidate set on
            # either path rather than on one of them.
            os.close(sibling.descriptor)
            continue
        opened.append(entry)
    return opened
