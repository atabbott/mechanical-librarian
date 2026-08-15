from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from mechanical_librarian.application.configuration import ConfigurationError
from mechanical_librarian.bootstrap import composition
from mechanical_librarian.interfaces.cli import main

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "workbench"))

from mechanical_librarian_workbench import (  # type: ignore[import-not-found]  # noqa: E402  # reason: the workbench package is added to sys.path at import time above; the gate's mypy_path covers src/ only.
    CAPABILITIES,
    BatchRequest,
    CatalogueRequest,
    ContainedReviewerRequest,
    CoverIdentityRequest,
    CoverObservationRequest,
    CoverPolicyRequest,
    DecisionPreviewRequest,
    IsolatedNetworkProfile,
    ProposalRequest,
    ReportRequest,
    ReviewCaseRequest,
    WorkbenchContext,
    WorkbenchFailure,
    WorkbenchOperations,
    WorkbenchResult,
    execute,
    scenarios,  # noqa: E402
)

from mechanical_librarian.adapters.reviewers.contained_reviewer import (  # noqa: E402
    ContainedReviewerProcessError,
)
from mechanical_librarian.application.contained_reviewer_contract import (  # noqa: E402
    ContainedReviewerAttempt,
)


def _operation(*_args: object) -> str:
    return '{"ok":true}'


def _operations() -> WorkbenchOperations:
    return WorkbenchOperations(*(_operation for _ in WorkbenchOperations.__dataclass_fields__))


def _network_files(context: WorkbenchContext) -> tuple[str, ...]:
    config = context.path("config.json")
    config.write_text(
        '{"connections":[{"endpoint":"http://127.0.0.1:8765"}]}',
        encoding="utf-8",
    )
    profile = context.path("contained-reviewer-profile.json")
    profile.write_text("{}", encoding="utf-8")
    profile.chmod(0o600)
    context.path("reviewer-instruction.txt").write_text("Summarize the input.", encoding="utf-8")
    context.path("reviewer-input.json").write_text('{"value":"synthetic"}', encoding="utf-8")
    context.path("reviewer-output-schema.json").write_text('{"$schema":"https://json-schema.org/draft/2020-12/schema","additionalProperties":false,"properties":{"summary":{"type":"string"}},"required":["summary"],"type":"object"}', encoding="utf-8")
    context.path("batch.json").write_text("{}", encoding="utf-8")
    return tuple(sorted({
        hashlib.sha256(context.path(name).read_bytes()).hexdigest()
        for name in ("config.json", "contained-reviewer-profile.json", "reviewer-instruction.txt",
                     "reviewer-input.json", "reviewer-output-schema.json", "batch.json")
    }))


class RuntimeWorkbenchTests(unittest.TestCase):
    def test_runtime_exposes_only_review_server(self) -> None:
        errors = io.StringIO()
        self.assertEqual(main(("metadata-report",), stderr=errors), 2)
        self.assertEqual(errors.getvalue(), "Review server failed: invalid invocation.\n")

    def test_workbench_catalogue_maps_every_relocated_capability(self) -> None:
        self.assertEqual(len(CAPABILITIES), 19)
        self.assertEqual(
            {operation for capability in CAPABILITIES for operation in capability.operations},
            set(WorkbenchOperations.__dataclass_fields__),
        )

    def test_default_bindings_are_the_shared_application_composition(self) -> None:
        operations = WorkbenchOperations.production_operations()
        for name in WorkbenchOperations.__dataclass_fields__:
            if name == "run_contained_reviewer":
                self.assertEqual(
                    "scripts.workbench_contained_reviewer",
                    getattr(operations, name).__module__,
                )
                self.assertFalse(hasattr(composition, name))
            else:
                self.assertIs(getattr(operations, name), getattr(composition, name))

    def test_every_catalogued_scenario_executes_through_typed_requests(self) -> None:
        digest = "a" * 64
        requests = (
            ReportRequest("observe", connection="primary", library="books", item="item"),
            ReportRequest("inspect", report_id="report"),
            ReportRequest("cover", report_id="report", artifact_id="artifact"),
            ProposalRequest("create", "report"), ProposalRequest("inspect", "proposal"),
            ReviewCaseRequest("admit", "proposal"), ReviewCaseRequest("inspect", "case"),
            CatalogueRequest("refresh", connection="primary"), CatalogueRequest("list"),
            CatalogueRequest(
                "get", connection="primary", library="books",
                library_id="library-id", selector="item",
            ),
            BatchRequest("preview"), BatchRequest("inspect"), BatchRequest("run", manifest_id=digest),
            DecisionPreviewRequest("case"),
            ContainedReviewerRequest(
                "invoke", instruction="reviewer-instruction.txt",
                input_document="reviewer-input.json",
                output_schema="reviewer-output-schema.json",
            ),
            CoverIdentityRequest("report"),
            CoverObservationRequest("report"),
            CoverPolicyRequest("report"),
        )
        with WorkbenchContext.temporary() as context:
            context.network = IsolatedNetworkProfile(
                "isolated-test", True, True, _network_files(context)
            )

            def batch(*arguments: object) -> bool:
                emit = arguments[-1]
                assert callable(emit)
                emit({"kind": "item", "status": "completed"})
                emit({"kind": "summary", "status": "completed"})
                return True

            operations = replace(_operations(), run_batch=batch)
            prepared = mock.Mock(document="{}", images=())
            classified = mock.Mock(document='{"ok":true}')
            observed = mock.Mock(document='{"ok":true}')
            adjudicated = mock.Mock(document='{"ok":true}')
            with (
                mock.patch(
                    "mechanical_librarian_workbench.scenarios.prepare_cover_packet",
                    return_value=prepared,
                ),
                mock.patch(
                    "mechanical_librarian_workbench.scenarios.classify_cover_identity",
                    return_value=classified,
                ),
                mock.patch(
                    "mechanical_librarian_workbench.scenarios.observe_cover_packet",
                    return_value=observed,
                ),
                mock.patch(
                    "mechanical_librarian_workbench.scenarios.adjudicate_cover_policy",
                    return_value=adjudicated,
                ),
            ):
                results = tuple(execute(context, request, operations) for request in requests)
            self.assertEqual(
                [item.name for item in results],
                [
                    item.name for item in CAPABILITIES
                    if item.name != "cover-packet-review"
                ],
            )
            self.assertEqual(len(tuple(context.path("attempts").iterdir())), 7)
            batch_artifact = context.path("artifacts/" + results[12].artifacts[0])
            self.assertEqual(
                json.loads(batch_artifact.read_text(encoding="utf-8")),
                {
                    "completed": True,
                    "events": [
                        {"kind": "item", "status": "completed"},
                        {"kind": "summary", "status": "completed"},
                    ],
                },
            )

    def test_cover_policy_scenario_redacts_invalid_evidence(self) -> None:
        prepared = mock.Mock(document="{}", images=())
        classified = mock.Mock(document='{"ok":true}')
        with WorkbenchContext.temporary() as context:
            with (
                mock.patch(
                    "mechanical_librarian_workbench.scenarios.prepare_cover_packet",
                    return_value=prepared,
                ),
                mock.patch(
                    "mechanical_librarian_workbench.scenarios.classify_cover_identity",
                    return_value=classified,
                ),
                mock.patch(
                    "mechanical_librarian_workbench.scenarios.adjudicate_cover_policy",
                    side_effect=ValueError("/private/hostile"),
                ),
            ):
                with self.assertRaises(WorkbenchFailure) as caught:
                    execute(context, CoverPolicyRequest("report"), _operations())
        self.assertEqual(caught.exception.category, "invalid")
        self.assertNotIn("private", str(caught.exception))

    def test_cover_policy_binds_authority_scoped_observer_profile(self) -> None:
        prepared = mock.Mock(document="{}", images=())
        decision = mock.Mock(document='{"status":"safe"}')
        with WorkbenchContext.temporary() as context:
            profile = context.path("contained-reviewer-profile.json")
            profile.write_bytes(b'{"profile":"bound"}')
            profile.chmod(0o600)
            with (
                mock.patch(
                    "mechanical_librarian_workbench.scenarios.prepare_cover_packet",
                    return_value=prepared,
                ),
                mock.patch(
                    "mechanical_librarian_workbench.scenarios.adjudicate_cover_policy",
                    return_value=decision,
                ) as adjudicate,
            ):
                execute(
                    context,
                    CoverPolicyRequest("report", observation_document="{}"),
                    _operations(),
                )
        self.assertEqual(
            adjudicate.call_args.kwargs["profile_payload"],
            b'{"profile":"bound"}',
        )

    def test_observer_profile_read_is_bound_to_one_open_descriptor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = root / "profile.json"
            replacement = root / "replacement.json"
            profile.write_bytes(b'{"profile":"original"}')
            replacement.write_bytes(b'{"profile":"replacement"}')
            profile.chmod(0o600)
            replacement.chmod(0o600)
            original_open = os.open

            def replace_after_open(path: Path, flags: int):
                descriptor = original_open(path, flags)
                if path == profile:
                    os.replace(replacement, profile)
                return descriptor

            with mock.patch.object(os, "open", side_effect=replace_after_open):
                payload = scenarios._read_observer_profile(profile)

        self.assertEqual(payload, b'{"profile":"original"}')
        self.assertFalse(replacement.exists())

    def test_batch_failed_completion_and_events_are_preserved(self) -> None:
        def batch(*arguments: object) -> bool:
            emit = arguments[-1]
            assert callable(emit)
            emit({"kind": "summary", "status": "failed"})
            return False

        with WorkbenchContext.temporary() as context:
            context.network = IsolatedNetworkProfile(
                "isolated-test", True, True, _network_files(context)
            )
            result = execute(
                context,
                BatchRequest("run", manifest_id="a" * 64),
                replace(_operations(), run_batch=batch),
            )
            payload = json.loads(
                context.path("artifacts/" + result.artifacts[0]).read_text(encoding="utf-8")
            )
        self.assertEqual(payload, {
            "completed": False,
            "events": [{"kind": "summary", "status": "failed"}],
        })
        self.assertEqual(result.data["status"], "failed")
        self.assertIn("batch-run failed", result.summary)

    def test_contained_reviewer_uses_admitted_bytes_after_durable_attempt(self) -> None:
        request = ContainedReviewerRequest(
            "invoke",
            instruction="reviewer-instruction.txt",
            input_document="reviewer-input.json",
            output_schema="reviewer-output-schema.json",
        )
        with WorkbenchContext.temporary() as context:
            context.network = IsolatedNetworkProfile(
                "isolated-test", True, True, _network_files(context)
            )
            expected = tuple(
                context.path(name).read_bytes()
                for name in (
                    "contained-reviewer-profile.json",
                    "reviewer-instruction.txt",
                    "reviewer-input.json",
                    "reviewer-output-schema.json",
                )
            )
            original_require_network = context.require_network
            fsync_calls: list[int] = []
            real_fsync = os.fsync

            def bind_then_replace(
                *paths: Path,
                private_first: bool = False,
            ) -> tuple[bytes, ...]:
                bound = original_require_network(
                    *paths,
                    private_first=private_first,
                )
                for path in paths:
                    path.write_bytes(b'"replaced after admission"')
                return bound

            def invoke(*payloads: object) -> str:
                self.assertGreaterEqual(len(fsync_calls), 2)
                self.assertEqual(
                    (expected[0], "invoke", expected[1], expected[2], expected[3]),
                    payloads,
                )
                return '{"ok":true}'

            context.require_network = bind_then_replace
            operations = replace(_operations(), run_contained_reviewer=invoke)
            with mock.patch(
                "mechanical_librarian_workbench.context.os.fsync",
                side_effect=lambda descriptor: (
                    fsync_calls.append(descriptor), real_fsync(descriptor)  # type: ignore[func-returns-value]  # reason: the tuple deliberately records the call and yields element [1]; append's None is discarded.
                )[1],
            ):
                execute(context, request, operations)

    def test_network_operations_execute_exact_admitted_snapshots(self) -> None:
        cases = (
            (ReportRequest("observe", connection="primary", library="books", item="item"), "run_metadata_report"),
            (ProposalRequest("create", "report"), "create_proposal_set"),
            (ReviewCaseRequest("admit", "proposal"), "admit_review_case"),
            (CatalogueRequest("refresh", connection="primary"), "run_catalogue"),
        )
        for request, operation_name in cases:
            with self.subTest(operation=operation_name), WorkbenchContext.temporary() as context:
                secret = context.path("secret.txt")
                secret.write_bytes(b"admitted credential")
                secret.chmod(0o600)
                config = context.path("config.json")
                config.write_text(
                    json.dumps(
                        {
                            "connections": [{
                                "endpoint": "http://127.0.0.1:8765",
                                "secret_file": str(secret),
                            }]
                        },
                        separators=(",", ":"),
                    ),
                    encoding="utf-8",
                )
                context.network = IsolatedNetworkProfile(
                    "isolated-test", True, True,
                    tuple(sorted({
                        hashlib.sha256(config.read_bytes()).hexdigest(),
                        hashlib.sha256(secret.read_bytes()).hexdigest(),
                    })),
                )
                original_require_network = context.require_network

                def bind_then_replace(
                    *paths: Path,
                    private_first: bool = False,
                    private_tail: int = 0,
                    first_payload: bytes | None = None,
                ) -> tuple[bytes, ...]:
                    bound = original_require_network(  # noqa: B023 - closure is created and consumed within this iteration
                        *paths,
                        private_first=private_first,
                        private_tail=private_tail,
                        first_payload=first_payload,
                    )
                    config.write_bytes(b'{"connections":[{"endpoint":"https://live.invalid"}]}')  # noqa: B023 - closure is created and consumed within this iteration
                    secret.write_bytes(b"replacement credential")  # noqa: B023 - closure is created and consumed within this iteration
                    return bound

                def operation(config_path: Path, *_args: object) -> str:
                    self.assertNotEqual(config_path, config)  # noqa: B023 - closure is created and consumed within this iteration
                    execution = json.loads(config_path.read_text(encoding="utf-8"))
                    connection = execution["connections"][0]
                    self.assertEqual(
                        connection["endpoint"], "http://127.0.0.1:8765"
                    )
                    execution_secret = Path(connection["secret_file"])
                    self.assertNotEqual(execution_secret, secret)  # noqa: B023 - closure is created and consumed within this iteration
                    self.assertEqual(
                        execution_secret.read_bytes(), b"admitted credential"
                    )
                    return '{"ok":true}'

                context.require_network = bind_then_replace
                execute(
                    context,
                    request,
                    replace(_operations(), **{operation_name: operation}),
                )

        with WorkbenchContext.temporary() as context:
            secret = context.path("secret.txt")
            secret.write_bytes(b"admitted credential")
            secret.chmod(0o600)
            config = context.path("config.json")
            config.write_text(
                json.dumps(
                    {
                        "connections": [{
                            "endpoint": "http://127.0.0.1:8765",
                            "secret_file": str(secret),
                        }]
                    },
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            manifest = context.path("batch.json")
            manifest.write_bytes(b"{}")
            context.network = IsolatedNetworkProfile(
                "isolated-test", True, True,
                tuple(sorted({
                    hashlib.sha256(config.read_bytes()).hexdigest(),
                    hashlib.sha256(manifest.read_bytes()).hexdigest(),
                    hashlib.sha256(secret.read_bytes()).hexdigest(),
                })),
            )
            expected_manifest = manifest.read_bytes()
            original_require_network = context.require_network

            def bind_batch_then_replace(
                *paths: Path,
                private_first: bool = False,
                private_tail: int = 0,
                first_payload: bytes | None = None,
            ) -> tuple[bytes, ...]:
                bound = original_require_network(
                    *paths,
                    private_first=private_first,
                    private_tail=private_tail,
                    first_payload=first_payload,
                )
                config.write_bytes(b'"replaced"')
                manifest.write_bytes(b'"replaced"')
                secret.write_bytes(b"replacement credential")
                return bound

            def run_batch(config_path: Path, _catalogue: Path, manifest_path: Path,
                          *_args: object) -> bool:
                self.assertEqual(manifest_path.read_bytes(), expected_manifest)
                execution = json.loads(config_path.read_text(encoding="utf-8"))
                execution_secret = Path(
                    execution["connections"][0]["secret_file"]
                )
                self.assertEqual(
                    execution_secret.read_bytes(), b"admitted credential"
                )
                return True

            context.require_network = bind_batch_then_replace
            execute(
                context,
                BatchRequest("run", manifest_id="a" * 64),
                replace(_operations(), run_batch=run_batch),
            )

    def test_result_has_equivalent_canonical_and_summary_rendering(self) -> None:
        digest = "a" * 64
        artifact = "workbench_artifact_" + digest
        result = WorkbenchResult(
            "catalogue-list",
            f"catalogue-list completed; artifact={artifact}; sha256={digest}; bytes=11",
            {"artifact_bytes": "11", "artifact_sha256": digest, "status": "completed"},
            (artifact,),
        )
        document = json.loads(result.render("json"))
        self.assertEqual(result.render("summary"), result.summary)
        self.assertEqual(document["name"], "catalogue-list")
        self.assertEqual(document["data"], result.data)
        self.assertIn(document["artifacts"][0], result.summary)
        self.assertIn(document["data"]["artifact_sha256"], result.summary)

    def test_context_refuses_production_traversal_and_unapproved_inputs(self) -> None:
        with self.assertRaises(PermissionError):
            WorkbenchContext(Path("/data"))
        with self.assertRaises(PermissionError):
            WorkbenchContext(Path("/data/workbench"))
        with self.assertRaises(PermissionError):
            WorkbenchContext.temporary().require_network()
        with self.assertRaises(PermissionError):
            WorkbenchContext(ROOT / ".tmp", input_class="private-corpus")
        with WorkbenchContext.temporary() as context:
            with self.assertRaises(ValueError):
                context.path("../escape")

    def test_action_specific_inputs_are_required(self) -> None:
        with self.assertRaises(ValueError):
            ReportRequest("observe")
        with self.assertRaises(ValueError):
            BatchRequest("run")
        with self.assertRaises(ValueError):
            CatalogueRequest("refresh")
        with self.assertRaises(ValueError):
            ProposalRequest("inspect", None)
        exact = {
            "connection": "primary",
            "library": "books",
            "library_id": "library-id",
            "selector": "item-id",
        }
        for missing in exact:
            values = {**exact, missing: None}
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                CatalogueRequest("get", **values)

    def test_proposal_and_case_currentness_require_network_authority(self) -> None:
        for request in (
            ProposalRequest("create", "report"),
            ReviewCaseRequest("admit", "proposal"),
        ):
            with self.subTest(request=request), WorkbenchContext.temporary() as context:
                _network_files(context)
                with self.assertRaises(WorkbenchFailure) as caught:
                    execute(context, request, _operations())
                self.assertEqual(caught.exception.category, "refused")
                self.assertFalse(context.path("attempts").exists())

    def test_operation_failures_are_redacted(self) -> None:
        def fail(*_args: object) -> str:
            raise RuntimeError("token=/private/secret")

        operations = replace(_operations(), reopen_metadata_report=fail)
        with WorkbenchContext.temporary() as context:
            with self.assertRaisesRegex(WorkbenchFailure, "scenario failed") as caught:
                execute(context, ReportRequest("inspect", report_id="report"), operations)
        self.assertNotIn("private", str(caught.exception))

    def test_contained_terminal_attempt_evidence_crosses_redacting_boundary(self) -> None:
        attempts = (
            ContainedReviewerAttempt(True, "local_cli_abort", 19, "a" * 64),
            ContainedReviewerAttempt(True),
        )

        def fail(*_args: object) -> str:
            raise ContainedReviewerProcessError("runtime", attempts=attempts)

        request = ContainedReviewerRequest(
            "invoke",
            instruction="reviewer-instruction.txt",
            input_document="reviewer-input.json",
            output_schema="reviewer-output-schema.json",
        )
        with WorkbenchContext.temporary() as context:
            context.network = IsolatedNetworkProfile(
                "isolated-test", True, True, _network_files(context)
            )
            operations = replace(_operations(), run_contained_reviewer=fail)
            with self.assertRaisesRegex(WorkbenchFailure, "scenario failed") as caught:
                execute(context, request, operations)
        self.assertEqual(caught.exception.attempts, attempts)
        self.assertNotIn("local_cli_abort", str(caught.exception))
        with self.assertRaises(ValueError):
            WorkbenchFailure("failed", attempts=(object(),))

    def test_operation_contract_json_preserves_application_field_order(self) -> None:
        raw = '[{"connection":"primary","item":"item","eligible":true}]'
        operations = replace(_operations(), run_catalogue=lambda *_args: raw)
        with WorkbenchContext.temporary() as context:
            result = execute(context, CatalogueRequest("list"), operations)
            artifact = context.path("artifacts/" + result.artifacts[0])
            self.assertEqual(artifact.read_text(encoding="utf-8"), raw)

    def test_runtime_configuration_failure_has_its_typed_exit(self) -> None:
        def fail(*_args: object) -> None:
            raise ConfigurationError("private configuration detail")

        errors = io.StringIO()
        result = main(
            (
                "review-serve", "--config", "config.json",
                "--database", "reports.sqlite", "--catalogue", "catalogue.sqlite",
                "--artifacts", "artifacts",
            ),
            review_serve_runner=fail,
            stderr=errors,
        )
        self.assertEqual(result, 3)
        self.assertEqual(
            errors.getvalue(),
            "Review server failed: configuration unavailable.\n",
        )

    def test_network_profile_rejects_production_shaped_configuration(self) -> None:
        documents = (
            '{"connections":[{"endpoint":"https://library.example.com"}]}',
            '{"connections":[{"endpoint":"http://127.0.0.1:8765",'
            '"secret_file":"/run/secrets/production"}]}',
            '{"connections":[{"endpoint":"http://127.0.0.1:8765",'
            '"secret_file":"secret.txt"}]}',
            '{"connections":[{"endpoint":"http://127.0.0.1:8765"}],'
            '"providers":[{"enabled":true}]}',
        )
        for document in documents:
            with self.subTest(document=document), WorkbenchContext.temporary() as context:
                config = context.path("config.json")
                config.write_text(document, encoding="utf-8")
                digest = hashlib.sha256(config.read_bytes()).hexdigest()
                context.network = IsolatedNetworkProfile(
                    "isolated-test", True, True, (digest,)
                )
                with self.assertRaises(WorkbenchFailure) as caught:
                    execute(
                        context,
                        ReportRequest(
                            "observe", connection="primary", library="books", item="item"
                        ),
                        _operations(),
                    )
                self.assertEqual(caught.exception.category, "refused")

        with WorkbenchContext.temporary() as context:
            secret = context.path("exposed.secret")
            secret.write_bytes(b"synthetic")
            secret.chmod(0o644)
            config = context.path("config.json")
            config.write_text(
                json.dumps({
                    "connections": [{
                        "endpoint": "http://127.0.0.1:8765",
                        "secret_file": str(secret),
                    }]
                }),
                encoding="utf-8",
            )
            context.network = IsolatedNetworkProfile(
                "isolated-test", True, True,
                tuple(sorted({
                    hashlib.sha256(config.read_bytes()).hexdigest(),
                    hashlib.sha256(secret.read_bytes()).hexdigest(),
                })),
            )
            with self.assertRaises(WorkbenchFailure) as caught:
                execute(
                    context,
                    ReportRequest(
                        "observe", connection="primary", library="books", item="item"
                    ),
                    _operations(),
                )
            self.assertEqual(caught.exception.category, "refused")

    def test_isolated_configuration_is_bounded_before_json_decode(self) -> None:
        with WorkbenchContext.temporary() as context:
            config = context.path("config.json")
            config.write_bytes(b"{" + b" " * 1_048_576 + b"}")
            with mock.patch(
                "mechanical_librarian_workbench.context.json.loads",
                side_effect=AssertionError("decoder must not be reached"),
            ) as loads, self.assertRaises(PermissionError):
                context.require_isolated_configuration(config)
            loads.assert_not_called()

    def test_persistent_workspace_and_artifacts_are_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as parent:
            workspace = Path(parent) / "persistent"
            context = WorkbenchContext(workspace, input_class="test")
            self.assertEqual(workspace.stat().st_mode & 0o777, 0o700)
            artifact_id, _ = context.store_artifact(b"private")
            artifact = context.path("artifacts") / artifact_id
            self.assertEqual(context.path("artifacts").stat().st_mode & 0o777, 0o700)
            self.assertEqual(artifact.stat().st_mode & 0o777, 0o600)
            config = context.path("config.json")
            config.write_text(
                '{"connections":[{"endpoint":"http://127.0.0.1:8765"}]}',
                encoding="utf-8",
            )
            payload, private = context.require_isolated_configuration(config)
            context.network = IsolatedNetworkProfile(
                "isolated-test", True, True,
                (hashlib.sha256(payload).hexdigest(),),
            )
            bound = context.require_network(config, *private, first_payload=payload)
            snapshot = context.authority_snapshot(bound[0])
            attempt = next(context.path("attempts").iterdir())
            self.assertEqual(context.path("attempts").stat().st_mode & 0o777, 0o700)
            self.assertEqual(attempt.stat().st_mode & 0o777, 0o600)
            self.assertEqual(context.path("authority").stat().st_mode & 0o777, 0o700)
            self.assertEqual(snapshot.stat().st_mode & 0o777, 0o600)

            artifact.chmod(0o644)
            with self.assertRaises(PermissionError):
                context.store_artifact(b"private")
            artifact.chmod(0o600)
            artifact.write_bytes(b"changed")
            with self.assertRaises(PermissionError):
                context.store_artifact(b"private")

            unsafe_artifacts = Path(parent) / "unsafe-artifacts"
            unsafe_artifacts.mkdir(mode=0o700)
            unsafe_context = WorkbenchContext(unsafe_artifacts, input_class="test")
            (unsafe_artifacts / "artifacts").mkdir(mode=0o755)
            with self.assertRaises(PermissionError):
                unsafe_context.store_artifact(b"private")

            unsafe_workspace = Path(parent) / "unsafe-workspace"
            unsafe_workspace.mkdir(mode=0o755)
            with self.assertRaises(PermissionError):
                WorkbenchContext(unsafe_workspace, input_class="test")

    def test_repeated_large_result_artifact_reuses_exact_private_file(self) -> None:
        payload = b"x" * 1_048_577
        with WorkbenchContext.temporary() as context:
            first = context.store_artifact(payload)
            second = context.store_artifact(payload)
            self.assertEqual(second, first)
            artifact = context.path("artifacts") / first[0]
            self.assertEqual(artifact.stat().st_size, len(payload))
            self.assertEqual(artifact.stat().st_mode & 0o777, 0o600)

    def test_authority_snapshot_write_failure_does_not_poison_retry(self) -> None:
        payload = b"admitted authority"
        with WorkbenchContext.temporary() as context:
            real_write = os.write
            writes = 0

            def fail_after_prefix(descriptor: int, remaining: bytes) -> int:
                nonlocal writes
                writes += 1
                if writes == 1:
                    return real_write(descriptor, remaining[:1])
                raise OSError("synthetic write failure")

            with mock.patch(
                "mechanical_librarian_workbench.context.os.write",
                side_effect=fail_after_prefix,
            ):
                with self.assertRaises(PermissionError):
                    context.authority_snapshot(payload)

            snapshot = context.authority_snapshot(payload)
            self.assertEqual(snapshot.read_bytes(), payload)
            self.assertEqual(
                tuple(context.path("authority").iterdir()),
                (snapshot,),
            )

    def test_batch_accepts_exact_schema_maximum_authority_files(self) -> None:
        with WorkbenchContext.temporary() as context:
            connections: list[dict[str, str]] = []
            authority_paths: list[Path] = []
            for index in range(32):
                secret = context.path(f"secret-{index}.txt")
                secret.write_bytes(f"secret-{index}".encode("utf-8"))
                secret.chmod(0o600)
                authority_paths.append(secret)
                connections.append(
                    {
                        "endpoint": "http://127.0.0.1:8765",
                        "secret_file": str(secret),
                    }
                )
            review: dict[str, str] = {
                "owner_password_hash_file": "",
                "tls_certificate_file": "",
                "tls_private_key_file": "",
            }
            for field in tuple(review):
                authority = context.path(f"{field}.txt")
                authority.write_bytes(field.encode("utf-8"))
                authority.chmod(0o600)
                authority_paths.append(authority)
                review[field] = str(authority)
            config = context.path("config.json")
            config.write_text(
                json.dumps(
                    {"connections": connections, "review": review},
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            manifest = context.path("batch.json")
            manifest.write_bytes(b"{}")
            context.network = IsolatedNetworkProfile(
                "isolated-test",
                True,
                True,
                tuple(
                    sorted(
                        hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in (config, manifest, *authority_paths)
                    )
                ),
            )

            result = execute(
                context,
                BatchRequest("run", manifest_id="a" * 64),
                replace(_operations(), run_batch=lambda *_args: True),
            )

            self.assertEqual(result.data["status"], "completed")
            attempt = json.loads(
                next(context.path("attempts").iterdir()).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(len(attempt["input_sha256"]), 37)

        with self.assertRaises(ValueError):
            IsolatedNetworkProfile(
                "isolated-test",
                True,
                True,
                tuple(f"{index:064x}" for index in range(38)),
            )

    def test_documented_persistent_workspace_is_ignored(self) -> None:
        completed = subprocess.run(
            ["git", "check-ignore", "--quiet", ".workbench/report/artifacts/private"],
            cwd=ROOT,
            check=False,
        )
        self.assertEqual(completed.returncode, 0)

    def test_temporary_workspace_is_removed(self) -> None:
        with WorkbenchContext.temporary() as context:
            workspace = context.workspace
            context.store_artifact(b"{}")
            self.assertTrue(workspace.exists())
        self.assertFalse(workspace.exists())

    def test_temporary_cleanup_ignores_mutated_public_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as parent:
            unrelated = Path(parent) / "unrelated"
            unrelated.mkdir()
            context = WorkbenchContext.temporary()
            original = context.workspace
            context.workspace = unrelated
            context.close()
            self.assertFalse(original.exists())
            self.assertTrue(unrelated.exists())

    def test_workbench_is_not_production_source(self) -> None:
        self.assertFalse((ROOT / "src" / "mechanical_librarian_workbench").exists())


if __name__ == "__main__":
    unittest.main()
