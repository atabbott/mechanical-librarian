"""A finished work is searched again on a schedule (#1451, ADR-0102).

The discovery scan keeps a finished item whose file has not moved, so before
#1451 nothing was asked again. Every prepared work now carries a mark that
brings it back to extraction at its slot in `REFRESH_INTERVAL_SECONDS`, and an
audiobook that admitted no recording also weekly, after
`RESEARCH_INTERVAL_SECONDS`. A search whose proposals equal the current ones,
one that failed over a complete report, and a weekly search that still admits
no recording leave everything as it was; any other search flows through like
any refreshed preparation.

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
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.adapters.persistence.review_preparation import (
    REFRESH_INTERVAL_SECONDS,
    RESEARCH_INTERVAL_SECONDS,
    next_research_slot,
    preparation_work_id,
)
from mechanical_librarian.application.catalogue import CatalogueEntry
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.review_pipeline import (
    PipelineAction,
    ReviewPreparationPipeline,
)
from mechanical_librarian.domain.audiobook_corroboration import recording_admitted
from mechanical_librarian.domain.metadata_report import (
    FileFacts,
    MetadataReport,
    ProviderObservation,
    ProviderStatus,
    TargetFacts,
    default_target_fields,
    provider_access_time,
)
from mechanical_librarian.domain.proposal_set import ProposalSet, evaluate_report
from tests.support.metadata_report_doubles import single_track_audio_facts
from tests.support.review_cases import _report
from tests.unit.test_review_pipeline import (
    _PRODUCER,
    _audio_entry,
    _entry,
    _install_settings_authority,
)

_WEEK = RESEARCH_INTERVAL_SECONDS
_PERIOD = REFRESH_INTERVAL_SECONDS
# The one work of the synthetic ebook cohort. Its phase is about 41 days into
# the period, so a weekly mark set near time zero comes before its slot.
_WORK = preparation_work_id("primary", "ebooks", "item-1")


@pytest.fixture
def application_path() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "application.sqlite3"
        repository = open_repository(path)
        _install_settings_authority(repository)
        repository.enter_shadow_preview(1, ())
        repository.close()
        yield path


def _restamped(report: MetadataReport, accessed: int) -> MetadataReport:
    """The same evidence, asked live at another time: a new report id."""
    return MetadataReport.create(
        target=report.target,
        file=report.file,
        local_claims=report.local_claims,
        local_reasons=report.local_reasons,
        providers=tuple(
            replace(value, accessed_at=provider_access_time(accessed))
            for value in report.providers
        ),
        cover_sources=report.cover_sources,
        audio=report.audio,
        companion=report.companion,
        library_languages=report.library_languages,
    )


def _failed(report: MetadataReport) -> MetadataReport:
    """The same search with one more provider that could not be reached."""
    return MetadataReport.create(
        target=report.target,
        file=report.file,
        local_claims=report.local_claims,
        local_reasons=report.local_reasons,
        providers=(
            *report.providers,
            ProviderObservation(
                "open_library_primary",
                ProviderStatus.UNAVAILABLE,
                authority="open_library",
            ),
        ),
    )


def _item_report(item: str, title: str) -> MetadataReport:
    report = _report(title, "a", "b")
    return MetadataReport.create(
        target=TargetFacts("primary", "ebooks", item, default_target_fields(title)),
        file=report.file,
        local_claims=report.local_claims,
        local_reasons=report.local_reasons,
        providers=report.providers,
    )


def _pipeline(
    path: Path,
    report: MetadataReport | tuple[MetadataReport, ...],
    now: int,
    *,
    unmatched: bool,
    researched: list[bool] | None = None,
    disposition: str | None = None,
) -> ReviewPreparationPipeline:
    reports = {
        value.target.item: value
        for value in (report if isinstance(report, tuple) else (report,))
    }
    entries: tuple[CatalogueEntry, ...] = tuple(
        replace(_entry(item, value.file.identity), library="ebooks")
        for item, value in reports.items()
    )

    def extract(repository, action: PipelineAction, claim_next: bool):
        if researched is not None:
            researched.append(action.research)
        report = reports[action.item_id]
        # As preparation does, the item's current report is the one replaced.
        current = repository.current_optional(
            "primary", "ebooks", action.item_id, report.file.identity
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
                    disposition,
                    claim_next,
                    None,
                    unmatched,
                )
            ),
        )
        return report.report_id, True

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
        discover=lambda *_: (1, "a" * 64, entries),
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

    researched: list[bool] = []
    _drain(_pipeline(application_path, old, 20, unmatched=True, researched=researched))
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
    assert 2 == _drain(
        _pipeline(application_path, new, later, unmatched=True, researched=researched)
    )
    # Only the re-search is claimed as one, so only it asks providers live
    # instead of reusing a held answer (#1064).
    assert researched == [False, True]
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


def test_a_research_claimed_by_its_work_id_is_a_research_too(
    application_path: Path,
) -> None:
    """Both claim queries name a re-search, so neither reuses a held answer."""
    report = _report("Prepared once", "a", "b")
    _drain(_pipeline(application_path, report, 20, unmatched=True))
    later = 20 + _WEEK
    entry = replace(_entry("item-1", report.file.identity), library="ebooks")
    repository = open_repository(application_path)
    try:
        repository.request_review_scan("primary", "ebooks", "lib-1", _PRODUCER, later)
        discovery = repository.claim_review_pipeline_action(later)
        assert discovery is not None and discovery.stage == "discovery"
        assert not discovery.research
        repository.complete_review_discovery(discovery, 1, "a" * 64, (entry,), later)
        (work_id,) = repository._connection.execute(
            "SELECT work_id FROM review_preparation_work"
        ).fetchone()
        action = repository.claim_review_pipeline_work(work_id, later)
    finally:
        repository.close()
    assert action is not None and action.stage == "extraction"
    assert action.research is True


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
    # A matched report is searched again at its slot, not weekly.
    assert work[5] == next_research_slot(_WORK, later + _WEEK)
    assert current == new.report_id
    assert package == (new.report_id, work[4])


def test_a_matched_preparation_comes_due_at_its_slot(
    application_path: Path,
) -> None:
    report = _report("Matched first time", "a", "b")
    researched: list[bool] = []
    _drain(
        _pipeline(application_path, report, 20, unmatched=False, researched=researched)
    )
    work = _state(application_path)[0]
    due = work[5]
    assert type(due) is int
    # Never within a week of being prepared, and never more than a period
    # after that week, at the work's own phase.
    assert due == next_research_slot(_WORK, 20 + _WEEK)
    assert 20 + _WEEK < due <= 20 + _WEEK + _PERIOD
    assert due % _PERIOD == int(_WORK[-16:], 16) % _PERIOD

    # Until then a scan finds nothing to do.
    assert 1 == _drain(_pipeline(application_path, report, due - 1, unmatched=False))
    assert _state(application_path)[0] == work

    # At its slot the work is extracted again and asks live (#1064). The same
    # evidence asked again keeps everything, and the next slot is a period on.
    again = _restamped(report, 1)
    assert again.report_id != report.report_id
    assert 2 == _drain(
        _pipeline(application_path, again, due, unmatched=False, researched=researched)
    )
    assert researched == [False, True]
    assert _state(application_path)[0] == (*work[:5], due + _PERIOD)


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


def _stage_successor(path: Path, report: MetadataReport, at: int) -> object:
    repository = open_repository(path)
    try:
        target = report.target
        current = repository.current_optional(
            target.connection, target.library, target.item, report.file.identity
        )
        assert current is not None
        source = MetadataReport.from_json(current).report_id
        repository.publish(
            report,
            lambda: True,
            source,
            after_publication=lambda bound, value: bound.stage_provider_retry_successor(
                source, value, at
            ),
            expected_package=repository.current_review_package(
                target.connection, target.library, target.item
            ),
        )
        return repository._connection.execute(
            "SELECT stage,research_not_before FROM review_preparation_work"
        ).fetchone()
    finally:
        repository.close()


def test_a_matched_provider_retry_successor_waits_for_its_slot(
    application_path: Path,
) -> None:
    old = _report("Prepared once", "a", "b")
    _drain(_pipeline(application_path, old, 20, unmatched=True))
    assert _state(application_path)[0][5] == 20 + _WEEK

    # An ebook successor is never unmatched, as a recovered audiobook that
    # now admits a recording is not: it is searched again at its slot.
    assert _stage_successor(application_path, _report("Recovered", "a", "c"), 30) == (
        "provider",
        next_research_slot(_WORK, 30 + _WEEK),
    )


def test_an_unmatched_provider_retry_successor_waits_its_own_interval(
    application_path: Path,
) -> None:
    _audio_scan(application_path, _audio_report("Recorded work"), 10)
    assert _state(application_path)[0][5] == 14 + _WEEK

    assert _stage_successor(application_path, _audio_report("Recovered"), 30) == (
        "provider",
        30 + _WEEK,
    )


def test_a_slot_is_the_next_time_at_the_works_phase() -> None:
    """The slot is strictly after `t`, at the phase, within one period."""
    for item in range(40):
        work_id = preparation_work_id("primary", "ebooks", f"item-{item}")
        phase = int(work_id[-16:], 16) % _PERIOD
        for after in (0, 1, phase - 1, phase, phase + 1, 7 * _PERIOD + 12_345):
            slot = next_research_slot(work_id, after)
            assert after < slot <= after + _PERIOD
            assert slot % _PERIOD == phase
            # The next one is exactly one period on, and a slot is its own.
            assert next_research_slot(work_id, slot) == slot + _PERIOD
            assert next_research_slot(work_id, slot - 1) == slot


def test_slots_spread_evenly_across_the_period() -> None:
    """About a ninetieth of the works come due on each day of the period."""
    days = [0] * 90
    for item in range(9_000):
        work_id = preparation_work_id("primary", "ebooks", f"item-{item}")
        days[next_research_slot(work_id, 0) % _PERIOD // 86_400] += 1
    # 100 a day in expectation; a binomial spread stays well inside this.
    assert 60 < min(days) and max(days) < 140


def test_a_legacy_finished_work_takes_its_slot_at_the_next_scan(
    application_path: Path,
) -> None:
    """A work finished before scheduled searches is marked, not re-queued."""
    report = _report("Finished before", "a", "b")
    _drain(_pipeline(application_path, report, 20, unmatched=False))
    repository = open_repository(application_path)
    try:
        repository._connection.execute(
            "UPDATE review_preparation_work SET research_not_before=NULL"
        )
    finally:
        repository.close()
    work = _state(application_path)[0]

    scanned_at = 5 * _PERIOD
    assert 1 == _drain(_pipeline(application_path, report, scanned_at, unmatched=False))
    assert _state(application_path)[0] == (
        *work[:5],
        next_research_slot(_WORK, scanned_at + _WEEK),
    )


def _case_rows(path: Path) -> tuple[object, ...]:
    repository = open_repository(path)
    try:
        return tuple(
            repository._connection.execute(query).fetchall()
            for query in (
                "SELECT * FROM current_review_case_lineages",
                "SELECT * FROM current_review_packages",
                "SELECT * FROM review_cases",
                "SELECT * FROM review_case_transitions",
                "SELECT * FROM human_decisions",
                "SELECT * FROM proposal_sets",
            )
        )
    finally:
        repository.close()


def test_a_slot_search_with_the_same_proposals_keeps_everything(
    application_path: Path,
) -> None:
    report = _report("Prepared once", "a", "b")
    _drain(_pipeline(application_path, report, 20, unmatched=False))
    work, current, lineage, package = _state(application_path)
    before = _case_rows(application_path)
    due = work[5]
    assert type(due) is int

    again = _restamped(report, 1)
    # Raw ids differ on every live ask: only the content is compared.
    assert evaluate_report(again).proposal_set_id != (
        evaluate_report(report).proposal_set_id
    )
    assert 2 == _drain(_pipeline(application_path, again, due, unmatched=False))

    # The case, its lineage, package, transitions and decisions, and both
    # report pointers are as they were; no proposal set was stored.
    assert _case_rows(application_path) == before
    assert _state(application_path) == (
        (*work[:5], next_research_slot(_WORK, due + _WEEK)),
        current,
        lineage,
        package,
    )
    # The new report is kept as history only.
    repository = open_repository(application_path)
    try:
        assert repository.get(again.report_id)
        assert json.loads(repository.get_review_case(str(work[4])))["current"]
    finally:
        repository.close()


def test_a_slot_search_with_different_proposals_takes_the_ordinary_path(
    application_path: Path,
) -> None:
    report = _report("Prepared once", "a", "b")
    _drain(_pipeline(application_path, report, 20, unmatched=False))
    work = _state(application_path)[0]
    due = work[5]
    assert type(due) is int

    changed = _report("Titled differently", "a", "b")
    assert 5 == _drain(_pipeline(application_path, changed, due, unmatched=False))
    after, current, lineage, package = _state(application_path)
    assert after[:4] == ("complete", "complete", "case", changed.report_id)
    assert after[4] != work[4]
    assert after[5] == next_research_slot(_WORK, due + _WEEK)
    assert current == lineage == changed.report_id
    assert package == (changed.report_id, after[4])
    repository = open_repository(application_path)
    try:
        transitions = repository._connection.execute(
            "SELECT kind,from_review_case_id,to_review_case_id "
            "FROM review_case_transitions ORDER BY checkpoint"
        ).fetchall()
    finally:
        repository.close()
    assert transitions[-1] == ("superseded", work[4], after[4])


def test_a_failed_search_over_a_complete_report_replaces_nothing(
    application_path: Path,
) -> None:
    report = _report("Prepared once", "a", "b")
    _drain(_pipeline(application_path, report, 20, unmatched=False))
    work, current, lineage, package = _state(application_path)
    before = _case_rows(application_path)
    due = work[5]
    assert type(due) is int

    # Even a search that would propose something else changes nothing when a
    # provider failed, and it is asked again within the week.
    failed = _failed(_report("Titled differently", "a", "b"))
    assert 2 == _drain(_pipeline(application_path, failed, due, unmatched=False))
    assert _case_rows(application_path) == before
    assert _state(application_path) == (
        (*work[:5], due + _WEEK),
        current,
        lineage,
        package,
    )


def test_a_failed_search_over_an_incomplete_report_takes_the_ordinary_path(
    application_path: Path,
) -> None:
    report = _failed(_report("Prepared once", "a", "b"))
    _drain(_pipeline(application_path, report, 20, unmatched=False))
    work = _state(application_path)[0]
    due = work[5]
    assert type(due) is int

    failed = _failed(_report("Titled differently", "a", "b"))
    assert 5 == _drain(_pipeline(application_path, failed, due, unmatched=False))
    after, current, _lineage, package = _state(application_path)
    assert after[:4] == ("complete", "complete", "case", failed.report_id)
    assert after[4] != work[4]
    assert current == failed.report_id
    assert package == (failed.report_id, after[4])


def test_an_unmatched_audiobook_takes_the_content_rule_on_its_slot(
    application_path: Path,
) -> None:
    """Its weekly search keeps a case that admits no recording; its slot
    search keeps only what proposes the same (ADR-0089, ADR-0102)."""
    phase = int(_WORK[-16:], 16) % _PERIOD
    # Prepared three days before its slot, so the slot comes before the week.
    prepared = phase - 3 * 86_400
    report = _report("Prepared once", "a", "b")
    _drain(_pipeline(application_path, report, prepared, unmatched=True))
    work = _state(application_path)[0]
    assert work[5] == phase

    changed = _report("Titled differently", "a", "b")
    assert 5 == _drain(_pipeline(application_path, changed, phase, unmatched=True))
    after = _state(application_path)[0]
    assert after[:4] == ("complete", "complete", "case", changed.report_id)
    assert after[4] != work[4]
    # Back on its weekly cadence, which now comes before its next slot.
    assert after[5] == phase + _WEEK

    # That weekly search proposes something else again, and admits no
    # recording: the case stays, as ADR-0089 decided.
    weekly = _report("Titled a third way", "a", "b")
    assert 2 == _drain(
        _pipeline(application_path, weekly, phase + _WEEK, unmatched=True)
    )
    assert _state(application_path)[0] == (*after[:5], phase + 2 * _WEEK)


def test_the_comparison_reads_the_policy_through_the_injected_seam(
    application_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seam resolves through the composing repository at call time. With
    no policy bound, the report is evaluated as the proposal stage evaluates
    it then; a policy that cannot be read counts as a change."""
    report = _report("Prepared once", "a", "b")
    _drain(_pipeline(application_path, report, 20, unmatched=False))
    work = _state(application_path)[0]
    due = work[5]
    assert type(due) is int
    libraries: list[str] = []
    original = SQLiteMetadataReportRepository.current_resolved_metadata_policy

    def unbound(self: SQLiteMetadataReportRepository, library: str) -> None:
        libraries.append(library)
        assert original(self, library) is None

    monkeypatch.setattr(
        SQLiteMetadataReportRepository, "current_resolved_metadata_policy", unbound
    )
    assert 2 == _drain(
        _pipeline(application_path, _restamped(report, 1), due, unmatched=False)
    )
    assert libraries == ["ebooks"]
    assert _state(application_path)[0][4] == work[4]

    def unreadable(self: SQLiteMetadataReportRepository, library: str) -> None:
        # Only publication's read fails; the proposal stage reads it again.
        libraries.append(library)
        if len(libraries) == 2:
            raise MetadataReportError("active metadata policy is unavailable")
        assert original(self, library) is None

    monkeypatch.setattr(
        SQLiteMetadataReportRepository, "current_resolved_metadata_policy", unreadable
    )
    later = next_research_slot(_WORK, due + _WEEK)
    assert 5 == _drain(
        _pipeline(application_path, _restamped(report, 2), later, unmatched=False)
    )
    # Taken as a change: the ordinary path proposes and admits again.
    assert len(libraries) > 2
    assert _state(application_path)[0][3] == _restamped(report, 2).report_id


def test_a_backlog_after_downtime_drains_and_relocks_to_each_phase(
    application_path: Path,
) -> None:
    reports = tuple(_item_report(f"item-{item}", "Prepared once") for item in range(4))
    _drain(_pipeline(application_path, reports, 20, unmatched=False))

    # A year without a scan: every work is long overdue. The next scan queues
    # them all and the pipeline claims them one at a time.
    resumed = 20 + 4 * _PERIOD
    researched: list[bool] = []
    again = tuple(_restamped(value, 1) for value in reports)
    assert 1 + len(reports) == _drain(
        _pipeline(
            application_path, again, resumed, unmatched=False, researched=researched
        )
    )
    assert researched == [True] * len(reports)
    repository = open_repository(application_path)
    try:
        marks = dict(
            repository._connection.execute(
                "SELECT work_id,research_not_before FROM review_preparation_work "
                "WHERE state='complete'"
            ).fetchall()
        )
    finally:
        repository.close()
    assert len(marks) == len(reports)
    for work_id, mark in marks.items():
        assert mark == next_research_slot(work_id, resumed + _WEEK)
        assert mark % _PERIOD == int(work_id[-16:], 16) % _PERIOD


def test_a_search_that_now_identifies_nothing_never_takes_a_case_away(
    application_path: Path,
) -> None:
    """A slot search whose report admits no work identity keeps the case, and
    a later search that admits it again returns the work to its slot."""
    report = _report("Prepared once", "a", "b")
    _drain(_pipeline(application_path, report, 20, unmatched=False))
    work, current, lineage, package = _state(application_path)
    before = _case_rows(application_path)
    due = work[5]
    assert type(due) is int

    lost = _report("Identified as nothing", "a", "b")
    assert 1 + 1 == _drain(
        _pipeline(
            application_path, lost, due, unmatched=True, disposition="evidence_only"
        )
    )
    assert _case_rows(application_path) == before
    # Searched weekly while it admits no recording, as ADR-0089 says.
    assert _state(application_path) == (
        (*work[:5], due + _WEEK),
        current,
        lineage,
        package,
    )
    repository = open_repository(application_path)
    try:
        assert json.loads(repository.get_review_case(str(work[4])))["current"]
    finally:
        repository.close()

    # Found again, with the same proposals: everything stays and the work
    # is back on its slot.
    found = _restamped(report, 1)
    assert 2 == _drain(_pipeline(application_path, found, due + _WEEK, unmatched=False))
    assert _case_rows(application_path) == before
    assert _state(application_path)[0] == (*work[:5], due + _PERIOD)


def test_a_search_that_identifies_nothing_over_no_case_names_no_case(
    application_path: Path,
) -> None:
    """With no case to keep, the ordinary path records the evidence, and no
    pointer is left naming a case."""
    report = _report("Nothing to ask", "a", "b", actionable=False)
    _drain(_pipeline(application_path, report, 20, unmatched=False))
    work, _current, _lineage, package = _state(application_path)
    assert work[2] == "no_case" and package[1] is None
    due = work[5]
    assert type(due) is int

    lost = _report("Identified as nothing", "a", "b", actionable=False)
    assert 2 == _drain(
        _pipeline(
            application_path, lost, due, unmatched=True, disposition="evidence_only"
        )
    )
    after, current, lineage, package = _state(application_path)
    assert after[:5] == ("complete", "complete", "evidence_only", lost.report_id, None)
    assert current == lineage == lost.report_id
    assert package == (lost.report_id, None)
    repository = open_repository(application_path)
    try:
        assert repository._connection.execute(
            "SELECT outcome,review_case_id FROM current_review_case_lineages"
        ).fetchall() == [("no_case", None)]
        assert repository._connection.execute(
            "SELECT outcome FROM current_review_packages"
        ).fetchall() == [("evidence_only",)]
    finally:
        repository.close()
