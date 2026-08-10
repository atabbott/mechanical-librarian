from __future__ import annotations

import io
import hashlib
import json
import os
import subprocess
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from mechanical_librarian.interfaces.cli import main
from mechanical_librarian.bootstrap import composition

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "workbench"))

from mechanical_librarian_workbench import (  # noqa: E402
    CAPABILITIES,
    BatchRequest,
    CatalogueRequest,
    ContainedReviewerRequest,
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
        self.assertEqual(len(CAPABILITIES), 14)
        self.assertEqual(
            {operation for capability in CAPABILITIES for operation in capability.operations},
            set(WorkbenchOperations.__dataclass_fields__),
        )

    def test_default_bindings_are_the_shared_application_composition(self) -> None:
        operations = WorkbenchOperations.production_operations()
        for name in WorkbenchOperations.__dataclass_fields__:
            if name == "run_contained_reviewer":
                self.assertEqual(getattr(operations, name).__module__, "mechanical_librarian_workbench.reviewer")
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
            BatchRequest("preview"), BatchRequest("inspect"), BatchRequest("run", manifest_id=digest),
            DecisionPreviewRequest("case"),
            ContainedReviewerRequest(
                "invoke", instruction="reviewer-instruction.txt",
                input_document="reviewer-input.json",
                output_schema="reviewer-output-schema.json",
            ),
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
            results = tuple(execute(context, request, operations) for request in requests)
            self.assertEqual([item.name for item in results], [item.name for item in CAPABILITIES])
            self.assertEqual(len(tuple(context.path("attempts").iterdir())), 4)
            batch_artifact = context.path("artifacts/" + results[11].artifacts[0])
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

            context.require_network = bind_then_replace  # type: ignore[method-assign]
            operations = replace(_operations(), run_contained_reviewer=invoke)
            with mock.patch(
                "mechanical_librarian_workbench.context.os.fsync",
                side_effect=lambda descriptor: (
                    fsync_calls.append(descriptor), real_fsync(descriptor)
                )[1],
            ):
                execute(context, request, operations)

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
            ProposalRequest("inspect", None)  # type: ignore[arg-type]

    def test_operation_failures_are_redacted(self) -> None:
        def fail(*_args: object) -> str:
            raise RuntimeError("token=/private/secret")

        operations = replace(_operations(), reopen_metadata_report=fail)
        with WorkbenchContext.temporary() as context:
            with self.assertRaisesRegex(WorkbenchFailure, "scenario failed") as caught:
                execute(context, ReportRequest("inspect", report_id="report"), operations)
        self.assertNotIn("private", str(caught.exception))

    def test_network_profile_rejects_production_shaped_configuration(self) -> None:
        documents = (
            '{"connections":[{"endpoint":"https://library.example.com"}]}',
            '{"connections":[{"endpoint":"http://127.0.0.1:8765",'
            '"secret_file":"/run/secrets/production"}]}',
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

    def test_workbench_is_not_production_source(self) -> None:
        self.assertFalse((ROOT / "src" / "mechanical_librarian_workbench").exists())


if __name__ == "__main__":
    unittest.main()
