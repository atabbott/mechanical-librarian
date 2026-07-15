#!/usr/bin/env python3
"""Read the observable GitHub connector-review state without mutation."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone
from typing import Any, Callable


CONNECTOR_LOGINS = {"chatgpt-codex-connector", "chatgpt-codex-connector[bot]"}
OID_PATTERN = re.compile(r"^[0-9a-f]{40}$")
BODY_OID_PATTERN = re.compile(r"(?<![0-9a-f])([0-9a-f]{40})(?![0-9a-f])", re.IGNORECASE)
REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
TRIGGER_PATTERN = re.compile(r"^@codex review(?:\nFocus: [^\n]+)?$")
PICKUP_SECONDS = 120


class ProtocolInputError(ValueError):
    """A clean invocation or observable-protocol error."""


def gh_pages(endpoint: str) -> list[Any]:
    """Return every REST page; even singleton reads use the paginated form."""
    completed = subprocess.run(
        [
            "gh",
            "api",
            endpoint,
            "-H",
            "Accept: application/vnd.github+json",
            "--paginate",
            "--slurp",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    value = json.loads(completed.stdout)
    if not isinstance(value, list):
        raise ProtocolInputError(f"paginated GitHub response is not an array: {endpoint}")
    return value


def gh_object(endpoint: str) -> dict[str, Any]:
    pages = gh_pages(endpoint)
    if len(pages) != 1 or not isinstance(pages[0], dict):
        raise ProtocolInputError(f"expected exactly one GitHub object: {endpoint}")
    return pages[0]


def gh_items(endpoint: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for page in gh_pages(endpoint):
        if not isinstance(page, list) or not all(isinstance(item, dict) for item in page):
            raise ProtocolInputError(f"expected paginated GitHub item arrays: {endpoint}")
        result.extend(page)
    return result


def parse_time(value: str, label: str = "timestamp") -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as error:
        raise ProtocolInputError(f"{label} must be an ISO 8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProtocolInputError(f"{label} requires an explicit UTC offset")
    return parsed


def format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def connector_actor(item: dict[str, Any]) -> bool:
    user = item.get("user") or item.get("author") or {}
    return isinstance(user, dict) and user.get("login") in CONNECTOR_LOGINS


def normalize_trigger(body: Any) -> str:
    if not isinstance(body, str):
        raise ProtocolInputError("C2 trigger body must be text")
    normalized = unicodedata.normalize("NFC", body).replace("\r\n", "\n").replace("\r", "\n")
    normalized = "\n".join(line.strip() for line in normalized.split("\n")).strip()
    if not TRIGGER_PATTERN.fullmatch(normalized):
        raise ProtocolInputError(
            "C2 trigger must normalize exactly to '@codex review' or "
            "'@codex review\\nFocus: <single-line focus>'"
        )
    return normalized


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or not OID_PATTERN.fullmatch(value):
        raise ProtocolInputError(f"{label} must be an explicit lowercase 40-character commit ID")
    return value


def _actor_login(item: dict[str, Any]) -> str | None:
    actor = item.get("user") or item.get("author") or {}
    return actor.get("login") if isinstance(actor, dict) else None


def _occurred_at(item: dict[str, Any], *keys: str) -> tuple[str | None, datetime | None]:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str):
            try:
                parsed = parse_time(value, key)
            except ProtocolInputError:
                return value, None
            return format_time(parsed), parsed
    return None, None


def _commit_binding(item: dict[str, Any], body: str, requested_head: str) -> tuple[str | None, str]:
    for key in ("commit_id", "original_commit_id", "head_sha", "head_commit"):
        value = item.get(key)
        if isinstance(value, str) and OID_PATTERN.fullmatch(value.lower()):
            commit = value.lower()
            return commit, "MATCH" if commit == requested_head else "MISMATCH"
    mentioned = sorted({match.lower() for match in BODY_OID_PATTERN.findall(body)})
    if len(mentioned) == 1:
        return mentioned[0], "MATCH" if mentioned[0] == requested_head else "MISMATCH"
    if len(mentioned) > 1:
        return None, "AMBIGUOUS"
    return None, "UNAVAILABLE"


def _after_request(occurred: datetime | None, requested_at: datetime) -> bool:
    return occurred is not None and occurred >= requested_at


def reaction_event(
    item: dict[str, Any],
    *,
    target_kind: str,
    target_id: int,
    on_target: bool,
    requested_at: datetime,
) -> dict[str, Any] | None:
    if not connector_actor(item):
        return None
    occurred_raw, occurred = _occurred_at(item, "created_at")
    if occurred is not None and occurred < requested_at:
        return None
    return {
        "type": "REACTION",
        "id": item.get("id"),
        "actor": _actor_login(item),
        "occurred_at": occurred_raw,
        "content": item.get("content"),
        "target_kind": target_kind,
        "target_id": target_id,
        "on_target": on_target,
        "commit_id": None,
        "commit_binding": "TARGET_BOUND" if on_target else "TARGET_MISMATCH",
    }


def result_event(
    item: dict[str, Any],
    *,
    event_type: str,
    requested_head: str,
    requested_at: datetime,
    timestamp_keys: tuple[str, ...],
) -> dict[str, Any] | None:
    if not connector_actor(item):
        return None
    occurred_raw, occurred = _occurred_at(item, *timestamp_keys)
    if occurred is not None and not _after_request(occurred, requested_at):
        return None
    body = item.get("body") if isinstance(item.get("body"), str) else ""
    commit_id, binding = _commit_binding(item, body, requested_head)
    return {
        "type": event_type,
        "id": item.get("id"),
        "actor": _actor_login(item),
        "occurred_at": occurred_raw,
        "commit_id": commit_id,
        "commit_binding": binding,
        # An undated result is observable variance, not evidence for this round.
        "substantive": occurred is not None and (bool(body.strip()) or event_type == "REVIEW"),
        "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest() if body else None,
        "review_id": item.get("pull_request_review_id"),
        "review_state": item.get("state"),
    }


def derive_state(events: list[dict[str, Any]], elapsed_seconds: float) -> tuple[str, list[str]]:
    expected_reactions = [
        event
        for event in events
        if event["type"] == "REACTION" and event["on_target"] and event.get("occurred_at") is not None
    ]
    eyes = any(event.get("content") == "eyes" for event in expected_reactions)
    plus_one = any(event.get("content") == "+1" for event in expected_reactions)
    terminal_results = [
        event
        for event in events
        if event["type"] in {"REVIEW", "RESULT_COMMENT", "REVIEW_COMMENT"}
        and event.get("substantive")
    ]
    on_target_results = [
        event for event in terminal_results if event.get("commit_binding") in {"MATCH", "UNAVAILABLE"}
    ]
    off_target_results = [
        event for event in terminal_results if event.get("commit_binding") in {"MISMATCH", "AMBIGUOUS"}
    ]
    off_target_terminal_reactions = [
        event
        for event in events
        if event["type"] == "REACTION" and not event["on_target"] and event.get("content") == "+1"
    ]
    variances: list[str] = []
    if any(event.get("occurred_at") is None for event in events):
        variances.append("CONNECTOR_EVENT_TIMESTAMP_MISSING_OR_INVALID")
    if any(event.get("content") not in {"eyes", "+1"} for event in expected_reactions):
        variances.append("UNEXPECTED_ON_TARGET_REACTION")
    if any(event["type"] == "REACTION" and not event["on_target"] for event in events):
        variances.append("OFF_TARGET_REACTION")
    if any(event.get("commit_binding") == "UNAVAILABLE" for event in terminal_results):
        variances.append("RESULT_COMMIT_BINDING_UNAVAILABLE")
    if eyes and plus_one:
        variances.append("EYES_AND_PLUS_ONE_CONFLICT")
    if plus_one and terminal_results:
        variances.append("CLEAN_AND_FINDINGS_CONFLICT")

    if eyes and plus_one or plus_one and terminal_results:
        return "INCONSISTENT", sorted(set(variances))
    if on_target_results:
        return "FINDINGS_POSTED", sorted(set(variances))
    if plus_one:
        return "CLEAN", sorted(set(variances))
    if off_target_results or off_target_terminal_reactions:
        return "TERMINAL_OFF_TARGET", sorted(set(variances))
    if eyes:
        return "IN_PROGRESS", sorted(set(variances))
    if variances:
        return "PROTOCOL_VARIANCE", sorted(set(variances))
    if elapsed_seconds >= PICKUP_SECONDS:
        return "PICKUP_UNOBSERVED", []
    return "PENDING_PICKUP", []


def _event_datetime(event: dict[str, Any]) -> datetime | None:
    value = event.get("occurred_at")
    return parse_time(value) if isinstance(value, str) else None


def _latency(start: datetime, end: datetime | None) -> float | None:
    return round((end - start).total_seconds(), 3) if end is not None else None


def _first_event_time(events: list[dict[str, Any]], predicate: Callable[[dict[str, Any]], bool]) -> datetime | None:
    values = [_event_datetime(event) for event in events if predicate(event)]
    present = [value for value in values if value is not None]
    return min(present) if present else None


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--repo", required=True, help="owner/repository")
    result.add_argument("--pr", type=int, required=True)
    result.add_argument("--requested-at", required=True, help="exact PR/comment creation timestamp")
    result.add_argument("--opening-head", required=True, help="recorded C1 opening-head commit ID")
    result.add_argument("--trigger-comment", type=int, help="exact C2 trigger comment database ID")
    result.add_argument("--requested-head", help="recorded C2 requested-head commit ID")
    return result


def collect_report(args: argparse.Namespace, *, observed_at: datetime | None = None) -> dict[str, Any]:
    if not REPO_PATTERN.fullmatch(args.repo):
        raise ProtocolInputError("--repo must use owner/repository syntax")
    if args.pr <= 0:
        raise ProtocolInputError("--pr must be positive")
    opening_head = _sha(args.opening_head, "--opening-head")
    request_time = parse_time(args.requested_at, "--requested-at")
    if args.trigger_comment is None and args.requested_head is not None:
        raise ProtocolInputError("--requested-head is valid only with --trigger-comment")
    if args.trigger_comment is not None:
        if args.trigger_comment <= 0:
            raise ProtocolInputError("--trigger-comment must be positive")
        requested_head_argument = _sha(args.requested_head, "--requested-head")
    else:
        requested_head_argument = opening_head
    observed = observed_at or datetime.now(timezone.utc)
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ProtocolInputError("observation time requires an explicit UTC offset")
    if observed < request_time:
        raise ProtocolInputError("observation time precedes request time")

    pr = gh_object(f"repos/{args.repo}/pulls/{args.pr}")
    current_head = _sha(pr["head"]["sha"], "current PR head")
    comments = gh_items(f"repos/{args.repo}/issues/{args.pr}/comments")
    reviews = gh_items(f"repos/{args.repo}/pulls/{args.pr}/reviews")
    review_comments = gh_items(f"repos/{args.repo}/pulls/{args.pr}/comments")

    if args.trigger_comment is None:
        round_name = "C1"
        request_kind = "AUTOMATIC_PR_OPEN"
        requested_head = requested_head_argument
        target_kind = "PR_DESCRIPTION"
        target_id = args.pr
        target_created_at = pr.get("created_at")
        trigger = None
        expected_reactions = gh_items(f"repos/{args.repo}/issues/{args.pr}/reactions")
        alternate_reactions: list[dict[str, Any]] = []
    else:
        requested_head = requested_head_argument
        round_name = "C2"
        request_kind = "EXPLICIT_TRIGGER_COMMENT"
        target_kind = "TRIGGER_COMMENT"
        target_id = args.trigger_comment
        trigger = gh_object(f"repos/{args.repo}/issues/comments/{args.trigger_comment}")
        if trigger.get("issue_url", "").rsplit("/", 1)[-1] != str(args.pr):
            raise ProtocolInputError("C2 trigger comment does not belong to the requested PR")
        normalized_trigger = normalize_trigger(trigger.get("body"))
        target_created_at = trigger.get("created_at")
        expected_reactions = gh_items(f"repos/{args.repo}/issues/comments/{args.trigger_comment}/reactions")
        alternate_reactions = gh_items(f"repos/{args.repo}/issues/{args.pr}/reactions")
        for comment in comments:
            comment_id = comment.get("id")
            if isinstance(comment_id, int) and comment_id != args.trigger_comment:
                for reaction in gh_items(f"repos/{args.repo}/issues/comments/{comment_id}/reactions"):
                    event = reaction_event(
                        reaction,
                        target_kind="OTHER_ISSUE_COMMENT",
                        target_id=comment_id,
                        on_target=False,
                        requested_at=request_time,
                    )
                    if event is not None:
                        alternate_reactions.append({"_event": event})

    if not isinstance(target_created_at, str):
        raise ProtocolInputError("request target lacks an exact creation timestamp")
    target_time = parse_time(target_created_at, "request target created_at")
    if request_time != target_time:
        raise ProtocolInputError("--requested-at must exactly equal the request target creation timestamp")

    events: list[dict[str, Any]] = []
    for reaction in expected_reactions:
        event = reaction_event(
            reaction,
            target_kind=target_kind,
            target_id=target_id,
            on_target=True,
            requested_at=request_time,
        )
        if event is not None:
            events.append(event)
    if round_name == "C2":
        for reaction in alternate_reactions:
            if "_event" in reaction:
                events.append(reaction["_event"])
                continue
            event = reaction_event(
                reaction,
                target_kind="PR_DESCRIPTION",
                target_id=args.pr,
                on_target=False,
                requested_at=request_time,
            )
            if event is not None:
                events.append(event)
    for review in reviews:
        event = result_event(
            review,
            event_type="REVIEW",
            requested_head=requested_head,
            requested_at=request_time,
            timestamp_keys=("submitted_at",),
        )
        if event is not None:
            events.append(event)
    for comment in review_comments:
        event = result_event(
            comment,
            event_type="REVIEW_COMMENT",
            requested_head=requested_head,
            requested_at=request_time,
            timestamp_keys=("created_at", "updated_at"),
        )
        if event is not None:
            events.append(event)
    for comment in comments:
        if round_name == "C2" and comment.get("id") == args.trigger_comment:
            continue
        event = result_event(
            comment,
            event_type="RESULT_COMMENT",
            requested_head=requested_head,
            requested_at=request_time,
            timestamp_keys=("created_at", "updated_at"),
        )
        if event is not None:
            events.append(event)

    events.sort(key=lambda event: (event.get("occurred_at") or "", str(event.get("id") or ""), event["type"]))
    elapsed = (observed - request_time).total_seconds()
    state, variances = derive_state(events, elapsed)
    pickup_at = _first_event_time(
        events,
        lambda event: event["type"] != "REACTION" or event.get("content") in {"eyes", "+1"},
    )
    terminal_at = _first_event_time(
        events,
        lambda event: (
            event["type"] == "REACTION" and event.get("on_target") and event.get("content") == "+1"
        )
        or (
            event["type"] in {"REVIEW", "RESULT_COMMENT", "REVIEW_COMMENT"}
            and event.get("substantive")
            and event.get("commit_binding") in {"MATCH", "UNAVAILABLE"}
        ),
    )
    off_target_terminal_at = _first_event_time(
        events,
        lambda event: (
            event["type"] == "REACTION" and not event.get("on_target") and event.get("content") == "+1"
        )
        or event.get("commit_binding") in {"MISMATCH", "AMBIGUOUS"},
    )
    reaction_ids = [event["id"] for event in events if event["type"] == "REACTION"]
    review_ids = [event["id"] for event in events if event["type"] == "REVIEW"]
    result_comment_ids = [
        event["id"] for event in events if event["type"] in {"RESULT_COMMENT", "REVIEW_COMMENT"}
    ]
    return {
        "schema_version": 1,
        "observed_at": format_time(observed),
        "repo": args.repo,
        "pr": args.pr,
        "round": round_name,
        "state": state,
        "protocol_variances": variances,
        "request": {
            "kind": request_kind,
            "requested_at": format_time(request_time),
            "target_kind": target_kind,
            "target_id": target_id,
            "trigger_normalized": normalized_trigger if trigger is not None else None,
        },
        "heads": {
            "opening": opening_head,
            "requested": requested_head,
            "current": current_head,
            "current_matches_requested": current_head == requested_head,
        },
        "latency_seconds": {
            "observed": round(elapsed, 3),
            "pickup": _latency(request_time, pickup_at),
            "terminal": _latency(request_time, terminal_at),
            "off_target_terminal": _latency(request_time, off_target_terminal_at),
        },
        "ids": {
            "reaction_ids": reaction_ids,
            "review_ids": review_ids,
            "result_comment_ids": result_comment_ids,
        },
        "events": events,
    }


def main() -> int:
    args = parser().parse_args()
    try:
        print(json.dumps(collect_report(args), indent=2, sort_keys=True))
        return 0
    except (
        KeyError,
        OSError,
        ProtocolInputError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
    ) as error:
        message = str(error)
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            message = error.stderr.strip() or message
        print(json.dumps({"result": "ERROR", "error": message}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
