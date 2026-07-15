from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.materialize_epub_fixtures import MaterializationError, _manifest


ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "materialize_epub_fixtures.py"
MANIFEST = ROOT / "tests" / "fixtures" / "epub_package" / "manifest.json"


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in sorted(directory.iterdir())
    }


class MaterializeEpubFixturesTests(unittest.TestCase):
    def test_registry_parser_rejects_consumer_recipe_expectation_and_schema_drift(self) -> None:
        original = json.loads(MANIFEST.read_text(encoding="utf-8"))
        mutations = (
            lambda value: value.__setitem__("unknown", True),
            lambda value: value["fixtures"][0].__setitem__(
                "consumer_ownership", ["f07-opening-typo"]
            ),
            lambda value: value["fixtures"][0]["expected"]["package"].__setitem__(
                "exit", 70
            ),
            lambda value: value["fixtures"][0].__setitem__(
                "deterministic_recipe", "unowned-recipe"
            ),
            lambda value: value["fixtures"][1].__setitem__(
                "id", value["fixtures"][0]["id"]
            ),
            lambda value: (
                next(
                    entry
                    for entry in value["fixtures"]
                    if entry["classification"] == "linked"
                ).__setitem__("generator", "tests.unit.missing.Unowned.test"),
                next(
                    entry
                    for entry in value["fixtures"]
                    if entry["classification"] == "linked"
                )["generation_inputs"].__setitem__(
                    "recipe", "tests.unit.missing.Unowned.test"
                ),
            ),
            lambda value: value["fixtures"].remove(
                next(
                    entry
                    for entry in value["fixtures"]
                    if entry["id"] == "opening-node-text-limit-matrix"
                )
            ),
            lambda value: (
                next(entry for entry in value["fixtures"] if entry["id"] == "opening-node-text-limit-matrix").__setitem__(
                    "generator", "tests.unit.test_epub_opening.EpubOpeningTests.test_canonical_envelope_rejects_forged_versions_lineage_and_usage"
                ),
                next(entry for entry in value["fixtures"] if entry["id"] == "opening-node-text-limit-matrix")["generation_inputs"].__setitem__(
                    "recipe", "tests.unit.test_epub_opening.EpubOpeningTests.test_canonical_envelope_rejects_forged_versions_lineage_and_usage"
                ),
            ),
            lambda value: (
                next(entry for entry in value["fixtures"] if entry["id"] == "multiple-rootfiles").__setitem__(
                    "generator", "unowned_missing_recipe"
                ),
                next(entry for entry in value["fixtures"] if entry["id"] == "multiple-rootfiles")["generation_inputs"].__setitem__(
                    "recipe", "unowned_missing_recipe"
                ),
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / "manifest.json"
            for mutate in mutations:
                value = json.loads(json.dumps(original))
                mutate(value)
                candidate.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(MaterializationError):
                    _manifest(candidate)

    def test_two_clean_offline_runs_are_environment_and_order_independent(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        reversed_manifest = dict(manifest)
        reversed_manifest["fixtures"] = list(reversed(manifest["fixtures"]))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            alternate_manifest = root / "manifest.json"
            alternate_manifest.write_text(
                json.dumps(reversed_manifest, ensure_ascii=False), encoding="utf-8"
            )
            network_guard = root / "network-guard"
            network_guard.mkdir()
            (network_guard / "sitecustomize.py").write_text(
                "import socket\n"
                "def blocked(*args, **kwargs):\n"
                "    raise AssertionError('fixture materializer attempted network access')\n"
                "socket.socket = blocked\n"
                "socket.create_connection = blocked\n"
                "socket.getaddrinfo = blocked\n",
                encoding="utf-8",
            )
            outputs = (root / "first", root / "second")
            manifests = (MANIFEST, alternate_manifest)
            environments = (
                {
                    "HOME": str(root / "home-a"),
                    "LANG": "C",
                    "LC_ALL": "C",
                    "TZ": "UTC",
                    "PYTHONHASHSEED": "1",
                },
                {
                    "HOME": str(root / "home-b"),
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8",
                    "TZ": "Pacific/Auckland",
                    "PYTHONHASHSEED": "987654",
                },
            )
            for output, selected_manifest, overrides in zip(
                outputs, manifests, environments, strict=True
            ):
                environment = {
                    **os.environ,
                    **overrides,
                    "PYTHONPATH": os.pathsep.join(
                        (str(network_guard), str(ROOT))
                    ),
                }
                result = subprocess.run(
                    (
                        sys.executable,
                        str(SCRIPT),
                        "--manifest",
                        str(selected_manifest),
                        "--output",
                        str(output),
                    ),
                    cwd=root,
                    env=environment,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertRegex(result.stdout, r"^materialized=\d+ index=SHA256SUMS\n$")
            self.assertEqual(_snapshot(outputs[0]), _snapshot(outputs[1]))

    def test_flat_sha256_index_is_canonical_and_complete(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        expected_names = sorted(
            entry["materialized_filename"]
            for entry in manifest["fixtures"]
            if entry["classification"] == "materialized"
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "cohort"
            result = subprocess.run(
                (sys.executable, str(SCRIPT), "--output", str(output)),
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                sorted(path.name for path in output.glob("*.epub")), expected_names
            )
            self.assertFalse(any(path.is_dir() for path in output.iterdir()))
            lines = (output / "SHA256SUMS").read_text(encoding="ascii").splitlines()
            self.assertEqual(lines, sorted(lines, key=lambda line: line[66:]))
            self.assertEqual(len(lines), len(expected_names))
            for line, name in zip(lines, expected_names, strict=True):
                digest, separator, indexed_name = line.partition("  ")
                self.assertEqual(separator, "  ")
                self.assertEqual(indexed_name, name)
                self.assertEqual(
                    digest, hashlib.sha256((output / name).read_bytes()).hexdigest()
                )


if __name__ == "__main__":
    unittest.main()
