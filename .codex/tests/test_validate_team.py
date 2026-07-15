from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import validate_team  # noqa: E402


class TeamValidationTests(unittest.TestCase):
    def test_declarative_team_and_budgets_validate(self) -> None:
        result = validate_team.validate()
        self.assertEqual({"reader", "worker", "reviewer", "challenger"}, set(result["profiles"]))
        self.assertEqual(6, len(result["workflows"]))
        self.assertLessEqual(result["metrics"]["runtime_python_lines"], 2500)
        self.assertLessEqual(result["metrics"]["test_python_lines"], 2500)
        self.assertLessEqual(result["metrics"]["skill_entry_lines_max"], 180)
        self.assertLessEqual(result["metrics"]["mandatory_shared_reads_max"], 3)

    def test_comparison_baseline_preserves_the_superseded_round(self) -> None:
        result = validate_team.validate()
        baseline = result["baseline"]
        self.assertEqual(
            "d9d4be28116ac1b44ef6da124b93f7d0fba00f39",
            baseline["source"]["ref"],
        )
        self.assertEqual(17050, baseline["metrics"]["insertions"])
        self.assertEqual(19, baseline["metrics"]["profiles"])
        self.assertFalse(baseline["forward_run_observation"]["qualified"])

    def test_read_only_workflows_do_not_expose_writer(self) -> None:
        result = validate_team.validate()
        self.assertIsNone(result["workflows"]["explore-product-ideas"]["writer_profile"])
        self.assertIsNone(result["workflows"]["prepare-ready-work"]["writer_profile"])
        self.assertNotIn("worker", result["workflows"]["prepare-ready-work"]["profiles"])

    def test_specialties_are_tasks_not_profiles(self) -> None:
        result = validate_team.validate()
        tasks = {
            task
            for workflow in result["workflows"].values()
            for task in workflow["default_review_tasks"]
        }
        self.assertIn("code_quality", tasks)
        self.assertNotIn("code_quality", result["profiles"])


if __name__ == "__main__":
    unittest.main()
