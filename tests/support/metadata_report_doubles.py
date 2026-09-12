"""Shared metadata-report collaborators.

The target reader, EPUB inspector, and report repository the generation
cohorts compose against. They live here so each concern-scoped module
builds the same application without redefining its collaborators.
"""

from __future__ import annotations

from mechanical_librarian.application.metadata_report import (
    AudioInspectionResult,
    CurrentnessError,
    InspectionResult,
    TargetSnapshot,
)
from mechanical_librarian.domain.audio_inspection import (
    AudioChapter,
    AudioChapterComposition,
    AudioEmbeddedCover,
    AudioObservation,
    AudioSidecarDigest,
    AudioTrackDigest,
)
from mechanical_librarian.domain.metadata_report import (
    AudioFacts,
    ClaimKind,
    LocalClaim,
    MetadataReport,
)


class FakeTarget:
    def __init__(self, snapshots: list[TargetSnapshot]) -> None:
        self.snapshots = snapshots
        self.calls = 0

    def read(self, item: str) -> TargetSnapshot:
        value = self.snapshots[self.calls]
        self.calls += 1
        return value


class FakeInspector:
    def __init__(self, result: InspectionResult) -> None:
        self.result = result
        self.inspections = 0
        self.verifications = 0
        # The ceiling each call was handed: the EPUB and audio paths draw
        # theirs from different settings and only the argument shows which.
        self.ceilings: list[int] = []

    def inspect(self, mount, relative_path, maximum_bytes):
        self.inspections += 1
        self.ceilings.append(maximum_bytes)
        return self.result

    def unchanged(self, mount, relative_path, expected_digest, maximum_bytes):
        self.verifications += 1
        return expected_digest == self.result.content_digest


class FakeAudioInspector:
    """The audiobook counterpart of ``FakeInspector``, with no cover authority."""

    def __init__(self, result: AudioInspectionResult) -> None:
        self.result = result
        self.inspections = 0
        self.verifications = 0
        self.ceilings: list[int] = []
        self.tracks: list[tuple[str, ...]] = []
        # What the mount holds now, which starts as what inspection read and
        # can be reassigned to stage a sidecar added, removed, or edited since,
        # or a track whose bytes were rewritten under an unchanged manifest.
        self.current_sidecars = result.audio.sidecars
        self.current_digest = result.content_digest

    def inspect(self, mount, tracks, maximum_bytes):
        self.inspections += 1
        self.ceilings.append(maximum_bytes)
        self.tracks.append(tuple(value.relative_path for value in tracks))
        return self.result

    def unchanged(
        self,
        mount,
        relative_paths,
        expected_digest,
        maximum_bytes,
        expected_sidecars=(),
        expected_tracks=(),
    ):
        self.verifications += 1
        return (
            expected_digest == self.current_digest
            and tuple(expected_sidecars) == self.current_sidecars
        )


_NO_EMBEDDED_COVER = AudioEmbeddedCover(False)


def single_track_audio_facts(
    content_digest: str = "b" * 64,
    *,
    chapters: tuple[AudioChapter, ...] = (),
    chapter_rows_omitted: bool = False,
    embedded_cover: AudioEmbeddedCover = _NO_EMBEDDED_COVER,
    file_format: str = "m4b",
    name: str = "book.m4b",
) -> AudioFacts:
    """Build the one-track audio section a single-file report publishes.

    Every audio section names the file it was composed from, and the report
    checks that the manifest composes its own content digest, so a report
    double states the digest it was built with here too.
    """
    return AudioFacts(
        chapters=chapters,
        chapter_rows_omitted=chapter_rows_omitted,
        embedded_cover=embedded_cover,
        tracks=(AudioTrackDigest(1, "a" * 64, content_digest, file_format, name),),
    )


def audio_result(
    facts: tuple[tuple[ClaimKind, str, str, str | None], ...] = (),
    *,
    content_digest: str = "d" * 64,
    chapters: tuple[AudioChapter, ...] = (),
    chapter_rows_omitted: bool = False,
    chapters_truncated: bool = False,
    embedded_cover: AudioEmbeddedCover = _NO_EMBEDDED_COVER,
    sidecars: tuple[AudioSidecarDigest, ...] = (),
    tracks: tuple[AudioTrackDigest, ...] | None = None,
    composition: AudioChapterComposition = AudioChapterComposition.SINGLE_FILE,
    observations: tuple[AudioObservation, ...] = (),
) -> AudioInspectionResult:
    """Build one audiobook evidence set from ``(kind, value, basis, role)`` facts.

    The default manifest is the one-track item the single-file cases use, so a
    double still produces a document the report invariants accept: a published
    audio section always names the files it was composed from.
    """
    return AudioInspectionResult(
        content_digest,
        tuple(
            LocalClaim.create(
                kind=kind,
                observed=value,
                canonical=value,
                excerpt=value,
                applicability="not_applicable",
                basis=basis,
                checksum="not_applicable",
                applicability_basis="not_applicable",
                role=role,
                occurrence=occurrence,
            )
            for occurrence, (kind, value, basis, role) in enumerate(facts, 1)
        ),
        AudioFacts(
            chapters,
            chapters_truncated,
            embedded_cover,
            sidecars,
            composition,
            (
                (AudioTrackDigest(1, "a" * 64, content_digest, "m4b", "book.m4b"),)
                if tracks is None
                else tracks
            ),
            observations,
            chapter_rows_omitted,
        ),
    )


class FakeReportRepository:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.closed = False

    def publish(
        self, report: MetadataReport, currentness, expected_current=None
    ) -> str:
        currentness()
        self.values.setdefault(report.report_id, report.to_json())
        return self.values[report.report_id]

    def get(self, report_id: str) -> str:
        return self.values[report_id]

    def current(
        self, connection: str, library: str, item: str, file_identity: str
    ) -> str:
        raise CurrentnessError("persisted current report is unavailable")

    def current_optional(
        self, connection: str, library: str, item: str, file_identity: str
    ) -> str | None:
        return None

    def close(self) -> None:
        self.closed = True
