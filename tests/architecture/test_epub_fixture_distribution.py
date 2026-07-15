from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
