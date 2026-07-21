from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from mechanical_librarian.application.hardcover_identity import (
    HardcoverExecutionPolicy,
    HardcoverIdentityOutcome,
    HardcoverIdentityReport,
    IdentityAttemptResult,
)
from mechanical_librarian.bootstrap import composition
from mechanical_librarian.application.hardcover_safe_executor import (
    hardcover_identity_work_key,
)
from mechanical_librarian.bootstrap.composition import (
    _run_hardcover_identity_report,
    _run_new_qualification_state_check,
    _run_state_check,
)
from mechanical_librarian.domain.hardcover_identity import (
    ProviderBookObservation,
    ProviderIdentityObservation,
    TitleComparison,
    validate_private_query,
)
from mechanical_librarian.domain.inventory import SecretReference
from mechanical_librarian.domain.security import SecretHandle, SecretScope
from mechanical_librarian.infrastructure.security.provider_secrets import (
    PrivateFileProviderCredentials,
    ProviderSecretUnavailable,
)
from mechanical_librarian.interfaces.cli import main


ROOT = Path(__file__).parents[2]
WORK_KEY_CONTRACT = (
    ROOT / "docs/reference/hardcover-work-key-contract-v1.json"
)
ACTIVATION_REGISTRY = (
    ROOT / "docs/reference/hardcover-f09f-activation-registry-v1.json"
)
EXAMPLE_CONFIG = ROOT / "docs/examples/synthetic-config.json"
OPERATOR_GUIDE = ROOT / "docs/operations/hardcover-identity-report.md"


class HardcoverActivationTests(unittest.TestCase):
    def _one_shot_provenance_script(self) -> str:
        documentation = OPERATOR_GUIDE.read_text(encoding="utf-8")
        procedure = documentation.split(
            "### Authorized production one-shot procedure", 1
        )[1].split("## Private query contract", 1)[0]
        return procedure.split("```sh\n", 1)[1].split("\n```", 1)[0]

    def _one_shot_test_repository(self, root: Path) -> Path:
        repository = root / "repository"
        source = repository / "src" / "mechanical_librarian"
        shutil.copytree(ROOT / "src" / "mechanical_librarian", source)
        subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
        subprocess.run(["git", "add", "src"], cwd=repository, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Documentation Test",
                "-c",
                "user.email=documentation-test@example.invalid",
                "commit",
                "-qm",
                "fixture",
            ],
            cwd=repository,
            check=True,
        )
        return repository

    def _one_shot_test_wheel(self, repository: Path, wheel: Path) -> None:
        with zipfile.ZipFile(wheel, "w") as archive:
            source = repository / "src" / "mechanical_librarian"
            for path in sorted(source.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(repository / "src"))

    def _run_one_shot_provenance_script(
        self, repository: Path, wheel: Path, sentinel: Path
    ) -> subprocess.CompletedProcess[str]:
        script = self._one_shot_provenance_script().replace(
            'uv build --out-dir "$wheel_output"',
            'cp "$TEST_WHEEL" '
            '"$wheel_output/mechanical_librarian-0.0.0-py3-none-any.whl"',
        ).replace(
            'python3 -m pip install --force-reinstall --no-deps "$wheel_path"',
            'printf "installed\\n" >"$INSTALL_SENTINEL"',
        )
        environment = os.environ.copy()
        environment.update(
            {"TEST_WHEEL": str(wheel), "INSTALL_SENTINEL": str(sentinel)}
        )
        return subprocess.run(
            ["/bin/sh", "-c", script],
            cwd=repository,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def _enabled_config(self, root: Path) -> Path:
        source = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
        source["hardcover_provider"] = {
            "enabled": True,
            "secret_reference": "/must-not-open/provider-token",
            "authority_revision": 1,
        }
        path = root / "config.json"
        path.write_text(json.dumps(source), encoding="utf-8")
        return path

    def test_work_key_binds_exact_frozen_contract_without_local_title(self) -> None:
        payload = WORK_KEY_CONTRACT.read_bytes()
        self.assertEqual(len(payload), 1_822)
        self.assertEqual(
            hashlib.sha256(payload).hexdigest(),
            "cd5cdaf45a60173c4dedb10b5c8415c74adb5a996b54158333085d9cda12bd6c",
        )
        source = json.loads(payload)
        self.assertEqual(source["version"], "hardcover-isbn-work-key-v1")
        query = validate_private_query(
            {"isbn": "9780000000019", "title": "Synthetic Ebook Alpha"}
        )
        renamed = validate_private_query(
            {"isbn": "9780000000019", "title": "A Different Local Title"}
        )
        self.assertEqual(
            hardcover_identity_work_key(query, 1),
            "46e0831ebbfb726eed965231bf6d064481646f664251aa284ab343bb58ebabcd",
        )
        self.assertEqual(
            hardcover_identity_work_key(query, 1),
            hardcover_identity_work_key(renamed, 1),
        )
        self.assertNotEqual(
            hardcover_identity_work_key(query, 1),
            hardcover_identity_work_key(query, 2),
        )

    def test_one_shot_operator_procedure_uses_valid_json_configuration(self) -> None:
        documentation = OPERATOR_GUIDE.read_text(encoding="utf-8")
        procedure = documentation.split(
            "### Authorized production one-shot procedure", 1
        )[1].split("## Private query contract", 1)[0]
        encoded = procedure.split("```json\n", 1)[1].split("\n```", 1)[0]
        self.assertEqual(
            json.loads(encoded),
            {
                "hardcover_provider": {
                    "enabled": True,
                    "secret_reference": (
                        "/run/mechanical-librarian/secrets/hardcover-token"
                    ),
                    "authority_revision": 1,
                }
            },
        )
        self.assertIn("complete valid `config-v1` JSON document", procedure)
        self.assertIn("--config /etc/mechanical-librarian/config.json", procedure)
        self.assertNotIn("config.yml", procedure)
        self.assertNotIn("result_sha256", procedure)
        self.assertNotIn("evidence_sha256", procedure)
        self.assertIn('rm -- "$result_path"', procedure)
        self.assertIn('test ! -e "$result_path"', procedure)
        self.assertIn("schema=", procedure)
        self.assertIn("outcome=", procedure)
        self.assertIn("tree=%s", procedure)

    def test_one_shot_provenance_procedure_succeeds_and_short_circuits(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = self._one_shot_test_repository(root)
            valid_wheel = root / "valid.whl"
            self._one_shot_test_wheel(repository, valid_wheel)
            installed = root / "installed"

            success = self._run_one_shot_provenance_script(
                repository, valid_wheel, installed
            )
            self.assertEqual(success.returncode, 0, success.stderr)
            self.assertEqual(installed.read_text(encoding="utf-8"), "installed\n")

            installed.unlink()
            invalid_wheel = root / "invalid.whl"
            with zipfile.ZipFile(invalid_wheel, "w") as archive:
                archive.writestr("mechanical_librarian/__init__.py", b"")
            failure = self._run_one_shot_provenance_script(
                repository, invalid_wheel, installed
            )
            self.assertNotEqual(failure.returncode, 0)
            self.assertFalse(installed.exists())

    def test_activation_registry_binds_c6_privacy_and_authority_contracts(
        self,
    ) -> None:
        payload = ACTIVATION_REGISTRY.read_bytes()
        self.assertEqual(len(payload), 2_121)
        self.assertEqual(
            hashlib.sha256(payload).hexdigest(),
            "552fb260d15f0e81a9ac4f64e926897513d698ecfd3857d303b0328356ea88b0",
        )
        registry = json.loads(payload)
        self.assertEqual(registry["closes"], [239, 242])
        self.assertEqual(len(registry["retained_synthetic_scenarios"]), 6)
        self.assertNotIn("provider_not_activated", registry["public_outcomes"])
        for binding in registry["bindings"].values():
            resource = ROOT / binding["path"]
            bound = resource.read_bytes()
            self.assertEqual(len(bound), binding["bytes"])
            self.assertEqual(hashlib.sha256(bound).hexdigest(), binding["sha256"])
        one_shot = json.loads(
            (
                ROOT
                / registry["bindings"]["live-one-shot-contract"]["path"]
            ).read_text(encoding="utf-8")
        )
        self.assertIn(
            "before provider preparation",
            one_shot["egress_budget"]["acceptance_binding"],
        )
        self.assertIn(
            "cannot widen authority",
            one_shot["egress_budget"]["mode_switch"],
        )
        production_schema = json.loads(
            (
                ROOT
                / registry["bindings"]["production-output-schema"]["path"]
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            production_schema["properties"]["schema"]["const"],
            "hardcover-identity-report-v6",
        )
        self.assertEqual(
            set(production_schema["properties"]["outcome"]["enum"]),
            {outcome.value for outcome in HardcoverIdentityOutcome},
        )
        self.assertEqual(
            json.loads(
                HardcoverIdentityReport(
                    HardcoverIdentityOutcome.MATCHED,
                    1001,
                    2001,
                    TitleComparison.EQUAL,
                ).to_json()
            )["schema"],
            "hardcover-identity-report-v6",
        )
        privacy = json.loads(
            (
                ROOT
                / registry["bindings"]["production-output-privacy"]["path"]
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            privacy["allowed_runtime"]["matched"],
            ["schema", "outcome", "edition_id", "book_id", "comparison"],
        )
        self.assertIn("local_title", privacy["forbidden_persistence"])
        self.assertIn("raw_isbn", privacy["forbidden_persistence"])
        self.assertNotIn(
            "provider_title", privacy["allowed_runtime"]["matched"]
        )
        private = privacy["allowed_private_persistence"]
        self.assertEqual(
            private["observation_integrity_digest"]["inputs"],
            ["domain", "work_key", "generation", "canonical_matched_observation"],
        )
        self.assertEqual(
            private["observation_integrity_digest"]["excludes"],
            ["local_title", "raw_isbn", "serialized_private_query"],
        )
        self.assertEqual(
            private["binding_recovery_record"]["fields"],
            ["device", "inode", "marker", "original_application_id"],
        )
        for forbidden in ("path", "credential", "user_value"):
            self.assertIn(forbidden, privacy["forbidden_persistence"])

    def test_enabled_invalid_query_returns_before_state_secret_dns_or_network(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self._enabled_config(root)
            query = root / "query.json"
            query.write_bytes(b"not-json")
            query.chmod(0o600)
            with (
                patch(
                    "mechanical_librarian.bootstrap.composition._run_state_check",
                    side_effect=AssertionError("state reached"),
                ),
                patch(
                    "mechanical_librarian.bootstrap.composition.PrivateFileProviderCredentials",
                    side_effect=AssertionError("secret reached"),
                ),
                patch(
                    "mechanical_librarian.infrastructure.security.hardcover_transport.subprocess.Popen",
                    side_effect=AssertionError("DNS reached"),
                ),
                patch(
                    "mechanical_librarian.adapters.providers.hardcover_identity.httpx.AsyncClient",
                    side_effect=AssertionError("network reached"),
                ),
            ):
                report = _run_hardcover_identity_report(
                    config,
                    root / "state.sqlite",
                    query,
                    HardcoverExecutionPolicy.NORMAL,
                )
        self.assertEqual(report.outcome, HardcoverIdentityOutcome.INVALID_INPUT)

    def test_gate_invalid_relative_database_fails_before_state_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            config = self._enabled_config(root)
            query = root / "query.json"
            query.write_text(
                json.dumps(
                    {"isbn": "9780000000019", "title": "Synthetic Ebook Alpha"}
                ),
                encoding="utf-8",
            )
            query.chmod(0o600)
            with patch(
                "mechanical_librarian.bootstrap.composition._run_state_check",
                side_effect=AssertionError("state mutated"),
            ):
                report = _run_hardcover_identity_report(
                    config,
                    Path("relative-state.sqlite"),
                    query,
                    HardcoverExecutionPolicy.NORMAL,
                )
        self.assertEqual(
            report.outcome, HardcoverIdentityOutcome.PERSISTENCE_FAILURE
        )

    def test_identity_token_reader_is_strict_and_rejects_bearer_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "token"
            path.write_bytes(b"raw-token")
            path.chmod(0o600)
            handle = SecretHandle(
                SecretReference(str(path)), SecretScope.METADATA_PROVIDER
            )
            credentials = PrivateFileProviderCredentials()
            self.assertEqual(credentials.hardcover_identity_token(handle), b"raw-token")
            path.write_bytes(b"Bearer raw-token")
            path.chmod(0o600)
            with self.assertRaises(ProviderSecretUnavailable):
                credentials.hardcover_identity_token(handle)

    def test_installed_composition_persists_start_before_provider_and_matches(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            config = self._enabled_config(root)
            query = root / "query.json"
            query.write_text(
                json.dumps(
                    {"isbn": "9780000000019", "title": "Synthetic Ebook Alpha"}
                ),
                encoding="utf-8",
            )
            query.chmod(0o600)
            database = root / "state.sqlite"

            class _Provider:
                def prepare(self, _query, _context):
                    return self

                def exchange(self, _context, *, begin_transport):
                    begin_transport()
                    connection = sqlite3.connect(database)
                    try:
                        started = connection.execute(
                            "SELECT SUM(attempt_count) FROM hardcover_identity_generations"
                        ).fetchone()
                    finally:
                        connection.close()
                    if started != (1,):
                        raise AssertionError("provider reached before durable start")
                    return IdentityAttemptResult.success(
                        (
                            ProviderIdentityObservation(
                                1001,
                                2001,
                                None,
                                "0000000019",
                                True,
                                "9780000000019",
                                True,
                                True,
                                4,
                                "normalized",
                                False,
                                ProviderBookObservation(
                                    2001,
                                    None,
                                    1,
                                    "processed",
                                    "Synthetic Ebook Alpha",
                                    None,
                                    False,
                                    False,
                                ),
                            ),
                        )
                    )

            with patch(
                "mechanical_librarian.bootstrap.composition.LiveHardcoverIdentityProvider",
                return_value=_Provider(),
            ):
                report = _run_hardcover_identity_report(
                    config, database, query, HardcoverExecutionPolicy.NORMAL
                )

            self.assertEqual(report.outcome, HardcoverIdentityOutcome.MATCHED)
            persisted = database.read_bytes()
            self.assertNotIn(b"9780000000019", persisted)
            self.assertIn(b"Synthetic Ebook Alpha", persisted)

    def test_cli_passes_one_shot_only_to_installed_production_runner(self) -> None:
        runner = Mock(
            return_value=HardcoverIdentityReport(
                HardcoverIdentityOutcome.MATCHED,
                1001,
                2001,
                TitleComparison.EQUAL,
            )
        )
        output = io.StringIO()
        errors = io.StringIO()
        code = main(
            [
                "hardcover-identity-report",
                "--config",
                "/configuration.json",
                "--database",
                "/state.sqlite",
                "--query-file",
                "/query.json",
                "--format",
                "json",
                "--qualification-one-shot",
            ],
            state_check_runner=Mock(),
            hardcover_identity_runner=runner,
            stdout=output,
            stderr=errors,
        )
        self.assertEqual((code, errors.getvalue()), (0, ""))
        runner.assert_called_once_with(
            Path("/configuration.json"),
            Path("/state.sqlite"),
            Path("/query.json"),
            HardcoverExecutionPolicy.QUALIFICATION_ONE_SHOT,
        )
        self.assertEqual(json.loads(output.getvalue())["outcome"], "matched")

    def test_one_shot_requires_json_and_fails_every_non_match(self) -> None:
        for arguments, outcome in (
            (("--format", "text"), HardcoverIdentityOutcome.MATCHED),
            (("--format", "json"), HardcoverIdentityOutcome.NO_MATCH),
            (("--format", "json"), HardcoverIdentityOutcome.RETRY_EXHAUSTED),
        ):
            with self.subTest(arguments=arguments, outcome=outcome):
                report = (
                    HardcoverIdentityReport(
                        outcome,
                        1001,
                        2001,
                        TitleComparison.EQUAL,
                    )
                    if outcome is HardcoverIdentityOutcome.MATCHED
                    else HardcoverIdentityReport(outcome)
                )
                runner = Mock(return_value=report)
                code = main(
                    [
                        "hardcover-identity-report",
                        "--config",
                        "/configuration.json",
                        "--database",
                        "/state.sqlite",
                        "--query-file",
                        "/query.json",
                        *arguments,
                        "--qualification-one-shot",
                    ],
                    state_check_runner=Mock(),
                    hardcover_identity_runner=runner,
                    stdout=io.StringIO(),
                    stderr=io.StringIO(),
                )
                self.assertEqual(code, 2)
                if arguments == ("--format", "text"):
                    runner.assert_not_called()
                else:
                    runner.assert_called_once()

    def test_one_shot_rejects_publication_loss_and_database_replacement(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            database = root / "state.sqlite"
            with patch(
                "mechanical_librarian.infrastructure.persistence.sqlite.BoundDisposableState.publish",
                return_value=False,
            ):
                state, identity = _run_new_qualification_state_check(database)
            self.assertFalse(state.succeeded)
            self.assertIsNone(identity)
            self.assertFalse(database.exists())

    def test_disposable_state_check_stops_after_first_directory_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            for index in range(32):
                (root / f"entry-{index}").touch()
            real_scandir = os.scandir
            next_calls = 0

            class _CountingEntries:
                def __init__(self, descriptor: int) -> None:
                    self._entries = real_scandir(descriptor)

                def __iter__(self):
                    return self

                def __next__(self):
                    nonlocal next_calls
                    next_calls += 1
                    return next(self._entries)

                def close(self) -> None:
                    self._entries.close()

            with patch.object(
                composition.os,
                "scandir",
                side_effect=lambda descriptor: _CountingEntries(descriptor),
            ):
                self.assertFalse(
                    composition._qualification_state_is_disposable(
                        root / "state.sqlite"
                    )
                )

            self.assertEqual(next_calls, 1)

    def test_disposable_state_check_fails_closed_when_descriptor_close_fails(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            real_close = os.close

            def close_then_fail(descriptor: int) -> None:
                real_close(descriptor)
                raise OSError("synthetic close failure")

            with patch.object(
                composition.os, "close", side_effect=close_then_fail
            ):
                self.assertFalse(
                    composition._qualification_state_is_disposable(
                        root / "state.sqlite"
                    )
                )

        with (
            tempfile.TemporaryDirectory() as input_directory,
            tempfile.TemporaryDirectory() as state_directory,
        ):
            inputs = Path(input_directory)
            inputs.chmod(0o700)
            state_root = Path(state_directory)
            state_root.chmod(0o700)
            config = self._enabled_config(inputs)
            query = inputs / "query.json"
            query.write_text(
                json.dumps(
                    {"isbn": "9780000000019", "title": "Synthetic Ebook Alpha"}
                ),
                encoding="utf-8",
            )
            query.chmod(0o600)
            database = state_root / "state.sqlite"

            def replaced_state(_database: Path):
                result = _run_state_check(database)
                original = database.stat(follow_symlinks=False)
                replacement = state_root / "replacement.sqlite"
                self.assertTrue(_run_state_check(replacement).succeeded)
                replacement.replace(database)
                return result, (original.st_dev, original.st_ino)

            with (
                patch(
                    "mechanical_librarian.bootstrap.composition._run_new_qualification_state_check",
                    side_effect=replaced_state,
                ),
                patch(
                    "mechanical_librarian.bootstrap.composition.PrivateFileProviderCredentials",
                    side_effect=AssertionError("secret reached"),
                ),
            ):
                report = _run_hardcover_identity_report(
                    config,
                    database,
                    query,
                    HardcoverExecutionPolicy.QUALIFICATION_ONE_SHOT,
                )
            self.assertEqual(
                report.outcome, HardcoverIdentityOutcome.PERSISTENCE_FAILURE
            )

    def test_one_shot_rejects_concurrent_population_before_credentials(self) -> None:
        with (
            tempfile.TemporaryDirectory() as input_directory,
            tempfile.TemporaryDirectory() as state_directory,
        ):
            inputs = Path(input_directory)
            inputs.chmod(0o700)
            state_root = Path(state_directory)
            state_root.chmod(0o700)
            config = self._enabled_config(inputs)
            query = inputs / "query.json"
            query.write_text(
                json.dumps(
                    {"isbn": "9780000000019", "title": "Synthetic Ebook Alpha"}
                ),
                encoding="utf-8",
            )
            query.chmod(0o600)
            database = state_root / "state.sqlite"
            real_scandir = os.scandir
            next_calls = 0
            close_calls = 0

            class _CountingEntries:
                def __init__(self, descriptor: int) -> None:
                    self._entries = real_scandir(descriptor)

                def __iter__(self):
                    return self

                def __next__(self):
                    nonlocal next_calls
                    next_calls += 1
                    return next(self._entries)

                def close(self) -> None:
                    nonlocal close_calls
                    close_calls += 1
                    self._entries.close()

            def populate_after_composition_admission(_database: Path) -> bool:
                for index in range(32):
                    (state_root / f"concurrent-entry-{index}").touch()
                return True

            with (
                patch.object(
                    composition,
                    "_qualification_state_is_disposable",
                    side_effect=populate_after_composition_admission,
                ),
                patch(
                    "mechanical_librarian.infrastructure.persistence.sqlite.os.scandir",
                    side_effect=lambda descriptor: _CountingEntries(descriptor),
                ),
                patch(
                    "mechanical_librarian.bootstrap.composition.PrivateFileProviderCredentials",
                    side_effect=AssertionError("credential reached"),
                ),
            ):
                report = _run_hardcover_identity_report(
                    config,
                    database,
                    query,
                    HardcoverExecutionPolicy.QUALIFICATION_ONE_SHOT,
                )

            self.assertEqual(
                report.outcome,
                HardcoverIdentityOutcome.PERSISTENCE_FAILURE,
            )
            self.assertEqual(next_calls, 1)
            self.assertEqual(close_calls, 1)

    def test_one_shot_translates_scan_allocation_failure_before_credentials(
        self,
    ) -> None:
        with (
            tempfile.TemporaryDirectory() as input_directory,
            tempfile.TemporaryDirectory() as state_directory,
        ):
            inputs = Path(input_directory)
            inputs.chmod(0o700)
            state_root = Path(state_directory)
            state_root.chmod(0o700)
            config = self._enabled_config(inputs)
            query = inputs / "query.json"
            query.write_text(
                json.dumps(
                    {"isbn": "9780000000019", "title": "Synthetic Ebook Alpha"}
                ),
                encoding="utf-8",
            )
            query.chmod(0o600)

            with (
                patch.object(
                    composition,
                    "_qualification_state_is_disposable",
                    return_value=True,
                ),
                patch(
                    "mechanical_librarian.infrastructure.persistence.sqlite.os.scandir",
                    side_effect=MemoryError("synthetic allocation failure"),
                ),
                patch(
                    "mechanical_librarian.bootstrap.composition.PrivateFileProviderCredentials",
                    side_effect=AssertionError("credential reached"),
                ),
            ):
                report = _run_hardcover_identity_report(
                    config,
                    state_root / "state.sqlite",
                    query,
                    HardcoverExecutionPolicy.QUALIFICATION_ONE_SHOT,
                )

            self.assertEqual(
                report.outcome,
                HardcoverIdentityOutcome.PERSISTENCE_FAILURE,
            )

    def test_normal_mode_binds_existing_state_check_to_final_gate_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            config = self._enabled_config(root)
            query = root / "query.json"
            query.write_text(
                json.dumps(
                    {"isbn": "9780000000019", "title": "Synthetic Ebook Alpha"}
                ),
                encoding="utf-8",
            )
            query.chmod(0o600)
            database = root / "state.sqlite"
            self.assertTrue(_run_state_check(database).succeeded)

            real_bound_check = composition._run_bound_existing_state_check

            def replace_after_check(_database: Path):
                result = real_bound_check(database)
                replacement = root / "replacement.sqlite"
                self.assertTrue(_run_state_check(replacement).succeeded)
                replacement.replace(database)
                return result

            with (
                patch(
                    "mechanical_librarian.bootstrap.composition._run_bound_existing_state_check",
                    side_effect=replace_after_check,
                ),
                patch(
                    "mechanical_librarian.bootstrap.composition.PrivateFileProviderCredentials",
                    side_effect=AssertionError("secret reached"),
                ),
            ):
                report = _run_hardcover_identity_report(
                    config, database, query, HardcoverExecutionPolicy.NORMAL
                )
            self.assertEqual(
                report.outcome, HardcoverIdentityOutcome.PERSISTENCE_FAILURE
            )

    def test_normal_mode_revalidates_newly_published_state_as_existing(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            config = self._enabled_config(root)
            query = root / "query.json"
            query.write_text(
                json.dumps(
                    {"isbn": "9780000000019", "title": "Synthetic Ebook Alpha"}
                ),
                encoding="utf-8",
            )
            query.chmod(0o600)
            database = root / "state.sqlite"
            with (
                patch(
                    "mechanical_librarian.bootstrap.composition._run_state_check",
                    wraps=_run_state_check,
                ) as state_check,
                patch(
                    "mechanical_librarian.bootstrap.composition._run_bound_existing_state_check",
                    wraps=composition._run_bound_existing_state_check,
                ) as bound_check,
            ):
                report = _run_hardcover_identity_report(
                    config, database, query, HardcoverExecutionPolicy.NORMAL
                )
            self.assertEqual(state_check.call_count, 1)
            self.assertEqual(bound_check.call_count, 1)
            self.assertTrue(database.is_file())
            self.assertEqual(
                report.outcome, HardcoverIdentityOutcome.CREDENTIAL_UNAVAILABLE
            )

    def test_normal_mode_revalidates_concurrent_initializer_winner(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            config = self._enabled_config(root)
            query = root / "query.json"
            query.write_text(
                json.dumps(
                    {"isbn": "9780000000019", "title": "Synthetic Ebook Alpha"}
                ),
                encoding="utf-8",
            )
            query.chmod(0o600)
            database = root / "state.sqlite"
            competitor = root / "competitor.sqlite"
            self.assertTrue(_run_state_check(competitor).succeeded)
            competitor_identity = competitor.stat()
            real_publish = composition.publish_staged_state

            def publish_after_competitor(staged: Path, target: Path) -> bool:
                competitor.replace(target)
                return real_publish(staged, target)

            with (
                patch(
                    "mechanical_librarian.bootstrap.composition.publish_staged_state",
                    side_effect=publish_after_competitor,
                ),
                patch(
                    "mechanical_librarian.bootstrap.composition._run_state_check",
                    wraps=_run_state_check,
                ) as state_check,
            ):
                report = _run_hardcover_identity_report(
                    config, database, query, HardcoverExecutionPolicy.NORMAL
                )
            self.assertEqual(state_check.call_count, 1)
            self.assertEqual(
                (database.stat().st_dev, database.stat().st_ino),
                (competitor_identity.st_dev, competitor_identity.st_ino),
            )
            self.assertEqual(
                report.outcome, HardcoverIdentityOutcome.CREDENTIAL_UNAVAILABLE
            )

    def test_credentialed_f09b_command_is_retired_before_runner(self) -> None:
        runner = Mock()
        errors = io.StringIO()
        code = main(
            [
                "hardcover-title-proposal-report",
                "--database",
                "/state.sqlite",
                "--secret-reference",
                "/provider-token",
            ],
            state_check_runner=Mock(),
            hardcover_title_proposal_runner=runner,
            stdout=io.StringIO(),
            stderr=errors,
        )
        self.assertEqual(code, 2)
        self.assertIn("hardcover-identity-report", errors.getvalue())
        runner.assert_not_called()

    def test_credentialed_f09b_abbreviations_are_retired_before_runner(self) -> None:
        for spelling in ("--secret-ref", "--secret-r", "--secret"):
            with self.subTest(spelling=spelling):
                runner = Mock()
                errors = io.StringIO()
                code = main(
                    [
                        "hardcover-title-proposal-report",
                        "--database",
                        "/state.sqlite",
                        spelling,
                        "/provider-token",
                    ],
                    state_check_runner=Mock(),
                    hardcover_title_proposal_runner=runner,
                    stdout=io.StringIO(),
                    stderr=errors,
                )
                self.assertEqual(code, 2)
                self.assertIn("hardcover-identity-report", errors.getvalue())
                runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
