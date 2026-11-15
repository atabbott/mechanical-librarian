"""Preparing an item again from the same answers converges (#1064, R1).

A reused provider answer brings back the time it was fetched, so a second
preparation of an unchanged item yields the byte-identical report. This drives
one ebook through the real review pipeline twice with a fixed transport and
clock and shows the second publish, proposal and admission land on the same
report, proposal and case; a different clock is the negative control.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import (
    NonClosingRepository,
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.adapters.persistence.review_preparation import (
    mark_preparation_work_fresh,
)
from mechanical_librarian.adapters.providers.google_books_metadata import (
    GoogleBooksMetadataProvider,
)
from mechanical_librarian.application.configuration import (
    LibraryConfiguration,
    MediaMountConfiguration,
    ReportLimits,
    TargetConnectionConfiguration,
)
from mechanical_librarian.application.metadata_report import (
    GenerateMetadataReport,
    InspectionResult,
    ProviderBinding,
    TargetFile,
    TargetSnapshot,
)
from mechanical_librarian.application.review_pipeline import (
    PipelineAction,
    ReviewPreparationPipeline,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    LocalClaim,
    MetadataReport,
)
from mechanical_librarian.domain.proposal_set import ProposalSet, evaluate_report
from tests.support.metadata_report_doubles import FakeInspector, FakeTarget
from tests.unit.test_review_pipeline import (
    _PRODUCER,
    _entry,
    _install_settings_authority,
)

_FILE = "a" * 64


@pytest.fixture
def application_path() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "application.sqlite3"
        repository = open_repository(path)
        _install_settings_authority(repository)
        repository.enter_shadow_preview(1, ())
        repository.close()
        yield path


def _answer(_: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"content-type": "application/json"},
        json={
            "totalItems": 1,
            "items": [
                {
                    "id": "volume_1",
                    "volumeInfo": {
                        "title": "Provider Title",
                        "authors": ["Ada Example"],
                        "industryIdentifiers": [
                            {"type": "ISBN_13", "identifier": "9780306406157"}
                        ],
                        "language": "en",
                    },
                }
            ],
        },
    )


def _service(
    repository: SQLiteMetadataReportRepository,
    wall_clock: float,
    receipt: Callable[..., None],
) -> GenerateMetadataReport:
    claims = (
        LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="9780306406157",
            canonical="9780306406157",
            excerpt="ISBN 9780306406157",
            applicability="ebook",
            basis="opening_matter",
            checksum="valid_isbn_13",
            applicability_basis="ebook",
            scope=ClaimScope.EDITION,
        ),
        LocalClaim.create(
            kind=ClaimKind.TITLE,
            observed="Opening Title",
            canonical="Opening Title",
            excerpt="Opening Title",
            applicability="not_applicable",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            scope=ClaimScope.WORK,
        ),
    )
    snapshot = TargetSnapshot(
        "ebook", "Synthetic work", (TargetFile(_FILE, "item-1.epub", "epub"),)
    )
    return GenerateMetadataReport(
        target=FakeTarget([snapshot] * 8),
        inspector=FakeInspector(InspectionResult("c" * 64, claims)),
        repository_factory=lambda: NonClosingRepository(repository),
        providers=(
            ProviderBinding(
                "google_books",
                True,
                lambda: GoogleBooksMetadataProvider(
                    "google_books",
                    httpx.Client(transport=httpx.MockTransport(_answer)),
                    65_536,
                    clock=lambda: 0.0,
                    wall_clock=lambda: wall_clock,
                    sleep=lambda _: None,
                    jitter=lambda: 0.0,
                ),
                "google_books",
            ),
        ),
        connection=TargetConnectionConfiguration(
            "primary", "https://example.invalid", "/private/target"
        ),
        library=LibraryConfiguration(
            "ebooks", "primary", "lib-1", "ebook", "books", ("google_books",)
        ),
        mount=MediaMountConfiguration("books", "/media", "/library", True),
        limits=ReportLimits(),
        publication_receipt=receipt,
        require_singular_target=True,
    )


def _pipeline(
    path: Path, wall_clock: float, now: int, extracted: list[str]
) -> ReviewPreparationPipeline:
    entry = replace(_entry("item-1", _FILE), library="ebooks")

    def extract(repository, action: PipelineAction, claim_next: bool):
        serialized = _service(
            repository,
            wall_clock,
            lambda bound, report: bound.complete_review_extraction_publication(
                action, report.report_id, now, 0, None, claim_next
            ),
        ).run("item-1", 0, action.file_identity)
        extracted.append(MetadataReport.from_json(serialized).report_id)
        return extracted[-1], True

    def propose(repository, report_id: str) -> str:
        proposal = evaluate_report(MetadataReport.from_json(repository.get(report_id)))
        repository.publish_proposal(proposal)
        return proposal.proposal_set_id

    def admit(repository, proposal_set_id: str) -> tuple[str, str | None]:
        proposal = ProposalSet.from_json(repository.get_proposal(proposal_set_id))
        value = json.loads(repository.admit_review_case(proposal))
        return value["status"], value.get("review_case_id")

    def project(repository, action: PipelineAction) -> str:
        outcome = repository.persist_shadow_outcome(
            action.review_case_id or "", now, staged_action=action
        )
        return "stale" if outcome == "stale" else "complete"

    return ReviewPreparationPipeline(
        repository_factory=lambda: open_repository(path),
        libraries=(("primary", "ebooks", "lib-1", _PRODUCER),),
        discover=lambda *_: (1, "a" * 64, (entry,)),
        extract=extract,
        providers=lambda *_: (),
        propose=propose,
        admit=admit,
        project=project,
        clock=lambda: now,
    )


def _drain(pipeline: ReviewPreparationPipeline) -> None:
    pipeline.request("primary", "ebooks")
    steps = 0
    while pipeline.run_next(1):
        steps += 1
        assert steps < 20


def _state(path: Path) -> tuple[object, ...]:
    repository = open_repository(path)
    try:
        return repository._connection.execute(
            "SELECT stage,state,outcome,report_id,proposal_set_id,review_case_id "
            "FROM review_preparation_work"
        ).fetchone()
    finally:
        repository.close()


def _refresh(path: Path) -> None:
    repository = open_repository(path)
    try:
        mark_preparation_work_fresh(
            repository._connection, "primary", "ebooks", "item-1"
        )
    finally:
        repository.close()


def test_a_second_preparation_from_the_same_answers_converges(
    application_path: Path,
) -> None:
    extracted: list[str] = []
    _drain(_pipeline(application_path, 1_700_000_000.25, 20, extracted))
    first = _state(application_path)
    assert first[:3] == ("complete", "complete", "case")
    assert None not in first

    _refresh(application_path)
    _drain(_pipeline(application_path, 1_700_000_000.25, 30, extracted))
    # The item was extracted again, to the same report, and the publish,
    # proposal and admission reselected what was already current.
    assert extracted == [first[3], first[3]]
    assert _state(application_path) == first


def test_a_different_access_time_names_a_different_report(
    application_path: Path,
) -> None:
    extracted: list[str] = []
    _drain(_pipeline(application_path, 1_700_000_000.25, 20, extracted))
    first = _state(application_path)

    _refresh(application_path)
    _drain(_pipeline(application_path, 1_700_000_001.25, 30, extracted))
    second = _state(application_path)
    assert second[:3] == ("complete", "complete", "case")
    assert extracted == [first[3], second[3]]
    assert second[3] != first[3]
