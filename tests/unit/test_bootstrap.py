from __future__ import annotations

import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from mechanical_librarian.bootstrap import main
from mechanical_librarian.infrastructure.persistence.sqlite import SchemaError


class BootstrapTests(unittest.TestCase):
    def test_composed_job_heartbeat_is_restart_safe_and_path_free(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hostile-sentinel.sqlite"
            outputs: list[tuple[int, str, str]] = []
            for _ in range(2):
                output = io.StringIO()
                errors = io.StringIO()
                with redirect_stdout(output), redirect_stderr(errors):
                    exit_code = main(
                        ["job-heartbeat", "--database", str(path)]
                    )
                outputs.append((exit_code, output.getvalue(), errors.getvalue()))

        for exit_code, output, errors in outputs:
            self.assertEqual(exit_code, 0)
            self.assertIn("Job heartbeat passed.", output)
            self.assertIn("authority=synthetic-report-only", output)
            self.assertNotIn("hostile-sentinel", output)
            self.assertEqual(errors, "")

    @patch("socket.socket", side_effect=AssertionError("network is forbidden"))
    def test_job_heartbeat_constructs_no_network_authority(
        self, _socket: Mock
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            errors = io.StringIO()
            with redirect_stdout(output), redirect_stderr(errors):
                exit_code = main(
                    [
                        "job-heartbeat",
                        "--database",
                        str(Path(directory) / "state.sqlite"),
                    ]
                )
        self.assertEqual(exit_code, 0)
        self.assertEqual(errors.getvalue(), "")
        self.assertIn("synthetic-report-only", output.getvalue())

    def run_check(self) -> tuple[int, str, str]:
        output = io.StringIO()
        errors = io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            exit_code = main(
                ["state-check", "--database", "/private/hostile-sentinel.sqlite"]
            )
        return exit_code, output.getvalue(), errors.getvalue()

    @patch("mechanical_librarian.bootstrap.preflight")
    @patch("mechanical_librarian.bootstrap.path_entry_exists", return_value=True)
    @patch("mechanical_librarian.bootstrap.connect_existing")
    def test_connection_failure_maps_to_fixed_path_free_result(
        self, mocked_connect: Mock, _mocked_exists: Mock, _mocked_preflight: Mock
    ) -> None:
        mocked_connect.side_effect = SchemaError(
            "/private/hostile-sentinel.sqlite token=private"
        )

        self.assertEqual(
            self.run_check(),
            (1, "", "State check failed: state could not be verified.\n"),
        )

    @patch("mechanical_librarian.bootstrap.migrate")
    @patch("mechanical_librarian.bootstrap.preflight")
    @patch("mechanical_librarian.bootstrap.path_entry_exists", return_value=True)
    @patch("mechanical_librarian.bootstrap.connect_existing")
    def test_schema_failure_closes_connection_in_finally(
        self,
        mocked_connect: Mock,
        _mocked_exists: Mock,
        _mocked_preflight: Mock,
        mocked_migrate: Mock,
    ) -> None:
        connection = Mock()
        mocked_connect.return_value = connection
        mocked_migrate.side_effect = SchemaError("hostile-sentinel")

        self.assertEqual(self.run_check()[0], 1)
        connection.close.assert_called_once_with()

    @patch("mechanical_librarian.bootstrap.migrate", return_value=1)
    @patch("mechanical_librarian.bootstrap.preflight")
    @patch("mechanical_librarian.bootstrap.path_entry_exists", return_value=True)
    @patch("mechanical_librarian.bootstrap.connect_existing")
    def test_locked_append_is_typed_unavailable_and_connection_is_closed(
        self,
        mocked_connect: Mock,
        _mocked_exists: Mock,
        _mocked_preflight: Mock,
        _mocked_migrate: Mock,
    ) -> None:
        connection = Mock()
        connection.execute.side_effect = sqlite3.OperationalError("database is locked")
        mocked_connect.return_value = connection

        self.assertEqual(
            self.run_check(),
            (1, "", "State check failed: state is unavailable.\n"),
        )
        connection.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
