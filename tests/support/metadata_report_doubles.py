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
    AudioEmbeddedCover,
    AudioSidecarDigest,
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
        # What the mount holds now, which starts as what inspection read and
        # can be reassigned to stage a sidecar added, removed, or edited since.
        self.current_sidecars = result.audio.sidecars

    def inspect(self, mount, relative_path, maximum_bytes):
        self.inspections += 1
        self.ceilings.append(maximum_bytes)
        return self.result

    def unchanged(
        self,
        mount,
        relative_path,
        expected_digest,
        maximum_bytes,
        expected_sidecars=(),
    ):
        self.verifications += 1
        return (
            expected_digest == self.result.content_digest
            and tuple(expected_sidecars) == self.current_sidecars
        )


_NO_EMBEDDED_COVER = AudioEmbeddedCover(False)


def audio_result(
    facts: tuple[tuple[ClaimKind, str, str, str | None], ...] = (),
    *,
    content_digest: str = "d" * 64,
    chapters: tuple[AudioChapter, ...] = (),
    chapters_truncated: bool = False,
    embedded_cover: AudioEmbeddedCover = _NO_EMBEDDED_COVER,
    sidecars: tuple[AudioSidecarDigest, ...] = (),
) -> AudioInspectionResult:
    """Build one audiobook evidence set from ``(kind, value, basis, role)`` facts."""
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
        AudioFacts(chapters, chapters_truncated, embedded_cover, sidecars),
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
