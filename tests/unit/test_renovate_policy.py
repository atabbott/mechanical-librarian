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
        self.assertEqual(self.config["rangeStrategy"], "pin")
        self.assertFalse(self.config["ignoreTests"])
        self.assertFalse(self.config["platformAutomerge"])
        self.assertFalse(self.config["automerge"])

    def test_no_update_class_can_bypass_the_trusted_merge_controller(self) -> None:
        self.assertFalse(self.config["automerge"])
        self.assertFalse(self.config["platformAutomerge"])
        self.assertFalse(self.config["ignoreTests"])
        self.assertTrue(self.config["lockFileMaintenance"]["enabled"])
        self.assertNotIn("automerge", self.config["lockFileMaintenance"])
        for rule in self.config["packageRules"]:
            self.assertFalse(rule.get("automerge", False))

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

    def test_application_manifests_and_toolchains_remain_exact(self) -> None:
        with (ROOT / "pyproject.toml").open("rb") as stream:
            python = tomllib.load(stream)
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
