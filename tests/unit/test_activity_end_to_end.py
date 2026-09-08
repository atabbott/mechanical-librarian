"""One durable document, written and read back the way the product does it.

Every other Activity proof in this suite stops at a seam. The persistence tests
assert the record the query projects; the composer tests build that record in
Python and assert the words; the component tests are given the words as props.
Each passes while the join between them is wrong, and a broken lookup renders
as ``Recorded`` -- which reads as a reasonable non-change row rather than as a
fault. This module closes that gap: the documents are
written through the real guarded application, read back through the real
activity query, and joined to their real report by the real application layer,
and what is asserted is the sentence an owner would read (#636).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import cast

import pytest

from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.application.field_effects import (
    AuthoritySnapshot,
    GuardedFieldApplication,
)
from mechanical_librarian.application.review_web import InspectCurrentReviewCase
from mechanical_librarian.domain.automatic_decision import (
    F19A_QUALIFICATION_MANIFEST_SHA256,
    AutomaticDecision,
)
from mechanical_librarian.domain.cover_automatic_decision import CoverAutomaticDecision
from mechanical_librarian.domain.field_effect import (
    EffectAuthority,
    EffectConsent,
    FieldEffectOperation,
    FieldEffectPlan,
)
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    TargetDestination,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.shadow_outcome import ShadowOutcomeSet
from tests.support.automatic_decisions import _automatic_decisions
from tests.support.automatic_no_change import _automatic_no_change_inputs
from tests.support.proposal_evidence import (
    _claim,
    _fields,
    _local_description,
    _provider,
    _target_work_candidate,
    _target_work_report,
)
from tests.support.proposal_evidence import _report as _proposal_report
from tests.unit.test_field_effects import (  # noqa: F401
    CAPABILITY,
    DEPENDENCY,
    _authorize,
    _EffectEnvironment,
    _plan,
    effects,
    envelope,
    metadata_report,
)


def _ledger(
    environment: _EffectEnvironment, *, search: str | None = None
) -> list[dict[str, object]]:
    """Read the ledger exactly as the web transport reads it.

    The collaborators here are the ones ``bootstrap.composition`` supplies for
    the activity-only application, so the projection under assertion is the one
    the running product serves rather than a convenient stand-in.
    """
    service = ContinuousGuardedApplication(
        environment.repository,
        {},
        {},
        PersistedFieldEffectAuthority(environment.repository),
        (("primary", "books"),),
        lambda: 2_000,
        lambda *_: None,
        activity_only=True,
    )
    events = service.activity(search=search)["events"]
    assert isinstance(events, list)
    return events


def _facts(event: dict[str, object]) -> dict[str, str]:
    evidence = event["evidence"]
    assert isinstance(evidence, list)
    return {str(item["label"]): str(item["value"]) for item in evidence}


def _automatic_no_change_decisions(
    environment: _EffectEnvironment, *, qualified: bool
) -> tuple[AutomaticDecision, ...]:
    report, category, option, destination = _automatic_no_change_inputs("exact_title")
    decisions = _automatic_decisions(
        environment, report, category, option, qualified=qualified
    )
    assert not decisions or any(
        value.destination == destination and value.outcome == "keep_current"
        for value in decisions
    )
    return decisions


def _automatic_candidate_decision(
    environment: _EffectEnvironment,
    *,
    authority: str = "hardcover",
) -> AutomaticDecision:
    report = _target_work_report(
        (
            (
                "catalogue",
                authority,
                (_target_work_candidate("catalogue", "edition-1", "work-1"),),
                True,
            ),
        )
    )
    decisions = _automatic_decisions(
        environment,
        report,
        "description_fill",
        "safe_plain_text",
        qualified=True,
    )
    return next(
        value
        for value in decisions
        if value.destination == "description" and value.outcome == "candidate"
    )


def _qualified_no_change_decision(environment: _EffectEnvironment) -> AutomaticDecision:
    decisions = _automatic_no_change_decisions(environment, qualified=True)
    return next(value for value in decisions if value.destination == "title")


@pytest.mark.parametrize(
    ("authority", "label"),
    (("hardcover", "Hardcover"), ("audnexus", "Audnexus")),
)
def test_automatic_description_read_model_names_only_its_bound_source(
    effects: _EffectEnvironment,  # noqa: F811
    authority: str,
    label: str,
) -> None:
    decision = _automatic_candidate_decision(effects, authority=authority)

    detail = InspectCurrentReviewCase(effects.repository).run(decision.review_case_id)
    description = next(
        field for field in detail["fields"] if field["destination"] == "description"
    )

    assert description["automatic_selection_sources"] == [label]


def test_equivalent_local_description_keeps_exact_automatic_authority_after_reopen(
    effects: _EffectEnvironment,  # noqa: F811
) -> None:
    candidate = _target_work_candidate("catalogue", "edition-1", "work-1")
    local = _local_description("A QUALIFIED DESCRIPTION.")
    report = _target_work_report(
        (("catalogue", "hardcover", (candidate,), True),),
        additional_local_claims=(local,),
    )
    proposal = evaluate_report(report)
    result = next(
        value
        for value in proposal.results
        if value.destination is TargetDestination.DESCRIPTION
    )
    alternative = result.alternatives[0]
    assert alternative.value.value == "A qualified description."

    decisions = _automatic_decisions(
        effects,
        report,
        "description_fill",
        "safe_plain_text",
        qualified=True,
    )
    decision = next(
        value
        for value in decisions
        if value.destination == "description" and value.outcome == "candidate"
    )
    assert decision.candidate_id == alternative.alternative_id

    effects.repository.close()
    effects.repository = open_repository(
        effects.database_path, create=False, cross_thread=True
    )
    ShadowOutcomeSet.from_json(
        effects.repository.persist_shadow_outcome(decision.review_case_id, 4)
    )
    persisted = tuple(
        AutomaticDecision.from_json(value)
        for value in effects.repository.get_review_case_decisions(
            decision.review_case_id
        )
        if AutomaticDecision.from_json(value).destination == "description"
    )
    assert persisted == (decision,)

    authority = PersistedFieldEffectAuthority(effects.repository).resolve(decision)
    assert authority.decision_value == {
        "kind": "text",
        "value": "A qualified description.",
    }
    detail = InspectCurrentReviewCase(effects.repository).run(decision.review_case_id)
    description = next(
        field for field in detail["fields"] if field["destination"] == "description"
    )
    assert description["automatic_decision"] == {
        "candidate_id": alternative.alternative_id,
        "decision_id": decision.decision_id,
        "destination": "description",
        "occurred_at": 4,
        "outcome": "candidate",
        "proposal_id": decision.proposal_id,
        "review_case_id": decision.review_case_id,
        "selected_value": {"kind": "text", "value": "A qualified description."},
        "target_fingerprint": decision.target_fingerprint,
    }
    assert description["automatic_selection_sources"] == ["Hardcover"]
    assert effects.repository._connection.execute(
        "SELECT count(*) FROM field_effects"
    ).fetchone() == (0,)
    activity = next(
        event for event in _ledger(effects) if event["event_id"] == decision.decision_id
    )
    assert _facts(activity)["New value"] == "A qualified description."


def test_a_qualified_automatic_decision_reaches_activity_without_raw_inserts(
    effects: _EffectEnvironment,  # noqa: F811
) -> None:
    decision = _qualified_no_change_decision(effects)

    # Replaying the exact successor's shadow outcome cannot manufacture a
    # second automatic fact, and an automatic no-change is not owner work.
    ShadowOutcomeSet.from_json(
        effects.repository.persist_shadow_outcome(decision.review_case_id, 4)
    )
    assert effects.repository.count_current_attention_fields(None) == 0
    assert [
        AutomaticDecision.from_json(value)
        for value in effects.repository.get_review_case_decisions(
            decision.review_case_id
        )
        if AutomaticDecision.from_json(value).destination == decision.destination
    ] == [decision]

    automatic = next(
        event for event in _ledger(effects) if event["event_id"] == decision.decision_id
    )
    assert automatic["category"] == "automatic"
    assert automatic["state"] == "recorded"
    assert automatic["change"] == "Chose a title value automatically"
    assert _facts(automatic)["New value"] == "Secret History"


def test_missing_authority_degrades_one_supported_automatic_decision(
    effects: _EffectEnvironment,  # noqa: F811
) -> None:
    decision = _qualified_no_change_decision(effects)
    effects.repository._connection.execute(
        "DROP TRIGGER shadow_decision_outcomes_no_delete"
    )
    effects.repository._connection.execute(
        "DELETE FROM shadow_outcome_field_facts WHERE review_case_id=?",
        (decision.review_case_id,),
    )
    effects.repository._connection.execute(
        "DELETE FROM shadow_decision_outcomes WHERE review_case_id=?",
        (decision.review_case_id,),
    )
    effects.repository._connection.commit()

    retained = effects.repository.field_effect_activity_page(
        search=None,
        event_type="metadata",
        occurred_after=0,
        page=1,
        page_size=25,
    )
    assert retained["total"] == 1
    assert retained["events"][0]["record"] == {
        "source_kind": "degraded",
        "degraded_reason": "automatic_authority_unavailable",
    }


def test_an_unqualified_policy_records_no_automatic_decision(
    effects: _EffectEnvironment,  # noqa: F811
) -> None:
    assert _automatic_no_change_decisions(effects, qualified=False) == ()
    assert not [event for event in _ledger(effects) if event["category"] == "automatic"]


def test_a_qualified_automatic_no_change_is_recorded_by_normal_scheduling(
    effects: _EffectEnvironment,  # noqa: F811
) -> None:
    report = metadata_report("Paper Moons: Larkspur", "a", "b", item_id="book-1")
    effects.repository.publish(report, lambda: True)
    retry = _plan(
        effects,
        destination="subtitle",
        pre="Original subtitle",
        desired="Replacement subtitle",
        suffix="e",
    )
    effects.target.fail_next = True
    assert effects.service.apply(retry.plan.plan_id).state == "retryable"

    decision = _qualified_no_change_decision(effects)

    class UnexpectedAuthority:
        def resolve(
            self,
            _decision: HumanDecision | AutomaticDecision | CoverAutomaticDecision,
        ) -> AuthoritySnapshot:
            raise AssertionError("keep-current decision reached authority")

        def current(self, _authority: EffectAuthority) -> bool:
            raise AssertionError("keep-current decision reached authority")

    scheduler = ContinuousGuardedApplication(
        effects.repository,
        {("primary", "ebooks"): cast(GuardedFieldApplication, SimpleNamespace())},
        {("primary", "ebooks"): SimpleNamespace()},
        UnexpectedAuthority(),
        (("primary", "ebooks"),),
        lambda: 10,
        lambda *_: None,
    )
    assert scheduler.schedule() == 0
    no_change = next(
        event for event in _ledger(effects) if event["state"] == "no_change_required"
    )
    assert decision.to_json() in effects.repository.get_review_case_decisions(
        decision.review_case_id
    )
    assert no_change["change"] == "Kept Secret History"

    waiting = _ledger(effects, search="Waiting safely")
    assert [(event["state"], event["destination"]) for event in waiting] == [
        ("retryable", "subtitle")
    ]
    no_change_rows = _ledger(effects, search="No change required")
    assert [event["state"] for event in no_change_rows] == ["no_change_required"]
    assert _ledger(effects, search="retryable") == []
    assert _ledger(effects, search="no_change_required") == []


def test_a_real_write_reaches_the_owner_as_one_exact_sentence(
    effects: _EffectEnvironment,  # noqa: F811
) -> None:
    """A planned write, projected from its own durable event and report."""
    report = metadata_report("Paper Moons: Larkspur", "a", "b", item_id="book-1")
    effects.repository.publish(report, lambda: True)
    _plan(
        effects,
        destination="title",
        pre="Paper Moons: Larkspur",
        desired="Paper Moons over Larkspur Station",
        suffix="a",
    )

    written = next(event for event in _ledger(effects) if event["state"] == "planned")
    assert written["category"] == "owner"
    assert written["field_label"] == "Title"
    assert written["change"] == (
        "Paper Moons: Larkspur → Paper Moons over Larkspur Station"
    )
    assert written["change_detail"] == (
        "Set Title from Paper Moons: Larkspur to Paper Moons over Larkspur Station"
    )
    assert _facts(written) == {
        "Previous value": "Paper Moons: Larkspur",
        "New value": "Paper Moons over Larkspur Station",
        "Chosen by": "Library owner",
        "Confirmed in Audiobookshelf": "Not confirmed yet",
    }


def test_only_a_verified_write_claims_audiobookshelf_confirmed_it(
    effects: _EffectEnvironment,  # noqa: F811
) -> None:
    """The lifecycle is distinguished while the change identity does not move.

    Applying the plan leaves several real events behind it, one per state the
    write passed through. Every one of them carries the same change, because
    the change is a property of the plan; only the verified one says the
    library was read back and agreed.
    """
    report = metadata_report("Paper Moons: Larkspur", "a", "b", item_id="book-1")
    effects.repository.publish(report, lambda: True)
    planned = _plan(
        effects,
        destination="title",
        pre="Paper Moons: Larkspur",
        desired="Paper Moons over Larkspur Station",
        suffix="a",
    )
    effects.service.apply(planned.plan.plan_id)

    writes = [event for event in _ledger(effects) if event["event_type"] != "policy"]
    assert [str(event["state"]) for event in writes] == ["verified"]
    assert [event["change_id"] for event in writes] == [planned.plan.plan_id]
    transitions = effects.repository.field_effect_activity_transitions(
        str(writes[0]["event_id"])
    )
    states = {str(event["state"]) for event in transitions}
    assert "verified" in states and len(states) > 1
    assert [event["sequence"] for event in transitions] == list(
        range(1, len(transitions) + 1)
    )
    continued = effects.repository.field_effect_activity_transitions(
        str(writes[0]["event_id"]), after_sequence=2
    )
    assert [event["sequence"] for event in continued] == [3, 4]
    assert transitions == effects.repository.field_effect_activity_transitions(
        planned.plan.plan_id
    )
    assert {str(event["change"]) for event in writes} == {
        "Paper Moons: Larkspur → Paper Moons over Larkspur Station"
    }
    for event in writes:
        confirmation = _facts(event)["Confirmed in Audiobookshelf"]
        assert confirmation == (
            "Confirmed as Paper Moons over Larkspur Station"
            if event["state"] == "verified"
            else "Not confirmed yet"
        )


def test_a_list_value_reaches_the_owner_joined_rather_than_bracketed(
    effects: _EffectEnvironment,  # noqa: F811
) -> None:
    """A real contributor list, through the real path, reads as prose."""
    report = metadata_report("Paper Moons: Larkspur", "a", "b", item_id="book-1")
    effects.repository.publish(report, lambda: True)
    _plan(
        effects,
        destination="authors",
        pre=["Ursula K. Le Guin"],
        desired=["Ursula K. Le Guin", "雨の地図"],
        suffix="a",
    )

    listed = next(
        event for event in _ledger(effects) if event["destination"] == "authors"
    )
    assert listed["field_label"] == "Authors"
    assert listed["change"] == ("Ursula K. Le Guin → Ursula K. Le Guin, 雨の地図")
    assert _facts(listed)["New value"] == "Ursula K. Le Guin, 雨の地図"


def test_a_series_membership_keeps_its_index_through_the_real_path(
    effects: _EffectEnvironment,  # noqa: F811
) -> None:
    report = metadata_report("Paper Moons: Larkspur", "a", "b", item_id="book-1")
    effects.repository.publish(report, lambda: True)
    _plan(
        effects,
        destination="series",
        pre=None,
        desired=[{"name": "Earthsea Cycle", "sequence": "1"}],
        suffix="a",
    )

    series = next(
        event for event in _ledger(effects) if event["destination"] == "series"
    )
    assert series["change"] == "Not set → Earthsea Cycle (1)"
    assert series["change_detail"] == "Set Series from Not set to Earthsea Cycle (1)"


def _admitted_case(environment: _EffectEnvironment) -> tuple[ReviewCase, dict]:
    """Admit one real review case and return it with an undecided proposal."""
    report = metadata_report("Old title", "1", "2", item_id="book-1")
    environment.repository.publish(report, lambda: True)
    proposal_set = evaluate_report(report)
    environment.repository.publish_proposal(proposal_set)
    case_id = json.loads(environment.repository.admit_review_case(proposal_set))[
        "review_case_id"
    ]
    case = ReviewCase.create_from_data(
        json.loads(environment.repository.get_review_case(case_id))["case"]
    )
    proposal = next(
        value
        for value in case.proposals
        if value["result"]["outcome"] == "unresolved_choice"
    )
    return case, proposal


def test_each_owner_outcome_reaches_the_ledger_in_its_own_words(
    effects: _EffectEnvironment,  # noqa: F811
) -> None:
    """Accept, keep and reject are three different sentences, not one.

    The vocabulary asserted here is the code's (``keep_current``), not the
    ticket's paraphrase.
    """
    case, proposal = _admitted_case(effects)
    selected = proposal["result"]["alternatives"][0]["value"]
    for index, outcome in enumerate(("accept", "keep_current", "reject")):
        effects.repository.record_human_decision(
            HumanDecision.create(
                case,
                proposal_id=proposal["proposal_id"],
                target_fingerprint=case.target_fingerprint,
                destination=proposal["destination"],
                actor="owner",
                outcome=outcome,
                selected_value=selected if outcome == "accept" else None,
                idempotency_key=f"activity-owner-{index:04d}",
                occurred_at=1_000 + index,
            )
        )

    rows = {
        str(event["state"]): event
        for event in _ledger(effects)
        if event["category"] == "owner"
    }
    assert rows.keys() == {"accept", "keep_current", "reject"}
    assert rows["accept"]["change"] == "Old title → Exact evaluator candidate"
    assert rows["accept"]["change_detail"] == (
        "Set Title from Old title to Exact evaluator candidate"
    )
    assert rows["keep_current"]["change"] == "Kept Old title"
    assert rows["keep_current"]["change_detail"] == "Kept Title as Old title"
    assert rows["reject"]["change"] == "Left unchanged"
    assert rows["reject"]["change_detail"] == "Left Title unchanged"


def test_verified_owner_replacement_retains_history_and_activity_predecessor(
    effects: _EffectEnvironment,  # noqa: F811
) -> None:
    """The second verified plan reads its predecessor, not the original target."""
    providers = (
        _provider(
            "alpha",
            "alpha-edition",
            (
                _claim(
                    "alpha",
                    "alpha-edition",
                    ClaimKind.LANGUAGE,
                    "en",
                    scope=ClaimScope.EDITION,
                ),
            ),
        ),
        _provider(
            "beta",
            "beta-edition",
            (
                _claim(
                    "beta",
                    "beta-edition",
                    ClaimKind.LANGUAGE,
                    "fr",
                    scope=ClaimScope.EDITION,
                ),
            ),
        ),
    )
    report = _proposal_report(
        fields=_fields(
            language=TargetField(TargetDestination.LANGUAGE, TargetFieldState.MISSING)
        ),
        providers=providers,
    )
    effects.repository.publish(report, lambda: True)
    proposal_set = evaluate_report(report)
    effects.repository.publish_proposal(proposal_set)
    case_id = json.loads(effects.repository.admit_review_case(proposal_set))[
        "review_case_id"
    ]
    case = ReviewCase.create_from_data(
        json.loads(effects.repository.get_review_case(case_id))["case"]
    )
    proposal = next(
        value for value in case.proposals if value["destination"] == "language"
    )
    choices = {
        value["value"]["value"]: value["value"]
        for value in proposal["result"]["alternatives"]
        if value["assessment"] != "preserves_target"
    }
    first_value, second_value = choices["en"], choices["fr"]
    proposal_id = proposal["proposal_id"]
    assert type(proposal_id) is str

    def decision(
        selected_value: dict[str, object], key: str, occurred_at: int
    ) -> HumanDecision:
        return HumanDecision.create(
            case,
            proposal_id=proposal_id,
            target_fingerprint=case.target_fingerprint,
            destination="language",
            actor="owner",
            outcome="accept",
            selected_value=selected_value,
            idempotency_key=key,
            occurred_at=occurred_at,
        )

    def verify(
        source: HumanDecision,
        pre_value: object,
        desired_value: object,
        occurred_at: int,
    ) -> None:
        plan = FieldEffectPlan.create(
            connection=report.target.connection,
            library=report.target.library,
            work_id=report.target.item,
            destination=source.destination,
            pre_value=pre_value,
            desired_value=desired_value,
            target_fingerprint=source.target_fingerprint,
            target_version="language-version-" + str(occurred_at),
            dependency_digests=(source.target_fingerprint,),
            authority=EffectAuthority(
                source.decision_id,
                source.review_case_id,
                source.target_fingerprint,
                "persisted_review_case",
                "human",
                "candidate",
                None,
                None,
            ),
            consent=EffectConsent.create(
                connection=report.target.connection,
                library=report.target.library,
                destination=source.destination,
                envelope_identity=effects.repository.field_effect_consent_envelope_identity(
                    report.target.library
                ),
                granted_at=occurred_at,
            ),
            capability_digest="d" * 64,
            created_at=occurred_at,
        )
        effects.repository.record_field_effect(
            FieldEffectOperation(plan, updated_at=occurred_at)
        )
        effects.repository.claim_field_effect(
            plan.plan_id, "planned", "a" * 64, occurred_at + 1
        )
        effects.repository.transition_field_effect(
            plan.plan_id, "claimed", "applying", occurred_at + 2
        )
        effects.repository.transition_field_effect(
            plan.plan_id, "applying", "verified", occurred_at + 3
        )

    first = decision(first_value, "activity-replacement-first-0001", 1_000)
    effects.repository.record_human_decision(first)
    verify(first, None, "en", 1_010)
    second = decision(second_value, "activity-replacement-second-0001", 1_020)
    effects.repository.record_human_decision(second)
    verify(second, "en", "fr", 1_030)

    decisions = [
        HumanDecision.from_json(document)
        for document in effects.repository.get_review_case_decisions(
            case.review_case_id
        )
        if HumanDecision.from_json(document).destination == "language"
    ]
    assert [value.decision_id for value in decisions] == [
        first.decision_id,
        second.decision_id,
    ]
    history = [
        event
        for event in effects.repository.get_review_case_history(case.review_case_id, 10)
        if event.get("kind") == "human_decision_recorded"
    ]
    assert [event["decision_id"] for event in history] == [
        second.decision_id,
        first.decision_id,
    ]
    replacement = next(
        event
        for event in _ledger(effects)
        if event["change_id"] != first.decision_id
        and event["destination"] == "language"
        and event["state"] == "verified"
        and _facts(event).get("New value") == "fr"
    )
    assert replacement["change"] == "en → fr"
    assert _facts(replacement)["Previous value"] == "en"
    assert _facts(replacement)["New value"] == "fr"


def _automatic_write(
    environment: _EffectEnvironment, case: ReviewCase, proposal: dict
) -> object:
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
    environment.authority.automatic_values[automatic.decision_id] = envelope(
        "title", "Automatic pick"
    )
    _authorize(environment, automatic, work_id="book-1")
    return environment.service.plan(
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


def test_a_policy_chosen_write_names_the_policy_rather_than_the_owner(
    effects: _EffectEnvironment,  # noqa: F811
) -> None:
    case, proposal = _admitted_case(effects)
    assert _automatic_write(effects, case, proposal) is not None

    written = next(
        event for event in _ledger(effects) if event["category"] == "automatic"
    )
    assert written["change"] == "Old title → Automatic pick"
    assert _facts(written)["Chosen by"] == "Automatic policy"


def test_a_write_the_library_refused_never_claims_a_before_and_after(
    effects: _EffectEnvironment,  # noqa: F811
) -> None:
    """A stopped write states what it had prepared and why it stopped."""
    report = metadata_report("Paper Moons: Larkspur", "a", "b", item_id="book-1")
    effects.repository.publish(report, lambda: True)
    planned = _plan(
        effects,
        destination="title",
        pre="Paper Moons: Larkspur",
        desired="Paper Moons over Larkspur Station",
        suffix="a",
    )
    effects.target.unavailable_scope_next = "connection"
    assert effects.service.apply(planned.plan.plan_id).state == "stale"

    stopped = next(
        event for event in _ledger(effects) if event["category"] == "recovery"
    )
    assert stopped["change"] == "Prepared change closed"
    assert _facts(stopped) == {
        "Prepared value": "Paper Moons over Larkspur Station",
        "Why it stopped": "Audiobookshelf did not offer this field",
        "Chosen by": "Library owner",
        "Confirmed in Audiobookshelf": "No write was applied",
    }
