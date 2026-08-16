"""Shared metadata-report collaborators.

The target reader, EPUB inspector, and report repository the generation
cohorts compose against. They live here so each concern-scoped module
builds the same application without redefining its collaborators.
"""

from __future__ import annotations

from mechanical_librarian.application.metadata_report import (
    CurrentnessError,
    InspectionResult,
    TargetSnapshot,
)
from mechanical_librarian.domain.metadata_report import (
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

    def inspect(self, mount, relative_path, maximum_bytes):
        self.inspections += 1
        return self.result

    def unchanged(self, mount, relative_path, expected_digest, maximum_bytes):
        self.verifications += 1
        return expected_digest == self.result.content_digest


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
