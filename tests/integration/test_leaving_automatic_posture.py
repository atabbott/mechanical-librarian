"""Leaving Automatic with an unchanged policy keeps every work's review case.

``enter_shadow_preview`` reactivates the unchanged policy, which retires every
current case for its successor. With the policy unchanged that successor is
the same proposal set, so preparation admits the same case again, and it
continues its episode with the owner's answers (#1568). An automatic decision recorded under Automatic stays on
that case and plans and writes nothing outside the Automatic posture.
It decides only in the generation it was recorded under: confirming Automatic
requeues the prepared works, so each is decided again from what Preview
showed (#1573), and an earlier decision keeps no field out of Attention
(#1601).

The harness (`tests/support/prepared_library.py`) drives a real SQLite
repository and the real ``ReviewPreparationPipeline`` with stages that call
the real report, proposal, admission and projection seams.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.adapters.persistence.metadata_reports import (
    open_repository,
)
from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.application.field_effects import (
    GuardedApplicationError,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.review_web import (
    InspectCurrentReviewCase,
)
from mechanical_librarian.domain.automatic_decision import AutomaticDecision
from mechanical_librarian.domain.field_effect import (
    FieldEffectError,
    FieldEffectNoChange,
    field_value_version,
)
from mechanical_librarian.domain.human_decision import HumanDecision, HumanDecisionError
from mechanical_librarian.domain.metadata_policy import (
    DEFAULT_OPTIONS,
    OPTION_CATALOGUE,
    MetadataPolicyProfile,
    human_review_destinations,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import (
    TargetDestination,
)
from mechanical_librarian.domain.proposal_set import ProposalSet, evaluate_report
from mechanical_librarian.domain.review_case import ReviewAdmissionResult, ReviewCase
from mechanical_librarian.interfaces.web.app import _ActivityResponse
from tests.support.audiobook_reports import (
    publish_audiobook_preview,
    synthetic_audiobook_bibliographic_report,
)
from tests.support.owner_field_record import record_recomputes  # noqa: F401
from tests.support.prepared_library import LINEAGE as _LINEAGE
from tests.support.prepared_library import PRODUCER as _PRODUCER
from tests.support.prepared_library import Library as _Library
from tests.support.prepared_library import accept as _accept
from tests.support.prepared_library import applier as _applier
from tests.support.prepared_library import library as library
from tests.support.prepared_library import planned as _planned
from tests.support.prepared_library import prepared as _prepared
from tests.support.prepared_library import preview_library as preview_library


def test_leaving_automatic_with_an_unchanged_policy_keeps_the_review_case(
    library: _Library,
) -> None:
    connection = library.root._connection
    case_before = library.case_id()
    attention_before = library.attention()
    needs_before = [item["item"] for item in library.library("needs_attention")]
    automatic_count = connection.execute(
        "SELECT count(*) FROM automatic_decisions"
    ).fetchone()
    assert attention_before and needs_before == ["item-1"]
    assert "authors" not in {destination for _, destination in attention_before}

    library.leave_automatic()

    assert library.admissions[-1] == ("admitted", case_before)
    assert library.evaluations[-1] == library.evaluations[0]
    assert library.external == []
    assert connection.execute(
        "SELECT review_case_id,checkpoint,outcome FROM current_review_case_lineages "
        "WHERE " + _LINEAGE
    ).fetchone() == (case_before, 3, "admitted")
    assert connection.execute(
        "SELECT checkpoint,actor,from_review_case_id,to_review_case_id,kind,reason "
        "FROM review_case_transitions WHERE item_id='item-1' ORDER BY checkpoint"
    ).fetchall() == [
        (1, "review-admission-v1", None, case_before, "admitted", "actionable"),
        (2, "policy-activation-v1", case_before, None, "retired", "policy_changed"),
        (3, "review-admission-v1", None, case_before, "admitted", "actionable"),
    ]
    assert connection.execute(
        "SELECT stage,state,outcome,review_case_id FROM review_preparation_work "
        "WHERE item_id='item-1'"
    ).fetchone() == ("complete", "complete", "case", case_before)
    assert connection.execute(
        "SELECT state,total FROM metadata_policy_transitions "
        "ORDER BY generation DESC LIMIT 1"
    ).fetchone() == ("completed", 1)
    assert connection.execute(
        "SELECT state FROM metadata_policy_transition_items"
    ).fetchall() == [("complete",)]

    # Attention and Library read the work as before the switch.
    assert library.attention() == attention_before
    assert [item["item"] for item in library.library("needs_attention")] == needs_before
    (every,) = library.library("all")
    assert (every["detail_kind"], every["review_case_id"]) == ("case", case_before)

    # The automatic decision stays (append-only) and keeps its field out of
    # Attention, as a Preview successor's Automatic field is kept out.
    assert (
        connection.execute("SELECT count(*) FROM automatic_decisions").fetchone()
        == automatic_count
    )
    assert library.automatic().review_case_id == case_before

    # Admitting the same proposal set again replays and records nothing.
    transitions = connection.execute(
        "SELECT count(*) FROM review_case_transitions"
    ).fetchone()
    proposal = ProposalSet.from_json(library.root.get_proposal(library.evaluations[-1]))
    replay = ReviewAdmissionResult.from_json(library.root.admit_review_case(proposal))
    assert (replay.status, replay.review_case_id) == ("replayed", case_before)
    assert (
        connection.execute("SELECT count(*) FROM review_case_transitions").fetchone()
        == transitions
    )


def test_owner_answers_from_before_leaving_automatic_are_kept(
    library: _Library, tmp_path: Path
) -> None:
    case = library.case()
    asked = [
        str(value)
        for _, value in library.attention()
        if value in {proposal["destination"] for proposal in case.proposals}
    ]
    assert len(asked) >= 2
    planned_destination, waiting_destination = asked[:2]
    decision = _accept(case, planned_destination, "before-leaving-automatic", 11)
    library.root.record_human_decision(decision)
    library.root.grant_field_effect_consents(
        "primary", ("ebooks",), (planned_destination,), 12
    )
    scheduler, service, target = _applier(library, tmp_path)
    library.now[0] = 13
    scheduler.schedule()
    unclaimed = _planned(library)[decision.decision_id]
    assert unclaimed.state == "planned"
    # Answered after the applier ran, so nothing has planned it yet.
    waiting = _accept(case, waiting_destination, "before-leaving-automatic-2", 14)
    library.root.record_human_decision(waiting)
    assert waiting.decision_id in library.pending()
    answered = {(case.review_case_id, planned_destination)} | {
        (case.review_case_id, waiting_destination)
    }
    assert not answered & set(library.attention())

    library.leave_automatic()

    # The same case continues its episode, so the owner's answers stay
    # current: nothing asks again, and both still write (#1568).
    assert library.case_id() == case.review_case_id
    assert library.root._connection.execute(
        "SELECT count(*) FROM review_case_episodes WHERE review_case_id=?",
        (case.review_case_id,),
    ).fetchone() == (0,)
    assert not answered & set(library.attention())
    read = _read(library, scheduler)
    for destination, recorded in (
        (planned_destination, decision),
        (waiting_destination, waiting),
    ):
        field = read["fields"][destination]
        assert field["human_decision"]["decision_id"] == recorded.decision_id
        assert field["classification"] == "human_recorded"
        assert field["needs_attention"] is False
    assert waiting.decision_id in library.pending()
    assert library.root.field_effect_authority_is_current(unclaimed.plan.authority)
    target.seed(unclaimed.plan)
    assert service.apply(unclaimed.plan.plan_id).state == "verified"
    assert (unclaimed.plan.plan_id, "verified", planned_destination) in {
        (event["change_id"], event["state"], event["destination"])
        for event in cast(
            list[dict[str, Any]], _read(library, scheduler)["activity"]["events"]
        )
    }

    # Confirming Automatic decides the work again, and the owner's answers
    # stay: no automatic decision is recorded on a field the owner decided,
    # and the waiting answer still plans and writes (#1573).
    library.root.activate_qualified_automatic(40)
    library.now[0] = 41
    library.drain()
    assert library.decisions() == [("authors", 2), ("authors", 3)]
    assert not answered & set(library.attention())
    read = _read(library, scheduler)
    for destination, recorded in (
        (planned_destination, decision),
        (waiting_destination, waiting),
    ):
        field = read["fields"][destination]
        assert field["human_decision"]["decision_id"] == recorded.decision_id
        assert field["classification"] == "human_recorded"
    assert waiting.decision_id in library.pending()
    library.root.grant_field_effect_consents(
        "primary", ("ebooks",), (waiting_destination,), 42
    )
    library.now[0] = 43
    scheduler.schedule()
    later = _planned(library)[waiting.decision_id].plan
    target.seed(later)
    assert service.apply(later.plan_id).state == "verified"


def test_an_owner_answer_on_an_automatic_field_outlasts_confirming_automatic(
    library: _Library, tmp_path: Path
) -> None:
    """The owner's answer outlasts confirming Automatic and still writes.

    Automatic decides the field again in the new generation, as on a later
    case of the work, and the answer's hold keeps that decision unwritten,
    so the field shows the hold and Resume as it did before leaving.
    """
    automatic = library.automatic()
    owner = _accept(library.case(), "authors", "override-before-leaving", 11)
    library.root.record_human_decision(owner)
    library.root.grant_field_effect_consents("primary", ("ebooks",), ("authors",), 12)

    library.leave_automatic()
    # The owner's answer, not the earlier automatic choice, decides the
    # field in Preview, so the case shows no earlier choice (#1575).
    preview = _read_fields(library)["authors"]
    assert preview["human_decision"]["decision_id"] == owner.decision_id
    assert preview["earlier_automatic_value"] is None
    library.root.activate_qualified_automatic(40)
    library.now[0] = 41
    library.drain()

    assert library.decisions() == [("authors", 2), ("authors", 3)]
    assert automatic.decision_id not in library.pending()
    assert library.automatic(3).decision_id not in library.pending()
    field = _read_fields(library)["authors"]
    assert field["human_decision"]["decision_id"] == owner.decision_id
    assert field["classification"] == "human_recorded"
    assert field["manual_hold"] is True
    assert owner.decision_id in library.pending()
    scheduler, service, target = _applier(library, tmp_path)
    library.now[0] = 42
    scheduler.schedule()
    plan = _planned(library)[owner.decision_id].plan
    target.seed(plan)
    assert service.apply(plan.plan_id).state == "verified"


@pytest.mark.parametrize(
    "episode",
    ["current", "earlier", "released"],
    ids=["current", "earlier", "released"],
)
def test_a_held_field_keeps_its_hold_and_resume_after_automatic_is_left_and_confirmed(
    library: _Library, episode: str
) -> None:
    """Leaving Automatic and confirming it again keeps the field's hold and
    Resume, and Resume still hands the field back to Automatic.

    `earlier` is an answer from an earlier episode of the case (a work whose
    content went away and came back, #1358), which the case reads as no
    answer: before this fix the field then read No change, with no hold and
    no Resume, while the answer still kept Automatic off it. `released` is an
    answer whose hold Resume already released before leaving: Automatic's
    decision was waiting to be written, and it still is after confirming.
    """
    library.automatic()
    owner = _accept(library.case(), "authors", "held-before-leaving", 11)
    library.root.record_human_decision(owner)
    case_id = library.case_id()
    if episode == "earlier":
        # The work returned to this content: a new episode starts after the
        # answer, as admission records it (#1358).
        library.root._connection.execute(
            "INSERT INTO review_case_episodes(review_case_id,decision_floor,"
            "event_floor) VALUES(?,(SELECT MAX(ordinal) FROM human_decisions "
            "WHERE review_case_id=?),0)",
            (case_id, case_id),
        )
        library.root._connection.commit()
    before = _read_fields(library)["authors"]
    assert before["manual_hold"] is True
    if episode == "released":
        assert library.root.clear_field_effect_manual_hold(
            case_id, "authors", 12, owner.decision_id
        )
        assert library.automatic().decision_id in library.pending()

    library.leave_automatic()
    library.root.activate_qualified_automatic(40)
    library.now[0] = 41
    library.drain()

    assert library.case_id() == case_id
    assert library.decisions() == [("authors", 2), ("authors", 3)]
    again = library.automatic(3)
    assert again.review_case_id == case_id
    if episode == "released":
        assert again.decision_id in library.pending()
        assert _read_fields(library)["authors"]["manual_hold"] is False
        return
    assert again.decision_id not in library.pending()
    field = _read_fields(library)["authors"]
    assert field["manual_hold"] is True
    assert field["classification"] == "human_recorded"
    assert field["classification_cause"] == "human_decided"
    assert (field["human_decision"] is None) is (episode == "earlier")

    assert library.root.clear_field_effect_manual_hold(
        case_id, "authors", 42, owner.decision_id
    )
    assert again.decision_id in library.pending()
    assert _read_fields(library)["authors"]["manual_hold"] is False


def test_an_answer_with_no_earlier_automatic_decision_still_keeps_automatic_off(
    preview_library: _Library,
) -> None:
    """Only a field Automatic had decided on the case is decided again under
    its hold. An answer given in Preview, before Automatic decided anything on
    the case, still keeps Automatic from recording a decision there (ADR-0085,
    Amendment 1: it shows as an ordinary answer until a later case)."""
    library = preview_library
    owner = _accept(library.case(), "authors", "answered-in-preview", 11)
    library.root.record_human_decision(owner)
    library.root.activate_qualified_automatic(40)
    library.now[0] = 41
    library.drain()
    assert ("authors", library.generation()) not in library.decisions()
    assert _read_fields(library)["authors"]["manual_hold"] is False


@pytest.mark.parametrize("unpinned", [True, False], ids=["unpinned", "pinned"])
def test_a_pin_taken_back_before_automatic_decided_shows_the_hold_once_it_is_on(
    preview_library: _Library, tmp_path: Path, unpinned: bool
) -> None:
    """A pin taken back in Preview, before Automatic decided anything on the
    case, leaves a hold Automatic decides under once it is on: the field shows
    the hold and Resume on that case, and Resume plans the decision (ADR-0085,
    Amendment 6). Before, the unpin refused the decision, so the field read No
    change with no hold and no Resume until a later case. Activity notes the
    held choice. A pin still in force keeps Automatic off it (Amendment 2)."""
    library = preview_library
    root = library.root
    pin = _owner(library, "pin", 11)
    root.record_human_decision(pin)
    if unpinned:
        root.record_human_decision(_owner(library, "unpin", 12))
    _confirm_automatic(library, 40)

    field = _read_fields(library)["authors"]
    if not unpinned:
        assert ("authors", library.generation()) not in library.decisions()
        assert field["manual_hold"] is False
        assert field["classification_cause"] == "pinned"
        return
    decided = library.automatic(library.generation())
    assert decided.review_case_id == library.case_id()
    assert decided.decision_id not in library.pending()
    assert field["manual_hold"] is True
    assert (field["classification"], field["classification_cause"]) == (
        "human_recorded",
        "human_decided",
    )
    scheduler, _, _ = _applier(library, tmp_path)
    assert _note(library, scheduler, decided.decision_id) == "held"
    assert root.clear_field_effect_manual_hold(
        library.case_id(), "authors", 42, pin.decision_id
    )
    assert decided.decision_id in library.pending()
    assert _read_fields(library)["authors"]["manual_hold"] is False
    assert _note(library, scheduler, decided.decision_id) is None


def test_a_title_pin_taken_back_in_preview_holds_the_whole_bundle(
    tmp_path: Path,
) -> None:
    """An audiobook's split title and subtitle are decided as one bundle. A
    pin on the title taken back in Preview leaves a hold Automatic decides the
    bundle under once it is on: the title shows the hold, and Resume on it
    lets the bundle plan (ADR-0085, Amendment 6)."""
    root = open_repository(tmp_path / "pair.sqlite3", cross_thread=True)
    try:
        report = synthetic_audiobook_bibliographic_report()
        case_id, _policy = publish_audiobook_preview(
            root,
            report,
            ("title_layout", "subtitle"),
            selected_options={
                "title_layout": "title_and_subtitle",
                "subtitle": "corroborated_split",
            },
            preview=True,
        )
        root.persist_shadow_outcome(case_id, 4)
        case = ReviewCase.create_from_data(
            json.loads(root.get_review_case(case_id))["case"]
        )
        proposal_id = next(
            value["proposal_id"]
            for value in case.proposals
            if value["destination"] == "title"
        )
        pin, unpin = (
            HumanDecision.create(
                case,
                proposal_id=str(proposal_id),
                target_fingerprint=case.target_fingerprint,
                destination="title",
                actor="owner",
                outcome=outcome,
                selected_value=None,
                idempotency_key=f"owner-command-{outcome}-title",
                occurred_at=at,
            )
            for outcome, at in (("pin", 7), ("unpin", 8))
        )
        root.record_human_decision(pin)
        root.record_human_decision(unpin)
        root.activate_qualified_automatic(40)
        root.reconcile_case_level(case_id, 41)
        root.persist_shadow_outcome(case_id, 41)

        bundles = root._connection.execute(
            "SELECT destination,json_extract(document,'$.bundle_id') "
            "FROM automatic_decisions ORDER BY destination"
        ).fetchall()
        assert [row[0] for row in bundles] == ["subtitle", "title"]
        assert bundles[0][1] is not None and bundles[0][1] == bundles[1][1]
        detail = InspectCurrentReviewCase(root).run(case_id)
        fields = {str(field["destination"]): field for field in detail["fields"]}
        assert fields["title"]["manual_hold"] is True
        assert root.clear_field_effect_manual_hold(
            case_id, "title", 50, pin.decision_id
        )
        detail = InspectCurrentReviewCase(root).run(case_id)
        fields = {str(field["destination"]): field for field in detail["fields"]}
        assert fields["title"]["manual_hold"] is False
    finally:
        root.close()


def test_an_owner_plan_waits_while_its_case_is_retired_for_reconciliation(
    library: _Library, tmp_path: Path
) -> None:
    """A plan picked between the retirement and re-admission still writes."""
    case = library.case()
    destination = next(
        str(value)
        for _, value in library.attention()
        if value in {proposal["destination"] for proposal in case.proposals}
    )
    decision = _accept(case, destination, "during-reconciliation", 11)
    library.root.record_human_decision(decision)
    library.root.grant_field_effect_consents("primary", ("ebooks",), (destination,), 12)
    scheduler, service, target = _applier(library, tmp_path)
    library.now[0] = 13
    scheduler.schedule()
    plan = _planned(library)[decision.decision_id].plan
    target.seed(plan)

    # Leaving Automatic retires the case; preparation has not re-admitted it.
    assert library.root.enter_shadow_preview(20, ("ebooks",)) == "preview"
    assert library.root._connection.execute(
        "SELECT review_case_id FROM current_review_case_lineages WHERE " + _LINEAGE
    ).fetchone() == (None,)
    assert library.root.next_field_effect_plan_id(None) is None
    with pytest.raises(GuardedApplicationError, match="deferred"):
        service.apply(plan.plan_id)
    assert _planned(library)[decision.decision_id].state == "planned"

    library.now[0] = 30
    library.drain()
    assert library.case_id() == case.review_case_id
    assert library.root.next_field_effect_plan_id(None) == (plan.plan_id, "ebooks")
    assert service.apply(plan.plan_id).state == "verified"


def test_an_automatic_decision_writes_only_in_the_automatic_posture(
    library: _Library, tmp_path: Path
) -> None:
    automatic = library.automatic()
    library.root.grant_field_effect_consents(
        "primary", ("ebooks",), ("authors", "title", "language"), 12
    )
    assert automatic.decision_id in library.pending()

    library.leave_automatic()

    assert library.root.shadow_posture() == "preview"
    assert library.case_id() == automatic.review_case_id
    # Write permission outlives the switch, so only posture keeps the earlier
    # automatic decision from planning and writing.
    assert (
        library.root.current_field_effect_consent("primary", "ebooks", "authors")
        is not None
    )
    assert automatic.decision_id not in library.pending()
    assert not [
        row
        for row in library.root.pending_field_effect_decision_counts()
        if row["origin"] == "automatic"
    ]

    # An owner decision in the new episode still plans and writes in Preview.
    case = library.case()
    destination = next(
        value for _, value in library.attention() if value in {"title", "language"}
    )
    owner = _accept(case, str(destination), "after-leaving-automatic", 31)
    library.root.record_human_decision(owner)
    scheduler, service, target = _applier(library, tmp_path)
    library.now[0] = 32
    scheduler.schedule()
    planned = _planned(library)
    assert automatic.decision_id not in planned
    target.seed(planned[owner.decision_id].plan)
    assert service.apply(planned[owner.decision_id].plan.plan_id).state == "verified"

    # Confirming Automatic again leaves the earlier decision as history: it
    # decides nothing in this generation, so it is not pending and still
    # authorizes no write (#1573).
    library.root.activate_qualified_automatic(40)
    assert automatic.decision_id not in library.pending()
    with pytest.raises(FieldEffectError, match="authority_binding"):
        PersistedFieldEffectAuthority(library.root).resolve(automatic)
    library.now[0] = 41
    scheduler.schedule()
    assert automatic.decision_id not in _planned(library)

    # Confirming requeued the work, and projecting it in Automatic decides the
    # field again in this generation from the outcome Preview showed.
    library.drain()
    assert library.decisions() == [("authors", 2), ("authors", 3)]
    fresh = library.automatic(3)
    assert fresh.review_case_id == automatic.review_case_id
    assert fresh.decision_id in library.pending()
    PersistedFieldEffectAuthority(library.root).resolve(fresh)
    library.now[0] = 42
    scheduler.schedule()
    planned = _planned(library)
    assert automatic.decision_id not in planned
    renewed = planned[fresh.decision_id].plan
    target.seed(renewed)
    assert service.apply(renewed.plan_id).state == "verified"
    assert fresh.decision_id not in library.pending()
    assert (renewed.plan_id, "verified", "authors") in {
        (event["change_id"], event["state"], event["destination"])
        for event in cast(
            list[dict[str, Any]], _read(library, scheduler)["activity"]["events"]
        )
    }


@pytest.mark.parametrize("confirm_automatic", [False, True])
def test_an_automatic_plan_from_before_leaving_automatic_writes_nothing(
    library: _Library, tmp_path: Path, confirm_automatic: bool
) -> None:
    """The plan waits in Preview and goes stale after Confirm Automatic."""
    automatic = library.automatic()
    library.root.grant_field_effect_consents("primary", ("ebooks",), ("authors",), 12)
    scheduler, service, target = _applier(library, tmp_path)
    library.now[0] = 13
    scheduler.schedule()
    plan = _planned(library)[automatic.decision_id].plan
    assert library.root.field_effect_authority_is_current(plan.authority)
    assert _waiting(scheduler) == (1, "authors")

    library.leave_automatic()

    assert library.case_id() == automatic.review_case_id
    assert not library.root.field_effect_authority_is_current(plan.authority)
    if confirm_automatic:
        # The plan was never claimed, and its decision is pending again in the
        # Automatic posture, but it belongs to the earlier generation.
        library.root.activate_qualified_automatic(40)
        assert library.root.shadow_posture() == "automatic"
        assert not library.root.field_effect_authority_is_current(plan.authority)
    target.seed(plan)
    before = target.read(plan).value
    if not confirm_automatic:
        # Outside Automatic the plan waits rather than being consumed, and
        # Activity neither counts it as ready nor names it as the next write.
        assert _waiting(scheduler) == (0, None)
        assert library.root.next_field_effect_plan_id(None) is None
        # A plan selected just before the posture changed is an empty poll.
        with patch.object(
            library.root,
            "next_field_effect_plan_id",
            return_value=(plan.plan_id, "ebooks"),
        ):
            assert scheduler.run_cycle() is True
        with pytest.raises(GuardedApplicationError, match="deferred"):
            service.apply(plan.plan_id)
        assert _planned(library)[automatic.decision_id].state == "planned"
        library.root.activate_qualified_automatic(40)
    # In Automatic it is ready again, and is selected and goes stale: it
    # belongs to the earlier generation.
    assert _waiting(scheduler) == (1, "authors")
    assert library.root.next_field_effect_plan_id(None) == (plan.plan_id, "ebooks")
    applied = service.apply(plan.plan_id)
    assert (applied.state, applied.failure) == ("stale", "authority")
    assert target.read(plan).value == before

    # Confirming requeued the work, so it is decided again in this generation
    # and that decision plans and writes; the earlier plan stays stale (#1573).
    library.now[0] = 50
    library.drain()
    fresh = library.automatic(3)
    scheduler.schedule()
    operations = _planned(library)
    assert operations[automatic.decision_id].state == "stale"
    renewed = operations[fresh.decision_id].plan
    assert renewed.plan_id != plan.plan_id
    target.seed(renewed)
    assert service.apply(renewed.plan_id).state == "verified"


def _waiting(scheduler: ContinuousGuardedApplication) -> tuple[int, object]:
    """Activity's ready count and the write it names as next."""
    activity = scheduler.activity(
        search=None, event_type="all", range_days=30, page=1, page_size=25
    )
    waiting = cast(dict[str, Any], activity["waiting"])
    current = cast(dict[str, Any] | None, activity["current_waiting"])
    return waiting["scheduled"], None if current is None else current["destination"]


def _read(library: _Library, scheduler: ContinuousGuardedApplication) -> dict[str, Any]:
    """Read the case page and Activity as the review API does."""
    detail = InspectCurrentReviewCase(library.root).run(library.case_id())
    activity = scheduler.activity(
        search=None, event_type="all", range_days=30, page=1, page_size=25
    )
    fields = {
        str(field["destination"]): field
        for field in cast(list[dict[str, Any]], detail["fields"])
    }
    return {"fields": fields, "activity": activity}


def test_the_case_and_activity_read_after_leaving_automatic(
    library: _Library, tmp_path: Path
) -> None:
    scheduler, _, _ = _applier(library, tmp_path)
    automatic = library.automatic()
    before = _read(library, scheduler)
    authors = before["fields"]["authors"]
    assert authors["automatic_decision"]["decision_id"] == automatic.decision_id
    assert authors["classification"] == "automatic_recorded"
    assert authors["earlier_automatic_value"] is None

    library.leave_automatic()

    # The earlier automatic decision decides nothing in the new generation, so
    # the case reads its field as any case of that generation does, and
    # Activity keeps it as history with nothing waiting to write.
    for posture in ("preview", "automatic"):
        if posture == "automatic":
            library.root.activate_qualified_automatic(40)
            library.now[0] = 41
        assert library.root.shadow_posture() == posture
        read = _read(library, scheduler)
        fields = read["fields"]
        assert fields["authors"]["automatic_decision"] is None
        assert fields["authors"]["classification"] != "automatic_recorded"
        assert fields["authors"]["needs_attention"] is False
        # A source offers other authors, so the field never says none did.
        assert fields["authors"]["classification_reason"] == _NOTHING_TO_DECIDE
        # Only Preview shows what Automatic chose before (#1575): after Turn
        # on Automatic the field is about to be decided again.
        assert fields["authors"]["earlier_automatic_value"] == (
            {"kind": "list", "value": ["Ada Example"]} if posture == "preview" else None
        )
        assert {
            destination
            for destination, field in fields.items()
            if field["earlier_automatic_value"] is not None
        } == ({"authors"} if posture == "preview" else set())
        assert {
            destination
            for destination, field in fields.items()
            if field["needs_attention"]
        } == {destination for _, destination in library.attention()}
        activity = read["activity"]
        (recorded,) = (
            event
            for event in cast(list[dict[str, Any]], activity["events"])
            if event["event_id"] == automatic.decision_id
        )
        assert (recorded["state"], recorded["failure"]) == ("recorded", None)
        (configured,) = cast(list[dict[str, Any]], activity["configured_libraries"])
        assert configured["prepared"]["automatic"] == 0

    # Confirming requeued the work: projecting it in Automatic decides authors
    # in this generation, and the case, Attention and Activity agree (#1573).
    library.drain()
    fresh = library.automatic(3)
    read = _read(library, scheduler)
    fields = read["fields"]
    assert fields["authors"]["automatic_decision"]["decision_id"] == fresh.decision_id
    assert fields["authors"]["classification"] == "automatic_recorded"
    assert fields["authors"]["earlier_automatic_value"] is None
    assert fields["authors"]["needs_attention"] is False
    assert {
        destination for destination, field in fields.items() if field["needs_attention"]
    } == {destination for _, destination in library.attention()}
    (configured,) = cast(list[dict[str, Any]], read["activity"]["configured_libraries"])
    assert configured["prepared"]["automatic"] == 1


def _note(
    library: _Library, scheduler: ContinuousGuardedApplication, event_id: str
) -> str | None:
    """The note Activity gives one row, read as the review API serves it."""
    activity = _read(library, scheduler)["activity"]
    _ActivityResponse.model_validate(activity)
    (event,) = (
        event
        for event in cast(list[dict[str, Any]], activity["events"])
        if event["event_id"] == event_id
    )
    return cast(str | None, event["note"])


@pytest.mark.parametrize(
    "variant", ("answer", "pin", "written"), ids=("answer", "pin", "written")
)
def test_activity_notes_an_automatic_choice_a_hold_keeps_unwritten(
    library: _Library, tmp_path: Path, variant: str
) -> None:
    """Automatic's choice on a field an owner answer holds reads Held by you
    while Automatic is on and the hold keeps it unwritten; a pinned hold,
    a choice already written, an earlier generation's choice and a released
    hold read no note, nor does a Resume on its way; checks that end with
    failures bring it back (ADR-0085, #1755)."""
    root = library.root
    automatic = library.automatic()
    case_id = library.case_id()
    if variant == "written":
        _, scheduler = _automatic_written(library, tmp_path)
    else:
        scheduler, _, _ = _applier(library, tmp_path)
    assert _note(library, scheduler, automatic.decision_id) is None
    owner = _accept(library.case(), "authors", "held-for-the-note", 14)
    root.record_human_decision(owner)
    if variant == "pin":
        # A pinned hold offers Unpin, not Resume.
        root.record_human_decision(_owner(library, "pin", 15))
    if variant != "answer":
        assert _note(library, scheduler, automatic.decision_id) is None
        return
    assert _note(library, scheduler, automatic.decision_id) == "held"

    library.leave_automatic()
    assert _note(library, scheduler, automatic.decision_id) is None

    _confirm_automatic(library, 40)
    again = library.automatic(3)
    assert _note(library, scheduler, again.decision_id) == "held"
    assert _note(library, scheduler, automatic.decision_id) is None

    # A requested Resume is on its way, so the note goes; checks that end
    # with failures keep the hold and ask for Resume again, so it is back.
    scan = root.request_review_scan("primary", "ebooks", "lib-1", _PRODUCER, 41)
    assert root.request_field_effect_manual_return(
        case_id, "authors", owner.decision_id, scan.scan_id, 41, 41
    )
    assert _note(library, scheduler, again.decision_id) is None
    with root._connection:
        root._connection.execute(
            "UPDATE review_preparation_scans SET state='completed_with_failures' "
            "WHERE scan_id=?",
            (scan.scan_id,),
        )
    assert _read_fields(library)["authors"]["manual_return_failed"] is True
    assert _note(library, scheduler, again.decision_id) == "held"

    assert root.clear_field_effect_manual_hold(
        case_id, "authors", 42, owner.decision_id
    )
    assert _note(library, scheduler, again.decision_id) is None


def test_activity_gives_no_held_note_to_a_choice_with_nothing_to_write(
    library: _Library, tmp_path: Path
) -> None:
    """A choice recorded as needing no change has no write waiting for
    Resume, so a later hold gives it no note (ADR-0085)."""
    root = library.root
    automatic = library.automatic()
    case = library.case()
    scheduler, _, _ = _applier(library, tmp_path)
    root.record_human_decision(
        _accept(case, "authors", "held-for-the-no-change-note", 14)
    )
    assert _note(library, scheduler, automatic.decision_id) == "held"
    record = FieldEffectNoChange.create(
        decision_id=automatic.decision_id,
        review_case_id=automatic.review_case_id,
        report_id=automatic.report_id,
        connection=str(case.target["connection"]),
        library=str(case.target["library"]),
        work_id=str(case.target["item"]),
        destination="authors",
        target_fingerprint=automatic.target_fingerprint,
        target_version="0" * 64,
        occurred_at=15,
        origin="automatic_policy",
    )
    with root._connection:
        root._connection.execute(
            "INSERT INTO field_effect_no_changes(decision_id,review_case_id,"
            "report_id,connection_name,library_name,work_id,destination,"
            "target_fingerprint,target_version,origin,occurred_at,event_id) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                record.decision_id,
                record.review_case_id,
                record.report_id,
                record.connection,
                record.library,
                record.work_id,
                record.destination,
                record.target_fingerprint,
                record.target_version,
                record.origin,
                record.occurred_at,
                record.event_id,
            ),
        )
    assert _note(library, scheduler, automatic.decision_id) is None


@pytest.mark.parametrize("held", ["title", "subtitle"])
def test_a_hold_on_one_field_of_a_bundle_notes_both_choices(
    tmp_path: Path, held: str
) -> None:
    """Automatic writes an audiobook's split title and subtitle together, so
    an answer holding either keeps both choices unwritten until Resume, and
    Activity notes both (ADR-0085)."""
    root = open_repository(tmp_path / "pair.sqlite3", cross_thread=True)
    try:
        case_id, _ = publish_audiobook_preview(
            root,
            synthetic_audiobook_bibliographic_report(),
            ("title_layout", "subtitle"),
            selected_options={
                "title_layout": "title_and_subtitle",
                "subtitle": "corroborated_split",
            },
        )
        root.persist_shadow_outcome(case_id, 4)
        pair = [
            str(row[0])
            for row in root._connection.execute(
                "SELECT decision_id FROM automatic_decisions WHERE "
                "review_case_id=? AND destination IN ('title','subtitle') "
                "AND policy_generation=2",
                (case_id,),
            )
        ]
        assert len(pair) == 2
        assert (
            root._field_effect_activity._activity_page_notes_once([], [], [], pair)
            == {}
        )
        case = ReviewCase.create_from_data(
            json.loads(root.get_review_case(case_id))["case"]
        )
        root.record_human_decision(_accept(case, held, f"held-for-the-{held}-pair", 7))
        assert root._connection.execute(
            "SELECT destination FROM field_effect_manual_holds "
            "WHERE review_case_id=? AND active=1",
            (case_id,),
        ).fetchall() == [(held,)]
        assert root._field_effect_activity._activity_page_notes_once(
            [], [], [], pair
        ) == dict.fromkeys(pair, "held")
    finally:
        root.close()


def test_an_automatic_description_a_carried_hold_keeps_reads_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Automatic description advice admitted beside a hold carried from the
    work's earlier case waits for Resume like any automatic choice, so
    Activity notes it (ADR-0085)."""
    from tests.support.description_advice import (
        _prepared,
        qualify_description_advice,
    )

    qualify_description_advice(monkeypatch)
    repository, store, binding, original, _ = _prepared(tmp_path)
    try:
        store.admit(original.candidate_id, binding, "automatic_policy", "e" * 64, 11)
        (decision_id,) = repository._connection.execute(
            "SELECT decision_id FROM current_description_decisions "
            "WHERE review_case_id=?",
            (binding.review_case_id,),
        ).fetchone()
        assert decision_id.startswith("automatic_decision_")
        notes = repository._field_effect_activity._activity_page_notes_once
        assert notes([], [], [], [decision_id]) == {}
        # The row a later case of the work carries: no answer on this case.
        with repository._connection:
            repository._connection.execute(
                "INSERT INTO field_effect_manual_holds(review_case_id,destination,"
                "active,updated_at,document) VALUES(?,'description',1,12,?)",
                (
                    binding.review_case_id,
                    json.dumps(
                        {
                            "decision_id": "human_decision_" + "1" * 64,
                            "pending_return": None,
                            "pin": None,
                            "updated_at": 12,
                        }
                    ),
                ),
            )
        assert notes([], [], [], [decision_id]) == {decision_id: "held"}
    finally:
        repository.close()


def _asked(library: _Library) -> tuple[set[str], set[str]]:
    """The fields the case page asks for, and the ones Attention lists."""
    fields = _read_fields(library)
    return (
        {
            destination
            for destination, field in fields.items()
            if field["needs_attention"]
        },
        {str(destination) for _, destination in library.attention()},
    )


def _read_fields(library: _Library) -> dict[str, dict[str, Any]]:
    detail = InspectCurrentReviewCase(library.root).run(library.case_id())
    return {
        str(field["destination"]): field
        for field in cast(list[dict[str, Any]], detail["fields"])
    }


def test_pausing_after_leaving_automatic_lists_every_field_the_case_asks_for(
    library: _Library,
) -> None:
    """An earlier generation's decision keeps no field out of Attention (#1601)."""
    library.leave_automatic()
    assert library.root.shadow_posture() == "preview"

    library.root.pause_automatic_safety(True, 31)

    case_needs, attention = _asked(library)
    assert case_needs == attention == {"authors", "language", "title"}
    (row,) = library.library("all")
    assert row["attention_count"] == 3
    # A field that needs attention shows no earlier automatic choice (#1575).
    assert _read_fields(library)["authors"]["earlier_automatic_value"] is None

    # Resuming in Preview: an Automatic field without abstention is never
    # asked for, so authors leaves both again.
    library.root.pause_automatic_safety(False, 32)
    case_needs, attention = _asked(library)
    assert case_needs == attention == {"language", "title"}
    (row,) = library.library("all")
    assert row["attention_count"] == 2
    assert _read_fields(library)["authors"]["earlier_automatic_value"] == {
        "kind": "list",
        "value": ["Ada Example"],
    }


def test_pausing_in_automatic_keeps_a_decided_field_out_of_attention(
    library: _Library,
) -> None:
    """The current generation's decision settles its field while paused."""
    library.root.pause_automatic_safety(True, 31)
    case_needs, attention = _asked(library)
    assert case_needs == attention == {"language", "title"}


def test_returning_to_a_policy_decides_its_case_again_in_the_new_generation(
    library: _Library,
) -> None:
    """Policy A, then B, then A again, all in Automatic (#1573)."""
    root = library.root
    automatic = library.automatic()
    options = dict(DEFAULT_OPTIONS)
    destinations = {
        name: "unsupported" if name == "explicit" else "ask"
        for name in OPTION_CATALOGUE
    }
    options["contributor_names"] = "canonical"
    destinations["contributor_names"] = "automatic"
    destinations["series"] = "ignore"
    other = MetadataPolicyProfile.create("Other policy", options, destinations)
    other_resolved = resolve_policy(other, "ebooks")
    root.create_metadata_policy_draft(other.canonical_json)
    (first_policy,) = root._connection.execute(
        "SELECT active_policy_id FROM metadata_policy_state"
    ).fetchone()
    first_resolution = root._connection.execute(
        "SELECT resolved_policy_id,document FROM metadata_policy_resolutions "
        "WHERE generation=2 AND library_name='ebooks'"
    ).fetchone()
    root.activate_metadata_policy(
        other.policy_id,
        first_policy,
        (("ebooks", other_resolved.resolved_id, other_resolved.canonical_json),),
        20,
        expected_active_generation=2,
    )
    library.now[0] = 21
    library.drain()
    assert library.case_id() != automatic.review_case_id
    assert library.decisions() == [("authors", 2), ("authors", 3)]

    root.activate_metadata_policy(
        first_policy,
        other.policy_id,
        (("ebooks", *first_resolution),),
        30,
        expected_active_generation=3,
    )
    library.now[0] = 31
    library.drain()

    assert root.shadow_posture() == "automatic"
    assert library.case_id() == automatic.review_case_id
    assert library.generation() == 4
    assert library.decisions() == [("authors", 2), ("authors", 3), ("authors", 4)]
    fresh = library.automatic(4)
    assert fresh.review_case_id == automatic.review_case_id
    PersistedFieldEffectAuthority(root).resolve(fresh)
    with pytest.raises(FieldEffectError, match="authority_binding"):
        PersistedFieldEffectAuthority(root).resolve(automatic)
    assert fresh.decision_id in library.pending()
    assert automatic.decision_id not in library.pending()
    case_needs, attention = _asked(library)
    assert "authors" not in case_needs
    assert case_needs == attention


def _owner(library: _Library, outcome: str, at: int) -> HumanDecision:
    """An owner command that carries no value, on the authors of the case."""
    case = library.case()
    proposal = next(
        value for value in case.proposals if value["destination"] == "authors"
    )
    return HumanDecision.create(
        case,
        proposal_id=str(proposal["proposal_id"]),
        target_fingerprint=case.target_fingerprint,
        destination="authors",
        actor="owner",
        outcome=outcome,
        selected_value=None,
        idempotency_key=f"owner-command-{outcome}-{at}",
        occurred_at=at,
    )


_HELD = (
    "Your earlier decision still holds this field, so turning on Automatic "
    "leaves it as it is."
)
_NOTHING_TO_DECIDE = "Nothing to decide for this field."


@pytest.mark.parametrize(
    "owner",
    ("none", "earlier_episode", "earlier_episode_resumed", "unpinned"),
    ids=("none", "earlier_episode", "earlier_episode_resumed", "unpinned"),
)
def test_preview_shows_an_earlier_choice_only_where_automatic_decides_again(
    tmp_path: Path, owner: str
) -> None:
    """The work leaves its first case and comes back to it, a new episode
    (#1358). Preview shows Automatic's earlier choice, whose line says turning
    Automatic on decides the field again, only where it then decides the field
    and plans its write. An answer from the first episode keeps its hold, so
    Automatic decides under it and writes nothing until Resume (ADR-0085,
    Amendment 4), as does a pin taken back in Preview, whose hold stays.
    Once Resume released the answer's hold, Automatic decides and writes."""
    with _prepared(tmp_path, automatic=True, library_model="publication") as library:
        root, held = library.root, library.held
        assert held is not None
        first, authors = library.case_id(), held.authors
        if owner.startswith("earlier_episode"):
            answer = _accept(library.case(), "authors", "answer-first-episode", 11)
            root.record_human_decision(answer)
            assert _read_fields(library)["authors"]["manual_hold"] is True
            if owner == "earlier_episode_resumed":
                assert root.clear_field_effect_manual_hold(
                    first, "authors", 11, answer.decision_id
                )
        # The authors change in Audiobookshelf and change back, each read by
        # a scan.
        for value, at in ((("Someone Else",), 12), (authors, 14)):
            held.write(TargetDestination.AUTHORS, value)
            root.request_review_scan("primary", "ebooks", "lib-1", _PRODUCER, at)
            library.now[0] = at + 1
            library.drain()
            assert (library.case_id() == first) is (value == authors)
        library.leave_automatic()
        assert library.case_id() == first
        if owner == "unpinned":
            for outcome, at in (("pin", 31), ("unpin", 32)):
                root.record_human_decision(_owner(library, outcome, at))

        preview = _read_fields(library)["authors"]
        assert (preview["classification"], preview["human_decision"]) == (
            "unsupported",
            None,
        )
        shown = preview["earlier_automatic_value"]
        held_on = owner in {"earlier_episode", "unpinned"}
        # The hold that keeps Automatic off says so, without Resume: nothing
        # of Automatic's is held yet in Preview.
        assert (
            preview["classification_cause"],
            preview["classification_reason"],
            preview["manual_hold"],
            preview["needs_attention"],
        ) == (
            ("held_in_preview", _HELD, False, False)
            if held_on
            else ("nothing_to_decide", _NOTHING_TO_DECIDE, False, False)
        )
        assert shown == (
            None if held_on else {"kind": "list", "value": ["Ada Example"]}
        )
        _confirm_automatic(library, 40)
        field = _read_fields(library)["authors"]
        decided = field["automatic_decision"]
        assert decided is not None
        assert field["manual_hold"] is held_on
        assert (decided["decision_id"] in library.pending()) is (shown is not None)


def test_a_pin_taken_back_in_preview_holds_a_field_automatic_never_decided(
    preview_library: _Library,
) -> None:
    """A pin taken back in Preview leaves its hold, so the field reads held.
    Turning on Automatic then leaves its value as it is: Automatic decides the
    field under the hold and writes nothing until Resume (ADR-0085,
    Amendments 5 and 6)."""
    library = preview_library
    before = _read_fields(library)["authors"]["current"]
    for outcome, at in (("pin", 11), ("unpin", 12)):
        library.root.record_human_decision(_owner(library, outcome, at))
    preview = _read_fields(library)["authors"]
    assert (
        preview["classification_cause"],
        preview["classification_reason"],
        preview["manual_hold"],
        preview["needs_attention"],
    ) == ("held_in_preview", _HELD, False, False)

    _confirm_automatic(library, 40)

    assert ("authors", library.generation()) in library.decisions()
    field = _read_fields(library)["authors"]
    assert (field["classification_cause"], field["manual_hold"]) == (
        "human_decided",
        True,
    )
    assert field["current"] == before


def test_preview_reasons_say_nothing_to_decide_where_a_source_offers_another_value(
    preview_library: _Library,
) -> None:
    """A field no one holds that a source offers another value for never says
    no source offered one."""
    authors = _read_fields(preview_library)["authors"]
    assert (authors["classification_cause"], authors["classification_reason"]) == (
        "nothing_to_decide",
        _NOTHING_TO_DECIDE,
    )
    assert authors["preservation_only"] is False
    assert [value["value"] for value in authors["proposed_values"]] == [["Ada Example"]]
    assert authors["current"]["value"] == ["Current Writer"]


@pytest.mark.parametrize("pinned", (None, "title", "subtitle"))
def test_an_owner_row_on_one_field_of_a_bundle_hides_every_earlier_choice_of_it(
    tmp_path: Path, pinned: str | None
) -> None:
    """Automatic decides an audiobook's split title and subtitle as one
    bundle, all or none, so a pin on either field keeps it off both (ADR-0085,
    Amendment 2). Neither field then shows its earlier choice."""
    root = open_repository(tmp_path / "pair.sqlite3", cross_thread=True)
    try:
        report = synthetic_audiobook_bibliographic_report()
        case_id, policy = publish_audiobook_preview(
            root,
            report,
            ("title_layout", "subtitle"),
            selected_options={
                "title_layout": "title_and_subtitle",
                "subtitle": "corroborated_split",
            },
        )
        root.persist_shadow_outcome(case_id, 4)
        (bundle,) = root._connection.execute(
            "SELECT DISTINCT json_extract(document,'$.bundle_id') "
            "FROM automatic_decisions WHERE policy_generation=2"
        ).fetchall()
        assert bundle[0] is not None
        assert root.enter_shadow_preview(5, ("ebooks",)) == "preview"
        proposal = evaluate_report(
            report, policy.resolved_id, human_review_destinations(policy)
        )
        admitted = json.loads(root.admit_review_case(proposal))
        assert admitted["review_case_id"] == case_id
        root.persist_shadow_outcome(case_id, 6)
        if pinned is not None:
            case = ReviewCase.create_from_data(
                json.loads(root.get_review_case(case_id))["case"]
            )
            proposal_id = next(
                value["proposal_id"]
                for value in case.proposals
                if value["destination"] == pinned
            )
            root.record_human_decision(
                HumanDecision.create(
                    case,
                    proposal_id=str(proposal_id),
                    target_fingerprint=case.target_fingerprint,
                    destination=pinned,
                    actor="owner",
                    outcome="pin",
                    selected_value=None,
                    idempotency_key=f"owner-command-pin-{pinned}",
                    occurred_at=7,
                )
            )
        earlier = dict(
            root.read_current_review_case(case_id, None).earlier_automatic_values
        )
        assert set(earlier) == (set() if pinned else {"subtitle", "title"})
    finally:
        root.close()


def test_confirming_automatic_decides_the_works_first_prepared_in_preview(
    preview_library: _Library, tmp_path: Path
) -> None:
    """Start in Preview and confirm Automatic later (#1573)."""
    library = preview_library
    root = library.root
    assert root._connection.execute(
        "SELECT count(*) FROM shadow_decision_outcomes"
    ).fetchone() == (1,)
    assert library.decisions() == []

    root.activate_qualified_automatic(40)
    # The confirm transaction requeued the prepared work to projection.
    assert root._connection.execute(
        "SELECT stage,state FROM review_preparation_work WHERE item_id='item-1'"
    ).fetchone() == ("projection", "queued")
    library.now[0] = 41
    library.drain()

    # Projection is content-addressed: the outcome Preview showed decides.
    assert library.decisions() == [("authors", 2)]
    assert root._connection.execute(
        "SELECT count(*) FROM shadow_decision_outcomes"
    ).fetchone() == (1,)
    decided = library.automatic(2)
    PersistedFieldEffectAuthority(root).resolve(decided)
    fields = _read_fields(library)
    assert fields["authors"]["classification"] == "automatic_recorded"
    case_needs, attention = _asked(library)
    assert "authors" not in case_needs
    assert case_needs == attention
    root.grant_field_effect_consents("primary", ("ebooks",), ("authors",), 42)
    scheduler, service, target = _applier(library, tmp_path)
    library.now[0] = 43
    scheduler.schedule()
    plan = _planned(library)[decided.decision_id].plan
    target.seed(plan)
    assert service.apply(plan.plan_id).state == "verified"


def test_a_failed_requeue_leaves_confirm_automatic_undone(library: _Library) -> None:
    library.leave_automatic()
    with (
        patch.object(
            library.root._review_preparation,
            "_requeue_current_case_projections",
            side_effect=sqlite3.OperationalError("injected"),
        ),
        pytest.raises(
            MetadataReportError, match="automatic posture persistence failed"
        ),
    ):
        library.root.activate_qualified_automatic(40)
    assert library.root.shadow_posture() == "preview"
    assert library.root._connection.execute(
        "SELECT stage,state FROM review_preparation_work WHERE item_id='item-1'"
    ).fetchone() == ("complete", "complete")


def test_an_owner_decision_after_confirming_holds_the_current_generation(
    library: _Library, tmp_path: Path
) -> None:
    """The owner overrides and releases the decision that decides now (#1573)."""
    root = library.root
    root.grant_field_effect_consents("primary", ("ebooks",), ("authors",), 12)
    library.leave_automatic()
    root.activate_qualified_automatic(40)
    library.now[0] = 41
    library.drain()
    fresh = library.automatic(3)
    scheduler, _, _ = _applier(library, tmp_path)
    library.now[0] = 42
    scheduler.schedule()
    plan = _planned(library)[fresh.decision_id].plan
    assert root.field_effect_authority_is_current(plan.authority)

    owner = _accept(library.case(), "authors", "after-confirming-automatic", 43)
    root.record_human_decision(owner)

    # The owner's decision supersedes this generation's plan and holds its
    # decision, not the earlier generation's.
    assert not root.field_effect_authority_is_current(plan.authority)
    (held,) = root._connection.execute(
        "SELECT json_extract(document,'$.decision_id') FROM field_effect_manual_holds "
        "WHERE review_case_id=? AND destination='authors' AND active=1",
        (fresh.review_case_id,),
    ).fetchone()
    assert held == owner.decision_id

    # Releasing the hold makes this generation's decision current again.
    assert (
        root.clear_field_effect_manual_hold(fresh.review_case_id, "authors", 45)
        is not None
    )
    assert root._connection.execute(
        "SELECT decision_id FROM field_effect_authorities WHERE current=1 "
        "AND decision_id IN (?,?)",
        (library.automatic(2).decision_id, fresh.decision_id),
    ).fetchall() == [(fresh.decision_id,)]


@pytest.mark.parametrize("state", ["claimed", "applying", "reconciling"])
def test_an_earlier_generation_write_in_flight_still_refuses_an_owner_answer(
    library: _Library, tmp_path: Path, state: str
) -> None:
    """An earlier generation decides nothing, but its write is still out (#1573).

    Leaving Automatic readmits the case while the authors write is claimed,
    applying or reconciling. The owner cannot replace the decision until that
    service request is reconciled, as before generations were recorded.
    """
    root = library.root
    automatic = library.automatic()
    root.grant_field_effect_consents("primary", ("ebooks",), ("authors",), 12)
    scheduler, _, _ = _applier(library, tmp_path)
    library.now[0] = 13
    scheduler.schedule()
    plan = _planned(library)[automatic.decision_id].plan
    root.claim_field_effect(plan.plan_id, "planned", "c" * 64, 14)
    if state != "claimed":
        root.transition_field_effect(plan.plan_id, "claimed", "applying", 15)
    if state == "reconciling":
        root.transition_field_effect(plan.plan_id, "applying", "reconciling", 16)

    library.leave_automatic()

    assert library.generation() == 3
    assert library.case_id() == automatic.review_case_id
    assert root.get_field_effect(plan.plan_id).state == state
    owner = _accept(library.case(), "authors", "owner-while-writing", 40)
    with pytest.raises(HumanDecisionError, match="already_decided"):
        root.record_human_decision(owner)
    assert (
        root._connection.execute(
            "SELECT count(*) FROM human_decisions WHERE review_case_id=?",
            (automatic.review_case_id,),
        ).fetchone()[0]
        == 0
    )
    assert root.get_field_effect(plan.plan_id).state == state


def test_an_earlier_generation_write_settled_unwritten_lets_the_owner_answer(
    library: _Library, tmp_path: Path
) -> None:
    """A settled earlier-generation write no longer stands in the owner's way (#1573).

    The write went stale without changing the library, so the report still
    holds and leaving Automatic returns the same case, where the earlier
    generation's decision is replaced by the owner's answer.
    """
    root = library.root
    automatic = library.automatic()
    root.grant_field_effect_consents("primary", ("ebooks",), ("authors",), 12)
    scheduler, _, _ = _applier(library, tmp_path)
    library.now[0] = 13
    scheduler.schedule()
    plan = _planned(library)[automatic.decision_id].plan
    root.claim_field_effect(plan.plan_id, "planned", "c" * 64, 14)
    root.transition_field_effect(plan.plan_id, "claimed", "applying", 15)
    root.transition_field_effect(
        plan.plan_id, "applying", "stale", 16, "target_changed"
    )

    library.leave_automatic()

    assert library.external == []
    assert library.case_id() == automatic.review_case_id
    owner = _accept(library.case(), "authors", "owner-after-writing", 40)
    root.record_human_decision(owner)
    assert root._connection.execute(
        "SELECT decision_id FROM human_decisions WHERE review_case_id=?",
        (automatic.review_case_id,),
    ).fetchall() == [(owner.decision_id,)]


def _automatic_written(
    library: _Library, tmp_path: Path
) -> tuple[AutomaticDecision, ContinuousGuardedApplication]:
    """Automatic's authors decision, written and read back."""
    root = library.root
    automatic = library.automatic()
    root.grant_field_effect_consents("primary", ("ebooks",), ("authors",), 12)
    scheduler, service, target = _applier(library, tmp_path)
    library.now[0] = 13
    scheduler.schedule()
    plan = _planned(library)[automatic.decision_id].plan
    target.seed(plan)
    assert service.apply(plan.plan_id).state == "verified"
    return automatic, scheduler


def _settled_without_a_write(
    library: _Library, scheduler: ContinuousGuardedApplication, owner: HumanDecision
) -> None:
    """The owner's accept of the written value holds the field, never waits
    for a write, and is marked as needing none (ADR-0105)."""
    root = library.root
    library.now[0] += 1
    scheduler.schedule()
    assert owner.decision_id not in library.pending()
    assert owner.decision_id not in _planned(library)
    assert root.pending_field_effect_decision_counts() == ()
    assert root._connection.execute(
        "SELECT origin,target_version FROM field_effect_no_changes WHERE decision_id=?",
        (owner.decision_id,),
    ).fetchall() == [("human", field_value_version("authors", ["Ada Example"]))]
    (hold,) = root._connection.execute(
        "SELECT json_extract(document,'$.decision_id') FROM "
        "field_effect_manual_holds WHERE destination='authors' AND active=1"
    ).fetchone()
    assert hold == owner.decision_id
    assert _read(library, scheduler)["fields"]["authors"]["no_change_required"] is True


# Leaving Automatic after the write reads the work again into a new case
# (#1986), so the old case is pressed under Automatic, running or paused.
@pytest.mark.parametrize("posture", ["automatic", "automatic_paused"])
def test_choosing_automatics_written_value_needs_no_write(
    library: _Library, tmp_path: Path, posture: str
) -> None:
    """Pressing Automatic's card once its write landed records the owner's
    answer with nothing to write; Activity lists it as kept (ADR-0105)."""
    root = library.root
    automatic, scheduler = _automatic_written(library, tmp_path)
    if posture == "automatic_paused":
        root.pause_automatic_safety(True, 31)

    owner = _accept(library.case(), "authors", "owner-presses-automatics-card", 40)
    root.record_human_decision(owner)
    _settled_without_a_write(library, scheduler, owner)

    activity = cast(
        list[dict[str, Any]], _read(library, scheduler)["activity"]["events"]
    )
    # The owner's own row says the accept kept the written value, as History
    # does, never a change from the value the case read.
    (accepted,) = (event for event in activity if event["state"] == "accept")
    assert accepted["change"] == "Kept Ada Example"
    assert {fact["label"]: fact["value"] for fact in accepted["evidence"]} == {
        "Previous value": "Ada Example",
        "New value": "Ada Example",
        "Chosen by": "You",
    }
    (kept,) = (event for event in activity if event["state"] == "no_change_required")
    assert kept["change"] == "Kept Ada Example"
    # It is the owner's choice: Activity lists it with the owner's, not
    # Automatic's.
    assert (kept["event_type"], kept["category"]) == ("review", "owner")
    for event_type, listed in (("review", True), ("metadata", False)):
        filtered = scheduler.activity(
            search=None, event_type=event_type, range_days=30, page=1, page_size=25
        )
        assert (
            kept["event_id"]
            in {
                event["event_id"]
                for event in cast(list[dict[str, Any]], filtered["events"])
            }
        ) is listed
    assert {fact["label"]: fact["value"] for fact in kept["evidence"]} == {
        "Current value": "Ada Example",
        "Chosen by": "You",
        "Confirmed in Audiobookshelf": "No change was needed",
    }
    (recorded,) = (
        entry
        for entry in root.get_review_case_history(library.case_id(), 50)
        if entry.get("decision_id") == owner.decision_id
    )
    assert recorded["previous_value"] == recorded["selected_value"]

    # A replay records nothing more; a second accept of the same value is
    # refused while the first stands, and records nothing.
    assert json.loads(root.record_human_decision(owner))["decision_id"] == (
        owner.decision_id
    )
    again = _accept(library.case(), "authors", "owner-presses-it-again", 42)
    with pytest.raises(HumanDecisionError, match="owner_value_unchanged"):
        root.record_human_decision(again)
    assert root._connection.execute(
        "SELECT COUNT(*) FROM field_effect_no_changes WHERE origin='human'"
    ).fetchone() == (1,)
    assert root._connection.execute(
        "SELECT decision_id FROM human_decisions"
    ).fetchall() == [(owner.decision_id,)]

    if posture == "automatic":
        # Returning the field to Automatic leaves its written decision
        # settled: nothing waits and nothing is planned.
        assert root.clear_field_effect_manual_hold(
            library.case_id(), "authors", 43, owner.decision_id
        )
        library.now[0] = 44
        scheduler.schedule()
        assert automatic.decision_id not in library.pending()
        assert set(_planned(library)) == {automatic.decision_id}


def test_choosing_the_written_value_after_resume_holds_the_field_again(
    library: _Library, tmp_path: Path
) -> None:
    """Resume automatic handling releases the owner's no-change answer; the
    owner can then choose the same value again, which holds the field anew
    with nothing to write, and pin it."""
    root = library.root
    _, scheduler = _automatic_written(library, tmp_path)
    first = _accept(library.case(), "authors", "owner-presses-before-resume", 40)
    root.record_human_decision(first)
    assert root.clear_field_effect_manual_hold(
        library.case_id(), "authors", 41, first.decision_id
    )
    resumed = _read(library, scheduler)["fields"]["authors"]
    assert resumed["pin_control"] == {"pin": "change_waiting", "unpin": False}

    again = _accept(library.case(), "authors", "owner-presses-after-resume", 42)
    root.record_human_decision(again)
    _settled_without_a_write(library, scheduler, again)
    field = _read(library, scheduler)["fields"]["authors"]
    assert field["pin_control"] == {"pin": "available", "unpin": False}


def test_the_card_pin_on_automatics_written_value_settles(
    library: _Library, tmp_path: Path
) -> None:
    """After an entry of the previous names, the card pin on Automatic's
    written value sends accept then pin: both settle with nothing to write."""
    root = library.root
    _, scheduler = _automatic_written(library, tmp_path)
    case = library.case()
    proposal = next(
        value for value in case.proposals if value["destination"] == "authors"
    )
    entry = HumanDecision.create(
        case,
        proposal_id=str(proposal["proposal_id"]),
        target_fingerprint=case.target_fingerprint,
        destination="authors",
        actor="owner",
        outcome="owner_entered",
        selected_value={"kind": "list", "value": ["Current Writer"]},
        idempotency_key="owner-enters-the-previous-names",
        occurred_at=40,
        previous=None,
        written=(["Ada Example"],),
    )
    root.record_human_decision(entry)
    accept = _accept(library.case(), "authors", "card-pin-accepts-first", 41)
    root.record_human_decision(accept)
    pin = HumanDecision.create(
        library.case(),
        proposal_id=str(proposal["proposal_id"]),
        target_fingerprint=case.target_fingerprint,
        destination="authors",
        actor="owner",
        outcome="pin",
        selected_value=None,
        idempotency_key="card-pin-then-pins",
        occurred_at=42,
    )
    root.record_human_decision(pin)
    library.now[0] = 43
    scheduler.schedule()
    assert entry.decision_id not in library.pending()
    assert entry.decision_id not in _planned(library)
    assert accept.decision_id not in library.pending()
    assert accept.decision_id not in _planned(library)
    assert root.pending_field_effect_decision_counts() == ()
    (hold,) = root._connection.execute(
        "SELECT document FROM field_effect_manual_holds "
        "WHERE destination='authors' AND active=1"
    ).fetchone()
    held = json.loads(hold)
    assert held["decision_id"] == accept.decision_id
    assert held["pin"] is not None


def test_an_earlier_generation_write_once_settled_lets_the_owner_answer(
    tmp_path: Path,
) -> None:
    """A settled earlier-generation write no longer stands in the owner's way.

    Its report predates the write, so leaving Automatic reads the work again
    from the library (ADR-0034): the current case starts from the written
    authors, and the owner's answer on that field is recorded there.
    """
    with _prepared(tmp_path, automatic=True, library_model="publication") as library:
        root = library.root
        automatic = library.automatic()
        root.grant_field_effect_consents("primary", ("ebooks",), ("authors",), 12)
        scheduler, service, target = _applier(library, tmp_path)
        library.now[0] = 13
        scheduler.schedule()
        plan = _planned(library)[automatic.decision_id].plan
        target.seed(plan)
        assert service.apply(plan.plan_id).state == "verified"
        assert library.held is not None
        library.held.write(TargetDestination.AUTHORS, ("Ada Example",))

        library.leave_automatic()

        case = library.case()
        assert case.review_case_id != automatic.review_case_id
        authors = _read(library, scheduler)["fields"]["authors"]
        assert authors["owner_control"]["current"] == ["Ada Example"]
        proposal = next(
            value for value in case.proposals if value["destination"] == "authors"
        )
        owner = HumanDecision.create(
            case,
            proposal_id=str(proposal["proposal_id"]),
            target_fingerprint=case.target_fingerprint,
            destination="authors",
            actor="owner",
            outcome="pin",
            selected_value=None,
            idempotency_key="owner-after-writing",
            occurred_at=40,
        )
        root.record_human_decision(owner)
        assert root._connection.execute(
            "SELECT decision_id FROM human_decisions WHERE review_case_id=?",
            (case.review_case_id,),
        ).fetchall() == [(owner.decision_id,)]


def test_a_new_generation_write_waits_for_an_earlier_write_still_out(
    library: _Library, tmp_path: Path
) -> None:
    """A later generation never writes over an unreconciled request (#1573).

    One automatic decision per field used to make a second write for the
    field impossible; with one per generation, the newer write waits until the
    earlier one is settled.
    """
    root = library.root
    automatic = library.automatic()
    root.grant_field_effect_consents("primary", ("ebooks",), ("authors",), 12)
    scheduler, service, target = _applier(library, tmp_path)
    library.now[0] = 13
    scheduler.schedule()
    earlier = _planned(library)[automatic.decision_id].plan
    root.claim_field_effect(earlier.plan_id, "planned", "c" * 64, 14)
    root.transition_field_effect(earlier.plan_id, "claimed", "applying", 15)
    library.leave_automatic()
    root.activate_qualified_automatic(40)
    library.now[0] = 41
    library.drain()
    fresh = library.automatic(3)
    library.now[0] = 42
    scheduler.schedule()
    renewed = _planned(library)[fresh.decision_id].plan
    target.seed(renewed)
    before = target.read(renewed).value

    # While the earlier request is out, the new plan is not selected, Activity
    # counts nothing as ready (it names the earlier write still in progress),
    # and applying the new plan defers without consuming it.
    assert root.field_effect_deferred(renewed.plan_id)
    assert root.next_field_effect_plan_id(None) is None
    assert _waiting(scheduler) == (0, "authors")
    with pytest.raises(GuardedApplicationError, match="deferred"):
        service.apply(renewed.plan_id)
    assert root.get_field_effect(renewed.plan_id).state == "planned"
    assert target.read(renewed).value == before

    # Once the earlier request is settled, the new plan writes.
    root.transition_field_effect(earlier.plan_id, "applying", "stale", 43, "authority")
    assert not root.field_effect_deferred(renewed.plan_id)
    assert root.next_field_effect_plan_id(None) == (renewed.plan_id, "ebooks")
    assert service.apply(renewed.plan_id).state == "verified"


def _withdraw(library: _Library, key: str, at: int) -> HumanDecision:
    case = library.case()
    proposal = next(
        value for value in case.proposals if value["destination"] == "authors"
    )
    return HumanDecision.create(
        case,
        proposal_id=str(proposal["proposal_id"]),
        target_fingerprint=case.target_fingerprint,
        destination="authors",
        actor="owner",
        outcome="withdraw",
        selected_value=None,
        idempotency_key=key,
        occurred_at=at,
    )


def _confirm_automatic(library: _Library, at: int) -> None:
    library.root.activate_qualified_automatic(at)
    library.now[0] = at + 1
    library.drain()


@pytest.mark.parametrize(
    "answered", ["before_leaving", "in_preview", "before_leaving_then_confirmed"]
)
def test_an_answer_beside_an_earlier_generation_decision_can_be_withdrawn(
    library: _Library, answered: str
) -> None:
    """The page offers Withdraw where only an earlier generation's automatic
    decision is on the field, so the store accepts it: that decision holds
    nothing and no Resume shows to release it (ADR-0036, ADR-0085)."""
    root = library.root
    earlier = library.automatic()
    if answered != "in_preview":
        root.record_human_decision(
            _accept(library.case(), "authors", "answer-before-leaving", 11)
        )
        # Beside this generation's decision the refusal stands, and says so.
        assert _read_fields(library)["authors"]["owner_control"]["withdraw"] == (
            "automatic_hold"
        )
        with pytest.raises(HumanDecisionError) as refused:
            root.record_human_decision(_withdraw(library, "withdraw-in-automatic", 12))
        assert refused.value.code == "automatic_hold"
    library.leave_automatic()
    if answered == "in_preview":
        root.record_human_decision(
            _accept(library.case(), "authors", "answer-in-preview-mode", 31)
        )
    if answered.endswith("confirmed"):
        # Confirming decides the held field again under its hold, so the
        # field reads as before leaving: the hold, Resume, and the refusal
        # (ADR-0085, Amendment 4).
        _confirm_automatic(library, 40)
        assert library.decisions() == [("authors", 2), ("authors", 3)]
        field = _read_fields(library)["authors"]
        assert (field["manual_hold"], field["owner_control"]["withdraw"]) == (
            True,
            "automatic_hold",
        )
        with pytest.raises(HumanDecisionError) as refused:
            root.record_human_decision(_withdraw(library, "withdraw-confirmed", 41))
        assert refused.value.code == "automatic_hold"
        return
    assert library.decisions() == [("authors", 2)]
    field = _read_fields(library)["authors"]
    assert (field["manual_hold"], field["owner_control"]["withdraw"]) == (
        False,
        "available",
    )

    root.record_human_decision(_withdraw(library, "withdraw-earlier-generation", 50))

    # The earlier decision still writes nothing and nothing holds the field.
    assert earlier.decision_id not in library.pending()
    assert library.pending() == []
    assert root._connection.execute(
        "SELECT count(*) FROM field_effect_manual_holds WHERE active=1"
    ).fetchone() == (0,)
    field = _read_fields(library)["authors"]
    assert field["human_decision"] is None
    assert field["owner_control"]["withdraw"] is None
    assert field["needs_attention"] is False
    # Automatic stays off a field the owner has answered, a withdrawal
    # included, so the page doesn't say turning it on decides it again.
    assert field["earlier_automatic_value"] is None
    if not answered.endswith("confirmed"):
        _confirm_automatic(library, 60)
    assert library.decisions() == [("authors", 2)]
    assert library.pending() == []
    assert _read_fields(library)["authors"]["automatic_decision"] is None
