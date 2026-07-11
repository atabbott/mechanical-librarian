from __future__ import annotations

from copy import deepcopy
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
    body_html: str = "",
) -> dict:
    all_nodes = all_issues or []
    manual_nodes = manual_issues or []
    return {
        "data": {
            "repository": {
                "nameWithOwner": REPOSITORY,
                "defaultBranchRef": {"name": "main"},
                "pullRequest": {
                    "isDraft": draft,
                    "baseRefName": "main",
                    "bodyHTML": body_html,
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
        self.assertEqual(evaluate_snapshot(snapshot(draft=True), REPOSITORY).code, "draft_exempt")
        self.assertFalse(evaluate_snapshot(snapshot(), REPOSITORY).allowed)

    def test_one_or_multiple_open_native_references_pass(self) -> None:
        for issues in ([issue(135)], [issue(135), issue(139)]):
            with self.subTest(issues=issues):
                result = evaluate_snapshot(snapshot(issues), REPOSITORY)
                self.assertTrue(result.allowed)
                self.assertEqual(result.code, "valid")

    def test_missing_nonexistent_examples_quotes_and_code_fail_as_unrecognized(self) -> None:
        for fixture in ("missing", "nonexistent", "example", "quote", "fenced-code"):
            with self.subTest(fixture=fixture):
                result = evaluate_snapshot(snapshot(), REPOSITORY)
                self.assertFalse(result.allowed)
                self.assertEqual(result.code, "missing_or_invalid_closing_reference")

    def test_closed_manual_and_foreign_references_fail_safely(self) -> None:
        cases = (
            (snapshot([issue(114, "CLOSED")]), "closed_issue_reference"),
            (snapshot([issue(135)], [issue(135)]), "manual_link_not_closing_reference"),
            (
                snapshot([issue(135, repository="another/repository")]),
                "foreign_repository_reference",
            ),
            (
                snapshot([issue(135), issue(114, "CLOSED")]),
                "closed_issue_reference",
            ),
        )
        for fixture, code in cases:
            with self.subTest(code=code):
                result = evaluate_snapshot(fixture, REPOSITORY)
                self.assertFalse(result.allowed)
                self.assertEqual(result.code, code)
                self.assertNotIn("title", result.message())

    def test_redundant_manual_link_can_be_disambiguated_by_a_bounded_body_line(self) -> None:
        valid = snapshot(
            [issue(135)],
            [issue(135)],
            body_html=(
                '<p><span class="issue-keyword">Closes</span> '
                '<a class="issue-link" href="/issues/135">#135</a></p>'
            ),
        )
        self.assertTrue(evaluate_snapshot(valid, REPOSITORY).allowed)
        for body_html in (
            "<p>Example: Closes #135</p>",
            "<blockquote><p>Closes #135</p></blockquote>",
            "<pre><code>Closes #135</code></pre>",
            "<p><code>Closes #135</code></p>",
            "<!--\nCloses #135\n-->",
            "<pre>\nCloses #135\n</pre>",
            '<p><a href="/issues/135">Closes #135</a></p>',
            "<p><strong>Closes #135</strong></p>",
            "<p>Closes <code>not-native</code>#135</p>",
        ):
            with self.subTest(body_html=body_html):
                result = evaluate_snapshot(
                    snapshot([issue(135)], [issue(135)], body_html=body_html), REPOSITORY
                )
                self.assertEqual(result.code, "manual_link_not_closing_reference")

    def test_edits_recompute_the_complete_snapshot(self) -> None:
        before = evaluate_snapshot(snapshot(), REPOSITORY)
        after = evaluate_snapshot(snapshot([issue(135)]), REPOSITORY)
        removed = evaluate_snapshot(snapshot(), REPOSITORY)
        self.assertFalse(before.allowed)
        self.assertTrue(after.allowed)
        self.assertFalse(removed.allowed)

    def test_non_default_base_and_incomplete_or_malformed_metadata_fail_closed(self) -> None:
        non_default = snapshot([issue(135)])
        non_default["data"]["repository"]["pullRequest"]["baseRefName"] = "release"
        truncated = snapshot([issue(135)])
        truncated["data"]["repository"]["pullRequest"]["allClosingIssues"][
            "totalCount"
        ] = 2
        too_many = snapshot([issue(135)])
        too_many["data"]["repository"]["pullRequest"]["allClosingIssues"][
            "totalCount"
        ] = 101
        too_many["data"]["repository"]["pullRequest"]["allClosingIssues"]["pageInfo"][
            "hasNextPage"
        ] = True
        malformed = snapshot([issue(135)])
        malformed["data"]["repository"]["pullRequest"]["allClosingIssues"]["nodes"] = [
            {"number": "135", "state": "OPEN", "repository": {"nameWithOwner": REPOSITORY}}
        ]
        mismatch = snapshot([issue(135)])
        mismatch["data"]["repository"]["nameWithOwner"] = "another/repository"
        cases = (
            (non_default, "unsupported_base_branch"),
            (truncated, "metadata_incomplete"),
            (malformed, "metadata_incomplete"),
            (mismatch, "metadata_repository_mismatch"),
            (too_many, "too_many_references"),
            ({}, "metadata_incomplete"),
        )
        for fixture, code in cases:
            with self.subTest(code=code):
                result = evaluate_snapshot(fixture, REPOSITORY)
                self.assertFalse(result.allowed)
                self.assertEqual(result.code, code)

    def test_duplicate_metadata_fails_closed_and_output_is_deterministic(self) -> None:
        duplicate = snapshot([issue(135), issue(135)])
        self.assertEqual(evaluate_snapshot(duplicate, REPOSITORY).code, "metadata_incomplete")
        result = evaluate_snapshot(snapshot([issue(139), issue(135)]), REPOSITORY)
        self.assertEqual(
            result.references,
            ("example/mechanical-librarian#135", "example/mechanical-librarian#139"),
        )

    def test_workflow_is_bounded_read_only_and_rechecks_body_and_ready_transitions(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "require-linked-issue.yml").read_text()
        self.assertIn("pull_request_target:", workflow)
        self.assertNotIn("\n  pull_request:\n", workflow)
        self.assertIn("types: [opened, edited, reopened, ready_for_review, synchronize]", workflow)
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
        self.assertIn("bodyHTML", workflow)


if __name__ == "__main__":
    unittest.main()
