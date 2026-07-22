#!/usr/bin/env python3
"""Validate a bounded GitHub closing-issue GraphQL snapshot."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import re
import sys
from typing import Any, Mapping


@dataclass(frozen=True)
class GateResult:
    allowed: bool
    code: str
    references: tuple[str, ...] = ()

    def message(self) -> str:
        suffix = f" ({', '.join(self.references)})" if self.references else ""
        return f"linked-issue: {self.code}{suffix}"


class TooManyReferencesError(ValueError):
    """The bounded snapshot cannot represent every closing reference."""


class TooManyLabelsError(ValueError):
    """The bounded snapshot cannot represent every pull-request label."""


_TRACKING_EXEMPT_LABEL = "tracking-exempt"
_CLOSING_KEYWORD = r"(?:close|closes|closed|fix|fixes|fixed|resolve|resolves|resolved)"
_CLOSING_REFERENCE = rf"{_CLOSING_KEYWORD}:?[ \t]+#[1-9][0-9]*"
_CLOSING_LINE = re.compile(
    rf"^[ \t]*(?:[-+*][ \t]+|[0-9]+[.)][ \t]+)?{_CLOSING_REFERENCE}"
    rf"(?:[ \t]*,[ \t]*{_CLOSING_REFERENCE})*[ \t]*$",
    re.IGNORECASE,
)
_FENCE = re.compile(r"^[ \t]{0,3}(?P<fence>`{3,}|~{3,})")
_FENCE_CLOSER = re.compile(r"^[ \t]{0,3}(?P<fence>`{3,}|~{3,})[ \t]*$")
_ISSUE_REFERENCE = re.compile(r"#[1-9][0-9]*")


def evaluate_snapshot(snapshot: object, expected_repository: str) -> GateResult:
    """Evaluate only repository, branch, draft, number, and state metadata."""

    try:
        repository = _mapping(_mapping(snapshot)["data"])["repository"]
        repository = _mapping(repository)
        if repository["nameWithOwner"] != expected_repository:
            return GateResult(False, "metadata_repository_mismatch")
        pull_request = _mapping(repository["pullRequest"])
        if type(pull_request["isDraft"]) is not bool:
            raise ValueError
        if pull_request["isDraft"]:
            return GateResult(True, "draft_exempt")
        default_branch = _mapping(repository["defaultBranchRef"])["name"]
        if pull_request["baseRefName"] != default_branch:
            return GateResult(False, "unsupported_base_branch")
        body = pull_request["body"]
        if type(body) is not str or len(body) > 262_144:
            raise ValueError
        all_references = _connection(pull_request["allClosingIssues"])
        manual_references = _connection(pull_request["manualClosingIssues"])
        explicit_body_numbers = _explicit_body_references(body)
    except TooManyReferencesError:
        return GateResult(False, "too_many_references")
    except (KeyError, TypeError, ValueError):
        return GateResult(False, "metadata_incomplete")

    if all_references is None or manual_references is None:
        return GateResult(False, "metadata_incomplete")

    manual_keys = {_key(node) for node in manual_references}
    all_by_key = {_key(node) for node in all_references}
    if not manual_keys.issubset(all_by_key):
        return GateResult(False, "metadata_incomplete")
    explicit_body_keys = {
        (expected_repository, number) for number in explicit_body_numbers
    }
    recognized = tuple(
        node
        for node in all_references
        if _key(node) not in manual_keys or _key(node) in explicit_body_keys
    )
    if recognized:
        foreign = tuple(
            node
            for node in recognized
            if node["repository"]["nameWithOwner"] != expected_repository
        )
        if foreign:
            return GateResult(False, "foreign_repository_reference", _labels(foreign))
        closed = tuple(node for node in recognized if node["state"] != "OPEN")
        if closed:
            return GateResult(False, "closed_issue_reference", _labels(closed))
        return GateResult(True, "valid", _labels(recognized))

    try:
        labels = _label_connection(pull_request["labels"])
    except TooManyLabelsError:
        return GateResult(False, "too_many_labels")
    except (KeyError, TypeError, ValueError):
        return GateResult(False, "metadata_incomplete")
    if labels is None:
        return GateResult(False, "metadata_incomplete")
    if _TRACKING_EXEMPT_LABEL in labels:
        return GateResult(True, "tracking_exempt")
    if manual_references:
        return GateResult(
            False,
            "manual_link_not_closing_reference",
            _labels(manual_references),
        )
    return GateResult(False, "missing_or_invalid_closing_reference")


def _mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError
    return value


def _connection(value: object) -> tuple[Mapping[str, Any], ...] | None:
    try:
        connection = _mapping(value)
        nodes = connection["nodes"]
        total_count = connection["totalCount"]
        has_next_page = _mapping(connection["pageInfo"])["hasNextPage"]
        if (
            type(nodes) is not list
            or type(total_count) is not int
            or type(has_next_page) is not bool
        ):
            return None
        if total_count > 100 or has_next_page:
            raise TooManyReferencesError
        if total_count != len(nodes):
            return None
        normalized: list[Mapping[str, Any]] = []
        seen: set[tuple[str, int]] = set()
        for raw_node in nodes:
            node = _mapping(raw_node)
            repository = _mapping(node["repository"])
            name = repository["nameWithOwner"]
            number = node["number"]
            state = node["state"]
            if (
                type(name) is not str
                or not name
                or type(number) is not int
                or number < 1
                or state not in {"OPEN", "CLOSED"}
            ):
                return None
            key = (name, number)
            if key in seen:
                return None
            seen.add(key)
            normalized.append(
                {"repository": {"nameWithOwner": name}, "number": number, "state": state}
            )
        return tuple(sorted(normalized, key=_key))
    except TooManyReferencesError:
        raise
    except (KeyError, TypeError, ValueError):
        return None


def _label_connection(value: object) -> frozenset[str] | None:
    try:
        connection = _mapping(value)
        nodes = connection["nodes"]
        total_count = connection["totalCount"]
        has_next_page = _mapping(connection["pageInfo"])["hasNextPage"]
        if (
            type(nodes) is not list
            or type(total_count) is not int
            or type(has_next_page) is not bool
        ):
            return None
        if total_count > 100 or has_next_page:
            raise TooManyLabelsError
        if total_count != len(nodes):
            return None
        labels: set[str] = set()
        for raw_node in nodes:
            name = _mapping(raw_node)["name"]
            if type(name) is not str or not name or name in labels:
                return None
            labels.add(name)
        return frozenset(labels)
    except TooManyLabelsError:
        raise
    except (KeyError, TypeError, ValueError):
        return None


def _key(node: Mapping[str, Any]) -> tuple[str, int]:
    return node["repository"]["nameWithOwner"], node["number"]


def _labels(nodes: tuple[Mapping[str, Any], ...]) -> tuple[str, ...]:
    return tuple(
        f"{node['repository']['nameWithOwner']}#{node['number']}"
        for node in sorted(nodes, key=_key)
    )


def _explicit_body_references(body: str) -> frozenset[int]:
    """Read only exact, standalone GitHub closing-reference lines."""

    references: set[int] = set()
    fence: str | None = None
    in_comment = False
    for line in body.splitlines():
        if in_comment:
            if "-->" in line:
                in_comment = False
            continue
        if "<!--" in line:
            if "-->" not in line:
                in_comment = True
            continue
        if fence is not None:
            fence_match = _FENCE_CLOSER.fullmatch(line)
            if (
                fence_match
                and fence_match.group("fence")[0] == fence[0]
                and len(fence_match.group("fence")) >= len(fence)
            ):
                fence = None
            continue
        fence_match = _FENCE.match(line)
        if fence_match:
            fence = fence_match.group("fence")
            continue
        if line.startswith(("    ", "\t")):
            continue
        match = _CLOSING_LINE.fullmatch(line)
        if match is None:
            continue
        for reference in _ISSUE_REFERENCE.findall(line):
            references.add(int(reference[1:]))
            if len(references) > 100:
                raise ValueError
    return frozenset(references)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    arguments = parser.parse_args()
    try:
        snapshot = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeError):
        result = GateResult(False, "metadata_incomplete")
    else:
        result = evaluate_snapshot(snapshot, arguments.repository)
    print(result.message())
    return 0 if result.allowed else 1


if __name__ == "__main__":
    raise SystemExit(main())
