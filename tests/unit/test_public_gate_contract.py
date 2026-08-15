from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "public-gate.yml"
EXACT_SHA = "${{ github.event.pull_request.head.sha || github.sha }}"


class PublicGateContractTests(unittest.TestCase):
    def test_qualifies_pull_request_heads_and_merged_main(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("pull_request:", workflow)
        self.assertIn("types: [opened, synchronize, reopened]", workflow)
        self.assertIn("  push:\n    branches: [main]\n", workflow)
        self.assertNotIn("ready_for_review", workflow)

    def test_every_exact_revision_reference_resolves_for_both_events(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertEqual(workflow.count(f"ref: {EXACT_SHA}"), 5)
        self.assertIn(f'--source-sha "{EXACT_SHA}"', workflow)
        # An unguarded head reference is null on a push to `main`.
        self.assertNotIn("${{ github.event.pull_request.head.sha }}", workflow)

    def test_concurrency_never_cancels_post_merge_verification(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(
            "group: public-gate-${{ github.workflow }}-"
            "${{ github.event.pull_request.number || github.sha }}\n",
            workflow,
        )
        self.assertIn(
            "cancel-in-progress: ${{ github.event_name == 'pull_request' }}",
            workflow,
        )
        self.assertNotIn("cancel-in-progress: true", workflow)

    def test_retains_the_three_independent_timeout_bounded_tiers(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        for job in ("fast-source", "exact-installed", "protected-browser"):
            with self.subTest(job=job):
                self.assertIn(f"  {job}:\n", workflow)
        self.assertNotIn("  public-gate:\n", workflow)
        self.assertNotIn("needs:", workflow)

    def test_every_job_is_bounded_on_the_pinned_runner(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("  container-image:\n", workflow)
        self.assertIn("  dependency-audit:\n", workflow)
        self.assertEqual(workflow.count("runs-on: ubuntu-24.04"), 5)
        self.assertEqual(workflow.count("timeout-minutes: 10"), 5)

    def test_builds_the_shipped_container_from_the_exact_revision(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(
            "run: docker build --pull --tag mechanical-librarian:gate .",
            workflow,
        )
        # The built image is never pushed, loaded, or run by the gate: its tag
        # is written once and never referenced again. The `docker run` in the
        # dependency audit runs a different, digest-pinned scanner image and
        # never touches this one.
        self.assertNotIn("docker push", workflow)
        self.assertEqual(workflow.count("mechanical-librarian:gate"), 1)

    def test_caches_the_pinned_chromium_payload_but_still_verifies_it(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(
            "key: playwright-chromium-${{ runner.os }}-${{ runner.arch }}-"
            "${{ steps.playwright.outputs.version }}\n",
            workflow,
        )
        self.assertEqual(workflow.count("path: ~/.cache/ms-playwright"), 2)
        self.assertIn("uv run playwright install --with-deps chromium", workflow)
        self.assertIn(
            "run: uv run python scripts/verify_playwright_browser.py",
            workflow,
        )
        # The payload is saved only after verification succeeds.
        save = workflow.index("actions/cache/save@")
        verify = workflow.index("scripts/verify_playwright_browser.py")
        self.assertLess(verify, save)

    def test_every_action_is_sha_pinned_without_persisted_credentials(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        uses = [
            line.strip().removeprefix("uses:").strip()
            for line in workflow.splitlines()
            if line.strip().startswith("uses:")
        ]
        self.assertEqual(len(uses), 16)
        for reference in uses:
            with self.subTest(reference=reference):
                _, _, revision = reference.partition("@")
                pinned = revision.split()[0]
                self.assertEqual(len(pinned), 40)
                self.assertTrue(all(value in "0123456789abcdef" for value in pinned))
        self.assertEqual(
            workflow.count("persist-credentials: false"),
            workflow.count("actions/checkout@"),
        )
        self.assertIn("permissions:\n  contents: read\n", workflow)


if __name__ == "__main__":
    unittest.main()
