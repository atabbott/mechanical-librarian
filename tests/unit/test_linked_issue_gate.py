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
                    "bodyHTML": body_html,
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
    TRACKING_RATIONALE = (
        "<h2>Tracking exemption</h2>"
        "<p>Corrects one misspelling without changing behavior.</p>"
    )

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

    def test_open_local_issue_remains_normal_with_exemption_content(self) -> None:
        result = evaluate_snapshot(
            snapshot(
                [issue(198)],
                body_html=self.TRACKING_RATIONALE,
                labels=["tracking-exempt"],
            ),
            REPOSITORY,
        )
        self.assertTrue(result.allowed)
        self.assertEqual(result.code, "valid")
        self.assertEqual(result.references, (f"{REPOSITORY}#198",))

    def test_tracked_reference_ignores_exemption_label_metadata(self) -> None:
        excessive = snapshot([issue(198)])
        excessive_labels = excessive["data"]["repository"]["pullRequest"]["labels"]
        excessive_labels["totalCount"] = 101
        excessive_labels["pageInfo"]["hasNextPage"] = True

        malformed = snapshot([issue(198)])
        malformed["data"]["repository"]["pullRequest"]["labels"]["nodes"] = [
            {"name": 12}
        ]
        for fixture in (excessive, malformed):
            labels = fixture["data"]["repository"]["pullRequest"]["labels"]
            with self.subTest(labels=labels):
                result = evaluate_snapshot(fixture, REPOSITORY)
                self.assertTrue(result.allowed)
                self.assertEqual(result.code, "valid")

    def test_exact_label_and_visible_top_level_rationale_allow_exemption(self) -> None:
        result = evaluate_snapshot(
            snapshot(
                body_html=self.TRACKING_RATIONALE,
                labels=["tracking-exempt"],
            ),
            REPOSITORY,
        )
        self.assertTrue(result.allowed)
        self.assertEqual(result.code, "tracking_exempt")
        self.assertEqual(result.references, ())
        self.assertNotIn("misspelling", result.message())

    def test_label_matching_is_exact_and_case_sensitive(self) -> None:
        for label in ("Tracking-Exempt", "tracking_exempt", "tracking-exempt "):
            with self.subTest(label=label):
                result = evaluate_snapshot(
                    snapshot(body_html=self.TRACKING_RATIONALE, labels=[label]),
                    REPOSITORY,
                )
                self.assertFalse(result.allowed)
                self.assertEqual(result.code, "tracking_exemption_label_missing")

    def test_exemption_requires_both_authorization_and_rationale(self) -> None:
        label_only = evaluate_snapshot(snapshot(labels=["tracking-exempt"]), REPOSITORY)
        rationale_only = evaluate_snapshot(
            snapshot(body_html=self.TRACKING_RATIONALE), REPOSITORY
        )
        self.assertEqual(label_only.code, "tracking_exemption_rationale_missing")
        self.assertEqual(rationale_only.code, "tracking_exemption_label_missing")

    def test_non_visible_or_ambiguous_rationale_fails_closed(self) -> None:
        invalid = (
            "<h2>Tracking exemption</h2>",
            "<h2>Tracking exemption</h2><p>   </p>",
            "<h2>Tracking exemption</h2><p>\u200b\u2060</p>",
            "<h2>Tracking exemption</h2><p>.</p>",
            "<h2>Tracking exemption</h2><p>-</p>",
            "<h2>Tracking exemption</h2><p>© ⚙️ 🚀</p>",
            "<h2>Tracking exemption</h2><p><br></p>",
            "<h2>Tracking exemption</h2><!-- rationale -->",
            "<h2>Tracking exemption</h2><p><code>comment cleanup</code></p>",
            "<h2>Tracking exemption</h2><pre>comment cleanup</pre>",
            (
                "<h2>Tracking exemption</h2>"
                "<blockquote><p>comment cleanup</p></blockquote>"
            ),
            (
                "<blockquote><h2>Tracking exemption</h2>"
                "<p>comment cleanup</p></blockquote>"
            ),
            "<div><h2>Tracking exemption</h2><p>comment cleanup</p></div>",
            "<h2>Tracking exemption</h2><div><p>comment cleanup</p></div>",
            (
                "<h2>Tracking exemption</h2><p>first</p>"
                "<h2>Tracking exemption</h2><p>second</p>"
            ),
            "<h2>Tracking exemption</h2><script>comment cleanup</script>",
            "<h2>Tracking exemption</h2><style>p { color: red; }</style>",
        )
        for body_html in invalid:
            with self.subTest(body_html=body_html):
                result = evaluate_snapshot(
                    snapshot(body_html=body_html, labels=["tracking-exempt"]),
                    REPOSITORY,
                )
                self.assertFalse(result.allowed)
                self.assertEqual(
                    result.code, "tracking_exemption_rationale_missing"
                )

    def test_standard_void_element_inside_visible_rationale_is_supported(self) -> None:
        body_html = (
            "<h2>Tracking exemption</h2>"
            "<p>Corrects typo.<br>\nNo behavior change.</p>"
        )
        result = evaluate_snapshot(
            snapshot(body_html=body_html, labels=["tracking-exempt"]), REPOSITORY
        )
        self.assertTrue(result.allowed)
        self.assertEqual(result.code, "tracking_exempt")

    def test_other_top_level_sections_do_not_supply_exemption_prose(self) -> None:
        body_html = (
            "<h2>Tracking exemption</h2>"
            "<h2>Verification</h2><p>Tests pass.</p>"
        )
        result = evaluate_snapshot(
            snapshot(body_html=body_html, labels=["tracking-exempt"]), REPOSITORY
        )
        self.assertEqual(result.code, "tracking_exemption_rationale_missing")

    def test_invalid_body_examples_fail_as_unrecognized(self) -> None:
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

    def test_exemption_cannot_bypass_foreign_or_closed_references(self) -> None:
        for referenced, code in (
            ([issue(198, "CLOSED")], "closed_issue_reference"),
            (
                [issue(198, repository="another/repository")],
                "foreign_repository_reference",
            ),
        ):
            with self.subTest(code=code):
                result = evaluate_snapshot(
                    snapshot(
                        referenced,
                        body_html=self.TRACKING_RATIONALE,
                        labels=["tracking-exempt"],
                    ),
                    REPOSITORY,
                )
                self.assertFalse(result.allowed)
                self.assertEqual(result.code, code)

    def test_body_line_disambiguates_redundant_manual_link(self) -> None:
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
                    snapshot([issue(135)], [issue(135)], body_html=body_html),
                    REPOSITORY,
                )
                self.assertEqual(result.code, "manual_link_not_closing_reference")

    def test_manual_overlap_preserves_repository_identity(self) -> None:
        body_html = (
            '<p><span class="issue-keyword">Closes</span> '
            '<a class="issue-link" href="/issues/135">#135</a></p>'
        )
        foreign_same_number = issue(135, repository="another/repository")
        same_number_result = evaluate_snapshot(
            snapshot(
                [issue(135), foreign_same_number],
                [foreign_same_number],
                body_html=body_html,
            ),
            REPOSITORY,
        )
        self.assertTrue(same_number_result.allowed)
        self.assertEqual(
            same_number_result.references,
            ("example/mechanical-librarian#135",),
        )

        foreign_other_number = issue(139, repository="another/repository")
        different_number_result = evaluate_snapshot(
            snapshot(
                [issue(135), foreign_other_number],
                [foreign_other_number],
                body_html=body_html,
            ),
            REPOSITORY,
        )
        self.assertTrue(different_number_result.allowed)
        self.assertEqual(
            different_number_result.references,
            ("example/mechanical-librarian#135",),
        )

        local_manual = issue(135)
        foreign_native_result = evaluate_snapshot(
            snapshot(
                [foreign_same_number, local_manual],
                [local_manual],
                body_html=body_html,
            ),
            REPOSITORY,
        )
        self.assertFalse(foreign_native_result.allowed)
        self.assertEqual(foreign_native_result.code, "foreign_repository_reference")
        self.assertEqual(
            foreign_native_result.references,
            ("another/repository#135",),
        )

        local_overlap_result = evaluate_snapshot(
            snapshot([local_manual], [local_manual], body_html=body_html),
            REPOSITORY,
        )
        self.assertTrue(local_overlap_result.allowed)
        self.assertEqual(
            local_overlap_result.references,
            ("example/mechanical-librarian#135",),
        )

        foreign_manual_result = evaluate_snapshot(
            snapshot(
                [foreign_same_number],
                [foreign_same_number],
                body_html=body_html,
            ),
            REPOSITORY,
        )
        self.assertFalse(foreign_manual_result.allowed)
        self.assertEqual(
            foreign_manual_result.code, "manual_link_not_closing_reference"
        )

    def test_all_closing_keywords_disambiguate_native_manual_overlap(self) -> None:
        keywords = (
            "close",
            "closes",
            "closed",
            "fix",
            "fixes",
            "fixed",
            "resolve",
            "resolves",
            "resolved",
        )
        for keyword in keywords:
            for separator in (" ", ": "):
                with self.subTest(keyword=keyword, separator=separator):
                    body_html = (
                        f'<p><span class="issue-keyword">{keyword}</span>{separator}'
                        '<a class="issue-link" href="/issues/135">#135</a></p>'
                    )
                    result = evaluate_snapshot(
                        snapshot([issue(135)], [issue(135)], body_html=body_html),
                        REPOSITORY,
                    )
                    self.assertTrue(result.allowed)
                    self.assertEqual(result.code, "valid")

        for keyword in ("CLOSE", "FIXED", "RESOLVED"):
            with self.subTest(keyword=keyword, case="uppercase"):
                body_html = (
                    f'<p><span class="issue-keyword">{keyword}</span>: '
                    '<a class="issue-link" href="/issues/135">#135</a></p>'
                )
                self.assertTrue(
                    evaluate_snapshot(
                        snapshot([issue(135)], [issue(135)], body_html=body_html),
                        REPOSITORY,
                    ).allowed
                )

    def test_colon_separator_and_context_variants_remain_fail_closed(self) -> None:
        invalid_body_html = (
            '<p><span class="issue-keyword">Closes</span>; '
            '<a class="issue-link" href="/issues/135">#135</a></p>',
            '<p><span class="issue-keyword">Closes</span>:: '
            '<a class="issue-link" href="/issues/135">#135</a></p>',
            '<p><span class="issue-keyword">Closes</span> : '
            '<a class="issue-link" href="/issues/135">#135</a></p>',
            '<p><span class="issue-keyword">Closes</span>:'
            '<a class="issue-link" href="/issues/135">#135</a></p>',
            '<p><span class="issue-keyword">Closes</span>: example '
            '<a class="issue-link" href="/issues/135">#135</a></p>',
            '<p><span class="issue-keyword">Closes</span><em>:</em> '
            '<a class="issue-link" href="/issues/135">#135</a></p>',
            '<p><span class="issue-keyword">Closes</span><!-- example -->: '
            '<a class="issue-link" href="/issues/135">#135</a></p>',
            '<p><span class="issue-keyword">Closes</span>: '
            '<a href="/issues/135">#135</a></p>',
            '<blockquote><p><span class="issue-keyword">Closes</span>: '
            '<a class="issue-link" href="/issues/135">#135</a></p></blockquote>',
            '<p><code><span class="issue-keyword">Closes</span>: '
            '<a class="issue-link" href="/issues/135">#135</a></code></p>',
            '<p><span class="issue-keyword">closing</span>: '
            '<a class="issue-link" href="/issues/135">#135</a></p>',
        )
        for body_html in invalid_body_html:
            with self.subTest(body_html=body_html):
                result = evaluate_snapshot(
                    snapshot([issue(135)], [issue(135)], body_html=body_html),
                    REPOSITORY,
                )
                self.assertEqual(result.code, "manual_link_not_closing_reference")

    def test_edits_recompute_the_complete_snapshot(self) -> None:
        before = evaluate_snapshot(snapshot(), REPOSITORY)
        after = evaluate_snapshot(snapshot([issue(135)]), REPOSITORY)
        removed = evaluate_snapshot(snapshot(), REPOSITORY)
        self.assertFalse(before.allowed)
        self.assertTrue(after.allowed)
        self.assertFalse(removed.allowed)

    def test_malformed_or_unsupported_metadata_fails_closed(self) -> None:
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
            {
                "number": "135",
                "state": "OPEN",
                "repository": {"nameWithOwner": REPOSITORY},
            }
        ]
        mismatch = snapshot([issue(135)])
        mismatch["data"]["repository"]["nameWithOwner"] = "another/repository"
        oversized_body = snapshot()
        oversized_body["data"]["repository"]["pullRequest"]["bodyHTML"] = (
            "x" * 262_145
        )
        cases = (
            (non_default, "unsupported_base_branch"),
            (truncated, "metadata_incomplete"),
            (malformed, "metadata_incomplete"),
            (mismatch, "metadata_repository_mismatch"),
            (too_many, "too_many_references"),
            (oversized_body, "metadata_incomplete"),
            ({}, "metadata_incomplete"),
        )
        for fixture, code in cases:
            with self.subTest(code=code):
                result = evaluate_snapshot(fixture, REPOSITORY)
                self.assertFalse(result.allowed)
                self.assertEqual(result.code, code)

    def test_duplicate_metadata_fails_closed_and_output_is_deterministic(self) -> None:
        duplicate = snapshot([issue(135), issue(135)])
        self.assertEqual(
            evaluate_snapshot(duplicate, REPOSITORY).code, "metadata_incomplete"
        )
        result = evaluate_snapshot(snapshot([issue(139), issue(135)]), REPOSITORY)
        self.assertEqual(
            result.references,
            ("example/mechanical-librarian#135", "example/mechanical-librarian#139"),
        )

    def test_label_metadata_is_bounded_unique_and_well_formed(self) -> None:
        malformed = snapshot(labels=["tracking-exempt"])
        malformed["data"]["repository"]["pullRequest"]["labels"]["nodes"] = [
            {"name": 12}
        ]
        duplicate = snapshot(labels=["tracking-exempt", "tracking-exempt"])
        truncated = snapshot(labels=["tracking-exempt"])
        truncated["data"]["repository"]["pullRequest"]["labels"]["totalCount"] = 2
        paginated = snapshot(labels=["tracking-exempt"])
        paginated["data"]["repository"]["pullRequest"]["labels"]["pageInfo"][
            "hasNextPage"
        ] = True
        too_many = snapshot(labels=[f"label-{number}" for number in range(100)])
        too_many["data"]["repository"]["pullRequest"]["labels"]["totalCount"] = 101
        for fixture, code in (
            (malformed, "metadata_incomplete"),
            (duplicate, "metadata_incomplete"),
            (truncated, "metadata_incomplete"),
            (paginated, "too_many_labels"),
            (too_many, "too_many_labels"),
        ):
            with self.subTest(code=code):
                result = evaluate_snapshot(fixture, REPOSITORY)
                self.assertFalse(result.allowed)
                self.assertEqual(result.code, code)

    def test_workflow_contract_is_bounded_and_read_only(self) -> None:
        workflow_path = ROOT / ".github" / "workflows" / "require-linked-issue.yml"
        workflow = workflow_path.read_text()
        self.assertIn("pull_request_target:", workflow)
        self.assertNotIn("\n  pull_request:\n", workflow)
        self.assertIn(
            "types: [opened, edited, reopened, ready_for_review, synchronize, "
            "labeled, unlabeled]",
            workflow,
        )
        self.assertIn("contents: read", workflow)
        self.assertIn("issues: read", workflow)
        self.assertIn("pull-requests: read", workflow)
        self.assertNotIn("permissions: write", workflow)
        self.assertIn("timeout-minutes: 2", workflow)
        self.assertIn(
            "actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5",
            workflow,
        )
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("ref: ${{ github.event.pull_request.base.sha }}", workflow)
        self.assertIn("userLinkedOnly:true", workflow)
        self.assertIn("first:100", workflow)
        self.assertIn("pageInfo{hasNextPage}", workflow)
        self.assertIn("bodyHTML", workflow)
        self.assertIn("labels(first:100)", workflow)
        self.assertIn("nodes{name}", workflow)
        self.assertIn("concurrency:", workflow)
        self.assertIn(
            "group: linked-issue-${{ github.event.pull_request.number }}", workflow
        )
        self.assertIn("cancel-in-progress: true", workflow)
        self.assertNotIn("github.event.pull_request.head", workflow)


if __name__ == "__main__":
    unittest.main()
