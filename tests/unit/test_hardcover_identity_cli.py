from __future__ import annotations

import io
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from mechanical_librarian.application.hardcover_identity import (
    HardcoverIdentityOutcome,
    HardcoverIdentityReport,
)
from mechanical_librarian.bootstrap.composition import (
    _run_hardcover_identity_report,
    _run_hardcover_inert_preview,
)
from mechanical_librarian.interfaces.cli import main


ROOT = Path(__file__).parents[2]
EXAMPLE_CONFIG = ROOT / "docs" / "examples" / "synthetic-config.json"


class HardcoverIdentityCliTests(unittest.TestCase):
    def production_argv(self, config: Path) -> list[str]:
        return [
            "hardcover-identity-report",
            "--config",
            str(config),
            "--database",
            "/must-not-open/state.sqlite3",
            "--query-file",
            "/must-not-open/query.json",
            "--format",
            "json",
        ]

    def synthetic_input(self, *, title: str = "Synthetic Ebook Alpha") -> dict[str, object]:
        return {
            "schema": "hardcover-synthetic-input-v3",
            "scenario": "inert-report-v3",
            "config": {
                "enabled": False,
                "secret_reference": None,
                "authority_revision": 1,
            },
            "private_query": {"isbn": "9780000000019", "title": title},
        }

    def test_production_disabled_and_enabled_short_circuit_before_private_authority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            disabled = root / "disabled.json"
            source = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
            disabled.write_text(json.dumps(source), encoding="utf-8")
            enabled = root / "enabled.json"
            source["hardcover_provider"] = {
                "enabled": True,
                "secret_reference": "/must-not-open/hardcover-token",
                "authority_revision": 1,
            }
            enabled.write_text(json.dumps(source), encoding="utf-8")

            for config, outcome in (
                (disabled, "disabled"),
                (enabled, "provider_not_activated"),
            ):
                with self.subTest(outcome=outcome), patch(
                    "mechanical_librarian.infrastructure.security.private_query.read_private_query",
                    side_effect=AssertionError("private query opened"),
                ), patch(
                    "mechanical_librarian.infrastructure.security.provider_secrets.read_private_secret",
                    side_effect=AssertionError("secret opened"),
                ), patch(
                    "mechanical_librarian.infrastructure.persistence.sqlite.connect",
                    side_effect=AssertionError("database opened"),
                ):
                    output = io.StringIO()
                    errors = io.StringIO()
                    exit_code = main(
                        self.production_argv(config),
                        state_check_runner=Mock(),
                        hardcover_identity_runner=_run_hardcover_identity_report,
                        stdout=output,
                        stderr=errors,
                    )
                    self.assertEqual(exit_code, 0)
                    self.assertEqual(errors.getvalue(), "")
                    self.assertEqual(json.loads(output.getvalue())["outcome"], outcome)
                    self.assertNotIn("must-not-open", output.getvalue())

    def test_synthetic_preview_matches_c1_oracle_without_echo_or_stable_identifier(self) -> None:
        argv = [
            "hardcover-identity-report",
            "--mode",
            "synthetic-preview",
            "--scenario",
            "inert-report-v3",
            "--input",
            "-",
            "--format",
            "json",
        ]
        outputs: list[str] = []
        for title in ("Synthetic Ebook Alpha", "Different Private Title"):
            output = io.StringIO()
            errors = io.StringIO()
            source = self.synthetic_input(title=title)
            exit_code = main(
                argv,
                state_check_runner=Mock(),
                hardcover_inert_preview_runner=_run_hardcover_inert_preview,
                stdin=io.StringIO(json.dumps(source)),
                stdout=output,
                stderr=errors,
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(errors.getvalue(), "")
            self.assertNotIn(title, output.getvalue())
            self.assertNotIn(source["private_query"]["isbn"], output.getvalue())
            self.assertNotIn("sha", output.getvalue().casefold())
            outputs.append(output.getvalue())
        self.assertEqual(outputs[0], outputs[1])
        oracle = (
            ROOT
            / "tests/fixtures/hardcover_identity/c1-inert-report-example-output.json"
        )
        self.assertEqual(outputs[0], oracle.read_text(encoding="utf-8"))

    def test_c1_registry_is_exact_cumulative_public_evidence(self) -> None:
        fixture_root = ROOT / "tests/fixtures/hardcover_identity"
        expected = {
            "c1-inert-report-input.json": "f9896b56f48d99dd10be69556a95b24b4968beb07b3989517a399d981af66223",
            "c1-inert-report-input-schema.json": "0e275c72d40e2f7e2460d18ce572fea598d79aeff113252a135bc05e8285aa61",
            "c1-inert-report-example-output.json": "910bc6803bbee3c1a43dba545cd497e036ea1ac3f8c663057cb705a4a60e5479",
            "c1-inert-report-variant-matrix.json": "e18f2eb3dcd75c09cb57443da8e2bea666604813c94a922260de7e92b30b9bf4",
            "preview-output-schema-v5.json": "fd0f7d4e0248faa97ceddd6cbe442c23dfae4f48c8a02b47ee3df36bc7eedec7",
            "preview-output-privacy-contract-v2.json": "02a897d7f3c997cc1c99b5f3ad808f3e636f704305cefa13ea44c8c5fb0c18e3",
            "preview-output-privacy-scan-v2.json": "81aa8c3db583bdd326ccda572dcb510060bb1868e93b449737d71adef7bee7bc",
            "preview-registry-c1-v5.json": "9767fd07740724055d4e96c3461473b9f262fa68bf89076a5b7b68cc859f5598",
        }
        for name, digest in expected.items():
            with self.subTest(name=name):
                self.assertEqual(hashlib.sha256((fixture_root / name).read_bytes()).hexdigest(), digest)
        registry = json.loads((fixture_root / "preview-registry-c1-v5.json").read_text())
        self.assertEqual([item["scenario"] for item in registry["scenarios"]], ["inert-report-v3"])
        self.assertIsNone(registry["predecessor_registry_sha256"])

    def test_title_only_preview_crosses_no_hash_log_or_persistence_spy(self) -> None:
        source = self.synthetic_input(title="Private Title Spy Sentinel")
        with patch("hashlib.sha256", side_effect=AssertionError("title hashed")), patch(
            "logging.Logger._log", side_effect=AssertionError("title logged")
        ), patch(
            "mechanical_librarian.infrastructure.persistence.sqlite.connect",
            side_effect=AssertionError("title persisted"),
        ):
            report = _run_hardcover_inert_preview(source)
        self.assertEqual(report.outcome, HardcoverIdentityOutcome.DISABLED)
        self.assertNotIn("Sentinel", report.to_json())

    def test_synthetic_invalid_input_is_typed_and_zero_access(self) -> None:
        source = self.synthetic_input()
        source["unexpected"] = True
        output = io.StringIO()
        errors = io.StringIO()
        with patch(
            "mechanical_librarian.infrastructure.security.private_query.read_private_query",
            side_effect=AssertionError("private file opened"),
        ), patch(
            "mechanical_librarian.infrastructure.persistence.sqlite.connect",
            side_effect=AssertionError("database opened"),
        ):
            exit_code = main(
                [
                    "hardcover-identity-report",
                    "--mode",
                    "synthetic-preview",
                    "--scenario",
                    "inert-report-v3",
                    "--input",
                    "-",
                    "--format",
                    "json",
                ],
                state_check_runner=Mock(),
                hardcover_inert_preview_runner=_run_hardcover_inert_preview,
                stdin=io.StringIO(json.dumps(source)),
                stdout=output,
                stderr=errors,
            )
        self.assertEqual(exit_code, 2)
        self.assertEqual(errors.getvalue(), "")
        self.assertEqual(
            json.loads(output.getvalue())["result"],
            {
                "outcome": "invalid_input",
                "private_file_opened": False,
                "database_opened": False,
                "secret_opened": False,
                "network_started": False,
            },
        )

    def test_unknown_production_result_fails_closed(self) -> None:
        output = io.StringIO()
        errors = io.StringIO()
        runner = Mock(
            return_value=HardcoverIdentityReport(HardcoverIdentityOutcome.INVALID_INPUT)
        )
        exit_code = main(
            self.production_argv(EXAMPLE_CONFIG),
            state_check_runner=Mock(),
            hardcover_identity_runner=runner,
            stdout=output,
            stderr=errors,
        )
        self.assertEqual(exit_code, 2)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(errors.getvalue(), "Hardcover identity report failed safely.\n")


if __name__ == "__main__":
    unittest.main()
