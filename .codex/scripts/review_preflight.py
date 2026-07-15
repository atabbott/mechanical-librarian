#!/usr/bin/env python3
"""Validate a frozen review-obligation packet against one exact Git target."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn


SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import validate_team as team_validation  # noqa: E402


ADR_PATTERN = re.compile(r"^docs/architecture/decisions/[0-9]{4}-[^/]+\.md$")
MUTABLE_ADR_FIELDS = ("- Status:", "- Superseded by:")
OID_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
PACKET_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SCHEMA_VERSION = 2
PASS = "PASS"
FAIL = "FAIL"
ERROR = "ERROR"
PASSING_EVIDENCE_STATES = {"FRESH", "INHERITED"}
SOURCE_KINDS = {"CHECK", "REVIEW", "EXTERNAL_GATE"}
MAX_PACKET_BYTES = 2 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 20 * 1024 * 1024


class PacketError(ValueError):
    """A clean, user-correctable packet or invocation error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _raise(code: str, message: str) -> NoReturn:
    raise PacketError(code, message)


def _json_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _raise("DUPLICATE_JSON_KEY", f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _object(value: Any, label: str, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        _raise("INVALID_PACKET", f"{label} must be an object")
    actual = set(value)
    if actual != keys:
        missing = sorted(keys - actual)
        extra = sorted(actual - keys)
        details = []
        if missing:
            details.append(f"missing {missing}")
        if extra:
            details.append(f"unexpected {extra}")
        _raise("INVALID_PACKET", f"{label} has invalid fields: {', '.join(details)}")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        _raise("INVALID_PACKET", f"{label} must be an array")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        _raise("INVALID_PACKET", f"{label} must be a nonempty, surrounding-whitespace-free string")
    if "\x00" in value:
        _raise("INVALID_PACKET", f"{label} must not contain NUL")
    return value


def _nullable_reason(value: Any, label: str, *, required: bool) -> str | None:
    if value is None and not required:
        return None
    reason = _string(value, label)
    if not required:
        _raise("INVALID_PACKET", f"{label} must be null when the obligation is REQUIRED")
    return reason


def _timestamp(value: Any, label: str) -> datetime:
    raw = _string(value, label)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise PacketError("INVALID_TIMESTAMP", f"{label} is not ISO 8601: {raw!r}") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _raise("INVALID_TIMESTAMP", f"{label} requires an explicit UTC offset")
    return parsed


def _oid(value: Any, label: str) -> str:
    oid = _string(value, label)
    if not OID_PATTERN.fullmatch(oid):
        _raise("INVALID_PACKET", f"{label} must be a lowercase 40-character Git object ID")
    return oid


def _sha256(value: Any, label: str) -> str:
    digest = _string(value, label)
    if not SHA256_PATTERN.fullmatch(digest):
        _raise("INVALID_PACKET", f"{label} must be a lowercase 64-character SHA-256 digest")
    return digest


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _absolute_path(value: Any, label: str) -> Path:
    raw = _string(value, label)
    path = Path(raw)
    if not path.is_absolute():
        _raise("INVALID_PACKET", f"{label} must be absolute")
    resolved = path.resolve(strict=False)
    if str(resolved) != raw:
        _raise("INVALID_PACKET", f"{label} must be normalized: expected {str(resolved)!r}")
    return resolved


def normalize_repo_path(value: Any, label: str) -> str:
    raw = _string(value, label)
    if "\\" in raw:
        _raise("UNSAFE_PATH", f"{label} must use repository POSIX separators")
    pure = PurePosixPath(raw)
    if pure.is_absolute() or raw != pure.as_posix():
        _raise("UNSAFE_PATH", f"{label} must be a normalized repository-relative path")
    if any(part in {"", ".", ".."} for part in pure.parts):
        _raise("UNSAFE_PATH", f"{label} contains an unsafe path segment")
    return raw


def _path_list(value: Any, label: str, *, patterns: bool = False) -> list[str]:
    result: list[str] = []
    for index, item in enumerate(_list(value, label)):
        item_label = f"{label}[{index}]"
        if patterns and isinstance(item, str) and item.endswith("/**"):
            prefix = normalize_repo_path(item[:-3], item_label)
            normalized = f"{prefix}/**"
        else:
            normalized = normalize_repo_path(item, item_label)
            if patterns and "*" in normalized:
                _raise("UNSAFE_PATH", f"{item_label} supports only an exact path or a trailing /**")
        if normalized in result:
            _raise("INVALID_PACKET", f"{label} contains duplicate path {normalized!r}")
        result.append(normalized)
    return result


def _validate_evidence(
    value: Any,
    label: str,
) -> dict[str, Any]:
    evidence = _object(
        value,
        label,
        {
            "id",
            "source_kind",
            "source_id",
            "expected_command",
            "expected_artifact_ids",
            "expected_environment_digest",
            "expected_invalidation_rule",
        },
    )
    _string(evidence["id"], f"{label}.id")
    source_kind = _string(evidence["source_kind"], f"{label}.source_kind")
    if source_kind not in SOURCE_KINDS:
        _raise("INVALID_PACKET", f"{label}.source_kind is unsupported")
    _string(evidence["source_id"], f"{label}.source_id")
    _string(evidence["expected_command"], f"{label}.expected_command")
    artifact_ids = [
        _string(item, f"{label}.expected_artifact_ids[{index}]")
        for index, item in enumerate(_list(evidence["expected_artifact_ids"], f"{label}.expected_artifact_ids"))
    ]
    if not artifact_ids or len(set(artifact_ids)) != len(artifact_ids):
        _raise("INVALID_PACKET", f"{label}.expected_artifact_ids must be nonempty and unique")
    _sha256(evidence["expected_environment_digest"], f"{label}.expected_environment_digest")
    _string(evidence["expected_invalidation_rule"], f"{label}.expected_invalidation_rule")
    return evidence


def _validate_file_obligation(value: Any, label: str) -> dict[str, Any]:
    obligation = _object(value, label, {"state", "paths", "reason"})
    state = _string(obligation["state"], f"{label}.state")
    if state not in {"REQUIRED", "NOT_APPLICABLE"}:
        _raise("INVALID_PACKET", f"{label}.state must be REQUIRED or NOT_APPLICABLE")
    paths = _path_list(obligation["paths"], f"{label}.paths")
    if state == "REQUIRED":
        if not paths:
            _raise("INVALID_PACKET", f"{label}.paths must not be empty when REQUIRED")
        _nullable_reason(obligation["reason"], f"{label}.reason", required=False)
    else:
        if paths:
            _raise("INVALID_PACKET", f"{label}.paths must be empty when NOT_APPLICABLE")
        _nullable_reason(obligation["reason"], f"{label}.reason", required=True)
    return obligation


def _validate_qualification(
    value: Any,
    label: str,
) -> dict[str, Any]:
    obligation = _object(value, label, {"state", "reason", "evidence"})
    state = _string(obligation["state"], f"{label}.state")
    if state not in {"REQUIRED", "NOT_APPLICABLE"}:
        _raise("INVALID_PACKET", f"{label}.state must be REQUIRED or NOT_APPLICABLE")
    raw_evidence = _list(obligation["evidence"], f"{label}.evidence")
    if state == "REQUIRED":
        if not raw_evidence:
            _raise("INVALID_PACKET", f"{label}.evidence must not be empty when REQUIRED")
        _nullable_reason(obligation["reason"], f"{label}.reason", required=False)
    else:
        if raw_evidence:
            _raise("INVALID_PACKET", f"{label}.evidence must be empty when NOT_APPLICABLE")
        _nullable_reason(obligation["reason"], f"{label}.reason", required=True)
    for index, evidence in enumerate(raw_evidence):
        _validate_evidence(evidence, f"{label}.evidence[{index}]")
    return obligation


def validate_packet(value: Any) -> dict[str, Any]:
    packet = _object(
        value,
        "packet",
        {
            "schema_version",
            "packet_id",
            "frozen_at",
            "repository",
            "worktree",
            "target",
            "evidence_snapshot",
            "changed_paths",
            "obligations",
        },
    )
    if packet["schema_version"] != SCHEMA_VERSION or isinstance(packet["schema_version"], bool):
        _raise("UNSUPPORTED_SCHEMA", f"schema_version must be {SCHEMA_VERSION}")
    packet_id = _string(packet["packet_id"], "packet.packet_id")
    if not PACKET_ID_PATTERN.fullmatch(packet_id):
        _raise("INVALID_PACKET", "packet.packet_id contains unsupported characters")
    frozen_at = _timestamp(packet["frozen_at"], "packet.frozen_at")

    repository = _object(packet["repository"], "packet.repository", {"common_dir", "origin"})
    _absolute_path(repository["common_dir"], "packet.repository.common_dir")
    _string(repository["origin"], "packet.repository.origin")

    worktree = _object(packet["worktree"], "packet.worktree", {"root", "branch"})
    _absolute_path(worktree["root"], "packet.worktree.root")
    _string(worktree["branch"], "packet.worktree.branch")

    target = _object(
        packet["target"],
        "packet.target",
        {"target_id", "base_ref", "base_commit", "base_tree", "head_ref", "head_commit", "head_tree"},
    )
    target_id = _string(target["target_id"], "packet.target.target_id")
    _string(target["base_ref"], "packet.target.base_ref")
    base_commit = _oid(target["base_commit"], "packet.target.base_commit")
    _oid(target["base_tree"], "packet.target.base_tree")
    _string(target["head_ref"], "packet.target.head_ref")
    head_commit = _oid(target["head_commit"], "packet.target.head_commit")
    head_tree = _oid(target["head_tree"], "packet.target.head_tree")
    if base_commit == head_commit:
        _raise("INVALID_PACKET", "base_commit and head_commit must identify a nonempty comparison")

    snapshot = _object(
        packet["evidence_snapshot"],
        "packet.evidence_snapshot",
        {"path", "sha256", "run_id"},
    )
    normalize_repo_path(snapshot["path"], "packet.evidence_snapshot.path")
    _sha256(snapshot["sha256"], "packet.evidence_snapshot.sha256")
    _string(snapshot["run_id"], "packet.evidence_snapshot.run_id")

    changed_paths = _object(packet["changed_paths"], "packet.changed_paths", {"allowed", "required"})
    allowed = _path_list(changed_paths["allowed"], "packet.changed_paths.allowed", patterns=True)
    required = _path_list(changed_paths["required"], "packet.changed_paths.required")
    if not allowed:
        _raise("INVALID_PACKET", "packet.changed_paths.allowed must not be empty")
    for path in required:
        if not any(path == pattern or (pattern.endswith("/**") and path.startswith(pattern[:-2])) for pattern in allowed):
            _raise("INVALID_PACKET", f"required changed path is outside the allowed boundary: {path}")

    obligations = _object(
        packet["obligations"],
        "packet.obligations",
        {
            "documentation",
            "migrations",
            "security",
            "changelog",
            "private_qualification",
            "linux_qualification",
            "gates",
        },
    )
    seen_docs: set[str] = set()
    for index, item in enumerate(_list(obligations["documentation"], "packet.obligations.documentation")):
        label = f"packet.obligations.documentation[{index}]"
        document = _object(item, label, {"path", "claims"})
        path = normalize_repo_path(document["path"], f"{label}.path")
        if path in seen_docs:
            _raise("INVALID_PACKET", f"duplicate documentation obligation: {path}")
        seen_docs.add(path)
        for claim_index, raw_claim in enumerate(_list(document["claims"], f"{label}.claims")):
            claim_label = f"{label}.claims[{claim_index}]"
            claim = _object(raw_claim, claim_label, {"type", "value"})
            claim_type = _string(claim["type"], f"{claim_label}.type")
            claim_value = _string(claim["value"], f"{claim_label}.value")
            if claim_type not in {"LITERAL", "REGEX"}:
                _raise("INVALID_PACKET", f"{claim_label}.type must be LITERAL or REGEX")
            if claim_type == "REGEX":
                try:
                    re.compile(claim_value)
                except re.error as error:
                    raise PacketError("INVALID_REGEX", f"{claim_label}.value is invalid: {error}") from error

    for name in ("migrations", "security", "changelog"):
        _validate_file_obligation(obligations[name], f"packet.obligations.{name}")
    for name in ("private_qualification", "linux_qualification"):
        _validate_qualification(obligations[name], f"packet.obligations.{name}")

    seen_gates: set[str] = set()
    for index, raw_gate in enumerate(_list(obligations["gates"], "packet.obligations.gates")):
        label = f"packet.obligations.gates[{index}]"
        gate = _object(raw_gate, label, {"id", "state", "evidence"})
        gate_id = _string(gate["id"], f"{label}.id")
        if gate_id in seen_gates:
            _raise("INVALID_PACKET", f"duplicate declared gate: {gate_id}")
        seen_gates.add(gate_id)
        gate_state = _string(gate["state"], f"{label}.state")
        if gate_state not in {PASS, FAIL, "UNKNOWN"}:
            _raise("INVALID_PACKET", f"{label}.state must be PASS, FAIL, or UNKNOWN")
        gate_evidence = _list(gate["evidence"], f"{label}.evidence")
        if not gate_evidence:
            _raise("INVALID_PACKET", f"{label}.evidence must not be empty")
        for evidence_index, evidence in enumerate(gate_evidence):
            _validate_evidence(evidence, f"{label}.evidence[{evidence_index}]")
    return packet


def run_git(root: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def git(root: Path, *arguments: str) -> str:
    return run_git(root, *arguments).stdout


def resolve_git(root: Path, specification: str, kind: str) -> str:
    return git(root, "rev-parse", "--verify", f"{specification}^{{{kind}}}").strip()


def object_exists(root: Path, commit: str, path: str) -> bool:
    completed = run_git(root, "cat-file", "-e", f"{commit}:{path}", check=False)
    return completed.returncode == 0


def show(root: Path, commit: str, path: str) -> str:
    return git(root, "show", f"{commit}:{path}")


def field(text: str, prefix: str) -> str | None:
    for line in text.splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix).strip()
    return None


def immutable_body(text: str) -> str:
    lines = [line for line in text.splitlines() if not line.startswith(MUTABLE_ADR_FIELDS)]
    return "\n".join(lines).rstrip() + "\n"


def changed_paths(root: Path, base: str, head: str) -> set[str]:
    completed = subprocess.run(
        ["git", "-C", str(root), "diff", "--name-only", "-z", f"{base}...{head}"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        decoded = completed.stdout.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PacketError("UNSAFE_PATH", "changed Git paths are not valid UTF-8") from error
    return {normalize_repo_path(path, "changed Git path") for path in decoded.split("\x00") if path}


def _within(root: Path, path: str) -> bool:
    return (root / path).resolve(strict=False).is_relative_to(root)


def _allowed(path: str, patterns: list[str]) -> bool:
    return any(path == pattern or (pattern.endswith("/**") and path.startswith(pattern[:-2])) for pattern in patterns)


def _check(
    checks: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    check_id: str,
    passed: bool,
    message: str,
    **evidence: Any,
) -> None:
    entry = {"id": check_id, "state": PASS if passed else FAIL, "message": message, "evidence": evidence}
    checks.append(entry)
    if not passed:
        failures.append({"check_id": check_id, "message": message, "evidence": evidence})


def _has_symlink(root: Path, path: str) -> bool:
    current = root
    for part in PurePosixPath(path).parts:
        current /= part
        if current.is_symlink():
            return True
    return False


def _load_evidence_snapshot(root: Path, declaration: dict[str, Any]) -> tuple[dict[str, Any], str]:
    relative = declaration["path"]
    if not _within(root, relative) or _has_symlink(root, relative):
        _raise("UNSAFE_EVIDENCE_SNAPSHOT", "evidence snapshot must be a nonsymlink file inside the worktree")
    path = root / relative
    if not path.is_file():
        _raise("INVALID_EVIDENCE_SNAPSHOT", "evidence snapshot must be a regular file")
    if path.stat().st_size > MAX_SNAPSHOT_BYTES:
        _raise("EVIDENCE_SNAPSHOT_TOO_LARGE", "evidence snapshot exceeds the 20 MiB limit")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != declaration["sha256"]:
        _raise("EVIDENCE_SNAPSHOT_DRIFT", "evidence snapshot digest does not match the frozen packet")
    try:
        record = json.loads(raw.decode("utf-8"), object_pairs_hook=_json_without_duplicate_keys)
    except json.JSONDecodeError as error:
        raise PacketError("INVALID_EVIDENCE_SNAPSHOT", str(error)) from error
    except UnicodeDecodeError as error:
        raise PacketError("INVALID_EVIDENCE_SNAPSHOT", "evidence snapshot must be UTF-8") from error
    if not isinstance(record, dict) or record.get("schema_version") != 3:
        _raise("INVALID_EVIDENCE_SNAPSHOT", "evidence snapshot must be a version-3 team-run record")
    if record.get("run_id") != declaration["run_id"]:
        _raise("INVALID_EVIDENCE_SNAPSHOT", "evidence snapshot run_id does not match the frozen packet")
    try:
        team = team_validation.validate_team()
        team_validation.validate_record(record, team)
    except team_validation.ValidationError as error:
        raise PacketError("INVALID_EVIDENCE_SNAPSHOT", str(error)) from error
    if record["policy_bundle"]["digest"] != team["policy_bundle"]["digest"]:
        _raise("STALE_EVIDENCE_SNAPSHOT", "evidence snapshot policy bundle is not current")
    return record, digest


def _unique_entry(record: dict[str, Any], collection: str, source_id: str) -> dict[str, Any]:
    matches = [item for item in record[collection] if item["id"] == source_id]
    if len(matches) != 1:
        _raise(
            "EVIDENCE_SOURCE_MISSING",
            f"evidence source {source_id!r} does not resolve uniquely in {collection}",
        )
    return matches[0]


def _binding_digest(
    record: dict[str, Any], binding_id: str, reviewer_role: str
) -> tuple[str, bool]:
    matches = [item for item in record["bindings"] if item["binding_id"] == binding_id]
    if len(matches) != 1:
        return _canonical_digest({"missing_binding": binding_id}), False
    binding = matches[0]
    declarations = {
        item["role"]: item
        for item in [
            record["team_context"]["root"],
            *record["team_context"]["workflow_roles"],
        ]
    }
    qualified = (
        binding["requested_role"] == reviewer_role
        and binding["thread_id"] is not None
        and binding["permissions"]["value"] == ":read-only"
        and team_validation.binding_is_qualified(
            binding, declarations[reviewer_role]
        )
    )
    return _canonical_digest(binding), qualified


def _resolve_evidence(
    record: dict[str, Any],
    evidence: dict[str, Any],
    target: dict[str, Any],
    frozen_at: datetime,
) -> tuple[bool, dict[str, Any]]:
    source_kind = evidence["source_kind"]
    source_id = evidence["source_id"]
    target_id = target["target_id"]
    head_commit = target["head_commit"]
    artifacts = {item["id"]: item for item in record["artifacts"]}

    if source_kind == "CHECK":
        source = _unique_entry(record, "checks", source_id)
        source_artifacts = [artifacts[item_id] for item_id in source["artifact_ids"]]
        started_at = _timestamp(source["started_at"], f"check {source_id} started_at")
        ended_at = _timestamp(source["ended_at"], f"check {source_id} ended_at")
        artifact_bound = bool(source_artifacts) and all(
            item["target_id"] == target_id
            and item["ref"] == head_commit
            and item["environment_digest"] == source["environment"]["digest"]
            and _timestamp(item["built_at"], f"artifact {item['id']} built_at") <= started_at
            for item in source_artifacts
        )
        actual_command = source["command"]
        actual_artifact_ids = source["artifact_ids"]
        environment_digest = source["environment"]["digest"]
        invalidation_rule = source["invalidation_rule"]
        state = source["result"]
        evidence_state = source["validity"]
        source_passed = (
            source["target_id"] == target_id
            and source["ref"] == head_commit
            and state == PASS
            and evidence_state in PASSING_EVIDENCE_STATES
            and artifact_bound
            and ended_at <= frozen_at
        )
        completed_at = source["ended_at"]
    elif source_kind == "REVIEW":
        source = _unique_entry(record, "reviews", source_id)
        environment_digest, binding_qualified = _binding_digest(
            record, source["binding_id"], source["reviewer_role"]
        )
        actual_command = source["task"]
        actual_artifact_ids = [
            f"repository-ref:{source['ref']}",
            f"review-packet:{source['packet_digest']}",
        ]
        invalidation_rule = "target ref, review packet, reviewer binding, or policy bundle changes"
        state = source["result"]
        evidence_state = source["evidence_state"]
        source_passed = (
            source["target_id"] == target_id
            and source["ref"] == head_commit
            and state == PASS
            and evidence_state in PASSING_EVIDENCE_STATES
            and binding_qualified
            and not source["finding_ids"]
            and _timestamp(source["ended_at"], f"review {source_id} ended_at") <= frozen_at
        )
        completed_at = source["ended_at"]
    else:
        source = _unique_entry(record, "external_gates", source_id)
        external_by_id = {item["id"]: item for item in record["external_evidence"]}
        external = [external_by_id[item_id] for item_id in source["evidence_ids"]]
        actual_command = f"observe external gate {source['kind']}"
        actual_artifact_ids = sorted({f"external-head:{item['source_head']}" for item in external})
        environment_digest = _canonical_digest(
            sorted(
                ({"source_pr": item["source_pr"], "source_head": item["source_head"]} for item in external),
                key=lambda item: (item["source_pr"], item["source_head"]),
            )
        )
        invalidation_rule = "; ".join(sorted({item["invalidation_rule"] for item in external}))
        state = source["status"]
        evidence_state = (
            "FRESH"
            if external and all(item["evidence_state"] == "FRESH" for item in external)
            else "INHERITED"
            if external and all(item["evidence_state"] in PASSING_EVIDENCE_STATES for item in external)
            else "STALE"
        )
        checked_at = _timestamp(source["checked_at"], f"external gate {source_id} checked_at")
        source_passed = (
            source["target_id"] == target_id
            and source["ref"] == head_commit
            and state == "SATISFIED"
            and bool(external)
            and evidence_state in PASSING_EVIDENCE_STATES
            and all(
                item["target_id"] == target_id
                and item["source_head"] == head_commit
                and _timestamp(item["observed_at"], f"external evidence {item['id']} observed_at") <= checked_at
                for item in external
            )
            and checked_at <= frozen_at
        )
        completed_at = source["checked_at"]

    declaration_matches = (
        evidence["expected_command"] == actual_command
        and evidence["expected_artifact_ids"] == actual_artifact_ids
        and evidence["expected_environment_digest"] == environment_digest
        and evidence["expected_invalidation_rule"] == invalidation_rule
    )
    passed = source_passed and declaration_matches
    report = {
        "evidence_id": evidence["id"],
        "source_kind": source_kind,
        "source_id": source_id,
        "source_state": state,
        "evidence_state": evidence_state,
        "command_sha256": hashlib.sha256(actual_command.encode("utf-8")).hexdigest(),
        "artifact_ids": actual_artifact_ids,
        "environment_digest": environment_digest,
        "invalidation_rule_sha256": hashlib.sha256(invalidation_rule.encode("utf-8")).hexdigest(),
        "completed_at": completed_at,
        "source_passed": source_passed,
        "declaration_matches": declaration_matches,
    }
    return passed, report


def check_adrs(root: Path, base: str, head: str, changed: set[str]) -> list[tuple[str, str]]:
    failures: list[tuple[str, str]] = []
    for path in sorted(changed):
        if not ADR_PATTERN.fullmatch(path) or not object_exists(root, base, path):
            continue
        before = show(root, base, path)
        if field(before, "- Status:") != "accepted":
            continue
        if not object_exists(root, head, path):
            failures.append((path, "accepted ADR deleted"))
            continue
        after = show(root, head, path)
        after_status = field(after, "- Status:")
        superseded_by = field(after, "- Superseded by:")
        if immutable_body(before) != immutable_body(after):
            failures.append((path, "accepted ADR substance changed; supersede it instead"))
        if after_status not in {"accepted", "superseded"}:
            failures.append((path, f"accepted ADR has invalid transition to {after_status!r}"))
        if after_status == "accepted" and superseded_by not in {None, "none"}:
            failures.append((path, "accepted ADR names a successor without becoming superseded"))
        if after_status == "superseded" and superseded_by in {None, "none"}:
            failures.append((path, "superseded ADR lacks a successor"))
    return failures


def evaluate(packet: dict[str, Any], packet_sha256: str, invocation_root: Path) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    expected_root = Path(packet["worktree"]["root"])
    actual_root = Path(git(invocation_root, "rev-parse", "--show-toplevel").strip()).resolve()
    _check(
        checks,
        failures,
        "worktree.root",
        actual_root == expected_root,
        "worktree root matches frozen packet" if actual_root == expected_root else "worktree root does not match frozen packet",
        expected=str(expected_root),
        observed=str(actual_root),
    )
    root = actual_root
    actual_common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()).resolve()
    expected_common = Path(packet["repository"]["common_dir"])
    _check(
        checks,
        failures,
        "repository.common_dir",
        actual_common == expected_common,
        "repository common directory matches" if actual_common == expected_common else "repository common directory mismatch",
        expected=str(expected_common),
        observed=str(actual_common),
    )
    actual_origin = git(root, "remote", "get-url", "origin").strip()
    expected_origin = packet["repository"]["origin"]
    _check(
        checks,
        failures,
        "repository.origin",
        actual_origin == expected_origin,
        "origin matches" if actual_origin == expected_origin else "origin mismatch",
        expected=expected_origin,
        observed=actual_origin,
    )
    branch_process = run_git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    actual_branch = branch_process.stdout.strip() if branch_process.returncode == 0 else None
    expected_branch = packet["worktree"]["branch"]
    _check(
        checks,
        failures,
        "worktree.branch",
        actual_branch == expected_branch,
        "branch matches" if actual_branch == expected_branch else "branch mismatch or detached HEAD",
        expected=expected_branch,
        observed=actual_branch,
    )

    target = packet["target"]
    resolved = {
        "base_commit": resolve_git(root, target["base_ref"], "commit"),
        "base_tree": resolve_git(root, target["base_ref"], "tree"),
        "head_commit": resolve_git(root, target["head_ref"], "commit"),
        "head_tree": resolve_git(root, target["head_ref"], "tree"),
        "worktree_head_commit": resolve_git(root, "HEAD", "commit"),
        "worktree_head_tree": resolve_git(root, "HEAD", "tree"),
    }
    for name in ("base_commit", "base_tree", "head_commit", "head_tree"):
        expected = target[name]
        observed = resolved[name]
        _check(
            checks,
            failures,
            f"target.{name}",
            observed == expected,
            f"{name} matches frozen target" if observed == expected else f"{name} drifted from frozen target",
            expected=expected,
            observed=observed,
        )

    snapshot_record, snapshot_digest = _load_evidence_snapshot(root, packet["evidence_snapshot"])
    snapshot_targets = [
        item for item in snapshot_record["targets"] if item["target_id"] == target["target_id"]
    ]
    snapshot_target_matches = (
        len(snapshot_targets) == 1
        and snapshot_targets[0]["final_ref"] == target["head_commit"]
        and snapshot_record["repository"]["head_ref"] == target["head_commit"]
    )
    _check(
        checks,
        failures,
        "evidence_snapshot.target",
        snapshot_target_matches,
        "evidence snapshot is bound to the frozen target" if snapshot_target_matches else "evidence snapshot is off target",
        run_id=snapshot_record["run_id"],
        sha256=snapshot_digest,
        target_id=target["target_id"],
    )
    for name in ("commit", "tree"):
        observed = resolved[f"worktree_head_{name}"]
        expected = target[f"head_{name}"]
        _check(
            checks,
            failures,
            f"worktree.head_{name}",
            observed == expected,
            f"worktree HEAD {name} matches target" if observed == expected else f"worktree HEAD {name} is off target",
            expected=expected,
            observed=observed,
        )

    changed = changed_paths(root, target["base_commit"], target["head_commit"])
    unsafe = sorted(path for path in changed if not _within(root, path))
    for path in unsafe:
        _check(checks, failures, "changed_path.safe", False, "changed path resolves outside worktree", path=path)
    allowed = packet["changed_paths"]["allowed"]
    for path in sorted(changed):
        _check(
            checks,
            failures,
            "changed_path.allowed",
            _allowed(path, allowed),
            "changed path is within declared boundary" if _allowed(path, allowed) else "changed path is outside declared boundary",
            path=path,
        )
    for path in packet["changed_paths"]["required"]:
        _check(
            checks,
            failures,
            "changed_path.required",
            path in changed,
            "required path changed" if path in changed else "required path did not change",
            path=path,
        )

    adr_failures = check_adrs(root, target["base_commit"], target["head_commit"], changed)
    _check(
        checks,
        failures,
        "accepted_adr.immutable",
        not adr_failures,
        "accepted ADR transition policy satisfied" if not adr_failures else "accepted ADR transition policy failed",
        violations=[{"path": path, "message": message} for path, message in adr_failures],
    )

    obligations = packet["obligations"]
    for document in obligations["documentation"]:
        path = document["path"]
        exists = object_exists(root, target["head_commit"], path)
        _check(
            checks,
            failures,
            "documentation.changed",
            path in changed and exists,
            "required documentation changed and exists" if path in changed and exists else "required documentation must change and exist",
            path=path,
            changed=path in changed,
            exists=exists,
        )
        content = show(root, target["head_commit"], path) if exists else ""
        for claim in document["claims"]:
            present = claim["value"] in content if claim["type"] == "LITERAL" else re.search(claim["value"], content) is not None
            _check(
                checks,
                failures,
                "documentation.claim",
                present,
                "typed documentation claim is present" if present else "typed documentation claim is absent",
                path=path,
                claim_type=claim["type"],
                claim=claim["value"],
            )

    for name in ("migrations", "security", "changelog"):
        obligation = obligations[name]
        if obligation["state"] == "NOT_APPLICABLE":
            _check(
                checks,
                failures,
                f"obligation.{name}",
                True,
                f"{name} obligation declared not applicable",
                reason=obligation["reason"],
            )
            continue
        for path in obligation["paths"]:
            exists = object_exists(root, target["head_commit"], path)
            passed = path in changed and exists
            _check(
                checks,
                failures,
                f"obligation.{name}",
                passed,
                f"required {name} path changed and exists" if passed else f"required {name} path must change and exist",
                path=path,
                changed=path in changed,
                exists=exists,
            )

    for name in ("private_qualification", "linux_qualification"):
        obligation = obligations[name]
        if obligation["state"] == "NOT_APPLICABLE":
            _check(
                checks,
                failures,
                f"obligation.{name}",
                True,
                f"{name} declared not applicable",
                reason=obligation["reason"],
            )
            continue
        for evidence in obligation["evidence"]:
            passed, evidence_report = _resolve_evidence(
                snapshot_record,
                evidence,
                target,
                _timestamp(packet["frozen_at"], "packet.frozen_at"),
            )
            _check(
                checks,
                failures,
                f"obligation.{name}",
                passed,
                f"{name} evidence is passing and current" if passed else f"{name} evidence is not passing and current",
                **evidence_report,
            )

    for gate in obligations["gates"]:
        resolved_evidence = [
            _resolve_evidence(
                snapshot_record,
                item,
                target,
                _timestamp(packet["frozen_at"], "packet.frozen_at"),
            )
            for item in gate["evidence"]
        ]
        evidence_passed = all(passed for passed, _report in resolved_evidence)
        passed = gate["state"] == PASS and evidence_passed
        _check(
            checks,
            failures,
            f"gate.{gate['id']}",
            passed,
            "declared gate passed with passing evidence" if passed else "declared gate is not satisfied",
            declared_state=gate["state"],
            evidence_ids=[item["id"] for item in gate["evidence"]],
            resolved_evidence=[report for _passed, report in resolved_evidence],
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "packet": {
            "id": packet["packet_id"],
            "sha256": packet_sha256,
            "frozen_at": packet["frozen_at"],
        },
        "repository": {
            "common_dir": str(actual_common),
            "origin": actual_origin,
            "worktree": str(root),
            "branch": actual_branch,
        },
        "target": {
            "target_id": target["target_id"],
            "base_ref": target["base_ref"],
            "base_commit": resolved["base_commit"],
            "base_tree": resolved["base_tree"],
            "head_ref": target["head_ref"],
            "head_commit": resolved["head_commit"],
            "head_tree": resolved["head_tree"],
        },
        "evidence_snapshot": {
            "run_id": snapshot_record["run_id"],
            "sha256": snapshot_digest,
        },
        "changed_paths": sorted(changed),
        "checks": checks,
        "failures": failures,
        "result": PASS if not failures else FAIL,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--packet", required=True, help="frozen review-obligation packet JSON")
    return result


def error_report(code: str, message: str, *, packet_path: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "packet_path": packet_path,
        "result": ERROR,
        "error": {"code": code, "message": message},
    }


def main() -> int:
    args = parser().parse_args()
    packet_path = Path(args.packet)
    try:
        if packet_path.is_symlink():
            _raise("UNSAFE_PACKET", "packet path must not be a symbolic link")
        if packet_path.stat().st_size > MAX_PACKET_BYTES:
            _raise("PACKET_TOO_LARGE", "packet exceeds the 2 MiB limit")
        raw = packet_path.read_bytes()
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_json_without_duplicate_keys)
        packet = validate_packet(value)
        report = evaluate(packet, hashlib.sha256(raw).hexdigest(), Path.cwd())
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["result"] == PASS else 1
    except PacketError as error:
        print(json.dumps(error_report(error.code, str(error), packet_path=str(packet_path)), indent=2, sort_keys=True))
        return 2
    except json.JSONDecodeError as error:
        print(json.dumps(error_report("INVALID_JSON", str(error), packet_path=str(packet_path)), indent=2, sort_keys=True))
        return 2
    except UnicodeDecodeError:
        print(json.dumps(error_report("INVALID_ENCODING", "packet must be UTF-8", packet_path=str(packet_path)), indent=2, sort_keys=True))
        return 2
    except (OSError, subprocess.CalledProcessError) as error:
        message = str(error)
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            message = error.stderr.strip() or message
        print(json.dumps(error_report("ENVIRONMENT_ERROR", message, packet_path=str(packet_path)), indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
