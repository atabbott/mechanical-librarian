from __future__ import annotations

import ast
from importlib import metadata
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from zipfile import ZipFile


REPOSITORY_ROOT = Path(__file__).parents[2]
PACKAGE_ROOT = REPOSITORY_ROOT / "src" / "mechanical_librarian"
FORBIDDEN_FIXED_REFERENCES = (
    "dux-corpus",
    "fixtures/dux",
    "tests.fixtures.dux",
    "tests.support.dux_corpus",
    "358d70039a2d7da5e1d4cf7f19032ed5bf0fdc3f",
    "dux-complete-2026-07-10",
)


class DuxCorpusBoundaryTests(unittest.TestCase):
    def test_production_source_has_no_test_import_or_fixed_corpus_reference(self) -> None:
        violations: list[str] = []
        for path in sorted(PACKAGE_ROOT.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    if any(alias.name == "tests" or alias.name.startswith("tests.") for alias in node.names):
                        violations.append(f"{path.relative_to(REPOSITORY_ROOT)} imports tests")
                elif isinstance(node, ast.ImportFrom):
                    if node.module == "tests" or (node.module or "").startswith("tests."):
                        violations.append(f"{path.relative_to(REPOSITORY_ROOT)} imports tests")
            lowered = source.casefold().replace("\\", "/")
            if any(reference in lowered for reference in FORBIDDEN_FIXED_REFERENCES):
                violations.append(f"{path.relative_to(REPOSITORY_ROOT)} references corpus")
        self.assertEqual(violations, [])

    def test_installed_distribution_excludes_corpus_and_test_support(self) -> None:
        files = metadata.files("mechanical-librarian") or ()
        names = {str(path).replace("\\", "/") for path in files}
        self.assertFalse(any(name.startswith("tests/") for name in names))
        self.assertFalse(any("fixtures/dux" in name or "dux_corpus" in name for name in names))

    def test_built_wheel_excludes_corpus_and_test_support(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            home = temporary / "home"
            cache = temporary / "cache"
            output = temporary / "dist"
            home.mkdir()
            cache.mkdir()
            environment = os.environ.copy()
            environment.update(
                {
                    "HOME": str(home),
                    "UV_CACHE_DIR": str(cache),
                    "UV_OFFLINE": "1",
                }
            )
            completed = subprocess.run(
                [
                    "uv",
                    "build",
                    "--no-build-isolation",
                    "--wheel",
                    "--out-dir",
                    str(output),
                ],
                cwd=REPOSITORY_ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            wheels = list(output.glob("*.whl"))
            self.assertEqual(len(wheels), 1)
            with ZipFile(wheels[0]) as archive:
                names = archive.namelist()
        self.assertFalse(any(name.startswith("tests/") for name in names))
        self.assertFalse(any("fixtures/dux" in name or "dux_corpus" in name for name in names))


if __name__ == "__main__":
    unittest.main()
