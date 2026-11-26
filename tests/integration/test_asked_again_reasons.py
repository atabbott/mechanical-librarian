"""Why the record's earlier answer is asked again (#2039 contract 3.1-3.3).

Each proof answers a field through the real store, reaches one reason the
record's answer stops counting on the work's current case, and reads the
three places that say so: the case page's field (classification cause
`asked_again` and its `asked_again` document), the Attention item (the same
document) and the Activity note on the answer's own row (or its root's, where
it was carried). They agree with one another and with the reason.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from mechanical_librarian.adapters.persistence.owner_field_answers import (
    retire_record_answer,
)
from mechanical_librarian.adapters.persistence.review_sql import (
    record_asked_again_sql,
)
from mechanical_librarian.application.review_web import (
    BrowseReviewLibrary,
    InspectCurrentReviewCase,
)
from mechanical_librarian.domain.human_decision import (
    HumanDecision,
    HumanDecisionError,
)
from mechanical_librarian.interfaces.web.app import _AskedAgain, _AttentionItem
from tests.integration.test_question_check_placement import (
    _outcomes,
    _publisher_changed,
    _record,
)
from tests.support.evidence_led_titles import isbn_title_report
from tests.support.owner_field_record import (
    assert_record_recomputes,
    request_resume,
)
from tests.support.owner_values import OwnerStore, rewritten
from tests.unit.test_owner_answer_holds import _store

_NOTES = {
    "question_changed": "asked_again_choices",
    "target_changed": "asked_again_value",
    "withdrawn": "asked_again_withdrawn",
    "resume_failed": "asked_again_resume",
    "retired": "asked_again_retired",
    "matching_changed": "asked_again_matching",
    "other": "asked_again",
}


def _event(store: OwnerStore, decision_id: str) -> str:
    (event_id,) = store.repository._connection.execute(
        "SELECT event_id FROM human_decision_events WHERE decision_id=?",
        (decision_id,),
    ).fetchone()
    return str(event_id)


def _notes(store: OwnerStore) -> dict[str, str]:
    """Activity's note on every owner decision row, as a page reads them."""
    events = [
        str(row[0])
        for row in store.repository._connection.execute(
            "SELECT event_id FROM human_decision_events "
            "WHERE kind<>'human_decision_carried'"
        )
    ]
    return store.repository._field_effect_activity._activity_page_notes_once(events, [])


def _field(store: OwnerStore, destination: str) -> dict[str, Any]:
    detail = InspectCurrentReviewCase(store.repository).run(store.case.review_case_id)
    return next(
        field
        for field in cast(list[dict[str, Any]], detail["fields"])
        if field["destination"] == destination
    )


def _attention(store: OwnerStore, destination: str) -> dict[str, Any] | None:
    page = BrowseReviewLibrary(store.repository).attention(
        search=None, limit=50, cursor=None
    )
    return next(
        (
            item
            for item in cast(list[dict[str, Any]], page["items"])
            if item["review_case_id"] == store.case.review_case_id
            and item["destination"] == destination
        ),
        None,
    )


def _agrees(
    store: OwnerStore,
    reason: str,
    answer: HumanDecision,
    *,
    value: object,
    root: HumanDecision | None = None,
    destination: str = "title",
) -> None:
    """The case page, Attention and Activity say the same reason about the
    record's answer `answer`, given on `root`'s day where it was carried."""
    root = root or answer
    expected = {
        "reason": reason,
        "earlier": {
            "outcome": answer.outcome,
            "value": value,
            "occurred_at": root.occurred_at,
        },
    }
    field = _field(store, destination)
    assert (
        field["classification"],
        field["classification_cause"],
        field["classification_reason"],
        field["needs_attention"],
    ) == ("needs_attention", "asked_again", "This field needs your review.", True)
    assert field["asked_again"] == expected
    _AskedAgain.model_validate(field["asked_again"])
    item = _attention(store, destination)
    assert item is not None
    assert item["asked_again"] == expected
    _AttentionItem.model_validate(item)
    # The note is on the answer's own row, its root's where it was carried,
    # and on no other row.
    assert {
        event: note for event, note in _notes(store).items() if note is not None
    } == {_event(store, root.decision_id): _NOTES[reason]}
    assert_record_recomputes(store.repository._connection)


def _kept_title(report: Any) -> dict[str, object]:
    title = next(
        field for field in report.target.fields if field.destination.value == "title"
    )
    return {"kind": "text", "value": title.value}


def test_the_value_in_audiobookshelf_changed(tmp_path: Path) -> None:
    """`target_changed`: the library's title changed elsewhere."""
    store, report, _ = _store(tmp_path, posture="preview")
    store.publish(report)
    kept = store.decide("keep_current")
    store.publish(rewritten(report, title="Retitled Elsewhere"))
    assert [outcome for _, outcome, *_ in _outcomes(store)] == ["target_changed"]
    _agrees(store, "target_changed", kept, value=_kept_title(report))
    store.close()


def test_the_choices_changed(tmp_path: Path) -> None:
    """`question_changed`: the sources offer other titles."""
    store, report, _ = _store(tmp_path, posture="preview")
    store.publish(report)
    proposal = next(
        value for value in store.case.proposals if value["destination"] == "title"
    )
    chosen = next(
        alternative["value"]
        for alternative in proposal["result"]["alternatives"]
        if alternative["assessment"] != "preserves_target"
    )
    accepted = store.decide("accept", chosen)
    store.publish(isbn_title_report(titles=("A Different Example Title",)))
    assert [outcome for _, outcome, *_ in _outcomes(store)] == ["question_changed"]
    _agrees(store, "question_changed", accepted, value=chosen)
    store.close()


def test_a_withdrawn_later_choice_restores_the_earlier_one(tmp_path: Path) -> None:
    """`withdrawn`: the later case carried the keep; withdrawing it there
    restores the keep, no longer carried, which the case asks again on the
    keep's own day. The carried row has no Activity row (V26)."""
    store, report, _ = _store(tmp_path, posture="preview")
    store.publish(report)
    kept = store.decide("keep_current")
    store.publish(_publisher_changed(report))
    ((_, outcome, _, carried_id),) = _outcomes(store)
    assert outcome == "carried" and carried_id is not None
    # Carried, nothing is asked and no row has a note.
    assert _field(store, "title")["asked_again"] is None
    assert _notes(store) == {}
    store.decide("withdraw")
    assert (_record(store)[0], _record(store)[4]) == (kept.decision_id, 0)
    _agrees(store, "withdrawn", kept, value=_kept_title(report))
    store.close()


def test_a_withdrawn_choice_restores_a_carried_one_with_its_roots_day(
    tmp_path: Path,
) -> None:
    """U11: the restored answer is the carried row; its day is its root's."""
    store, report, _ = _store(tmp_path, posture="preview")
    store.publish(report)
    kept = store.decide("keep_current")
    store.publish(_publisher_changed(report))
    ((_, _, _, carried_id),) = _outcomes(store)
    assert carried_id is not None
    store.decide("owner_entered", {"kind": "text", "value": "Owner Title"})
    store.decide("withdraw")
    (carried_document,) = store.repository._connection.execute(
        "SELECT document FROM human_decisions WHERE decision_id=?", (carried_id,)
    ).fetchone()
    carried = HumanDecision.from_json(carried_document)
    assert _record(store)[0] == carried_id
    assert carried.occurred_at > kept.occurred_at
    _agrees(store, "withdrawn", carried, root=kept, value=_kept_title(report))
    store.close()


def test_a_failed_resume_hands_the_field_back(tmp_path: Path) -> None:
    """`resume_failed`: the question check closed a failed Resume over the
    answer (step 0)."""
    store, report, _ = _store(tmp_path)
    store.publish(report)
    first = store.case.review_case_id
    kept = store.decide("keep_current")
    request = request_resume(store.repository, first, "title", store.clock())
    assert request is not None
    store.publish(_publisher_changed(report))
    (scan,) = store.repository._connection.execute(
        "SELECT scan_id FROM owner_field_events WHERE event_id=?", (request,)
    ).fetchone()
    assert store.repository._fail_open_resumes_for_scan(scan, store.clock()) == 1
    store.repository._connection.commit()
    store.repository.retry_question_checks(None, store.clock(), provisional_only=True)
    assert [outcome for _, outcome, *_ in _outcomes(store)] == ["asked_again"]
    _agrees(store, "resume_failed", kept, value=_kept_title(report))
    store.close()


def _attention_reason(store: OwnerStore, destination: str) -> object:
    """The document Attention's reason column reads for the field of the
    current case (`record_asked_again_sql`), whether or not the field is in
    Attention."""
    (document,) = store.repository._connection.execute(
        "SELECT "
        + record_asked_again_sql("lineage", ":case", ":destination")
        + " FROM current_review_case_lineages lineage "
        "WHERE lineage.review_case_id=:case",
        {"case": store.case.review_case_id, "destination": destination},
    ).fetchone()
    return document


def _asks_nothing(store: OwnerStore, destination: str = "title") -> None:
    """The case page, Attention and Activity agree the record's answer asks
    nothing: no cause or document on the field, no Attention item with a
    reason (nor any reason in the column, read directly), and no Activity
    note."""
    field = _field(store, destination)
    assert field["classification_cause"] != "asked_again"
    assert field["asked_again"] is None
    item = _attention(store, destination)
    assert item is None or item.get("asked_again") is None
    assert _attention_reason(store, destination) is None
    assert {
        event: note for event, note in _notes(store).items() if note is not None
    } == {}
    assert_record_recomputes(store.repository._connection)


def test_a_new_resume_after_a_failed_one_stops_asking_until_it_fails(
    tmp_path: Path,
) -> None:
    """Review 1, finding 1: after a failed Resume asks the field again, the
    owner requests Resume again. While that request is open and has not
    failed, Automatic's decision settles the field, so the case page,
    Attention and Activity stop asking; once it fails, all three ask again
    with `resume_failed`."""
    store, report, _ = _store(tmp_path)
    store.publish(report)
    first = store.case.review_case_id
    kept = store.decide("keep_current")
    request = request_resume(store.repository, first, "title", store.clock())
    assert request is not None
    store.publish(_publisher_changed(report))
    (scan,) = store.repository._connection.execute(
        "SELECT scan_id FROM owner_field_events WHERE event_id=?", (request,)
    ).fetchone()
    assert store.repository._fail_open_resumes_for_scan(scan, store.clock()) == 1
    store.repository._connection.commit()
    store.repository.retry_question_checks(None, store.clock(), provisional_only=True)
    _agrees(store, "resume_failed", kept, value=_kept_title(report))
    assert '"reason":"resume_failed"' in str(_attention_reason(store, "title"))

    again = request_resume(
        store.repository, store.case.review_case_id, "title", store.clock()
    )
    assert again is not None
    _asks_nothing(store)

    (scan,) = store.repository._connection.execute(
        "SELECT scan_id FROM owner_field_events WHERE event_id=?", (again,)
    ).fetchone()
    assert store.repository._fail_open_resumes_for_scan(scan, store.clock()) == 1
    store.repository._connection.commit()
    _agrees(store, "resume_failed", kept, value=_kept_title(report))
    assert '"reason":"resume_failed"' in str(_attention_reason(store, "title"))
    store.close()


def test_a_retired_choice_is_asked_again(tmp_path: Path) -> None:
    """`retired`: regenerate-derived --retire-pending retired the answer's
    plan, so the next case asks it again (step 1)."""
    store, report, _ = _store(tmp_path, posture="preview")
    store.publish(report)
    entered = store.decide("owner_entered", {"kind": "text", "value": "Owner Title"})
    connection = store.repository._connection
    with connection:
        retire_record_answer(
            connection,
            (
                report.target.connection,
                report.target.library,
                report.target.item,
                "title",
            ),
            entered.decision_id,
            store.clock(),
        )
    store.publish(_publisher_changed(report))
    assert [outcome for _, outcome, *_ in _outcomes(store)] == ["asked_again"]
    _agrees(store, "retired", entered, value={"kind": "text", "value": "Owner Title"})
    store.close()


def test_a_refused_carry_asks_for_another_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`other`: the carried row was refused (`invalid`)."""
    store, report, _ = _store(tmp_path, posture="preview")
    store.publish(report)
    kept = store.decide("keep_current")
    decisions = store.repository._decisions

    def refused(*args: object) -> str:
        raise HumanDecisionError("unsupported")

    with monkeypatch.context() as patch:
        patch.setattr(decisions, "record_carried_decision", refused)
        store.publish(_publisher_changed(report))
    assert [outcome for _, outcome, *_ in _outcomes(store)] == ["invalid"]
    _agrees(store, "other", kept, value=_kept_title(report))
    store.close()


def test_a_decline_has_no_value(tmp_path: Path) -> None:
    """A historical decline is asked again with no value to quote."""
    store, report, _ = _store(tmp_path, posture="preview")
    store.publish(report)
    case = store.case
    proposal = next(
        value for value in case.proposals if value["destination"] == "title"
    )
    declined = HumanDecision.create(
        case,
        proposal_id=str(proposal["proposal_id"]),
        target_fingerprint=case.target_fingerprint,
        destination="title",
        actor="owner",
        outcome="reject",
        selected_value=None,
        idempotency_key="owner-decline-0001",
        occurred_at=store.clock(),
    )
    store.repository.record_human_decision(declined)
    store.publish(rewritten(report, title="Retitled Elsewhere"))
    _agrees(store, "target_changed", declined, value=None)
    store.close()


def test_book_matching_asked_again_on_a_later_case(tmp_path: Path) -> None:
    """`matching_changed`: Book matching asked the title again on its case
    (which reads as main there, C2b-28); a later case of the work asks it
    again for that reason."""
    from tests.integration.test_book_matching_fields import (
        _change,
        _check,
        _reask_store,
    )
    from tests.integration.test_book_matching_fields import _report as _level_report

    store, standing, changed_to = _reask_store(tmp_path, "group_member_changed")
    try:
        assert standing.destination == "title"
        _change(store, changed_to, 60)
        assert _check(store, 61) == "reconciled"
        # On its own case the answer reads as main did, with the existing note.
        assert _field(store, "title")["asked_again"] is None
        first = store.case.review_case_id
        later = rewritten(_level_report(), publisher="Changed Elsewhere")
        store.publish(later)
        assert store.case.review_case_id != first
        _check(store, 62)
        _agrees(
            store,
            "matching_changed",
            standing,
            value=standing.selected_value,
        )
    finally:
        store.close()


def test_a_carried_answer_says_in_history_which_choice_it_kept(tmp_path: Path) -> None:
    """T-H (V25): on the later case the carried row's History item names its
    root original's day; every other owner item names none."""
    from mechanical_librarian.interfaces.web.app import _HistoryDecisionItem

    store, report, _ = _store(tmp_path, posture="preview")
    store.publish(report)
    first = store.case.review_case_id
    kept = store.decide("keep_current")
    later = store.publish(_publisher_changed(report)).review_case_id
    ((_, outcome, _, carried_id),) = _outcomes(store)
    assert outcome == "carried"
    reader = InspectCurrentReviewCase(store.repository)
    items = [
        item
        for item in cast(list[dict[str, Any]], reader.history(later)["items"])
        if item.get("decision_id") == carried_id
    ]
    assert [(item["kind"], item["kept_from"]) for item in items] == [
        ("human_decision_carried", kept.occurred_at)
    ]
    # The earlier case is no longer current: its store history is read.
    (root_item,) = [
        item
        for item in store.repository.get_review_case_history(first, 50)
        if item.get("decision_id") == kept.decision_id
    ]
    assert root_item["kept_from"] is None
    # The carried item's kind and application wait for C4's wire bump
    # (`human_decision_carried`, `settled`), so only its own field passes the
    # history wire here.
    _HistoryDecisionItem.model_validate(
        {**items[0], "kind": "human_decision_recorded", "application": "apply"}
    )
    store.close()
