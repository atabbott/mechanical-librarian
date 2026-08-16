"""Explicit installed-wheel continuous-review scale qualification."""

from __future__ import annotations

from tests.integration.test_continuous_review_scale import qualify_installed_pipeline


def test_installed_pipeline_reconciles_one_thousand_fairly_across_restart() -> None:
    qualify_installed_pipeline(
        {"fiction": 3, "history": 498, "science": 498},
        expected_total=1_000,
        memory_ceiling_kib=524_288,
    )
