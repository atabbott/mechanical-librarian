from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "public-gate.yml"
EXACT_SHA = "${{ github.event.pull_request.head.sha || github.sha }}"


def test_qualifies_pull_request_heads_and_merged_main() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "pull_request:" in workflow
    assert "types: [opened, synchronize, reopened]" in workflow
    assert "  push:\n    branches: [main]\n" in workflow
    assert "ready_for_review" not in workflow


def test_every_exact_revision_reference_resolves_for_both_events() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert workflow.count(f"ref: {EXACT_SHA}") == 5
    assert f'--source-sha "{EXACT_SHA}"' in workflow
    # An unguarded head reference is null on a push to `main`.
    assert "${{ github.event.pull_request.head.sha }}" not in workflow


def test_concurrency_never_cancels_post_merge_verification() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert ("group: public-gate-${{ github.workflow }}-"
        "${{ github.event.pull_request.number || github.sha }}\n") in workflow
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in workflow
    assert "cancel-in-progress: true" not in workflow


@pytest.mark.parametrize(
    "job",
    ("fast-source", "exact-installed", "protected-browser"),
    ids=("fast_source", "exact_installed", "protected_browser"),
)
def test_retains_the_three_independent_timeout_bounded_tiers(job: str) -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert f"  {job}:\n" in workflow
    assert "  public-gate:\n" not in workflow
    assert "needs:" not in workflow


def test_every_job_is_bounded_on_the_pinned_runner() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "  container-image:\n" in workflow
    assert "  dependency-audit:\n" in workflow
    assert workflow.count("runs-on: ubuntu-24.04") == 5
    assert workflow.count("timeout-minutes: 10") == 5


def test_builds_the_shipped_container_from_the_exact_revision() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "run: docker build --pull --tag mechanical-librarian:gate ." in workflow
    # The built image is never pushed, loaded, or run by the gate: its tag
    # is written once and never referenced again. The `docker run` in the
    # dependency audit runs a different, digest-pinned scanner image and
    # never touches this one.
    assert "docker push" not in workflow
    assert workflow.count("mechanical-librarian:gate") == 1


def test_caches_the_pinned_chromium_payload_but_still_verifies_it() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert ("key: playwright-chromium-${{ runner.os }}-${{ runner.arch }}-"
        "${{ steps.playwright.outputs.version }}\n") in workflow
    assert workflow.count("path: ~/.cache/ms-playwright") == 2
    assert "uv run playwright install --with-deps chromium" in workflow
    assert "run: uv run python scripts/verify_playwright_browser.py" in workflow
    # The payload is saved only after verification succeeds.
    save = workflow.index("actions/cache/save@")
    verify = workflow.index("scripts/verify_playwright_browser.py")
    assert verify < save


def test_every_action_is_sha_pinned_without_persisted_credentials() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    uses = [
        line.strip().removeprefix("uses:").strip()
        for line in workflow.splitlines()
        if line.strip().startswith("uses:")
    ]
    assert len(uses) == 16
    # The references are read out of the workflow at run time, so they cannot
    # become parameters without making the collected case count -- and the
    # cohort identity digest built from it -- move whenever the workflow gains
    # or loses a step. Each reference is carried into its assertion instead, so
    # a failure still names the one that is not pinned.
    for reference in uses:
        _, _, revision = reference.partition("@")
        pinned = revision.split()[0]
        assert len(pinned) == 40, reference
        assert all(value in "0123456789abcdef" for value in pinned), reference
    assert workflow.count("persist-credentials: false") == workflow.count("actions/checkout@")
    assert "permissions:\n  contents: read\n" in workflow
