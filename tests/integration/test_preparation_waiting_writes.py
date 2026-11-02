"""Preparation never leaves a write waiting on a case it moved past (#1658).

A write staled only by a lost connection waits for its library and is planned
again into the same plan (#1402, #1415). Preparation moves a work off its case
when a rescan supersedes or retires it, when the work leaves the library or is
no longer eligible, and when it readmits a lineage an activation left retired
as another case or as none. A case left behind returns only as a new episode,
whose authority none of the old plans carries, so the write would wait for
good. Preparation therefore closes it in the transaction that admits or retires
the work, with a cover's cleanup. A write on the work's current case, including
one that returned as itself, keeps waiting and is still written once.

The field cases drive the real preparation harness of
`test_leaving_automatic_posture.py`; the cover cases build the same pipeline
over the real owner cover decision of `test_waiting_plan_identity.py`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.application.catalogue import CatalogueEntry
from mechanical_librarian.application.review_pipeline import (
    PipelineAction,
    ReviewPipelineRepository,
    ReviewPreparationPipeline,
)
from mechanical_librarian.domain.metadata_policy import MetadataPolicyProfile
from mechanical_librarian.domain.metadata_report import MetadataReport
from mechanical_librarian.domain.proposal_set import ProposalSet, evaluate_report
from mechanical_librarian.domain.review_case import ReviewAdmissionResult
from tests.integration.test_leaving_automatic_posture import _PRODUCER, _Library
from tests.integration.test_leaving_automatic_posture import library as library
from tests.integration.test_policy_activation_waiting_writes import (
    _CLOSED,
    _activate_cover_policy,
    _cover_case,
    _cover_lane,
    _FieldLane,
    _owner_decision_id,
    _reopen_fields,
)
from tests.support.owner_values import rewritten
from tests.unit.test_waiting_plan_identity import (
    _cleanup,
    _CoverLane,
    _decision_plans,
    _signals,
    _waiting_cover,
)
from tests.unit.test_waiting_plan_identity import cover as cover

_WAITING = ("stale", "connection")


def _entry(
    connection: str,
    library: str,
    item: str,
    file_identity: str,
    updated_at: int,
    classification: str = "singular-supported",
) -> CatalogueEntry:
    """The work as the library lists it; a later listing reads it renamed, so
    its source changed and preparation extracts it again."""
    return CatalogueEntry(
        connection,
        library,
        "lib-1",
        item,
        updated_at,
        "Synthetic work" if updated_at == 1 else "Another title",
        ("Ada Example",),
        True,
        False,
        classification,
        file_identity,
        "/book.epub",
        "epub",
        "books",
    )


def _discover(
    repository: SQLiteMetadataReportRepository,
    entries: tuple[CatalogueEntry, ...],
    at: int,
    generation: int,
) -> None:
    """Complete the queued discovery with what the library now lists."""
    discovery = repository.claim_review_pipeline_action(at)
    assert discovery is not None
    assert discovery.stage == "discovery"
    repository.complete_review_discovery(discovery, generation, "a" * 64, entries, at)


def _absent(
    repository: SQLiteMetadataReportRepository,
    entries: tuple[CatalogueEntry, ...],
    at: int,
) -> None:
    """Two discoveries agree the work is gone or ineligible, and the scan's
    completing claim retires it, as preparation confirms an absence."""
    connection, library, _ = _target(repository)
    repository.request_review_scan(connection, library, "lib-1", _PRODUCER, at)
    _discover(repository, entries, at + 1, 2)
    assert repository.claim_review_pipeline_action(at + 2) is None
    _discover(repository, entries, at + 3, 3)
    assert repository.claim_review_pipeline_action(at + 4) is None


def _target(repository: SQLiteMetadataReportRepository) -> tuple[str, str, str]:
    row = repository._connection.execute(
        "SELECT connection_name,library_name,item_id FROM current_review_case_lineages"
    ).fetchone()
    return cast(tuple[str, str, str], row)


def _transitions(
    repository: SQLiteMetadataReportRepository,
) -> list[tuple[object, ...]]:
    return repository._connection.execute(
        "SELECT actor,kind,from_review_case_id,to_review_case_id "
        "FROM review_case_transitions ORDER BY checkpoint"
    ).fetchall()


def _rescan(library: _Library, at: int) -> None:
    """The work changed in the library, so a rescan prepares it again."""
    library.root.request_review_scan("primary", "ebooks", "lib-1", _PRODUCER, at)
    _discover(
        library.root,
        (_entry("primary", "ebooks", "item-1", library.report.file.identity, 2),),
        at + 1,
        2,
    )
    library.now[0] = at + 2
    library.drain()


def _publish_successor(
    repository: ReviewPipelineRepository, report: MetadataReport
) -> None:
    """Publish ``report`` as the work's current report, as a re-extraction
    that reads the work changed does."""
    concrete = cast(SQLiteMetadataReportRepository, repository)
    target = report.target
    current = concrete._connection.execute(
        "SELECT report_id FROM current_metadata_reports WHERE connection_name=? "
        "AND library_name=? AND item_id=?",
        (target.connection, target.library, target.item),
    ).fetchone()
    concrete.publish(report, lambda: True, None if current is None else current[0])


def _publishing(
    report: MetadataReport,
) -> Callable[[ReviewPipelineRepository, PipelineAction, bool], tuple[str, bool]]:
    def extract(
        repository: ReviewPipelineRepository, _: PipelineAction, __: bool
    ) -> tuple[str, bool]:
        _publish_successor(repository, report)
        return report.report_id, False

    return extract


def _assert_field_closed(
    library: _Library, lane: _FieldLane, decision_id: str, waiting_id: str
) -> None:
    assert _decision_plans(library.root, decision_id) == [(waiting_id, *_CLOSED)]
    assert decision_id not in library.pending()
    assert all(kind != "automatic_recovery" for kind, _ in _signals(library.root))
    assert (waiting_id, "superseded") in lane.waiting_rows()
    assert (waiting_id, "stale") not in lane.waiting_rows()
    lane.reconnect()
    assert _decision_plans(library.root, decision_id) == [(waiting_id, *_CLOSED)]
    assert lane.sent == []


def _waiting_field(library: _Library, tmp_path: Path) -> tuple[str, str, _FieldLane]:
    decision_id = _owner_decision_id(library)
    lane = _FieldLane(library, tmp_path)
    library.now[0] = 13
    waiting_id = lane.wait(library, decision_id)
    assert ("automatic_recovery", "in_progress") in _signals(library.root)
    return decision_id, waiting_id, lane


def test_a_rescan_that_replaces_the_case_closes_a_waiting_field_write(
    library: _Library, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    decision_id, waiting_id, lane = _waiting_field(library, tmp_path)
    case_before = library.case_id()
    monkeypatch.setattr(
        library.pipeline,
        "_extract",
        _publishing(rewritten(library.report, title="Another title")),
    )

    _rescan(library, 20)

    case_after = library.case_id()
    assert case_after != case_before
    assert _transitions(library.root)[-1] == (
        "review-admission-v1",
        "superseded",
        case_before,
        case_after,
    )
    _assert_field_closed(library, lane, decision_id, waiting_id)
    _reopen_fields(library, tmp_path)


@pytest.mark.parametrize("reason", ["tombstoned", "ineligible"])
def test_preparation_retiring_a_work_closes_its_waiting_field_write(
    library: _Library, tmp_path: Path, reason: str
) -> None:
    decision_id, waiting_id, lane = _waiting_field(library, tmp_path)
    case_before = library.case_id()
    entries = (
        ()
        if reason == "tombstoned"
        else (
            _entry(
                "primary",
                "ebooks",
                "item-1",
                library.report.file.identity,
                1,
                "unsupported",
            ),
        )
    )

    _absent(library.root, entries, 20)

    kind = "tombstoned" if reason == "tombstoned" else "retired"
    assert _transitions(library.root)[-1] == (
        "review-preparation-v1",
        kind,
        case_before,
        None,
    )
    _assert_field_closed(library, lane, decision_id, waiting_id)
    _reopen_fields(library, tmp_path)


def test_a_due_back_case_replaced_by_a_rescan_closes_its_field_write(
    library: _Library, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Leaving Automatic keeps the owner's write for a case due back, but the
    rescan that brings the work back admits another case (#1657 Q2)."""
    decision_id, waiting_id, lane = _waiting_field(library, tmp_path)
    case_before = library.case_id()
    changed = rewritten(library.report, title="Another title")
    propose = library.pipeline._propose

    def propose_changed(repository: ReviewPipelineRepository, _: str) -> str:
        _publish_successor(repository, changed)
        return propose(repository, changed.report_id)

    assert library.root.enter_shadow_preview(20, ("ebooks",)) == "preview"
    assert _decision_plans(library.root, decision_id) == [(waiting_id, *_WAITING)]
    monkeypatch.setattr(library.pipeline, "_propose", propose_changed)
    library.now[0] = 30
    library.drain()

    case_after = library.case_id()
    assert case_after != case_before
    assert [row[:2] for row in _transitions(library.root)] == [
        ("review-admission-v1", "admitted"),
        ("policy-activation-v1", "retired"),
        ("review-admission-v1", "admitted"),
    ]
    assert _transitions(library.root)[-1][3] == case_after
    _assert_field_closed(library, lane, decision_id, waiting_id)
    _reopen_fields(library, tmp_path)


def _prepare(
    cover: _CoverLane,
    report: MetadataReport,
    at: int,
    *,
    entries: tuple[CatalogueEntry, ...] | None = None,
) -> None:
    """Rescan the cover store's work through the real preparation pipeline,
    whose extraction publishes ``report`` as the work's successor."""
    connection, library, item = _target(cover.repository)
    if entries is None:
        entries = (_entry(connection, library, item, report.file.identity, 2),)
    now = [at]
    database = cover.root / "application.sqlite3"

    def propose(repository: ReviewPipelineRepository, report_id: str) -> str:
        concrete = cast(SQLiteMetadataReportRepository, repository)
        proposal = evaluate_report(MetadataReport.from_json(concrete.get(report_id)))
        concrete.publish_proposal(proposal)
        return proposal.proposal_set_id

    def admit(
        repository: ReviewPipelineRepository, proposal_set_id: str
    ) -> tuple[str, str | None]:
        concrete = cast(SQLiteMetadataReportRepository, repository)
        proposal = ProposalSet.from_json(concrete.get_proposal(proposal_set_id))
        result = ReviewAdmissionResult.from_json(concrete.admit_review_case(proposal))
        return result.status, result.review_case_id

    def project(repository: ReviewPipelineRepository, action: Any) -> str:
        concrete = cast(SQLiteMetadataReportRepository, repository)
        outcome = concrete.persist_shadow_outcome(
            action.review_case_id, now[0], staged_action=action
        )
        return "stale" if outcome == "stale" else "complete"

    pipeline = ReviewPreparationPipeline(
        repository_factory=lambda: open_repository(
            database, authority=cover.repository.authority
        ),
        libraries=((connection, library, "lib-1", _PRODUCER),),
        discover=lambda *_: (_ for _ in ()).throw(AssertionError()),
        extract=_publishing(report),
        providers=lambda *_: (),
        propose=propose,
        admit=admit,
        project=project,
        clock=lambda: now[0],
    )
    cover.repository.request_review_scan(connection, library, "lib-1", _PRODUCER, at)
    _discover(cover.repository, entries, at + 1, 2)
    now[0] = at + 2
    for _ in range(32):
        if not pipeline.run_next():
            return
    raise AssertionError("preparation did not settle")


def _current_report(cover: _CoverLane) -> MetadataReport:
    (report_id,) = cover.repository._connection.execute(
        "SELECT report_id FROM current_review_case_lineages"
    ).fetchone()
    return MetadataReport.from_json(cover.repository.get(report_id))


def _move_past(cover: _CoverLane, path: str) -> None:
    report = _current_report(cover)
    connection, library, item = _target(cover.repository)
    if path == "retired":
        _prepare(cover, rewritten(report, title="Another title"), 300)
    elif path == "tombstoned":
        _absent(cover.repository, (), 300)
    elif path == "ineligible":
        _absent(
            cover.repository,
            (
                _entry(
                    connection, library, item, report.file.identity, 2, "unsupported"
                ),
            ),
            300,
        )
    else:
        _activate_cover_policy(cover, MetadataPolicyProfile.builtin(), reconcile=True)
        _prepare(cover, rewritten(report, title="Another title"), 300)


def _drive_cleanup(cover: _CoverLane) -> None:
    lane = _cover_lane(cover)
    cover.capabilities.category = "allowed"
    for _ in range(4):
        lane.wake()
        lane._run_until_idle()


_COVER_OUTCOMES = {
    "retired": ("review-admission-v1", "retired"),
    "tombstoned": ("review-preparation-v1", "tombstoned"),
    "ineligible": ("review-preparation-v1", "retired"),
    "due_back_no_case": ("review-admission-v1", "no_case"),
}


@pytest.mark.parametrize("path", list(_COVER_OUTCOMES))
def test_preparation_moving_past_a_case_closes_its_waiting_cover(
    cover: _CoverLane, path: str
) -> None:
    waiting_id = _waiting_cover(cover)
    case_id = _cover_case(cover.repository)
    assert ("automatic_recovery", "in_progress") in _signals(cover.repository)

    _move_past(cover, path)

    assert _transitions(cover.repository)[-1][:3] == (
        *_COVER_OUTCOMES[path],
        None if path == "due_back_no_case" else case_id,
    )
    assert _cover_case(cover.repository) is None
    left = cover.repository.get_field_effect(waiting_id)
    assert (left.state, left.failure) == _CLOSED
    assert cover.repository.pending_field_effect_decision_counts() == ()
    assert all(kind != "automatic_recovery" for kind, _ in _signals(cover.repository))
    assert _cleanup(cover.repository, waiting_id) == [("pending",)]
    _drive_cleanup(cover)
    assert _cleanup(cover.repository, waiting_id) == [("completed",)]
    assert not cover.recovery.exists(waiting_id)
    left = cover.repository.get_field_effect(waiting_id)
    assert (left.state, left.failure) == _CLOSED
    cover.reopen()


@pytest.mark.parametrize("kind", ["field", "cover"])
def test_an_unchanged_rescan_keeps_the_write_waiting(
    request: pytest.FixtureRequest, tmp_path: Path, kind: str
) -> None:
    """Preparation readmits the same case, so the write still lands once."""
    if kind == "field":
        library = cast(_Library, request.getfixturevalue("library"))
        decision_id, waiting_id, lane = _waiting_field(library, tmp_path)
        case_before = library.case_id()
        _rescan(library, 20)
        assert library.case_id() == case_before
        assert _decision_plans(library.root, decision_id) == [(waiting_id, *_WAITING)]
        lane.reconnect()
        assert _decision_plans(library.root, decision_id) == [
            (waiting_id, "verified", None)
        ]
        assert lane.sent == [waiting_id]
        _reopen_fields(library, tmp_path)
        return
    cover = cast(_CoverLane, request.getfixturevalue("cover"))
    waiting_id = _waiting_cover(cover)
    case_id = _cover_case(cover.repository)
    _prepare(cover, _current_report(cover), 300)
    assert _cover_case(cover.repository) == case_id
    _assert_cover_written_once(cover, waiting_id)


def _assert_cover_written_once(cover: _CoverLane, waiting_id: str) -> None:
    left = cover.repository.get_field_effect(waiting_id)
    assert (left.state, left.failure) == _WAITING
    assert _cleanup(cover.repository, waiting_id) == []
    assert cover.recovery.exists(waiting_id)
    _drive_cleanup(cover)
    written = cover.repository.get_field_effect(waiting_id)
    assert (written.state, written.failure) == ("verified", None)
    assert _cleanup(cover.repository, waiting_id) == [("completed",)]
    assert not cover.recovery.exists(waiting_id)
    cover.reopen()


def test_a_due_back_cover_readmitted_by_preparation_still_writes_once(
    cover: _CoverLane,
) -> None:
    """A same-policy reactivation retires the case, and real preparation
    readmits it as itself, so its episode continues (#1657)."""
    waiting_id = _waiting_cover(cover)
    case_id = _cover_case(cover.repository)
    _activate_cover_policy(cover, MetadataPolicyProfile.builtin(), reconcile=True)
    assert _cover_case(cover.repository) is None

    _prepare(cover, _current_report(cover), 300)

    assert _cover_case(cover.repository) == case_id
    _assert_cover_written_once(cover, waiting_id)


@pytest.mark.parametrize("fault", [True, False], ids=["fault", "no_fault"])
@pytest.mark.parametrize("path", ["retired", "tombstoned"])
def test_a_failed_preparation_keeps_the_waiting_cover(
    cover: _CoverLane, monkeypatch: pytest.MonkeyPatch, path: str, fault: bool
) -> None:
    """The close is part of the transition: a fault undoes both, and the same
    run without it closes the write."""
    waiting_id = _waiting_cover(cover)
    case_id = _cover_case(cover.repository)
    transitions = _transitions(cover.repository)

    def fail(*_: object) -> None:
        raise sqlite3.OperationalError("synthetic")

    if fault:
        monkeypatch.setattr(
            SQLiteMetadataReportRepository, "_record_field_effect_event", fail
        )
    try:
        _move_past(cover, path)
    except Exception:
        if not fault:
            raise
    monkeypatch.undo()
    left = cover.repository.get_field_effect(waiting_id)
    if fault:
        assert _transitions(cover.repository) == transitions
        assert _cover_case(cover.repository) == case_id
        assert (left.state, left.failure) == _WAITING
        assert _cleanup(cover.repository, waiting_id) == []
        assert cover.recovery.exists(waiting_id)
    else:
        assert len(_transitions(cover.repository)) == len(transitions) + 1
        assert _cover_case(cover.repository) is None
        assert (left.state, left.failure) == _CLOSED
        assert _cleanup(cover.repository, waiting_id) == [("pending",)]
    cover.reopen()
