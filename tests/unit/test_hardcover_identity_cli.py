from __future__ import annotations

import copy
from contextlib import ExitStack, contextmanager
import io
import hashlib
import json
import logging
import os
import subprocess
import tempfile
import traceback
import unittest
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
from mechanical_librarian.interfaces.cli import main
from tests.support.hardcover_c1_privacy import (
    ALTERNATE_ISBN,
    CAPTURE_CHANNELS,
    FORBIDDEN_RESULT_KEY_FRAGMENTS,
    PRIVACY_SCAN_PATH,
    REGISTRY_PATH,
    PrivacyEvidenceError,
    assert_channels_private,
    build_scan,
    canonical_bytes,
    c1_input_variants,
    forbidden_source_corpus,
    validate_output_document,
)


ROOT = Path(__file__).parents[2]
EXAMPLE_CONFIG = ROOT / "docs" / "examples" / "synthetic-config.json"


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

    def test_c1_public_scanner_reproduces_exact_present_tree_evidence(self) -> None:
        fixture_root = ROOT / "tests/fixtures/hardcover_identity"
        expected = {
            "c1-inert-report-input.json": "f9896b56f48d99dd10be69556a95b24b4968beb07b3989517a399d981af66223",
            "c1-inert-report-input-schema.json": "0e275c72d40e2f7e2460d18ce572fea598d79aeff113252a135bc05e8285aa61",
            "c1-inert-report-example-output.json": "910bc6803bbee3c1a43dba545cd497e036ea1ac3f8c663057cb705a4a60e5479",
            "c1-inert-report-variant-matrix.json": "e18f2eb3dcd75c09cb57443da8e2bea666604813c94a922260de7e92b30b9bf4",
            "c1-inert-report-output-schema-v1.json": "602c2037fa7277ee428f7a681f1f34d2a834203e9c12e61713fd85840c30b584",
            "c1-inert-report-privacy-contract-v1.json": "9ba41fefa5eb4c91c60228febe67ff366ac4e5b58d3422f8d4a6fd99d776b69e",
            "c1-inert-report-privacy-scan-v1.json": "837fb3d9d92f55a44dc8a6ce084ca7bcdfad3eca286bf3ce2c6ad7d96e648f44",
            "c1-inert-report-registry-v1.json": "2119055ef8352db570eeb69a4624d46ac8ff56f6f8fa571cd1f80ca22d9ff343",
        }
        self.assertEqual(
            {path.name for path in fixture_root.glob("*.json")}, set(expected)
        )
        for name, digest in expected.items():
            with self.subTest(name=name):
                self.assertEqual(
                    hashlib.sha256((fixture_root / name).read_bytes()).hexdigest(),
                    digest,
                )
        generated = canonical_bytes(build_scan(ROOT))
        committed = (ROOT / PRIVACY_SCAN_PATH).read_bytes()
        self.assertEqual(generated, committed)
        self.assertEqual(
            hashlib.sha256(generated).hexdigest(),
            expected[PRIVACY_SCAN_PATH.name],
        )
        registry = json.loads((ROOT / REGISTRY_PATH).read_text(encoding="utf-8"))
        self.assertEqual(
            registry["resources"]["privacy_scan"]["sha256"],
            hashlib.sha256(generated).hexdigest(),
        )
        self.assertFalse(registry["scope"]["future_scenarios_included"])
        future_markers = (
            "identity-decision-v3",
            "fixed-adapter-v3",
            "durable-work-v3",
            "safe-executor-v3",
            "schema8",
        )
        for name in (
            "c1-inert-report-output-schema-v1.json",
            "c1-inert-report-privacy-contract-v1.json",
            "c1-inert-report-privacy-scan-v1.json",
            "c1-inert-report-registry-v1.json",
        ):
            payload = (fixture_root / name).read_text(encoding="utf-8").casefold()
            for marker in future_markers:
                self.assertNotIn(marker, payload)

    def test_c1_generator_runs_directly_from_repository_root(self) -> None:
        paths = tuple(
            sorted((ROOT / "tests/fixtures/hardcover_identity").glob("*.json"))
        )
        before = {path: path.read_bytes() for path in paths}
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        completed = subprocess.run(
            ["python3", "scripts/generate_hardcover_c1_privacy.py"],
            cwd=ROOT,
            env=environment,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(
            {path: path.read_bytes() for path in paths},
            before,
        )

    def test_c1_output_schema_rejects_forbidden_and_additional_properties(
        self,
    ) -> None:
        fixture_root = ROOT / "tests/fixtures/hardcover_identity"
        output = json.loads(
            (fixture_root / "c1-inert-report-example-output.json").read_text()
        )
        validate_output_document(output)
        for fragment in FORBIDDEN_RESULT_KEY_FRAGMENTS:
            mutated = copy.deepcopy(output)
            mutated["result"][f"{fragment}_sentinel"] = False
            with self.subTest(forbidden_result_fragment=fragment):
                with self.assertRaises(PrivacyEvidenceError):
                    validate_output_document(mutated)
        for location in ("top", "result"):
            mutated = copy.deepcopy(output)
            target = mutated if location == "top" else mutated["result"]
            target["unexpected"] = False
            with self.subTest(additional_property=location):
                with self.assertRaises(PrivacyEvidenceError):
                    validate_output_document(mutated)
        for key in tuple(output):
            mutated = copy.deepcopy(output)
            del mutated[key]
            with self.subTest(missing_top_level_key=key):
                with self.assertRaises(PrivacyEvidenceError):
                    validate_output_document(mutated)
        for key in tuple(output["result"]):
            mutated = copy.deepcopy(output)
            del mutated["result"][key]
            with self.subTest(missing_result_key=key):
                with self.assertRaises(PrivacyEvidenceError):
                    validate_output_document(mutated)
        for key, value in (
            ("schema", "hardcover-synthetic-output-v6"),
            ("scenario", "another-scenario"),
        ):
            mutated = copy.deepcopy(output)
            mutated[key] = value
            with self.subTest(mutated_identity=key):
                with self.assertRaises(PrivacyEvidenceError):
                    validate_output_document(mutated)
        mutated = copy.deepcopy(output)
        mutated["result"]["outcome"] = "provider_not_activated"
        with self.assertRaises(PrivacyEvidenceError):
            validate_output_document(mutated)
        for key in (
            "private_file_opened",
            "database_opened",
            "secret_opened",
            "network_started",
        ):
            for value in (True, 0, 1, None, "false"):
                mutated = copy.deepcopy(output)
                mutated["result"][key] = value
                with self.subTest(counter=key, malformed_value=value):
                    with self.assertRaises(PrivacyEvidenceError):
                        validate_output_document(mutated)

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

    def test_c1_input_variants_are_channel_complete_private_and_metamorphic(
        self,
    ) -> None:
        variants = c1_input_variants(ROOT)
        corpus = forbidden_source_corpus(ROOT, variants=variants)
        channels: dict[str, list[str]] = {name: [] for name in CAPTURE_CHANNELS}
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

        def fingerprint_spy(*args: object, **kwargs: object) -> None:
            channels["fingerprint_inputs"].append(repr((args, kwargs)))
            raise _AuthorityTrap("fingerprint reached")

        def privacy_observer(channel: str, values: tuple[object, ...]) -> None:
            if channel == "metrics":
                name, labels, value, exemplar = values
                channels["metric_names"].append(repr(name))
                channels["metric_labels"].append(repr(labels))
                channels["metric_values"].append(repr(value))
                channels["metric_exemplars"].append(repr(exemplar))
                return
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
            stack.enter_context(
                patch(
                    "mechanical_librarian.bootstrap.composition.Fingerprint",
                    side_effect=fingerprint_spy,
                )
            )
            for target in persistence_targets:
                stack.enter_context(patch(target, side_effect=persistence_spy))

            for variant in variants:
                output = io.StringIO()
                errors = io.StringIO()
                exit_code = main(
                    argv,
                    state_check_runner=Mock(),
                    hardcover_inert_preview_runner=observed_runner,
                    stdin=io.StringIO(variant.input_payload.decode("utf-8")),
                    stdout=output,
                    stderr=errors,
                )
                self.assertEqual(exit_code, 0)
                channels["stdout"].append(output.getvalue())
                channels["stderr"].append(errors.getvalue())
                outputs.append(output.getvalue())

            invalid = copy.deepcopy(variants[0].source)
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

        self.assertEqual(outputs, [outputs[0]] * len(variants))
        self.assertEqual(channels["hash_inputs"], [])
        self.assertEqual(channels["fingerprint_inputs"], [])
        self.assertEqual(channels["structured_logs"], [])
        self.assertEqual(channels["audit_logs"], [])
        self.assertEqual(channels["non_audit_persistence"], [])
        self.assertTrue(channels["exceptions"])
        self.assertTrue(channels["traces"])
        self.assertTrue(channels["spans"])
        self.assertTrue(channels["metric_names"])
        self.assertTrue(channels["metric_labels"])
        self.assertTrue(channels["metric_values"])
        self.assertTrue(channels["metric_exemplars"])
        self.assertTrue(channels["exports"])
        self.assertTrue(channels["reports"])
        self.assertEqual(
            tuple(variant.name for variant in variants),
            ("canonical", "title-only-alternate", "isbn-alternate"),
        )
        self.assertEqual(variants[2].raw_isbn, ALTERNATE_ISBN)
        self.assertEqual(variants[2].canonical_isbn, "9780306406157")
        for variant in variants:
            self.assertIn(
                variant.query_payload.decode("utf-8"),
                corpus["serialized_private_query"],
            )
            self.assertIn(
                variant.input_payload.decode("utf-8"),
                corpus["serialized_input"],
            )
        self.assertIn(
            hashlib.md5(
                variants[0].title.encode("utf-8"), usedforsecurity=False
            ).hexdigest(),
            corpus["title"],
        )
        self.assertIn(
            hashlib.sha1(
                variants[0].title.encode("utf-8"), usedforsecurity=False
            ).hexdigest(),
            corpus["title"],
        )
        assert_channels_private(channels, corpus)
        incomplete_channels = dict(channels)
        del incomplete_channels[CAPTURE_CHANNELS[-1]]
        with self.assertRaises(PrivacyEvidenceError):
            assert_channels_private(incomplete_channels, corpus)
        incomplete_corpus = dict(corpus)
        del incomplete_corpus["resource_identity"]
        with self.assertRaises(PrivacyEvidenceError):
            assert_channels_private(channels, incomplete_corpus)

        hand_authored_mutations = (
            ("title", "WKXDIFFERENTCONFIDENTIALNAME91"),
            ("canonical_isbn", "9780306406157"),
            (
                "resource_identity",
                "dGVzdHMvZml4dHVyZXMvaGFyZGNvdmVyX2lkZW50aXR5L2MxLWluZXJ0"
                "LXJlcG9ydC1yZWdpc3RyeS12MS5qc29u",
            ),
            (
                "serialized_input",
                "33f0bddb9114b8a2a8c13269f09e6c1107f723836cd4ac3e6087e1131a46266d",
            ),
        )
        for source_class, projection in hand_authored_mutations:
            self.assertIn(projection, corpus[source_class])
            poisoned = {name: list(values) for name, values in channels.items()}
            poisoned["stdout"].append(projection)
            with self.subTest(hand_authored_source_class=source_class):
                with self.assertRaises(PrivacyEvidenceError):
                    assert_channels_private(poisoned, corpus)

        for channel in CAPTURE_CHANNELS:
            for source_class, projections in corpus.items():
                for projection in projections:
                    poisoned = {
                        name: list(values) for name, values in channels.items()
                    }
                    poisoned[channel].append(projection)
                    probe_corpus = {
                        name: frozenset({f"absent-probe:{name}"})
                        for name in corpus
                    }
                    probe_corpus[source_class] = frozenset({projection})
                    with self.subTest(
                        negative_scanner_channel=channel,
                        source_class=source_class,
                    ):
                        with self.assertRaises(PrivacyEvidenceError):
                            assert_channels_private(poisoned, probe_corpus)

        projected_cause = next(iter(corpus["serialized_private_query"]))
        cause = ValueError(projected_cause)
        handled = RuntimeError("handled synthetic preview failure")
        handled.__cause__ = cause
        handled.add_note(projected_cause)
        poisoned = {name: list(values) for name, values in channels.items()}
        poisoned["exceptions"].extend(_exception_chain_metadata(handled))
        with self.assertRaises(PrivacyEvidenceError):
            assert_channels_private(poisoned, corpus)

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

    def test_malformed_exact_type_synthetic_reports_fail_without_serialization(
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
        malformed = [
            InertPreviewReport("disabled"),  # type: ignore[arg-type]
            InertPreviewReport(HardcoverIdentityOutcome.PROVIDER_NOT_ACTIVATED),
        ]
        for counter in (
            "private_file_opened",
            "database_opened",
            "secret_opened",
            "network_started",
        ):
            for value in (True, 0, 1, None, "false"):
                malformed.append(
                    InertPreviewReport(
                        HardcoverIdentityOutcome.DISABLED,
                        **{counter: value},  # type: ignore[arg-type]
                    )
                )
        for report in malformed:
            output = io.StringIO()
            errors = io.StringIO()
            exit_code = main(
                argv,
                state_check_runner=Mock(),
                hardcover_inert_preview_runner=Mock(return_value=report),
                stdin=io.StringIO(json.dumps(self.synthetic_input())),
                stdout=output,
                stderr=errors,
            )
            with self.subTest(report=report):
                self.assertEqual(exit_code, 2)
                self.assertEqual(output.getvalue(), "")
                self.assertEqual(
                    errors.getvalue(),
                    "Hardcover identity report failed safely.\n",
                )


if __name__ == "__main__":
    unittest.main()
