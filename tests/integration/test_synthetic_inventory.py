from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import mechanical_librarian.bootstrap as bootstrap


ROOT = Path(__file__).parents[2]
CONFIGURATION = ROOT / "docs" / "examples" / "synthetic-config.json"


class ComposedSyntheticInventoryTests(unittest.TestCase):
    def run_inventory(self, configuration: Path, database: Path) -> tuple[int, str, str]:
        output = io.StringIO()
        errors = io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            exit_code = bootstrap.main(
                [
                    "synthetic-inventory",
                    "--config",
                    str(configuration),
                    "--database",
                    str(database),
                ]
            )
        return exit_code, output.getvalue(), errors.getvalue()

    def test_first_duplicate_and_process_style_restart_keep_one_history_and_current(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "state.sqlite"
            first = self.run_inventory(CONFIGURATION, database)
            second = self.run_inventory(CONFIGURATION, database)
            third = self.run_inventory(CONFIGURATION, database)

            self.assertEqual(first[0], 0, first[2])
            self.assertEqual(second[0], 0, second[2])
            self.assertEqual(third[0], 0, third[2])
            self.assertIn("persistence=accepted", first[1])
            for result in (first, second, third):
                self.assertIn("authority=synthetic-inventory-read-only", result[1])
                self.assertIn("items=1 history=1 current=1", result[1])
                self.assertNotIn(str(database), result[1] + result[2])
                self.assertEqual(result[2], "")
            self.assertIn("persistence=reused", second[1])
            self.assertIn("persistence=reused", third[1])

            connection = sqlite3.connect(database)
            self.addCleanup(connection.close)
            self.assertEqual(
                connection.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall(),
                [(1,), (2,), (3,)],
            )
            self.assertEqual(
                connection.execute("SELECT count(*) FROM inventory_snapshots").fetchone(),
                (1,),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM current_inventory_observations"
                ).fetchone(),
                (1,),
            )
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(inventory_snapshots)")
            }
            self.assertFalse(
                columns
                & {"vendor", "audiobookshelf", "endpoint", "secret", "path", "title"}
            )
            persisted = repr(
                connection.execute(
                    "SELECT external_item, canonical_input FROM inventory_snapshots"
                ).fetchall()
            )
            for private in (
                "synthetic-item-one",
                "synthetic.invalid",
                "unused-synthetic-reference",
            ):
                self.assertNotIn(private, persisted)

    @patch("mechanical_librarian.bootstrap.SQLiteInventoryRepository")
    @patch("mechanical_librarian.bootstrap.SyntheticLibraryReader")
    @patch("mechanical_librarian.bootstrap.preflight")
    def test_invalid_or_write_capable_configuration_fails_before_any_construction(
        self, mocked_preflight: Mock, mocked_reader: Mock, mocked_repository: Mock
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = json.loads(CONFIGURATION.read_text(encoding="utf-8"))
            source["connections"][0]["capability_overrides"] = ["metadata-write"]
            source["connections"][0]["endpoint"] = "https://private-sentinel.invalid"
            source["connections"][0]["secret_reference"] = "private-token-sentinel"
            configuration = Path(directory) / "private-config.json"
            configuration.write_text(json.dumps(source), encoding="utf-8")
            result = self.run_inventory(configuration, Path(directory) / "state.sqlite")

        self.assertEqual(
            result,
            (
                1,
                "",
                "Synthetic inventory failed: bounded report-only inventory could not be verified.\n",
            ),
        )
        mocked_preflight.assert_not_called()
        mocked_reader.assert_not_called()
        mocked_repository.assert_not_called()
        self.assertNotIn("private", result[2])
        self.assertNotIn("token", result[2])

    @patch("socket.socket", side_effect=AssertionError("network is forbidden"))
    def test_supported_composition_constructs_no_network_or_mutation_authority(
        self, _socket: Mock
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_inventory(
                CONFIGURATION, Path(directory) / "state.sqlite"
            )
        self.assertEqual(result[0], 0, result[2])
        module_names = set(vars(bootstrap))
        for forbidden in (
            "SupportedWriter",
            "WriteGrant",
            "SecretResolver",
            "WorkScheduler",
            "MetadataProvider",
        ):
            self.assertNotIn(forbidden, module_names)

    def test_incompatible_or_tampered_schema_is_fixed_and_path_free(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "private-sentinel.sqlite"
            self.assertEqual(self.run_inventory(CONFIGURATION, database)[0], 0)
            connection = sqlite3.connect(database)
            connection.execute(
                "UPDATE schema_migrations SET checksum = 'tampered' WHERE version = 3"
            )
            connection.commit()
            connection.close()
            result = self.run_inventory(CONFIGURATION, database)

        self.assertEqual(result[0], 1)
        self.assertEqual(result[1], "")
        self.assertNotIn("private-sentinel", result[2])
        self.assertEqual(
            result[2],
            "Synthetic inventory failed: bounded report-only inventory could not be verified.\n",
        )


if __name__ == "__main__":
    unittest.main()
