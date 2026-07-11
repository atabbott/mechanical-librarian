from __future__ import annotations

import io
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from mechanical_librarian.bootstrap import _run_state_check, main
from mechanical_librarian.infrastructure.persistence import sqlite
from mechanical_librarian.infrastructure.persistence.sqlite import (
    Migration,
    SchemaError,
)


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
            self.assertEqual(
                connection.execute("PRAGMA journal_mode").fetchone(),
                ("wal",),
            )
            rows = connection.execute(
                "SELECT id, kind, summary FROM audit_events"
            ).fetchall()
            self.assertEqual(
                rows,
                [("state-check-v1", "state-check", "verification succeeded")],
            )
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

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

    def test_unvisited_managed_page_corruption_fails_without_mutating_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            self.assertEqual(self.run_check(path)[0], 0)
            connection = sqlite3.connect(path)
            connection.execute(
                "INSERT INTO instance_metadata(key, value) VALUES ('probe', ?)",
                ("x" * 3000,),
            )
            connection.commit()
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            page_size = connection.execute("PRAGMA page_size").fetchone()[0]
            root_page = connection.execute(
                "SELECT rootpage FROM sqlite_master WHERE name = 'instance_metadata'"
            ).fetchone()[0]
            connection.close()

            contents = bytearray(path.read_bytes())
            page_offset = (root_page - 1) * page_size
            contents[page_offset + 5 : page_offset + 7] = (1).to_bytes(2, "big")
            path.write_bytes(contents)
            probe = sqlite3.connect(path)
            try:
                self.assertNotEqual(
                    probe.execute("PRAGMA integrity_check").fetchall(),
                    [("ok",)],
                )
            finally:
                probe.close()
            before = {
                candidate.name: candidate.read_bytes()
                for candidate in Path(directory).iterdir()
            }

            with patch(
                "mechanical_librarian.bootstrap.connect_existing",
                side_effect=AssertionError("corrupt source must not be opened"),
            ):
                self.assertEqual(
                    self.run_check(path),
                    (1, "", "State check failed: state could not be verified.\n"),
                )
            after = {
                candidate.name: candidate.read_bytes()
                for candidate in Path(directory).iterdir()
            }
            self.assertEqual(after, before)

    def test_rejected_existing_state_is_byte_for_byte_unchanged(self) -> None:
        for state_kind in ("delete", "journal", "wal"):
            with (
                self.subTest(state_kind=state_kind),
                tempfile.TemporaryDirectory() as directory,
            ):
                path = Path(directory) / "hostile-sentinel.sqlite"
                connection = sqlite3.connect(path)
                if state_kind == "wal":
                    self.assertEqual(
                        connection.execute("PRAGMA journal_mode = WAL").fetchone(),
                        ("wal",),
                    )
                connection.execute("CREATE TABLE foreign_state(value TEXT)")
                connection.commit()
                if state_kind == "delete":
                    connection.close()
                elif state_kind == "journal":
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute("INSERT INTO foreign_state VALUES ('pending')")

                before = {
                    candidate.name: candidate.read_bytes()
                    for candidate in Path(directory).iterdir()
                    if candidate.is_file()
                }
                if state_kind == "journal":
                    self.assertIn(f"{path.name}-journal", before)
                elif state_kind == "wal":
                    self.assertIn(f"{path.name}-wal", before)
                    self.assertIn(f"{path.name}-shm", before)
                self.assertEqual(
                    self.run_check(path),
                    (1, "", "State check failed: state could not be verified.\n"),
                )
                after = {
                    candidate.name: candidate.read_bytes()
                    for candidate in Path(directory).iterdir()
                    if candidate.is_file()
                }
                self.assertEqual(after, before)
                if state_kind != "delete":
                    connection.close()

    def test_non_regular_recovery_sidecars_are_rejected_without_changes(self) -> None:
        cases = (
            ("-journal", False),
            ("-wal", False),
            ("-shm", False),
            ("-shm", True),
        )
        for suffix, dangling in cases:
            with (
                self.subTest(suffix=suffix, dangling=dangling),
                tempfile.TemporaryDirectory() as directory,
            ):
                path = Path(directory) / "state.sqlite"
                self.assertEqual(self.run_check(path)[0], 0)
                target = Path(directory) / "sidecar-target"
                if not dangling:
                    target.write_bytes(b"synthetic sentinel")
                sidecar = Path(f"{path}{suffix}")
                sidecar.symlink_to(target)
                before_names = {candidate.name for candidate in Path(directory).iterdir()}
                before_main = path.read_bytes()
                before_target = target.read_bytes() if not dangling else None

                self.assertEqual(
                    self.run_check(path),
                    (1, "", "State check failed: state could not be verified.\n"),
                )
                self.assertEqual(
                    {candidate.name for candidate in Path(directory).iterdir()},
                    before_names,
                )
                self.assertEqual(path.read_bytes(), before_main)
                self.assertTrue(sidecar.is_symlink())
                self.assertEqual(sidecar.readlink(), target)
                if not dangling:
                    self.assertEqual(target.read_bytes(), before_target)

    def test_orphan_recovery_sidecars_block_first_publication_and_allow_retry(self) -> None:
        cases = (
            ("-journal", b"synthetic journal"),
            ("-wal", b"synthetic wal"),
            ("-shm", b"synthetic shm"),
            ("-shm", None),
        )
        for suffix, contents in cases:
            with (
                self.subTest(suffix=suffix, dangling=contents is None),
                tempfile.TemporaryDirectory() as directory,
            ):
                path = Path(directory) / "state.sqlite"
                sidecar = Path(f"{path}{suffix}")
                if contents is None:
                    sidecar.symlink_to(Path(directory) / "missing-sidecar-target")
                else:
                    sidecar.write_bytes(contents)
                before_names = {candidate.name for candidate in Path(directory).iterdir()}

                self.assertEqual(
                    self.run_check(path),
                    (1, "", "State check failed: state could not be verified.\n"),
                )
                self.assertFalse(path.exists())
                self.assertEqual(
                    {candidate.name for candidate in Path(directory).iterdir()},
                    before_names,
                )
                if contents is None:
                    self.assertTrue(sidecar.is_symlink())
                else:
                    self.assertEqual(sidecar.read_bytes(), contents)

                sidecar.unlink()
                self.assertEqual(self.run_check(path)[0], 0)

    def test_real_orphan_wal_cannot_replace_newly_published_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            donor = Path(directory) / "donor.sqlite"
            path = Path(directory) / "state.sqlite"
            connection = sqlite3.connect(donor)
            try:
                self.assertEqual(
                    connection.execute("PRAGMA journal_mode = WAL").fetchone(),
                    ("wal",),
                )
                connection.execute("CREATE TABLE stale_payload(value TEXT)")
                connection.execute("INSERT INTO stale_payload VALUES ('stale')")
                connection.commit()
                orphan_paths = tuple(
                    Path(f"{path}{suffix}") for suffix in ("-wal", "-shm")
                )
                for suffix, orphan in zip(("-wal", "-shm"), orphan_paths, strict=True):
                    shutil.copyfile(Path(f"{donor}{suffix}"), orphan)
                before = {orphan.name: orphan.read_bytes() for orphan in orphan_paths}

                self.assertEqual(
                    self.run_check(path),
                    (1, "", "State check failed: state could not be verified.\n"),
                )
                self.assertFalse(path.exists())
                self.assertEqual(
                    {orphan.name: orphan.read_bytes() for orphan in orphan_paths},
                    before,
                )
            finally:
                connection.close()

    def test_sidecar_created_during_staging_blocks_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            real_publish = sqlite.publish_staged_state

            def publish_after_orphan(staged: Path, target: Path) -> bool:
                Path(f"{target}-wal").write_bytes(b"appeared during staging")
                return real_publish(staged, target)

            with patch(
                "mechanical_librarian.bootstrap.publish_staged_state",
                side_effect=publish_after_orphan,
            ):
                self.assertEqual(
                    self.run_check(path),
                    (1, "", "State check failed: state could not be verified.\n"),
                )

            self.assertFalse(path.exists())
            self.assertEqual(Path(f"{path}-wal").read_bytes(), b"appeared during staging")
            self.assertEqual(
                {candidate.name for candidate in Path(directory).iterdir()},
                {f"{path.name}-wal"},
            )

    def test_existing_empty_state_and_in_memory_name_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hostile-sentinel.sqlite"
            path.touch()

            self.assertEqual(
                self.run_check(path),
                (1, "", "State check failed: state could not be verified.\n"),
            )
            self.assertEqual(path.read_bytes(), b"")
            self.assertEqual(
                {candidate.name for candidate in Path(directory).iterdir()},
                {path.name},
            )

            executable = Path(sys.executable).with_name("mechanical-librarian")
            completed = subprocess.run(
                [str(executable), "state-check", "--database", ":memory:"],
                cwd=directory,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(completed.stdout, "")
            self.assertNotIn(":memory:", completed.stderr)
            self.assertEqual(
                {candidate.name for candidate in Path(directory).iterdir()},
                {path.name},
            )

    def test_failed_first_initialization_leaves_retryable_absent_state(self) -> None:
        failed_migration = Migration(
            1,
            (
                "CREATE TABLE schema_migrations "
                "(version INTEGER PRIMARY KEY, checksum TEXT NOT NULL)",
                "CREATE TABLE incomplete_state (id INTEGER PRIMARY KEY)",
                "CREATE TABLE",
            ),
            frozenset({"schema_migrations", "incomplete_state"}),
        )
        failures = (
            patch.object(sqlite, "MIGRATIONS", (failed_migration,)),
            patch.object(
                sqlite,
                "_enable_wal",
                side_effect=SchemaError("injected WAL failure"),
            ),
        )
        for failure in failures:
            with (
                self.subTest(failure=failure),
                tempfile.TemporaryDirectory() as directory,
            ):
                path = Path(directory) / "state.sqlite"
                with failure:
                    self.assertEqual(
                        self.run_check(path),
                        (1, "", "State check failed: state could not be verified.\n"),
                    )
                self.assertFalse(path.exists())
                self.assertEqual(list(Path(directory).iterdir()), [])

                self.assertEqual(
                    self.run_check(path),
                    (0, "State check passed.\n", ""),
                )

    def test_no_clobber_publication_preserves_a_competing_invalid_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            real_publish = sqlite.publish_staged_state

            def publish_after_competitor(staged: Path, target: Path) -> bool:
                target.write_bytes(b"competing-state")
                return real_publish(staged, target)

            with patch(
                "mechanical_librarian.bootstrap.publish_staged_state",
                side_effect=publish_after_competitor,
            ):
                self.assertEqual(
                    self.run_check(path),
                    (1, "", "State check failed: state could not be verified.\n"),
                )

            self.assertEqual(path.read_bytes(), b"competing-state")
            self.assertEqual(
                {candidate.name for candidate in Path(directory).iterdir()},
                {path.name},
            )

    def test_unsupported_publication_fails_without_exposing_partial_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            with patch.object(sqlite.os, "link", side_effect=OSError("unsupported")):
                self.assertEqual(
                    self.run_check(path),
                    (1, "", "State check failed: state could not be verified.\n"),
                )

            self.assertFalse(path.exists())
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_concurrent_first_initializers_share_one_published_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            barrier = threading.Barrier(2)
            real_publish = sqlite.publish_staged_state

            def synchronized_publish(staged: Path, target: Path) -> bool:
                barrier.wait(timeout=5)
                return real_publish(staged, target)

            with (
                patch(
                    "mechanical_librarian.bootstrap.publish_staged_state",
                    side_effect=synchronized_publish,
                ),
                ThreadPoolExecutor(max_workers=2) as executor,
            ):
                results = tuple(executor.map(_run_state_check, (path, path)))

            self.assertTrue(all(result.succeeded for result in results))
            connection = sqlite3.connect(path)
            try:
                self.assertEqual(
                    connection.execute("SELECT count(*) FROM audit_events").fetchone(),
                    (1,),
                )
            finally:
                connection.close()
            self.assertEqual(
                {candidate.name for candidate in Path(directory).iterdir()},
                {path.name},
            )

    def test_existing_upgrade_failure_rolls_back_and_can_retry(self) -> None:
        failed_upgrade = Migration(
            2,
            (
                "CREATE TABLE retry_state (id INTEGER PRIMARY KEY)",
                "CREATE TABLE",
            ),
            frozenset({"retry_state"}),
        )
        successful_upgrade = Migration(
            2,
            ("CREATE TABLE retry_state (id INTEGER PRIMARY KEY)",),
            frozenset({"retry_state"}),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            self.assertEqual(self.run_check(path)[0], 0)

            with patch.object(sqlite, "MIGRATIONS", (*sqlite.MIGRATIONS, failed_upgrade)):
                self.assertEqual(self.run_check(path)[0], 1)
            connection = sqlite3.connect(path)
            try:
                self.assertIsNone(
                    connection.execute(
                        "SELECT name FROM sqlite_master WHERE name = 'retry_state'"
                    ).fetchone()
                )
            finally:
                connection.close()

            with patch.object(
                sqlite,
                "MIGRATIONS",
                (*sqlite.MIGRATIONS, successful_upgrade),
            ):
                self.assertEqual(self.run_check(path)[0], 0)

    def test_dangling_symlink_is_rejected_without_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            destination = Path(directory) / "missing.sqlite"
            path.symlink_to(destination)

            self.assertEqual(self.run_check(path)[0], 1)
            self.assertTrue(path.is_symlink())
            self.assertEqual(path.readlink(), destination)
            self.assertFalse(destination.exists())

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
