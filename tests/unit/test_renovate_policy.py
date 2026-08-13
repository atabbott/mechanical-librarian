from __future__ import annotations

import json
import re
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / ".github" / "renovate.json"


class RenovatePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    def effective_policy(
        self,
        *,
        manager: str,
        package: str,
        current_version: str,
        update_type: str,
    ) -> dict[str, object]:
        effective: dict[str, object] = {
            "automerge": self.config["automerge"],
            "dependencyDashboardApproval": False,
        }
        for rule in self.config["packageRules"]:
            if managers := rule.get("matchManagers"):
                if manager not in managers:
                    continue
            if packages := rule.get("matchPackageNames"):
                if package not in packages:
                    continue
            if update_types := rule.get("matchUpdateTypes"):
                if update_type not in update_types:
                    continue
            if version_pattern := rule.get("matchCurrentVersion"):
                negated = version_pattern.startswith("!/")
                pattern = version_pattern[2:-1] if negated else version_pattern[1:-1]
                matches = re.search(pattern, current_version) is not None
                if matches == negated:
                    continue
            for field in (
                "automerge",
                "dependencyDashboardApproval",
                "groupName",
            ):
                if field in rule:
                    effective[field] = rule[field]
        return effective

    def test_supported_managers_and_bounded_schedule_are_explicit(self) -> None:
        self.assertEqual(
            set(self.config["enabledManagers"]),
            {"dockerfile", "github-actions", "npm", "pep621"},
        )
        self.assertEqual(self.config["minimumReleaseAge"], "7 days")
        self.assertEqual(self.config["prCreation"], "immediate")
        self.assertEqual(self.config["prConcurrentLimit"], 5)
        self.assertEqual(self.config["branchConcurrentLimit"], 5)
        self.assertEqual(self.config["rangeStrategy"], "pin")
        self.assertFalse(self.config["ignoreTests"])
        self.assertTrue(self.config["platformAutomerge"])
        self.assertFalse(self.config["automerge"])
        self.assertEqual(self.config["automergeType"], "pr")
        self.assertEqual(self.config["automergeStrategy"], "squash")

    def test_only_named_low_risk_classes_enable_platform_automerge(self) -> None:
        self.assertFalse(self.config["automerge"])
        self.assertTrue(self.config["platformAutomerge"])
        self.assertFalse(self.config["ignoreTests"])
        self.assertTrue(self.config["lockFileMaintenance"]["enabled"])
        self.assertTrue(self.config["lockFileMaintenance"]["automerge"])
        automerge_rules = {
            rule["description"]
            for rule in self.config["packageRules"]
            if rule.get("automerge")
        }
        self.assertEqual(
            automerge_rules,
            {
                "Group compatible frontend updates",
                "Group Python patch updates",
                "Group stable Python minor updates",
            },
        )

    def test_project_python_compatibility_range_is_not_managed(self) -> None:
        ignored = [
            rule
            for rule in self.config["packageRules"]
            if rule.get("matchManagers") == ["pep621"]
            and rule.get("matchDepTypes") == ["requires-python"]
        ]
        self.assertEqual(len(ignored), 1)
        self.assertFalse(ignored[0]["enabled"])

    def test_only_major_and_replacement_updates_require_dashboard_approval(
        self,
    ) -> None:
        manual = [
            rule
            for rule in self.config["packageRules"]
            if rule.get("dependencyDashboardApproval")
        ]
        self.assertEqual(
            {rule["description"] for rule in manual},
            {
                "Require approval for major updates",
                "Require approval for dependency replacements",
            },
        )

    def test_protected_classes_override_earlier_automerge_rules(self) -> None:
        positions = {
            rule["description"]: index
            for index, rule in enumerate(self.config["packageRules"])
        }
        protected = {
            "Prevent automatic merging of pre-1.0 minor updates",
            "Require approval for major updates",
            "Prevent automatic merging of infrastructure and workflow updates",
            "Prevent automatic merging of runtime, toolchain, and build-backend updates",
            "Prevent automatic merging of Playwright minor updates",
            "Require approval for dependency replacements",
        }
        for description in protected:
            rule = self.config["packageRules"][positions[description]]
            self.assertFalse(rule["automerge"])

        enabling_positions = [
            positions["Group compatible frontend updates"],
            positions["Group Python patch updates"],
            positions["Group stable Python minor updates"],
        ]
        self.assertTrue(
            all(
                positions[description] > max(enabling_positions)
                for description in protected
            )
        )

        runtime_packages = next(
            set(rule["matchPackageNames"])
            for rule in self.config["packageRules"]
            if rule["description"]
            == "Prevent automatic merging of runtime, toolchain, and build-backend updates"
        )
        self.assertEqual(
            runtime_packages,
            {
                "@eslint/js",
                "@types/node",
                "@vitejs/plugin-react",
                "eslint",
                "eslint-plugin-react-hooks",
                "eslint-plugin-react-refresh",
                "globals",
                "jsdom",
                "node",
                "npm",
                "openapi-typescript",
                "python",
                "setuptools",
                "typescript",
                "typescript-eslint",
                "vite",
                "vitest",
                "uv",
            },
        )

    def test_effective_update_classification_preserves_manual_boundaries(
        self,
    ) -> None:
        cases = (
            ("npm", "react", "19.2.8", "minor", True, False),
            ("npm", "@testing-library/react", "16.3.2", "patch", True, False),
            ("npm", "vite", "8.2.1", "minor", False, False),
            ("npm", "eslint", "10.8.1", "patch", False, False),
            ("npm", "eslint-plugin-react-refresh", "0.5.3", "patch", False, False),
            ("pep621", "playwright", "1.61.0", "patch", True, False),
            ("pep621", "playwright", "1.61.0", "minor", False, False),
            ("pep621", "fastapi", "0.116.1", "minor", False, False),
            ("pep621", "pydantic", "2.13.4", "minor", True, False),
            ("dockerfile", "python", "3.13.14", "patch", False, False),
            ("github-actions", "actions/checkout", "6.0.2", "patch", False, False),
            ("npm", "react", "19.2.8", "major", False, True),
            ("npm", "react", "19.2.8", "replacement", False, True),
        )
        for manager, package, current, update, automerge, approval in cases:
            with self.subTest(package=package, update=update):
                effective = self.effective_policy(
                    manager=manager,
                    package=package,
                    current_version=current,
                    update_type=update,
                )
                self.assertEqual(effective["automerge"], automerge)
                self.assertEqual(
                    effective["dependencyDashboardApproval"], approval
                )

    def test_application_manifests_and_toolchains_remain_exact(self) -> None:
        with (ROOT / "pyproject.toml").open("rb") as stream:
            python = tomllib.load(stream)
        self.assertEqual(python["project"]["requires-python"], ">=3.13,<3.15")
        for requirement in python["project"]["dependencies"]:
            self.assertIn("==", requirement)
        dev = python["dependency-groups"]["dev"]
        self.assertIn("setuptools==83.0.0", dev)
        for requirement in dev:
            self.assertIn("==", requirement)

        frontend = json.loads(
            (ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
        )
        for section in ("dependencies", "devDependencies"):
            for requirement in frontend[section].values():
                self.assertRegex(requirement, r"^\d+\.\d+\.\d+$")
        for requirement in frontend["overrides"].values():
            self.assertRegex(requirement, r"^\d+\.\d+\.\d+$")
        self.assertEqual(frontend["engines"]["node"], "24.19.0")
        self.assertEqual(frontend["engines"]["npm"], "11.17.0")
        self.assertEqual(frontend["packageManager"], "npm@11.17.0")


if __name__ == "__main__":
    unittest.main()
