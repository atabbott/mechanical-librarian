from __future__ import annotations

import copy
import hashlib
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

from mechanical_librarian.bootstrap.composition import (
    _run_hardcover_safe_executor_preview,
)
from mechanical_librarian.infrastructure.hardcover_identity_gate import LOCK_NAME
from mechanical_librarian.interfaces.cli import main


ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests/fixtures/hardcover_identity"


class _AuthorityTrap(AssertionError):
    pass


class HardcoverSafeExecutorCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.root.chmod(0o700)
        self.database = self.root / "state.sqlite"
        self.source = json.loads(
            (FIXTURES / "c5-safe-executor-input.json").read_text(encoding="utf-8")
        )

    def argv(self, database: Path | None = None) -> list[str]:
        values = [
            "hardcover-identity-report",
            "--mode",
            "synthetic-preview",
            "--scenario",
            "safe-executor-v3",
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
        runner=_run_hardcover_safe_executor_preview,
    ) -> tuple[int, str, str]:
        output = io.StringIO()
        errors = io.StringIO()
        code = main(
            self.argv(self.database if database is None else database),
            state_check_runner=Mock(),
            hardcover_safe_executor_preview_runner=runner,
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

    def test_canonical_preview_matches_oracle_without_egress_or_fixture_reads(self) -> None:
        with self.authority_traps(), patch.object(
            Path, "read_bytes", side_effect=_AuthorityTrap("fixture read")
        ), patch.object(
            Path, "read_text", side_effect=_AuthorityTrap("fixture read")
        ):
            code, output, errors = self.run_preview()
        self.assertEqual((code, errors), (0, ""))
        self.assertEqual(
            output,
            (FIXTURES / "c5-safe-executor-example-output.json").read_text(
                encoding="utf-8"
            ),
        )
        self.assertEqual(stat.S_IMODE(self.database.stat().st_mode), 0o600)
        lock = self.root / LOCK_NAME
        self.assertTrue(lock.is_file())
        self.assertEqual(stat.S_IMODE(lock.stat().st_mode), 0o600)
        self.assertEqual(lock.stat().st_nlink, 1)
        self.assertNotIn("isbn", output.casefold())
        self.assertNotIn("database", output.casefold())
        persisted = self.database.read_bytes()
        for private in (
            b"9780000000019",
            b"0000000019",
            b"Synthetic Ebook Alpha",
            json.dumps(self.source, sort_keys=True).encode(),
            b"retry_then_match",
            b"worker-a",
        ):
            self.assertNotIn(private, persisted)

    def test_input_mutations_fail_before_database_or_gate_creation(self) -> None:
        variants: list[object] = []
        for key in ("schema", "scenario", "events"):
            missing = copy.deepcopy(self.source)
            del missing[key]
            variants.append(missing)
        extra = copy.deepcopy(self.source)
        extra["extra"] = True
        variants.append(extra)
        for index, (event_index, key, value) in enumerate(
            (
                (0, "mono_ns", 1),
                (1, "event", "gate_release"),
                (2, "fence", 2),
                (3, "candidate", 2),
                (20, "signal", "SIGTERM"),
                (34, "result", "acquired"),
                (48, "case", "renamed"),
            )
        ):
            changed = copy.deepcopy(self.source)
            changed["events"][event_index][key] = value
            variants.append(changed)
        reordered = copy.deepcopy(self.source)
        reordered["events"][1], reordered["events"][2] = (
            reordered["events"][2],
            reordered["events"][1],
        )
        variants.append(reordered)

        for index, variant in enumerate(variants):
            with self.subTest(index=index):
                database = self.root / f"invalid-{index}.sqlite"
                code, output, errors = self.run_preview(
                    variant, database=database
                )
                self.assertEqual((code, output), (2, ""))
                self.assertEqual(
                    errors, "Hardcover identity report failed safely.\n"
                )
                self.assertFalse(os.path.lexists(database))

    def test_database_must_be_absent_absolute_and_under_empty_private_root(self) -> None:
        runner = Mock(side_effect=_AuthorityTrap("runner reached"))
        relative = Path("relative.sqlite")
        with tempfile.TemporaryDirectory() as permissive_name:
            permissive = Path(permissive_name)
            permissive.chmod(0o755)
            for database in (relative, permissive / "state.sqlite"):
                with self.subTest(database=database):
                    code, output, errors = self.run_preview(
                        database=database, runner=runner
                    )
                    self.assertEqual((code, output), (2, ""))
                    self.assertEqual(
                        errors, "Hardcover identity report failed safely.\n"
                    )
        runner.assert_not_called()

    def test_existing_state_is_rejected_without_modification(self) -> None:
        self.database.write_bytes(b"preserve")
        before = self.database.read_bytes()
        code, output, errors = self.run_preview()
        self.assertEqual((code, output), (2, ""))
        self.assertEqual(errors, "Hardcover identity report failed safely.\n")
        self.assertEqual(self.database.read_bytes(), before)

    def test_c5_registry_is_cumulative_and_binds_exact_resources(self) -> None:
        registry_path = FIXTURES / "preview-registry-c5-v6.json"
        payload = registry_path.read_bytes()
        self.assertEqual(
            hashlib.sha256(payload).hexdigest(),
            "4f3233ce2ee822367536cb3ac05694769f67ea4911e749cac917b5a3e920ad35",
        )
        registry = json.loads(payload)
        self.assertEqual(
            registry["predecessor_registry_sha256"],
            "3e5183fb4ddff6674717f870c99b82ecb24eb2d232d759cf08957d2506d93e81",
        )
        self.assertEqual(
            [row["scenario"] for row in registry["scenarios"]],
            [
                "inert-report-v3",
                "identity-decision-v3",
                "fixed-adapter-v3",
                "durable-work-v3",
                "safe-executor-v3",
            ],
        )
        for scenario in registry["scenarios"]:
            for binding in scenario["resources"].values():
                resource = FIXTURES / binding["path"]
                data = resource.read_bytes()
                self.assertEqual(len(data), binding["bytes"])
                self.assertEqual(hashlib.sha256(data).hexdigest(), binding["sha256"])


if __name__ == "__main__":
    unittest.main()
