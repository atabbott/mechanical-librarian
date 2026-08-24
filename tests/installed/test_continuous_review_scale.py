"""Installed-wheel phase-free owner-decision/Activity smoke qualification."""

from tests.support.continuous_review_scale import qualify_installed_decision_activity


def test_installed_owner_decision_activity_smoke() -> None:
    evidence = qualify_installed_decision_activity(
        {"fiction": 4, "history": 20, "science": 20},
        expected_total=45,
    )
    assert evidence.history_events == 1
    assert evidence.category == "owner"
    assert evidence.outcome == "accept"
