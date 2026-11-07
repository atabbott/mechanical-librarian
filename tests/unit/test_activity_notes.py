"""Activity notes: what current state adds to a row without changing it (#1711).

A row's result is history: "Accepted" stays true after the work returns to
earlier content, and "Planned" stays true after the owner leaves Automatic.
What the owner needs to know now is a note beside that result: the field is
asking again, or the write is held. Notes are read per returned page from
current state, so the cached page and the search text never hold them.
"""

from __future__ import annotations

import json
import sqlite3

from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.domain.automatic_decision import (
    F19A_QUALIFICATION_MANIFEST_SHA256,
    AutomaticDecision,
)
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.interfaces.web.app import _ActivityResponse
from tests.support.field_effects import (  # noqa: F401
    CAPABILITY,
    DEPENDENCY,
    _authorize,
    _EffectEnvironment,
    effects,
    envelope,
    metadata_report,
)


def _application(repository: object) -> ContinuousGuardedApplication:
    return ContinuousGuardedApplication(
        repository,
        {},
        {},
        PersistedFieldEffectAuthority(repository),
        (("primary", "books"),),
        lambda: 2_000,
        lambda *_: None,
        activity_only=True,
    )


def _activity(
    environment: _EffectEnvironment, *, search: str | None = None
) -> dict[str, object]:
    """Read Activity as the web transport does, from a fresh page reader too."""
    reader = open_repository(
        environment.database_path,
        create=False,
        authority=environment.repository.authority,
    )
    try:
        reader.configure_activity_page_reader()
        actual = _application(reader).activity(search=search)
        assert actual == _application(environment.repository).activity(search=search)
        # The response model admits exactly the notes the read produces.
        _ActivityResponse.model_validate(actual)
        return actual
    finally:
        reader.close()


def _events(
    environment: _EffectEnvironment, *, search: str | None = None
) -> list[dict[str, object]]:
    events = _activity(environment, search=search)["events"]
    assert isinstance(events, list)
    return events


def _admit(environment: _EffectEnvironment, title: str, content: str) -> ReviewCase:
    report = metadata_report(title, content, content, item_id="book-1")
    environment.repository.publish(report, lambda: True)
    proposal_set = evaluate_report(report)
    environment.repository.publish_proposal(proposal_set)
    case_id = json.loads(environment.repository.admit_review_case(proposal_set))[
        "review_case_id"
    ]
    return ReviewCase.create_from_data(
        json.loads(environment.repository.get_review_case(case_id))["case"]
    )


def _title_proposal(case: ReviewCase) -> dict:
    return next(
        value
        for value in case.proposals
        if value["result"]["outcome"] == "unresolved_choice"
    )


def _decide(environment: _EffectEnvironment, case: ReviewCase, key: str) -> str:
    proposal = _title_proposal(case)
    decision = HumanDecision.create(
        case,
        proposal_id=proposal["proposal_id"],
        target_fingerprint=case.target_fingerprint,
        destination=proposal["destination"],
        actor="owner",
        outcome="accept",
        selected_value=proposal["result"]["alternatives"][0]["value"],
        idempotency_key=key,
        occurred_at=1_000,
    )
    environment.repository.record_human_decision(decision)
    return decision.decision_id


def _owner_notes(environment: _EffectEnvironment) -> dict[str, object]:
    return {
        str(event["event_id"]): event["note"]
        for event in _events(environment)
        if str(event["event_id"]).startswith("human_decision_event_")
    }


def _set_posture(environment: _EffectEnvironment, posture: str) -> None:
    connection = sqlite3.connect(environment.database_path)
    try:
        with connection:
            connection.execute(
                "UPDATE metadata_policy_state SET posture=? WHERE singleton=1",
                (posture,),
            )
    finally:
        connection.close()


def test_a_choice_under_a_new_episode_reads_as_asked_again(
    effects: _EffectEnvironment,  # noqa: F811
) -> None:
    first = _admit(effects, "Old title", "1")
    _decide(effects, first, "notes-owner-0001")
    (current,) = _owner_notes(effects).items()
    # A choice on the current case still counts.
    assert current[1] is None

    second = _admit(effects, "Other title", "2")
    assert second.review_case_id != first.review_case_id
    # On a superseded case the choice is history, not a field asking again.
    assert set(_owner_notes(effects).values()) == {None}

    # The work returns to the first content: the same case, a new episode.
    returned = _admit(effects, "Old title", "1")
    assert returned.review_case_id == first.review_case_id
    assert _owner_notes(effects) == {current[0]: "asked_again"}

    # A choice made in the new episode counts again, and has no note.
    _decide(effects, returned, "notes-owner-0002")
    notes = _owner_notes(effects)
    assert notes[current[0]] == "asked_again"
    assert list(notes.values()).count(None) == 1
    assert len(notes) == 2


def _automatic_plan(effects: _EffectEnvironment) -> str:  # noqa: F811
    case = _admit(effects, "Old title", "1")
    proposal = _title_proposal(case)
    automatic = AutomaticDecision.create(
        review_case_id=case.review_case_id,
        report_id=case.report_id,
        proposal_set_id=case.proposal_set_id,
        proposal_id=proposal["proposal_id"],
        target_fingerprint=case.target_fingerprint,
        destination=proposal["destination"],
        outcome="candidate",
        candidate_id="alternative_" + "f" * 64,
        resolved_policy_id="resolved_policy_" + "0" * 64,
        policy_generation=1,
        rule_id="shadow_rule_" + "1" * 64,
        policy_category="title_layout",
        policy_option="preserve",
        qualification_manifest_sha256=F19A_QUALIFICATION_MANIFEST_SHA256,
        source_outcome_id="shadow_outcome_" + "3" * 64,
        bundle_id="automatic_bundle_" + "4" * 64,
        occurred_at=1,
    )
    effects.authority.automatic_values[automatic.decision_id] = envelope(
        "title", "Automatic pick"
    )
    _authorize(effects, automatic, work_id="book-1")
    planned = effects.service.plan(
        automatic,
        connection="primary",
        library="books",
        work_id="book-1",
        destination=proposal["destination"],
        pre_value="Old title",
        desired_value=envelope("title", "Automatic pick"),
        target_fingerprint=case.target_fingerprint,
        target_version="version-1",
        dependency_digests=(DEPENDENCY,),
        capability_digest=CAPABILITY,
    )
    assert planned is not None
    effects.target.seed(planned.plan)
    return planned.plan.plan_id


def _automatic_rows(effects: _EffectEnvironment) -> list[dict[str, object]]:  # noqa: F811
    return [event for event in _events(effects) if event["category"] == "automatic"]


def test_a_planned_automatic_write_reads_as_held_while_automatic_is_off(
    effects: _EffectEnvironment,  # noqa: F811
) -> None:
    _automatic_plan(effects)
    (planned,) = _automatic_rows(effects)
    assert planned["state"] == "planned"
    assert planned["note"] is None

    before = _activity(effects)
    for posture in ("preview", "review"):
        _set_posture(effects, posture)
        after = _activity(effects)
        (held,) = _automatic_rows(effects)
        assert held["note"] == "held_back"
        # The note is the only thing a posture switch changes on the page.
        assert [
            {key: value for key, value in event.items() if key != "note"}
            for event in after["events"]
        ] == [
            {key: value for key, value in event.items() if key != "note"}
            for event in before["events"]
        ]
        # The counts already left the held write out of what is waiting; the
        # row now says why instead of reading "Planned" alone.
        assert after["counts"] == {}

    _set_posture(effects, "automatic")
    (planned,) = _automatic_rows(effects)
    assert planned["note"] is None


def test_a_verified_automatic_write_is_not_held(
    effects: _EffectEnvironment,  # noqa: F811
) -> None:
    plan_id = _automatic_plan(effects)
    effects.service.apply(plan_id)
    _set_posture(effects, "preview")
    rows = _automatic_rows(effects)
    assert [row["state"] for row in rows] == ["verified"]
    assert {row["note"] for row in rows} == {None}


def test_search_never_reads_a_note(
    effects: _EffectEnvironment,  # noqa: F811
) -> None:
    _automatic_plan(effects)
    _set_posture(effects, "preview")
    assert _automatic_rows(effects)[0]["note"] == "held_back"
    for words in (
        "held",
        "held_back",
        "asked again",
        "asked_again",
        "Automatic is off",
    ):
        assert _events(effects, search=words) == []
