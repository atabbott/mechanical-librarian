"""Installed-wheel continuous-review qualification at a representative cohort."""

from __future__ import annotations

import subprocess
import sys

import pytest

from tests.support.continuous_review_scale import (
    _wait_for_https,
    qualify_installed_pipeline,
)


def test_exited_installed_server_reports_only_a_fixed_failure() -> None:
    process = subprocess.Popen(
        (sys.executable, "-c", "raise SystemExit(2)"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    process.wait(timeout=10)

    with pytest.raises(
        AssertionError, match=r"^installed review server did not start$"
    ):
        _wait_for_https(process, "https://127.0.0.1:1")


def test_installed_pipeline_reconciles_representative_cohort_across_restart() -> None:
    qualify_installed_pipeline(
        {"fiction": 4, "history": 20, "science": 20},
        expected_total=45,
    )
