from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).parents[2]


class InstalledEpubOpeningEvidenceTests(unittest.TestCase):
    def test_exact_built_wheel_runs_durable_selector_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            distribution = root / "dist"
            cohort = root / "cohort"
            environment = root / "environment"
            commands = (
                (
                    "uv",
                    "build",
                    "--wheel",
                    "--out-dir",
                    str(distribution),
                ),
                ("uv", "venv", "--python", sys.executable, str(environment)),
            )
            for command in commands:
                completed = subprocess.run(
                    command,
                    cwd=ROOT,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=120,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
            environment_python = environment / "bin" / "python"
            install = subprocess.run(
                (
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    str(environment_python),
                    str(next(distribution.glob("*.whl"))),
                ),
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            self.assertEqual(install.returncode, 0, install.stderr)
            materialize = subprocess.run(
                (
                    sys.executable,
                    str(ROOT / "scripts" / "materialize_epub_fixtures.py"),
                    "--output",
                    str(cohort),
                ),
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            self.assertEqual(materialize.returncode, 0, materialize.stderr)
            qualify = subprocess.run(
                (
                    sys.executable,
                    str(ROOT / "scripts" / "qualify_epub_opening_evidence.py"),
                    "--executable",
                    str(environment / "bin" / "mechanical-librarian"),
                    "--python",
                    str(environment_python),
                    "--cohort",
                    str(cohort),
                    "--manifest",
                    str(
                        ROOT
                        / "tests"
                        / "fixtures"
                        / "epub_package"
                        / "manifest.json"
                    ),
                ),
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=420,
            )
            self.assertEqual(qualify.returncode, 0, qualify.stderr)
            evidence = json.loads(qualify.stdout)
            self.assertEqual(
                evidence["schema"], "epub-opening-evidence-qualification-v1"
            )
            self.assertEqual(evidence["artifact"], "installed-wheel-cli")
            aggregate = evidence["aggregate"]
            self.assertEqual(aggregate["selectors"], 29)
            self.assertEqual(
                aggregate["durable"] + sum(aggregate["operational_failures"].values()),
                29,
            )
            self.assertEqual(aggregate["restart_reused"], aggregate["durable"])
            self.assertEqual(aggregate["source_invalidations"], 1)
            self.assertNotIn(str(root), qualify.stdout)
            self.assertNotIn(".epub", qualify.stdout)


if __name__ == "__main__":
    unittest.main()
