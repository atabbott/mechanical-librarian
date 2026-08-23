"""Explicit installed-wheel continuous-review scale qualification."""

from __future__ import annotations

from tests.support.continuous_review_scale import qualify_installed_pipeline


def test_installed_pipeline_reconciles_two_thousand_fairly_across_restart() -> None:
    qualify_installed_pipeline(
        {"fiction": 3, "history": 998, "science": 998},
        expected_total=2_000,
        memory_ceiling_kib=524_288,
    )
