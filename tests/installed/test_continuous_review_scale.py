"""Installed-wheel-only representative continuous-review qualification."""

from tests.support.continuous_review_scale import qualify_installed_pipeline


def test_installed_pipeline_reconciles_then_restarts_completed_cohort() -> None:
    qualify_installed_pipeline(
        {"fiction": 4, "history": 20, "science": 20},
        expected_total=45,
    )
