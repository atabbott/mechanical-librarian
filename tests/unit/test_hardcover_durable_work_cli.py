from __future__ import annotations

import copy
import io
import json
import os
import socket
import stat
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

from mechanical_librarian.application.hardcover_identity import (
    DurableWorkPreviewReport,
)
from mechanical_librarian.bootstrap.composition import (
    _run_hardcover_durable_work_preview,
)
from mechanical_librarian.interfaces.cli import main


ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests/fixtures/hardcover_identity"


class _AuthorityTrap(AssertionError):
    pass


class HardcoverDurableWorkCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / "state.sqlite"
        self.source = json.loads(
            (FIXTURES / "c4-durable-work-input.json").read_text(encoding="utf-8")
        )

    def argv(self, database: Path | None = None) -> list[str]:
        values = [
            "hardcover-identity-report",
            "--mode",
            "synthetic-preview",
            "--scenario",
            "durable-work-v3",
            "--input",
            "-",
        ]
        if database is not None:
            values.extend(("--database", os.fspath(database)))
        values.extend(("--format", "json"))
        return values

    def run_preview(
        self,
        source: object | None = None,
        *,
        database: Path | None = None,
        runner=_run_hardcover_durable_work_preview,
    ) -> tuple[int, str, str]:
        output = io.StringIO()
        errors = io.StringIO()
        code = main(
            self.argv(self.database if database is None else database),
            state_check_runner=Mock(),
            hardcover_durable_work_preview_runner=runner,
            stdin=io.StringIO(json.dumps(self.source if source is None else source)),
            stdout=output,
            stderr=errors,
        )
        return code, output.getvalue(), errors.getvalue()

    def authority_traps(self) -> ExitStack:
        stack = ExitStack()
        for target in (
            "socket.socket",
            "socket.create_connection",
            "socket.getaddrinfo",
            "httpx.Client",
            "httpx.AsyncClient",
            "mechanical_librarian.infrastructure.security.provider_secrets.PrivateFileProviderCredentials",
            "mechanical_librarian.infrastructure.security.provider_secrets.read_private_secret",
        ):
            stack.enter_context(
                patch(target, side_effect=_AuthorityTrap("authority reached"))
            )
        return stack

    def test_canonical_preview_matches_oracle_and_publishes_owner_only_state(self) -> None:
        with self.authority_traps(), patch.object(
            Path, "read_bytes", side_effect=_AuthorityTrap("fixture read")
        ), patch.object(
            Path, "read_text", side_effect=_AuthorityTrap("fixture read")
        ):
            code, output, errors = self.run_preview()
        self.assertEqual((code, errors), (0, ""))
        self.assertEqual(
            output,
            (FIXTURES / "c4-durable-work-example-output.json").read_text(
                encoding="utf-8"
            ),
        )
        self.assertTrue(self.database.is_file())
        self.assertEqual(stat.S_IMODE(self.database.stat().st_mode), 0o600)
        self.assertNotIn("sha", output.casefold())
        self.assertNotIn("database", output.casefold())

    def test_canonical_preview_persists_only_bounded_public_structural_values(self) -> None:
        code, output, errors = self.run_preview()
        self.assertEqual((code, errors), (0, ""))
        raw = self.database.read_bytes()
        serialized_input = json.dumps(self.source, sort_keys=True).encode()
        self.assertNotIn(serialized_input, raw)
        for forbidden in (
            b"hardcover.app",
            b"ISBN",
            b"credential",
            b"failure-before-commit",
            b"restart_after_start",
        ):
            self.assertNotIn(forbidden, raw)
        result = json.loads(output)["result"]
        self.assertEqual(set(result), {
            "interrupted_recovery_gate_owned",
            "migration_idempotence_verified",
            "migration_rollback_verified",
            "outcome",
            "publication_rollback_verified",
            "schema_from",
            "schema_to",
        })

    def test_every_single_field_or_shape_change_fails_before_database_creation(self) -> None:
        variants: list[object] = []
        for key in ("schema", "scenario", "failure_points", "script"):
            missing = copy.deepcopy(self.source)
            del missing[key]
            variants.append(missing)
        extra = copy.deepcopy(self.source)
        extra["extra"] = True
        variants.append(extra)
        changed_schema = copy.deepcopy(self.source)
        changed_schema["schema"] = "hardcover-synthetic-input-v2"
        variants.append(changed_schema)
        changed_failure = copy.deepcopy(self.source)
        changed_failure["failure_points"].reverse()
        variants.append(changed_failure)
        changed_operation = copy.deepcopy(self.source)
        changed_operation["script"][6]["fence"] = 2
        variants.append(changed_operation)
        extra_operation_key = copy.deepcopy(self.source)
        extra_operation_key["script"][0]["extra"] = 1
        variants.append(extra_operation_key)
        truncated = copy.deepcopy(self.source)
        truncated["script"].pop()
        variants.append(truncated)

        for index, variant in enumerate(variants):
            with self.subTest(index=index):
                database = Path(self.directory.name) / f"invalid-{index}.sqlite"
                code, output, errors = self.run_preview(variant, database=database)
                self.assertEqual((code, output), (2, ""))
                self.assertEqual(errors, "Hardcover identity report failed safely.\n")
                self.assertFalse(os.path.lexists(database))

    def test_existing_database_is_rejected_without_modification(self) -> None:
        self.database.write_bytes(b"preserve-existing-state")
        before = self.database.read_bytes()
        code, output, errors = self.run_preview()
        self.assertEqual((code, output), (2, ""))
        self.assertEqual(errors, "Hardcover identity report failed safely.\n")
        self.assertEqual(self.database.read_bytes(), before)

    def test_database_path_must_be_absolute_absent_and_under_empty_owner_only_parent(self) -> None:
        runner = Mock(side_effect=_AuthorityTrap("runner reached"))
        relative = Path("relative-preview.sqlite")
        with tempfile.TemporaryDirectory() as permissive_name, tempfile.TemporaryDirectory() as nonempty_name:
            permissive = Path(permissive_name)
            permissive.chmod(0o755)
            nonempty = Path(nonempty_name)
            (nonempty / "marker").write_text("public", encoding="utf-8")
            for database in (
                relative,
                permissive / "state.sqlite",
                nonempty / "state.sqlite",
            ):
                with self.subTest(database=database):
                    code, output, errors = self.run_preview(
                        database=database, runner=runner
                    )
                    self.assertEqual((code, output), (2, ""))
                    self.assertEqual(
                        errors, "Hardcover identity report failed safely.\n"
                    )
                    self.assertFalse(os.path.lexists(database))
        runner.assert_not_called()

    def test_schema_eight_logical_snapshot_mismatch_fails_before_publication(self) -> None:
        with patch(
            "mechanical_librarian.bootstrap.composition._SCHEMA8_SNAPSHOT_SHA256",
            "0" * 64,
        ):
            code, output, errors = self.run_preview()
        self.assertEqual((code, output), (2, ""))
        self.assertEqual(errors, "Hardcover identity report failed safely.\n")
        self.assertFalse(os.path.lexists(self.database))

    def test_durable_scenario_requires_database_and_other_scenarios_reject_it(self) -> None:
        output = io.StringIO()
        errors = io.StringIO()
        code = main(
            self.argv(None),
            state_check_runner=Mock(),
            hardcover_durable_work_preview_runner=_run_hardcover_durable_work_preview,
            stdin=io.StringIO(json.dumps(self.source)),
            stdout=output,
            stderr=errors,
        )
        self.assertEqual((code, output.getvalue()), (2, ""))
        self.assertEqual(errors.getvalue(), "Hardcover identity report failed safely.\n")

        other = self.argv(self.database)
        other[other.index("durable-work-v3")] = "fixed-adapter-v3"
        output = io.StringIO()
        errors = io.StringIO()
        code = main(
            other,
            state_check_runner=Mock(),
            stdin=io.StringIO("{}"),
            stdout=output,
            stderr=errors,
        )
        self.assertEqual((code, output.getvalue()), (2, ""))
        self.assertEqual(errors.getvalue(), "Hardcover identity report failed safely.\n")

    def test_runner_failure_and_wrong_report_are_value_free(self) -> None:
        def failed(source, database):
            raise RuntimeError("private failure value")

        for runner in (failed, lambda source, database: object()):
            with self.subTest(runner=runner):
                code, output, errors = self.run_preview(runner=runner)
                self.assertEqual((code, output), (2, ""))
                self.assertEqual(errors, "Hardcover identity report failed safely.\n")
                self.assertNotIn("private", errors)

    def test_report_validation_is_closed(self) -> None:
        valid = DurableWorkPreviewReport(
            "stale", 8, 9, True, True, True, True
        )
        valid.validate()
        self.assertEqual(
            valid.to_json() + "\n",
            (FIXTURES / "c4-durable-work-example-output.json").read_text(
                encoding="utf-8"
            ),
        )
        invalid = DurableWorkPreviewReport(
            "stale", 8, 9, True, True, True, False
        )
        with self.assertRaisesRegex(TypeError, "malformed"):
            invalid.validate()

    def test_runtime_does_not_construct_network_clients(self) -> None:
        original_socket = socket.socket
        original_client = httpx.Client
        with self.authority_traps():
            code, _, errors = self.run_preview()
        self.assertEqual((code, errors), (0, ""))
        self.assertIs(socket.socket, original_socket)
        self.assertIs(httpx.Client, original_client)


if __name__ == "__main__":
    unittest.main()
