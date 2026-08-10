from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class GuardedCheckpointProbeTests(unittest.TestCase):
    def test_probe_uses_persisted_consent_across_restart(self) -> None:
        script = Path(__file__).parents[2] / "scripts" / "guarded_checkpoint_probe.py"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "state"
            for phase in ("prepare", "recover"):
                completed = subprocess.run(
                    [sys.executable, "-I", str(script), phase, "--root", str(root)],
                    check=True,
                    capture_output=True,
                    timeout=30,
                )
        result = json.loads(completed.stdout.decode("utf-8"))
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["schema"], "guarded-checkpoint-restart-v1")


if __name__ == "__main__":
    unittest.main()
