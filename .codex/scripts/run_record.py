#!/usr/bin/env python3
"""Create and validate a minimal workflow evidence record."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any


CODEX = Path(__file__).resolve().parents[1]
STATUSES = {"DRAFT", "IMPLEMENTING", "REVIEW", "AWAITING_EXTERNAL", "BLOCKED", "DELIVERED"}
RESULTS = {"PASS", "FINDINGS", "BLOCKED", "UNAVAILABLE"}
CONNECTOR_STATES = {
    "PENDING_PICKUP", "IN_PROGRESS", "PICKUP_UNOBSERVED", "CLEAN",
    "FINDINGS_POSTED", "TERMINAL_OFF_TARGET", "PROTOCOL_VARIANCE", "INCONSISTENT",
}


class RecordError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RecordError(message)


def exact(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    require(isinstance(value, dict) and set(value) == keys, f"invalid {label}")
    return value


def text(value: Any, label: str) -> str:
    require(isinstance(value, str) and value.strip(), f"{label} is required")
    return value


def timestamp(value: Any, label: str) -> str:
    value = text(value, label)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise RecordError(f"invalid {label}") from error
    require(parsed.tzinfo is not None and parsed.utcoffset() is not None, f"{label} needs a UTC offset")
    return value


def strings(value: Any, label: str) -> list[str]:
    require(
        isinstance(value, list)
        and all(isinstance(item, str) and item.strip() for item in value)
        and len(value) == len(set(value)),
        f"invalid {label}",
    )
    return value


def manifest() -> dict[str, Any]:
    return tomllib.loads((CODEX / "team.toml").read_text(encoding="utf-8"))


def validate_record(record: Any, *, qualified: bool = False) -> dict[str, Any]:
    record = exact(
        record,
        {
            "schema_version", "run_id", "workflow", "tier", "status", "risk_triggers",
            "target", "writer", "checks", "reviews", "findings", "connector",
            "delivery", "notes",
        },
        "record",
    )
    require(record["schema_version"] == 1, "unsupported record schema")
    text(record["run_id"], "run id")
    team = manifest()
    require(record["workflow"] in team["workflows"], "unknown workflow")
    workflow = team["workflows"][record["workflow"]]
    require(record["tier"] in {0, 1, 2}, "tier must be 0, 1, or 2")
    require(record["status"] in STATUSES, "invalid status")
    risk_triggers = strings(record["risk_triggers"], "risk triggers")
    require(record["tier"] == 2 or not risk_triggers, "risk triggers require Tier 2")
    if record["tier"] == 2:
        require(bool(risk_triggers), "Tier 2 requires a risk trigger")
    strings(record["notes"], "notes")

    target = exact(record["target"], {"kind", "id", "base_ref", "final_ref"}, "target")
    text(target["kind"], "target kind")
    text(target["id"], "target id")
    text(target["base_ref"], "target base ref")
    require(target["final_ref"] is None or isinstance(target["final_ref"], str), "invalid target final ref")

    writer = record["writer"]
    if writer is not None:
        writer = exact(writer, {"profile", "thread_id", "started_at", "ended_at"}, "writer")
        allowed_writer = {"root"}
        if workflow["writer_profile"]:
            allowed_writer.add(workflow["writer_profile"])
        require(writer["profile"] in allowed_writer, "writer exceeds workflow authority")
        if writer["profile"] == "root":
            require(writer["thread_id"] is None, "root writer must not invent a thread")
        else:
            text(writer["thread_id"], "writer thread id")
        timestamp(writer["started_at"], "writer start")
        timestamp(writer["ended_at"], "writer end")

    require(isinstance(record["checks"], list), "checks must be a list")
    for check in record["checks"]:
        check = exact(check, {"name", "command", "result", "head_ref", "observed_at"}, "check")
        text(check["name"], "check name")
        text(check["command"], "check command")
        require(check["result"] in {"PASS", "FAIL", "NOT_RUN"}, "invalid check result")
        text(check["head_ref"], "check head ref")
        timestamp(check["observed_at"], "check observed_at")

    require(isinstance(record["findings"], list), "findings must be a list")
    finding_ids: set[str] = set()
    for finding in record["findings"]:
        finding = exact(finding, {"id", "summary", "blocking", "resolved"}, "finding")
        identifier = text(finding["id"], "finding id")
        require(identifier not in finding_ids, "duplicate finding id")
        text(finding["summary"], "finding summary")
        require(isinstance(finding["blocking"], bool) and isinstance(finding["resolved"], bool), "invalid finding state")
        finding_ids.add(identifier)

    require(isinstance(record["reviews"], list), "reviews must be a list")
    fresh_threads: set[str] = set()
    for review in record["reviews"]:
        review = exact(
            review,
            {
                "tasks", "profile", "thread_id", "context", "origin_thread_id",
                "packet_digest", "result", "head_ref", "observed_at", "finding_ids",
            },
            "review",
        )
        strings(review["tasks"], "review tasks")
        require(review["profile"] in {"reviewer", "challenger"}, "invalid review profile")
        thread_id = text(review["thread_id"], "review thread id")
        require(review["context"] in {"FRESH", "TARGETED"}, "invalid review context")
        if review["context"] == "FRESH":
            require(review["origin_thread_id"] is None, "fresh review names an origin thread")
            require(thread_id not in fresh_threads, "fresh review thread is reused")
            fresh_threads.add(thread_id)
        else:
            require(review["origin_thread_id"] == thread_id, "targeted review changed thread")
        packet_digest = text(review["packet_digest"], "review packet digest")
        require(len(packet_digest) == 64 and all(char in "0123456789abcdef" for char in packet_digest), "invalid review packet digest")
        require(review["result"] in RESULTS, "invalid review result")
        text(review["head_ref"], "review head ref")
        timestamp(review["observed_at"], "review observed_at")
        review_findings = strings(review["finding_ids"], "review finding ids")
        require(set(review_findings) <= finding_ids, "review cites unknown finding")

    connector = record["connector"]
    if connector is not None:
        connector = exact(
            connector,
            {"round", "anchor", "state", "requested_head", "observed_at"},
            "connector",
        )
        require(connector["round"] in {"C1", "C2"}, "invalid connector round")
        text(connector["anchor"], "connector anchor")
        require(connector["state"] in CONNECTOR_STATES, "invalid connector state")
        text(connector["requested_head"], "connector requested head")
        timestamp(connector["observed_at"], "connector observed_at")

    delivery = record["delivery"]
    if delivery is not None:
        delivery = exact(delivery, {"kind", "url", "head_ref", "observed_at"}, "delivery")
        require(delivery["kind"] in {"PULL_REQUEST", "DOCUMENTED", "REPORT_ONLY"}, "invalid delivery kind")
        text(delivery["url"], "delivery URL")
        text(delivery["head_ref"], "delivery head ref")
        timestamp(delivery["observed_at"], "delivery observed_at")

    if qualified:
        require(record["status"] in {"DELIVERED", "AWAITING_EXTERNAL"}, "run is not in a qualifying terminal/handoff state")
        final_ref = text(target["final_ref"], "target final ref")
        if workflow["writer_profile"]:
            require(writer is not None, "writer workflow lacks writer evidence")
            require(record["checks"], "writer workflow lacks checks")
        require(
            all(item["result"] == "PASS" and item["head_ref"] == final_ref for item in record["checks"]),
            "checks are failed, not run, or stale",
        )
        passed_tasks = {
            task
            for review in record["reviews"]
            if review["result"] == "PASS" and review["head_ref"] == final_ref
            for task in review["tasks"]
        }
        required_tasks = set(workflow["default_review_tasks"])
        if record["tier"] == 2:
            required_tasks |= {"safety", "publication_challenge"}
            require(
                any(
                    item["profile"] == "challenger"
                    and item["result"] == "PASS"
                    and item["head_ref"] == final_ref
                    and "publication_challenge" in item["tasks"]
                    for item in record["reviews"]
                ),
                "Tier 2 lacks an independent challenger",
            )
        require(required_tasks <= passed_tasks, "required review tasks are incomplete")
        require(
            not any(item["blocking"] and not item["resolved"] for item in record["findings"]),
            "unresolved blocking finding",
        )
        require(delivery is not None and delivery["head_ref"] == final_ref, "delivery is missing or stale")
        if delivery["kind"] == "PULL_REQUEST":
            require(connector is not None and connector["requested_head"] == final_ref, "pull request lacks connector evidence")
            pending = connector["state"] in {"PENDING_PICKUP", "IN_PROGRESS", "PICKUP_UNOBSERVED"}
            require(
                record["status"] == ("AWAITING_EXTERNAL" if pending else "DELIVERED"),
                "connector state and run status disagree",
            )
            require(
                connector["state"] not in {"TERMINAL_OFF_TARGET", "PROTOCOL_VARIANCE", "INCONSISTENT"},
                "connector protocol is not conforming",
            )
    return record


def write_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    subparsers = result.add_subparsers(dest="command", required=True)
    init = subparsers.add_parser("init")
    init.add_argument("--output", type=Path, required=True)
    init.add_argument("--run-id", required=True)
    init.add_argument("--workflow", required=True)
    init.add_argument("--tier", type=int, choices=[0, 1, 2], required=True)
    init.add_argument("--target-kind", required=True)
    init.add_argument("--target-id", required=True)
    init.add_argument("--base-ref", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("record", type=Path)
    validate.add_argument("--qualified", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "init":
            value = {
                "schema_version": 1,
                "run_id": args.run_id,
                "workflow": args.workflow,
                "tier": args.tier,
                "status": "DRAFT",
                "risk_triggers": [],
                "target": {"kind": args.target_kind, "id": args.target_id, "base_ref": args.base_ref, "final_ref": None},
                "writer": None,
                "checks": [],
                "reviews": [],
                "findings": [],
                "connector": None,
                "delivery": None,
                "notes": [],
            }
            validate_record(value)
            write_atomic(args.output, value)
        else:
            value = json.loads(args.record.read_text(encoding="utf-8"))
            validate_record(value, qualified=args.qualified)
    except (OSError, json.JSONDecodeError, RecordError, tomllib.TOMLDecodeError) as error:
        print(f"FAIL: {error}", file=__import__("sys").stderr)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
