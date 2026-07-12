#!/usr/bin/env python3
"""Validate a bounded GitHub closing-issue GraphQL snapshot."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from html.parser import HTMLParser
import json
import sys
from typing import Any, Mapping
import unicodedata


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


_IGNORED_HTML = frozenset({"blockquote", "code", "pre", "script", "style"})
_CLOSING_KEYWORDS = frozenset(
    {
        "close",
        "closes",
        "closed",
        "fix",
        "fixes",
        "fixed",
        "resolve",
        "resolves",
        "resolved",
    }
)
_TRACKING_EXEMPT_LABEL = "tracking-exempt"
_TRACKING_EXEMPT_HEADING = "tracking exemption"
_HEADING_TAGS = frozenset(f"h{level}" for level in range(1, 7))
_VOID_HTML = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


class ClosingLineHTMLParser(HTMLParser):
    """Collect only GitHub-annotated closing-keyword/issue-link pairs."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ignored_depth = 0
        self.capture_tag: str | None = None
        self.stack: list[str] = []
        self.keyword_fragments: list[str] | None = None
        self.issue_fragments: list[str] | None = None
        self.pending_keyword = False
        self.separator_fragments: list[str] | None = None
        self.references: set[int] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        direct_child = (
            self.capture_tag is not None and self.stack[-1:] == [self.capture_tag]
        )
        if tag in _IGNORED_HTML:
            self.ignored_depth += 1
            self.pending_keyword = False
            self.separator_fragments = None
        elif (
            self.ignored_depth == 0
            and self.capture_tag is None
            and tag in {"p", "li"}
        ):
            self.capture_tag = tag
            self.pending_keyword = False
        elif self.ignored_depth == 0 and direct_child:
            classes = frozenset((attributes.get("class") or "").split())
            if tag == "span" and "issue-keyword" in classes:
                self.keyword_fragments = []
                self.pending_keyword = False
                self.separator_fragments = None
            elif (
                tag == "a"
                and self.pending_keyword
                and "issue-link" in classes
                and self._separator_is_valid()
            ):
                self.issue_fragments = []
                self.separator_fragments = None
            else:
                self.pending_keyword = False
                self.separator_fragments = None
        self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        if tag in _IGNORED_HTML:
            if self.ignored_depth > 0:
                self.ignored_depth -= 1
            return
        if self.ignored_depth != 0:
            return
        if tag == "span" and self.keyword_fragments is not None:
            keyword = "".join(self.keyword_fragments).strip().casefold()
            self.pending_keyword = keyword in _CLOSING_KEYWORDS
            self.separator_fragments = [] if self.pending_keyword else None
            self.keyword_fragments = None
        elif tag == "a" and self.issue_fragments is not None:
            issue_text = "".join(self.issue_fragments).strip()
            if (
                self.pending_keyword
                and issue_text.startswith("#")
                and issue_text[1:].isdigit()
            ):
                self.references.add(int(issue_text[1:]))
            self.issue_fragments = None
            self.pending_keyword = False
            self.separator_fragments = None
        elif tag == self.capture_tag:
            self.capture_tag = None
            self.pending_keyword = False
            self.separator_fragments = None

    def handle_data(self, data: str) -> None:
        if self.ignored_depth != 0:
            return
        if self.keyword_fragments is not None:
            self.keyword_fragments.append(data)
        elif self.issue_fragments is not None:
            self.issue_fragments.append(data)
        elif self.pending_keyword and self.separator_fragments is not None:
            self.separator_fragments.append(data)

    def handle_comment(self, data: str) -> None:
        if self.pending_keyword:
            self.pending_keyword = False
            self.separator_fragments = None

    def _separator_is_valid(self) -> bool:
        separator = "".join(self.separator_fragments or ())
        return (
            not separator
            or separator.isspace()
            or (
                separator.startswith(":")
                and len(separator) > 1
                and separator[1:].isspace()
            )
        )


class TrackingExemptionHTMLParser(HTMLParser):
    """Recognize one root-level exemption heading with visible prose.

    GitHub's rendered HTML is the trust boundary. Quoted, executable, code,
    comment-only, and nested content cannot supply the rationale.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.ignored_depth = 0
        self.heading_tag: str | None = None
        self.heading_is_root = False
        self.heading_fragments: list[str] = []
        self.target_sections = 0
        self.active_section = False
        self.paragraph_depth: int | None = None
        self.paragraph_fragments: list[str] = []
        self.has_visible_prose = False
        self.ambiguous = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in _VOID_HTML:
            return
        depth = len(self.stack)
        if tag in _IGNORED_HTML:
            self.ignored_depth += 1
        elif self.ignored_depth == 0 and tag in _HEADING_TAGS:
            if self.heading_tag is not None:
                self.ambiguous = True
            self.heading_tag = tag
            self.heading_is_root = depth == 0
            self.heading_fragments = []
            if depth == 0:
                self.active_section = False
        elif (
            self.ignored_depth == 0
            and self.active_section
            and tag == "p"
            and depth == 0
        ):
            self.paragraph_depth = depth + 1
            self.paragraph_fragments = []
        self.stack.append(tag)

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag in _VOID_HTML:
            self.handle_starttag(tag, attrs)
            return
        super().handle_startendtag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        depth = len(self.stack)
        if tag in _IGNORED_HTML:
            if self.ignored_depth > 0:
                self.ignored_depth -= 1
        elif self.ignored_depth == 0 and tag == self.heading_tag:
            heading = " ".join("".join(self.heading_fragments).split()).casefold()
            if heading == _TRACKING_EXEMPT_HEADING:
                if self.heading_is_root:
                    self.target_sections += 1
                    self.active_section = True
                else:
                    self.ambiguous = True
            self.heading_tag = None
            self.heading_fragments = []
        elif self.paragraph_depth == depth and tag == "p":
            if _contains_rationale_prose("".join(self.paragraph_fragments)):
                self.has_visible_prose = True
            self.paragraph_depth = None
            self.paragraph_fragments = []
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        else:
            self.ambiguous = True

    def handle_data(self, data: str) -> None:
        if self.ignored_depth != 0:
            return
        if self.heading_tag is not None:
            self.heading_fragments.append(data)
        elif self.paragraph_depth is not None:
            self.paragraph_fragments.append(data)

    def handle_comment(self, data: str) -> None:
        del data

    def valid(self) -> bool:
        return (
            not self.ambiguous
            and self.target_sections == 1
            and self.has_visible_prose
        )


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
        body_html = pull_request["bodyHTML"]
        if type(body_html) is not str or len(body_html) > 262_144:
            raise ValueError
        all_references = _connection(pull_request["allClosingIssues"])
        manual_references = _connection(pull_request["manualClosingIssues"])
    except TooManyReferencesError:
        return GateResult(False, "too_many_references")
    except (KeyError, TypeError, ValueError):
        return GateResult(False, "metadata_incomplete")

    if all_references is None or manual_references is None:
        return GateResult(False, "metadata_incomplete")

    manual_keys = {_key(node) for node in manual_references}
    all_by_key = {_key(node): node for node in all_references}
    if not manual_keys.issubset(all_by_key):
        return GateResult(False, "metadata_incomplete")
    explicit_body_keys = {
        (expected_repository, number) for number in _explicit_body_references(body_html)
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

    exempt_label = _TRACKING_EXEMPT_LABEL in labels
    rationale = _has_tracking_exemption_rationale(body_html)
    if exempt_label and rationale:
        return GateResult(True, "tracking_exempt")
    if exempt_label:
        return GateResult(False, "tracking_exemption_rationale_missing")
    if rationale:
        return GateResult(False, "tracking_exemption_label_missing")
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


def _contains_rationale_prose(value: str) -> bool:
    return any(unicodedata.category(character)[0] in {"L", "N"} for character in value)


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
                {
                    "repository": {"nameWithOwner": name},
                    "number": number,
                    "state": state,
                }
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


def _explicit_body_references(body_html: str) -> frozenset[int]:
    """Use GitHub-rendered structure only to resolve native/manual overlap."""

    parser = ClosingLineHTMLParser()
    parser.feed(body_html)
    parser.close()
    return frozenset(parser.references)


def _has_tracking_exemption_rationale(body_html: str) -> bool:
    parser = TrackingExemptionHTMLParser()
    try:
        parser.feed(body_html)
        parser.close()
    except ValueError:
        return False
    return parser.valid()


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
