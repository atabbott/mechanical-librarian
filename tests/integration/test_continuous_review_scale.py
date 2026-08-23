"""Installed-wheel continuous-review qualification at a representative cohort."""

from __future__ import annotations

import subprocess
import sys

import pytest

from tests.support import continuous_review_scale
from tests.support.continuous_review_scale import (
    _wait_for_https,
    qualify_installed_pipeline,
)


def test_exited_installed_server_reports_its_exit_code() -> None:
    process = subprocess.Popen(
        (sys.executable, "-c", "raise SystemExit(2)"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    process.wait(timeout=10)

    with pytest.raises(
        AssertionError,
        match=r"^installed review server exited before serving, exit code 2$",
    ):
        _wait_for_https(process, "https://127.0.0.1:1")


def test_unresponsive_installed_server_reports_the_deadline_it_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The branch a live-but-slow server takes, which no test had covered.

    It is the branch every recorded sighting is suspected of, and it used to
    raise the same string as the death above, so neither the message nor a
    test could tell them apart (#730).
    """

    monkeypatch.setattr(continuous_review_scale, "_REVIEW_SERVER_START_SECONDS", 0.5)
    process = subprocess.Popen(
        (sys.executable, "-c", "import time; time.sleep(30)"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        with pytest.raises(
            AssertionError,
            match=(
                r"^installed review server stayed alive without serving "
                r"for 0\.5 seconds$"
            ),
        ):
            _wait_for_https(process, "https://127.0.0.1:1")
        assert process.poll() is None
    finally:
        process.kill()
        process.wait(timeout=10)


def test_installed_pipeline_reconciles_then_restarts_completed_cohort() -> None:
    qualify_installed_pipeline(
        {"fiction": 4, "history": 20, "science": 20},
        expected_total=45,
    )
