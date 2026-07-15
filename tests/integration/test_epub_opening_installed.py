from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).parents[2]


class InstalledEpubOpeningCohortTests(unittest.TestCase):
    def test_exact_built_wheel_runs_complete_materialized_f07a_cohort(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            distribution = root / "dist"
            cohort = root / "cohort"
            environment = root / "environment"
            build = subprocess.run(
                ("uv", "build", "--wheel", "--out-dir", str(distribution)),
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            create_environment = subprocess.run(
                (
                    "uv",
                    "venv",
                    "--python",
                    sys.executable,
                    str(environment),
                ),
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            self.assertEqual(
                create_environment.returncode, 0, create_environment.stderr
            )
            environment_python = environment / "bin" / "python"
            install = subprocess.run(
                (
                    "uv", "pip", "install", "--python", str(environment_python),
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
            executable = environment / "bin" / "mechanical-librarian"
            self.assertTrue(executable.is_file())
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
                    str(ROOT / "scripts" / "qualify_epub_opening.py"),
                    "--executable",
                    str(executable),
                    "--cohort",
                    str(cohort),
                ),
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=180,
            )
            self.assertEqual(qualify.returncode, 0, qualify.stderr)
            evidence = json.loads(qualify.stdout)
            self.assertEqual(evidence["schema"], "epub-opening-qualification-v1")
            self.assertEqual(evidence["artifact"], "installed-wheel-cli")
            self.assertEqual(evidence["aggregate"]["total"], 23)
            self.assertEqual(
                sum(evidence["aggregate"]["opening_outcomes"].values()), 23
            )
            self.assertNotIn(str(root), qualify.stdout)
            self.assertNotIn(".epub", qualify.stdout)


if __name__ == "__main__":
    unittest.main()
