from __future__ import annotations

import io
import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path

from mechanical_librarian.interfaces.cli import main
from mechanical_librarian.bootstrap import composition

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "workbench"))

from mechanical_librarian_workbench import (  # noqa: E402
    CAPABILITIES,
    BatchRequest,
    CatalogueRequest,
    DecisionPreviewRequest,
    IsolatedNetworkProfile,
    ProposalRequest,
    ReportRequest,
    ReviewCaseRequest,
    ReviewerAuthRequest,
    ReviewerRequest,
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


class RuntimeWorkbenchTests(unittest.TestCase):
    def test_runtime_exposes_only_review_server(self) -> None:
        errors = io.StringIO()
        self.assertEqual(main(("metadata-report",), stderr=errors), 2)
        self.assertEqual(errors.getvalue(), "Review server failed: invalid invocation.\n")

    def test_workbench_catalogue_maps_every_relocated_capability(self) -> None:
        self.assertEqual(len(CAPABILITIES), 18)
        self.assertEqual(
            {operation for capability in CAPABILITIES for operation in capability.operations},
            set(WorkbenchOperations.__dataclass_fields__),
        )

    def test_default_bindings_are_the_shared_application_composition(self) -> None:
        operations = WorkbenchOperations.production_operations()
        for name in WorkbenchOperations.__dataclass_fields__:
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
            DecisionPreviewRequest("case"), ReviewerRequest("packet-preview", "case"),
            ReviewerRequest("request", "case"), ReviewerRequest("inspect", "request"),
            ReviewerRequest("reevaluate", "request"), ReviewerAuthRequest("status"),
        )
        with WorkbenchContext.temporary() as context:
            context.network = IsolatedNetworkProfile("isolated-test", True, True)
            results = tuple(execute(context, request, _operations()) for request in requests)
            self.assertEqual([item.name for item in results], [item.name for item in CAPABILITIES])
            self.assertEqual(len(tuple(context.path("attempts").iterdir())), 6)

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
