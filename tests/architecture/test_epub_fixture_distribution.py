from __future__ import annotations

import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).parents[2]


class EpubFixtureDistributionTests(unittest.TestCase):
    def test_wheel_and_sdist_exclude_fixture_registry_generators_and_materialized_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            completed = subprocess.run(
                (
                    "uv",
                    "build",
                    "--sdist",
                    "--wheel",
                    "--out-dir",
                    str(output),
                ),
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            wheel = next(output.glob("*.whl"))
            sdist = next(output.glob("*.tar.gz"))
            with ZipFile(wheel) as archive:
                wheel_names = archive.namelist()
            with tarfile.open(sdist, "r:gz") as archive:
                sdist_names = archive.getnames()
        for names in (wheel_names, sdist_names):
            serialized = "\n".join(names)
            self.assertNotIn("tests/fixtures/epub_package", serialized)
            self.assertNotIn("tests/support/epub_packages.py", serialized)
            self.assertNotIn("materialize_epub_fixtures.py", serialized)
            self.assertFalse(any(name.casefold().endswith(".epub") for name in names))
        self.assertTrue(any(name.endswith("/frontend/package.json") for name in sdist_names))
        self.assertTrue(
            any(name.endswith("/scripts/build_frontend.py") for name in sdist_names)
        )
        self.assertTrue(
            any(
                name.endswith("/docs/reference/review-openapi-v1.json")
                for name in sdist_names
            )
        )
        self.assertFalse(any("frontend/src/generated" in name for name in sdist_names))
        self.assertFalse(any("frontend/node_modules" in name for name in sdist_names))
        self.assertFalse(any(name.endswith(".map") for name in wheel_names + sdist_names))
        self.assertTrue(
            any("interfaces/web/react_assets/assets/" in name for name in wheel_names)
        )


if __name__ == "__main__":
    unittest.main()
