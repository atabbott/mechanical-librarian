from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from importlib import metadata
from pathlib import Path

import mechanical_librarian
from mechanical_librarian.interfaces.cli import main


class PackageSmokeTests(unittest.TestCase):
    def test_installed_metadata_matches_exposed_version(self) -> None:
        self.assertEqual(
            metadata.version("mechanical-librarian"),
            mechanical_librarian.__version__,
        )

    def test_empty_cli_succeeds(self) -> None:
        self.assertEqual(main([]), 0)

    def test_all_modules_import_outside_checkout_without_creating_state(self) -> None:
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in {"PYTHONHOME", "PYTHONPATH"}
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    (
                        "import importlib, pkgutil, mechanical_librarian; "
                        "[importlib.import_module(item.name) for item in "
                        "pkgutil.walk_packages(mechanical_librarian.__path__, "
                        "mechanical_librarian.__name__ + '.')]; "
                        "print(mechanical_librarian.__version__)"
                    ),
                ],
                cwd=temporary_directory,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            created_entries = list(directory.iterdir())

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), mechanical_librarian.__version__)
        self.assertEqual(created_entries, [])


if __name__ == "__main__":
    unittest.main()
