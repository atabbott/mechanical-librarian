#!/usr/bin/env python3
"""Qualify the exact-empty first-run lifecycle at one source head."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES = (
    "tests/unit/test_first_boot.py",
    "tests/unit/test_setup_state_store.py",
    "tests/unit/test_setup_web.py::test_surviving_session_rotates_csrf_for_mutation_after_restart",
    "tests/unit/test_setup_web.py::test_seven_step_journey_closes_setup_routes_after_exact_kickoff_copy",
    "tests/unit/test_setup_journey.py::test_kickoff_atomically_promotes_and_preserves_connection_secret",
    "tests/unit/test_composition_reopen.py::test_ordinary_server_releases_first_owner_state_for_interruption_and_resume",
    "tests/unit/test_runtime_dispatch.py::test_transition_orders_dormant_start_commit_publish_admit_and_drain",
    "tests/unit/test_review_web_transport.py::test_first_boot_acknowledgement_is_authenticated_csrf_bound_and_durable",
    "tests/browser/test_review_browser.py::test_first_run_installed_semantics_responsiveness_and_handoff",
    "tests/browser/test_review_browser.py::test_first_owner_claim_requires_the_deployment_claim_token",
)


def _git(*arguments: str) -> str:
    result = subprocess.run(
        ("git", *arguments),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-sha", required=True)
    arguments = parser.parse_args()
    head = _git("rev-parse", "HEAD")
    if head != arguments.source_sha or _git("status", "--porcelain"):
        raise SystemExit("exact_head_required")
    assets = ROOT / "frontend" / "dist"
    if not assets.is_dir():
        raise SystemExit("installed_assets_required")
    environment = os.environ.copy()
    environment["ML_REACT_ASSET_DIR"] = str(assets)
    completed = subprocess.run(
        (sys.executable, "-m", "pytest", *CASES, "-q"),
        cwd=ROOT,
        env=environment,
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit("first_run_lifecycle_failed")
    print(
        json.dumps(
            {
                "schema": "first-run-lifecycle-qualification-v1",
                "source_sha": head,
                "status": "passed",
                "properties": [
                    "deployment-held-claim-token",
                    "exact-empty-selection",
                    "ordinary-owner-session-continuity",
                    "one-listener-promotion",
                    "permanent-setup-route-closure",
                    "preparation-handoff-advances",
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
