from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github" / "scripts" / "validate_linked_issues.py"
SPEC = importlib.util.spec_from_file_location("validate_linked_issues", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
evaluate_snapshot = MODULE.evaluate_snapshot


REPOSITORY = "example/mechanical-librarian"


def issue(number: int, state: str = "OPEN", repository: str = REPOSITORY) -> dict:
    return {
        "number": number,
        "state": state,
        "repository": {"nameWithOwner": repository},
    }


def snapshot(
    all_issues: list[dict] | None = None,
    manual_issues: list[dict] | None = None,
    *,
    draft: bool = False,
    body: str = "",
    labels: list[str] | None = None,
) -> dict:
    all_nodes = all_issues or []
    manual_nodes = manual_issues or []
    label_nodes = [{"name": name} for name in (labels or [])]
    return {
        "data": {
            "repository": {
                "nameWithOwner": REPOSITORY,
                "defaultBranchRef": {"name": "main"},
                "pullRequest": {
                    "isDraft": draft,
                    "baseRefName": "main",
                    "body": body,
                    "labels": {
                        "totalCount": len(label_nodes),
                        "pageInfo": {"hasNextPage": False},
                        "nodes": label_nodes,
                    },
                    "allClosingIssues": {
                        "totalCount": len(all_nodes),
                        "pageInfo": {"hasNextPage": False},
                        "nodes": all_nodes,
                    },
                    "manualClosingIssues": {
                        "totalCount": len(manual_nodes),
                        "pageInfo": {"hasNextPage": False},
                        "nodes": manual_nodes,
                    },
                },
            }
        }
    }


class LinkedIssueGateTests(unittest.TestCase):
    def test_drafts_are_exempt_until_ready(self) -> None:
        self.assertEqual(
            evaluate_snapshot(snapshot(draft=True), REPOSITORY).code, "draft_exempt"
        )
        self.assertFalse(evaluate_snapshot(snapshot(), REPOSITORY).allowed)

    def test_one_or_multiple_open_native_references_pass(self) -> None:
        for issues in ([issue(135)], [issue(135), issue(139)]):
            with self.subTest(issues=issues):
                result = evaluate_snapshot(snapshot(issues), REPOSITORY)
                self.assertTrue(result.allowed)
                self.assertEqual(result.code, "valid")

    def test_exact_raw_closing_lines_disambiguate_manual_overlap(self) -> None:
        for line in (
            "Closes #135",
            "fixes: #135",
            "RESOLVED #135",
            "  Closed #135  ",
        ):
            with self.subTest(line=line):
                result = evaluate_snapshot(
                    snapshot([issue(135)], [issue(135)], body=line), REPOSITORY
                )
                self.assertTrue(result.allowed)
                self.assertEqual(result.references, (f"{REPOSITORY}#135",))

    def test_raw_line_supports_multiple_closing_references(self) -> None:
        result = evaluate_snapshot(
            snapshot(
                [issue(135), issue(139)],
                [issue(135), issue(139)],
                body="Closes #135, #139",
            ),
            REPOSITORY,
        )
        self.assertTrue(result.allowed)
        self.assertEqual(
            result.references, (f"{REPOSITORY}#135", f"{REPOSITORY}#139")
        )

    def test_comments_quotes_fenced_and_code_examples_do_not_disambiguate(self) -> None:
        for body in (
            "Example: Closes #135",
            "> Closes #135",
            "```\nCloses #135\n```",
            "~~~markdown\nCloses #135\n~~~",
            "`Closes #135`",
            "<!-- Closes #135 -->",
            "<!--\nCloses #135\n-->",
            "Closes `#135`",
            "Closes #135 because it is an example",
        ):
            with self.subTest(body=body):
                result = evaluate_snapshot(
                    snapshot([issue(135)], [issue(135)], body=body), REPOSITORY
                )
                self.assertFalse(result.allowed)
                self.assertEqual(result.code, "manual_link_not_closing_reference")

    def test_manual_overlap_preserves_repository_identity(self) -> None:
        foreign_same_number = issue(135, repository="another/repository")
        result = evaluate_snapshot(
            snapshot(
                [issue(135), foreign_same_number],
                [foreign_same_number],
                body="Closes #135",
            ),
            REPOSITORY,
        )
        self.assertTrue(result.allowed)
        self.assertEqual(result.references, (f"{REPOSITORY}#135",))

    def test_closed_and_foreign_native_references_fail_safely(self) -> None:
        for fixture, code in (
            (snapshot([issue(114, "CLOSED")]), "closed_issue_reference"),
            (
                snapshot([issue(135, repository="another/repository")]),
                "foreign_repository_reference",
            ),
            (snapshot([issue(135), issue(114, "CLOSED")]), "closed_issue_reference"),
        ):
            with self.subTest(code=code):
                result = evaluate_snapshot(fixture, REPOSITORY)
                self.assertFalse(result.allowed)
                self.assertEqual(result.code, code)
                self.assertNotIn("title", result.message())

    def test_exact_label_is_the_exemption_authorization(self) -> None:
        result = evaluate_snapshot(
            snapshot(body="## Tracking exemption\nVisible human rationale.", labels=["tracking-exempt"]),
            REPOSITORY,
        )
        self.assertTrue(result.allowed)
        self.assertEqual(result.code, "tracking_exempt")
        for label in ("Tracking-Exempt", "tracking_exempt", "tracking-exempt "):
            with self.subTest(label=label):
                result = evaluate_snapshot(snapshot(labels=[label]), REPOSITORY)
                self.assertFalse(result.allowed)
                self.assertEqual(result.code, "missing_or_invalid_closing_reference")

    def test_exemption_does_not_bypass_native_closed_or_foreign_references(self) -> None:
        for referenced, code in (
            ([issue(198, "CLOSED")], "closed_issue_reference"),
            ([issue(198, repository="another/repository")], "foreign_repository_reference"),
        ):
            with self.subTest(code=code):
                result = evaluate_snapshot(
                    snapshot(referenced, labels=["tracking-exempt"]), REPOSITORY
                )
                self.assertEqual(result.code, code)

    def test_current_failure_diagnostics_are_preserved(self) -> None:
        cases = (
            (snapshot(), "missing_or_invalid_closing_reference"),
            (snapshot([issue(135)], [issue(135)]), "manual_link_not_closing_reference"),
        )
        for fixture, code in cases:
            with self.subTest(code=code):
                result = evaluate_snapshot(fixture, REPOSITORY)
                self.assertFalse(result.allowed)
                self.assertEqual(result.code, code)

    def test_malformed_or_unsupported_metadata_fails_closed(self) -> None:
        non_default = snapshot([issue(135)])
        non_default["data"]["repository"]["pullRequest"]["baseRefName"] = "release"
        oversized_body = snapshot()
        oversized_body["data"]["repository"]["pullRequest"]["body"] = "x" * 262_145
        too_many = snapshot([issue(135)])
        too_many["data"]["repository"]["pullRequest"]["allClosingIssues"]["totalCount"] = 101
        too_many["data"]["repository"]["pullRequest"]["allClosingIssues"]["pageInfo"]["hasNextPage"] = True
        truncated = snapshot([issue(135)])
        truncated["data"]["repository"]["pullRequest"]["allClosingIssues"]["totalCount"] = 2
        malformed = snapshot([issue(135)])
        malformed["data"]["repository"]["pullRequest"]["allClosingIssues"]["nodes"] = [{
            "number": "135", "state": "OPEN", "repository": {"nameWithOwner": REPOSITORY}
        }]
        mismatch = snapshot([issue(135)])
        mismatch["data"]["repository"]["nameWithOwner"] = "another/repository"
        duplicate = snapshot([issue(135), issue(135)])
        for fixture, code in (
            (non_default, "unsupported_base_branch"),
            (oversized_body, "metadata_incomplete"),
            (too_many, "too_many_references"),
            (truncated, "metadata_incomplete"),
            (malformed, "metadata_incomplete"),
            (mismatch, "metadata_repository_mismatch"),
            (duplicate, "metadata_incomplete"),
            ({}, "metadata_incomplete"),
        ):
            with self.subTest(code=code):
                self.assertEqual(evaluate_snapshot(fixture, REPOSITORY).code, code)

    def test_label_metadata_is_bounded_unique_and_well_formed(self) -> None:
        malformed = snapshot(labels=["tracking-exempt"])
        malformed["data"]["repository"]["pullRequest"]["labels"]["nodes"] = [{"name": 12}]
        duplicate = snapshot(labels=["tracking-exempt", "tracking-exempt"])
        paginated = snapshot(labels=["tracking-exempt"])
        paginated["data"]["repository"]["pullRequest"]["labels"]["pageInfo"]["hasNextPage"] = True
        truncated = snapshot(labels=["tracking-exempt"])
        truncated["data"]["repository"]["pullRequest"]["labels"]["totalCount"] = 2
        too_many = snapshot(labels=[f"label-{number}" for number in range(100)])
        too_many["data"]["repository"]["pullRequest"]["labels"]["totalCount"] = 101
        for fixture, code in (
            (malformed, "metadata_incomplete"),
            (duplicate, "metadata_incomplete"),
            (paginated, "too_many_labels"),
            (truncated, "metadata_incomplete"),
            (too_many, "too_many_labels"),
        ):
            with self.subTest(code=code):
                self.assertEqual(evaluate_snapshot(fixture, REPOSITORY).code, code)

    def test_output_is_deterministic(self) -> None:
        result = evaluate_snapshot(snapshot([issue(139), issue(135)]), REPOSITORY)
        self.assertEqual(
            result.references, (f"{REPOSITORY}#135", f"{REPOSITORY}#139")
        )

    def test_workflow_contract_is_bounded_and_read_only(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "require-linked-issue.yml").read_text()
        self.assertIn("pull_request_target:", workflow)
        self.assertNotIn("\n  pull_request:\n", workflow)
        self.assertIn("types: [opened, edited, reopened, ready_for_review, synchronize, labeled, unlabeled]", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("issues: read", workflow)
        self.assertIn("pull-requests: read", workflow)
        self.assertNotIn("permissions: write", workflow)
        self.assertIn("timeout-minutes: 2", workflow)
        self.assertIn("actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("ref: ${{ github.event.pull_request.base.sha }}", workflow)
        self.assertIn("userLinkedOnly:true", workflow)
        self.assertIn("first:100", workflow)
        self.assertIn("pageInfo{hasNextPage}", workflow)
        self.assertIn(" body ", workflow)
        self.assertNotIn("bodyHTML", workflow)
        self.assertIn("labels(first:100)", workflow)
        self.assertNotIn("github.event.pull_request.head", workflow)
        self.assertIn("group: linked-issue-${{ github.event.pull_request.number }}", workflow)
        self.assertIn("cancel-in-progress: true", workflow)


if __name__ == "__main__":
    unittest.main()
