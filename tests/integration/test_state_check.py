from __future__ import annotations

import io
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from mechanical_librarian.bootstrap import main


class ComposedStateCheckTests(unittest.TestCase):
    def run_check(self, path: Path) -> tuple[int, str, str]:
        output = io.StringIO()
        errors = io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            exit_code = main(["state-check", "--database", str(path)])
        return exit_code, output.getvalue(), errors.getvalue()

    def test_two_installed_cli_processes_are_restart_safe_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "state.sqlite"
            executable = Path(sys.executable).with_name("mechanical-librarian")
            command = [
                str(executable),
                "state-check",
                "--database",
                str(path),
            ]

            first = subprocess.run(
                command,
                cwd=directory,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            second = subprocess.run(
                command,
                cwd=directory,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            for completed in (first, second):
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stdout, "State check passed.\n")
                self.assertEqual(completed.stderr, "")

            connection = sqlite3.connect(path)
            self.addCleanup(connection.close)
            rows = connection.execute(
                "SELECT id, kind, summary FROM audit_events"
            ).fetchall()
            self.assertEqual(
                rows,
                [("state-check-v1", "state-check", "verification succeeded")],
            )

    def test_exact_readback_succeeds_after_more_than_one_hundred_newer_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            self.assertEqual(self.run_check(path)[0], 0)
            connection = sqlite3.connect(path)
            connection.executemany(
                "INSERT INTO audit_events(id, kind, created_at, summary) "
                "VALUES (?, 'discovery', ?, 'discovered item')",
                tuple(
                    (
                        f"newer-{index:03d}",
                        f"2026-07-11T10:{index // 60:02d}:{index % 60:02d}.000000Z",
                    )
                    for index in range(101)
                ),
            )
            connection.commit()
            recent_ids = {
                row[0]
                for row in connection.execute(
                    "SELECT id FROM audit_events "
                    "ORDER BY created_at DESC, id DESC LIMIT 100"
                )
            }
            connection.close()
            self.assertNotIn("state-check-v1", recent_ids)

            self.assertEqual(
                self.run_check(path),
                (0, "State check passed.\n", ""),
            )

    def test_tampered_event_identity_fails_closed_without_adding_a_row(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            self.assertEqual(self.run_check(path)[0], 0)
            connection = sqlite3.connect(path)
            connection.execute(
                "UPDATE audit_events SET summary = 'verification failed' "
                "WHERE id = 'state-check-v1'"
            )
            connection.commit()
            connection.close()

            exit_code, output, errors = self.run_check(path)
            self.assertEqual(exit_code, 1)
            self.assertEqual(output, "")
            self.assertEqual(
                errors,
                "State check failed: state is corrupt or incompatible.\n",
            )

            connection = sqlite3.connect(path)
            self.addCleanup(connection.close)
            self.assertEqual(
                connection.execute("SELECT count(*) FROM audit_events").fetchone(),
                (1,),
            )

    def test_partial_and_corrupt_state_fail_without_exception_or_path_output(self) -> None:
        for state_kind in ("partial", "corrupt"):
            with self.subTest(state_kind=state_kind), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "hostile-sentinel.sqlite"
                if state_kind == "partial":
                    connection = sqlite3.connect(path)
                    connection.execute("CREATE TABLE audit_events (id TEXT PRIMARY KEY)")
                    connection.close()
                else:
                    path.write_bytes(b"not a sqlite database")

                exit_code, output, errors = self.run_check(path)
                self.assertEqual(exit_code, 1)
                self.assertEqual(output, "")
                self.assertEqual(
                    errors,
                    "State check failed: state could not be verified.\n",
                )
                self.assertNotIn("hostile-sentinel", output + errors)

    def test_unavailable_parent_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / "not-a-directory"
            parent.write_text("sentinel", encoding="utf-8")
            path = parent / "hostile-sentinel.sqlite"

            exit_code, output, errors = self.run_check(path)
            self.assertEqual(exit_code, 1)
            self.assertEqual(output, "")
            self.assertEqual(
                errors,
                "State check failed: state could not be verified.\n",
            )
            self.assertNotIn("hostile-sentinel", output + errors)


if __name__ == "__main__":
    unittest.main()
