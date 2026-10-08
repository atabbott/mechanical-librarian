"""Real cover producer and SQLite publication for retention proofs."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.application.cover_consideration import (
    CoverConsiderationService,
)
from mechanical_librarian.domain.metadata_report import MetadataReport
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from tests.support.cover_producers import _qualified_cover_report


@contextmanager
def retained_cover_case(
    root: Path,
) -> Iterator[
    tuple[
        SQLiteMetadataReportRepository,
        CoverConsiderationService,
        ReviewCase,
        MetadataReport,
    ]
]:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    report, _ = _qualified_cover_report(root)
    sources = report.cover_sources
    artifacts = CoverArtifactStore(root / "artifacts")
    repository = open_repository(root / "application.sqlite3")
    try:
        repository.publish(report, lambda: True)
        proposal = evaluate_report(report)
        repository.publish_proposal(proposal)
        case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
        case = ReviewCase.create_from_data(
            json.loads(repository.get_review_case(case_id))["case"]
        )
        service = CoverConsiderationService(
            repository,
            read_preview=lambda artifact_id, _variant: next(
                artifacts.preview(source.artifact)
                for source in sources
                if source.artifact is not None
                and source.artifact.artifact_id == artifact_id
            ),
            read_preview_digest=artifacts.read_preview_digest,
            clock=lambda: 10,
        )
        service.ensure(
            case_id,
            report.to_json(),
            next(value for value in case.proposals if value["destination"] == "cover"),
        )
        yield repository, service, case, report
    finally:
        repository.close()
