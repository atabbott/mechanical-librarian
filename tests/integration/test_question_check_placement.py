"""The question check where the projection runs it, and its retry (ADR-0108,
"The question check"; #2040 packets 3 and 4).

Each proof publishes a work through the real store, answers a field, then
publishes a later case of the same work, and reads the `carry_outcomes` row,
the carried decision and the record the check left. Every proof ends by
recomputing the record from the ledger (`assert_record_recomputes`).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from mechanical_librarian.domain import audiobook_decision_qualification
from mechanical_librarian.domain.human_decision import (
    HumanDecision,
    HumanDecisionError,
)
from mechanical_librarian.domain.metadata_policy import (
    MetadataPolicyProfile,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
)
from tests.integration.test_owner_field_record import _read_back
from tests.support.audiobook_reports import admitted_audiobook_report
from tests.support.evidence_led_titles import isbn_title_report
from tests.support.owner_field_record import (
    assert_record_recomputes,
    request_resume,
)
from tests.support.owner_values import OwnerStore, rewritten
from tests.support.prepared_library import accept, prepared
from tests.unit.test_owner_answer_holds import SOURCES, _activate, _profile, _store


def _outcomes(store: OwnerStore) -> list[tuple[str, str, int, str | None]]:
    """Every check outcome: answer, case, episode, outcome, carried row."""
    return [
        tuple(row)
        for row in store.repository._connection.execute(
            "SELECT review_case_id,outcome,episode,carried_decision_id "
            "FROM carry_outcomes ORDER BY checked_at,episode"
        )
    ]


def _carried(store: OwnerStore, decision_id: str) -> HumanDecision:
    (document,) = store.repository._connection.execute(
        "SELECT document FROM human_decisions WHERE decision_id=?", (decision_id,)
    ).fetchone()
    return HumanDecision.from_json(document)


def _record(store: OwnerStore, destination: str = "title") -> tuple[object, ...]:
    row = store.repository._connection.execute(
        "SELECT answer_id,review_case_id,episode,previous_answer_id,carry_eligible "
        "FROM owner_field_answers WHERE destination=?",
        (destination,),
    ).fetchone()
    assert row is not None
    return tuple(row)


def _review_store(tmp_path: Path, report: MetadataReport) -> OwnerStore:
    """A store in the Review posture, which no current writer sets but the
    projection still reads, so it is set here behind the store: the
    projection returns inactive after its guard."""
    store = OwnerStore(tmp_path, None)
    store.resolved = resolve_policy(
        MetadataPolicyProfile.builtin(), report.target.library
    )
    store.repository._connection.execute(
        "UPDATE metadata_policy_state SET posture='review'"
    )
    store.repository._connection.commit()
    return store


def _publisher_changed(report: MetadataReport) -> MetadataReport:
    """The same work read again with only an unrelated field changed, so the
    later case asks the title question it asked before."""
    return rewritten(report, publisher="Changed Elsewhere")


@pytest.mark.parametrize(
    "posture",
    ("automatic", "preview", "review"),
    ids=("automatic", "preview", "review"),
)
def test_the_same_question_carries_the_answer_on_every_projection_path(
    tmp_path: Path, posture: str
) -> None:
    """T4: a keep given on X is carried `settled` to the later case Y that
    asks the same title question, from Automatic (after the automatic
    block), Preview and the Review posture's early return. The carried row
    names its root, the record takes it with Y's case and episode and
    restores the root, the audit event is `human_decision_carried`, and a
    requeued projection of Y writes nothing more."""
    if posture == "review":
        report = isbn_title_report()
        store = _review_store(tmp_path, report)
        store.publish(report)
    else:
        store, report, _ = _store(tmp_path, posture=posture)
        store.publish(report)
    first = store.case.review_case_id
    kept = store.decide("keep_current")
    later = store.publish(
        rewritten(report, publisher="Another Publisher")
    ).review_case_id
    assert later != first
    if posture == "review":
        store.repository.persist_shadow_outcome(later, store.clock())

    ((case, outcome, episode, carried_id),) = _outcomes(store)
    assert (case, outcome, episode) == (later, "carried", 0)
    assert carried_id is not None
    carried = _carried(store, carried_id)
    assert (carried.carried_from, carried.outcome, carried.application) == (
        kept.decision_id,
        "keep_current",
        "settled",
    )
    assert carried.review_case_id == later
    assert _record(store) == (carried_id, later, 0, kept.decision_id, 1)
    assert store.repository._connection.execute(
        "SELECT kind FROM human_decision_events WHERE decision_id=?", (carried_id,)
    ).fetchall() == [("human_decision_carried",)]
    # The carry writes no no-change row of its own (ADR-0108; agreed with C3).
    assert (
        store.repository._connection.execute(
            "SELECT COUNT(*) FROM field_effect_no_changes WHERE decision_id=?",
            (carried_id,),
        ).fetchone()[0]
        == 0
    )
    # A settled row plans nothing.
    assert carried_id not in {
        json.loads(document)["decision_id"]
        for document in store.repository.pending_field_effect_decision_documents(32)
    }

    store.repository.persist_shadow_outcome(later, store.clock())
    assert len(_outcomes(store)) == 1
    assert_record_recomputes(store.repository._connection)
    store.close()


def test_the_audiobook_abstention_runs_the_check_under_its_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T4: while no audiobook tranche is qualified the projection abstains
    before any guard; it now runs the guard and the check before it
    returns inactive. A projection whose staged guard fails leaves the check
    to the retry and still returns inactive, never stale."""
    # No audiobook tranche qualified: the projection abstains.
    monkeypatch.setattr(
        audiobook_decision_qualification,
        "F46_PASSING_AUTOMATIC_OPTIONS",
        frozenset(),
    )
    report = admitted_audiobook_report()
    store = OwnerStore(tmp_path, None)
    store.repository.enter_shadow_preview(1, ())
    _activate(
        store,
        _profile("First policy", "ask"),
        MetadataPolicyProfile.builtin(),
        report.target.library,
    )
    store.publish(report)
    first = store.case.review_case_id
    assert store.case.media_kind == "audiobook"
    kept = store.decide("keep_current", destination="narrators")
    target = report.target
    later = store.publish(
        MetadataReport.create(
            target=TargetFacts(
                target.connection,
                target.library,
                target.item,
                tuple(
                    TargetField(
                        field.destination,
                        TargetFieldState.VALUE,
                        "Another Recording House",
                    )
                    if field.destination is TargetDestination.PUBLISHER
                    else field
                    for field in target.fields
                ),
            ),
            file=report.file,
            local_claims=report.local_claims,
            local_reasons=report.local_reasons,
            providers=report.providers,
            cover_sources=report.cover_sources,
            audio=report.audio,
        )
    ).review_case_id
    assert later != first

    assert store.repository.persist_shadow_outcome(later, store.clock()) == "inactive"

    outcomes = _outcomes(store)
    # The first case is no longer current: its guard fails, so the check is
    # skipped and the abstention still returns inactive.
    assert store.repository.persist_shadow_outcome(first, store.clock()) == "inactive"
    assert _outcomes(store) == outcomes
    assert [(case, outcome) for case, outcome, *_ in outcomes] == [(later, "carried")]
    carried = _carried(store, str(outcomes[0][3]))
    assert carried.carried_from == kept.decision_id
    assert carried.application == "settled"
    assert_record_recomputes(store.repository._connection)
    store.close()


def test_a_different_question_is_asked_again_and_not_checked_twice(
    tmp_path: Path,
) -> None:
    """Step 5: the library's title changed elsewhere, so the later case's
    target part differs: `target_changed`, the record keeps the answer
    from X, and the next projection of Y does not check it again."""
    store, report, _ = _store(tmp_path, posture="preview")
    store.publish(report)
    first = store.case.review_case_id
    kept = store.decide("keep_current")
    later = store.publish(rewritten(report, title="Retitled Elsewhere")).review_case_id

    assert [(case, outcome) for case, outcome, *_ in _outcomes(store)] == [
        (later, "target_changed")
    ]
    assert _record(store) == (kept.decision_id, first, 0, None, 1)
    store.repository.persist_shadow_outcome(later, store.clock())
    assert len(_outcomes(store)) == 1
    assert_record_recomputes(store.repository._connection)
    store.close()


def test_x_y_x_y_carries_one_row_per_visit(tmp_path: Path) -> None:
    """ADR-0108: a work that moves X, Y, X, Y gets one carried row per
    visit, each naming the root original, and the record restores the root."""
    store, report, _ = _store(tmp_path, posture="preview")
    x = store.publish(report).review_case_id
    kept = store.decide("keep_current")
    y_report = _publisher_changed(report)
    visits = []
    for case_report in (y_report, report, y_report):
        visits.append(store.publish(case_report).review_case_id)
    assert visits[1] == x and visits[0] == visits[2] != x

    carried = [row for row in _outcomes(store) if row[1] == "carried"]
    assert [case for case, *_ in carried] == visits
    # Each visit is its own case and episode.
    assert len({(case, episode) for case, _, episode, _ in carried}) == 3
    roots = {_carried(store, str(row[3])).carried_from for row in carried}
    assert roots == {kept.decision_id}
    assert _record(store)[3] == kept.decision_id
    assert_record_recomputes(store.repository._connection)
    store.close()


def test_withdrawing_a_carried_answer_restores_its_root_not_carried(
    tmp_path: Path,
) -> None:
    """Withdrawal and carry eligibility: withdrawing the carried row restores
    its root as not carry-eligible, so the next case asks the field again
    (step 1, `asked_again`)."""
    store, report, _ = _store(tmp_path, posture="preview")
    store.publish(report)
    kept = store.decide("keep_current")
    store.publish(_publisher_changed(report))
    ((_, _, _, carried_id),) = _outcomes(store)
    store.decide("withdraw")
    answer = _record(store)
    assert (answer[0], answer[4]) == (kept.decision_id, 0)

    later = store.publish(rewritten(report, publisher="Third Publisher"))
    assert [(case, outcome) for case, outcome, *_ in _outcomes(store)][-1] == (
        later.review_case_id,
        "asked_again",
    )
    assert carried_id is not None
    assert_record_recomputes(store.repository._connection)
    store.close()


def test_the_retry_runs_a_check_the_projection_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A check the projection never ran (here its case was admitted without
    a projection) is run by the retry on the published current case, once:
    a second retry finds nothing owed."""
    store, report, _ = _store(tmp_path, posture="preview")
    store.publish(report)
    kept = store.decide("keep_current")
    with monkeypatch.context() as patch:
        patch.setattr(
            store.repository, "persist_shadow_outcome", lambda *_args: "inactive"
        )
        later = store.publish(_publisher_changed(report)).review_case_id
    assert _outcomes(store) == []

    assert store.repository.retry_question_checks(None, store.clock()) == 1

    ((case, outcome, _, carried_id),) = _outcomes(store)
    assert (case, outcome) == (later, "carried")
    assert _carried(store, str(carried_id)).carried_from == kept.decision_id
    assert store.repository.retry_question_checks(None, store.clock()) == 0
    assert store.repository.retry_question_checks(("primary", "elsewhere"), 1) == 0
    assert_record_recomputes(store.repository._connection)
    store.close()


def test_a_plan_in_flight_defers_until_the_schedule_sweep(tmp_path: Path) -> None:
    """Step 2 and T12: the answer's plan is still planned at the later case,
    so the outcome is `deferred`; the schedule's sweep leaves it while the
    plan is out, and once the plan has ended checks it again: the answer is
    still unwritten and the later case asks the same question, so it is
    carried with its application and plans there."""
    store, report, _ = _store(tmp_path, posture="preview")
    store.publish(report)
    accepted = store.decide("accept", {"kind": "text", "value": SOURCES})
    operation = store.plan(accepted)
    later = store.publish(_publisher_changed(report)).review_case_id
    assert [(case, outcome) for case, outcome, *_ in _outcomes(store)] == [
        (later, "deferred")
    ]
    # Nothing has ended yet: the sweep leaves the provisional row as it is.
    assert (
        store.repository.retry_question_checks(
            None, store.clock(), provisional_only=True
        )
        == 1
    )
    assert [outcome for _, outcome, *_ in _outcomes(store)] == ["deferred"]

    # The plan was formed on the earlier case, so it ends without writing.
    ended = store.service.apply(operation.plan.plan_id)
    assert (ended.state, ended.failure) == ("stale", "authority")
    store.repository.retry_question_checks(None, store.clock(), provisional_only=True)
    ((case, outcome, _, carried_id),) = _outcomes(store)
    assert (case, outcome) == (later, "carried")
    # Still unwritten: carried with its application, so it plans on the
    # later case once, under the consent current then.
    carried = _carried(store, str(carried_id))
    assert (carried.carried_from, carried.application) == (
        accepted.decision_id,
        "apply",
    )
    assert carried.decision_id in {
        json.loads(document)["decision_id"]
        for document in store.repository.pending_field_effect_decision_documents(32)
    }
    assert (
        store.repository.retry_question_checks(
            None, store.clock(), provisional_only=True
        )
        == 0
    )
    assert_record_recomputes(store.repository._connection)
    store.close()


def test_an_open_resume_waits_and_a_failed_one_hands_the_field_back(
    tmp_path: Path,
) -> None:
    """Step 0: a Resume open at the later case leaves `resume_pending`; once
    its scan fails, the sweep closes the request `resume_superseded` naming
    the answer, the record loses carry eligibility, and the outcome becomes
    `asked_again`."""
    store, report, _ = _store(tmp_path)
    store.publish(report)
    first = store.case.review_case_id
    kept = store.decide("keep_current")
    request = request_resume(store.repository, first, "title", store.clock())
    assert request is not None
    later = store.publish(_publisher_changed(report)).review_case_id
    assert [(case, outcome) for case, outcome, *_ in _outcomes(store)] == [
        (later, "resume_pending")
    ]
    (scan,) = store.repository._connection.execute(
        "SELECT scan_id FROM owner_field_events WHERE event_id=?", (request,)
    ).fetchone()
    assert store.repository._fail_open_resumes_for_scan(scan, store.clock()) == 1
    store.repository._connection.commit()

    store.repository.retry_question_checks(None, store.clock(), provisional_only=True)

    assert [outcome for _, outcome, *_ in _outcomes(store)] == ["asked_again"]
    answer = _record(store)
    assert (answer[0], answer[4]) == (kept.decision_id, 0)
    assert store.repository._connection.execute(
        "SELECT kind,answer_id FROM owner_field_events WHERE request_id=? "
        "ORDER BY rowid",
        (request,),
    ).fetchall() == [
        ("resume_failed", None),
        ("resume_superseded", kept.decision_id),
    ]
    assert store.repository._connection.execute(
        "SELECT open_resume_id FROM owner_field_answers WHERE destination='title'"
    ).fetchone() == (None,)
    assert_record_recomputes(store.repository._connection)
    store.close()


@pytest.mark.parametrize(
    ("refusal", "outcome"),
    (
        (HumanDecisionError("stale"), None),
        (HumanDecisionError("already_decided"), None),
        (sqlite3.OperationalError("refused"), None),
        (HumanDecisionError("unsupported"), "invalid"),
    ),
    ids=("stale", "already_decided", "store", "domain"),
)
def test_a_refused_carry_writes_no_row_or_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    refusal: Exception,
    outcome: str | None,
) -> None:
    """T6 (Q11): a store refusal, or a refusal of the carried row a retry may
    not meet again, writes no row and keeps nothing of the carry, and the
    retry carries it; any other refusal of the constructor is `invalid`,
    final, with no carried row."""
    store, report, _ = _store(tmp_path, posture="preview")
    store.publish(report)
    kept = store.decide("keep_current")
    decisions = store.repository._decisions
    record = decisions.record_carried_decision

    def refused(*args: object) -> str:
        record(*args)  # type: ignore[arg-type]  # reason: the recorder's own arguments, passed through.
        raise refusal

    with monkeypatch.context() as patch:
        patch.setattr(decisions, "record_carried_decision", refused)
        later = store.publish(_publisher_changed(report)).review_case_id

    connection = store.repository._connection
    assert connection.execute(
        "SELECT count(*) FROM human_decisions WHERE carried_from IS NOT NULL"
    ).fetchone() == (0,)
    assert _record(store)[0] == kept.decision_id
    if outcome is None:
        assert _outcomes(store) == []
        assert store.repository.retry_question_checks(None, store.clock()) == 1
        ((case, carried, _, _),) = _outcomes(store)
        assert (case, carried) == (later, "carried")
    else:
        assert [(case, value) for case, value, *_ in _outcomes(store)] == [
            (later, outcome)
        ]
        store.repository.retry_question_checks(None, store.clock())
        assert len(_outcomes(store)) == 1
    assert_record_recomputes(connection)
    store.close()


def test_a_carried_row_plans_only_once_its_case_is_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The owner's arm after a carry: a carried unwritten accept takes the
    automatic arm's package join, so while the projection that carried it
    has only staged its case it is not pending; once the case is published,
    it is. The work moves X, Y, X: the return to X asks the question the
    answer was given against."""
    from mechanical_librarian.adapters.persistence import metadata_reports

    with prepared(tmp_path, automatic=True, library_model="publication") as library:
        root = library.root
        held = library.held
        assert held is not None
        original, first = held.authors, library.case_id()
        owner = accept(library.case(), "authors", "owner-answers-authors", 11)
        root.record_human_decision(owner)
        _read_back(library, ("Someone Else",), 12)

        seen: list[tuple[str, bool]] = []
        counted: list[bool] = []
        check = metadata_reports.SQLiteMetadataReportRepository._check_record_answers

        def observed(self: Any, case: Any, checked_at: int) -> None:
            check(self, case, checked_at)
            for (decision_id,) in self._connection.execute(
                "SELECT decision_id FROM human_decisions WHERE carried_from=?",
                (owner.decision_id,),
            ):
                seen.append(
                    (
                        str(decision_id),
                        decision_id
                        in {
                            json.loads(document)["decision_id"]
                            for document in self.pending_field_effect_decision_documents(
                                32
                            )
                        },
                    )
                )
                # Nor is it counted as a prepared change.
                counted.append(
                    str(decision_id)
                    in self.prepared_field_effect_decisions((str(decision_id),))
                )

        monkeypatch.setattr(
            metadata_reports.SQLiteMetadataReportRepository,
            "_check_record_answers",
            observed,
        )
        _read_back(library, original, 14)
        assert library.case_id() == first
        assert seen and all(pending is False for _, pending in seen)
        assert counted and not any(counted)
        carried = seen[0][0]
        assert carried in library.pending()
        assert owner.decision_id not in library.pending()
        assert_record_recomputes(root._connection)


def test_an_answer_on_the_tidied_case_is_carried_at_the_next_case(
    tmp_path: Path,
) -> None:
    """Amendment 1, R3: the consensus decoration tidy is asked again once the
    tidy is written (an expected non-carry, T11), and the owner's answer on
    that post-write case is carried at the next case asking the same
    question, so it is asked once. The same question under new proposal ids,
    as after a regeneration, is `test_decision_question`'s; the whole flow
    across the offline command is the shared regeneration proof (packet 7)."""
    from tests.unit.test_decision_question import _DECORATED, _title_report

    report = _title_report("Plain Work", _DECORATED, ("Plain Work", "Plain Work"))
    store = OwnerStore(tmp_path, None)
    store.repository.enter_shadow_preview(1, ())
    _activate(
        store,
        _profile("First policy", "ask"),
        MetadataPolicyProfile.builtin(),
        report.target.library,
    )
    store.publish(report)
    first = store.case.review_case_id
    kept = store.decide("keep_current")
    later = store.publish(_publisher_changed(report)).review_case_id
    assert later != first

    ((case, outcome, _, carried_id),) = _outcomes(store)
    assert (case, outcome) == (later, "carried")
    carried = _carried(store, str(carried_id))
    assert (carried.carried_from, carried.application) == (
        kept.decision_id,
        "settled",
    )
    assert_record_recomputes(store.repository._connection)
    store.close()


def test_recovery_and_scan_start_retry_the_question_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refused or never-run check is retried at startup and dormant start
    (the pipeline's recovery, after it has reset the pipeline) for every
    library, and at scan start for the library the scan reads (ADR-0108).
    The retry itself then finds the owed case: here a check whose carry was
    refused on the later case."""
    from mechanical_librarian.adapters.persistence import decisions, metadata_reports

    calls: list[object] = []
    retry = metadata_reports.SQLiteMetadataReportRepository.retry_question_checks

    def recorded(self: Any, library: Any, retried_at: int, **options: Any) -> int:
        checked = retry(self, library, retried_at, **options)
        calls.append((library, options.get("provisional_only", False), checked))
        return checked

    monkeypatch.setattr(
        metadata_reports.SQLiteMetadataReportRepository,
        "retry_question_checks",
        recorded,
    )
    with prepared(tmp_path, automatic=True, library_model="publication") as library:
        root = library.root
        held = library.held
        assert held is not None
        original = held.authors
        owner = accept(library.case(), "authors", "owner-answers-authors", 11)
        root.record_human_decision(owner)
        _read_back(library, ("Someone Else",), 12)
        assert (("primary", "ebooks"), False, 0) in calls

        def refused(*args: Any) -> str:
            raise HumanDecisionError("stale")

        with monkeypatch.context() as patch:
            patch.setattr(
                decisions.SQLiteDecisionRepository, "record_carried_decision", refused
            )
            _read_back(library, original, 14)
        assert [
            outcome
            for (outcome,) in root._connection.execute(
                "SELECT outcome FROM carry_outcomes WHERE review_case_id=?",
                (library.case_id(),),
            )
        ] == []

        calls.clear()
        library.pipeline.recover()

        assert calls == [(None, False, 1)]
        assert root._connection.execute(
            "SELECT outcome FROM carry_outcomes WHERE review_case_id=?",
            (library.case_id(),),
        ).fetchall() == [("carried",)]
        assert_record_recomputes(root._connection)
