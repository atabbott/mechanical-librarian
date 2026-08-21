"""Canonical equality shared by proposal evaluation and no-change terminalization."""

import json
import tempfile
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.domain.automatic_decision import (
    F19A_QUALIFICATION_MANIFEST_SHA256,
    AutomaticDecision,
)
from mechanical_librarian.domain.field_value_comparison import values_match
from mechanical_librarian.domain.metadata_report import TargetDestination
from mechanical_librarian.domain.proposal_set import evaluate_report
from tests.support.review_cases import _report


@pytest.mark.parametrize(
    ("destination", "current", "candidate"),
    (
        (TargetDestination.TITLE, "A  Title", "a title"),
        (TargetDestination.ISBN, "9780306406157", "9780306406157"),
        (TargetDestination.AUTHORS, ("Mick Herron",), ("mick herron",)),
        (TargetDestination.SUBTITLE, None, None),
    ),
    ids=("scalar", "identifier", "creator", "nullable"),
)
def test_values_match_uses_the_field_canonical_comparison(
    destination: TargetDestination,
    current: object,
    candidate: object,
) -> None:
    assert values_match(destination, current, candidate)


def test_values_match_keeps_a_near_match_actionable() -> None:
    assert not values_match(
        TargetDestination.TITLE,
        "A title",
        "A different title",
    )


def test_canonically_equal_automatic_title_is_durable_no_change_without_guards() -> None:
    """A policy-equal title cannot advance to a target mutation.

    The source values have passed their ordinary persisted-decision boundary;
    this isolates the terminal comparison which must keep the candidate out of
    the write, consent, and authority tables.
    """
    decision = AutomaticDecision.create(
        review_case_id="review_case_" + "a" * 64,
        report_id="report_" + "b" * 64,
        proposal_set_id="proposal_set_" + "c" * 64,
        proposal_id="review_proposal_" + "d" * 64,
        target_fingerprint="e" * 64,
        destination="title",
        outcome="candidate",
        candidate_id="alternative_" + "f" * 64,
        resolved_policy_id="resolved_policy_" + "1" * 64,
        policy_generation=1,
        rule_id="shadow_rule_" + "2" * 64,
        policy_category="title_layout",
        policy_option="preserve",
        qualification_manifest_sha256=F19A_QUALIFICATION_MANIFEST_SHA256,
        source_outcome_id="shadow_outcome_" + "3" * 64,
        bundle_id="automatic_bundle_" + "4" * 64,
        occurred_at=1,
    )
    source: dict[str, object] = {
        "connection": "primary",
        "library": "ebooks",
        "work_id": "item-1",
        "target_version": "4" * 64,
        "pre_value": "a title",
        "decision_value": {"kind": "text", "value": "A  Title"},
    }
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "review.sqlite3")
        report = _report()
        repository.publish(report, lambda: True)
        proposal_set = evaluate_report(report)
        repository.publish_proposal(proposal_set)
        review_case_id = json.loads(repository.admit_review_case(proposal_set))["review_case_id"]
        assert type(review_case_id) is str
        decision = AutomaticDecision.create(
            review_case_id=review_case_id,
            report_id=report.report_id,
            proposal_set_id=proposal_set.proposal_set_id,
            proposal_id=decision.proposal_id,
            target_fingerprint=decision.target_fingerprint,
            destination=decision.destination,
            outcome=decision.outcome,
            candidate_id=decision.candidate_id,
            resolved_policy_id=decision.resolved_policy_id,
            policy_generation=decision.policy_generation,
            rule_id=decision.rule_id,
            policy_category=decision.policy_category,
            policy_option=decision.policy_option,
            qualification_manifest_sha256=decision.qualification_manifest_sha256,
            source_outcome_id=decision.source_outcome_id,
            bundle_id=decision.bundle_id,
            occurred_at=decision.occurred_at,
        )
        repository._field_effects.field_effect_authority_source = lambda _: source
        repository._connection.execute(
            "INSERT INTO automatic_decisions(decision_id,review_case_id,destination,"
            "idempotency_digest,occurred_at,document) VALUES(?,?,?,?,?,?)",
            (
                decision.decision_id,
                decision.review_case_id,
                decision.destination,
                decision.idempotency_digest,
                decision.occurred_at,
                decision.to_json(),
            ),
        )
        repository._connection.commit()

        assert repository.record_current_field_effect_no_change(decision, 2)
        assert repository.record_current_field_effect_no_change(decision, 3)
        assert repository._connection.execute(
            "SELECT count(*) FROM field_effect_no_changes"
        ).fetchone() == (1,)
        assert repository._connection.execute(
            "SELECT count(*) FROM field_effects"
        ).fetchone() == (0,)
        assert repository._connection.execute(
            "SELECT count(*) FROM field_effect_authorities"
        ).fetchone() == (0,)
        assert repository._connection.execute(
            "SELECT count(*) FROM field_effect_consents"
        ).fetchone() == (0,)
        repository.close()
