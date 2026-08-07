"""Explicit installed-wheel scale qualification excluded from default discovery."""

from __future__ import annotations

import unittest

from tests.integration.test_continuous_review_scale import (
    ContinuousReviewScaleQualificationTests,
)


class SlowContinuousReviewScaleQualificationTests(
    ContinuousReviewScaleQualificationTests
):
    def test_installed_pipeline_reconciles_one_thousand_fairly_across_restart(
        self,
    ) -> None:
        self._qualify_installed_pipeline(
            {"fiction": 3, "history": 498, "science": 498},
            expected_total=1_000,
        )


if __name__ == "__main__":
    unittest.main()
