from __future__ import annotations

import json
from pathlib import Path
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / ".github" / "renovate.json"


class RenovatePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    def test_supported_managers_and_bounded_schedule_are_explicit(self) -> None:
        self.assertEqual(
            set(self.config["enabledManagers"]),
            {"dockerfile", "github-actions", "npm", "pep621"},
        )
        self.assertEqual(self.config["minimumReleaseAge"], "7 days")
        self.assertEqual(self.config["prConcurrentLimit"], 3)
        self.assertEqual(self.config["branchConcurrentLimit"], 3)
        self.assertFalse(self.config["ignoreTests"])
        self.assertFalse(self.config["platformAutomerge"])
        self.assertFalse(self.config["automerge"])

    def test_automerge_rules_cannot_admit_high_risk_managers_or_updates(self) -> None:
        automatic = [
            rule for rule in self.config["packageRules"] if rule.get("automerge")
        ]
        self.assertEqual(len(automatic), 2)
        for rule in automatic:
            self.assertLessEqual(set(rule["matchManagers"]), {"npm", "pep621"})
            self.assertLessEqual(set(rule["matchUpdateTypes"]), {"patch", "minor"})
            self.assertEqual(rule["matchCurrentValue"], "/^(?:\\^|>=)/")
        minor = next(
            rule for rule in automatic if rule["matchUpdateTypes"] == ["minor"]
        )
        self.assertEqual(minor["matchCurrentVersion"], "!/^0\\./")

    def test_lock_maintenance_is_the_only_other_automatic_class(self) -> None:
        maintenance = self.config["lockFileMaintenance"]
        self.assertTrue(maintenance["enabled"])
        self.assertTrue(maintenance["automerge"])
        self.assertEqual(maintenance["automergeType"], "pr")

    def test_high_risk_updates_require_dashboard_approval(self) -> None:
        manual = [
            rule
            for rule in self.config["packageRules"]
            if rule.get("dependencyDashboardApproval")
        ]
        self.assertTrue(
            any(rule.get("matchUpdateTypes") == ["major"] for rule in manual)
        )
        self.assertTrue(
            any(rule.get("matchUpdateTypes") == ["replacement"] for rule in manual)
        )
        self.assertTrue(
            any(
                set(rule.get("matchManagers", ()))
                == {"dockerfile", "github-actions"}
                for rule in manual
            )
        )
        protected_packages = {
            package
            for rule in manual
            for package in rule.get("matchPackageNames", ())
        }
        self.assertEqual(
            protected_packages,
            {"node", "npm", "playwright", "python", "setuptools", "uv"},
        )

    def test_application_manifests_are_ranged_but_toolchains_remain_exact(self) -> None:
        with (ROOT / "pyproject.toml").open("rb") as stream:
            python = tomllib.load(stream)
        for requirement in python["project"]["dependencies"]:
            self.assertIn(">=", requirement)
            self.assertIn(",<", requirement)
            self.assertNotIn("==", requirement)
        dev = python["dependency-groups"]["dev"]
        self.assertIn("setuptools==83.0.0", dev)
        for requirement in dev:
            if not requirement.startswith("setuptools"):
                self.assertIn(">=", requirement)
                self.assertIn(",<", requirement)

        frontend = json.loads(
            (ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
        )
        for section in ("dependencies", "devDependencies"):
            for requirement in frontend[section].values():
                self.assertTrue(requirement.startswith("^"))
        self.assertEqual(frontend["engines"]["node"], "24.19.0")
        self.assertEqual(frontend["engines"]["npm"], "11.17.0")
        self.assertEqual(frontend["packageManager"], "npm@11.17.0")


if __name__ == "__main__":
    unittest.main()
