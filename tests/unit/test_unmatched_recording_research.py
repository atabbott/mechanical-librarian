"""An audiobook that admitted no recording is searched again (#1451).

The discovery scan keeps a finished item whose file has not moved, so before
this an unmatched audiobook was never asked again. Its work now carries a mark
that brings it back to extraction once `RESEARCH_INTERVAL_SECONDS` has passed.
A search that still admits no recording leaves an open case as it was; one
that admits a recording flows through like any refreshed preparation.

The persistence mechanics are media-blind, so the case-bearing tests drive the
synthetic ebook cohort through the real pipeline and say what the publication
receipt was told. Which reports are unmatched is the composition root's
reading of `recording_admitted`, tested beside it.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import (
    open_repository,
)
from mechanical_librarian.adapters.persistence.review_preparation import (
    RESEARCH_INTERVAL_SECONDS,
)
from mechanical_librarian.application.catalogue import CatalogueEntry
from mechanical_librarian.application.review_pipeline import (
    PipelineAction,
    ReviewPreparationPipeline,
)
from mechanical_librarian.domain.audiobook_corroboration import recording_admitted
from mechanical_librarian.domain.metadata_report import (
    FileFacts,
    MetadataReport,
    TargetFacts,
    default_target_fields,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from tests.support.metadata_report_doubles import single_track_audio_facts
from tests.support.review_cases import _report
from tests.unit.test_review_pipeline import (
    _PRODUCER,
    _audio_entry,
    _entry,
    _install_settings_authority,
)

_WEEK = RESEARCH_INTERVAL_SECONDS


@pytest.fixture
def application_path() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "application.sqlite3"
        repository = open_repository(path)
        _install_settings_authority(repository)
        repository.enter_shadow_preview(1, ())
        repository.close()
        yield path


def _pipeline(
    path: Path, report: MetadataReport, now: int, *, unmatched: bool
) -> ReviewPreparationPipeline:
    proposal = evaluate_report(report)
    entry: CatalogueEntry = replace(
        _entry("item-1", report.file.identity), library="ebooks"
    )

    def extract(repository, action: PipelineAction, claim_next: bool):
        # As preparation does, the item's current report is the one replaced.
        current = repository.current_optional(
            "primary", "ebooks", "item-1", report.file.identity
        )
        repository.publish(
            report,
            lambda: True,
            None if current is None else MetadataReport.from_json(current).report_id,
            after_publication=lambda bound, current: (
                bound.complete_review_extraction_publication(
                    action,
                    current.report_id,
                    now,
                    0,
                    None,
                    claim_next,
                    None,
                    unmatched,
                )
            ),
        )
        return report.report_id, True

    def propose(repository, _: str) -> str:
        repository.publish_proposal(proposal)
        return proposal.proposal_set_id

    def admit(repository, _: str) -> tuple[str, str | None]:
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


def _drain(pipeline: ReviewPreparationPipeline) -> int:
    pipeline.request("primary", "ebooks")
    steps = 0
    while pipeline.run_next(1):
        steps += 1
        assert steps < 20
    return steps


def _state(path: Path) -> tuple[object, ...]:
    repository = open_repository(path)
    try:
        work = repository._connection.execute(
            "SELECT stage,state,outcome,report_id,review_case_id,research_not_before "
            "FROM review_preparation_work"
        ).fetchone()
        current = repository._connection.execute(
            "SELECT report_id FROM current_metadata_reports"
        ).fetchone()[0]
        lineage = repository._connection.execute(
            "SELECT report_id FROM current_metadata_report_lineages"
        ).fetchone()[0]
        package = repository._connection.execute(
            "SELECT report_id,review_case_id FROM current_review_packages"
        ).fetchone()
    finally:
        repository.close()
    return work, current, lineage, package


def test_an_unchanged_research_keeps_the_open_case(application_path: Path) -> None:
    old = _report("Prepared once", "a", "b")
    new = _report("Searched again", "a", "c")
    assert old.file.identity == new.file.identity

    _drain(_pipeline(application_path, old, 20, unmatched=True))
    work, current, _lineage, package = _state(application_path)
    case = work[4]
    assert work == ("complete", "complete", "case", old.report_id, case, 20 + _WEEK)
    assert current == old.report_id
    assert package == (old.report_id, case)

    # Before the interval, a scan finds nothing to do.
    assert 1 == _drain(_pipeline(application_path, new, 20 + _WEEK - 1, unmatched=True))
    assert _state(application_path)[0] == work

    # Once it has passed, the item is extracted again. Still unmatched, the
    # new report is kept only as history: the case and its report stay
    # current, and the next search waits another interval.
    later = 20 + _WEEK
    assert 2 == _drain(_pipeline(application_path, new, later, unmatched=True))
    work, current, lineage, package = _state(application_path)
    assert work == ("complete", "complete", "case", old.report_id, case, later + _WEEK)
    assert current == lineage == old.report_id
    assert package == (old.report_id, case)
    repository = open_repository(application_path)
    assert json.loads(repository.get_review_case(case))["current"]
    assert repository.get(new.report_id)
    repository.close()

    # And the scan after that rests on the kept case: no work is queued.
    assert 1 == _drain(_pipeline(application_path, new, later + 1, unmatched=True))


def test_a_research_that_admits_a_recording_replaces_the_case(
    application_path: Path,
) -> None:
    old = _report("Prepared once", "a", "b")
    new = _report("Matched later", "a", "c")
    _drain(_pipeline(application_path, old, 20, unmatched=True))
    old_case = _state(application_path)[0][4]

    later = 20 + _WEEK
    assert 5 == _drain(_pipeline(application_path, new, later, unmatched=False))
    work, current, _lineage, package = _state(application_path)

    assert work[:4] == ("complete", "complete", "case", new.report_id)
    assert work[4] != old_case
    # A matched report is not searched again.
    assert work[5] is None
    assert current == new.report_id
    assert package == (new.report_id, work[4])


def test_a_matched_preparation_is_never_searched_again(
    application_path: Path,
) -> None:
    report = _report("Matched first time", "a", "b")
    _drain(_pipeline(application_path, report, 20, unmatched=False))
    work = _state(application_path)[0]
    assert work[5] is None

    assert 1 == _drain(
        _pipeline(application_path, report, 20 + 10 * _WEEK, unmatched=False)
    )
    assert _state(application_path)[0] == work


def _audio_report(title: str) -> MetadataReport:
    return MetadataReport.create(
        target=TargetFacts("primary", "fiction", "item", default_target_fields(title)),
        file=FileFacts(0, "1" * 64, "b" * 64, "m4b"),
        local_claims=(),
        local_reasons=("no_local_audio_claims",),
        providers=(),
        audio=single_track_audio_facts(),
    )


def _audio_scan(path: Path, report: MetadataReport, at: int) -> PipelineAction | None:
    repository = open_repository(path)
    try:
        repository.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, at)
        discovery = repository.claim_review_pipeline_action(at + 1)
        assert discovery is not None and discovery.stage == "discovery"
        repository.complete_review_discovery(
            discovery,
            1,
            "a" * 64,
            (_audio_entry("item", report.file.identity),),
            at + 2,
        )
        extraction = repository.claim_review_pipeline_action(at + 3)
        if extraction is None:
            return None
        current = repository.current_optional(
            "primary", "fiction", "item", report.file.identity
        )
        repository.publish(
            report,
            lambda: True,
            None if current is None else MetadataReport.from_json(current).report_id,
            lambda value, published: value.complete_review_extraction_publication(
                extraction,
                published.report_id,
                at + 4,
                0,
                "evidence_only",
                recording_unmatched=not recording_admitted(report),
            ),
        )
        assert repository.claim_review_pipeline_action(at + 5) is None
        return extraction
    finally:
        repository.close()


def test_an_unmatched_audiobook_without_a_case_takes_the_new_evidence(
    application_path: Path,
) -> None:
    first = _audio_report("Recorded work")
    second = _audio_report("Recorded again")
    assert not recording_admitted(first)

    assert _audio_scan(application_path, first, 10) is not None
    work, current, _lineage, package = _state(application_path)
    assert work == (
        "complete",
        "complete",
        "evidence_only",
        first.report_id,
        None,
        14 + _WEEK,
    )
    assert package == (first.report_id, None)

    assert _audio_scan(application_path, second, 20) is None

    assert _audio_scan(application_path, second, 14 + _WEEK) is not None
    work, current, _lineage, package = _state(application_path)
    # No case to protect: the fresher evidence is the item's report.
    assert work == (
        "complete",
        "complete",
        "evidence_only",
        second.report_id,
        None,
        18 + 2 * _WEEK,
    )
    assert current == second.report_id
    assert package == (second.report_id, None)


def test_a_changed_file_clears_the_research_mark(application_path: Path) -> None:
    first = _audio_report("Recorded work")
    _audio_scan(application_path, first, 10)
    repository = open_repository(application_path)
    try:
        repository.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, 20)
        discovery = repository.claim_review_pipeline_action(21)
        assert discovery is not None
        repository.complete_review_discovery(
            discovery,
            1,
            "a" * 64,
            (
                replace(
                    _audio_entry("item", first.file.identity),
                    source_fingerprint="9" * 64,
                ),
            ),
            22,
        )
        work = repository._connection.execute(
            "SELECT stage,state,research_not_before FROM review_preparation_work"
        ).fetchone()
    finally:
        repository.close()
    assert work == ("extraction", "queued", None)
