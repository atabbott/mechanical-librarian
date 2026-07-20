from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import signal
import socket
import stat
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

from mechanical_librarian.bootstrap.composition import (
    _run_hardcover_identity_lifecycle_preview,
    _temporary_identity_cancellation_signals,
)
from mechanical_librarian.interfaces.cli import main
from mechanical_librarian.infrastructure.persistence.sqlite import SchemaError


ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests/fixtures/hardcover_identity"


class _AuthorityTrap(AssertionError):
    pass


class _ProcessDeath(BaseException):
    pass


class HardcoverIdentityLifecycleCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.root.chmod(0o700)
        self.database = self.root / "state.sqlite"
        self.source = json.loads(
            (FIXTURES / "c6-lifecycle-orchestration-input.json").read_text(
                encoding="utf-8"
            )
        )

    def run_preview(
        self,
        source: object | None = None,
        *,
        database: Path | None = None,
    ) -> tuple[int, str, str]:
        output = io.StringIO()
        errors = io.StringIO()
        selected = self.database if database is None else database
        code = main(
            [
                "hardcover-identity-report",
                "--mode",
                "synthetic-preview",
                "--scenario",
                "lifecycle-orchestration-v3",
                "--input",
                "-",
                "--database",
                os.fspath(selected),
                "--format",
                "json",
            ],
            state_check_runner=Mock(),
            hardcover_identity_lifecycle_preview_runner=(
                _run_hardcover_identity_lifecycle_preview
            ),
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

    def test_canonical_installed_seam_matches_oracle_without_egress(self) -> None:
        previous = {
            selected: signal.getsignal(selected)
            for selected in (signal.SIGINT, signal.SIGTERM)
        }
        with self.authority_traps(), patch.object(
            Path, "read_bytes", side_effect=_AuthorityTrap("fixture read")
        ), patch.object(
            Path, "read_text", side_effect=_AuthorityTrap("fixture read")
        ):
            code, output, errors = self.run_preview()

        self.assertEqual((code, errors), (0, ""))
        self.assertEqual(
            output,
            (
                FIXTURES / "c6-lifecycle-orchestration-example-output.json"
            ).read_text(encoding="utf-8"),
        )
        self.assertTrue(
            all(signal.getsignal(selected) is previous[selected] for selected in previous)
        )
        self.assertEqual(stat.S_IMODE(self.database.stat().st_mode), 0o600)
        self.assertNotIn("isbn", output.casefold())
        persisted = b"".join(path.read_bytes() for path in self.root.iterdir())
        for forbidden in (
            b"9780000000019",
            b"0000000019",
            b"Synthetic Ebook Alpha",
            *(case["label"].encode("ascii") for case in self.source["cases"]),
            *(case["process"].encode("ascii") for case in self.source["cases"]),
        ):
            self.assertNotIn(forbidden, persisted)
        self.assertIn(b"Remote Volume Zeta", persisted)

    def test_alternate_order_labels_results_and_delay_are_semantic(self) -> None:
        alternate = copy.deepcopy(self.source)
        alternate["cases"].reverse()
        for index, case in enumerate(alternate["cases"]):
            case["label"] = f"alternate-{index}"
            case["process"] = f"participant-{index}"
        retry_match = next(
            case for case in alternate["cases"] if case["operation"] == "retry_match"
        )
        retry_match["results"] = [
            {"outcome": "timeout", "retry_after_seconds": None},
            {"outcome": "matched", "retry_after_seconds": None},
        ]
        exhausted = next(
            case
            for case in alternate["cases"]
            if case["operation"] == "retry_exhausted"
        )
        exhausted["results"][1] = {
            "outcome": "rate_limited",
            "retry_after_seconds": 3,
        }

        code, output, errors = self.run_preview(alternate)

        self.assertEqual((code, errors), (0, ""))
        rendered = json.loads(output)
        self.assertEqual(rendered["result"]["attempts_started"], 11)
        for key, value in rendered["result"].items():
            if key not in {"attempts_started"}:
                self.assertIn(value, {"all_cases_passed", 1, 6, True})

    def test_invalid_input_fails_before_disposable_state_creation(self) -> None:
        variants = []
        for key in ("schema", "scenario", "cases"):
            changed = copy.deepcopy(self.source)
            del changed[key]
            variants.append(changed)
        duplicate = copy.deepcopy(self.source)
        duplicate["cases"][0]["operation"] = duplicate["cases"][1]["operation"]
        variants.append(duplicate)
        invalid_retry = copy.deepcopy(self.source)
        invalid_retry["cases"][0]["results"][0]["retry_after_seconds"] = 1
        variants.append(invalid_retry)

        for index, variant in enumerate(variants):
            with self.subTest(index=index):
                database = self.root / f"invalid-{index}.sqlite"
                code, output, errors = self.run_preview(variant, database=database)
                self.assertEqual((code, output), (2, ""))
                self.assertEqual(
                    errors, "Hardcover identity report failed safely.\n"
                )
                self.assertFalse(os.path.lexists(database))

    def test_signal_scope_restores_handlers_and_preserves_control_flow(self) -> None:
        previous = {
            selected: signal.getsignal(selected)
            for selected in (signal.SIGINT, signal.SIGTERM)
        }
        with self.assertRaises(_ProcessDeath):
            with _temporary_identity_cancellation_signals(threading.Event()):
                raise _ProcessDeath()
        self.assertTrue(
            all(signal.getsignal(selected) is previous[selected] for selected in previous)
        )

    def test_signal_scope_rejects_non_main_thread_without_mutation(self) -> None:
        previous = {
            selected: signal.getsignal(selected)
            for selected in (signal.SIGINT, signal.SIGTERM)
        }
        failures: list[BaseException] = []

        def invoke() -> None:
            try:
                with _temporary_identity_cancellation_signals(threading.Event()):
                    pass
            except BaseException as failure:
                failures.append(failure)

        worker = threading.Thread(target=invoke)
        worker.start()
        worker.join()
        self.assertEqual(len(failures), 1)
        self.assertIs(type(failures[0]), SchemaError)
        self.assertEqual(
            str(failures[0]), "identity lifecycle signal handling is unavailable"
        )
        self.assertTrue(
            all(signal.getsignal(selected) is previous[selected] for selected in previous)
        )

    def test_c6_registry_extends_c5_and_binds_every_resource(self) -> None:
        registry_path = FIXTURES / "preview-registry-c6-v1.json"
        payload = registry_path.read_bytes()
        self.assertEqual(
            hashlib.sha256(payload).hexdigest(),
            "2f2076dab597501583607b0022add479c236568aa7961a27214a906c95c0d060",
        )
        registry = json.loads(payload)
        self.assertEqual(
            registry["predecessor_registry_sha256"],
            "bbaa89174d682be79e4e0b42f1fe0fc8a1643201bbc7b090bf926cba421e5c39",
        )
        self.assertEqual(
            [scenario["scenario"] for scenario in registry["scenarios"]][-2:],
            ["safe-executor-v3", "lifecycle-orchestration-v3"],
        )
        for binding_name in (
            "output_schema",
            "output_privacy_contract",
            "output_privacy_scan",
        ):
            binding = registry[binding_name]
            resource = FIXTURES / binding["path"]
            data = resource.read_bytes()
            self.assertEqual(len(data), binding["bytes"])
            self.assertEqual(hashlib.sha256(data).hexdigest(), binding["sha256"])
        lifecycle = registry["scenarios"][-1]
        for binding in lifecycle["resources"].values():
            data = (FIXTURES / binding["path"]).read_bytes()
            self.assertEqual(len(data), binding["bytes"])
            self.assertEqual(hashlib.sha256(data).hexdigest(), binding["sha256"])
        output_schema = json.loads(
            (FIXTURES / registry["output_schema"]["path"]).read_text("utf-8")
        )
        lifecycle_result = output_schema["$defs"]["identity_lifecycle_result"]
        self.assertFalse(lifecycle_result["additionalProperties"])
        self.assertEqual(
            set(lifecycle_result["required"]), set(lifecycle_result["properties"])
        )
        privacy = json.loads(
            (FIXTURES / registry["output_privacy_contract"]["path"]).read_text(
                "utf-8"
            )
        )
        self.assertEqual(
            set(privacy["allowed_runtime_results"]["lifecycle-orchestration-v3"]),
            set(lifecycle_result["properties"]),
        )


if __name__ == "__main__":
    unittest.main()
