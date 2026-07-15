#!/usr/bin/env python3
"""Validate the private agent team and its durable run records."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import connector_state as connector_protocol


CODEX = Path(__file__).resolve().parents[1]
CONFIG = CODEX / "config.toml"
MANIFEST = CODEX / "team.toml"
SCHEMA = CODEX / "schemas" / "team-run.schema.json"
COMMON_POLICIES = {
    ".codex/standing-authority.md",
    ".codex/model-selection.md",
    ".codex/engineering-judgment.md",
    ".codex/binding-preflight.md",
    ".codex/concurrency.md",
}
FAMILY_POLICY = ".codex/finding-family-audit.md"
REVIEW_METHOD = ".codex/review-method.md"
REVIEW_LENSES = ".codex/review-lenses.md"
MUTATION_POLICY = ".codex/contract-mutation-testing.md"
WORKER_SCOPE_POLICY = ".codex/worker-scope-control.md"
CONNECTOR_POLICY = ".codex/codex-connector-review.md"
STRUCTURED_RECORD_POLICY = ".codex/structured-records.md"
CHARTER_REVIEW_ROLES = {
    "acceptance_reviewer",
    "code_quality_reviewer",
    "defect_verifier",
    "focused_verifier",
    "pr_feedback_reviewer",
    "pr_requirements_reviewer",
    "safety_reviewer",
}
PUBLICATION_CHALLENGE_ROLE = "publication_challenger"
WORKFLOW_WRITER_ROLES = {
    "clear-defect-backlog": ("defect_worker", {"defect_worker"}),
    "deliver-focused-change": ("focused_worker", {"focused_worker"}),
    "deliver-functional-slice": ("slice_worker", {"slice_worker"}),
    "maintain-open-prs": (
        "pr_remediation_worker",
        {"pr_remediation_worker", "slice_worker", "defect_worker"},
    ),
}
RECORD_STATUSES = {
    "INTAKE",
    "DISCOVERY",
    "IMPLEMENTATION",
    "REVIEW",
    "REMEDIATION",
    "QUALIFICATION",
    "DELIVERED",
    "AWAITING_EXTERNAL",
    "BLOCKED",
}
WORKFLOWS = {
    "clear-defect-backlog",
    "deliver-focused-change",
    "deliver-functional-slice",
    "explore-product-ideas",
    "maintain-open-prs",
    "prepare-ready-work",
}
CURRENT_RECORD_VERSION = 3
LEGACY_RECORD_VERSION = 1
PREVIOUS_RECORD_VERSION = 2
CURRENT_SEMANTICS_VERSION = 1
GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
ATTESTATION_STATES = {"VERIFIED", "DECLARED", "FALLBACK", "UNKNOWN"}
LEGACY_BINDING_STATES = {"VERIFIED", "ROLE_VERIFIED", "DECLARED", "FALLBACK"}
EVENT_TYPES = {
    "RUN_STARTED",
    "PHASE_ENTERED",
    "WRITER_STARTED",
    "WRITER_STOPPED",
    "RUN_ENDED",
}
V3_EVENT_TYPES = {
    "RUN_STARTED",
    "PHASE_ENTERED",
    "STAGE_STARTED",
    "STAGE_COMPLETED",
    "WRITER_STARTED",
    "WRITER_STOPPED",
    "ROOT_ESCALATION",
    "QUALIFICATION_RECORDED",
    "CONNECTOR_ROUND_STARTED",
    "CONNECTOR_PICKUP_OBSERVED",
    "CONNECTOR_PICKUP_REMOVED",
    "CONNECTOR_PICKUP_UNOBSERVED",
    "CONNECTOR_TERMINAL_OBSERVED",
    "MUTATION_RECORDED",
    "DELIVERY_RECORDED",
    "PROTOCOL_VARIANCE",
    "EXECUTION_POLICY_VIOLATION",
    "FAMILY_EXPANSION_RECORDED",
    "FAMILY_BOUNDARY_STOP",
    "PUBLICATION_CHALLENGE_REQUIRED",
    "MAINTAINER_DECISION_RECORDED",
    "RUN_ENDED",
}
CRITERION_STATUSES = {
    "PASS",
    "FAIL",
    "PARTIAL",
    "NOT_DEMONSTRATED",
    "NOT_APPLICABLE",
}
FINDING_DISPOSITIONS = {
    "UNRESOLVED",
    "RESOLVED",
    "DISPROVED",
    "USER_ACCEPTED",
    "BLOCKING",
}
FINDING_DECISIONS = {
    "PENDING",
    "FIX_NOW",
    "DEFER",
    "DISPROVE",
    "OMIT",
    "NOT_READY",
    "USER_ACCEPTED",
    "DUPLICATE",
}
FINDING_STATUSES = {"OPEN", "RESOLVED"}
EVIDENCE_STATES = {
    "FRESH",
    "INHERITED",
    "NOT_APPLICABLE",
    "UNAVAILABLE",
    "STALE",
}
OBLIGATION_KINDS = {
    "BINDING",
    "CRITERIA",
    "REVIEW",
    "CHECK",
    "QUALIFICATION",
    "EXTERNAL_GATE",
    "MUTATION",
    "CONNECTOR",
    "DELIVERY",
    "UPGRADE_RECONCILIATION",
    "FAMILY_BOUNDARY_ANALYSIS",
    "PUBLICATION_CHALLENGE",
    "MAINTAINER_DECISION",
}
OBLIGATION_STATUSES = {"OPEN", "SATISFIED", "NOT_APPLICABLE", "UNAVAILABLE"}
REVIEW_RESULTS = {"PASS", "FINDINGS", "BLOCKED"}
REVIEW_CONTEXT_MODES = {
    "FRESH_ISOLATED",
    "TARGETED_CONTINUATION",
    "REPLACEMENT_ISOLATED",
}
CHECK_RESULTS = {"PASS", "FAIL", "NOT_RUN"}
CHECK_KINDS = {
    "TARGETED",
    "FULL_GATE",
    "DEMONSTRATION",
    "REMOTE_CI",
    "PREFLIGHT",
    "NEGATIVE",
}
MUTATION_KINDS = {
    "REPOSITORY",
    "PROJECT",
    "ISSUE",
    "PULL_REQUEST",
    "COMMENT",
    "REVIEW_THREAD",
    "CONNECTOR_TRIGGER",
    "OTHER_EXTERNAL",
}
MUTATION_RESULTS = {"APPLIED", "NO_CHANGE", "FAILED", "INCONSISTENT"}
CONNECTOR_STATES = {
    "PENDING_PICKUP",
    "IN_PROGRESS",
    "CLEAN",
    "FINDINGS_POSTED",
    "PICKUP_UNOBSERVED",
    "TERMINAL_OFF_TARGET",
    "PROTOCOL_VARIANCE",
    "INCONSISTENT",
}
CONNECTOR_ACTORS = {
    "chatgpt-codex-connector",
    "chatgpt-codex-connector[bot]",
}
EXTERNAL_GATE_STATUSES = {
    "SATISFIED",
    "PENDING",
    "BLOCKED",
    "NOT_APPLICABLE",
    "UNAVAILABLE",
}
DELIVERY_MODES = {"PULL_REQUEST", "DOCUMENTED", "REPORT_ONLY", "QUEUE_HANDOFF"}
DELIVERY_STATUSES = {"DELIVERED", "AWAITING_EXTERNAL"}
VIOLATION_KINDS = {
    "EXECUTION_POLICY_VIOLATION",
    "PROTOCOL_VARIANCE",
    "INCONSISTENT",
}
VIOLATION_STATUSES = {"OPEN", "RESOLVED"}
FAMILY_EXPANSION_THRESHOLDS = (1, 2, 3)
FAMILY_AUTHORIZATION_ACTIONS = {
    "BOUNDED_REDESIGN",
    "BOUNDED_REVERSION",
    "MAINTAINER_SCOPE_DECISION",
}
SEMANTIC_TERMINAL_STATUSES = {"DELIVERED", "AWAITING_EXTERNAL"}
POLICY_BODIES = {
    *COMMON_POLICIES,
    FAMILY_POLICY,
    REVIEW_METHOD,
    REVIEW_LENSES,
    MUTATION_POLICY,
    WORKER_SCOPE_POLICY,
    CONNECTOR_POLICY,
    STRUCTURED_RECORD_POLICY,
}

WORKFLOW_TARGET_KINDS = {
    "clear-defect-backlog": {"ticket", "queue"},
    "deliver-focused-change": {"ticket"},
    "deliver-functional-slice": {"slice"},
    "explore-product-ideas": {"idea"},
    "maintain-open-prs": {"pull_request", "queue"},
    "prepare-ready-work": {"idea", "slice", "ticket", "queue"},
}

PHASE_TRANSITIONS = {
    "INTAKE": {"DISCOVERY", "AWAITING_EXTERNAL", "BLOCKED"},
    "DISCOVERY": {"IMPLEMENTATION", "REVIEW", "QUALIFICATION", "AWAITING_EXTERNAL", "BLOCKED"},
    "IMPLEMENTATION": {"REVIEW", "AWAITING_EXTERNAL", "BLOCKED"},
    "REVIEW": {"REMEDIATION", "QUALIFICATION", "AWAITING_EXTERNAL", "BLOCKED"},
    "REMEDIATION": {"REVIEW", "QUALIFICATION", "AWAITING_EXTERNAL", "BLOCKED"},
    "QUALIFICATION": {"REMEDIATION", "DELIVERED", "AWAITING_EXTERNAL", "BLOCKED"},
    "AWAITING_EXTERNAL": {"DISCOVERY", "REVIEW", "REMEDIATION", "QUALIFICATION", "DELIVERED", "BLOCKED"},
    "BLOCKED": {"DISCOVERY", "IMPLEMENTATION", "REVIEW", "REMEDIATION", "QUALIFICATION", "AWAITING_EXTERNAL"},
    "DELIVERED": set(),
}
MISS_REASONS = {
    "CONTRACT_MISREAD",
    "SIBLING_NOT_ENUMERATED",
    "FIXTURE_GAP",
    "INTERACTION_MISSED",
    "REMEDIATION_REGRESSION",
    "REVIEWER_CORRELATION",
    "EXTERNAL_EVIDENCE_DISCOVERED",
    "OTHER",
}


class ValidationError(ValueError):
    pass


def load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        return tomllib.load(stream)


def fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def duplicate_safe_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def build_policy_bundle(manifest: dict[str, Any]) -> dict[str, Any]:
    """Digest every workflow skill body and every record-governing policy body."""

    paths = set(POLICY_BODIES)
    paths.update(
        f".codex/{declared['skill']}"
        for declared in manifest["workflows"].values()
    )
    files = []
    for relative in sorted(paths):
        path = CODEX.parent / relative
        require(path.is_file(), f"policy bundle body is missing: {relative}")
        files.append({"path": relative, "digest": fingerprint(path)})
    return {
        "algorithm": "sha256",
        "digest": canonical_fingerprint(files),
        "files": files,
    }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: str, field: str) -> datetime:
    require(isinstance(value, str) and value, f"{field} timestamp is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValidationError(f"{field} timestamp is invalid") from error
    require(parsed.tzinfo is not None, f"{field} timestamp must include an offset")
    return parsed


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def require_string(value: Any, field: str, *, allow_empty: bool = False) -> None:
    require(isinstance(value, str), f"{field} must be a string")
    if not allow_empty:
        require(bool(value), f"{field} is required")


def require_string_list(value: Any, field: str, *, nonempty: bool = False) -> None:
    require(isinstance(value, list), f"{field} must be a list")
    if nonempty:
        require(bool(value), f"{field} must not be empty")
    require(all(isinstance(item, str) for item in value), f"{field} items must be strings")


def frontmatter_name(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    require(text.startswith("---\n"), f"{path}: missing YAML frontmatter")
    try:
        frontmatter = text.split("---", 2)[1]
    except IndexError as error:
        raise ValidationError(f"{path}: unterminated YAML frontmatter") from error
    match = re.search(r"^name:\s*([^\n]+)$", frontmatter, re.MULTILINE)
    require(match is not None, f"{path}: missing skill name")
    return match.group(1).strip()


def validate_policy_wiring(
    manifest: dict[str, Any],
    role_texts: dict[str, str],
    skill_texts: dict[str, str],
) -> None:
    """Validate stable policy references, not incidental workflow prose."""

    writer_roles = {
        role
        for role, declared in manifest["roles"].items()
        if declared["kind"] == "writer"
    }
    for role in writer_roles:
        require(
            WORKER_SCOPE_POLICY in role_texts[role],
            f"{role}: missing worker scope policy {WORKER_SCOPE_POLICY}",
        )

    writer_workflows = {
        workflow
        for workflow, declared in manifest["workflows"].items()
        if declared["writer"]
    }
    require(
        writer_workflows == set(WORKFLOW_WRITER_ROLES),
        "writer workflow registry differs from stable writer-role policy",
    )
    for workflow, (default_writer, allowed_writers) in WORKFLOW_WRITER_ROLES.items():
        declared = manifest["workflows"][workflow]
        require(
            declared["writer"] == default_writer,
            f"{workflow}: default writer differs from stable policy",
        )
        actual_writers = {
            role
            for role in declared["roles"]
            if manifest["roles"][role]["kind"] == "writer"
        }
        require(
            actual_writers == allowed_writers,
            f"{workflow}: writer candidates differ from stable policy",
        )

    require(
        CHARTER_REVIEW_ROLES | {PUBLICATION_CHALLENGE_ROLE} <= set(manifest["roles"]),
        "delivery review role registry is incomplete",
    )
    for role in CHARTER_REVIEW_ROLES:
        text = role_texts[role]
        require(REVIEW_METHOD in text, f"{role}: missing review method {REVIEW_METHOD}")
        require(
            FAMILY_POLICY not in text,
            f"{role}: reviewer must not self-certify the root family policy",
        )

    challenger = role_texts[PUBLICATION_CHALLENGE_ROLE]
    require(
        REVIEW_METHOD not in challenger and FAMILY_POLICY not in challenger,
        "publication challenger is coupled to shared review synthesis",
    )

    for workflow, declared in manifest["workflows"].items():
        if not declared["writer"]:
            continue
        text = skill_texts[workflow]
        require(REVIEW_METHOD in text, f"{workflow}: missing review method {REVIEW_METHOD}")
        require(FAMILY_POLICY in text, f"{workflow}: missing family policy {FAMILY_POLICY}")
        require(REVIEW_LENSES in text, f"{workflow}: missing dynamic lenses {REVIEW_LENSES}")
        require(MUTATION_POLICY in text, f"{workflow}: missing mutation policy {MUTATION_POLICY}")
        require(
            PUBLICATION_CHALLENGE_ROLE in declared["roles"],
            f"{workflow}: missing fresh publication challenger",
        )


def validate_team() -> dict[str, Any]:
    config = load_toml(CONFIG)
    manifest = load_toml(MANIFEST)
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    require(schema.get("type") == "object", "team-run schema must describe an object")
    require(
        schema.get("properties", {}).get("schema_version", {}).get("const") == CURRENT_RECORD_VERSION,
        "team-run schema does not describe the current record version",
    )
    require(manifest.get("schema_version") == 1, "unsupported team manifest version")
    require(config.get("model") == manifest["root"]["model"], "root model mismatch")
    require(
        config.get("model_reasoning_effort") == manifest["root"]["reasoning"],
        "root reasoning mismatch",
    )
    require(config["agents"].get("max_depth") == 1, "agent nesting must remain one level")
    require(config["agents"].get("max_threads", 0) >= 2, "at least two threads are required")

    roles = manifest["roles"]
    require(
        set(manifest["workflows"]) == WORKFLOWS,
        "manifest workflow registry differs from validator workflow set",
    )
    registry = {
        key: value
        for key, value in config["agents"].items()
        if isinstance(value, dict)
    }
    require(set(registry) == set(roles), "config role registry and manifest roles differ")

    role_summary: dict[str, Any] = {}
    role_texts: dict[str, str] = {}
    for role, declared in roles.items():
        path = CODEX / declared["config"]
        require(path.is_file(), f"{role}: missing config {path}")
        actual = load_toml(path)
        role_texts[role] = path.read_text(encoding="utf-8")
        require(actual.get("name") == role, f"{role}: role name mismatch")
        require(actual.get("model") == declared["model"], f"{role}: model mismatch")
        require(
            actual.get("model_reasoning_effort") == declared["reasoning"],
            f"{role}: reasoning mismatch",
        )
        require(
            actual.get("default_permissions") == declared["permissions"],
            f"{role}: permissions mismatch",
        )
        require("sandbox_mode" not in actual, f"{role}: do not combine legacy sandbox mode")
        expected_permission = ":workspace" if declared["kind"] == "writer" else ":read-only"
        require(declared["permissions"] == expected_permission, f"{role}: unsafe role kind")
        app_defaults = actual.get("apps", {}).get("_default", {})
        require(
            app_defaults.get("default_tools_approval_mode") == "writes",
            f"{role}: app writes must require approval",
        )
        require(
            app_defaults.get("destructive_enabled") is False,
            f"{role}: destructive app tools must be disabled",
        )
        require(
            ".codex/engineering-judgment.md" in actual.get("developer_instructions", ""),
            f"{role}: missing judgment policy",
        )
        registered_path = registry[role].get("config_file")
        require(registered_path == declared["config"], f"{role}: registry path mismatch")
        role_summary[role] = {
            "kind": declared["kind"],
            "model": declared["model"],
            "reasoning": declared["reasoning"],
            "permissions": declared["permissions"],
            "capabilities": sorted(declared["capabilities"]),
            "forbidden_capabilities": sorted(declared["forbidden_capabilities"]),
            "config_fingerprint": fingerprint(path),
        }

    skill_texts: dict[str, str] = {}
    for workflow, declared in manifest["workflows"].items():
        require(workflow in WORKFLOWS, f"unknown workflow {workflow}")
        thresholds = declared.get("family_expansion_thresholds")
        if declared["writer"] or thresholds is not None:
            require(
                thresholds == list(FAMILY_EXPANSION_THRESHOLDS),
                f"{workflow}: family expansion thresholds must be [1, 2, 3]",
            )
        if thresholds is not None:
            require(
                PUBLICATION_CHALLENGE_ROLE in declared["roles"],
                f"{workflow}: expansion-two publication challenger is unreachable",
            )
        if declared["writer"]:
            require(declared["writer"] in declared["roles"], f"{workflow}: writer not in roles")
            require(roles[declared["writer"]]["kind"] == "writer", f"{workflow}: invalid writer")
        else:
            require(
                all(roles[role]["kind"] != "writer" for role in declared["roles"]),
                f"{workflow}: read-only workflow includes a writer",
            )
        require(
            1 <= declared["max_parallel_readers"] <= 3,
            f"{workflow}: reader limit must be 1..3",
        )
        for role in declared["roles"]:
            require(role in roles, f"{workflow}: unknown role {role}")
        skill = CODEX / declared["skill"]
        require(skill.is_file(), f"{workflow}: missing skill")
        require(frontmatter_name(skill) == workflow, f"{workflow}: skill name mismatch")
        skill_text = skill.read_text(encoding="utf-8")
        skill_texts[workflow] = skill_text
        for policy in COMMON_POLICIES:
            require(policy in skill_text, f"{workflow}: missing policy {policy}")

    validate_policy_wiring(manifest, role_texts, skill_texts)

    policy_bundle = build_policy_bundle(manifest)

    return {
        "schema_version": manifest["schema_version"],
        "root": {
            "kind": "writer",
            "model": manifest["root"]["model"],
            "reasoning": manifest["root"]["reasoning"],
            "permissions": ":workspace",
            "capabilities": [
                "external_read",
                "external_write",
                "orchestration",
                "repository_read",
                "workspace_write",
            ],
            "forbidden_capabilities": [],
            "config_fingerprint": fingerprint(CONFIG),
        },
        "roles": role_summary,
        "workflows": sorted(manifest["workflows"]),
        "config_fingerprint": fingerprint(CONFIG),
        "manifest_fingerprint": fingerprint(MANIFEST),
        "policy_bundle": policy_bundle,
    }


def reader_wave_size(runtime_slots: int, declared_max: int) -> int:
    require(runtime_slots >= 1, "runtime slots must be positive")
    return min(max(0, runtime_slots - 1), 3, declared_max)


def validate_record_header(record: dict[str, Any], required: set[str]) -> None:
    require(isinstance(record, dict), "record must be an object")
    require(set(record) == required, "record keys do not match schema")
    require_string(record["workflow"], "record workflow")
    require(record["workflow"] in WORKFLOWS, "record workflow is invalid")
    require_string(record["status"], "record status")
    require(record["status"] in RECORD_STATUSES, "record status is invalid")
    require_string(record["run_id"], "run id")
    require(isinstance(record["target"], dict), "target must be an object")
    require(set(record["target"]) == {"kind", "id", "frozen_ref"}, "invalid target")
    require_string(record["target"]["kind"], "target kind")
    require(
        record["target"]["kind"] in {"idea", "slice", "ticket", "pull_request", "queue"},
        "invalid target kind",
    )
    require_string(record["target"]["id"], "target id")
    require_string(record["target"]["frozen_ref"], "target frozen ref")


def validate_checks_and_delivery(record: dict[str, Any]) -> None:
    require(isinstance(record["checks"], list), "checks must be a list")
    for check in record["checks"]:
        require(isinstance(check, dict), "check must be an object")
        require(set(check) == {"command", "result", "summary"}, "invalid check keys")
        require_string(check["command"], "check command")
        require_string(check["result"], "check result")
        require(check["result"] in {"PASS", "FAIL", "NOT_RUN"}, "invalid check result")
        require_string(check["summary"], "check summary", allow_empty=True)

    delivery = record["delivery"]
    require(isinstance(delivery, dict), "delivery must be an object")
    require(
        set(delivery) == {"branch", "commit", "pull_request", "merge_authorized"},
        "invalid delivery keys",
    )
    for field in ("branch", "commit", "pull_request"):
        require(
            delivery[field] is None or isinstance(delivery[field], str),
            f"delivery {field} must be a string or null",
        )
    require(delivery["merge_authorized"] is False, "team records cannot authorize merge")


def validate_legacy_record(record: dict[str, Any], team: dict[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version",
        "run_id",
        "workflow",
        "status",
        "target",
        "bindings",
        "criteria",
        "findings",
        "handoffs",
        "checks",
        "delivery",
    }
    validate_record_header(record, required)
    require(isinstance(record["bindings"], list), "legacy bindings must be a list")
    for binding in record["bindings"]:
        require(isinstance(binding, dict), "legacy binding must be an object")
        required_binding = {"role", "model", "reasoning", "state", "thread_id", "config_fingerprint"}
        require(set(binding) == required_binding, "invalid binding keys")
        require_string(binding["role"], "legacy binding role")
        require_string(binding["model"], "legacy binding model")
        require_string(binding["state"], "legacy binding state")
        require(binding["state"] in LEGACY_BINDING_STATES, "invalid legacy binding state")
        require_string(binding["reasoning"], "legacy binding reasoning")
        require(binding["reasoning"] in {"medium", "high"}, "invalid reasoning")
        require(binding["thread_id"] is None or isinstance(binding["thread_id"], str), "invalid legacy thread id")
        require_string(binding["config_fingerprint"], "legacy config fingerprint")
        require(
            re.fullmatch(r"[a-f0-9]{64}", binding["config_fingerprint"]) is not None,
            "invalid config fingerprint",
        )

    require(isinstance(record["criteria"], list), "legacy criteria must be a list")
    for criterion in record["criteria"]:
        require(isinstance(criterion, dict), "legacy criterion must be an object")
        require(set(criterion) == {"id", "text", "status", "evidence"}, "invalid criterion keys")
        require_string(criterion["status"], "legacy criterion status")
        require(criterion["status"] in CRITERION_STATUSES, "invalid criterion status")
        require(isinstance(criterion["evidence"], list), "criterion evidence must be a list")

    require(isinstance(record["findings"], list), "legacy findings must be a list")
    for finding in record["findings"]:
        require(isinstance(finding, dict), "legacy finding must be an object")
        require(
            set(finding) == {"id", "source", "severity", "summary", "disposition", "evidence"},
            "invalid finding keys",
        )
        require_string(finding["severity"], "legacy finding severity")
        require(finding["severity"] in {"CRITICAL", "HIGH", "MEDIUM", "LOW"}, "invalid severity")
        require_string(finding["disposition"], "legacy finding disposition")
        require(finding["disposition"] in FINDING_DISPOSITIONS, "invalid finding disposition")

    require(isinstance(record["handoffs"], list), "legacy handoffs must be a list")
    for handoff in record["handoffs"]:
        require(isinstance(handoff, dict), "legacy handoff must be an object")
        required_handoff = {"from", "to", "frozen_ref", "scope", "constraints", "expected_evidence"}
        require(set(handoff) == required_handoff, "invalid handoff keys")
        require(bool(handoff["scope"]), "handoff scope is required")
        require(bool(handoff["expected_evidence"]), "handoff evidence is required")

    validate_checks_and_delivery(record)
    binding_reports = []
    for binding in record["bindings"]:
        current = team["roles"].get(binding["role"])
        changed = current is None or any(
            binding[key] != current[key]
            for key in ("model", "reasoning", "config_fingerprint")
        )
        binding_reports.append(
            {
                "requested_role": binding["role"],
                "legacy_claim": binding["state"],
                "role": "LEGACY_CLAIM",
                "model": "UNKNOWN",
                "reasoning": "UNKNOWN",
                "permissions": "UNKNOWN",
                "comparison_to_current": "DRIFTED" if changed else "SAME_DECLARATION",
            }
        )
    return {
        "schema_version": LEGACY_RECORD_VERSION,
        "compatibility": "LEGACY_READ_ONLY",
        "manifest_comparison": "UNKNOWN",
        "bindings": binding_reports,
        "metrics": {
            "elapsed_time_seconds": "UNKNOWN",
            "remediation_cycles": "UNKNOWN",
            "unique_findings": "UNKNOWN",
            "duplicate_findings": "UNKNOWN",
            "handoff_reasons": "UNKNOWN",
            "root_interventions": "UNKNOWN",
        },
    }


def frozen_context(workflow: str, team: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    workflow_roles = []
    for role in manifest["workflows"][workflow]["roles"]:
        declared = team["roles"][role]
        workflow_roles.append({"role": role, **declared})
    root = {"role": "root", **team["root"]}
    payload = {
        "manifest_fingerprint": team["manifest_fingerprint"],
        "config_fingerprint": team["config_fingerprint"],
        "workflow": workflow,
        "root": root,
        "workflow_roles": workflow_roles,
    }
    return {
        "manifest_fingerprint": team["manifest_fingerprint"],
        "config_fingerprint": team["config_fingerprint"],
        "context_fingerprint": canonical_fingerprint(payload),
        "root": root,
        "workflow_roles": workflow_roles,
    }


def validate_declaration(declaration: dict[str, Any], field: str) -> None:
    require(isinstance(declaration, dict), f"{field} declaration must be an object")
    require(
        set(declaration)
        == {
            "role",
            "kind",
            "model",
            "reasoning",
            "permissions",
            "capabilities",
            "forbidden_capabilities",
            "config_fingerprint",
        },
        f"invalid {field} declaration keys",
    )
    require_string(declaration["kind"], f"{field} kind")
    require(declaration["kind"] in {"reader", "reviewer", "writer"}, f"invalid {field} kind")
    require(isinstance(declaration["role"], str) and declaration["role"], f"invalid {field} role")
    require(isinstance(declaration["model"], str) and declaration["model"], f"invalid {field} model")
    require_string(declaration["reasoning"], f"{field} reasoning")
    require(declaration["reasoning"] in {"medium", "high"}, f"invalid {field} reasoning")
    require_string(declaration["permissions"], f"{field} permissions")
    require(declaration["permissions"] in {":read-only", ":workspace"}, f"invalid {field} permissions")
    expected_permissions = ":workspace" if declaration["kind"] == "writer" else ":read-only"
    require(declaration["permissions"] == expected_permissions, f"unsafe {field} role permissions")
    require_string_list(declaration["capabilities"], f"{field} capabilities", nonempty=True)
    require_string_list(
        declaration["forbidden_capabilities"],
        f"{field} forbidden capabilities",
    )
    require(
        not set(declaration["capabilities"])
        & set(declaration["forbidden_capabilities"]),
        f"{field} capability is both required and forbidden",
    )
    require_string(declaration["config_fingerprint"], f"{field} config fingerprint")
    require(
        re.fullmatch(r"[a-f0-9]{64}", declaration["config_fingerprint"]) is not None,
        f"invalid {field} config fingerprint",
    )


def validate_attestation(
    attestation: dict[str, Any],
    field: str,
    declared_value: str,
    fallback_role: bool,
) -> None:
    require(isinstance(attestation, dict), f"{field} attestation must be an object")
    require(set(attestation) == {"value", "state", "provenance"}, f"invalid {field} attestation keys")
    require_string(attestation["state"], f"{field} attestation state")
    require(attestation["state"] in ATTESTATION_STATES, f"invalid {field} attestation state")
    require(
        isinstance(attestation["provenance"], str) and attestation["provenance"],
        f"{field} provenance is required",
    )
    if attestation["state"] == "UNKNOWN":
        require(attestation["value"] is None, f"unknown {field} must not claim a value")
        return
    require(isinstance(attestation["value"], str) and attestation["value"], f"{field} value is required")
    if field == "reasoning":
        require(attestation["value"] in {"medium", "high"}, "invalid attested reasoning")
    if field == "permissions":
        require(attestation["value"] in {":read-only", ":workspace"}, "invalid attested permissions")
    if attestation["state"] in {"DECLARED", "VERIFIED"}:
        if not (field == "role" and fallback_role):
            require(
                attestation["value"] == declared_value,
                f"{attestation['state'].lower()} {field} differs from frozen context",
            )
    if attestation["state"] == "FALLBACK" and field == "model":
        require(
            attestation["value"] == declared_value,
            "fallback model must equal the frozen declared model",
        )
    if attestation["state"] == "FALLBACK" and field == "reasoning":
        rank = {"medium": 1, "high": 2}
        require(
            rank[attestation["value"]] >= rank[declared_value],
            "fallback reasoning is weaker than the frozen declaration",
        )
    if attestation["state"] == "FALLBACK" and field == "permissions":
        require(
            attestation["value"] == declared_value,
            "fallback permissions violate the frozen role boundary",
        )


def validate_evidence(evidence: dict[str, Any], participants: set[str], field: str) -> None:
    require(isinstance(evidence, dict), f"{field} evidence must be an object")
    require(set(evidence) == {"source_kind", "source", "summary"}, f"invalid {field} evidence keys")
    require_string(evidence["source_kind"], f"{field} evidence source kind")
    require(evidence["source_kind"] in {"ROLE", "EXTERNAL"}, f"invalid {field} evidence source kind")
    require(isinstance(evidence["source"], str) and evidence["source"], f"{field} evidence source is required")
    require(isinstance(evidence["summary"], str) and evidence["summary"], f"{field} evidence summary is required")
    if evidence["source_kind"] == "ROLE":
        require(evidence["source"] in participants, f"{field} evidence names a non-participating role")


def validate_current_record(
    record: dict[str, Any],
    team: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    required = {
        "schema_version",
        "run_id",
        "workflow",
        "status",
        "target",
        "team_context",
        "bindings",
        "events",
        "criteria",
        "findings",
        "handoffs",
        "checks",
        "delivery",
    }
    validate_record_header(record, required)
    context = record["team_context"]
    require(isinstance(context, dict), "frozen team context must be an object")
    require(
        set(context) == {
            "manifest_fingerprint",
            "config_fingerprint",
            "context_fingerprint",
            "root",
            "workflow_roles",
        },
        "invalid frozen team context keys",
    )
    for field in ("manifest_fingerprint", "config_fingerprint", "context_fingerprint"):
        require_string(context[field], field)
        require(re.fullmatch(r"[a-f0-9]{64}", context[field]) is not None, f"invalid {field}")
    require(isinstance(context["workflow_roles"], list), "frozen workflow roles must be a list")
    validate_declaration(context["root"], "root")
    require(context["root"]["role"] == "root", "frozen root role is invalid")
    require(context["root"]["kind"] == "writer", "frozen root kind is invalid")
    role_names: set[str] = set()
    role_fingerprints: set[str] = set()
    declarations: dict[str, dict[str, Any]] = {"root": context["root"]}
    for declaration in context["workflow_roles"]:
        validate_declaration(declaration, "workflow role")
        role = declaration["role"]
        require(role not in role_names and role != "root", "duplicate frozen workflow role")
        require(declaration["config_fingerprint"] not in role_fingerprints, "copied workflow role fingerprint")
        role_names.add(role)
        role_fingerprints.add(declaration["config_fingerprint"])
        declarations[role] = declaration
    payload = {
        "manifest_fingerprint": context["manifest_fingerprint"],
        "config_fingerprint": context["config_fingerprint"],
        "workflow": record["workflow"],
        "root": context["root"],
        "workflow_roles": context["workflow_roles"],
    }
    require(
        canonical_fingerprint(payload) == context["context_fingerprint"],
        "frozen team context fingerprint mismatch",
    )
    expected = frozen_context(record["workflow"], team, manifest)
    manifest_comparison = "CURRENT" if context == expected else "DRIFTED"

    require(isinstance(record["bindings"], list), "bindings must be a list")
    participants: set[str] = set()
    participant_bindings: dict[str, dict[str, Any]] = {}
    thread_ids: set[str] = set()
    binding_reports = []
    for binding in record["bindings"]:
        require(isinstance(binding, dict), "participating binding must be an object")
        required_binding = {
            "requested_role",
            "thread_id",
            "role",
            "model",
            "reasoning",
            "permissions",
        }
        require(set(binding) == required_binding, "invalid participating binding keys")
        require_string(binding["requested_role"], "requested role")
        require(binding["thread_id"] is None or isinstance(binding["thread_id"], str), "invalid binding thread id")
        if binding["thread_id"] is not None:
            require(binding["thread_id"] not in thread_ids, "runtime thread participates as multiple roles")
            thread_ids.add(binding["thread_id"])
        requested = binding["requested_role"]
        require(requested in declarations, "binding role is outside the frozen workflow")
        require(requested not in participants, "duplicate participating role binding")
        participants.add(requested)
        declared = declarations[requested]
        role_attestation = binding["role"]
        validate_attestation(role_attestation, "role", requested, False)
        fallback_role = role_attestation["state"] == "FALLBACK"
        if fallback_role:
            require(role_attestation["value"] != requested, "fallback role must identify a different actual role")
        for field in ("model", "reasoning", "permissions"):
            validate_attestation(binding[field], field, declared[field], fallback_role)
        permission_attestation = binding["permissions"]
        if permission_attestation["state"] != "UNKNOWN":
            require(
                permission_attestation["value"] == declared["permissions"],
                "attested permissions violate the requested role boundary",
            )
        if fallback_role:
            require(
                permission_attestation["state"] in {"VERIFIED", "FALLBACK"},
                "fallback permissions require evidence about the actual fallback",
            )
        participant_bindings[requested] = binding
        binding_reports.append(
            {
                "requested_role": requested,
                "thread_id": binding["thread_id"],
                "role": dict(role_attestation),
                "model": dict(binding["model"]),
                "reasoning": dict(binding["reasoning"]),
                "permissions": dict(binding["permissions"]),
            }
        )

    require(isinstance(record["criteria"], list), "criteria must be a list")
    for criterion in record["criteria"]:
        require(isinstance(criterion, dict), "criterion must be an object")
        require(set(criterion) == {"id", "text", "status", "evidence"}, "invalid criterion keys")
        require(isinstance(criterion["id"], str) and criterion["id"], "criterion id is required")
        require(isinstance(criterion["text"], str) and criterion["text"], "criterion text is required")
        require_string(criterion["status"], "criterion status")
        require(criterion["status"] in CRITERION_STATUSES, "invalid criterion status")
        require(isinstance(criterion["evidence"], list), "criterion evidence must be a list")
        for evidence in criterion["evidence"]:
            validate_evidence(evidence, participants, "criterion")

    require(isinstance(record["findings"], list), "findings must be a list")
    finding_ids: set[str] = set()
    for finding in record["findings"]:
        require(isinstance(finding, dict), "finding must be an object")
        required_finding_keys = {
                "id",
                "source_kind",
                "source",
                "severity",
                "summary",
                "disposition",
                "duplicate_of",
                "evidence",
        }
        optional_finding_keys = {"stage", "family", "miss_reason"}
        require(
            required_finding_keys <= set(finding)
            and set(finding) <= required_finding_keys | optional_finding_keys,
            "invalid finding keys",
        )
        require(isinstance(finding["id"], str) and finding["id"], "finding id is required")
        require(finding["id"] not in finding_ids, "duplicate finding id")
        finding_ids.add(finding["id"])
        require_string(finding["source_kind"], "finding source kind")
        require(finding["source_kind"] in {"ROLE", "EXTERNAL"}, "invalid finding source kind")
        require(isinstance(finding["source"], str) and finding["source"], "finding source is required")
        require(isinstance(finding["summary"], str) and finding["summary"], "finding summary is required")
        if finding["source_kind"] == "ROLE":
            require(finding["source"] in participants, "finding names a non-participating role")
        require_string(finding["severity"], "finding severity")
        require(finding["severity"] in {"CRITICAL", "HIGH", "MEDIUM", "LOW"}, "invalid severity")
        require_string(finding["disposition"], "finding disposition")
        require(finding["disposition"] in FINDING_DISPOSITIONS, "invalid finding disposition")
        require(
            finding["duplicate_of"] is None or isinstance(finding["duplicate_of"], str),
            "invalid duplicate lineage",
        )
        require_string_list(finding["evidence"], "finding evidence")
        stage = finding.get("stage")
        require(
            stage is None or (isinstance(stage, str) and re.fullmatch(r"(?:I|PR|C)[0-9]+", stage)),
            "invalid finding stage",
        )
        family = finding.get("family")
        require(family is None or (isinstance(family, str) and family), "invalid finding family")
        miss_reason = finding.get("miss_reason")
        require(miss_reason is None or miss_reason in MISS_REASONS, "invalid finding miss reason")
    findings_by_id = {finding["id"]: finding for finding in record["findings"]}
    for finding in record["findings"]:
        duplicate_of = finding["duplicate_of"]
        if duplicate_of is None:
            continue
        require(duplicate_of != finding["id"], "finding cannot duplicate itself")
        require(duplicate_of in findings_by_id, "finding duplicate target is missing")
        require(findings_by_id[duplicate_of]["duplicate_of"] is None, "finding duplicate target must be canonical")

    writer_roles = {
        declaration["role"]
        for declaration in context["workflow_roles"]
        if declaration["kind"] == "writer"
    }
    allowed_writer_roles = WORKFLOW_WRITER_ROLES.get(record["workflow"], ("", set()))[1]
    require(writer_roles == allowed_writer_roles, "frozen writer candidates violate stable workflow policy")
    permitted_writers = set(writer_roles)
    if writer_roles:
        permitted_writers.add("root")
    require(isinstance(record["handoffs"], list), "handoffs must be a list")
    handoff_times: list[tuple[datetime, dict[str, Any]]] = []
    for handoff in record["handoffs"]:
        require(isinstance(handoff, dict), "handoff must be an object")
        required_handoff = {
            "from",
            "to",
            "at",
            "reason",
            "frozen_ref",
            "scope",
            "constraints",
            "expected_evidence",
        }
        require(set(handoff) == required_handoff, "invalid handoff keys")
        for field in ("from", "to", "at", "reason", "frozen_ref"):
            require_string(handoff[field], f"handoff {field}")
        require(handoff["from"] != handoff["to"], "handoff actors must differ")
        require(handoff["from"] in permitted_writers and handoff["to"] in permitted_writers, "invalid handoff writer")
        require(
            handoff["from"] in participants and handoff["to"] in participants,
            "handoff writer did not participate",
        )
        require_string_list(handoff["scope"], "handoff scope", nonempty=True)
        require_string_list(handoff["constraints"], "handoff constraints")
        require_string_list(handoff["expected_evidence"], "handoff expected evidence", nonempty=True)
        handoff_at = parse_timestamp(handoff["at"], "handoff")
        if handoff_times:
            require(handoff_at >= handoff_times[-1][0], "handoff chronology moves backwards")
        handoff_times.append((handoff_at, handoff))

    require(isinstance(record["events"], list) and record["events"], "events must be a nonempty list")
    event_times: list[datetime] = []
    active_writer: str | None = None
    active_writer_started: datetime | None = None
    ownership_timeline: list[tuple[datetime, int, str, dict[str, Any]]] = []
    phases: list[str] = []
    started_at: datetime | None = None
    ended_at: datetime | None = None
    for expected_sequence, event in enumerate(record["events"], start=1):
        require(isinstance(event, dict), "event must be an object")
        require(
            set(event) == {"sequence", "type", "at", "actor", "phase", "reason"},
            "invalid event keys",
        )
        require(
            isinstance(event["sequence"], int) and not isinstance(event["sequence"], bool),
            "event sequence must be an integer",
        )
        require(event["sequence"] == expected_sequence, "event sequence is not contiguous")
        require_string(event["type"], "event type")
        require(event["type"] in EVENT_TYPES, "invalid event type")
        require(event["actor"] is None or isinstance(event["actor"], str), "invalid event actor")
        require(event["phase"] is None or isinstance(event["phase"], str), "invalid event phase")
        require(event["reason"] is None or isinstance(event["reason"], str), "invalid event reason")
        at = parse_timestamp(event["at"], "event")
        if event_times:
            require(at >= event_times[-1], "event chronology moves backwards")
        event_times.append(at)
        event_type = event["type"]
        if event_type == "RUN_STARTED":
            require(expected_sequence == 1 and started_at is None, "run start must be the first event")
            require(event["actor"] is None and event["phase"] is None, "run start has invalid context")
            started_at = at
        elif event_type == "RUN_ENDED":
            require(expected_sequence == len(record["events"]), "run end must be the final event")
            require(ended_at is None and active_writer is None, "run ended with invalid active state")
            require(event["actor"] is None and event["phase"] is None, "run end has invalid context")
            ended_at = at
        elif event_type == "PHASE_ENTERED":
            require(event["phase"] in RECORD_STATUSES, "invalid phase event")
            require(event["actor"] is None, "phase event must not name an actor")
            phases.append(event["phase"])
        elif event_type == "WRITER_STARTED":
            require(event["actor"] in permitted_writers, "writer is not permitted by the frozen workflow")
            require(event["actor"] in participants, "writer start names a non-participating role")
            require(active_writer is None, "writer activity overlaps another writer")
            require(isinstance(event["reason"], str) and event["reason"], "writer start reason is required")
            writer_binding = participant_bindings[event["actor"]]
            require(
                writer_binding["permissions"]["state"] != "UNKNOWN"
                and writer_binding["permissions"]["value"] == ":workspace",
                "writer activity lacks a workspace-permission attestation",
            )
            active_writer = event["actor"]
            active_writer_started = at
            ownership_timeline.append((at, 2, "START", event))
        elif event_type == "WRITER_STOPPED":
            require(event["actor"] == active_writer, "writer stop does not match the active writer")
            require(isinstance(event["reason"], str) and event["reason"], "writer stop reason is required")
            require(active_writer_started is not None, "writer stop lacks a start")
            require(at > active_writer_started, "writer interval must have positive duration")
            ownership_timeline.append((at, 0, "STOP", event))
            active_writer = None
            active_writer_started = None
        if event_type not in {"PHASE_ENTERED"}:
            require(event["phase"] is None, "non-phase event must not carry a phase")
    require(started_at is not None, "current record lacks a run start event")
    require(bool(phases) and phases[-1] == record["status"], "record status differs from the latest phase event")
    for handoff_at, handoff in handoff_times:
        require(handoff_at >= started_at, "handoff predates the run")
        if ended_at is not None:
            require(handoff_at <= ended_at, "handoff occurs after the run ended")
        ownership_timeline.append((handoff_at, 1, "HANDOFF", handoff))

    owner: str | None = None
    timeline_active: str | None = None
    owner_has_completed_activity = False
    pending_handoff: dict[str, Any] | None = None
    root_interventions: list[str] = []
    for _, _, action, item in sorted(ownership_timeline, key=lambda entry: (entry[0], entry[1])):
        if action == "START":
            actor = item["actor"]
            reason = item["reason"]
            require(timeline_active is None, "writer ownership timeline overlaps")
            if owner is None:
                require(actor != "root", "root writer start lacks a preceding ownership handoff")
                owner = actor
            require(actor == owner, "writer ownership changed without a handoff")
            if pending_handoff is not None:
                require(reason == pending_handoff["reason"], "writer start reason differs from the handoff")
                if actor == "root":
                    root_interventions.append(reason)
                pending_handoff = None
            timeline_active = actor
        elif action == "STOP":
            actor = item["actor"]
            require(timeline_active == actor, "writer ownership stop does not match the active owner")
            timeline_active = None
            owner_has_completed_activity = True
        else:
            handoff = item
            require(timeline_active is None, "ownership handoff occurs during writer activity")
            require(owner == handoff["from"], "handoff source does not own writer activity")
            require(owner_has_completed_activity, "handoff source lacks completed writer activity")
            require(pending_handoff is None, "ownership handoff was not consumed before another handoff")
            owner = handoff["to"]
            owner_has_completed_activity = False
            pending_handoff = handoff

    validate_checks_and_delivery(record)
    remediation_cycles = 0
    open_remediation = False
    for phase in phases:
        if phase == "REMEDIATION":
            require(not open_remediation, "remediation phase re-entered before closure")
            open_remediation = True
        elif open_remediation and phase in {"REVIEW", "QUALIFICATION"}:
            remediation_cycles += 1
            open_remediation = False
    if ended_at is not None:
        require(not open_remediation, "run ended with an open remediation cycle")
    elapsed: int | str = "UNKNOWN"
    if ended_at is not None:
        elapsed = int((ended_at - started_at).total_seconds())
        require(elapsed >= 0, "run duration is impossible")
    unique_findings = sum(finding["duplicate_of"] is None for finding in record["findings"])
    duplicate_findings = len(record["findings"]) - unique_findings
    canonical_findings = [finding for finding in record["findings"] if finding["duplicate_of"] is None]
    findings_by_stage: dict[str, int] = {}
    miss_reasons: dict[str, int] = {}
    for finding in canonical_findings:
        if finding.get("stage"):
            findings_by_stage[finding["stage"]] = findings_by_stage.get(finding["stage"], 0) + 1
        if finding.get("miss_reason"):
            miss_reasons[finding["miss_reason"]] = miss_reasons.get(finding["miss_reason"], 0) + 1
    connector_escapes = sum(
        finding.get("stage") in {"C1", "C2"}
        and finding["disposition"] != "DISPROVED"
        for finding in canonical_findings
    )
    return {
        "schema_version": PREVIOUS_RECORD_VERSION,
        "compatibility": "LEGACY_V2_READ_ONLY",
        "manifest_comparison": manifest_comparison,
        "bindings": binding_reports,
        "metrics": {
            "elapsed_time_seconds": elapsed,
            "elapsed_time_basis": {
                "started_at": record["events"][0]["at"],
                "ended_at": record["events"][-1]["at"] if ended_at is not None else "UNKNOWN",
            },
            "remediation_cycles": remediation_cycles,
            "open_remediation": open_remediation,
            "unique_findings": unique_findings,
            "duplicate_findings": duplicate_findings,
            "findings_by_stage": dict(sorted(findings_by_stage.items())),
            "miss_reasons": dict(sorted(miss_reasons.items())),
            "connector_escape_findings": connector_escapes,
            "handoff_reasons": [handoff["reason"] for _, handoff in handoff_times],
            "root_interventions": len(root_interventions),
            "root_intervention_reasons": root_interventions,
        },
    }


def require_exact_keys(value: Any, keys: set[str], field: str) -> dict[str, Any]:
    require(isinstance(value, dict), f"{field} must be an object")
    require(set(value) == keys, f"invalid {field} keys")
    return value


def require_optional_string(value: Any, field: str) -> None:
    require(value is None or (isinstance(value, str) and value), f"{field} must be a nonempty string or null")


def require_digest(value: Any, field: str) -> None:
    require_string(value, field)
    require(re.fullmatch(r"[a-f0-9]{64}", value) is not None, f"invalid {field}")


def require_optional_digest(value: Any, field: str) -> None:
    require(value is None or (isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value)), f"invalid {field}")


def parse_stage(value: Any, field: str = "stage") -> tuple[str, int]:
    require_string(value, field)
    match = re.fullmatch(r"(I|PR)(0|[1-9][0-9]*)", value)
    if match:
        return match.group(1), int(match.group(2))
    match = re.fullmatch(r"C([12])", value)
    require(match is not None, f"invalid {field}; C0 and C3+ are prohibited")
    return "C", int(match.group(1))


def frozen_context_v3(workflow: str, team: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    workflow_roles = []
    for role in manifest["workflows"][workflow]["roles"]:
        workflow_roles.append({"role": role, **team["roles"][role]})
    root = {"role": "root", **team["root"]}
    payload = {
        "manifest_fingerprint": team["manifest_fingerprint"],
        "config_fingerprint": team["config_fingerprint"],
        "policy_bundle_digest": team["policy_bundle"]["digest"],
        "workflow": workflow,
        "root": root,
        "workflow_roles": workflow_roles,
    }
    return {
        "manifest_fingerprint": team["manifest_fingerprint"],
        "config_fingerprint": team["config_fingerprint"],
        "policy_bundle_digest": team["policy_bundle"]["digest"],
        "context_fingerprint": canonical_fingerprint(payload),
        "root": root,
        "workflow_roles": workflow_roles,
    }


def validate_policy_bundle_record(value: Any) -> dict[str, Any]:
    bundle = require_exact_keys(value, {"algorithm", "digest", "files"}, "policy bundle")
    require(bundle["algorithm"] == "sha256", "unsupported policy bundle algorithm")
    require_digest(bundle["digest"], "policy bundle digest")
    require(isinstance(bundle["files"], list) and bundle["files"], "policy bundle files must be nonempty")
    paths: list[str] = []
    for item in bundle["files"]:
        item = require_exact_keys(item, {"path", "digest"}, "policy bundle file")
        require_string(item["path"], "policy bundle path")
        require(item["path"].startswith(".codex/") and ".." not in Path(item["path"]).parts, "unsafe policy bundle path")
        require_digest(item["digest"], "policy body digest")
        paths.append(item["path"])
    require(paths == sorted(set(paths)), "policy bundle paths must be unique and sorted")
    require(canonical_fingerprint(bundle["files"]) == bundle["digest"], "policy bundle digest mismatch")
    return bundle


def validate_v3_context(
    record: dict[str, Any], team: dict[str, Any], manifest: dict[str, Any]
) -> tuple[dict[str, dict[str, Any]], str]:
    context = require_exact_keys(
        record["team_context"],
        {
            "manifest_fingerprint",
            "config_fingerprint",
            "policy_bundle_digest",
            "context_fingerprint",
            "root",
            "workflow_roles",
        },
        "frozen team context",
    )
    for field in (
        "manifest_fingerprint",
        "config_fingerprint",
        "policy_bundle_digest",
        "context_fingerprint",
    ):
        require_digest(context[field], field)
    require(context["policy_bundle_digest"] == record["policy_bundle"]["digest"], "context and policy bundle digests differ")
    validate_declaration(context["root"], "root")
    require(context["root"]["role"] == "root" and context["root"]["kind"] == "writer", "invalid frozen root")
    require(isinstance(context["workflow_roles"], list), "frozen workflow roles must be a list")
    declarations = {"root": context["root"]}
    fingerprints: set[str] = set()
    for declaration_value in context["workflow_roles"]:
        validate_declaration(declaration_value, "workflow role")
        role = declaration_value["role"]
        require(role not in declarations, "duplicate frozen workflow role")
        require(declaration_value["config_fingerprint"] not in fingerprints, "copied workflow role fingerprint")
        declarations[role] = declaration_value
        fingerprints.add(declaration_value["config_fingerprint"])
    payload = {
        "manifest_fingerprint": context["manifest_fingerprint"],
        "config_fingerprint": context["config_fingerprint"],
        "policy_bundle_digest": context["policy_bundle_digest"],
        "workflow": record["workflow"],
        "root": context["root"],
        "workflow_roles": context["workflow_roles"],
    }
    require(canonical_fingerprint(payload) == context["context_fingerprint"], "frozen team context fingerprint mismatch")
    expected = frozen_context_v3(record["workflow"], team, manifest)
    return declarations, "CURRENT" if context == expected else "DRIFTED"


def validate_v3_bindings(
    record: dict[str, Any],
    declarations: dict[str, dict[str, Any]],
    target_ids: set[str],
) -> tuple[set[str], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    require(isinstance(record["bindings"], list), "bindings must be a list")
    participants: set[str] = set()
    binding_ids: set[str] = set()
    thread_roles: dict[str, str] = {}
    by_id: dict[str, dict[str, Any]] = {}
    reports = []
    for binding in record["bindings"]:
        binding = require_exact_keys(
            binding,
            {
                "binding_id",
                "target_id",
                "ref",
                "requested_role",
                "thread_id",
                "role",
                "model",
                "reasoning",
                "permissions",
                "capability_evidence",
            },
            "binding",
        )
        require_string(binding["binding_id"], "binding id")
        require(binding["binding_id"] not in binding_ids, "duplicate binding id")
        require(
            binding["target_id"] is None or binding["target_id"] in target_ids,
            "binding target is unknown",
        )
        require_string(binding["ref"], "binding ref")
        require_string(binding["requested_role"], "requested role")
        requested = binding["requested_role"]
        require(requested in declarations, "binding role is outside the frozen workflow")
        require(binding["thread_id"] is None or (isinstance(binding["thread_id"], str) and binding["thread_id"]), "invalid binding thread id")
        declaration_value = declarations[requested]
        if binding["thread_id"] is not None:
            prior_role = thread_roles.get(binding["thread_id"])
            require(
                prior_role in {None, requested},
                "runtime thread participates as multiple roles",
            )
            thread_roles[binding["thread_id"]] = requested
        validate_attestation(binding["role"], "role", requested, False)
        fallback_role = binding["role"]["state"] == "FALLBACK"
        if fallback_role:
            require(binding["role"]["value"] != requested, "fallback role must identify the actual fallback")
        if (
            declaration_value["kind"] == "reviewer"
            and binding["role"]["value"] is not None
        ):
            require(
                binding["role"]["value"]
                in {requested, "isolated_cli_reviewer"},
                "reviewer binding actual role exceeds the frozen fallback contract",
            )
        for field in ("model", "reasoning", "permissions"):
            validate_attestation(binding[field], field, declaration_value[field], fallback_role)
        permission = binding["permissions"]
        if permission["state"] != "UNKNOWN":
            require(permission["value"] == declaration_value["permissions"], "attested permissions violate the requested role boundary")
        if fallback_role:
            require(
                permission["state"] in {"VERIFIED", "UNKNOWN"},
                "fallback permissions must be runtime-verified or explicitly unknown",
            )
        capability_evidence = require_exact_keys(
            binding["capability_evidence"],
            {"verified", "absent", "provenance"},
            "binding capability evidence",
        )
        require_string_list(
            capability_evidence["verified"], "verified capabilities"
        )
        require_string_list(capability_evidence["absent"], "absent capabilities")
        require_string(
            capability_evidence["provenance"], "capability evidence provenance"
        )
        require(
            not set(capability_evidence["verified"])
            & set(capability_evidence["absent"]),
            "capability is both verified and absent",
        )
        binding_ids.add(binding["binding_id"])
        participants.add(requested)
        by_id[binding["binding_id"]] = binding
        reports.append({key: binding[key] for key in binding})
    return participants, by_id, reports


def validate_v3_evidence(value: Any, participants: set[str], target_ids: set[str], field: str) -> None:
    evidence = require_exact_keys(
        value,
        {"id", "source_kind", "source", "target_id", "ref", "summary", "evidence_state"},
        f"{field} evidence",
    )
    require_string(evidence["id"], f"{field} evidence id")
    require(evidence["source_kind"] in {"ROLE", "EXTERNAL"}, f"invalid {field} evidence source kind")
    require_string(evidence["source"], f"{field} evidence source")
    require(evidence["target_id"] in target_ids, f"{field} evidence target is unknown")
    require_string(evidence["ref"], f"{field} evidence ref")
    require_string(evidence["summary"], f"{field} evidence summary")
    require(evidence["evidence_state"] in EVIDENCE_STATES, f"invalid {field} evidence state")
    if evidence["source_kind"] == "ROLE":
        require(evidence["source"] in participants, f"{field} evidence names a non-participating role")


def binding_is_qualified(
    binding: dict[str, Any], declaration: dict[str, Any]
) -> bool:
    return (
        all(
            binding[field]["state"] in {"VERIFIED", "FALLBACK"}
            for field in ("role", "model", "reasoning")
        )
        and binding["permissions"]["state"] == "VERIFIED"
        and set(declaration["capabilities"])
        <= set(binding["capability_evidence"]["verified"])
        and set(declaration["forbidden_capabilities"])
        <= set(binding["capability_evidence"]["absent"])
    )


def validate_v3_targets(record: dict[str, Any]) -> tuple[set[str], dict[str, dict[str, Any]]]:
    require(isinstance(record["targets"], list) and record["targets"], "targets must be a nonempty list")
    target_ids: set[str] = set()
    external_targets: set[tuple[str, str]] = set()
    by_id: dict[str, dict[str, Any]] = {}
    allowed = WORKFLOW_TARGET_KINDS[record["workflow"]]
    for target in record["targets"]:
        target = require_exact_keys(target, {"target_id", "kind", "id", "initial_ref", "final_ref"}, "target")
        require_string(target["target_id"], "target id")
        require(target["target_id"] not in target_ids, "duplicate target id")
        require(target["kind"] in allowed, "workflow/target mismatch")
        require_string(target["id"], "external target id")
        require((target["kind"], target["id"]) not in external_targets, "duplicate external target")
        require_string(target["initial_ref"], "target initial ref")
        require_optional_string(target["final_ref"], "target final ref")
        target_ids.add(target["target_id"])
        external_targets.add((target["kind"], target["id"]))
        by_id[target["target_id"]] = target
    return target_ids, by_id


def validate_v3_repository_and_artifacts(record: dict[str, Any], target_ids: set[str]) -> set[str]:
    repository = require_exact_keys(
        record["repository"],
        {"repository_id", "base_ref", "base_tree", "head_ref", "head_tree"},
        "repository identity",
    )
    require_string(repository["repository_id"], "repository id")
    require_string(repository["base_ref"], "repository base ref")
    require_optional_digest(repository["base_tree"], "repository base tree")
    require_optional_string(repository["head_ref"], "repository head ref")
    require_optional_digest(repository["head_tree"], "repository head tree")
    require(isinstance(record["artifacts"], list), "artifacts must be a list")
    artifact_ids: set[str] = set()
    for artifact in record["artifacts"]:
        artifact = require_exact_keys(
            artifact,
            {"id", "version", "target_id", "ref", "kind", "identifier", "digest", "built_at", "environment_digest"},
            "artifact",
        )
        require_string(artifact["id"], "artifact id")
        require(artifact["id"] not in artifact_ids, "duplicate artifact id")
        require(artifact["version"] == 1, "unsupported artifact version")
        require(artifact["target_id"] in target_ids, "artifact target is unknown")
        require_string(artifact["ref"], "artifact ref")
        require(
            artifact["kind"]
            in {
                "REPOSITORY",
                "TREE",
                "PACKAGE",
                "MODULE",
                "WHEEL",
                "DOCUMENT",
                "REVIEW_PACKET",
                "OTHER",
            },
            "invalid artifact kind",
        )
        require_string(artifact["identifier"], "artifact identifier")
        require_digest(artifact["digest"], "artifact digest")
        parse_timestamp(artifact["built_at"], "artifact built_at")
        require_digest(artifact["environment_digest"], "artifact environment digest")
        artifact_ids.add(artifact["id"])
    return artifact_ids


def validate_review_packet_blob(value: Any, label: str) -> dict[str, Any]:
    blob = require_exact_keys(
        value,
        {"id", "kind", "purpose", "source", "revision", "sha256", "content"},
        label,
    )
    require_string(blob["id"], f"{label} id")
    require(
        blob["kind"] in {"INLINE", "REPO_BLOB", "RUN_BLOB", "DIFF", "NAME_STATUS"},
        f"invalid {label} kind",
    )
    require(
        blob["purpose"]
        in {
            "REQUIREMENT",
            "ROLE_CONFIG",
            "POLICY",
            "SOURCE",
            "DIFF",
            "NAME_STATUS",
            "EVIDENCE",
            "REPAIR",
        },
        f"invalid {label} purpose",
    )
    require_string(blob["source"], f"{label} source")
    require_string(blob["revision"], f"{label} revision")
    require_digest(blob["sha256"], f"{label} digest")
    require(isinstance(blob["content"], str), f"{label} content must be text")
    require(
        hashlib.sha256(blob["content"].encode("utf-8")).hexdigest()
        == blob["sha256"],
        f"{label} content digest mismatch",
    )
    if blob["kind"] in {"REPO_BLOB", "RUN_BLOB"}:
        path = PurePosixPath(blob["source"])
        require(
            not path.is_absolute()
            and path.as_posix() == blob["source"]
            and all(part not in {"", ".", ".."} for part in path.parts),
            f"unsafe {label} source path",
        )
    return blob


def validate_review_packet_snapshot(
    snapshot: str,
    digest: str,
) -> dict[str, Any]:
    try:
        packet = json.loads(snapshot, object_pairs_hook=duplicate_safe_object)
    except json.JSONDecodeError as error:
        raise ValidationError(f"invalid review packet JSON: {error}") from error
    require(isinstance(packet, dict), "review packet snapshot must be an object")
    canonical = canonical_json_bytes(packet)
    require(snapshot.encode("utf-8") == canonical, "review packet snapshot is not canonical")
    require(
        hashlib.sha256(canonical).hexdigest() == digest,
        "review packet snapshot digest mismatch",
    )
    packet = require_exact_keys(
        packet,
        {
            "schema_version",
            "packet_id",
            "workflow",
            "target",
            "repository",
            "role",
            "binding_request",
            "task",
            "artifacts",
            "lineage",
            "exclusions",
        },
        "review packet",
    )
    require(packet["schema_version"] == 2, "unsupported review packet version")
    require_string(packet["packet_id"], "review packet id")
    workflow = require_exact_keys(
        packet["workflow"],
        {"run_id", "workflow", "target_id", "stage", "context_mode"},
        "review packet workflow",
    )
    for field in ("run_id", "workflow", "target_id"):
        require_string(workflow[field], f"review packet workflow {field}")
    parse_stage(workflow["stage"], "review packet stage")
    require(
        workflow["context_mode"] in REVIEW_CONTEXT_MODES,
        "invalid review packet context mode",
    )

    target = require_exact_keys(
        packet["target"],
        {"kind", "id", "revision", "url", "requirements"},
        "review packet target",
    )
    for field in ("kind", "id", "revision"):
        require_string(target[field], f"review packet target {field}")
    require_optional_string(target["url"], "review packet target url")

    repository = require_exact_keys(
        packet["repository"],
        {
            "mode",
            "repository_id",
            "worktree",
            "base_ref",
            "target_ref",
            "tree",
            "changed_path_count",
            "diff",
            "name_status",
        },
        "review packet repository",
    )
    require(repository["mode"] in {"STAGED", "COMMIT", "NONE"}, "invalid review packet repository mode")
    require_string(repository["repository_id"], "review packet repository id")
    require_optional_string(repository["worktree"], "review packet worktree")
    require_optional_string(repository["base_ref"], "review packet base ref")
    require_string(repository["target_ref"], "review packet target ref")
    require_optional_string(repository["tree"], "review packet tree")
    require(
        isinstance(repository["changed_path_count"], int)
        and not isinstance(repository["changed_path_count"], bool)
        and repository["changed_path_count"] >= 0,
        "invalid review packet changed-path count",
    )
    if repository["mode"] == "NONE":
        require(
            repository["worktree"] is None
            and repository["base_ref"] is None
            and repository["tree"] is None
            and repository["changed_path_count"] == 0
            and repository["diff"] is None
            and repository["name_status"] is None,
            "non-Git review packet contains Git state",
        )
    else:
        require(
            repository["worktree"] is not None
            and repository["base_ref"] is not None
            and repository["tree"] is not None
            and repository["diff"] is not None
            and repository["name_status"] is not None,
            "Git review packet lacks immutable repository evidence",
        )
    require(
        target["revision"] == repository["target_ref"],
        "review packet target revision differs from its frozen target ref",
    )

    role = require_exact_keys(
        packet["role"],
        {
            "requested_role",
            "config",
            "model",
            "reasoning",
            "permissions",
            "required_capabilities",
            "forbidden_capabilities",
            "policies",
        },
        "review packet role",
    )
    require_string(role["requested_role"], "review packet requested role")
    require_string(role["model"], "review packet role model")
    require(role["reasoning"] in {"medium", "high"}, "invalid review packet reasoning")
    require(role["permissions"] == ":read-only", "review packet role is not read-only")
    require_string_list(role["required_capabilities"], "review packet required capabilities", nonempty=True)
    require_string_list(role["forbidden_capabilities"], "review packet forbidden capabilities")
    current_team = validate_team()
    manifest = load_toml(MANIFEST)
    require(
        role["requested_role"] in current_team["roles"]
        and manifest["roles"][role["requested_role"]]["kind"] == "reviewer",
        "review packet role is not a configured reviewer",
    )
    declared_role = current_team["roles"][role["requested_role"]]
    require(
        role["model"] == declared_role["model"]
        and role["reasoning"] == declared_role["reasoning"]
        and role["permissions"] == declared_role["permissions"]
        and role["required_capabilities"] == declared_role["capabilities"]
        and role["forbidden_capabilities"]
        == declared_role["forbidden_capabilities"],
        "review packet role declaration differs from the configured role",
    )

    binding = require_exact_keys(
        packet["binding_request"],
        {
            "binding_id",
            "allowed_actual_roles",
            "launch",
            "originating_review_id",
            "originating_binding_id",
            "originating_thread_id",
        },
        "review packet binding request",
    )
    require_string(binding["binding_id"], "review packet binding id")
    require_string_list(
        binding["allowed_actual_roles"],
        "review packet allowed actual roles",
        nonempty=True,
    )
    allowed_roles = binding["allowed_actual_roles"]
    require(
        allowed_roles[0] == role["requested_role"]
        and set(allowed_roles) <= {role["requested_role"], "isolated_cli_reviewer"},
        "review packet actual-role fallback is unsafe",
    )
    launch = require_exact_keys(
        binding["launch"],
        {"ignore_user_config", "model", "reasoning", "approval_policy", "sandbox"},
        "review packet launch",
    )
    require(launch["ignore_user_config"] is True, "review launch must ignore user config")
    require(
        launch["model"] == role["model"]
        and launch["reasoning"] == role["reasoning"]
        and launch["approval_policy"] == "never"
        and launch["sandbox"] == "read-only",
        "review launch differs from the frozen safe role",
    )
    for field in (
        "originating_review_id",
        "originating_binding_id",
        "originating_thread_id",
    ):
        require_optional_string(binding[field], f"review packet {field}")

    task = require_exact_keys(
        packet["task"], {"text", "lens", "output_contract"}, "review packet task"
    )
    for field in task:
        require_string(task[field], f"review packet task {field}")

    lineage = require_exact_keys(
        packet["lineage"], {"finding_ids", "repair_evidence"}, "review packet lineage"
    )
    require_string_list(lineage["finding_ids"], "review packet finding lineage")

    exclusions = require_exact_keys(
        packet["exclusions"],
        {
            "worker_or_orchestrator_conclusions",
            "prior_findings_or_verdicts",
            "family_dispositions",
            "unrelated_remediation_history",
            "iteration_framing",
            "named_green_tests",
            "review_method",
            "family_policy",
            "review_synthesis",
        },
        "review packet exclusions",
    )
    for name in (
        "worker_or_orchestrator_conclusions",
        "prior_findings_or_verdicts",
        "family_dispositions",
        "unrelated_remediation_history",
        "iteration_framing",
        "named_green_tests",
        "review_synthesis",
    ):
        require(exclusions[name] is True, f"review packet must exclude {name}")

    blobs: list[dict[str, Any]] = []
    require(isinstance(target["requirements"], list) and target["requirements"], "review packet requirements are empty")
    blobs.extend(
        validate_review_packet_blob(item, "review packet requirement")
        for item in target["requirements"]
    )
    blobs.append(validate_review_packet_blob(role["config"], "review packet role config"))
    require(isinstance(role["policies"], list), "review packet policies must be an array")
    validated_policies = [
        validate_review_packet_blob(item, "review packet policy")
        for item in role["policies"]
    ]
    blobs.extend(validated_policies)
    expected_config_source = (
        f".codex/{manifest['roles'][role['requested_role']]['config']}"
    )
    expected_policy_sources = sorted(
        set(manifest["roles"][role["requested_role"]]["required_policies"])
        | {".codex/binding-preflight.md"}
    )
    require(
        role["config"]["source"] == expected_config_source
        and role["config"]["purpose"] == "ROLE_CONFIG"
        and [item["source"] for item in validated_policies]
        == expected_policy_sources
        and all(item["purpose"] == "POLICY" for item in validated_policies),
        "review packet role config/policy bundle is incomplete or mistyped",
    )
    require(
        role["config"]["content"]
        == (CODEX.parent / expected_config_source).read_text(encoding="utf-8")
        and all(
            item["content"]
            == (CODEX.parent / item["source"]).read_text(encoding="utf-8")
            for item in validated_policies
        ),
        "review packet role config/policy contents differ from the control plane",
    )
    expected_control_kind = "INLINE" if repository["mode"] == "NONE" else "REPO_BLOB"
    expected_control_revision = (
        current_team["policy_bundle"]["digest"]
        if repository["mode"] == "NONE"
        else repository["target_ref"]
    )
    require(
        role["config"]["kind"] == expected_control_kind
        and role["config"]["revision"] == expected_control_revision
        and all(
            item["kind"] == expected_control_kind
            and item["revision"] == expected_control_revision
            for item in validated_policies
        ),
        "review packet control artifacts are not bound to the target/control revision",
    )
    if repository["diff"] is not None:
        diff_blob = validate_review_packet_blob(repository["diff"], "review packet diff")
        name_status_blob = validate_review_packet_blob(
            repository["name_status"], "review packet name status"
        )
        blobs.extend((diff_blob, name_status_blob))
        require(
            repository["changed_path_count"] > 0
            and diff_blob["kind"] == "DIFF"
            and diff_blob["purpose"] == "DIFF"
            and diff_blob["source"] == "git-diff"
            and diff_blob["revision"]
            == f"{repository['base_ref']}..{repository['target_ref']}"
            and name_status_blob["kind"] == "NAME_STATUS"
            and name_status_blob["purpose"] == "NAME_STATUS"
            and name_status_blob["source"] == "git-name-status"
            and name_status_blob["revision"]
            == f"{repository['base_ref']}..{repository['target_ref']}"
            and len([line for line in name_status_blob["content"].splitlines() if line])
            == repository["changed_path_count"],
            "review packet Git evidence is empty, mistyped, or count-inconsistent",
        )
    require(isinstance(packet["artifacts"], list), "review packet artifacts must be an array")
    blobs.extend(
        validate_review_packet_blob(item, "review packet artifact")
        for item in packet["artifacts"]
    )
    require(isinstance(lineage["repair_evidence"], list), "review packet repair evidence must be an array")
    validated_repairs = [
        validate_review_packet_blob(item, "review packet repair evidence")
        for item in lineage["repair_evidence"]
    ]
    blobs.extend(validated_repairs)
    require(
        all(item["purpose"] == "REPAIR" for item in validated_repairs),
        "review packet repair evidence has the wrong purpose",
    )
    blob_ids = [item["id"] for item in blobs]
    require(blob_ids and len(blob_ids) == len(set(blob_ids)), "review packet blob IDs are empty or duplicated")
    require(all(item["purpose"] == "REQUIREMENT" for item in blobs[: len(target["requirements"])]), "review packet requirement has wrong purpose")
    require(
        all(item["revision"] == target["revision"] for item in target["requirements"])
        and all(item["revision"] == target["revision"] for item in packet["artifacts"])
        and all(item["revision"] == target["revision"] for item in validated_repairs),
        "review packet evidence revision differs from the frozen target",
    )
    run_prefix = f".codex/runs/{workflow['run_id']}/"
    safe_run_requirement = re.compile(
        r"^(issue|target|requirements?)-[A-Za-z0-9._-]+\.(md|txt|json)$"
    )
    require(
        all(
            item["revision"] == target["revision"]
            and not item["source"].startswith(".codex/runs/")
            for item in blobs
            if item["kind"] == "REPO_BLOB"
        ),
        "review packet repository blob is off-ref or aliases mutable run state",
    )
    require(
        all(
            item["revision"] == target["revision"]
            and item["purpose"] == "REQUIREMENT"
            and item["source"].startswith(run_prefix)
            and safe_run_requirement.fullmatch(PurePosixPath(item["source"]).name)
            is not None
            for item in blobs
            if item["kind"] == "RUN_BLOB"
        ),
        "review packet run blob is outside its restricted requirement namespace",
    )

    context_mode = workflow["context_mode"]
    if context_mode == "FRESH_ISOLATED":
        require(
            binding["originating_review_id"] is None
            and binding["originating_binding_id"] is None
            and binding["originating_thread_id"] is None
            and not lineage["finding_ids"]
            and not lineage["repair_evidence"],
            "fresh review packet inherits lineage",
        )
    else:
        require(
            binding["originating_review_id"] is not None
            and binding["originating_binding_id"] is not None
            and binding["originating_thread_id"] is not None
            and lineage["finding_ids"]
            and lineage["repair_evidence"],
            "continuation/replacement packet lacks bounded lineage",
        )
    if role["requested_role"] == PUBLICATION_CHALLENGE_ROLE:
        require(
            exclusions["review_method"]
            and exclusions["family_policy"]
            and exclusions["review_synthesis"],
            "publication packet includes shared synthesis context",
        )
        forbidden_sources = {REVIEW_METHOD, FAMILY_POLICY}
        require(
            not any(item["source"] in forbidden_sources for item in blobs),
            "publication packet embeds a forbidden shared review policy",
        )
    return packet


def packet_git(worktree: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(worktree), *arguments],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    require(
        completed.returncode == 0,
        f"review packet git {' '.join(arguments)} failed: {completed.stderr.strip()}",
    )
    return completed.stdout


def packet_git_status(worktree: Path, *arguments: str) -> int:
    completed = subprocess.run(
        ["git", "-C", str(worktree), *arguments],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    require(
        completed.returncode in {0, 1},
        f"review packet git {' '.join(arguments)} failed: {completed.stderr.strip()}",
    )
    return completed.returncode


def packet_repository_id(worktree: Path) -> str:
    remote = packet_git(worktree, "remote", "get-url", "origin").strip()
    match = re.search(r"github\.com(?::|/)([^/]+)/([^/]+?)(?:\.git)?$", remote)
    return f"{match.group(1)}/{match.group(2)}" if match else worktree.name


def validate_review_packet_live(
    packet: dict[str, Any],
    expected_repository: dict[str, Any] | None = None,
    *,
    trusted_worktree: Path | None = None,
) -> None:
    """Re-derive a Git packet from external state instead of trusting its hashes."""

    repository = packet["repository"]
    if repository["mode"] == "NONE":
        return
    packet_worktree = Path(repository["worktree"])
    worktree = trusted_worktree or packet_worktree
    require(
        worktree.is_absolute()
        and not worktree.is_symlink()
        and worktree.is_dir()
        and worktree.resolve() == worktree,
        "review packet worktree is unavailable or no longer normalized",
    )
    require(
        packet_worktree == worktree,
        "review packet worktree differs from the trusted qualification root",
    )
    repository_root = Path(
        packet_git(worktree, "rev-parse", "--show-toplevel").strip()
    ).resolve()
    require(
        repository_root == worktree,
        "trusted qualification root is not the Git worktree root",
    )
    require(
        packet_repository_id(worktree) == repository["repository_id"],
        "review packet repository identity drifted",
    )
    base_ref = packet_git(
        worktree, "rev-parse", f"{repository['base_ref']}^{{commit}}"
    ).strip()
    require(base_ref == repository["base_ref"], "review packet base ref drifted")
    base_tree = packet_git(
        worktree, "rev-parse", f"{base_ref}^{{tree}}"
    ).strip()
    if repository["mode"] == "STAGED":
        require(
            repository["target_ref"] == repository["tree"],
            "review packet staged target ref drifted",
        )
        current_tree = repository["target_ref"]
        require(
            packet_git(worktree, "cat-file", "-t", current_tree).strip()
            == "tree"
            and packet_git_status(
                worktree,
                "diff",
                "--cached",
                "--quiet",
                "--no-ext-diff",
                current_tree,
                "--",
            )
            == 0,
            "review packet staged target ref drifted",
        )
        diff_arguments = (
            "diff",
            "--find-renames",
            base_ref,
            current_tree,
            "--",
        )
        names_arguments = (
            "diff",
            "--name-status",
            base_ref,
            current_tree,
            "--",
        )
        blob_prefix = f"{current_tree}:"
    else:
        target_ref = packet_git(
            worktree,
            "rev-parse",
            f"{repository['target_ref']}^{{commit}}",
        ).strip()
        require(
            target_ref == repository["target_ref"],
            "review packet target ref drifted",
        )
        current_tree = packet_git(
            worktree, "rev-parse", f"{target_ref}^{{tree}}"
        ).strip()
        diff_arguments = (
            "diff",
            "--find-renames",
            base_ref,
            target_ref,
            "--",
        )
        names_arguments = (
            "diff",
            "--name-status",
            base_ref,
            target_ref,
            "--",
        )
        blob_prefix = f"{target_ref}:"
    require(current_tree == repository["tree"], "review packet repository tree drifted")
    require(
        packet_git(worktree, *diff_arguments) == repository["diff"]["content"],
        "review packet repository diff drifted",
    )
    require(
        packet_git(worktree, *names_arguments)
        == repository["name_status"]["content"],
        "review packet repository name-status drifted",
    )
    if expected_repository is not None:
        require(
            expected_repository["repository_id"] == repository["repository_id"]
            and expected_repository["base_ref"] == base_ref
            and expected_repository["base_tree"]
            == hashlib.sha256(base_tree.encode("utf-8")).hexdigest()
            and expected_repository["head_ref"] == repository["target_ref"]
            and expected_repository["head_tree"]
            == hashlib.sha256(current_tree.encode("utf-8")).hexdigest(),
            "review packet repository evidence differs from the qualified record",
        )
    blobs = [
        *packet["target"]["requirements"],
        packet["role"]["config"],
        *packet["role"]["policies"],
        *packet["artifacts"],
        *packet["lineage"]["repair_evidence"],
    ]
    for blob in blobs:
        if blob["kind"] == "REPO_BLOB":
            require(
                packet_git(worktree, "show", f"{blob_prefix}{blob['source']}")
                == blob["content"],
                f"review packet repository blob drifted: {blob['source']}",
            )
        elif blob["kind"] == "RUN_BLOB":
            path = worktree / blob["source"]
            resolved = path.resolve(strict=False)
            component = worktree
            has_symlink_component = False
            for part in PurePosixPath(blob["source"]).parts:
                component /= part
                has_symlink_component = (
                    has_symlink_component or component.is_symlink()
                )
            require(
                path.is_file()
                and resolved.is_relative_to(worktree)
                and not has_symlink_component
                and path.read_text(encoding="utf-8") == blob["content"],
                f"review packet run blob drifted: {blob['source']}",
            )


def validate_v3_reviews(
    record: dict[str, Any], bindings: dict[str, dict[str, Any]], target_ids: set[str]
) -> set[str]:
    require(isinstance(record["reviews"], list), "reviews must be a list")
    review_ids: set[str] = set()
    for review in record["reviews"]:
        review = require_exact_keys(
            review,
            {
                "id", "version", "target_id", "ref", "stage", "reviewer_role", "binding_id",
                "task", "lens", "context_mode", "originating_review_id", "originating_thread_id",
                "packet_id", "packet_digest", "packet_snapshot", "started_at", "ended_at", "result",
                "finding_ids", "evidence", "evidence_state",
            },
            "review",
        )
        require_string(review["id"], "review id")
        require(review["id"] not in review_ids, "duplicate review id")
        require(review["version"] == 1, "unsupported review version")
        require(review["target_id"] in target_ids, "review target is unknown")
        require_string(review["ref"], "review ref")
        parse_stage(review["stage"], "review stage")
        require_string(review["binding_id"], "review binding id")
        require(review["binding_id"] in bindings, "review names an unknown binding")
        binding = bindings[review["binding_id"]]
        require(
            binding["requested_role"] == review["reviewer_role"]
            and binding["target_id"] == review["target_id"]
            and binding["ref"] == review["ref"],
            "review binding differs from its role/target/ref",
        )
        require(binding["thread_id"] is not None, "review binding lacks a runtime thread")
        for field in ("task", "lens"):
            require_string(review[field], f"review {field}")
        require(review["context_mode"] in REVIEW_CONTEXT_MODES, "invalid review context mode")
        require_optional_string(review["originating_review_id"], "review originating review id")
        require_optional_string(review["originating_thread_id"], "review originating thread id")
        if review["context_mode"] == "FRESH_ISOLATED":
            require(
                review["originating_review_id"] is None
                and review["originating_thread_id"] is None,
                "fresh isolated review must not inherit lineage",
            )
            require(
                not any(
                    bindings[item["binding_id"]]["thread_id"]
                    == binding["thread_id"]
                    for item in record["reviews"][: len(review_ids)]
                ),
                "fresh review reuses a prior reviewer thread",
            )
        elif review["context_mode"] == "TARGETED_CONTINUATION":
            require(review["originating_review_id"] in review_ids, "targeted continuation lacks an earlier review")
            original = next(item for item in record["reviews"] if item["id"] == review["originating_review_id"])
            original_binding = bindings[original["binding_id"]]
            require(
                original["binding_id"] != review["binding_id"]
                and original["reviewer_role"] == review["reviewer_role"]
                and original["target_id"] == review["target_id"]
                and original_binding["thread_id"] == binding["thread_id"]
                and review["originating_thread_id"] == binding["thread_id"],
                "targeted continuation must name the reviewer's exact original thread",
            )
        else:
            require(review["originating_review_id"] in review_ids, "replacement review lacks an earlier review")
            original = next(item for item in record["reviews"] if item["id"] == review["originating_review_id"])
            original_binding = bindings[original["binding_id"]]
            require(
                original["reviewer_role"] == review["reviewer_role"]
                and original["target_id"] == review["target_id"]
                and review["binding_id"] != original["binding_id"]
                and binding["thread_id"] != original_binding["thread_id"]
                and review["originating_thread_id"] == original_binding["thread_id"]
                and original["evidence_state"] == "UNAVAILABLE",
                "replacement review lacks proven unavailable-thread lineage",
            )
        require_string(review["packet_id"], "review packet id")
        require_digest(review["packet_digest"], "review packet digest")
        require_string(review["packet_snapshot"], "review packet snapshot")
        packet = validate_review_packet_snapshot(
            review["packet_snapshot"], review["packet_digest"]
        )
        require(
            packet["packet_id"] == review["packet_id"]
            and packet["workflow"]["run_id"] == record["run_id"]
            and packet["workflow"]["workflow"] == record["workflow"]
            and packet["workflow"]["target_id"] == review["target_id"]
            and packet["workflow"]["stage"] == review["stage"]
            and packet["workflow"]["context_mode"] == review["context_mode"]
            and packet["target"]["kind"]
            == next(
                item["kind"]
                for item in record["targets"]
                if item["target_id"] == review["target_id"]
            )
            and packet["target"]["id"]
            == next(
                item["id"]
                for item in record["targets"]
                if item["target_id"] == review["target_id"]
            )
            and packet["target"]["revision"] == review["ref"]
            and packet["repository"]["repository_id"]
            == record["repository"]["repository_id"]
            and packet["repository"]["target_ref"] == review["ref"]
            and packet["role"]["requested_role"] == review["reviewer_role"]
            and packet["binding_request"]["binding_id"] == review["binding_id"]
            and packet["binding_request"]["originating_review_id"]
            == review["originating_review_id"]
            and packet["binding_request"]["originating_binding_id"]
            == (
                None
                if review["originating_review_id"] is None
                else next(
                    item["binding_id"]
                    for item in record["reviews"]
                    if item["id"] == review["originating_review_id"]
                )
            )
            and packet["binding_request"]["originating_thread_id"]
            == review["originating_thread_id"]
            and packet["task"]["text"] == review["task"]
            and packet["task"]["lens"] == review["lens"],
            "review fields differ from the canonical packet snapshot",
        )
        if packet["repository"]["mode"] != "NONE":
            require(
                packet["repository"]["base_ref"]
                == record["repository"]["base_ref"]
                and packet["repository"]["target_ref"]
                == record["repository"]["head_ref"],
                "review packet base/head differs from the frozen run repository",
            )
        require(
            binding["role"]["value"] is None
            or binding["role"]["value"]
            in packet["binding_request"]["allowed_actual_roles"],
            "review binding actual role is outside the canonical packet allowlist",
        )
        started = parse_timestamp(review["started_at"], "review started_at")
        ended = parse_timestamp(review["ended_at"], "review ended_at")
        require(ended > started, "review interval must have positive duration")
        require(review["result"] in REVIEW_RESULTS, "invalid review result")
        require_string_list(review["finding_ids"], "review finding ids")
        require_string_list(review["evidence"], "review evidence", nonempty=True)
        require(review["evidence_state"] in EVIDENCE_STATES, "invalid review evidence state")
        review_ids.add(review["id"])
    return review_ids


def validate_v3_findings(
    record: dict[str, Any], participants: set[str], target_ids: set[str]
) -> set[str]:
    require(isinstance(record["findings"], list), "findings must be a list")
    finding_ids: set[str] = set()
    by_id: dict[str, dict[str, Any]] = {}
    for finding in record["findings"]:
        finding = require_exact_keys(
            finding,
            {
                "id", "version", "target_id", "ref", "source_kind", "source", "severity", "summary",
                "decision", "status", "duplicate_of", "stage", "family", "miss_reason",
                "miss_reason_detail", "evidence",
            },
            "finding",
        )
        require_string(finding["id"], "finding id")
        require(finding["id"] not in finding_ids, "duplicate finding id")
        require(finding["version"] == 1, "unsupported finding version")
        require(finding["target_id"] in target_ids, "finding target is unknown")
        require_string(finding["ref"], "finding ref")
        require(finding["source_kind"] in {"ROLE", "EXTERNAL"}, "invalid finding source kind")
        require_string(finding["source"], "finding source")
        if finding["source_kind"] == "ROLE":
            require(finding["source"] in participants, "finding names a non-participating role")
        require(finding["severity"] in {"CRITICAL", "HIGH", "MEDIUM", "LOW"}, "invalid finding severity")
        require_string(finding["summary"], "finding summary")
        require(finding["decision"] in FINDING_DECISIONS, "invalid finding decision")
        require(finding["status"] in FINDING_STATUSES, "invalid finding status")
        require_optional_string(finding["duplicate_of"], "finding duplicate_of")
        parse_stage(finding["stage"], "finding stage")
        require_optional_string(finding["family"], "finding family")
        require(finding["miss_reason"] is None or finding["miss_reason"] in MISS_REASONS, "invalid finding miss reason")
        require_optional_string(finding["miss_reason_detail"], "finding miss reason detail")
        if finding["miss_reason"] is not None and finding["decision"] != "DUPLICATE":
            require(finding["family"] is not None, "canonical family expansion requires a stable family id")
        if finding["miss_reason"] == "OTHER":
            require(finding["miss_reason_detail"] is not None, "OTHER miss reason requires detail")
        require_string_list(finding["evidence"], "finding evidence")
        finding_ids.add(finding["id"])
        by_id[finding["id"]] = finding
    for finding in record["findings"]:
        duplicate_of = finding["duplicate_of"]
        if finding["decision"] == "DUPLICATE":
            require(duplicate_of is not None, "duplicate decision requires duplicate lineage")
        else:
            require(duplicate_of is None, "only a duplicate decision may name duplicate lineage")
        if duplicate_of is not None:
            require(duplicate_of != finding["id"] and duplicate_of in by_id, "invalid finding duplicate target")
            require(by_id[duplicate_of]["decision"] != "DUPLICATE", "finding duplicate target must be canonical")
        require(not (finding["decision"] == "PENDING" and finding["status"] == "RESOLVED"), "pending finding cannot be resolved")
    return finding_ids


def validate_v3_criteria(
    record: dict[str, Any], participants: set[str], target_ids: set[str]
) -> tuple[set[str], set[str]]:
    require(isinstance(record["criteria"], list), "criteria must be a list")
    criterion_ids: set[str] = set()
    evidence_ids: set[str] = set()
    normalized: set[tuple[str, str]] = set()
    for criterion in record["criteria"]:
        criterion = require_exact_keys(
            criterion,
            {"id", "version", "target_id", "text", "status", "na_justification", "skip_id", "evidence"},
            "criterion",
        )
        require_string(criterion["id"], "criterion id")
        require(criterion["id"] not in criterion_ids, "duplicate criterion id")
        require(criterion["version"] == 1, "unsupported criterion version")
        require(criterion["target_id"] in target_ids, "criterion target is unknown")
        require_string(criterion["text"], "criterion text")
        normalized_key = (criterion["target_id"], " ".join(criterion["text"].casefold().split()))
        require(normalized_key not in normalized, "duplicate or contradictory criterion text")
        normalized.add(normalized_key)
        require(criterion["status"] in CRITERION_STATUSES, "invalid criterion status")
        require_optional_string(criterion["na_justification"], "criterion N/A justification")
        require_optional_string(criterion["skip_id"], "criterion skip id")
        if criterion["status"] == "NOT_APPLICABLE":
            require(criterion["na_justification"] is not None and criterion["skip_id"] is not None, "N/A criterion requires justification and skip")
        else:
            require(criterion["na_justification"] is None and criterion["skip_id"] is None, "only N/A criterion may carry skip fields")
        require(isinstance(criterion["evidence"], list), "criterion evidence must be a list")
        if criterion["status"] == "PASS":
            require(bool(criterion["evidence"]), "PASS criterion requires evidence")
        for evidence in criterion["evidence"]:
            validate_v3_evidence(evidence, participants, target_ids, "criterion")
            require(evidence["id"] not in evidence_ids, "duplicate criterion evidence id")
            evidence_ids.add(evidence["id"])
        criterion_ids.add(criterion["id"])
    return criterion_ids, evidence_ids


def validate_v3_skip_manifest(record: dict[str, Any], target_ids: set[str]) -> set[str]:
    manifest = require_exact_keys(record["skip_manifest"], {"version", "entries"}, "skip manifest")
    require(manifest["version"] == 1, "unsupported skip manifest version")
    require(isinstance(manifest["entries"], list), "skip manifest entries must be a list")
    skip_ids: set[str] = set()
    for entry in manifest["entries"]:
        entry = require_exact_keys(
            entry,
            {
                "id", "version", "target_id", "ref", "obligation_kind", "role", "test_id", "platform",
                "reason", "owning_gate", "authority", "at", "evidence_state",
            },
            "skip manifest entry",
        )
        require_string(entry["id"], "skip id")
        require(entry["id"] not in skip_ids, "duplicate skip id")
        require(entry["version"] == 1, "unsupported skip version")
        require(entry["target_id"] in target_ids, "skip target is unknown")
        require_string(entry["ref"], "skip ref")
        require(entry["obligation_kind"] in OBLIGATION_KINDS, "invalid skip obligation kind")
        require_optional_string(entry["role"], "skip role")
        for field in ("test_id", "platform", "reason", "owning_gate", "authority"):
            require_string(entry[field], f"skip {field}")
        parse_timestamp(entry["at"], "skip at")
        require(entry["evidence_state"] in {"NOT_APPLICABLE", "UNAVAILABLE"}, "invalid skip evidence state")
        skip_ids.add(entry["id"])
    return skip_ids


def validate_environment(value: Any, field: str) -> str:
    environment = require_exact_keys(
        value,
        {"kind", "platform", "runtime", "container_image", "lock_digest", "digest"},
        field,
    )
    require(environment["kind"] in {"LOCAL", "CI", "CONTAINER", "REMOTE"}, f"invalid {field} kind")
    require_string(environment["platform"], f"{field} platform")
    require_string(environment["runtime"], f"{field} runtime")
    require_optional_string(environment["container_image"], f"{field} container image")
    require_digest(environment["lock_digest"], f"{field} lock digest")
    require_digest(environment["digest"], f"{field} digest")
    payload = {key: environment[key] for key in environment if key != "digest"}
    require(canonical_fingerprint(payload) == environment["digest"], f"{field} digest mismatch")
    return environment["digest"]


def validate_v3_checks(
    record: dict[str, Any], target_ids: set[str], artifact_ids: set[str], skip_ids: set[str]
) -> set[str]:
    require(isinstance(record["checks"], list), "checks must be a list")
    check_ids: set[str] = set()
    for check in record["checks"]:
        check = require_exact_keys(
            check,
            {
                "id", "version", "target_id", "ref", "kind", "command", "started_at", "ended_at",
                "result", "artifact_ids", "environment", "invalidation_rule", "validity", "skip_id", "summary",
            },
            "check",
        )
        require_string(check["id"], "check id")
        require(check["id"] not in check_ids, "duplicate check id")
        require(check["version"] == 1, "unsupported check version")
        require(check["target_id"] in target_ids, "check target is unknown")
        require_string(check["ref"], "check ref")
        require(check["kind"] in CHECK_KINDS, "invalid check kind")
        require_string(check["command"], "check command")
        started = parse_timestamp(check["started_at"], "check started_at")
        ended = parse_timestamp(check["ended_at"], "check ended_at")
        require(ended > started, "check interval must have positive duration")
        require(check["result"] in CHECK_RESULTS, "invalid check result")
        require_string_list(check["artifact_ids"], "check artifact ids", nonempty=True)
        require(set(check["artifact_ids"]) <= artifact_ids, "check names an unknown artifact")
        environment_digest = validate_environment(check["environment"], "check environment")
        artifact_environment = {
            artifact["environment_digest"]
            for artifact in record["artifacts"]
            if artifact["id"] in check["artifact_ids"]
        }
        require(artifact_environment == {environment_digest}, "check artifacts and environment identity differ")
        require_string(check["invalidation_rule"], "check invalidation rule")
        require(check["validity"] in EVIDENCE_STATES, "invalid check validity")
        require_optional_string(check["skip_id"], "check skip id")
        if check["validity"] == "NOT_APPLICABLE":
            require(check["skip_id"] in skip_ids, "N/A check requires a recorded skip")
        else:
            require(check["skip_id"] is None, "only N/A check may carry a skip")
        require_string(check["summary"], "check summary")
        check_ids.add(check["id"])
    return check_ids


def validate_v3_external_evidence(record: dict[str, Any], target_ids: set[str]) -> set[str]:
    require(isinstance(record["external_evidence"], list), "external evidence must be a list")
    evidence_ids: set[str] = set()
    for evidence in record["external_evidence"]:
        evidence = require_exact_keys(
            evidence,
            {
                "id", "version", "target_id", "source_pr", "source_head", "observed_at",
                "invalidation_rule", "evidence_state", "summary",
            },
            "external evidence",
        )
        require_string(evidence["id"], "external evidence id")
        require(evidence["id"] not in evidence_ids, "duplicate external evidence id")
        require(evidence["version"] == 1, "unsupported external evidence version")
        require(evidence["target_id"] in target_ids, "external evidence target is unknown")
        require_string(evidence["source_pr"], "external evidence source PR")
        require_string(evidence["source_head"], "external evidence source head")
        parse_timestamp(evidence["observed_at"], "external evidence observed_at")
        require_string(evidence["invalidation_rule"], "external evidence invalidation rule")
        require(evidence["evidence_state"] in EVIDENCE_STATES, "invalid external evidence state")
        require_string(evidence["summary"], "external evidence summary")
        evidence_ids.add(evidence["id"])
    return evidence_ids


def validate_v3_obligations(
    record: dict[str, Any], target_ids: set[str], skip_ids: set[str]
) -> set[str]:
    require(isinstance(record["obligations"], list), "obligations must be a list")
    obligation_ids: set[str] = set()
    unique_scope: set[tuple[Any, ...]] = set()
    for obligation in record["obligations"]:
        obligation = require_exact_keys(
            obligation,
            {
                "id", "version", "kind", "target_id", "ref", "stage", "role", "status",
                "evidence_ids", "justification", "skip_id",
            },
            "obligation",
        )
        require_string(obligation["id"], "obligation id")
        require(obligation["id"] not in obligation_ids, "duplicate obligation id")
        require(obligation["version"] == 1, "unsupported obligation version")
        require(obligation["kind"] in OBLIGATION_KINDS, "invalid obligation kind")
        require(obligation["target_id"] in target_ids, "obligation target is unknown")
        require_string(obligation["ref"], "obligation ref")
        require_optional_string(obligation["stage"], "obligation stage")
        if obligation["stage"] is not None:
            parse_stage(obligation["stage"], "obligation stage")
        require_optional_string(obligation["role"], "obligation role")
        require(obligation["status"] in OBLIGATION_STATUSES, "invalid obligation status")
        require_string_list(obligation["evidence_ids"], "obligation evidence ids")
        require_optional_string(obligation["justification"], "obligation justification")
        require_optional_string(obligation["skip_id"], "obligation skip id")
        if obligation["status"] in {"NOT_APPLICABLE", "UNAVAILABLE"}:
            require(obligation["justification"] is not None, "skipped/unavailable obligation requires justification")
            require(obligation["skip_id"] in skip_ids, "skipped/unavailable obligation requires a matching skip")
            require(obligation["skip_id"] in obligation["evidence_ids"], "obligation must cite its skip evidence")
        else:
            require(obligation["skip_id"] is None, "open/satisfied obligation must not carry a skip")
        if obligation["status"] == "SATISFIED":
            require(bool(obligation["evidence_ids"]), "satisfied obligation requires evidence")
        scope = (
            obligation["kind"], obligation["target_id"], obligation["ref"],
            obligation["stage"], obligation["role"],
        )
        require(scope not in unique_scope, "duplicate obligation scope")
        unique_scope.add(scope)
        obligation_ids.add(obligation["id"])
    return obligation_ids


def validate_v3_satisfied_obligation_evidence(
    record: dict[str, Any], bindings: dict[str, dict[str, Any]]
) -> None:
    """Reject satisfied claims backed by evidence of another kind or scope."""

    evidence_by_kind = {
        "CRITERIA": {item["id"]: item for item in record["criteria"]},
        "REVIEW": {item["id"]: item for item in record["reviews"]},
        "CHECK": {item["id"]: item for item in record["checks"]},
        "QUALIFICATION": {item["id"]: item for item in record["checks"]},
        "EXTERNAL_GATE": {
            item["id"]: item for item in record["external_gates"]
        },
        "MUTATION": {item["id"]: item for item in record["mutations"]},
        "CONNECTOR": {
            item["id"]: item for item in record["connector_rounds"]
        },
        "DELIVERY": {item["id"]: item for item in record["deliveries"]},
    }
    for obligation in record["obligations"]:
        if obligation["status"] != "SATISFIED":
            continue
        kind = obligation["kind"]
        if kind == "BINDING":
            require(
                bool(obligation["evidence_ids"])
                and set(obligation["evidence_ids"]).issubset(bindings),
                "satisfied BINDING obligation lacks its direct binding proof",
            )
            for binding_id in obligation["evidence_ids"]:
                binding = bindings[binding_id]
                require(
                    binding["requested_role"] == obligation["role"]
                    and binding["target_id"] in {None, obligation["target_id"]}
                    and binding["ref"] == obligation["ref"],
                    "BINDING obligation cites an off-scope invocation binding",
                )
            continue
        if kind in {
            "UPGRADE_RECONCILIATION",
            "FAMILY_BOUNDARY_ANALYSIS",
            "PUBLICATION_CHALLENGE",
            "MAINTAINER_DECISION",
        }:
            continue
        allowed = evidence_by_kind[kind]
        require(
            set(obligation["evidence_ids"]).issubset(allowed),
            f"{kind} obligation cites cross-kind evidence",
        )
        for evidence_id in obligation["evidence_ids"]:
            evidence = allowed[evidence_id]
            require(
                evidence["target_id"] == obligation["target_id"],
                f"{kind} obligation evidence is off-target",
            )
            if kind == "CRITERIA":
                require(
                    evidence["status"] == "PASS"
                    and any(
                        item["target_id"] == obligation["target_id"]
                        and item["ref"] == obligation["ref"]
                        and item["evidence_state"] in {"FRESH", "INHERITED"}
                        for item in evidence["evidence"]
                    ),
                    "CRITERIA obligation lacks current exact-ref evidence",
                )
            elif kind in {"REVIEW", "PUBLICATION_CHALLENGE"}:
                require(
                    evidence["ref"] == obligation["ref"]
                    and evidence["result"] != "BLOCKED"
                    and evidence["evidence_state"] in {"FRESH", "INHERITED"}
                    and (
                        obligation["role"] is None
                        or evidence["reviewer_role"] == obligation["role"]
                    )
                    and (
                        obligation["stage"] is None
                        or evidence["stage"] == obligation["stage"]
                    ),
                    f"{kind} obligation evidence is stale or out of scope",
                )
            elif kind in {"CHECK", "QUALIFICATION"}:
                require(
                    evidence["ref"] == obligation["ref"]
                    and evidence["result"] == "PASS"
                    and evidence["validity"] in {"FRESH", "INHERITED"},
                    f"{kind} obligation lacks a passing exact-ref check",
                )
            elif kind == "EXTERNAL_GATE":
                require(
                    evidence["ref"] == obligation["ref"]
                    and evidence["status"] == "SATISFIED",
                    "EXTERNAL_GATE obligation evidence is incomplete or off-ref",
                )
            elif kind == "MUTATION":
                require(
                    evidence["ref_after"] == obligation["ref"]
                    and evidence["result"] in {"APPLIED", "NO_CHANGE"}
                    and evidence["evidence_state"] in {"FRESH", "INHERITED"},
                    "MUTATION obligation evidence is stale or off-ref",
                )
            elif kind == "CONNECTOR":
                require(
                    evidence["ref"] == obligation["ref"]
                    and evidence["completed_at"] is not None
                    and evidence["state"]
                    not in {
                        "PENDING_PICKUP",
                        "IN_PROGRESS",
                        "PROTOCOL_VARIANCE",
                        "INCONSISTENT",
                    },
                    "CONNECTOR obligation evidence is incomplete or off-ref",
                )
            elif kind == "DELIVERY":
                require(
                    evidence["ref"] == obligation["ref"]
                    and evidence["status"] in SEMANTIC_TERMINAL_STATUSES,
                    "DELIVERY obligation evidence is incomplete or off-ref",
                )


def validate_v3_handoffs(record: dict[str, Any], target_ids: set[str], participants: set[str]) -> set[str]:
    require(isinstance(record["handoffs"], list), "handoffs must be a list")
    handoff_ids: set[str] = set()
    previous: datetime | None = None
    for handoff in record["handoffs"]:
        handoff = require_exact_keys(
            handoff,
            {
                "id", "version", "target_id", "ref", "from", "to", "at", "reason", "packet_digest",
                "scope", "constraints", "expected_evidence",
            },
            "handoff",
        )
        require_string(handoff["id"], "handoff id")
        require(handoff["id"] not in handoff_ids, "duplicate handoff id")
        require(handoff["version"] == 1, "unsupported handoff version")
        require(handoff["target_id"] in target_ids, "handoff target is unknown")
        require_string(handoff["ref"], "handoff ref")
        require(handoff["from"] in participants and handoff["to"] in participants, "handoff actor did not participate")
        require(handoff["from"] != handoff["to"], "handoff actors must differ")
        at = parse_timestamp(handoff["at"], "handoff at")
        if previous is not None:
            require(at >= previous, "handoff chronology moves backwards")
        previous = at
        require_string(handoff["reason"], "handoff reason")
        require_digest(handoff["packet_digest"], "handoff packet digest")
        require_string_list(handoff["scope"], "handoff scope", nonempty=True)
        require_string_list(handoff["constraints"], "handoff constraints")
        require_string_list(handoff["expected_evidence"], "handoff expected evidence", nonempty=True)
        handoff_ids.add(handoff["id"])
    return handoff_ids


def validate_project_mutation(project: Any) -> None:
    project = require_exact_keys(
        project,
        {
            "issue_number", "item_id", "precondition_item_id", "mutation_id", "intended_before",
            "intended_after", "observed_readback", "unintended_change_check",
        },
        "Project mutation",
    )
    require(isinstance(project["issue_number"], int) and not isinstance(project["issue_number"], bool) and project["issue_number"] > 0, "invalid Project issue number")
    for field in ("item_id", "precondition_item_id", "mutation_id"):
        require_string(project[field], f"Project {field}")
    require(project["precondition_item_id"] == project["item_id"], "Project issue-to-item precondition failed")
    require(isinstance(project["intended_before"], dict), "Project intended_before must be an object")
    require(isinstance(project["intended_after"], dict), "Project intended_after must be an object")
    require(isinstance(project["observed_readback"], dict), "Project observed_readback must be an object")
    require(project["intended_before"] != project["intended_after"], "Project mutation records no intended change")
    require(project["observed_readback"] == project["intended_after"], "Project mutation readback differs from intent")
    require(project["unintended_change_check"] == "PASS", "Project unintended-change check did not pass")


def validate_v3_mutations(record: dict[str, Any], target_ids: set[str], participants: set[str]) -> set[str]:
    require(isinstance(record["mutations"], list), "mutations must be a list")
    mutation_ids: set[str] = set()
    for mutation in record["mutations"]:
        mutation = require_exact_keys(
            mutation,
            {
                "id", "version", "target_id", "kind", "at", "actor", "ref_before", "ref_after",
                "external_id", "summary", "result", "evidence_state", "project",
            },
            "mutation",
        )
        require_string(mutation["id"], "mutation id")
        require(mutation["id"] not in mutation_ids, "duplicate mutation id")
        require(mutation["version"] == 1, "unsupported mutation version")
        require(mutation["target_id"] in target_ids, "mutation target is unknown")
        require(mutation["kind"] in MUTATION_KINDS, "invalid mutation kind")
        parse_timestamp(mutation["at"], "mutation at")
        require_string(mutation["actor"], "mutation actor")
        if mutation["kind"] == "REPOSITORY":
            require(mutation["actor"] in participants, "repository mutation actor did not participate")
        require_optional_string(mutation["ref_before"], "mutation ref_before")
        require_optional_string(mutation["ref_after"], "mutation ref_after")
        require_optional_string(mutation["external_id"], "mutation external id")
        require_string(mutation["summary"], "mutation summary")
        require(mutation["result"] in MUTATION_RESULTS, "invalid mutation result")
        require(mutation["evidence_state"] in EVIDENCE_STATES, "invalid mutation evidence state")
        if mutation["kind"] == "PROJECT":
            validate_project_mutation(mutation["project"])
        else:
            require(mutation["project"] is None, "non-Project mutation must not carry Project fields")
        mutation_ids.add(mutation["id"])
    return mutation_ids


def validate_v3_external_gates(
    record: dict[str, Any], target_ids: set[str], skip_ids: set[str],
    external_evidence: dict[str, dict[str, Any]],
) -> set[str]:
    require(isinstance(record["external_gates"], list), "external gates must be a list")
    gate_ids: set[str] = set()
    for gate in record["external_gates"]:
        gate = require_exact_keys(
            gate,
            {"id", "version", "target_id", "ref", "kind", "required", "status", "checked_at", "evidence_ids", "skip_id"},
            "external gate",
        )
        require_string(gate["id"], "external gate id")
        require(gate["id"] not in gate_ids, "duplicate external gate id")
        require(gate["version"] == 1, "unsupported external gate version")
        require(gate["target_id"] in target_ids, "external gate target is unknown")
        require_string(gate["ref"], "external gate ref")
        require(gate["kind"] in {"TRACKING_READY", "REMOTE_CI", "BRANCH_PROTECTION", "CONNECTOR_C1", "CONNECTOR_C2", "USER_DECISION", "OTHER"}, "invalid external gate kind")
        require(isinstance(gate["required"], bool), "external gate required flag must be boolean")
        require(gate["status"] in EXTERNAL_GATE_STATUSES, "invalid external gate status")
        checked_at = parse_timestamp(gate["checked_at"], "external gate checked_at")
        require_string_list(gate["evidence_ids"], "external gate evidence ids")
        require(
            set(gate["evidence_ids"]) <= set(external_evidence) | skip_ids,
            "external gate names unknown evidence",
        )
        require_optional_string(gate["skip_id"], "external gate skip id")
        if gate["status"] == "NOT_APPLICABLE":
            require(gate["skip_id"] in skip_ids and gate["skip_id"] in gate["evidence_ids"], "N/A external gate requires its skip evidence")
        else:
            require(gate["skip_id"] is None, "only N/A external gate may carry a skip")
        if gate["status"] == "SATISFIED":
            require(bool(gate["evidence_ids"]), "satisfied external gate requires evidence")
            require(
                set(gate["evidence_ids"]) <= set(external_evidence),
                "satisfied external gate requires external evidence",
            )
            for evidence_id in gate["evidence_ids"]:
                evidence = external_evidence[evidence_id]
                require(
                    evidence["target_id"] == gate["target_id"],
                    "external gate evidence target differs from its gate",
                )
                require(
                    evidence["source_head"] == gate["ref"],
                    "external gate evidence is off-ref",
                )
                require(
                    evidence["evidence_state"] in {"FRESH", "INHERITED"},
                    "external gate evidence is stale or unavailable",
                )
                require(
                    parse_timestamp(
                        evidence["observed_at"], "external evidence observed_at"
                    )
                    <= checked_at,
                    "external gate evidence was observed after the gate check",
                )
        gate_ids.add(gate["id"])
    return gate_ids


def validate_v3_connectors(record: dict[str, Any], target_ids: set[str], finding_ids: set[str]) -> set[str]:
    require(isinstance(record["connector_rounds"], list), "connector rounds must be a list")
    round_ids: set[str] = set()
    per_target: dict[str, list[int]] = {}
    for item in record["connector_rounds"]:
        item = require_exact_keys(
            item,
            {
                "id",
                "version",
                "target_id",
                "round",
                "repository",
                "pull_request",
                "ref",
                "trigger_kind",
                "trigger_id",
                "anchor_kind",
                "anchor_id",
                "state",
                "started_at",
                "completed_at",
                "finding_ids",
                "observation_ids",
            },
            "connector round",
        )
        require_string(item["id"], "connector round id")
        require(item["id"] not in round_ids, "duplicate connector round id")
        require(item["version"] == 1 and item["target_id"] in target_ids, "invalid connector round version/target")
        family, ordinal = parse_stage(item["round"], "connector round")
        require(family == "C", "connector round must be C1 or C2")
        require_string(item["repository"], "connector repository")
        require_string(item["pull_request"], "connector pull request")
        require_string(item["ref"], "connector ref")
        require(item["trigger_kind"] in {"AUTOMATIC", "MANUAL"}, "invalid connector trigger kind")
        require_optional_string(item["trigger_id"], "connector trigger id")
        require_string(item["anchor_id"], "connector anchor id")
        require(
            (
                ordinal == 1
                and item["trigger_kind"] == "AUTOMATIC"
                and item["trigger_id"] is None
                and item["anchor_kind"] == "PULL_REQUEST_DESCRIPTION"
                and item["anchor_id"] == item["pull_request"]
            )
            or (
                ordinal == 2
                and item["trigger_kind"] == "MANUAL"
                and item["trigger_id"] is not None
                and item["anchor_kind"] == "REQUEST_COMMENT"
                and item["anchor_id"] == item["trigger_id"]
            ),
            "connector trigger/anchor does not match round",
        )
        require(item["state"] in CONNECTOR_STATES, "invalid connector state")
        started = parse_timestamp(item["started_at"], "connector started_at")
        require_optional_string(item["completed_at"], "connector completed_at")
        if item["completed_at"] is not None:
            require(parse_timestamp(item["completed_at"], "connector completed_at") >= started, "connector completion predates start")
        require(
            (item["state"] in {"CLEAN", "FINDINGS_POSTED"})
            == (item["completed_at"] is not None),
            "only a conforming clean/findings connector result is completed",
        )
        require_string_list(item["finding_ids"], "connector finding ids")
        require(set(item["finding_ids"]) <= finding_ids, "connector names unknown findings")
        require_string_list(item["observation_ids"], "connector observation ids", nonempty=True)
        per_target.setdefault(item["target_id"], []).append(ordinal)
        round_ids.add(item["id"])
    for ordinals in per_target.values():
        require(ordinals == sorted(set(ordinals)), "connector rounds must be unique and ordered C1 then C2")
        require(ordinals[0] == 1, "C2 cannot precede C1")
    by_target_round = {(item["target_id"], item["round"]): item for item in record["connector_rounds"]}
    findings = {item["id"]: item for item in record["findings"]}
    for item in record["connector_rounds"]:
        if item["round"] != "C2":
            continue
        c1 = by_target_round[(item["target_id"], "C1")]
        require(c1["state"] == "FINDINGS_POSTED" and c1["finding_ids"], "C2 requires actionable C1 findings")
        require(any(findings[finding_id]["decision"] == "FIX_NOW" for finding_id in c1["finding_ids"]), "C2 requires an accepted actionable C1 finding")
    return round_ids


def validate_v3_deliveries(record: dict[str, Any], target_ids: set[str]) -> set[str]:
    require(isinstance(record["deliveries"], list), "deliveries must be a list")
    delivery_ids: set[str] = set()
    delivered_targets: set[str] = set()
    for item in record["deliveries"]:
        item = require_exact_keys(
            item,
            {"id", "version", "target_id", "ref", "mode", "status", "branch", "commit", "pull_request", "at", "evidence"},
            "delivery",
        )
        require_string(item["id"], "delivery id")
        require(item["id"] not in delivery_ids and item["target_id"] not in delivered_targets, "duplicate delivery or delivered target")
        require(item["version"] == 1 and item["target_id"] in target_ids, "invalid delivery version/target")
        require_string(item["ref"], "delivery ref")
        require(item["mode"] in DELIVERY_MODES and item["status"] in DELIVERY_STATUSES, "invalid delivery mode/status")
        for field in ("branch", "commit", "pull_request"):
            require_optional_string(item[field], f"delivery {field}")
        parse_timestamp(item["at"], "delivery at")
        require_string_list(item["evidence"], "delivery evidence", nonempty=True)
        delivery_ids.add(item["id"])
        delivered_targets.add(item["target_id"])
    return delivery_ids


def validate_connector_trigger_mutations(record: dict[str, Any]) -> None:
    applied = [
        item
        for item in record["mutations"]
        if item["kind"] == "CONNECTOR_TRIGGER" and item["result"] == "APPLIED"
    ]
    c2_rounds = [item for item in record["connector_rounds"] if item["round"] == "C2"]
    require(
        len(applied) == len(c2_rounds),
        "successful connector triggers and C2 rounds are not bijective",
    )
    for mutation in applied:
        require(mutation["actor"] == "root", "connector trigger was not performed by root")
        matches = [
            connector
            for connector in c2_rounds
            if connector["target_id"] == mutation["target_id"]
            and connector["trigger_id"] == mutation["external_id"]
            and connector["ref"] == mutation["ref_after"]
        ]
        require(
            len(matches) == 1,
            "connector trigger mutation lacks one exact C2 receipt/round",
        )
    per_target = [item["target_id"] for item in applied]
    require(
        len(per_target) == len(set(per_target)),
        "duplicate manual connector trigger consumed for one target",
    )


def validate_v3_violations(record: dict[str, Any], target_ids: set[str]) -> set[str]:
    require(isinstance(record["violations"], list), "violations must be a list")
    ids: set[str] = set()
    for item in record["violations"]:
        item = require_exact_keys(item, {"id", "version", "target_id", "ref", "kind", "at", "summary", "status", "resolution", "evidence_ids"}, "violation")
        require_string(item["id"], "violation id")
        require(item["id"] not in ids and item["version"] == 1, "duplicate/unsupported violation")
        require(item["target_id"] in target_ids, "violation target is unknown")
        require_string(item["ref"], "violation ref")
        require(item["kind"] in VIOLATION_KINDS, "invalid violation kind")
        parse_timestamp(item["at"], "violation at")
        require_string(item["summary"], "violation summary")
        require(item["status"] in VIOLATION_STATUSES, "invalid violation status")
        require_optional_string(item["resolution"], "violation resolution")
        if item["status"] == "RESOLVED":
            require(item["resolution"] is not None, "resolved violation requires resolution")
        require_string_list(item["evidence_ids"], "violation evidence ids")
        ids.add(item["id"])
    return ids


def validate_connector_observation_snapshot(
    snapshot: str, digest: str
) -> dict[str, Any]:
    """Validate one canonical snapshot emitted by connector_state.py."""
    try:
        report = json.loads(snapshot, object_pairs_hook=duplicate_safe_object)
    except json.JSONDecodeError as error:
        raise ValidationError(f"invalid connector observation JSON: {error}") from error
    require(isinstance(report, dict), "connector observation snapshot must be an object")
    canonical = canonical_json_bytes(report)
    require(
        snapshot.encode("utf-8") == canonical,
        "connector observation snapshot is not canonical",
    )
    require(
        hashlib.sha256(canonical).hexdigest() == digest,
        "connector observation snapshot digest mismatch",
    )
    report = require_exact_keys(
        report,
        {
            "schema_version",
            "observed_at",
            "repo",
            "pr",
            "round",
            "state",
            "protocol_variances",
            "request",
            "heads",
            "latency_seconds",
            "ids",
            "events",
        },
        "connector observation snapshot",
    )
    require(report["schema_version"] == 1, "unsupported connector observation version")
    observed_at = parse_timestamp(report["observed_at"], "connector observed_at")
    require_string(report["repo"], "connector repository")
    require(
        isinstance(report["pr"], int)
        and not isinstance(report["pr"], bool)
        and report["pr"] > 0,
        "invalid connector pull request",
    )
    require(report["round"] in {"C1", "C2"}, "invalid connector snapshot round")
    require(report["state"] in CONNECTOR_STATES, "invalid connector snapshot state")
    require_string_list(report["protocol_variances"], "connector protocol variances")
    request = require_exact_keys(
        report["request"],
        {"kind", "requested_at", "target_kind", "target_id", "trigger_normalized"},
        "connector request",
    )
    requested_at = parse_timestamp(request["requested_at"], "connector requested_at")
    require(observed_at >= requested_at, "connector observation predates its request")
    require_optional_string(request["trigger_normalized"], "connector normalized trigger")
    if report["round"] == "C1":
        require(
            request["kind"] == "AUTOMATIC_PR_OPEN"
            and request["target_kind"] == "PR_DESCRIPTION"
            and request["target_id"] == report["pr"]
            and request["trigger_normalized"] is None,
            "invalid automatic connector request",
        )
    else:
        require(
            request["kind"] == "EXPLICIT_TRIGGER_COMMENT"
            and request["target_kind"] == "TRIGGER_COMMENT"
            and isinstance(request["target_id"], int)
            and not isinstance(request["target_id"], bool)
            and request["target_id"] > 0
            and request["trigger_normalized"] is not None,
            "invalid requested connector review",
        )
    heads = require_exact_keys(
        report["heads"],
        {"opening", "requested", "current", "current_matches_requested"},
        "connector heads",
    )
    for field in ("opening", "requested", "current"):
        require(
            isinstance(heads[field], str) and GIT_SHA.fullmatch(heads[field]),
            f"invalid connector {field} head",
        )
    require(
        isinstance(heads["current_matches_requested"], bool)
        and heads["current_matches_requested"]
        == (heads["current"] == heads["requested"]),
        "connector current-head comparison is false",
    )
    latency = require_exact_keys(
        report["latency_seconds"],
        {"observed", "pickup", "terminal", "off_target_terminal"},
        "connector latency",
    )
    for field, value in latency.items():
        require(
            value is None
            or (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and value >= 0
            ),
            f"invalid connector {field} latency",
        )
    expected_observed = round((observed_at - requested_at).total_seconds(), 3)
    require(latency["observed"] == expected_observed, "connector observed latency mismatch")
    identifiers = require_exact_keys(
        report["ids"],
        {"reaction_ids", "review_ids", "result_comment_ids"},
        "connector event ids",
    )
    for field in identifiers:
        require(
            isinstance(identifiers[field], list)
            and all(
                isinstance(item, int) and not isinstance(item, bool) and item > 0
                for item in identifiers[field]
            ),
            f"invalid connector {field}",
        )
    require(isinstance(report["events"], list), "connector events must be an array")
    events: list[dict[str, Any]] = []
    for value in report["events"]:
        require(isinstance(value, dict), "connector event must be an object")
        event_type = value.get("type")
        if event_type == "REACTION":
            event = require_exact_keys(
                value,
                {
                    "type", "id", "actor", "occurred_at", "content", "target_kind",
                    "target_id", "on_target", "commit_id", "commit_binding",
                },
                "connector reaction",
            )
            require_string(event["content"], "connector reaction content")
            require(isinstance(event["on_target"], bool), "invalid connector reaction target flag")
            require_string(event["target_kind"], "connector reaction target kind")
            require(
                isinstance(event["target_id"], int)
                and not isinstance(event["target_id"], bool)
                and event["target_id"] > 0,
                "invalid connector reaction target id",
            )
            require(event["commit_id"] is None, "connector reaction unexpectedly names a commit")
            require(
                event["commit_binding"] in {"TARGET_BOUND", "TARGET_MISMATCH"},
                "invalid connector reaction binding",
            )
        else:
            require(
                event_type in {"REVIEW", "RESULT_COMMENT", "REVIEW_COMMENT"},
                "invalid connector event type",
            )
            event = require_exact_keys(
                value,
                {
                    "type", "id", "actor", "occurred_at", "commit_id", "commit_binding",
                    "substantive", "body_sha256", "review_id", "review_state",
                },
                "connector result",
            )
            require(
                event["commit_binding"] in {"MATCH", "MISMATCH", "AMBIGUOUS", "UNAVAILABLE"},
                "invalid connector result binding",
            )
            require(isinstance(event["substantive"], bool), "invalid connector result substance")
            require_optional_digest(event["body_sha256"], "connector result body digest")
            require_optional_string(event["commit_id"], "connector result commit id")
            if event["commit_id"] is not None:
                require(GIT_SHA.fullmatch(event["commit_id"]) is not None, "invalid connector result commit id")
            require(
                event["review_id"] is None
                or (
                    isinstance(event["review_id"], int)
                    and not isinstance(event["review_id"], bool)
                    and event["review_id"] > 0
                ),
                "invalid connector parent review id",
            )
            require_optional_string(event["review_state"], "connector review state")
        require(
            isinstance(event["id"], int)
            and not isinstance(event["id"], bool)
            and event["id"] > 0,
            "invalid connector event id",
        )
        require(event["actor"] in CONNECTOR_ACTORS, "connector event actor is not the connector")
        require_optional_string(event["occurred_at"], "connector event occurred_at")
        if event["occurred_at"] is not None:
            require(
                parse_timestamp(event["occurred_at"], "connector event occurred_at")
                >= requested_at,
                "connector snapshot contains an event predating the request",
            )
        if event["type"] == "REACTION":
            require(
                (
                    event["on_target"] is True
                    and event["target_kind"] == request["target_kind"]
                    and event["target_id"] == request["target_id"]
                    and event["commit_binding"] == "TARGET_BOUND"
                )
                or (
                    event["on_target"] is False
                    and event["commit_binding"] == "TARGET_MISMATCH"
                ),
                "connector reaction target projection is inconsistent",
            )
        events.append(event)
    require(
        events
        == sorted(
            events,
            key=lambda item: (
                item.get("occurred_at") or "",
                str(item.get("id") or ""),
                item["type"],
            ),
        ),
        "connector events are not canonically ordered",
    )
    require(
        identifiers["reaction_ids"]
        == [item["id"] for item in events if item["type"] == "REACTION"]
        and identifiers["review_ids"]
        == [item["id"] for item in events if item["type"] == "REVIEW"]
        and identifiers["result_comment_ids"]
        == [
            item["id"]
            for item in events
            if item["type"] in {"RESULT_COMMENT", "REVIEW_COMMENT"}
        ],
        "connector event id indexes differ from the snapshot events",
    )

    def first_event_time(predicate: Any) -> datetime | None:
        times = [
            parse_timestamp(item["occurred_at"], "connector event occurred_at")
            for item in events
            if item["occurred_at"] is not None and predicate(item)
        ]
        return min(times) if times else None

    pickup_at = first_event_time(
        lambda item: item["type"] != "REACTION"
        or item.get("content") in {"eyes", "+1"}
    )
    terminal_at = first_event_time(
        lambda item: (
            item["type"] == "REACTION"
            and item.get("on_target")
            and item.get("content") == "+1"
        )
        or (
            item["type"] in {"REVIEW", "RESULT_COMMENT", "REVIEW_COMMENT"}
            and item.get("substantive")
            and item.get("commit_binding") in {"MATCH", "UNAVAILABLE"}
        )
    )
    off_target_at = first_event_time(
        lambda item: (
            item["type"] == "REACTION"
            and not item.get("on_target")
            and item.get("content") == "+1"
        )
        or item.get("commit_binding") in {"MISMATCH", "AMBIGUOUS"}
    )

    def expected_latency(value: datetime | None) -> float | None:
        return (
            round((value - requested_at).total_seconds(), 3)
            if value is not None
            else None
        )

    require(
        latency["pickup"] == expected_latency(pickup_at)
        and latency["terminal"] == expected_latency(terminal_at)
        and latency["off_target_terminal"] == expected_latency(off_target_at),
        "connector pickup/terminal latency differs from raw event times",
    )
    derived_state, derived_variances = connector_protocol.derive_state(
        events, latency["observed"]
    )
    require(
        report["state"] == derived_state
        and report["protocol_variances"] == derived_variances,
        "connector state/variance differs from its raw observations",
    )
    return report


def validate_v3_events(record: dict[str, Any], target_ids: set[str], participants: set[str]) -> dict[str, Any]:
    require(isinstance(record["events"], list) and record["events"], "events must be nonempty")
    previous_time: datetime | None = None
    phases: list[str] = []
    active_writer: str | None = None
    pending_root: dict[str, Any] | None = None
    active_stages: dict[str, tuple[str, str, datetime]] = {}
    completed_stages: list[dict[str, Any]] = []
    stage_ordinals: dict[tuple[str, str], int] = {}
    observations: dict[str, dict[str, Any]] = {}
    observation_reports: dict[str, dict[str, Any]] = {}
    ended = False
    writer_intervals: list[tuple[str, str, datetime, datetime, str]] = []
    active_start: tuple[str, str, datetime] | None = None
    previous_event: dict[str, Any] | None = None
    delivered_at: datetime | None = None
    permitted_writers = {
        item["role"] for item in record["team_context"]["workflow_roles"] if item["kind"] == "writer"
    } | {"root"}
    for sequence, event in enumerate(record["events"], 1):
        event = require_exact_keys(
            event,
            {
                "sequence", "version", "type", "at", "target_id", "ref", "actor", "stage",
                "from_state", "to_state", "reason", "related_id", "digest", "observation_id",
                "external_id", "latency_ms", "anchor_kind", "anchor_id", "on_target",
                "observation_snapshot",
            },
            "event",
        )
        require(event["sequence"] == sequence and event["version"] == 1, "event sequence/version is invalid")
        require(event["type"] in V3_EVENT_TYPES, "invalid event type")
        at = parse_timestamp(event["at"], "event at")
        if previous_time is not None:
            require(at >= previous_time, "event chronology moves backwards")
        previous_time = at
        for field in ("target_id", "ref", "actor", "stage", "from_state", "to_state", "reason", "related_id", "observation_id", "external_id"):
            require_optional_string(event[field], f"event {field}")
        require_optional_digest(event["digest"], "event digest")
        require(event["latency_ms"] is None or (isinstance(event["latency_ms"], int) and not isinstance(event["latency_ms"], bool) and event["latency_ms"] >= 0), "invalid event latency")
        require(
            event["anchor_kind"] is None
            or event["anchor_kind"]
            in {"PULL_REQUEST_DESCRIPTION", "REQUEST_COMMENT"},
            "invalid event anchor kind",
        )
        require_optional_string(event["anchor_id"], "event anchor id")
        require(
            event["on_target"] is None or isinstance(event["on_target"], bool),
            "invalid event on-target flag",
        )
        require_optional_string(
            event["observation_snapshot"], "event observation snapshot"
        )
        kind = event["type"]
        if kind.startswith("CONNECTOR_"):
            require(
                event["observation_snapshot"] is not None,
                "connector observation lacks its canonical snapshot",
            )
        else:
            require(
                event["observation_snapshot"] is None,
                "non-connector event contains a connector observation snapshot",
            )
        if delivered_at is not None and kind != "RUN_ENDED":
            raise ValidationError("semantic activity occurred after DELIVERED")
        if sequence == 1:
            require(kind == "RUN_STARTED", "run start must be first")
        if kind == "RUN_ENDED":
            require(sequence == len(record["events"]) and active_writer is None and not active_stages, "run end must be final with no active work")
            ended = True
        elif kind == "PHASE_ENTERED":
            require(event["to_state"] in RECORD_STATUSES, "invalid phase")
            if phases:
                require(event["to_state"] in PHASE_TRANSITIONS[phases[-1]], "illegal phase transition")
                require(event["from_state"] == phases[-1], "phase from_state mismatch")
            else:
                require(event["to_state"] == "INTAKE" and event["from_state"] is None, "first phase must be INTAKE")
            phases.append(event["to_state"])
            if event["to_state"] == "DELIVERED":
                delivered_at = at
        elif kind in {"STAGE_STARTED", "STAGE_COMPLETED"}:
            require(event["target_id"] in target_ids and event["ref"] is not None and event["stage"] is not None, "stage event lacks target/ref/stage")
            family, ordinal = parse_stage(event["stage"], "event stage")
            if kind == "STAGE_STARTED":
                require(event["target_id"] not in active_stages, "target has overlapping stages")
                previous = stage_ordinals.get((event["target_id"], family))
                expected = (0 if family in {"I", "PR"} else 1) if previous is None else previous + 1
                require(ordinal == expected, "stage ordinal skipped, repeated, or reversed")
                active_stages[event["target_id"]] = (
                    event["stage"], event["ref"], at
                )
                stage_ordinals[(event["target_id"], family)] = ordinal
            else:
                active_stage = active_stages.get(event["target_id"])
                require(
                    active_stage is not None
                    and active_stage[:2] == (event["stage"], event["ref"]),
                    "stage completion does not match active stage",
                )
                completed_stages.append(
                    {
                        "target_id": event["target_id"],
                        "ref": event["ref"],
                        "stage": event["stage"],
                        "started_at": active_stage[2],
                        "completed_at": at,
                    }
                )
                del active_stages[event["target_id"]]
        elif kind == "ROOT_ESCALATION":
            require(event["actor"] == "root" and event["target_id"] in target_ids and event["ref"] is not None and event["digest"] is not None and event["reason"], "invalid root escalation")
            require(active_writer is None, "root escalation occurred during writer activity")
            pending_root = event
        elif kind == "WRITER_STARTED":
            require(event["actor"] in participants and event["target_id"] in target_ids and event["ref"] is not None and event["reason"], "invalid writer start")
            require(event["actor"] in permitted_writers, "writer start names a read-only role")
            require(active_writer is None, "writer activity overlaps")
            if event["actor"] == "root" and WORKFLOW_WRITER_ROLES.get(record["workflow"]):
                require(previous_event is pending_root and pending_root is not None and pending_root["target_id"] == event["target_id"] and pending_root["ref"] == event["ref"] and pending_root["digest"] == event["digest"], "root writer start lacks matching immediate pre-edit ROOT_ESCALATION")
            pending_root = None
            active_writer = event["actor"]
            active_start = (event["target_id"], event["ref"], at)
        elif kind == "WRITER_STOPPED":
            require(event["actor"] == active_writer and active_start is not None and at > active_start[2], "writer stop does not match active writer")
            writer_intervals.append((event["actor"], active_start[0], active_start[2], at, active_start[1]))
            active_writer = None
            active_start = None
        elif kind.startswith("CONNECTOR_"):
            require(
                event["target_id"] in target_ids
                and event["ref"]
                and event["related_id"]
                and event["observation_id"]
                and event["latency_ms"] is not None
                and event["anchor_kind"] is not None
                and event["anchor_id"] is not None
                and event["on_target"] is not None
                and event["digest"] is not None,
                "connector observation lacks durable scope/content/timing identity",
            )
            require(event["observation_id"] not in observations, "duplicate connector observation id")
            report = validate_connector_observation_snapshot(
                event["observation_snapshot"], event["digest"]
            )
            require(
                report["observed_at"] == event["at"]
                and report["round"] == event["stage"]
                and report["heads"]["requested"] == event["ref"],
                "connector event scope differs from its helper snapshot",
            )
            if kind == "CONNECTOR_ROUND_STARTED":
                require(
                    event["actor"] == "root"
                    and event["external_id"] is None
                    and event["from_state"] is None
                    and event["to_state"] == "PENDING_PICKUP"
                    and event["reason"] in {"AUTOMATIC", "MANUAL"}
                    and event["on_target"] is True,
                    "invalid connector round-start observation",
                )
                require(
                    report["state"] == "PENDING_PICKUP"
                    and event["latency_ms"]
                    == int(round(report["latency_seconds"]["observed"] * 1000))
                    and event["latency_ms"] < 120_000,
                    "connector round-start snapshot is not an honest initial pending observation",
                )
            elif kind == "CONNECTOR_PICKUP_OBSERVED":
                require(
                    event["actor"] in CONNECTOR_ACTORS
                    and event["external_id"]
                    and event["reason"] == "eyes"
                    and event["on_target"] is True
                    and event["from_state"] in {"PENDING_PICKUP", "PICKUP_UNOBSERVED"}
                    and event["to_state"] == "IN_PROGRESS",
                    "illegal connector pickup transition",
                )
                selected = [
                    item
                    for item in report["events"]
                    if item["type"] == "REACTION"
                    and str(item["id"]) == event["external_id"]
                    and item["actor"] == event["actor"]
                    and item["content"] == "eyes"
                    and item["on_target"] is True
                ]
                pickup_latency = report["latency_seconds"]["pickup"]
                require(
                    len(selected) == 1
                    and report["state"] == "IN_PROGRESS"
                    and pickup_latency is not None
                    and event["latency_ms"]
                    == int(round(pickup_latency * 1000)),
                    "connector pickup is not projected from one exact helper observation",
                )
            elif kind == "CONNECTOR_PICKUP_REMOVED":
                require(
                    event["actor"] == "root"
                    and event["external_id"]
                    and event["reason"] == "eyes_removed"
                    and event["on_target"] is True
                    and event["from_state"] == "IN_PROGRESS"
                    and event["to_state"] == "PICKUP_REMOVED",
                    "connector pickup removal lacks exact in-progress evidence",
                )
                require(
                    report["state"]
                    in {"CLEAN", "FINDINGS_POSTED", "TERMINAL_OFF_TARGET"}
                    and not any(
                        item["type"] == "REACTION"
                        and item["content"] == "eyes"
                        and item["on_target"] is True
                        for item in report["events"]
                    )
                    and event["latency_ms"]
                    == int(round(report["latency_seconds"]["observed"] * 1000)),
                    "connector pickup-removal snapshot still contains the pickup",
                )
            elif kind == "CONNECTOR_PICKUP_UNOBSERVED":
                require(
                    event["actor"] == "root"
                    and event["external_id"] is None
                    and event["latency_ms"] >= 120_000
                    and event["from_state"] == "PENDING_PICKUP"
                    and event["to_state"] == "PICKUP_UNOBSERVED"
                    and event["reason"] == "NO_EYES_WITHIN_120_SECONDS"
                    and event["on_target"] is True,
                    "pickup-unobserved evidence violates the two-minute boundary",
                )
                require(
                    report["state"] == "PICKUP_UNOBSERVED"
                    and event["latency_ms"]
                    == int(round(report["latency_seconds"]["observed"] * 1000)),
                    "pickup-unobserved event differs from its helper snapshot",
                )
            elif kind == "CONNECTOR_TERMINAL_OBSERVED":
                require(
                    event["actor"] in CONNECTOR_ACTORS and event["external_id"],
                    "connector terminal lacks external connector identity",
                )
                require(
                    event["to_state"]
                    in CONNECTOR_STATES - {"PENDING_PICKUP", "IN_PROGRESS", "PICKUP_UNOBSERVED"},
                    "illegal connector terminal observation",
                )
                if event["to_state"] == "CLEAN":
                    require(
                        event["reason"] == "+1" and event["on_target"] is True,
                        "clean connector result is not an on-target +1",
                    )
                    selected = [
                        item
                        for item in report["events"]
                        if item["type"] == "REACTION"
                        and str(item["id"]) == event["external_id"]
                        and item["actor"] == event["actor"]
                        and item["content"] == "+1"
                        and item["on_target"] is True
                    ]
                    terminal_latency = report["latency_seconds"]["terminal"]
                elif event["to_state"] == "FINDINGS_POSTED":
                    require(
                        event["reason"] == "findings" and event["on_target"] is True,
                        "connector findings are not on the requested target/ref",
                    )
                    selected = [
                        item
                        for item in report["events"]
                        if item["type"] in {"REVIEW", "RESULT_COMMENT", "REVIEW_COMMENT"}
                        and str(item["id"]) == event["external_id"]
                        and item["actor"] == event["actor"]
                        and item["substantive"] is True
                        and item["commit_binding"] in {"MATCH", "UNAVAILABLE"}
                    ]
                    terminal_latency = report["latency_seconds"]["terminal"]
                elif event["to_state"] == "TERMINAL_OFF_TARGET":
                    require(event["on_target"] is False, "off-target terminal is marked on-target")
                    selected = [
                        item
                        for item in report["events"]
                        if str(item["id"]) == event["external_id"]
                        and item["actor"] == event["actor"]
                        and (
                            (
                                item["type"] == "REACTION"
                                and item["content"] == "+1"
                                and item["on_target"] is False
                            )
                            or item.get("commit_binding") in {"MISMATCH", "AMBIGUOUS"}
                        )
                    ]
                    terminal_latency = report["latency_seconds"]["off_target_terminal"]
                else:
                    selected = [
                        item
                        for item in report["events"]
                        if str(item["id"]) == event["external_id"]
                        and item["actor"] == event["actor"]
                    ]
                    terminal_latency = report["latency_seconds"]["observed"]
                require(
                    report["state"] == event["to_state"]
                    and len(selected) == 1
                    and terminal_latency is not None
                    and event["latency_ms"]
                    == int(round(terminal_latency * 1000)),
                    "connector terminal is not projected from one exact helper observation",
                )
            observations[event["observation_id"]] = event
            observation_reports[event["observation_id"]] = report
        previous_event = event
    require(phases and phases[-1] == record["status"], "record status differs from latest phase")
    return {
        "ended": ended,
        "phases": phases,
        "observations": observations,
        "observation_reports": observation_reports,
        "writer_intervals": writer_intervals,
        "completed_stages": completed_stages,
        "delivered_at": delivered_at,
        "last_at": previous_time,
    }


def validate_family_expansion_controls(
    record: dict[str, Any],
    manifest: dict[str, Any],
    *,
    qualification: bool,
) -> list[dict[str, Any]]:
    """Derive canonical expansion counts and enforce configured stop controls."""

    thresholds = manifest["workflows"][record["workflow"]].get(
        "family_expansion_thresholds"
    )
    if thresholds is None:
        return []
    require(
        thresholds == list(FAMILY_EXPANSION_THRESHOLDS),
        "record workflow has unsupported family expansion thresholds",
    )
    events_with_index = list(enumerate(record["events"]))
    expansion_events: dict[str, tuple[int, dict[str, Any]]] = {}
    canonical_expansion_ids = {
        finding["id"]
        for finding in record["findings"]
        if finding["duplicate_of"] is None and finding["miss_reason"] is not None
    }
    for index, event in events_with_index:
        if event["type"] != "FAMILY_EXPANSION_RECORDED":
            continue
        require(
            event["related_id"] in canonical_expansion_ids,
            "family expansion event must reference a canonical expansion finding",
        )
        require(
            event["related_id"] not in expansion_events,
            "canonical expansion has multiple count events",
        )
        expansion_events[event["related_id"]] = (index, event)

    by_scope: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for finding in record["findings"]:
        if finding["duplicate_of"] is not None or finding["miss_reason"] is None:
            continue
        by_scope.setdefault((finding["target_id"], finding["family"]), []).append(
            finding
        )

    reviews = {review["id"]: review for review in record["reviews"]}
    bindings = {
        binding["binding_id"]: binding for binding in record["bindings"]
    }
    declarations = {
        item["role"]: item
        for item in [
            record["team_context"]["root"],
            *record["team_context"]["workflow_roles"],
        ]
    }
    derived: list[dict[str, Any]] = []

    def control_event(
        event_type: str,
        finding: dict[str, Any],
    ) -> tuple[int, dict[str, Any]] | None:
        matches = [
            (index, event)
            for index, event in events_with_index
            if event["type"] == event_type
            and event["related_id"] == finding["id"]
        ]
        require(len(matches) <= 1, f"{event_type} is duplicated for one expansion")
        return matches[0] if matches else None

    def scoped_obligation(
        kind: str,
        finding: dict[str, Any],
        role: str,
    ) -> dict[str, Any] | None:
        matches = [
            obligation
            for obligation in record["obligations"]
            if obligation["kind"] == kind
            and obligation["target_id"] == finding["target_id"]
            and obligation["ref"] == finding["ref"]
            and obligation["stage"] == finding["stage"]
            and obligation["role"] == role
        ]
        require(len(matches) <= 1, f"duplicate {kind} family obligation")
        return matches[0] if matches else None

    for (target_id, family), findings in by_scope.items():
        prior_event_index: int | None = None
        for count, finding in enumerate(findings, 1):
            recorded = expansion_events.get(finding["id"])
            if recorded is None:
                require(
                    not qualification,
                    f"family {family} expansion {count} lacks FAMILY_EXPANSION_RECORDED",
                )
                prior_event_index = None
                continue
            event_index, event = recorded
            require(
                prior_event_index is None or event_index > prior_event_index,
                "family expansion count chronology moves backwards",
            )
            prior_event_index = event_index
            require(
                event["actor"] == "root"
                and event["target_id"] == target_id
                and event["ref"] == finding["ref"]
                and event["stage"] == finding["stage"]
                and event["from_state"] == family
                and event["to_state"] == f"EXPANSION_{count}"
                and event["reason"] == finding["miss_reason"]
                and event["digest"] is not None,
                "family expansion event does not bind its count/family/miss/ref",
            )
            later_writer_indices = [
                index
                for index, candidate in events_with_index
                if index > event_index and candidate["type"] == "WRITER_STARTED"
            ]
            next_writer = min(later_writer_indices) if later_writer_indices else None

            if count == thresholds[1]:
                boundary = control_event("FAMILY_BOUNDARY_STOP", finding)
                challenge_required = control_event(
                    "PUBLICATION_CHALLENGE_REQUIRED", finding
                )
                boundary_obligation = scoped_obligation(
                    "FAMILY_BOUNDARY_ANALYSIS", finding, "root"
                )
                challenge_obligation = scoped_obligation(
                    "PUBLICATION_CHALLENGE", finding, PUBLICATION_CHALLENGE_ROLE
                )
                if qualification or next_writer is not None:
                    require(
                        boundary is not None,
                        "second family expansion lacks root-cause/design-boundary stop event",
                    )
                    require(
                        challenge_required is not None,
                        "second family expansion lacks publication-challenge-required event",
                    )
                    require(
                        boundary_obligation is not None
                        and boundary_obligation["status"] == "SATISFIED"
                        and finding["id"] in boundary_obligation["evidence_ids"]
                        and boundary_obligation["justification"],
                        "second family expansion lacks satisfied root/design-boundary analysis obligation",
                    )
                    require(
                        challenge_obligation is not None,
                        "second family expansion lacks fresh publication challenge obligation",
                    )
                if boundary is not None:
                    boundary_index, boundary_event = boundary
                    require(
                        boundary_index > event_index
                        and boundary_event["actor"] == "root"
                        and boundary_event["target_id"] == target_id
                        and boundary_event["ref"] == finding["ref"]
                        and boundary_event["stage"] == finding["stage"]
                        and boundary_event["from_state"] == f"EXPANSION_{count}"
                        and boundary_event["to_state"]
                        == "ROOT_CAUSE_DESIGN_BOUNDARY_STOP"
                        and boundary_event["reason"]
                        and boundary_event["digest"] is not None,
                        "invalid second-expansion boundary stop event",
                    )
                    if next_writer is not None:
                        require(
                            boundary_index < next_writer,
                            "writer started before second-expansion boundary stop",
                        )
                if challenge_required is not None:
                    challenge_index, challenge_event = challenge_required
                    require(
                        boundary is not None
                        and challenge_index > boundary[0]
                        and challenge_event["actor"] == "root"
                        and challenge_event["target_id"] == target_id
                        and challenge_event["ref"] == finding["ref"]
                        and challenge_event["stage"] == finding["stage"]
                        and challenge_event["from_state"] == f"EXPANSION_{count}"
                        and challenge_event["to_state"]
                        == "FRESH_PUBLICATION_CHALLENGE_REQUIRED"
                        and challenge_event["reason"]
                        and challenge_event["digest"] is not None,
                        "invalid fresh-publication-challenge requirement event",
                    )
                    if next_writer is not None:
                        require(
                            challenge_index < next_writer,
                            "writer started before fresh publication challenge was required",
                        )
                    if qualification:
                        require(
                            challenge_obligation is not None
                            and challenge_obligation["status"] == "SATISFIED",
                            "fresh publication challenge obligation is incomplete",
                        )
                        challenge_reviews = [
                            reviews[evidence_id]
                            for evidence_id in challenge_obligation["evidence_ids"]
                            if evidence_id in reviews
                        ]
                        final_ref = next(
                            target["final_ref"]
                            for target in record["targets"]
                            if target["target_id"] == target_id
                        )
                        require(
                            any(
                                review["reviewer_role"]
                                == PUBLICATION_CHALLENGE_ROLE
                                and review["context_mode"] == "FRESH_ISOLATED"
                                and review["evidence_state"] == "FRESH"
                                and review["result"] != "BLOCKED"
                                and review["target_id"] == target_id
                                and review["ref"] == final_ref
                                and review["binding_id"] in bindings
                                and binding_is_qualified(
                                    bindings[review["binding_id"]],
                                    declarations[PUBLICATION_CHALLENGE_ROLE],
                                )
                                and parse_timestamp(
                                    review["started_at"], "publication challenge start"
                                )
                                > parse_timestamp(
                                    challenge_event["at"],
                                    "publication challenge requirement",
                                )
                                for review in challenge_reviews
                            ),
                            "second expansion lacks a later fresh isolated publication challenge",
                        )

            if count >= thresholds[2]:
                authorization = control_event(
                    "MAINTAINER_DECISION_RECORDED", finding
                )
                authorization_obligation = scoped_obligation(
                    "MAINTAINER_DECISION", finding, "maintainer"
                )
                if qualification or next_writer is not None:
                    require(
                        authorization is not None,
                        "third-or-later family expansion lacks maintainer authorization event",
                    )
                    require(
                        authorization_obligation is not None
                        and authorization_obligation["status"] == "SATISFIED"
                        and finding["id"]
                        in authorization_obligation["evidence_ids"]
                        and authorization_obligation["justification"],
                        "third-or-later family expansion lacks constrained maintainer decision obligation",
                    )
                if authorization is not None:
                    authorization_index, authorization_event = authorization
                    require(
                        authorization_index > event_index
                        and authorization_event["actor"] == "maintainer"
                        and authorization_event["target_id"] == target_id
                        and authorization_event["ref"] == finding["ref"]
                        and authorization_event["stage"] == finding["stage"]
                        and authorization_event["from_state"]
                        == f"EXPANSION_{count}"
                        and authorization_event["to_state"]
                        in FAMILY_AUTHORIZATION_ACTIONS
                        and authorization_event["reason"]
                        and authorization_event["digest"] is not None,
                        "invalid third-or-later maintainer authorization event",
                    )
                    if next_writer is not None:
                        require(
                            authorization_index < next_writer,
                            "writer started before third-or-later maintainer authorization",
                        )

        derived.append(
            {"target_id": target_id, "family": family, "expansion_count": len(findings)}
        )
    return sorted(
        derived, key=lambda item: (item["target_id"], item["family"])
    )


def validate_v3_record(record: dict[str, Any], team: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version", "semantics_version", "run_id", "workflow", "status", "policy_bundle",
        "continuity", "repository", "team_context", "targets", "artifacts", "bindings", "events",
        "obligations", "skip_manifest", "criteria", "reviews", "findings", "handoffs", "checks",
        "external_evidence", "external_gates", "mutations", "connector_rounds", "deliveries", "violations",
    }
    require_exact_keys(record, required, "record")
    require(record["schema_version"] == CURRENT_RECORD_VERSION, "invalid current schema version")
    require(record["semantics_version"] == CURRENT_SEMANTICS_VERSION, "unsupported record semantics version")
    require_string(record["run_id"], "run id")
    require(record["workflow"] in WORKFLOWS, "record workflow is invalid")
    require(record["status"] in RECORD_STATUSES, "record status is invalid")
    validate_policy_bundle_record(record["policy_bundle"])
    continuity = require_exact_keys(record["continuity"], {"source_schema_version", "source_record_digest", "upgraded_at", "limitations"}, "continuity")
    require(continuity["source_schema_version"] in {None, 1, 2}, "invalid continuity source version")
    require_optional_digest(continuity["source_record_digest"], "continuity source digest")
    require_optional_string(continuity["upgraded_at"], "continuity upgraded_at")
    if continuity["upgraded_at"] is not None:
        parse_timestamp(continuity["upgraded_at"], "continuity upgraded_at")
    require_string_list(continuity["limitations"], "continuity limitations")
    require((continuity["source_schema_version"] is None) == (continuity["source_record_digest"] is None), "continuity source version/digest must be paired")
    target_ids, targets = validate_v3_targets(record)
    artifact_ids = validate_v3_repository_and_artifacts(record, target_ids)
    declarations, manifest_comparison = validate_v3_context(record, team, manifest)
    participants, bindings, binding_reports = validate_v3_bindings(
        record, declarations, target_ids
    )
    skip_ids = validate_v3_skip_manifest(record, target_ids)
    criterion_ids, criterion_evidence_ids = validate_v3_criteria(record, participants, target_ids)
    finding_ids = validate_v3_findings(record, participants, target_ids)
    review_ids = validate_v3_reviews(record, bindings, target_ids)
    for review in record["reviews"]:
        require(set(review["finding_ids"]) <= finding_ids, "review names unknown findings")
    for finding in record["findings"]:
        if finding["source_kind"] == "EXTERNAL":
            pass
    check_ids = validate_v3_checks(record, target_ids, artifact_ids, skip_ids)
    external_evidence_ids = validate_v3_external_evidence(record, target_ids)
    external_evidence = {item["id"]: item for item in record["external_evidence"]}
    gate_ids = validate_v3_external_gates(
        record, target_ids, skip_ids, external_evidence
    )
    mutation_ids = validate_v3_mutations(record, target_ids, participants)
    round_ids = validate_v3_connectors(record, target_ids, finding_ids)
    validate_connector_trigger_mutations(record)
    delivery_ids = validate_v3_deliveries(record, target_ids)
    violation_ids = validate_v3_violations(record, target_ids)
    handoff_ids = validate_v3_handoffs(record, target_ids, participants)
    obligation_ids = validate_v3_obligations(record, target_ids, skip_ids)
    event_report = validate_v3_events(record, target_ids, participants)
    semantic_times: list[tuple[str, datetime]] = []
    semantic_times.extend(
        ("artifact", parse_timestamp(item["built_at"], "artifact built_at"))
        for item in record["artifacts"]
    )
    semantic_times.extend(
        ("review", parse_timestamp(item["ended_at"], "review ended_at"))
        for item in record["reviews"]
    )
    semantic_times.extend(
        ("check", parse_timestamp(item["ended_at"], "check ended_at"))
        for item in record["checks"]
    )
    semantic_times.extend(
        ("external evidence", parse_timestamp(item["observed_at"], "external evidence observed_at"))
        for item in record["external_evidence"]
    )
    semantic_times.extend(
        ("external gate", parse_timestamp(item["checked_at"], "external gate checked_at"))
        for item in record["external_gates"]
    )
    semantic_times.extend(
        ("mutation", parse_timestamp(item["at"], "mutation at"))
        for item in record["mutations"]
    )
    semantic_times.extend(
        (
            "connector round",
            parse_timestamp(
                item["completed_at"] or item["started_at"],
                "connector round time",
            ),
        )
        for item in record["connector_rounds"]
    )
    semantic_times.extend(
        ("delivery", parse_timestamp(item["at"], "delivery at"))
        for item in record["deliveries"]
    )
    if event_report["delivered_at"] is not None:
        for label, occurred in semantic_times:
            require(
                occurred <= event_report["delivered_at"],
                f"{label} activity occurred after DELIVERED",
            )
    if event_report["ended"]:
        for label, occurred in semantic_times:
            require(
                occurred <= event_report["last_at"],
                f"{label} activity occurred after RUN_ENDED",
            )
    family_expansions = validate_family_expansion_controls(
        record, manifest, qualification=False
    )
    if not manifest["workflows"][record["workflow"]]["writer"]:
        require(not ({"IMPLEMENTATION", "REMEDIATION"} & set(event_report["phases"])), "read-only workflow entered a writer phase")
    all_evidence_ids = (
        set(bindings) | skip_ids | criterion_ids | criterion_evidence_ids | finding_ids | review_ids | check_ids |
        external_evidence_ids | gate_ids | mutation_ids | round_ids | delivery_ids | violation_ids | handoff_ids |
        artifact_ids
    )
    for obligation in record["obligations"]:
        require(set(obligation["evidence_ids"]) <= all_evidence_ids, "obligation cites unknown evidence")
    for connector in record["connector_rounds"]:
        require(
            set(connector["observation_ids"]).issubset(
                event_report["observations"]
            ),
            "connector round cites unknown observation",
        )
        observations = [event_report["observations"][item] for item in connector["observation_ids"]]
        reports = [
            event_report["observation_reports"][item]
            for item in connector["observation_ids"]
        ]
        require(
            connector["observation_ids"]
            == [item["observation_id"] for item in sorted(observations, key=lambda item: item["sequence"])],
            "connector observations are not in event order",
        )
        require(
            all(
                item["related_id"] == connector["id"]
                and item["target_id"] == connector["target_id"]
                and item["ref"] == connector["ref"]
                and item["stage"] == connector["round"]
                and item["anchor_kind"] == connector["anchor_kind"]
                for item in observations
            ),
            "connector observation scope differs from its round",
        )
        require(
            all(
                report["repo"] == connector["repository"]
                and f"{report['repo']}#{report['pr']}" == connector["pull_request"]
                and report["round"] == connector["round"]
                and report["heads"]["requested"] == connector["ref"]
                and report["request"]["requested_at"] == connector["started_at"]
                for report in reports
            ),
            "connector helper snapshot identity differs from its round",
        )
        if connector["round"] == "C1":
            require(
                connector["anchor_id"] == connector["pull_request"]
                and all(
                    report["request"]["target_kind"] == "PR_DESCRIPTION"
                    and report["request"]["target_id"] == report["pr"]
                    for report in reports
                ),
                "automatic connector snapshots are not anchored to the PR description",
            )
        else:
            require(
                connector["anchor_id"] == connector["trigger_id"]
                and all(
                    report["request"]["target_kind"] == "TRIGGER_COMMENT"
                    and str(report["request"]["target_id"])
                    == connector["trigger_id"]
                    for report in reports
                ),
                "requested connector snapshots are not anchored to the trigger comment",
            )
        require(
            observations[0]["type"] == "CONNECTOR_ROUND_STARTED"
            and observations[0]["anchor_id"] == connector["anchor_id"]
            and parse_timestamp(observations[0]["at"], "connector round-start event")
            >= parse_timestamp(connector["started_at"], "connector round started_at"),
            "connector round lacks one exact first helper observation",
        )
        require(
            all(
                item["anchor_id"] == connector["anchor_id"]
                for item in observations
                if item["on_target"] is True
            ),
            "on-target connector observation names another anchor",
        )
        pickups = [item for item in observations if item["type"] == "CONNECTOR_PICKUP_OBSERVED"]
        removals = [item for item in observations if item["type"] == "CONNECTOR_PICKUP_REMOVED"]
        unobserved = [item for item in observations if item["type"] == "CONNECTOR_PICKUP_UNOBSERVED"]
        terminal = [item for item in observations if item["type"] == "CONNECTOR_TERMINAL_OBSERVED"]
        if connector["state"] == "PENDING_PICKUP":
            require(len(observations) == 1, "pending connector round has later observations")
        elif connector["state"] == "IN_PROGRESS":
            require(pickups and not removals and not terminal, "in-progress connector state lacks current eyes")
        elif connector["state"] == "PICKUP_UNOBSERVED":
            require(unobserved and observations[-1] is unobserved[-1] and not terminal, "pickup-unobserved state lacks the bounded no-eyes observation")
        else:
            require(terminal and terminal[-1]["to_state"] == connector["state"], "connector terminal state lacks a matching observation")
        if pickups and terminal:
            require(
                removals
                and removals[-1]["sequence"] > pickups[-1]["sequence"]
                and removals[-1]["sequence"] < terminal[-1]["sequence"],
                "connector terminal result did not remove the observed eyes pickup",
            )
            pickup_report = event_report["observation_reports"][
                pickups[-1]["observation_id"]
            ]
            removal_report = event_report["observation_reports"][
                removals[-1]["observation_id"]
            ]
            pickup_external_id = pickups[-1]["external_id"]
            require(
                removals[-1]["external_id"] == pickup_external_id
                and any(
                    item["type"] == "REACTION"
                    and str(item["id"]) == pickup_external_id
                    and item["content"] == "eyes"
                    and item["on_target"] is True
                    for item in pickup_report["events"]
                )
                and not any(
                    item["type"] == "REACTION"
                    and item["content"] == "eyes"
                    and item["on_target"] is True
                    for item in removal_report["events"]
                )
                and removals[-1]["observation_snapshot"]
                == terminal[-1]["observation_snapshot"],
                "pickup removal is not proven by the before/after helper snapshots",
            )
        require(
            reports[-1]["state"] == connector["state"],
            "connector round state differs from its last helper snapshot",
        )
        if connector["state"] in {"CLEAN", "FINDINGS_POSTED"}:
            require(
                connector["completed_at"] == terminal[-1]["at"],
                "connector completion time differs from its terminal observation",
            )
    for mutation in record["mutations"]:
        if mutation["kind"] != "REPOSITORY" or mutation["result"] != "APPLIED":
            continue
        at = parse_timestamp(mutation["at"], "mutation at")
        require(
            any(actor == mutation["actor"] and target == mutation["target_id"] and start <= at <= end for actor, target, start, end, _ in event_report["writer_intervals"]),
            "repository mutation occurred outside its writer interval",
        )
    intervals = event_report["writer_intervals"]
    for previous, current in zip(intervals, intervals[1:]):
        if previous[0] == current[0]:
            continue
        matching = [
            item for item in record["handoffs"]
            if item["from"] == previous[0] and item["to"] == current[0]
            and item["target_id"] == current[1]
            and parse_timestamp(item["at"], "handoff at") >= previous[3]
            and parse_timestamp(item["at"], "handoff at") <= current[2]
        ]
        require(len(matching) == 1, "writer ownership changed without one exact handoff")
    policy_comparison = "CURRENT" if record["policy_bundle"] == team["policy_bundle"] else "DRIFTED"
    serializable_event_report = {
        "ended": event_report["ended"],
        "phases": list(event_report["phases"]),
        "observation_count": len(event_report["observations"]),
        "completed_stages": [
            {
                "target_id": stage["target_id"],
                "ref": stage["ref"],
                "stage": stage["stage"],
                "started_at": stage["started_at"].isoformat().replace(
                    "+00:00", "Z"
                ),
                "completed_at": stage["completed_at"].isoformat().replace(
                    "+00:00", "Z"
                ),
            }
            for stage in event_report["completed_stages"]
        ],
        "delivered_at": (
            event_report["delivered_at"].isoformat().replace("+00:00", "Z")
            if event_report["delivered_at"] is not None
            else None
        ),
        "writer_intervals": [
            {
                "actor": actor,
                "target_id": target_id,
                "started_at": started_at.isoformat().replace("+00:00", "Z"),
                "ended_at": ended_at.isoformat().replace("+00:00", "Z"),
                "ref": ref,
            }
            for actor, target_id, started_at, ended_at, ref in event_report["writer_intervals"]
        ],
        "last_at": (
            event_report["last_at"].isoformat().replace("+00:00", "Z")
            if event_report["last_at"] is not None
            else None
        ),
    }
    return {
        "schema_version": CURRENT_RECORD_VERSION,
        "compatibility": "CURRENT",
        "manifest_comparison": manifest_comparison,
        "policy_comparison": policy_comparison,
        "bindings": binding_reports,
        "target_count": len(targets),
        "event_report": serializable_event_report,
        "family_expansions": family_expansions,
    }


def assert_record_qualified(
    record: dict[str, Any],
    team: dict[str, Any],
    *,
    trusted_worktree: Path | None = None,
) -> dict[str, Any]:
    report = validate_record(record, team)
    require(record.get("schema_version") == CURRENT_RECORD_VERSION, "legacy/incomplete records cannot satisfy --assert-qualified; upgrade to v3")
    validate_family_expansion_controls(
        record, load_toml(MANIFEST), qualification=True
    )
    require(report["manifest_comparison"] == "CURRENT", "frozen team context drifted before qualification")
    require(report["policy_comparison"] == "CURRENT", "policy bundle drifted before qualification")
    require(record["status"] in SEMANTIC_TERMINAL_STATUSES, "qualification requires DELIVERED or AWAITING_EXTERNAL")
    require(report["event_report"]["ended"], "qualified run must end explicitly")
    require(record["repository"]["head_ref"] is not None and record["repository"]["head_tree"] is not None, "qualified record lacks exact repository head/tree")
    targets = {target["target_id"]: target for target in record["targets"]}
    bindings = {binding["binding_id"]: binding for binding in record["bindings"]}
    reviews = {review["id"]: review for review in record["reviews"]}
    live_verified_packets: set[str] = set()
    skips = {item["id"]: item for item in record["skip_manifest"]["entries"]}
    obligations = record["obligations"]
    for obligation in obligations:
        require(obligation["status"] not in {"OPEN", "UNAVAILABLE"}, f"obligation is incomplete: {obligation['id']}")
        if obligation["status"] == "NOT_APPLICABLE":
            skip = skips[obligation["skip_id"]]
            require(skip["evidence_state"] == "NOT_APPLICABLE" and skip["target_id"] == obligation["target_id"] and skip["ref"] == obligation["ref"] and skip["obligation_kind"] == obligation["kind"], "obligation skip does not match its scope")
    declarations = {
        item["role"]: item
        for item in [
            record["team_context"]["root"],
            *record["team_context"]["workflow_roles"],
        ]
    }
    final_refs = {target_id: target["final_ref"] for target_id, target in targets.items()}
    repository = record["repository"]
    head_targets = {
        target_id
        for target_id, final_ref in final_refs.items()
        if final_ref is not None and final_ref == repository["head_ref"]
    }
    require(
        head_targets,
        "repository head ref does not match any final target",
    )
    repository_artifacts = [
        item
        for item in record["artifacts"]
        if item["kind"] == "REPOSITORY"
        and item["target_id"] in head_targets
        and item["ref"] == repository["head_ref"]
        and item["identifier"] == repository["repository_id"]
        and item["digest"] == repository["head_tree"]
    ]
    require(
        len(repository_artifacts) == 1,
        "repository head/tree lacks one exact repository artifact",
    )
    focused_i0_cycles: dict[str, dict[str, Any]] = {}
    if record["workflow"] == "deliver-focused-change":
        for target_id, final_ref in final_refs.items():
            cycles = [
                stage
                for stage in report["event_report"]["completed_stages"]
                if stage["target_id"] == target_id
                and stage["ref"] == final_ref
                and stage["stage"] == "I0"
            ]
            require(
                len(cycles) == 1,
                f"target {target_id} lacks one completed final-ref I0 review cycle",
            )
            focused_i0_cycles[target_id] = cycles[0]
    for target_id, final_ref in final_refs.items():
        require(final_ref is not None, f"target {target_id} lacks final ref")
        scoped = [item for item in obligations if item["target_id"] == target_id and item["ref"] == final_ref]
        required_kinds = {"CRITERIA", "CHECK", "QUALIFICATION", "EXTERNAL_GATE", "DELIVERY"}
        if record["workflow"] in {
            "clear-defect-backlog",
            "deliver-focused-change",
            "deliver-functional-slice",
            "maintain-open-prs",
        }:
            required_kinds.add("CONNECTOR")
        for kind in required_kinds:
            require(any(item["kind"] == kind and item["status"] in {"SATISFIED", "NOT_APPLICABLE"} for item in scoped), f"target {target_id} lacks complete {kind} obligation")
        for role, declaration_value in declarations.items():
            role_obligations = [item for item in scoped if item["kind"] == "BINDING" and item["role"] == role]
            require(len(role_obligations) == 1, f"target {target_id} lacks unique binding obligation for {role}")
            obligation = role_obligations[0]
            if obligation["status"] == "SATISFIED":
                scoped_bindings = [
                    bindings[binding_id]
                    for binding_id in obligation["evidence_ids"]
                    if binding_id in bindings
                ]
                require(scoped_bindings, f"required binding is missing: {role}")
                require(
                    all(
                        binding["requested_role"] == role
                        and binding["target_id"] in {None, target_id}
                        and binding["ref"] == final_ref
                        and binding_is_qualified(binding, declaration_value)
                        for binding in scoped_bindings
                    ),
                    f"required binding lacks exact strength/capability proof: {role}",
                )
            else:
                require(obligation["status"] == "NOT_APPLICABLE", f"binding obligation is incomplete: {role}")
        reviewer_roles = [item["role"] for item in record["team_context"]["workflow_roles"] if item["kind"] == "reviewer"]
        for role in reviewer_roles:
            review_obligations = [item for item in scoped if item["kind"] == "REVIEW" and item["role"] == role]
            require(len(review_obligations) == 1, f"target {target_id} lacks unique review/skip obligation for {role}")
            if record["workflow"] == "deliver-focused-change" and review_obligations[0]["status"] == "SATISFIED":
                review_obligation = review_obligations[0]
                require(
                    review_obligation["stage"] == "I0",
                    f"focused reviewer obligation is outside I0: {role}",
                )
                cycle = focused_i0_cycles[target_id]
                cycle_started = parse_timestamp(
                    cycle["started_at"], "I0 cycle started_at"
                )
                cycle_completed = parse_timestamp(
                    cycle["completed_at"], "I0 cycle completed_at"
                )
                for review_id in review_obligation["evidence_ids"]:
                    review = next(
                        item for item in record["reviews"] if item["id"] == review_id
                    )
                    require(
                        cycle_started
                        <= parse_timestamp(review["started_at"], "review started_at")
                        < parse_timestamp(review["ended_at"], "review ended_at")
                        <= cycle_completed,
                        f"focused I0 review falls outside its recorded cycle: {role}",
                    )
    require(record["criteria"], "qualified record has no criteria")
    external = {item["id"]: item for item in record["external_evidence"]}
    for criterion in record["criteria"]:
        require(criterion["status"] in {"PASS", "NOT_APPLICABLE"}, f"criterion is incomplete: {criterion['id']}")
        require(criterion["target_id"] in targets, "criterion target is unknown")
        if criterion["status"] == "PASS":
            for evidence in criterion["evidence"]:
                require(evidence["ref"] == final_refs[criterion["target_id"]], "criterion PASS is not final-ref-bound")
                require(evidence["evidence_state"] in {"FRESH", "INHERITED"}, "criterion PASS evidence is unavailable/stale")
                if evidence["source_kind"] == "ROLE":
                    matching = [
                        binding
                        for binding in bindings.values()
                        if binding["requested_role"] == evidence["source"]
                        and binding["target_id"] in {None, criterion["target_id"]}
                        and binding["ref"] == evidence["ref"]
                    ]
                    require(
                        matching
                        and any(
                            binding_is_qualified(
                                binding, declarations[evidence["source"]]
                            )
                            for binding in matching
                        ),
                        "criterion PASS is sourced from an unqualified invocation binding",
                    )
                else:
                    require(
                        evidence["source"] in external
                        and external[evidence["source"]]["target_id"]
                        == criterion["target_id"]
                        and external[evidence["source"]]["source_head"]
                        == evidence["ref"]
                        and external[evidence["source"]]["evidence_state"]
                        in {"FRESH", "INHERITED"},
                        "criterion external evidence is stale, off-target, or unbound",
                    )
    for obligation in obligations:
        if (
            obligation["kind"] in {"REVIEW", "PUBLICATION_CHALLENGE"}
            and obligation["status"] == "SATISFIED"
        ):
            require(obligation["evidence_ids"], "satisfied review obligation lacks a review")
            for review_id in obligation["evidence_ids"]:
                require(review_id in reviews and reviews[review_id]["target_id"] == obligation["target_id"] and reviews[review_id]["ref"] == obligation["ref"] and reviews[review_id]["reviewer_role"] == obligation["role"], "review obligation evidence is out of scope")
                review = reviews[review_id]
                require(review["result"] != "BLOCKED" and review["evidence_state"] in {"FRESH", "INHERITED"}, "required review is blocked/unavailable")
                require(
                    review["binding_id"] in bindings
                    and bindings[review["binding_id"]]["thread_id"] is not None
                    and binding_is_qualified(
                        bindings[review["binding_id"]],
                        declarations[review["reviewer_role"]],
                    ),
                    "satisfied review obligation uses an unqualified invocation binding",
                )
                if review["packet_digest"] not in live_verified_packets:
                    packet = validate_review_packet_snapshot(
                        review["packet_snapshot"], review["packet_digest"]
                    )
                    require(
                        record["workflow"] not in WORKFLOW_WRITER_ROLES
                        or packet["repository"]["mode"] != "NONE",
                        "writer workflow review packet lacks Git evidence",
                    )
                    validate_review_packet_live(
                        packet,
                        repository,
                        trusted_worktree=trusted_worktree or CODEX.parent,
                    )
                    live_verified_packets.add(review["packet_digest"])
    for finding in record["findings"]:
        require(finding["status"] == "RESOLVED", f"unresolved finding: {finding['id']}")
    artifacts = {item["id"]: item for item in record["artifacts"]}
    for check in record["checks"]:
        require(check["result"] == "PASS", f"failed/not-run check: {check['id']}")
        require(check["validity"] in {"FRESH", "INHERITED", "NOT_APPLICABLE"}, f"stale/unavailable check: {check['id']}")
        if check["validity"] != "NOT_APPLICABLE":
            require(check["ref"] == final_refs[check["target_id"]], "check is not final-ref-bound")
            require(
                all(
                    artifacts[item]["target_id"] == check["target_id"]
                    and artifacts[item]["ref"] == check["ref"]
                    for item in check["artifact_ids"]
                ),
                "check artifact is not final-target/ref-bound",
            )
    for target_id, final_ref in final_refs.items():
        kinds = {item["kind"] for item in record["artifacts"] if item["target_id"] == target_id and item["ref"] == final_ref}
        for kind in {"REPOSITORY", "TREE", "PACKAGE", "MODULE", "WHEEL"}:
            if kind not in kinds:
                require(any(item["target_id"] == target_id and item["ref"] == final_ref and item["owning_gate"] == f"ARTIFACT_IDENTITY:{kind}" and item["evidence_state"] == "NOT_APPLICABLE" for item in skips.values()), f"target {target_id} lacks {kind} identity or exact N/A skip")
    validate_v3_satisfied_obligation_evidence(record, bindings)
    for mutation in record["mutations"]:
        require(mutation["result"] not in {"FAILED", "INCONSISTENT"} and mutation["evidence_state"] not in {"STALE", "UNAVAILABLE"}, f"mutation is failed/inconsistent/stale: {mutation['id']}")
    require(all(item["status"] == "RESOLVED" for item in record["violations"]), "open protocol/execution-policy violation remains")
    pending_gates = [item for item in record["external_gates"] if item["required"] and item["status"] == "PENDING"]
    require(not any(item["required"] and item["status"] in {"BLOCKED", "UNAVAILABLE"} for item in record["external_gates"]), "required external gate is blocked/unavailable")
    if pending_gates:
        require(record["status"] == "AWAITING_EXTERNAL", "pending external gate requires AWAITING_EXTERNAL")
    elif record["status"] == "AWAITING_EXTERNAL":
        raise ValidationError("AWAITING_EXTERNAL lacks a pending required gate")
    for connector in record["connector_rounds"]:
        require(
            connector["state"]
            not in {"TERMINAL_OFF_TARGET", "PROTOCOL_VARIANCE", "INCONSISTENT"},
            "connector protocol is off-target or inconsistent",
        )
    deliveries = {item["target_id"]: item for item in record["deliveries"]}
    require(set(deliveries) == set(targets), "qualification requires one delivery per target")
    writer_workflow = bool(load_toml(MANIFEST)["workflows"][record["workflow"]]["writer"])
    for target_id, delivery in deliveries.items():
        require(delivery["ref"] == final_refs[target_id], "delivery is not final-ref-bound")
        if writer_workflow and record["workflow"] != "maintain-open-prs":
            require(delivery["mode"] == "PULL_REQUEST" and all(delivery[field] for field in ("branch", "commit", "pull_request")), "delivery workflow requires non-null branch/commit/pull request")
        elif record["workflow"] == "maintain-open-prs":
            require(delivery["mode"] in {"PULL_REQUEST", "QUEUE_HANDOFF"} and delivery["pull_request"], "PR maintenance delivery is incomplete")
        else:
            require(delivery["mode"] in {"DOCUMENTED", "REPORT_ONLY"}, "read-only workflow has inappropriate delivery")
        require(delivery["status"] == ("AWAITING_EXTERNAL" if record["status"] == "AWAITING_EXTERNAL" else "DELIVERED"), "delivery status differs from run status")
        if delivery["mode"] in {"PULL_REQUEST", "QUEUE_HANDOFF"}:
            c1 = [
                item
                for item in record["connector_rounds"]
                if item["target_id"] == target_id
                and item["round"] == "C1"
                and item["ref"] == delivery["ref"]
                and item["pull_request"] == delivery["pull_request"]
            ]
            require(
                len(c1) == 1,
                "PR delivery lacks one automatic C1 round for the exact PR/ref",
            )
            if c1[0]["state"] in {
                "PENDING_PICKUP",
                "IN_PROGRESS",
                "PICKUP_UNOBSERVED",
            }:
                require(
                    record["status"] == "AWAITING_EXTERNAL",
                    "nonterminal C1 must be handed off as AWAITING_EXTERNAL",
                )
    qualification_events = [item for item in record["events"] if item["type"] == "QUALIFICATION_RECORDED"]
    for target_id, final_ref in final_refs.items():
        qualification_obligations = [
            item
            for item in obligations
            if item["kind"] == "QUALIFICATION"
            and item["target_id"] == target_id
            and item["ref"] == final_ref
            and item["status"] == "SATISFIED"
        ]
        require(
            len(qualification_obligations) == 1,
            f"target {target_id} lacks one evidence-backed qualification obligation",
        )
        relevant = [
            item
            for item in qualification_events
            if item["target_id"] == target_id
            and item["ref"] == final_ref
            and item["related_id"] == qualification_obligations[0]["id"]
        ]
        require(len(relevant) == 1 and relevant[0]["digest"] is not None, f"target {target_id} lacks exact final-ref qualification event")
        qualified_at = parse_timestamp(relevant[0]["at"], "qualification at")
        latest_evidence = [parse_timestamp(item["ended_at"], "review/check end") for item in [*record["reviews"], *record["checks"]] if item["target_id"] == target_id]
        latest_evidence += [parse_timestamp(item["at"], "mutation at") for item in record["mutations"] if item["target_id"] == target_id]
        require(not latest_evidence or qualified_at >= max(latest_evidence), "qualification predates target evidence/mutation")
    return {**report, "qualified": True}


def validate_record(record: dict[str, Any], team: dict[str, Any]) -> dict[str, Any]:
    require(isinstance(record, dict), "record must be an object")
    version = record.get("schema_version")
    require(isinstance(version, int) and not isinstance(version, bool), "record schema version must be an integer")
    if version == LEGACY_RECORD_VERSION:
        return validate_legacy_record(record, team)
    if version == PREVIOUS_RECORD_VERSION:
        return validate_current_record(record, team, load_toml(MANIFEST))
    if version == CURRENT_RECORD_VERSION:
        return validate_v3_record(record, team, load_toml(MANIFEST))
    raise ValidationError(f"unsupported record schema version: {version!r}")


def initial_record(
    run_id: str,
    workflow: str,
    target_kind: str,
    target_id: str,
    frozen_ref: str,
    team: dict[str, Any],
    started_at: str | None = None,
) -> dict[str, Any]:
    manifest = load_toml(MANIFEST)
    started_at = started_at or utc_now()
    target_id_value = "target-1"
    context = frozen_context_v3(workflow, team, manifest)
    return {
        "schema_version": CURRENT_RECORD_VERSION,
        "semantics_version": CURRENT_SEMANTICS_VERSION,
        "run_id": run_id,
        "workflow": workflow,
        "status": "INTAKE",
        "policy_bundle": team["policy_bundle"],
        "continuity": {"source_schema_version": None, "source_record_digest": None, "upgraded_at": None, "limitations": []},
        "repository": {"repository_id": "mechanical-librarian", "base_ref": frozen_ref, "base_tree": None, "head_ref": None, "head_tree": None},
        "team_context": context,
        "targets": [{"target_id": target_id_value, "kind": target_kind, "id": target_id, "initial_ref": frozen_ref, "final_ref": None}],
        "artifacts": [],
        "bindings": [],
        "events": [
            v3_event(1, "RUN_STARTED", started_at),
            v3_event(2, "PHASE_ENTERED", started_at, to_state="INTAKE"),
        ],
        "obligations": [],
        "skip_manifest": {"version": 1, "entries": []},
        "criteria": [],
        "reviews": [],
        "findings": [],
        "handoffs": [],
        "checks": [],
        "external_evidence": [],
        "external_gates": [],
        "mutations": [],
        "connector_rounds": [],
        "deliveries": [],
        "violations": [],
    }


def v3_event(
    sequence: int,
    event_type: str,
    at: str,
    **values: Any,
) -> dict[str, Any]:
    event = {
        "sequence": sequence,
        "version": 1,
        "type": event_type,
        "at": at,
        "target_id": None,
        "ref": None,
        "actor": None,
        "stage": None,
        "from_state": None,
        "to_state": None,
        "reason": None,
        "related_id": None,
        "digest": None,
        "observation_id": None,
        "external_id": None,
        "latency_ms": None,
        "anchor_kind": None,
        "anchor_id": None,
        "on_target": None,
        "observation_snapshot": None,
    }
    event.update(values)
    return event


def upgrade_record(record: dict[str, Any], team: dict[str, Any], upgraded_at: str | None = None) -> dict[str, Any]:
    """Create a non-qualifying v3 continuation without altering the legacy source."""

    version = record.get("schema_version")
    require(version in {1, 2}, "only v1/v2 records can be upgraded")
    validate_record(record, team)
    upgraded_at = upgraded_at or utc_now()
    target = record["target"]
    result = initial_record(
        record["run_id"], record["workflow"], target["kind"], target["id"], target["frozen_ref"], team, upgraded_at
    )
    result["continuity"] = {
        "source_schema_version": version,
        "source_record_digest": canonical_fingerprint(record),
        "upgraded_at": upgraded_at,
        "limitations": [
            "legacy schema did not prove per-ref obligations, exact artifacts/environments, external mutations, or execution-policy violations",
            "all inherited claims require explicit reconciliation before qualification",
        ],
    }
    if version == 2:
        for index, legacy_binding in enumerate(record.get("bindings", []), 1):
            result["bindings"].append(
                {
                    "binding_id": f"legacy-binding-{index}",
                    "target_id": "target-1",
                    "ref": target["frozen_ref"],
                    **json.loads(json.dumps(legacy_binding)),
                    "capability_evidence": {
                        "verified": [],
                        "absent": [],
                        "provenance": "legacy schema did not attest capabilities",
                    },
                }
            )
    skip_id = "legacy-history-unavailable"
    result["skip_manifest"]["entries"].append(
        {
            "id": skip_id, "version": 1, "target_id": "target-1", "ref": target["frozen_ref"],
            "obligation_kind": "UPGRADE_RECONCILIATION", "role": None, "test_id": "legacy-v2-history",
            "platform": "historical", "reason": result["continuity"]["limitations"][0],
            "owning_gate": "UPGRADE_RECONCILIATION", "authority": "schema migration",
            "at": upgraded_at, "evidence_state": "UNAVAILABLE",
        }
    )
    result["obligations"].append(
        {
            "id": "upgrade-reconciliation", "version": 1, "kind": "UPGRADE_RECONCILIATION",
            "target_id": "target-1", "ref": target["frozen_ref"], "stage": None, "role": None,
            "status": "UNAVAILABLE", "evidence_ids": [skip_id],
            "justification": "legacy gaps must be reconciled with inherited evidence and explicit violations",
            "skip_id": skip_id,
        }
    )
    validate_record(result, team)
    return result


def self_test(team: dict[str, Any]) -> None:
    require(reader_wave_size(1, 3) == 0, "one-slot scheduling failed")
    require(reader_wave_size(2, 3) == 1, "two-slot scheduling failed")
    require(reader_wave_size(4, 3) == 3, "four-slot scheduling failed")
    require(reader_wave_size(8, 2) == 2, "declared scheduling cap failed")
    started = "2026-07-13T10:00:00Z"
    record = initial_record(
        "self-test",
        "deliver-focused-change",
        "ticket",
        "1",
        "revision-1",
        team,
        started,
    )
    report = validate_record(record, team)
    require(report["metrics"]["elapsed_time_seconds"] == "UNKNOWN", "missing elapsed evidence was inferred")

    def declaration(current: dict[str, Any], role: str) -> dict[str, Any]:
        if role == "root":
            return current["team_context"]["root"]
        return next(
            candidate
            for candidate in current["team_context"]["workflow_roles"]
            if candidate["role"] == role
        )

    def participating_binding(
        current: dict[str, Any],
        role: str,
        *,
        role_state: str = "DECLARED",
        model_state: str = "DECLARED",
    ) -> dict[str, Any]:
        declared = declaration(current, role)
        return {
            "requested_role": role,
            "thread_id": f"/root/{role}" if role != "root" else "/root",
            "role": {
                "value": role,
                "state": role_state,
                "provenance": "synthetic runtime evidence" if role_state == "VERIFIED" else "frozen team context",
            },
            "model": {
                "value": declared["model"] if model_state != "UNKNOWN" else None,
                "state": model_state,
                "provenance": "frozen team context" if model_state == "DECLARED" else "runtime did not expose model",
            },
            "reasoning": {
                "value": declared["reasoning"],
                "state": "DECLARED",
                "provenance": "frozen team context",
            },
            "permissions": {
                "value": declared["permissions"],
                "state": "DECLARED",
                "provenance": "frozen team context",
            },
        }

    def refresh_context_fingerprint(current: dict[str, Any]) -> None:
        context = current["team_context"]
        context["context_fingerprint"] = canonical_fingerprint(
            {
                "manifest_fingerprint": context["manifest_fingerprint"],
                "config_fingerprint": context["config_fingerprint"],
                "workflow": current["workflow"],
                "root": context["root"],
                "workflow_roles": context["workflow_roles"],
            }
        )

    def expect_record_failure(candidate: dict[str, Any], message: str) -> None:
        try:
            validate_record(candidate, team)
        except ValidationError:
            return
        raise ValidationError(message)

    def event(
        sequence: int,
        event_type: str,
        at: str,
        *,
        actor: str | None = None,
        phase: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        return {
            "sequence": sequence,
            "type": event_type,
            "at": at,
            "actor": actor,
            "phase": phase,
            "reason": reason,
        }

    invalid = json.loads(json.dumps(record))
    invalid["delivery"]["merge_authorized"] = True
    expect_record_failure(invalid, "invalid merge authority was accepted")

    invalid = json.loads(json.dumps(record))
    invalid["bindings"].append(
        {
            "requested_role": "unknown_role",
            "thread_id": None,
            "role": {"value": "unknown_role", "state": "DECLARED", "provenance": "synthetic"},
            "model": {"value": "unknown", "state": "DECLARED", "provenance": "synthetic"},
            "reasoning": {"value": "medium", "state": "DECLARED", "provenance": "synthetic"},
            "permissions": {"value": ":read-only", "state": "DECLARED", "provenance": "synthetic"},
        }
    )
    expect_record_failure(invalid, "unknown workflow role was accepted")

    invalid = json.loads(json.dumps(record))
    slice_declared = team["roles"]["slice_worker"]
    invalid["bindings"].append(
        {
            "requested_role": "slice_worker",
            "thread_id": None,
            "role": {"value": "slice_worker", "state": "DECLARED", "provenance": "another workflow"},
            "model": {"value": slice_declared["model"], "state": "DECLARED", "provenance": "another workflow"},
            "reasoning": {"value": slice_declared["reasoning"], "state": "DECLARED", "provenance": "another workflow"},
            "permissions": {
                "value": slice_declared["permissions"],
                "state": "DECLARED",
                "provenance": "another workflow",
            },
        }
    )
    expect_record_failure(invalid, "role from another workflow was accepted")

    invalid = json.loads(json.dumps(record))
    invalid["bindings"].append(participating_binding(record, "focused_worker"))
    invalid["bindings"].append(participating_binding(record, "focused_worker"))
    expect_record_failure(invalid, "duplicate participating binding was accepted")

    invalid = json.loads(json.dumps(record))
    binding = participating_binding(record, "focused_worker")
    binding["model"]["value"] = "wrong-model"
    invalid["bindings"].append(binding)
    expect_record_failure(invalid, "declared model mismatch was accepted")

    invalid = json.loads(json.dumps(record))
    invalid["team_context"]["workflow_roles"][1]["config_fingerprint"] = invalid[
        "team_context"
    ]["workflow_roles"][0]["config_fingerprint"]
    invalid_payload = {
        "manifest_fingerprint": invalid["team_context"]["manifest_fingerprint"],
        "config_fingerprint": invalid["team_context"]["config_fingerprint"],
        "workflow": invalid["workflow"],
        "root": invalid["team_context"]["root"],
        "workflow_roles": invalid["team_context"]["workflow_roles"],
    }
    invalid["team_context"]["context_fingerprint"] = canonical_fingerprint(invalid_payload)
    expect_record_failure(invalid, "copied role fingerprint was accepted")

    verified_role = json.loads(json.dumps(record))
    verified_role["bindings"].append(
        participating_binding(verified_role, "focused_verifier", role_state="VERIFIED", model_state="UNKNOWN")
    )
    verified_report = validate_record(verified_role, team)
    require(verified_report["bindings"][0]["role"]["state"] == "VERIFIED", "verified role evidence was lost")
    require(verified_report["bindings"][0]["model"]["state"] == "UNKNOWN", "unknown model evidence was promoted")

    invalid = json.loads(json.dumps(record))
    writer_binding = participating_binding(invalid, "focused_worker")
    reviewer_binding = participating_binding(invalid, "focused_verifier")
    reviewer_binding["thread_id"] = writer_binding["thread_id"]
    invalid["bindings"] = [writer_binding, reviewer_binding]
    expect_record_failure(invalid, "one runtime thread was accepted for multiple roles")

    invalid = json.loads(json.dumps(record))
    fallback = participating_binding(invalid, "focused_verifier")
    fallback["role"] = {
        "value": "generic_fallback",
        "state": "FALLBACK",
        "provenance": "synthetic fallback disclosure",
    }
    fallback["permissions"] = {
        "value": ":workspace",
        "state": "VERIFIED",
        "provenance": "synthetic runtime evidence",
    }
    invalid["bindings"] = [fallback]
    expect_record_failure(invalid, "reviewer fallback exceeded its read-only permission boundary")

    invalid = json.loads(json.dumps(record))
    fallback = participating_binding(invalid, "focused_worker")
    fallback["role"] = {
        "value": "generic_fallback",
        "state": "FALLBACK",
        "provenance": "synthetic fallback disclosure",
    }
    fallback["permissions"] = {
        "value": ":read-only",
        "state": "VERIFIED",
        "provenance": "synthetic runtime evidence",
    }
    invalid["bindings"] = [fallback]
    expect_record_failure(invalid, "read-only fallback was accepted for a writer role")

    invalid = json.loads(json.dumps(record))
    fallback = participating_binding(invalid, "focused_worker")
    fallback["role"] = {
        "value": "generic_fallback",
        "state": "FALLBACK",
        "provenance": "synthetic fallback disclosure",
    }
    invalid["bindings"] = [fallback]
    expect_record_failure(invalid, "requested-role permission declaration was accepted for a fallback writer")

    invalid = json.loads(json.dumps(record))
    reviewer = participating_binding(invalid, "focused_verifier")
    reviewer["permissions"] = {
        "value": ":workspace",
        "state": "FALLBACK",
        "provenance": "synthetic permission fallback",
    }
    invalid["bindings"] = [reviewer]
    expect_record_failure(invalid, "workspace permission fallback was accepted for a reviewer")

    invalid = json.loads(json.dumps(record))
    invalid["criteria"].append(
        {
            "id": "criterion-1",
            "text": "Synthetic evidence attribution",
            "status": "PASS",
            "evidence": [
                {"source_kind": "ROLE", "source": "focused_verifier", "summary": "not actually participating"}
            ],
        }
    )
    expect_record_failure(invalid, "evidence from a non-participating role was accepted")

    sequential = json.loads(json.dumps(record))
    sequential["status"] = "DELIVERED"
    sequential["bindings"] = [
        participating_binding(sequential, "focused_worker"),
        participating_binding(sequential, "root"),
    ]
    sequential["handoffs"] = [
        {
            "from": "focused_worker",
            "to": "root",
            "at": "2026-07-13T10:03:00Z",
            "reason": "bounded high-risk escalation",
            "frozen_ref": "revision-1",
            "scope": ["synthetic high-risk boundary"],
            "constraints": ["one writer"],
            "expected_evidence": ["synthetic check"],
        }
    ]
    sequential["events"] = [
        event(1, "RUN_STARTED", started),
        event(2, "PHASE_ENTERED", started, phase="INTAKE"),
        event(3, "PHASE_ENTERED", "2026-07-13T10:01:00Z", phase="IMPLEMENTATION"),
        event(
            4,
            "WRITER_STARTED",
            "2026-07-13T10:01:00Z",
            actor="focused_worker",
            reason="normal implementation",
        ),
        event(
            5,
            "WRITER_STOPPED",
            "2026-07-13T10:02:00Z",
            actor="focused_worker",
            reason="high-risk boundary found",
        ),
        event(
            6,
            "WRITER_STARTED",
            "2026-07-13T10:04:00Z",
            actor="root",
            reason="bounded high-risk escalation",
        ),
        event(
            7,
            "WRITER_STOPPED",
            "2026-07-13T10:05:00Z",
            actor="root",
            reason="bounded change complete",
        ),
        event(8, "PHASE_ENTERED", "2026-07-13T10:06:00Z", phase="REMEDIATION"),
        event(9, "PHASE_ENTERED", "2026-07-13T10:07:00Z", phase="QUALIFICATION"),
        event(10, "PHASE_ENTERED", "2026-07-13T10:08:00Z", phase="DELIVERED"),
        event(11, "RUN_ENDED", "2026-07-13T10:09:00Z"),
    ]
    sequential_report = validate_record(sequential, team)
    require(sequential_report["metrics"]["root_interventions"] == 1, "root intervention was not derived")
    require(sequential_report["metrics"]["remediation_cycles"] == 1, "remediation cycle was not derived")

    overlapping = json.loads(json.dumps(sequential))
    overlapping["events"][4] = {
        "sequence": 5,
        "type": "WRITER_STARTED",
        "at": "2026-07-13T10:02:00Z",
        "actor": "root",
        "phase": None,
        "reason": "bounded high-risk escalation",
    }
    expect_record_failure(overlapping, "overlapping writer activity was accepted")

    same_owner_resume = json.loads(json.dumps(sequential))
    same_owner_resume["events"].insert(
        7,
        event(
            8,
            "WRITER_STARTED",
            "2026-07-13T10:05:30Z",
            actor="root",
            reason="bounded high-risk escalation",
        ),
    )
    same_owner_resume["events"].insert(
        8,
        event(
            9,
            "WRITER_STOPPED",
            "2026-07-13T10:05:45Z",
            actor="root",
            reason="second interval complete",
        ),
    )
    for sequence, current_event in enumerate(same_owner_resume["events"], start=1):
        current_event["sequence"] = sequence
    validate_record(same_owner_resume, team)

    reused_transition = json.loads(json.dumps(sequential))
    reused_transition["events"].insert(
        7,
        event(
            8,
            "WRITER_STARTED",
            "2026-07-13T10:05:30Z",
            actor="focused_worker",
            reason="normal implementation",
        ),
    )
    reused_transition["events"].insert(
        8,
        event(
            9,
            "WRITER_STOPPED",
            "2026-07-13T10:05:45Z",
            actor="focused_worker",
            reason="second interval complete",
        ),
    )
    for sequence, current_event in enumerate(reused_transition["events"], start=1):
        current_event["sequence"] = sequence
    expect_record_failure(reused_transition, "one handoff authorized a second ownership transition")

    root_first = json.loads(json.dumps(record))
    root_first["bindings"] = [participating_binding(root_first, "root")]
    root_first["events"].append(
        event(3, "WRITER_STARTED", "2026-07-13T10:01:00Z", actor="root", reason="unhanded escalation")
    )
    expect_record_failure(root_first, "root writer activity without an ownership handoff was accepted")

    unhanded_transition = initial_record(
        "unhanded-transition",
        "maintain-open-prs",
        "queue",
        "synthetic-queue",
        "revision-3",
        team,
        started,
    )
    unhanded_transition["bindings"] = [
        participating_binding(unhanded_transition, "defect_worker"),
        participating_binding(unhanded_transition, "slice_worker"),
    ]
    unhanded_transition["events"].extend(
        [
            event(3, "WRITER_STARTED", "2026-07-13T10:01:00Z", actor="defect_worker", reason="first scope"),
            event(4, "WRITER_STOPPED", "2026-07-13T10:02:00Z", actor="defect_worker", reason="first done"),
            event(5, "WRITER_STARTED", "2026-07-13T10:03:00Z", actor="slice_worker", reason="second scope"),
            event(6, "WRITER_STOPPED", "2026-07-13T10:04:00Z", actor="slice_worker", reason="second done"),
        ]
    )
    expect_record_failure(unhanded_transition, "writer ownership changed without a handoff")

    backwards_handoffs = json.loads(json.dumps(sequential))
    backwards_handoffs["handoffs"].append(
        {
            "from": "root",
            "to": "focused_worker",
            "at": "2026-07-13T10:05:30Z",
            "reason": "return routine ownership",
            "frozen_ref": "revision-1",
            "scope": ["synthetic routine boundary"],
            "constraints": ["one writer"],
            "expected_evidence": ["synthetic check"],
        }
    )
    backwards_handoffs["handoffs"].reverse()
    expect_record_failure(backwards_handoffs, "backwards handoff chronology was accepted")

    read_only = initial_record(
        "read-only",
        "prepare-ready-work",
        "ticket",
        "2",
        "revision-2",
        team,
        started,
    )
    read_only["bindings"].append(participating_binding(read_only, "root"))
    read_only["events"].append(
        event(
            3,
            "WRITER_STARTED",
            "2026-07-13T10:01:00Z",
            actor="root",
            reason="not permitted",
        )
    )
    expect_record_failure(read_only, "writer activity in a read-only workflow was accepted")

    reclassified = json.loads(json.dumps(read_only))
    reclassified["bindings"] = []
    reclassified["events"] = reclassified["events"][:2]
    reclassified["team_context"]["workflow_roles"][0]["kind"] = "writer"
    reclassified["team_context"]["workflow_roles"][0]["permissions"] = ":workspace"
    refresh_context_fingerprint(reclassified)
    expect_record_failure(reclassified, "a read-only workflow role was reclassified as a writer")

    missing_default_writer = json.loads(json.dumps(record))
    missing_default_writer["team_context"]["workflow_roles"] = [
        declared
        for declared in missing_default_writer["team_context"]["workflow_roles"]
        if declared["role"] != "focused_worker"
    ]
    refresh_context_fingerprint(missing_default_writer)
    expect_record_failure(missing_default_writer, "required default writer declaration was omitted")

    missing_maintenance_writer = initial_record(
        "missing-maintenance-writer",
        "maintain-open-prs",
        "queue",
        "synthetic-queue",
        "revision-4",
        team,
        started,
    )
    missing_maintenance_writer["team_context"]["workflow_roles"] = [
        declared
        for declared in missing_maintenance_writer["team_context"]["workflow_roles"]
        if declared["role"] != "slice_worker"
    ]
    refresh_context_fingerprint(missing_maintenance_writer)
    expect_record_failure(missing_maintenance_writer, "required maintenance writer candidate was omitted")

    lineage = json.loads(json.dumps(verified_role))
    lineage["findings"] = [
        {
            "id": "canonical",
            "source_kind": "ROLE",
            "source": "focused_verifier",
            "severity": "MEDIUM",
            "summary": "canonical finding",
            "disposition": "RESOLVED",
            "duplicate_of": None,
            "evidence": [],
        },
        {
            "id": "duplicate",
            "source_kind": "EXTERNAL",
            "source": "synthetic external reviewer",
            "severity": "LOW",
            "summary": "duplicate finding",
            "disposition": "RESOLVED",
            "duplicate_of": "canonical",
            "evidence": [],
        },
    ]
    lineage_report = validate_record(lineage, team)
    require(lineage_report["metrics"]["unique_findings"] == 1, "unique finding count is wrong")
    require(lineage_report["metrics"]["duplicate_findings"] == 1, "duplicate finding count is wrong")
    for bad_target in ("duplicate", "missing"):
        invalid = json.loads(json.dumps(lineage))
        invalid["findings"][1]["duplicate_of"] = bad_target
        expect_record_failure(invalid, f"invalid duplicate lineage {bad_target!r} was accepted")
    invalid = json.loads(json.dumps(lineage))
    invalid["findings"][0]["duplicate_of"] = "duplicate"
    expect_record_failure(invalid, "cyclic duplicate lineage was accepted")

    invalid = json.loads(json.dumps(record))
    invalid["team_context"]["manifest_fingerprint"] = "0" * 64
    expect_record_failure(invalid, "manifest identity mutation was not bound by the context fingerprint")

    invalid = json.loads(json.dumps(record))
    invalid["team_context"]["config_fingerprint"] = "0" * 64
    expect_record_failure(invalid, "config identity mutation was not bound by the context fingerprint")

    drifted_team = json.loads(json.dumps(team))
    drifted_team["manifest_fingerprint"] = "0" * 64
    drifted_report = validate_record(record, drifted_team)
    require(drifted_report["manifest_comparison"] == "DRIFTED", "manifest drift was not reported")

    schema_invalid_cases = [
        ("run_id", 7, "numeric run id was accepted"),
        ("target.id", ["not", "a", "string"], "list target id was accepted"),
        ("checks", [{"command": "", "result": "PASS", "summary": ""}], "empty check command was accepted"),
        ("checks", [{"command": "true", "result": "PASS", "summary": 7}], "numeric check summary was accepted"),
        ("delivery.branch", 7, "numeric delivery branch was accepted"),
        ("events.sequence", True, "boolean event sequence was accepted"),
    ]
    for field, value, message in schema_invalid_cases:
        invalid = json.loads(json.dumps(record))
        if field == "run_id":
            invalid["run_id"] = value
        elif field == "target.id":
            invalid["target"]["id"] = value
        elif field == "checks":
            invalid["checks"] = value
        elif field == "delivery.branch":
            invalid["delivery"]["branch"] = value
        else:
            invalid["events"][0]["sequence"] = value
        expect_record_failure(invalid, message)

    invalid = json.loads(json.dumps(lineage))
    invalid["findings"][0]["evidence"] = [7]
    expect_record_failure(invalid, "numeric finding evidence was accepted")

    invalid = json.loads(json.dumps(sequential))
    invalid["handoffs"][0]["scope"] = "not-a-list"
    expect_record_failure(invalid, "malformed handoff scope was accepted")

    invalid = json.loads(json.dumps(record))
    invalid["events"][1]["at"] = "2026-07-13T09:59:59Z"
    expect_record_failure(invalid, "backwards event chronology was accepted")

    invalid = json.loads(json.dumps(record))
    invalid["schema_version"] = 999
    expect_record_failure(invalid, "unsupported schema version was accepted")

    legacy_path = CODEX / "runs" / "maintain-prs-20260711-159" / "team-run.json"
    if legacy_path.is_file():
        legacy_report = validate_record(json.loads(legacy_path.read_text(encoding="utf-8")), team)
        require(legacy_report["compatibility"] == "LEGACY_READ_ONLY", "legacy record was not preserved")
        require(
            all(binding["model"] == "UNKNOWN" for binding in legacy_report["bindings"]),
            "legacy composite binding claim verified a model",
        )

    manifest = load_toml(MANIFEST)
    role_texts = {
        role: (CODEX / declared["config"]).read_text(encoding="utf-8")
        for role, declared in manifest["roles"].items()
    }
    skill_texts = {
        workflow: (CODEX / declared["skill"]).read_text(encoding="utf-8")
        for workflow, declared in manifest["workflows"].items()
    }
    validate_policy_wiring(manifest, role_texts, skill_texts)

    def expect_policy_failure(
        candidate_manifest: dict[str, Any],
        candidate_roles: dict[str, str],
        candidate_skills: dict[str, str],
        message: str,
    ) -> None:
        try:
            validate_policy_wiring(candidate_manifest, candidate_roles, candidate_skills)
        except ValidationError:
            return
        raise ValidationError(message)

    broken_skills = dict(skill_texts)
    broken_skills["deliver-focused-change"] = broken_skills[
        "deliver-focused-change"
    ].replace(REVIEW_METHOD, "missing-review-method")
    expect_policy_failure(
        manifest,
        role_texts,
        broken_skills,
        "missing writer-workflow review method was accepted",
    )

    broken_roles = dict(role_texts)
    broken_roles["focused_verifier"] = broken_roles["focused_verifier"].replace(
        REVIEW_METHOD,
        "missing-review-method",
    )
    expect_policy_failure(
        manifest,
        broken_roles,
        skill_texts,
        "missing delivery-reviewer review method was accepted",
    )

    broken_roles = dict(role_texts)
    broken_roles["focused_verifier"] += f"\n{FAMILY_POLICY}\n"
    expect_policy_failure(
        manifest,
        broken_roles,
        skill_texts,
        "reviewer self-certification of the root family policy was accepted",
    )

    broken_roles = dict(role_texts)
    broken_roles[PUBLICATION_CHALLENGE_ROLE] += f"\n{REVIEW_METHOD}\n"
    expect_policy_failure(
        manifest,
        broken_roles,
        skill_texts,
        "publication challenger coupling was accepted",
    )

    broken_skills = dict(skill_texts)
    broken_skills["deliver-focused-change"] = broken_skills[
        "deliver-focused-change"
    ].replace(REVIEW_LENSES, "missing-review-lenses")
    expect_policy_failure(
        manifest,
        role_texts,
        broken_skills,
        "missing dynamic review lenses were accepted",
    )

    broken_skills = dict(skill_texts)
    broken_skills["deliver-focused-change"] = broken_skills[
        "deliver-focused-change"
    ].replace(MUTATION_POLICY, "missing-mutation-policy")
    expect_policy_failure(
        manifest,
        role_texts,
        broken_skills,
        "missing mutation policy was accepted",
    )

    broken_roles = dict(role_texts)
    broken_roles["focused_worker"] = broken_roles["focused_worker"].replace(
        WORKER_SCOPE_POLICY,
        "missing-worker-scope-policy",
    )
    expect_policy_failure(
        manifest,
        broken_roles,
        skill_texts,
        "missing writer role scope policy was accepted",
    )

    broken_manifest = json.loads(json.dumps(manifest))
    broken_manifest["workflows"]["maintain-open-prs"]["roles"].remove("slice_worker")
    expect_policy_failure(
        broken_manifest,
        role_texts,
        skill_texts,
        "incomplete maintain-open-prs writer candidates were accepted",
    )


def self_test_v3(team: dict[str, Any]) -> None:
    require(reader_wave_size(1, 3) == 0, "one-slot scheduling failed")
    require(reader_wave_size(4, 3) == 3, "four-slot scheduling failed")
    record = initial_record(
        "self-test-v3", "deliver-focused-change", "ticket", "1", "a" * 40, team,
        "2026-07-15T10:00:00Z",
    )
    report = validate_record(record, team)
    require(report["compatibility"] == "CURRENT", "v3 record did not validate structurally")
    try:
        assert_record_qualified(record, team)
    except ValidationError:
        pass
    else:
        raise ValidationError("empty v3 record falsely qualified")


def discover_records(
    root: Path,
    *,
    workflow: str | None = None,
    target_kind: str | None = None,
    target_id: str | None = None,
) -> list[dict[str, Any]]:
    results = []
    for path in sorted(root.rglob("team-run.json")) if root.is_dir() else []:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if workflow is not None and record.get("workflow") != workflow:
            continue
        targets = record.get("targets")
        if not isinstance(targets, list):
            legacy = record.get("target")
            targets = [legacy] if isinstance(legacy, dict) else []
        if target_kind is not None and not any(item.get("kind") == target_kind for item in targets):
            continue
        if target_id is not None and not any(item.get("id") == target_id for item in targets):
            continue
        results.append(
            {
                "path": str(path), "run_id": record.get("run_id"), "schema_version": record.get("schema_version"),
                "workflow": record.get("workflow"), "status": record.get("status"),
                "targets": [{key: item.get(key) for key in ("target_id", "kind", "id", "final_ref")} for item in targets],
                "resumable": record.get("status") != "DELIVERED" and not any(event.get("type") == "RUN_ENDED" for event in record.get("events", [])),
            }
        )
    run_ids = [item["run_id"] for item in results]
    require(len(run_ids) == len(set(run_ids)), "run index contains duplicate run ids")
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="print team validation as JSON")
    parser.add_argument("--runtime-slots", type=int, help="show adaptive reader capacity")
    parser.add_argument("--record", type=Path, help="validate an existing team-run record")
    parser.add_argument("--assert-qualified", action="store_true", help="apply the strict semantic completion gate to --record")
    parser.add_argument("--self-test", action="store_true", help="run synthetic validation checks")
    parser.add_argument("--init-run", metavar="RUN_ID", help="create a new private run record")
    parser.add_argument("--upgrade-record", type=Path, help="read a v1/v2 record and create a separate non-qualifying v3 continuation")
    parser.add_argument("--upgrade-output", type=Path, help="new path for --upgrade-record (the source is never rewritten)")
    parser.add_argument("--runs-root", type=Path, default=CODEX / "runs")
    parser.add_argument("--index-runs", action="store_true", help="index discoverable records under --runs-root")
    parser.add_argument("--resume-run", nargs="?", const="", metavar="RUN_ID", help="find one resumable run by id or workflow/target filters")
    parser.add_argument("--workflow", choices=sorted(WORKFLOWS))
    parser.add_argument("--target-kind", choices=["idea", "slice", "ticket", "pull_request", "queue"])
    parser.add_argument("--target-id")
    parser.add_argument("--frozen-ref")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        team = validate_team()
        if args.runtime_slots is not None:
            manifest = load_toml(MANIFEST)
            team["runtime_slots"] = args.runtime_slots
            team["reader_wave_sizes"] = {
                name: reader_wave_size(args.runtime_slots, workflow["max_parallel_readers"])
                for name, workflow in manifest["workflows"].items()
            }
        if args.record:
            loaded_record = json.loads(args.record.read_text(encoding="utf-8"))
            record_report = assert_record_qualified(loaded_record, team) if args.assert_qualified else validate_record(loaded_record, team)
            team["validated_record"] = str(args.record)
            team["record_report"] = record_report
        if args.self_test:
            self_test_v3(team)
            team["self_test"] = "PASS"
        if args.upgrade_record:
            require(args.upgrade_output is not None, "--upgrade-record requires --upgrade-output")
            require(args.upgrade_output != args.upgrade_record, "upgrade output must differ from the legacy source")
            upgraded = upgrade_record(json.loads(args.upgrade_record.read_text(encoding="utf-8")), team)
            args.upgrade_output.parent.mkdir(parents=True, exist_ok=True)
            require(not args.upgrade_output.exists(), "upgrade output already exists")
            args.upgrade_output.write_text(json.dumps(upgraded, indent=2) + "\n", encoding="utf-8")
            team["upgraded_record"] = str(args.upgrade_output)
        if args.index_runs or args.resume_run is not None:
            index = discover_records(args.runs_root, workflow=args.workflow, target_kind=args.target_kind, target_id=args.target_id)
            team["run_index"] = index
            if args.resume_run is not None:
                candidates = [item for item in index if item["resumable"] and (not args.resume_run or item["run_id"] == args.resume_run)]
                require(len(candidates) == 1, f"resume requires exactly one matching run; found {len(candidates)}")
                team["resume_record"] = candidates[0]
        if args.init_run:
            require(
                all([args.workflow, args.target_kind, args.target_id, args.frozen_ref]),
                "--init-run requires --workflow, --target-kind, --target-id, and --frozen-ref",
            )
            require(re.fullmatch(r"[A-Za-z0-9._-]+", args.init_run) is not None, "unsafe run id")
            record = initial_record(
                args.init_run,
                args.workflow,
                args.target_kind,
                args.target_id,
                args.frozen_ref,
                team,
            )
            validate_record(record, team)
            output = CODEX / "runs" / args.init_run / "team-run.json"
            output.parent.mkdir(parents=True, exist_ok=False)
            output.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
            team["created_record"] = str(output)
        if args.json:
            print(json.dumps(team, indent=2, sort_keys=True))
        else:
            print(f"PASS: {len(team['roles'])} roles and {len(team['workflows'])} workflows validated")
            if args.runtime_slots is not None:
                print(f"reader wave sizes: {team['reader_wave_sizes']}")
            if args.record:
                print(f"record validated: {args.record}")
                print(json.dumps(team["record_report"], indent=2, sort_keys=True))
            if args.self_test:
                print("synthetic workflow checks: PASS")
            if args.init_run:
                print(f"record created: {team['created_record']}")
            if args.upgrade_record:
                print(f"record upgraded: {team['upgraded_record']}")
            if args.index_runs:
                print(json.dumps(team["run_index"], indent=2, sort_keys=True))
            if args.resume_run is not None:
                print(f"resume record: {team['resume_record']['path']}")
        return 0
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
