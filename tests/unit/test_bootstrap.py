from __future__ import annotations

import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import mechanical_librarian.bootstrap as bootstrap
from mechanical_librarian.application.connection_probe import (
    ConnectionProbeOutcome,
    ConnectionProbeResult,
)
from mechanical_librarian.application.inventory_reconciliation import InventoryOutcome
from mechanical_librarian.application.inventory_polling import InventoryPollOutcome
from mechanical_librarian.domain.identifiers import (
    AdapterId,
    ConnectionId,
    LibraryId,
)
from mechanical_librarian.domain.inventory import (
    AdapterCapabilities,
    LibraryConnection,
    MediaType,
    SecretReference,
    ServiceEndpoint,
)
from mechanical_librarian.domain.inventory_v2 import ScanBounds
from mechanical_librarian.infrastructure.persistence.sqlite import SchemaError
from mechanical_librarian.infrastructure.persistence.sqlite import migrate

main = bootstrap.main


class BootstrapTests(unittest.TestCase):
    @staticmethod
    def inventory_fixture():
        library = LibraryId("01234567-89ab-4def-8123-456789abcdef")
        endpoint = ServiceEndpoint("https://example.invalid")
        secret = SecretReference("/tmp/synthetic-secret")
        configured = SimpleNamespace(
            endpoint=endpoint,
            secret=secret,
            adapter="audiobookshelf",
            connection_id=ConnectionId("primary"),
            enabled_libraries=(library.value,),
        )
        connection = LibraryConnection(
            configured.connection_id,
            AdapterId("audiobookshelf"),
            endpoint,
            secret,
            AdapterCapabilities(
                inventory_read=True,
                metadata_read=True,
                media_file_read=False,
                notifications=False,
                pagination=True,
                concurrency_tokens=False,
                cover_read=True,
                writable_fields=frozenset(),
                cover_write=False,
                supported_media_types=frozenset({MediaType.AUDIOBOOK, MediaType.EBOOK}),
            ),
        )
        configuration = SimpleNamespace(
            inventory=SimpleNamespace(
                poll_generation=0,
                max_attempts=3,
                max_elapsed_seconds=900,
                initial_backoff_seconds=5,
                max_backoff_seconds=300,
                connect_timeout_seconds=10,
                read_timeout_seconds=30,
                max_in_flight_requests=1,
            )
        )
        return (
            configuration,
            configured,
            connection,
            (library,),
            ScanBounds(2, 4, 10, 1_000_000, 300, 0),
        )

    def test_inventory_database_failure_precedes_target_probe(self) -> None:
        with (
            patch.object(
                bootstrap,
                "_inventory_connection",
                return_value=self.inventory_fixture(),
            ),
            patch.object(bootstrap, "preflight", side_effect=SchemaError("synthetic")),
            patch.object(bootstrap, "_probe_configured_connection") as probe,
        ):
            result = bootstrap._run_audiobookshelf_inventory(
                Path("configuration.json"), Path("state.sqlite")
            )

        self.assertEqual(result.outcome, InventoryOutcome.INVALID_REQUEST)
        probe.assert_not_called()

    def test_inventory_probe_runs_after_durable_begin_and_terminalizes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = sqlite3.connect(Path(directory) / "state.sqlite")
            database.execute("PRAGMA foreign_keys=ON")
            migrate(database)

            def probe_after_begin(*_args, **_kwargs):
                self.assertEqual(
                    database.execute(
                        "SELECT COUNT(*) FROM inventory_scans WHERE state='running'"
                    ).fetchone(),
                    (1,),
                )
                return ConnectionProbeResult(
                    ConnectionProbeOutcome.AUTHENTICATION_FAILED
                )

            try:
                with (
                    patch.object(
                        bootstrap,
                        "_inventory_connection",
                        return_value=self.inventory_fixture(),
                    ),
                    patch.object(bootstrap, "preflight"),
                    patch.object(bootstrap, "path_entry_exists", return_value=True),
                    patch.object(
                        bootstrap,
                        "_run_existing_operation",
                        side_effect=lambda _path, operation: operation(database),
                    ),
                    patch.object(
                        bootstrap,
                        "_probe_configured_connection",
                        side_effect=probe_after_begin,
                    ),
                ):
                    result = bootstrap._run_audiobookshelf_inventory(
                        Path("configuration.json"), Path("state.sqlite")
                    )
            finally:
                database.close()

        self.assertEqual(result.outcome, InventoryOutcome.INCOMPLETE)
        self.assertEqual(result.libraries[0].inspection.failure_kind, "authentication")

    def test_poll_intent_and_claim_are_durable_before_target_probe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = sqlite3.connect(Path(directory) / "state.sqlite")
            database.execute("PRAGMA foreign_keys=ON")
            migrate(database)

            def probe_after_claim(*_args, **_kwargs):
                self.assertEqual(
                    database.execute(
                        "SELECT state,attempt,fence FROM inventory_polls"
                    ).fetchone(),
                    ("running", 1, 1),
                )
                self.assertEqual(
                    database.execute(
                        "SELECT COUNT(*) FROM inventory_scan_poll_claims"
                    ).fetchone(),
                    (1,),
                )
                return ConnectionProbeResult(
                    ConnectionProbeOutcome.AUTHENTICATION_FAILED
                )

            try:
                with (
                    patch.object(
                        bootstrap,
                        "_inventory_connection",
                        return_value=self.inventory_fixture(),
                    ),
                    patch.object(bootstrap, "preflight"),
                    patch.object(bootstrap, "path_entry_exists", return_value=True),
                    patch.object(
                        bootstrap,
                        "_run_existing_operation",
                        side_effect=lambda _path, operation: operation(database),
                    ),
                    patch.object(
                        bootstrap,
                        "_probe_configured_connection",
                        side_effect=probe_after_claim,
                    ),
                ):
                    result = bootstrap._run_audiobookshelf_poll(
                        Path("configuration.json"),
                        Path("state.sqlite"),
                        "test-trigger",
                    )
                operational = repr(
                    database.execute(
                        "SELECT * FROM inventory_polls"
                    ).fetchall()
                    + database.execute(
                        "SELECT * FROM inventory_poll_attempts"
                    ).fetchall()
                    + database.execute(
                        "SELECT * FROM inventory_scan_poll_claims"
                    ).fetchall()
                )
            finally:
                database.close()

        self.assertEqual(result.outcome, InventoryPollOutcome.BLOCKED)
        self.assertEqual(result.inspection.state.value, "blocked")
        self.assertEqual(result.inspection.compatibility, "unknown")
        self.assertNotIn("example.invalid", operational)
        self.assertNotIn("synthetic-secret", operational)
        self.assertNotIn("01234567-89ab", operational)

    def test_composed_job_heartbeat_is_restart_safe_and_path_free(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hostile-sentinel.sqlite"
            outputs: list[tuple[int, str, str]] = []
            for _ in range(2):
                output = io.StringIO()
                errors = io.StringIO()
                with redirect_stdout(output), redirect_stderr(errors):
                    exit_code = main(["job-heartbeat", "--database", str(path)])
                outputs.append((exit_code, output.getvalue(), errors.getvalue()))

        for exit_code, output, errors in outputs:
            self.assertEqual(exit_code, 0)
            self.assertIn("Job heartbeat passed.", output)
            self.assertIn("authority=synthetic-report-only", output)
            self.assertNotIn("hostile-sentinel", output)
            self.assertEqual(errors, "")

    @patch("socket.socket", side_effect=AssertionError("network is forbidden"))
    def test_job_heartbeat_constructs_no_network_authority(self, _socket: Mock) -> None:
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
