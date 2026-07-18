from __future__ import annotations

import base64
import copy
from contextlib import ExitStack, contextmanager
import io
import hashlib
import json
import logging
import re
import tempfile
import traceback
import unittest
import unicodedata
from urllib.parse import quote, quote_plus
from pathlib import Path
from unittest.mock import Mock, patch

from mechanical_librarian.application.hardcover_identity import (
    HardcoverIdentityOutcome,
    HardcoverIdentityReport,
    InertPreviewReport,
)
from mechanical_librarian.bootstrap import composition as composition_module
from mechanical_librarian.bootstrap.composition import (
    _run_hardcover_identity_report,
    _run_hardcover_inert_preview,
)
from mechanical_librarian.domain.title_proposals import normalized_title_key
from mechanical_librarian.interfaces.cli import main


ROOT = Path(__file__).parents[2]
EXAMPLE_CONFIG = ROOT / "docs" / "examples" / "synthetic-config.json"


def _forbidden_title_projections(title: str) -> frozenset[str]:
    encoded = title.encode("utf-8")
    normalized = normalized_title_key(title)
    textual = {
        title,
        title.casefold(),
        normalized,
        unicodedata.normalize("NFC", title),
        unicodedata.normalize("NFKC", title),
        json.dumps(title, ensure_ascii=True),
        repr(title),
        encoded.hex(),
        base64.b16encode(encoded).decode("ascii"),
        base64.b32encode(encoded).decode("ascii"),
        base64.b64encode(encoded).decode("ascii"),
        base64.urlsafe_b64encode(encoded).decode("ascii"),
        quote(title),
        quote_plus(title),
        str(len(title)),
        str(len(encoded)),
        f"title-length={len(title)}",
    }
    tokens = set(re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", title))
    tokens.update(token.casefold() for token in tuple(tokens))
    fragments = {
        value[index : index + 8]
        for value in textual | tokens
        for index in range(0, len(value), 8)
        if len(value[index : index + 8]) >= 4
    }
    fragments.update(value[:8] for value in textual | tokens if len(value) >= 8)
    digesters = (
        hashlib.md5(encoded, usedforsecurity=False),
        hashlib.sha1(encoded, usedforsecurity=False),
        hashlib.sha224(encoded),
        hashlib.sha256(encoded),
        hashlib.sha384(encoded),
        hashlib.sha512(encoded),
        hashlib.sha3_256(encoded),
        hashlib.sha3_512(encoded),
        hashlib.blake2s(encoded),
        hashlib.blake2b(encoded),
    )
    digests: set[str] = set()
    for digester in digesters:
        raw_digest = digester.digest()
        hexadecimal = digester.hexdigest()
        digests.update(
            {
                hexadecimal,
                hexadecimal[:8],
                base64.b64encode(raw_digest).decode("ascii"),
                base64.urlsafe_b64encode(raw_digest).decode("ascii"),
            }
        )
    values = textual | tokens | fragments | digests
    return frozenset(value for value in values if value)


def _assert_private_channels(
    channels: dict[str, list[str]], projections: frozenset[str]
) -> None:
    for channel, values in channels.items():
        rendered = "\n".join(values)
        for projection in projections:
            if projection in rendered:
                raise AssertionError(f"forbidden title projection reached {channel}")


def _exception_chain_metadata(error: BaseException) -> list[str]:
    captured: list[str] = []
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        captured.extend(
            (
                type(current).__qualname__,
                str(current),
                repr(current.args),
                repr(getattr(current, "__notes__", None)),
                repr(current.__suppress_context__),
                repr(traceback.extract_tb(current.__traceback__)),
            )
        )
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return captured


class _AuthorityTrap(AssertionError):
    pass


@contextmanager
def _zero_authority() -> object:
    targets = (
        "mechanical_librarian.bootstrap.composition.HardcoverTitleProvider",
        "mechanical_librarian.bootstrap.composition.PrivateFileProviderCredentials",
        "mechanical_librarian.bootstrap.composition.httpx.AsyncClient",
        "mechanical_librarian.bootstrap.composition.httpx.Client",
        "mechanical_librarian.bootstrap.composition.httpx.Proxy",
        "mechanical_librarian.bootstrap.composition.httpx.HTTPTransport",
        "mechanical_librarian.bootstrap.composition.httpx.AsyncHTTPTransport",
        "mechanical_librarian.bootstrap.composition.connect",
        "mechanical_librarian.bootstrap.composition.connect_existing",
        "mechanical_librarian.infrastructure.security.private_query.read_private_query",
        "mechanical_librarian.infrastructure.security.private_file.read_private_secret",
        "mechanical_librarian.infrastructure.security.provider_secrets."
        "read_private_secret",
        "mechanical_librarian.infrastructure.security.provider_secrets."
        "PrivateFileProviderCredentials",
        "mechanical_librarian.infrastructure.persistence.sqlite.connect",
        "mechanical_librarian.infrastructure.persistence.sqlite.connect_existing",
        "mechanical_librarian.adapters.providers.hardcover_title."
        "HardcoverTitleProvider",
        "mechanical_librarian.adapters.providers.hardcover_title._system_resolver",
        "socket.getaddrinfo",
        "socket.create_connection",
        "socket.socket",
    )
    with ExitStack() as stack:
        for target in targets:
            stack.enter_context(
                patch(
                    target,
                    side_effect=_AuthorityTrap(f"authority reached: {target}"),
                )
            )
        yield


def _merge_patch(target: dict[str, object], update: dict[str, object]) -> None:
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge_patch(target[key], value)  # type: ignore[arg-type]
        else:
            target[key] = value


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

    def synthetic_input(
        self, *, title: str = "Synthetic Ebook Alpha"
    ) -> dict[str, object]:
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

    def test_production_disabled_and_enabled_short_circuit_before_private_authority(
        self,
    ) -> None:
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
                with self.subTest(outcome=outcome), _zero_authority():
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

    def test_composition_lookup_authority_traps_are_executable(self) -> None:
        invocations = (
            ("provider", lambda: composition_module.HardcoverTitleProvider(None)),
            (
                "credentials",
                lambda: composition_module.PrivateFileProviderCredentials(),
            ),
            ("client", lambda: composition_module.httpx.AsyncClient()),
            ("proxy", lambda: composition_module.httpx.Proxy("http://invalid")),
            ("transport", lambda: composition_module.httpx.AsyncHTTPTransport()),
            ("database", lambda: composition_module.connect(Path("/must-not-open"))),
        )
        with _zero_authority():
            for name, invoke in invocations:
                with self.subTest(composition_lookup=name):
                    with self.assertRaises(_AuthorityTrap):
                        invoke()

    def test_synthetic_preview_matches_c1_oracle_without_echo_or_stable_identifier(
        self,
    ) -> None:
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
                self.assertEqual(
                    hashlib.sha256((fixture_root / name).read_bytes()).hexdigest(),
                    digest,
                )
        registry = json.loads(
            (fixture_root / "preview-registry-c1-v5.json").read_text()
        )
        self.assertEqual(
            [item["scenario"] for item in registry["scenarios"]],
            ["inert-report-v3"],
        )
        self.assertIsNone(registry["predecessor_registry_sha256"])
        privacy = json.loads(
            (fixture_root / "preview-output-privacy-contract-v2.json").read_text()
        )
        scan = json.loads(
            (fixture_root / "preview-output-privacy-scan-v2.json").read_text()
        )
        self.assertEqual(
            privacy["runtime_channels"],
            scan["implementation_qualification"]["capture_channels"],
        )
        self.assertEqual(
            scan["implementation_qualification"]["status"],
            "required at each installed head and activation",
        )
        self.assertEqual(
            set(scan["implementation_qualification"]["spies"]),
            {
                "exceptions",
                "hashing",
                "metrics",
                "persistence_export_reporting",
                "tracing",
            },
        )
        self.assertGreaterEqual(
            len(scan["implementation_qualification"]["negative_failing_tests"]), 6
        )

    def test_retained_c1_variant_matrix_executes_through_real_cli_seam(self) -> None:
        matrix = json.loads(
            (
                ROOT
                / "tests/fixtures/hardcover_identity/c1-inert-report-variant-matrix.json"
            ).read_text(encoding="utf-8")
        )
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
        self.assertEqual(matrix["scenario"], "inert-report-v3")
        for case in matrix["cases"]:
            with self.subTest(case=case["id"]), _zero_authority():
                source = copy.deepcopy(self.synthetic_input())
                update = copy.deepcopy(case["input_patch"])
                private_update = update.get("private_query")
                if isinstance(private_update, dict) and isinstance(
                    private_update.get("title_generator"), dict
                ):
                    generator = private_update.pop("title_generator")
                    private_update["title"] = generator["scalar"] * generator["size"]
                _merge_patch(source, update)
                output = io.StringIO()
                errors = io.StringIO()
                exit_code = main(
                    argv,
                    state_check_runner=Mock(),
                    hardcover_inert_preview_runner=_run_hardcover_inert_preview,
                    stdin=io.StringIO(json.dumps(source)),
                    stdout=output,
                    stderr=errors,
                )
                result = json.loads(output.getvalue())["result"]
                self.assertEqual(errors.getvalue(), "")
                self.assertEqual(result["outcome"], case["expected"]["outcome"])
                self.assertEqual(exit_code, 0 if result["outcome"] == "disabled" else 2)
                for counter in (
                    "private_file_opened",
                    "database_opened",
                    "secret_opened",
                    "network_started",
                ):
                    self.assertFalse(result[counter])

    def test_title_only_cli_pair_is_channel_complete_private_and_metamorphic(
        self,
    ) -> None:
        titles = (
            "QzvLomarixNebu73",
            "WkxPadrunoSefi91",
        )
        projections = frozenset(
            value for title in titles for value in _forbidden_title_projections(title)
        )
        channels: dict[str, list[str]] = {
            name: []
            for name in (
                "stdout",
                "stderr",
                "exceptions",
                "audit_logs",
                "structured_logs",
                "traces",
                "spans",
                "metrics",
                "non_audit_persistence",
                "exports",
                "reports",
                "hash_inputs",
            )
        }
        real_hashes = {
            "md5": hashlib.md5,
            "sha1": hashlib.sha1,
            "sha224": hashlib.sha224,
            "sha256": hashlib.sha256,
            "sha384": hashlib.sha384,
            "sha512": hashlib.sha512,
            "sha3_256": hashlib.sha3_256,
            "sha3_512": hashlib.sha3_512,
            "blake2s": hashlib.blake2s,
            "blake2b": hashlib.blake2b,
        }
        real_hash_new = hashlib.new

        def hash_spy(name: str):
            def invoke(value: bytes = b"", *args: object, **kwargs: object):
                channels["hash_inputs"].append(repr(value))
                return real_hashes[name](value, *args, **kwargs)

            return invoke

        def hash_new_spy(
            name: str, value: bytes = b"", **kwargs: object
        ) -> object:
            channels["hash_inputs"].append(repr((name, value)))
            return real_hash_new(name, value, **kwargs)

        def log_spy(*args: object, **kwargs: object) -> None:
            channels["structured_logs"].append(repr((args, kwargs)))

        def audit_spy(*args: object, **kwargs: object) -> None:
            channels["audit_logs"].append(repr((args, kwargs)))
            raise _AuthorityTrap("audit persistence reached")

        def persistence_spy(*args: object, **kwargs: object) -> None:
            channels["non_audit_persistence"].append(repr((args, kwargs)))
            raise _AuthorityTrap("non-audit persistence reached")

        def privacy_observer(channel: str, values: tuple[object, ...]) -> None:
            channels[channel].append(repr(values))

        original_to_json = InertPreviewReport.to_json

        def report_spy(report: InertPreviewReport) -> str:
            rendered = original_to_json(report)
            channels["exports"].append(rendered)
            channels["reports"].append(rendered)
            return rendered

        def observed_runner(source: dict[str, object]) -> InertPreviewReport:
            try:
                return _run_hardcover_inert_preview(
                    source,
                    privacy_observer=privacy_observer,
                )
            except BaseException as error:
                channels["exceptions"].extend(_exception_chain_metadata(error))
                raise

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
        persistence_targets = (
            "mechanical_librarian.bootstrap.composition.SQLiteInventoryRepository",
            "mechanical_librarian.bootstrap.composition.SQLiteInventoryV2Repository",
            "mechanical_librarian.bootstrap.composition."
            "SQLiteInventoryPollingRepository",
            "mechanical_librarian.bootstrap.composition."
            "SQLiteEpubOpeningEvidenceRepository",
            "mechanical_librarian.bootstrap.composition.SQLiteTitleProposalRepository",
            "mechanical_librarian.bootstrap.composition."
            "SQLiteTitleReviewCaseRepository",
            "mechanical_librarian.bootstrap.composition.SQLiteJobHeartbeatRepository",
        )
        with ExitStack() as stack:
            stack.enter_context(_zero_authority())
            stack.enter_context(
                patch.object(logging.Logger, "_log", side_effect=log_spy)
            )
            stack.enter_context(
                patch(
                    "mechanical_librarian.adapters.persistence.audit."
                    "SQLiteAuditRepository.append",
                    side_effect=audit_spy,
                )
            )
            stack.enter_context(
                patch.object(InertPreviewReport, "to_json", new=report_spy)
            )
            for name in real_hashes:
                stack.enter_context(
                    patch(
                        f"hashlib.{name}",
                        side_effect=hash_spy(name),
                    )
                )
            stack.enter_context(patch("hashlib.new", side_effect=hash_new_spy))
            for target in persistence_targets:
                stack.enter_context(patch(target, side_effect=persistence_spy))

            for title in titles:
                output = io.StringIO()
                errors = io.StringIO()
                exit_code = main(
                    argv,
                    state_check_runner=Mock(),
                    hardcover_inert_preview_runner=observed_runner,
                    stdin=io.StringIO(json.dumps(self.synthetic_input(title=title))),
                    stdout=output,
                    stderr=errors,
                )
                self.assertEqual(exit_code, 0)
                channels["stdout"].append(output.getvalue())
                channels["stderr"].append(errors.getvalue())
                outputs.append(output.getvalue())

            invalid = self.synthetic_input(title=titles[0])
            invalid["unexpected"] = True
            invalid_output = io.StringIO()
            invalid_errors = io.StringIO()
            self.assertEqual(
                main(
                    argv,
                    state_check_runner=Mock(),
                    hardcover_inert_preview_runner=observed_runner,
                    stdin=io.StringIO(json.dumps(invalid)),
                    stdout=invalid_output,
                    stderr=invalid_errors,
                ),
                2,
            )
            channels["stdout"].append(invalid_output.getvalue())
            channels["stderr"].append(invalid_errors.getvalue())

        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(channels["hash_inputs"], [])
        self.assertEqual(channels["structured_logs"], [])
        self.assertEqual(channels["audit_logs"], [])
        self.assertEqual(channels["non_audit_persistence"], [])
        self.assertTrue(channels["exceptions"])
        self.assertTrue(channels["traces"])
        self.assertTrue(channels["spans"])
        self.assertTrue(channels["metrics"])
        self.assertTrue(channels["exports"])
        self.assertTrue(channels["reports"])
        self.assertIn(titles[0][:8], projections)
        self.assertIn(
            hashlib.md5(
                titles[0].encode("utf-8"), usedforsecurity=False
            ).hexdigest(),
            projections,
        )
        self.assertIn(
            hashlib.sha1(
                titles[0].encode("utf-8"), usedforsecurity=False
            ).hexdigest(),
            projections,
        )
        _assert_private_channels(channels, projections)

        for channel in channels:
            for projection in projections:
                poisoned = {name: list(values) for name, values in channels.items()}
                poisoned[channel].append(projection)
                with self.subTest(negative_scanner_channel=channel):
                    with self.assertRaises(AssertionError):
                        _assert_private_channels(poisoned, projections)

        projected_cause = next(iter(projections))
        cause = ValueError(projected_cause)
        handled = RuntimeError("handled synthetic preview failure")
        handled.__cause__ = cause
        handled.add_note(projected_cause)
        poisoned = {name: list(values) for name, values in channels.items()}
        poisoned["exceptions"].extend(_exception_chain_metadata(handled))
        with self.assertRaises(AssertionError):
            _assert_private_channels(poisoned, projections)

    def test_synthetic_invalid_input_is_typed_and_zero_access(self) -> None:
        source = self.synthetic_input()
        source["unexpected"] = True
        output = io.StringIO()
        errors = io.StringIO()
        with _zero_authority():
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

    def test_valid_and_invalid_synthetic_cli_are_zero_authority(self) -> None:
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
        for source, expected_exit in (
            (self.synthetic_input(), 0),
            ({**self.synthetic_input(), "unexpected": True}, 2),
        ):
            with self.subTest(expected_exit=expected_exit), _zero_authority():
                output = io.StringIO()
                errors = io.StringIO()
                exit_code = main(
                    argv,
                    state_check_runner=Mock(),
                    hardcover_inert_preview_runner=_run_hardcover_inert_preview,
                    stdin=io.StringIO(json.dumps(source)),
                    stdout=output,
                    stderr=errors,
                )
            self.assertEqual(exit_code, expected_exit)
            self.assertEqual(errors.getvalue(), "")

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
