from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

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


def test_drafts_are_exempt_until_ready() -> None:
    assert evaluate_snapshot(snapshot(draft=True), REPOSITORY).code == "draft_exempt"
    assert not evaluate_snapshot(snapshot(), REPOSITORY).allowed


@pytest.mark.parametrize(
    "issues",
    ([issue(135)], [issue(135), issue(139)]),
    ids=("one_reference", "two_references"),
)
def test_one_or_multiple_open_native_references_pass(issues: list[dict]) -> None:
    result = evaluate_snapshot(snapshot(issues), REPOSITORY)
    assert result.allowed
    assert result.code == "valid"


@pytest.mark.parametrize(
    "line",
    (
        "Closes #135",
        "fixes: #135",
        "RESOLVED #135",
        "  Closed #135  ",
    ),
    ids=("closes", "fixes_colon", "resolved_uppercase", "padded_closed"),
)
def test_exact_raw_closing_lines_disambiguate_manual_overlap(line: str) -> None:
    result = evaluate_snapshot(
        snapshot([issue(135)], [issue(135)], body=line), REPOSITORY
    )
    assert result.allowed
    assert result.references == (f"{REPOSITORY}#135",)


def test_raw_line_requires_a_keyword_for_every_closing_reference() -> None:
    bare_reference = evaluate_snapshot(
        snapshot(
            [issue(135), issue(139)],
            [issue(135), issue(139)],
            body="Closes #135, #139",
        ),
        REPOSITORY,
    )
    assert bare_reference.code == "manual_link_not_closing_reference"

    result = evaluate_snapshot(
        snapshot(
            [issue(135), issue(139)],
            [issue(135), issue(139)],
            body="Closes #135, closes #139",
        ),
        REPOSITORY,
    )
    assert result.allowed
    assert result.references == (f"{REPOSITORY}#135", f"{REPOSITORY}#139")


@pytest.mark.parametrize(
    "body",
    (
        "Closes #135, fixes #139",
        "- Closes #135",
        "1. Resolves #135",
    ),
    ids=("repeated_keywords", "bullet_item", "numbered_item"),
)
def test_raw_line_supports_repeated_keywords_and_list_items(body: str) -> None:
    result = evaluate_snapshot(
        snapshot(
            [issue(135), issue(139)],
            [issue(135), issue(139)],
            body=body,
        ),
        REPOSITORY,
    )
    assert result.allowed


def test_longer_fence_stays_open_until_a_valid_closer() -> None:
    result = evaluate_snapshot(
        snapshot(
            [issue(135)],
            [issue(135)],
            body="````\n```\nCloses #135\n````",
        ),
        REPOSITORY,
    )
    assert result.code == "manual_link_not_closing_reference"

    result = evaluate_snapshot(
        snapshot(
            [issue(135)],
            [issue(135)],
            body="```\nexample\n````\nCloses #135",
        ),
        REPOSITORY,
    )
    assert result.allowed


@pytest.mark.parametrize(
    "body",
    (
        "Example: Closes #135",
        "> Closes #135",
        "```\nCloses #135\n```",
        "~~~markdown\nCloses #135\n~~~",
        "`Closes #135`",
        "<!-- Closes #135 -->",
        "<!--\nCloses #135\n-->",
        "Closes `#135`",
        "Closes #135 because it is an example",
    ),
    ids=(
        "example_prefix",
        "block_quote",
        "backtick_fence",
        "tilde_fence",
        "inline_code",
        "one_line_comment",
        "multiline_comment",
        "quoted_number",
        "trailing_prose",
    ),
)
def test_comments_quotes_fenced_and_code_examples_do_not_disambiguate(
    body: str,
) -> None:
    result = evaluate_snapshot(
        snapshot([issue(135)], [issue(135)], body=body), REPOSITORY
    )
    assert not result.allowed
    assert result.code == "manual_link_not_closing_reference"


def test_comment_delimiters_are_scanned_across_each_line() -> None:
    result = evaluate_snapshot(
        snapshot(
            [issue(135)],
            [issue(135)],
            body="<!-- note --> <!--\nCloses #135\n-->",
        ),
        REPOSITORY,
    )
    assert result.code == "manual_link_not_closing_reference"


@pytest.mark.parametrize(
    "body",
    (
        "```\n<!--\n```\nCloses #135",
        "    <!--\nCloses #135",
    ),
    ids=("fenced_comment_opener", "indented_comment_opener"),
)
def test_comments_inside_code_do_not_poison_later_closing_lines(body: str) -> None:
    result = evaluate_snapshot(
        snapshot([issue(135)], [issue(135)], body=body), REPOSITORY
    )
    assert result.allowed


@pytest.mark.parametrize("marker", ("```", "~~~"), ids=("backtick", "tilde"))
def test_fence_markers_inside_comments_do_not_open_fence_state(marker: str) -> None:
    result = evaluate_snapshot(
        snapshot(
            [issue(135)],
            [issue(135)],
            body=f"<!--\n{marker}\n-->\nCloses #135",
        ),
        REPOSITORY,
    )
    assert result.allowed


def test_manual_overlap_preserves_repository_identity() -> None:
    foreign_same_number = issue(135, repository="another/repository")
    result = evaluate_snapshot(
        snapshot(
            [issue(135), foreign_same_number],
            [foreign_same_number],
            body="Closes #135",
        ),
        REPOSITORY,
    )
    assert result.allowed
    assert result.references == (f"{REPOSITORY}#135",)


@pytest.mark.parametrize(
    ("fixture", "code"),
    (
        (snapshot([issue(114, "CLOSED")]), "closed_issue_reference"),
        (
            snapshot([issue(135, repository="another/repository")]),
            "foreign_repository_reference",
        ),
        (snapshot([issue(135), issue(114, "CLOSED")]), "closed_issue_reference"),
    ),
    ids=("closed", "foreign", "open_beside_closed"),
)
def test_closed_and_foreign_native_references_fail_safely(
    fixture: dict, code: str
) -> None:
    result = evaluate_snapshot(fixture, REPOSITORY)
    assert not result.allowed
    assert result.code == code
    assert "title" not in result.message()


@pytest.mark.parametrize(
    "label",
    ("Tracking-Exempt", "tracking_exempt", "tracking-exempt "),
    ids=("capitalized", "underscored", "trailing_space"),
)
def test_exact_label_is_the_exemption_authorization(label: str) -> None:
    result = evaluate_snapshot(
        snapshot(body="## Tracking exemption\nVisible human rationale.", labels=["tracking-exempt"]),
        REPOSITORY,
    )
    assert result.allowed
    assert result.code == "tracking_exempt"
    result = evaluate_snapshot(snapshot(labels=[label]), REPOSITORY)
    assert not result.allowed
    assert result.code == "missing_or_invalid_closing_reference"


@pytest.mark.parametrize(
    ("referenced", "code"),
    (
        ([issue(198, "CLOSED")], "closed_issue_reference"),
        ([issue(198, repository="another/repository")], "foreign_repository_reference"),
    ),
    ids=("closed", "foreign"),
)
def test_exemption_does_not_bypass_native_closed_or_foreign_references(
    referenced: list[dict], code: str
) -> None:
    result = evaluate_snapshot(
        snapshot(referenced, labels=["tracking-exempt"]), REPOSITORY
    )
    assert result.code == code


@pytest.mark.parametrize(
    ("fixture", "code"),
    (
        (snapshot(), "missing_or_invalid_closing_reference"),
        (snapshot([issue(135)], [issue(135)]), "manual_link_not_closing_reference"),
    ),
    ids=("no_reference", "manual_link_only"),
)
def test_current_failure_diagnostics_are_preserved(fixture: dict, code: str) -> None:
    result = evaluate_snapshot(fixture, REPOSITORY)
    assert not result.allowed
    assert result.code == code


def _unsupported_metadata_cases() -> dict[str, tuple[dict, str]]:
    """Each malformed or unsupported snapshot, named by what it breaks."""

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
    return {
        "non_default_branch": (non_default, "unsupported_base_branch"),
        "oversized_body": (oversized_body, "metadata_incomplete"),
        "too_many_references": (too_many, "too_many_references"),
        "truncated_references": (truncated, "metadata_incomplete"),
        "malformed_reference": (malformed, "metadata_incomplete"),
        "repository_mismatch": (mismatch, "metadata_repository_mismatch"),
        "duplicate_reference": (duplicate, "metadata_incomplete"),
        "empty_document": ({}, "metadata_incomplete"),
    }


_UNSUPPORTED_METADATA = _unsupported_metadata_cases()


@pytest.mark.parametrize(
    ("fixture", "code"),
    tuple(_UNSUPPORTED_METADATA.values()),
    ids=tuple(_UNSUPPORTED_METADATA),
)
def test_malformed_or_unsupported_metadata_fails_closed(
    fixture: dict, code: str
) -> None:
    assert evaluate_snapshot(fixture, REPOSITORY).code == code


def _label_metadata_cases() -> dict[str, tuple[dict, str]]:
    """Each unusable label payload, named by what it breaks."""

    malformed = snapshot(labels=["tracking-exempt"])
    malformed["data"]["repository"]["pullRequest"]["labels"]["nodes"] = [{"name": 12}]
    duplicate = snapshot(labels=["tracking-exempt", "tracking-exempt"])
    paginated = snapshot(labels=["tracking-exempt"])
    paginated["data"]["repository"]["pullRequest"]["labels"]["pageInfo"]["hasNextPage"] = True
    truncated = snapshot(labels=["tracking-exempt"])
    truncated["data"]["repository"]["pullRequest"]["labels"]["totalCount"] = 2
    too_many = snapshot(labels=[f"label-{number}" for number in range(100)])
    too_many["data"]["repository"]["pullRequest"]["labels"]["totalCount"] = 101
    return {
        "malformed_name": (malformed, "metadata_incomplete"),
        "duplicate_label": (duplicate, "metadata_incomplete"),
        "paginated_labels": (paginated, "too_many_labels"),
        "truncated_labels": (truncated, "metadata_incomplete"),
        "too_many_labels": (too_many, "too_many_labels"),
    }


_LABEL_METADATA = _label_metadata_cases()


@pytest.mark.parametrize(
    ("fixture", "code"),
    tuple(_LABEL_METADATA.values()),
    ids=tuple(_LABEL_METADATA),
)
def test_label_metadata_is_bounded_unique_and_well_formed(
    fixture: dict, code: str
) -> None:
    assert evaluate_snapshot(fixture, REPOSITORY).code == code


def test_output_is_deterministic() -> None:
    result = evaluate_snapshot(snapshot([issue(139), issue(135)]), REPOSITORY)
    assert result.references == (f"{REPOSITORY}#135", f"{REPOSITORY}#139")


def test_remote_linked_issue_workflow_is_not_maintained() -> None:
    workflow = ROOT / ".github" / "workflows" / "require-linked-issue.yml"
    assert not workflow.exists()
