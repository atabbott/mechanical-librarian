#!/usr/bin/env python3
"""Apply small validated updates to a private team-run record."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import validate_team as team_validation


STATUSES = sorted(team_validation.RECORD_STATUSES)
DISPOSITIONS = sorted(team_validation.FINDING_DISPOSITIONS)
MISS_REASONS = sorted(team_validation.MISS_REASONS)


def load_record(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_record(path: Path, record: dict[str, Any]) -> None:
    team = team_validation.validate_team()
    team_validation.validate_record(record, team)
    encoded = json.dumps(record, indent=2) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def now(value: str | None) -> str:
    return value or team_validation.utc_now()


def append_event(
    record: dict[str, Any],
    event_type: str,
    *,
    at: str,
    actor: str | None = None,
    phase: str | None = None,
    reason: str | None = None,
    target_id: str | None = None,
    ref: str | None = None,
    stage: str | None = None,
    from_state: str | None = None,
    to_state: str | None = None,
    related_id: str | None = None,
    digest: str | None = None,
    observation_id: str | None = None,
    external_id: str | None = None,
    latency_ms: int | None = None,
    anchor_kind: str | None = None,
    anchor_id: str | None = None,
    on_target: bool | None = None,
    observation_snapshot: str | None = None,
) -> None:
    if record.get("schema_version") == team_validation.CURRENT_RECORD_VERSION:
        record["events"].append(
            team_validation.v3_event(
                len(record["events"]) + 1,
                event_type,
                at,
                actor=actor,
                target_id=target_id,
                ref=ref,
                stage=stage,
                from_state=from_state,
                to_state=to_state,
                reason=reason,
                related_id=related_id,
                digest=digest,
                observation_id=observation_id,
                external_id=external_id,
                latency_ms=latency_ms,
                anchor_kind=anchor_kind,
                anchor_id=anchor_id,
                on_target=on_target,
                observation_snapshot=observation_snapshot,
            )
        )
        return
    record["events"].append(
        {
            "sequence": len(record["events"]) + 1,
            "type": event_type,
            "at": at,
            "actor": actor,
            "phase": phase,
            "reason": reason,
        }
    )


def declaration(record: dict[str, Any], role: str) -> dict[str, Any]:
    context = record["team_context"]
    candidates = [context["root"], *context["workflow_roles"]]
    for candidate in candidates:
        if candidate["role"] == role:
            return candidate
    raise ValueError(f"role is outside frozen workflow: {role}")


def attest(value: str, provenance: str) -> dict[str, str]:
    return {"value": value, "state": "DECLARED", "provenance": provenance}


def is_v3(record: dict[str, Any]) -> bool:
    return record.get("schema_version") == team_validation.CURRENT_RECORD_VERSION


def require_arguments(args: argparse.Namespace, *names: str) -> None:
    missing = [name.replace("_", "-") for name in names if getattr(args, name, None) is None]
    if missing:
        raise ValueError(f"v3 command requires: {', '.join('--' + name for name in missing)}")


def default_target_id(record: dict[str, Any], value: str | None) -> str:
    if value is not None:
        return value
    targets = record.get("targets", [])
    if len(targets) == 1:
        return targets[0]["target_id"]
    raise ValueError("v3 command requires --target-id for a multi-target record")


def command_phase(args: argparse.Namespace, record: dict[str, Any]) -> None:
    if record["status"] == args.status:
        raise ValueError(f"record is already in phase {args.status}")
    previous = record["status"]
    record["status"] = args.status
    if record.get("schema_version") == team_validation.CURRENT_RECORD_VERSION:
        append_event(record, "PHASE_ENTERED", at=now(args.at), from_state=previous, to_state=args.status)
    else:
        append_event(record, "PHASE_ENTERED", at=now(args.at), phase=args.status)


def command_bind(args: argparse.Namespace, record: dict[str, Any]) -> None:
    if not is_v3(record):
        existing = next(
            (item for item in record["bindings"] if item["requested_role"] == args.role),
            None,
        )
    else:
        args.target_id = default_target_id(record, args.target_id)
        require_arguments(args, "binding_id", "ref")
        existing = next(
            (
                item
                for item in record["bindings"]
                if item["binding_id"] == args.binding_id
            ),
            None,
        )
    if existing is not None and not args.replace:
        raise ValueError(
            f"binding is already recorded: {args.binding_id or args.role}; use --replace to correct it"
        )
    frozen = declaration(record, args.role)
    common_state = args.state
    states = {
        "role": args.role_state or common_state,
        "model": args.model_state or common_state,
        "reasoning": args.reasoning_state or common_state,
        "permissions": args.permissions_state or common_state,
    }
    provenances = {
        "role": args.role_provenance or args.provenance,
        "model": args.model_provenance or args.provenance,
        "reasoning": args.reasoning_provenance or args.provenance,
        "permissions": args.permissions_provenance or args.provenance,
    }
    supplied = {
        "role": args.actual_role,
        "model": args.model,
        "reasoning": args.reasoning,
        "permissions": args.permissions,
    }
    if any(states[name] == "UNKNOWN" and supplied[name] is not None for name in states):
        raise ValueError("UNKNOWN attestation cannot claim a value")
    if states["role"] == "FALLBACK" and (
        args.actual_role is None or args.actual_role == args.role
    ):
        raise ValueError("FALLBACK binding requires --actual-role naming the distinct fallback")

    defaults = {
        "role": args.role,
        "model": frozen["model"],
        "reasoning": frozen["reasoning"],
        "permissions": frozen["permissions"],
    }

    def binding_attestation(name: str) -> dict[str, Any]:
        state = states[name]
        value = None if state == "UNKNOWN" else supplied[name] or defaults[name]
        return {
            "value": value,
            "state": state,
            "provenance": provenances[name],
        }

    binding = {
        "binding_id": args.binding_id,
        "target_id": args.target_id,
        "ref": args.ref,
        "requested_role": args.role,
        "thread_id": args.thread_id,
        "role": binding_attestation("role"),
        "model": binding_attestation("model"),
        "reasoning": binding_attestation("reasoning"),
        "permissions": binding_attestation("permissions"),
        "capability_evidence": {
            "verified": sorted(set(args.verified_capability)),
            "absent": sorted(set(args.absent_capability)),
            "provenance": args.capability_provenance,
        },
    }
    if not is_v3(record):
        binding = {
            key: binding[key]
            for key in (
                "requested_role",
                "thread_id",
                "role",
                "model",
                "reasoning",
                "permissions",
            )
        }
    if existing is None:
        record["bindings"].append(binding)
    else:
        record["bindings"][record["bindings"].index(existing)] = binding


def command_writer(args: argparse.Namespace, record: dict[str, Any]) -> None:
    if is_v3(record):
        args.target_id = default_target_id(record, args.target_id)
        require_arguments(args, "ref")
        if args.action == "escalate":
            if args.role != "root":
                raise ValueError("writer escalate role must be root")
            require_arguments(args, "packet_digest")
        if args.action == "start" and args.role == "root":
            require_arguments(args, "packet_digest")
    if args.action == "escalate":
        append_event(record, "ROOT_ESCALATION", at=now(args.at), actor="root", reason=args.reason, target_id=args.target_id, ref=args.ref, digest=args.packet_digest)
        return
    event = "WRITER_STARTED" if args.action == "start" else "WRITER_STOPPED"
    append_event(record, event, at=now(args.at), actor=args.role, reason=args.reason, target_id=args.target_id, ref=args.ref, digest=args.packet_digest)


def command_finding(args: argparse.Namespace, record: dict[str, Any]) -> None:
    if any(item["id"] == args.id for item in record["findings"]):
        raise ValueError(f"finding already exists: {args.id}")
    if is_v3(record):
        args.target_id = default_target_id(record, args.target_id)
        require_arguments(args, "ref")
        if args.decision == "DUPLICATE" and args.duplicate_of is None:
            raise ValueError("DUPLICATE finding requires --duplicate-of")
        if args.decision != "DUPLICATE" and args.duplicate_of is not None:
            raise ValueError("only a DUPLICATE finding may use --duplicate-of")
        if args.miss_reason is not None and args.decision != "DUPLICATE" and args.family is None:
            raise ValueError("canonical family expansion requires --family")
        if args.miss_reason == "OTHER" and args.miss_reason_detail is None:
            raise ValueError("OTHER miss reason requires --miss-reason-detail")
        if args.decision == "PENDING" and args.finding_status == "RESOLVED":
            raise ValueError("PENDING finding cannot be RESOLVED")
        record["findings"].append(
            {
                "id": args.id, "version": 1, "target_id": args.target_id, "ref": args.ref,
                "source_kind": args.source_kind, "source": args.source, "severity": args.severity,
                "summary": args.summary, "decision": args.decision, "status": args.finding_status,
                "duplicate_of": args.duplicate_of, "stage": args.stage, "family": args.family,
                "miss_reason": args.miss_reason, "miss_reason_detail": args.miss_reason_detail,
                "evidence": args.evidence,
            }
        )
        return
    record["findings"].append(
        {
            "id": args.id,
            "source_kind": args.source_kind,
            "source": args.source,
            "severity": args.severity,
            "summary": args.summary,
            "disposition": "UNRESOLVED",
            "duplicate_of": args.duplicate_of,
            "stage": args.stage,
            "family": args.family,
            "miss_reason": args.miss_reason,
            "evidence": args.evidence,
        }
    )


def find_finding(record: dict[str, Any], finding_id: str) -> dict[str, Any]:
    for finding in record["findings"]:
        if finding["id"] == finding_id:
            return finding
    raise ValueError(f"unknown finding: {finding_id}")


def command_disposition(args: argparse.Namespace, record: dict[str, Any]) -> None:
    finding = find_finding(record, args.id)
    if is_v3(record):
        legacy = getattr(args, "disposition", None)
        if legacy is not None:
            mapping = {
                "UNRESOLVED": ("PENDING", "OPEN"),
                "RESOLVED": ("FIX_NOW", "RESOLVED"),
                "DISPROVED": ("DISPROVE", "RESOLVED"),
                "USER_ACCEPTED": ("USER_ACCEPTED", "RESOLVED"),
                "BLOCKING": ("NOT_READY", "OPEN"),
            }
            args.decision, args.finding_status = mapping[legacy]
        if args.miss_reason_detail is not None and args.miss_reason is None:
            raise ValueError("--miss-reason-detail requires --miss-reason")
        if not any(
            (
                args.decision,
                args.finding_status,
                args.family,
                args.miss_reason,
                args.miss_reason_detail,
                args.evidence,
            )
        ):
            raise ValueError("v3 finding update would be a no-op")
        if args.decision is not None:
            finding["decision"] = args.decision
        if args.finding_status is not None:
            finding["status"] = args.finding_status
        if args.family is not None:
            finding["family"] = args.family
        if args.miss_reason is not None:
            finding["miss_reason"] = args.miss_reason
            finding["miss_reason_detail"] = args.miss_reason_detail
    else:
        if args.disposition is None:
            raise ValueError("legacy disposition requires a disposition value")
        finding["disposition"] = args.disposition
    finding["evidence"].extend(args.evidence)


def command_check(args: argparse.Namespace, record: dict[str, Any]) -> None:
    if is_v3(record):
        require_arguments(
            args,
            "id",
            "target_id",
            "ref",
            "kind",
            "started_at",
            "ended_at",
            "environment_json",
            "invalidation_rule",
            "validity",
        )
        if not args.artifact_id:
            raise ValueError("v3 check requires at least one --artifact-id")
        record["checks"].append(
            {
                "id": args.id,
                "version": 1,
                "target_id": args.target_id,
                "ref": args.ref,
                "kind": args.kind,
                "command": args.command_text,
                "started_at": args.started_at,
                "ended_at": args.ended_at,
                "result": args.result,
                "artifact_ids": args.artifact_id,
                "environment": json.loads(args.environment_json),
                "invalidation_rule": args.invalidation_rule,
                "validity": args.validity,
                "skip_id": args.skip_id,
                "summary": args.summary,
            }
        )
        return
    record["checks"].append(
        {"command": args.command_text, "result": args.result, "summary": args.summary}
    )


def command_criterion(args: argparse.Namespace, record: dict[str, Any]) -> None:
    if any(item["id"] == args.id for item in record["criteria"]):
        raise ValueError(f"criterion already exists: {args.id}")
    if is_v3(record):
        require_arguments(args, "target_id")
        if args.status == "NOT_APPLICABLE":
            require_arguments(args, "na_justification", "skip_id")
            if any(value is not None for value in (args.evidence_id, args.ref, args.source_kind, args.source, args.evidence_state)):
                raise ValueError("NOT_APPLICABLE criterion must not carry evidence fields")
            evidence: list[dict[str, Any]] = []
        else:
            if args.na_justification is not None or args.skip_id is not None:
                raise ValueError("only NOT_APPLICABLE criterion may carry skip fields")
            evidence = []
            supplied = (args.evidence_id, args.ref, args.source_kind, args.source, args.evidence_state)
            if args.status == "PASS" or any(value is not None for value in supplied):
                require_arguments(args, "evidence_id", "ref", "source_kind", "source", "evidence_state")
                evidence.append(
                    {
                        "id": args.evidence_id,
                        "source_kind": args.source_kind,
                        "source": args.source,
                        "target_id": args.target_id,
                        "ref": args.ref,
                        "summary": args.summary,
                        "evidence_state": args.evidence_state,
                    }
                )
        record["criteria"].append(
            {
                "id": args.id,
                "version": 1,
                "target_id": args.target_id,
                "text": args.text,
                "status": args.status,
                "na_justification": args.na_justification,
                "skip_id": args.skip_id,
                "evidence": evidence,
            }
        )
        return
    require_arguments(args, "source_kind", "source")
    record["criteria"].append(
        {
            "id": args.id,
            "text": args.text,
            "status": args.status,
            "evidence": [
                {
                    "source_kind": args.source_kind,
                    "source": args.source,
                    "summary": args.summary,
                }
            ],
        }
    )


def command_handoff(args: argparse.Namespace, record: dict[str, Any]) -> None:
    if is_v3(record):
        args.target_id = default_target_id(record, args.target_id)
        require_arguments(args, "packet_digest")
        record["handoffs"].append(
            {
                "id": args.id or f"handoff-{len(record['handoffs']) + 1}",
                "version": 1,
                "target_id": args.target_id,
                "ref": args.frozen_ref,
                "from": args.from_role,
                "to": args.to_role,
                "at": now(args.at),
                "reason": args.reason,
                "packet_digest": args.packet_digest,
                "scope": args.scope,
                "constraints": args.constraint,
                "expected_evidence": args.expected_evidence,
            }
        )
        return
    record["handoffs"].append(
        {
            "from": args.from_role,
            "to": args.to_role,
            "at": now(args.at),
            "reason": args.reason,
            "frozen_ref": args.frozen_ref,
            "scope": args.scope,
            "constraints": args.constraint,
            "expected_evidence": args.expected_evidence,
        }
    )


def command_delivery(args: argparse.Namespace, record: dict[str, Any]) -> None:
    if is_v3(record):
        if args.json is None:
            raise ValueError("v3 delivery requires --json; legacy branch flags are not accepted")
        if any(value is not None for value in (args.branch, args.commit, args.pull_request)):
            raise ValueError("v3 delivery cannot combine --json with legacy branch flags")
        record["deliveries"].append(json.loads(args.json))
        return
    for field in ("branch", "commit", "pull_request"):
        value = getattr(args, field)
        if value is not None:
            record["delivery"][field] = value


def command_end(args: argparse.Namespace, record: dict[str, Any]) -> None:
    append_event(record, "RUN_ENDED", at=now(args.at))


def command_event(args: argparse.Namespace, record: dict[str, Any]) -> None:
    if not is_v3(record):
        raise ValueError("event is available only for v3 records")
    observation_snapshot = None
    digest = args.digest
    if args.observation_snapshot is not None:
        observation_snapshot = args.observation_snapshot.read_text(encoding="utf-8")
        computed = hashlib.sha256(observation_snapshot.encode("utf-8")).hexdigest()
        if digest is not None and digest != computed:
            raise ValueError("event digest differs from the observation snapshot")
        digest = computed
    if args.type.startswith("CONNECTOR_") and observation_snapshot is None:
        raise ValueError("connector events require --observation-snapshot")
    append_event(
        record,
        args.type,
        at=now(args.at),
        actor=args.actor,
        reason=args.reason,
        target_id=args.target_id,
        ref=args.ref,
        stage=args.stage,
        from_state=args.from_state,
        to_state=args.to_state,
        related_id=args.related_id,
        digest=digest,
        observation_id=args.observation_id,
        external_id=args.external_id,
        latency_ms=args.latency_ms,
        anchor_kind=args.anchor_kind,
        anchor_id=args.anchor_id,
        on_target=args.on_target,
        observation_snapshot=observation_snapshot,
    )


def command_append(args: argparse.Namespace, record: dict[str, Any]) -> None:
    value = json.loads(args.json)
    if args.collection == "skip_manifest":
        record["skip_manifest"]["entries"].append(value)
    else:
        collection = record.get(args.collection)
        if not isinstance(collection, list):
            raise ValueError(f"record collection is not appendable: {args.collection}")
        collection.append(value)


def command_finalize(args: argparse.Namespace, record: dict[str, Any]) -> None:
    if not is_v3(record):
        raise ValueError("finalize is available only for v3 records")
    target = next((item for item in record["targets"] if item["target_id"] == args.target_id), None)
    if target is None:
        raise ValueError(f"unknown target: {args.target_id}")
    if args.final_ref != args.head_ref:
        raise ValueError("final-ref and head-ref must be identical for the finalized target")
    target["final_ref"] = args.final_ref
    record["repository"]["head_ref"] = args.head_ref
    record["repository"]["head_tree"] = args.head_tree


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("record", type=Path)
    subparsers = result.add_subparsers(dest="command", required=True)

    phase = subparsers.add_parser("phase")
    phase.add_argument("status", choices=STATUSES)
    phase.add_argument("--at")
    phase.set_defaults(handler=command_phase)

    bind = subparsers.add_parser("bind")
    bind.add_argument("role")
    bind.add_argument("--binding-id")
    bind.add_argument("--target-id")
    bind.add_argument("--ref")
    bind.add_argument("--thread-id")
    bind.add_argument("--state", choices=sorted(team_validation.ATTESTATION_STATES), default="DECLARED")
    bind.add_argument("--provenance", default="frozen team declaration")
    for dimension in ("role", "model", "reasoning", "permissions"):
        bind.add_argument(
            f"--{dimension}-state",
            choices=sorted(team_validation.ATTESTATION_STATES),
        )
        bind.add_argument(f"--{dimension}-provenance")
    bind.add_argument("--actual-role")
    bind.add_argument("--model")
    bind.add_argument("--reasoning")
    bind.add_argument("--permissions")
    bind.add_argument("--verified-capability", action="append", default=[])
    bind.add_argument("--absent-capability", action="append", default=[])
    bind.add_argument(
        "--capability-provenance", default="capabilities not independently verified"
    )
    bind.add_argument("--replace", action="store_true", help="replace an existing binding atomically")
    bind.set_defaults(handler=command_bind)

    writer = subparsers.add_parser("writer")
    writer.add_argument("action", choices=["start", "stop", "escalate"])
    writer.add_argument("role")
    writer.add_argument("--reason", required=True)
    writer.add_argument("--target-id")
    writer.add_argument("--ref")
    writer.add_argument("--packet-digest")
    writer.add_argument("--at")
    writer.set_defaults(handler=command_writer)

    finding = subparsers.add_parser("finding")
    finding.add_argument("id")
    finding.add_argument("--source-kind", choices=["ROLE", "EXTERNAL"], required=True)
    finding.add_argument("--source", required=True)
    finding.add_argument("--severity", choices=["CRITICAL", "HIGH", "MEDIUM", "LOW"], required=True)
    finding.add_argument("--summary", required=True)
    finding.add_argument("--target-id")
    finding.add_argument("--ref")
    finding.add_argument("--stage", default="I0")
    finding.add_argument("--decision", choices=sorted(team_validation.FINDING_DECISIONS), default="PENDING")
    finding.add_argument("--finding-status", choices=sorted(team_validation.FINDING_STATUSES), default="OPEN")
    finding.add_argument("--family")
    finding.add_argument("--miss-reason", choices=MISS_REASONS)
    finding.add_argument("--miss-reason-detail")
    finding.add_argument("--duplicate-of")
    finding.add_argument("--evidence", action="append", default=[])
    finding.set_defaults(handler=command_finding)

    disposition = subparsers.add_parser("disposition")
    disposition.add_argument("id")
    disposition.add_argument("disposition", choices=DISPOSITIONS, nargs="?")
    disposition.add_argument("--decision", choices=sorted(team_validation.FINDING_DECISIONS))
    disposition.add_argument("--finding-status", choices=sorted(team_validation.FINDING_STATUSES))
    disposition.add_argument("--family")
    disposition.add_argument("--miss-reason", choices=MISS_REASONS)
    disposition.add_argument("--miss-reason-detail")
    disposition.add_argument("--evidence", action="append", default=[])
    disposition.set_defaults(handler=command_disposition)

    finding_update = subparsers.add_parser("finding-update")
    finding_update.add_argument("id")
    finding_update.add_argument("--decision", choices=sorted(team_validation.FINDING_DECISIONS))
    finding_update.add_argument("--finding-status", choices=sorted(team_validation.FINDING_STATUSES))
    finding_update.add_argument("--family")
    finding_update.add_argument("--miss-reason", choices=MISS_REASONS)
    finding_update.add_argument("--miss-reason-detail")
    finding_update.add_argument("--evidence", action="append", default=[])
    finding_update.set_defaults(handler=command_disposition, disposition=None)

    check = subparsers.add_parser("check")
    check.add_argument("result", choices=["PASS", "FAIL", "NOT_RUN"])
    check.add_argument("--id")
    check.add_argument("--target-id")
    check.add_argument("--ref")
    check.add_argument("--kind", choices=sorted(team_validation.CHECK_KINDS))
    check.add_argument("--command-text", required=True)
    check.add_argument("--started-at")
    check.add_argument("--ended-at")
    check.add_argument("--artifact-id", action="append", default=[])
    check.add_argument("--environment-json")
    check.add_argument("--invalidation-rule")
    check.add_argument("--validity", choices=sorted(team_validation.EVIDENCE_STATES))
    check.add_argument("--skip-id")
    check.add_argument("--summary", default="recorded check")
    check.set_defaults(handler=command_check)

    criterion = subparsers.add_parser("criterion")
    criterion.add_argument("id")
    criterion.add_argument("--text", required=True)
    criterion.add_argument(
        "--status",
        choices=sorted(team_validation.CRITERION_STATUSES),
        required=True,
    )
    criterion.add_argument("--target-id")
    criterion.add_argument("--evidence-id")
    criterion.add_argument("--ref")
    criterion.add_argument("--source-kind", choices=["ROLE", "EXTERNAL"])
    criterion.add_argument("--source")
    criterion.add_argument("--evidence-state", choices=sorted(team_validation.EVIDENCE_STATES))
    criterion.add_argument("--na-justification")
    criterion.add_argument("--skip-id")
    criterion.add_argument("--summary", required=True)
    criterion.set_defaults(handler=command_criterion)

    handoff = subparsers.add_parser("handoff")
    handoff.add_argument("--id")
    handoff.add_argument("--target-id")
    handoff.add_argument("--from", dest="from_role", required=True)
    handoff.add_argument("--to", dest="to_role", required=True)
    handoff.add_argument("--reason", required=True)
    handoff.add_argument("--frozen-ref", required=True)
    handoff.add_argument("--scope", action="append", required=True)
    handoff.add_argument("--constraint", action="append", default=[])
    handoff.add_argument("--expected-evidence", action="append", required=True)
    handoff.add_argument("--packet-digest")
    handoff.add_argument("--at")
    handoff.set_defaults(handler=command_handoff)

    delivery = subparsers.add_parser("delivery")
    delivery.add_argument("--json")
    delivery.add_argument("--branch")
    delivery.add_argument("--commit")
    delivery.add_argument("--pull-request")
    delivery.set_defaults(handler=command_delivery)

    end = subparsers.add_parser("end")
    end.add_argument("--at")
    end.set_defaults(handler=command_end)

    event = subparsers.add_parser("event", help="append one validated v3 lifecycle or observation event")
    event.add_argument("type", choices=sorted(team_validation.V3_EVENT_TYPES))
    event.add_argument("--actor")
    event.add_argument("--reason")
    event.add_argument("--target-id")
    event.add_argument("--ref")
    event.add_argument("--stage")
    event.add_argument("--from-state")
    event.add_argument("--to-state")
    event.add_argument("--related-id")
    event.add_argument("--digest")
    event.add_argument("--observation-id")
    event.add_argument("--external-id")
    event.add_argument("--latency-ms", type=int)
    event.add_argument("--anchor-kind")
    event.add_argument("--anchor-id")
    event.add_argument(
        "--on-target", action=argparse.BooleanOptionalAction, default=None
    )
    event.add_argument("--observation-snapshot", type=Path)
    event.add_argument("--at")
    event.set_defaults(handler=command_event)

    append = subparsers.add_parser("append", help="append one exact v3 object, then structurally validate atomically")
    append.add_argument(
        "collection",
        choices=["artifacts", "obligations", "skip_manifest", "criteria", "reviews", "findings", "handoffs", "checks", "external_evidence", "external_gates", "mutations", "connector_rounds", "deliveries", "violations"],
    )
    append.add_argument("--json", required=True)
    append.set_defaults(handler=command_append)

    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--target-id", required=True)
    finalize.add_argument("--final-ref", required=True)
    finalize.add_argument("--head-ref", required=True)
    finalize.add_argument("--head-tree", required=True)
    finalize.set_defaults(handler=command_finalize)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        record = load_record(args.record)
        args.handler(args, record)
        write_record(args.record, record)
        print(f"PASS: updated {args.record}")
        return 0
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
