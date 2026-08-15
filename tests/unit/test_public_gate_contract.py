from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "public-gate.yml"


class PublicGateContractTests(unittest.TestCase):
    def test_qualifies_pull_request_heads_only(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("pull_request:", workflow)
        self.assertIn("types: [opened, synchronize, reopened]", workflow)
        self.assertNotIn("\n  push:", workflow)
        self.assertNotIn("ready_for_review", workflow)
        self.assertIn("cancel-in-progress: true", workflow)
        head_sha = "${{ github.event.pull_request.head.sha }}"
        self.assertEqual(workflow.count(f"ref: {head_sha}"), 3)
        self.assertIn(f'--source-sha "{head_sha}"', workflow)
        self.assertNotIn("${{ github.sha }}", workflow)

    def test_retains_three_independent_timeout_bounded_jobs(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        for job in ("fast-source", "exact-installed", "protected-browser"):
            with self.subTest(job=job):
                self.assertIn(f"  {job}:\n", workflow)
        self.assertEqual(workflow.count("runs-on: ubuntu-24.04"), 3)
        self.assertEqual(workflow.count("timeout-minutes: 10"), 3)
        self.assertNotIn("  public-gate:\n", workflow)
        self.assertNotIn("needs:", workflow)


if __name__ == "__main__":
    unittest.main()
