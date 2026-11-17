"""A written work keeps its case for 30 days (#1523, S8, S9, S18, R1).

A scan that finds nothing left to decide retires a work's case. Within 30 days
of the work's latest verified write it keeps the case instead, recorded as
`recent_write`, so the owner can still go back on the write. Only a scan
supplies the time; once the write is 30 days old the same answer retires the
case. Every write goes through the guarded path over the fake library.
Synthetic fixtures only.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import (
    NonClosingRepository,
    SQLiteMetadataReportRepository,
)
from mechanical_librarian.application.review_pipeline import (
    PipelineAction,
    ReviewPreparationPipeline,
)
from mechanical_librarian.application.review_web import InspectCurrentReviewCase
from mechanical_librarian.domain.metadata_report import MetadataReport
from mechanical_librarian.domain.proposal_set import ProposalSet, evaluate_report
from mechanical_librarian.domain.review_case import KEPT_CASE_SECONDS
from tests.support.owner_values import OwnerStore
from tests.support.preparation_pipeline import (
    PRODUCER,
    entry,
    install_settings_authority,
)
from tests.support.review_cases import _report

# The opening matter's title: once written, its read-back keeps the title
# (nothing to decide) while a source still returns it, so the field keeps its
# owner control on the kept case.
WRITTEN = "Exact evaluator candidate"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[OwnerStore]:
    value = OwnerStore(tmp_path, _report())
    yield value
    value.close()


def _write(store: OwnerStore) -> int:
    """Write the owner's title and return when the write was verified."""
    store.write(store.decide("owner_entered", {"kind": "text", "value": WRITTEN}))
    return store.repository._connection.execute(
        "SELECT MAX(occurred_at) FROM field_effect_events WHERE kind='verified'"
    ).fetchone()[0]


def _read_back(title: str = WRITTEN) -> MetadataReport:
    """The work read again with nothing left to decide."""
    return _report(title, competing=False)


def _reason(store: OwnerStore) -> str:
    return store.repository._connection.execute(
        "SELECT transition.reason FROM current_review_case_lineages pointer "
        "JOIN review_case_transitions transition "
        "ON transition.transition_id=pointer.transition_id"
    ).fetchone()[0]


def _current(store: OwnerStore, review_case_id: str) -> bool:
    return json.loads(store.repository.get_review_case(review_case_id))["current"]


def test_a_written_work_keeps_its_case_until_the_write_is_30_days_old(
    store: OwnerStore,
) -> None:
    writing = store.case
    written_at = _write(store)
    hold = store.repository.field_effect_manual_hold_token(
        writing.review_case_id, "title"
    )
    assert hold is not None
    read_back = _read_back()
    assert store.admit(read_back, now=written_at + KEPT_CASE_SECONDS - 1) == (
        "superseded"
    )
    kept = store.case
    assert kept.review_case_id != writing.review_case_id
    assert _reason(store) == "recent_write"
    assert _current(store, kept.review_case_id)
    assert store.repository.review_case_kept(kept.review_case_id)
    # The owner's hold follows the work onto the kept case.
    assert (
        store.repository.field_effect_manual_hold_token(kept.review_case_id, "title")
        == hold
    )
    # The field still reads the written value, so going back is offered.
    detail = InspectCurrentReviewCase(store.repository).run(kept.review_case_id)
    title = next(value for value in detail["fields"] if value["destination"] == "title")
    assert {"rule": "previous", "value": "Title"} in title["owner_control"]["entry"][
        "suggestions"
    ]
    # The same answer replays while the write is recent ...
    assert store.admit(read_back, now=written_at + KEPT_CASE_SECONDS - 1) == (
        "replayed"
    )
    # ... and retires the kept case once the write is 30 days old.
    assert store.admit(read_back, now=written_at + KEPT_CASE_SECONDS) == "retired"
    assert not _current(store, kept.review_case_id)
    assert _reason(store) == "nonactionable"


def test_without_a_scan_time_nothing_to_decide_retires_as_before(
    store: OwnerStore,
) -> None:
    _write(store)
    assert store.admit(_read_back()) == "retired"


def test_a_work_never_written_retires_its_case(store: OwnerStore) -> None:
    nothing = _report("Title", actionable=False)
    assert store.admit(nothing, now=store.clock()) == "retired"


def test_an_old_write_keeps_no_case(store: OwnerStore) -> None:
    written_at = _write(store)
    assert store.admit(_read_back(), now=written_at + KEPT_CASE_SECONDS) == "retired"


def test_an_actionable_scan_replaces_the_kept_case(store: OwnerStore) -> None:
    written_at = _write(store)
    store.publish(_read_back(), now=written_at + 1)
    kept = store.case
    assert store.admit(_report(WRITTEN), now=written_at + 2) == "superseded"
    assert store.case.review_case_id != kept.review_case_id
    assert _reason(store) == "actionable"
    assert not store.repository.review_case_kept(store.case.review_case_id)
    assert not _current(store, kept.review_case_id)


def test_a_scan_time_must_be_a_whole_non_negative_second(store: OwnerStore) -> None:
    from mechanical_librarian.application.metadata_report import MetadataReportError

    proposal = evaluate_report(_report())
    for now in (-1, 1.5, True):
        with pytest.raises(MetadataReportError):
            store.repository.admit_review_case(proposal, now=now)  # type: ignore[arg-type]  # reason: proves the store refuses a time no scan supplies.


def _pipeline(
    store: SQLiteMetadataReportRepository, report: MetadataReport, now: int
) -> ReviewPreparationPipeline:
    """One ebook prepared from `report`, admitted at the scan's time `now`."""
    listed = replace(
        entry("item-1", report.file.identity),
        library="ebooks",
        title=str(report.target.fields[0].value),
        source_fingerprint=None,
    )

    def extract(repository, action: PipelineAction, claim_next: bool):
        current = repository.current_optional(
            "primary", "ebooks", action.item_id, report.file.identity
        )
        repository.publish(
            report,
            lambda: True,
            None if current is None else MetadataReport.from_json(current).report_id,
            after_publication=lambda bound, published: (
                bound.complete_review_extraction_publication(
                    action, published.report_id, now, 0, None, claim_next, None, False
                )
            ),
        )
        return report.report_id, True

    def propose(repository, report_id: str) -> str:
        proposal = evaluate_report(MetadataReport.from_json(repository.get(report_id)))
        repository.publish_proposal(proposal)
        return proposal.proposal_set_id

    def admit(repository, proposal_set_id: str) -> tuple[str, str | None]:
        # As the pipeline's admission stage does, with the scan's time.
        proposal = ProposalSet.from_json(repository.get_proposal(proposal_set_id))
        value = json.loads(repository.admit_review_case(proposal, now=now))
        return value["status"], value.get("review_case_id")

    def project(repository, action: PipelineAction) -> str:
        outcome = repository.persist_shadow_outcome(
            action.review_case_id or "", now, staged_action=action
        )
        return "stale" if outcome == "stale" else "complete"

    return ReviewPreparationPipeline(
        repository_factory=lambda: NonClosingRepository(store),
        libraries=(("primary", "ebooks", "lib-1", PRODUCER),),
        discover=lambda *_: (1, "a" * 64, (listed,)),
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


def _work(store: OwnerStore) -> tuple[object, ...]:
    return store.repository._connection.execute(
        "SELECT stage,state,outcome,review_case_id FROM review_preparation_work"
    ).fetchone()


def test_an_unchanged_scan_retires_the_kept_case_once_the_write_is_old(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """An unchanged work never reaches admission again, so preparation sends
    a kept case back to admission once its write is 30 days old, and the same
    answer then retires it (#1523, S9)."""
    store = OwnerStore(tmp_path, None)
    install_settings_authority(store.repository)
    store.repository.enter_shadow_preview(1, ())
    _drain(_pipeline(store.repository, _report(), 20))
    writing = _work(store)
    assert writing[:3] == ("complete", "complete", "case")
    store.load(str(writing[3]))
    written_at = _write(store)

    # The scan after the write keeps the case.
    _drain(_pipeline(store.repository, _read_back(), written_at + 1))
    kept = _work(store)
    assert kept[:3] == ("complete", "complete", "case")
    assert kept[3] != writing[3]
    assert _reason(store) == "recent_write"
    # The work's scheduled search is set beyond this test, so only the expiry
    # can move the unchanged work.
    store.repository._connection.execute(
        "UPDATE review_preparation_work SET research_not_before=?",
        (written_at + 10 * KEPT_CASE_SECONDS,),
    )

    # A day short of 30 days, an unchanged scan finds nothing to do ...
    assert (
        _drain(
            _pipeline(
                store.repository, _read_back(), written_at + KEPT_CASE_SECONDS - 1
            )
        )
        == 1
    )
    assert _work(store) == kept
    # ... and at 30 days it admits the same answer again, which retires it.
    _drain(_pipeline(store.repository, _read_back(), written_at + KEPT_CASE_SECONDS))
    assert _work(store) == ("complete", "complete", "no_case", None)
    assert _reason(store) == "nonactionable"
    assert not _current(store, str(kept[3]))
    # Keeping and retiring a case writes no event naming the work's values.
    captured = capfd.readouterr()
    assert WRITTEN not in captured.err + captured.out
    store.close()


@pytest.mark.parametrize(
    ("kind", "from_case", "to_case", "admitted"),
    (
        ("admitted", None, "a", True),
        ("superseded", "b", "a", True),
        ("no_case", None, None, False),
        ("retired", "b", None, False),
    ),
    ids=("admitted", "superseded", "no-case", "retired"),
)
def test_only_a_kept_admission_carries_the_recent_write_reason(
    kind: str, from_case: str | None, to_case: str | None, admitted: bool
) -> None:
    from mechanical_librarian.domain.review_case import (
        ReviewCaseValueError,
        ReviewTransition,
    )

    def build() -> ReviewTransition:
        return ReviewTransition(
            "primary",
            "ebooks",
            "item-1",
            2,
            "proposal_set_" + "1" * 64,
            "report_" + "2" * 64,
            kind,
            None if from_case is None else "review_case_" + from_case * 64,
            None if to_case is None else "review_case_" + to_case * 64,
            "recent_write",
        )

    if admitted:
        assert build().reason == "recent_write"
    else:
        with pytest.raises(ReviewCaseValueError):
            build()


def test_the_store_refuses_a_recent_write_retirement(store: OwnerStore) -> None:
    import sqlite3

    connection = store.repository._connection
    row = connection.execute(
        "SELECT connection_name,library_name,item_id,proposal_set_id,report_id,"
        "review_case_id FROM current_review_case_lineages"
    ).fetchone()
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO review_case_transitions(transition_id,connection_name,"
            "library_name,item_id,checkpoint,actor,from_review_case_id,"
            "to_review_case_id,proposal_set_id,report_id,kind,reason) "
            "VALUES(?,?,?,?,9,'review-admission-v1',?,NULL,?,?,'retired',"
            "'recent_write')",
            ("review_transition_" + "e" * 64, *row[:3], row[5], *row[3:5]),
        )
