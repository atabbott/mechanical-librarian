from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class TrackedControlBoundaryTests(unittest.TestCase):
    def tracked_control_paths(self) -> list[str]:
        completed = subprocess.run(
            ["git", "ls-files", ".codex"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return [line for line in completed.stdout.splitlines() if line]

    def test_canonical_controls_are_tracked_and_private_state_is_not(self) -> None:
        paths = self.tracked_control_paths()
        required = {
            ".codex/team.toml",
            ".codex/structured-records.md",
            ".codex/schemas/team-run.schema.json",
            ".codex/scripts/validate_team.py",
            ".codex/scripts/connector_state.py",
            ".codex/skills/deliver-functional-slice/SKILL.md",
            ".codex/tests/test_run_record_validation.py",
        }
        self.assertTrue(required.issubset(paths), required.difference(paths))

        forbidden = (
            ".codex/runs/",
            ".codex/ideas/",
            ".codex/private-test-resources.md",
            "__pycache__",
            ".DS_Store",
        )
        for path in paths:
            self.assertFalse(any(marker in path for marker in forbidden), path)

    def test_tracked_controls_contain_no_private_machine_paths(self) -> None:
        forbidden = (
            b"/" + b"Users/",
            b"/" + b"home/",
            b"\\" + b"Users\\",
            b"aidan" + b"abbott",
        )
        for relative in self.tracked_control_paths():
            data = (ROOT / relative).read_bytes()
            for marker in forbidden:
                self.assertNotIn(marker, data, relative)


if __name__ == "__main__":
    unittest.main()
