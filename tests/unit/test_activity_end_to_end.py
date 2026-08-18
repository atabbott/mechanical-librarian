"""One durable document, written and read back the way the product does it.

Every other Activity proof in this suite stops at a seam. The persistence tests
assert the record the query projects; the composer tests build that record in
Python and assert the words; the component tests are given the words as props.
Each passes while the join between them is wrong, and a broken lookup renders
as ``Recorded`` -- which reads as a reasonable non-change row rather than as a
fault. This module closes that gap for the kinds it covers: the documents are
written through the real guarded application, read back through the real
activity query, and joined to their real report by the real application layer,
and what is asserted is the sentence an owner would read (#636).

Two of the ledger's source kinds are deliberately absent here, and the reason
is worth more than the coverage would have been.

``automatic_decision`` and ``no_change`` both require a durable automatic
decision document. Reaching one through supported seams gets a long way and
then stops: activating a policy that qualifies a destination works (the
built-in profile already sends ``title_layout`` to ``automatic`` under the
qualified ``preserve`` option), proposals evaluated under that resolved policy
are admitted, and ``persist_shadow_outcome`` runs. But every field of the
resulting outcome carries ``abstention``, and ``_record_automatic_outcomes``
only admits ``candidate``/``keep_current``/``ignore``. Turning an abstention
into a decision is a property of the *evidence in the report*, not of the
policy -- it needs provider claims the shadow evaluator will actually decide
on, which is evaluator work rather than test setup.

``record_current_field_effect_no_change`` is a real public seam that the
application itself calls, but it resolves authority from a persisted decision
and so raises ``authority_binding`` without one; it inherits the same block.

The suite's existing automatic-decision fixtures reach behind the product with
a raw ``INSERT INTO automatic_decisions``. Rows built that way would prove the
insert, not the join these tests exist to check, so they are not used here.
"""

from __future__ import annotations

import json

from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
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


def _ledger(environment: _EffectEnvironment) -> list[dict[str, object]]:
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
    events = service.activity()["events"]
    assert isinstance(events, list)
    return events


def _facts(event: dict[str, object]) -> dict[str, str]:
    evidence = event["evidence"]
    assert isinstance(evidence, list)
    return {str(item["label"]): str(item["value"]) for item in evidence}


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
    states = {str(event["state"]) for event in writes}
    assert "verified" in states and len(states) > 1
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
