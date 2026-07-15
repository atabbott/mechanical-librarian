#!/usr/bin/env python3
"""Freeze, verify, and receipt immutable context-isolated review packets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

import validate_team as team_validation


SCHEMA_VERSION = 2
RECEIPT_VERSION = 1
MAX_PACKET_BYTES = 20 * 1024 * 1024
PACKET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
GIT_OID = re.compile(r"^[0-9a-f]{40}$")
SAFE_RUN_REQUIREMENT = re.compile(r"^(issue|target|requirements?)-[A-Za-z0-9._-]+\.(md|txt|json)$")


class PacketError(ValueError):
    """A clean packet-contract failure."""


def fail(message: str) -> NoReturn:
    raise PacketError(message)


def exact_object(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        fail(f"{label} must have exactly {sorted(keys)}")
    return value


def text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or "\x00" in value:
        fail(f"{label} must be a nonempty normalized string")
    return value


def optional_text(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return text(value, label)


def strings(value: Any, label: str, *, nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        fail(f"{label} must be {'a nonempty ' if nonempty else 'an '}array")
    result = [text(item, f"{label}[{index}]") for index, item in enumerate(value)]
    if len(result) != len(set(result)):
        fail(f"{label} must not contain duplicates")
    return result


def safe_path(value: Any, label: str) -> str:
    raw = text(value, label)
    path = PurePosixPath(raw)
    if path.is_absolute() or path.as_posix() != raw or any(part in {"", ".", ".."} for part in path.parts):
        fail(f"{label} must be a safe repository-relative POSIX path")
    return raw


def duplicate_safe_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            fail(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def canonical_bytes(value: Any) -> bytes:
    return team_validation.canonical_json_bytes(value)


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def read_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file():
        fail(f"{label} must be a nonsymlink regular file")
    if path.stat().st_size > MAX_PACKET_BYTES:
        fail(f"{label} exceeds the 20 MiB limit")
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=duplicate_safe_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PacketError(str(error)) from error
    if not isinstance(value, dict):
        fail(f"{label} must contain one JSON object")
    return value, raw


def write_atomic(path: Path, content: bytes) -> None:
    if path.is_symlink():
        fail("output must not be a symbolic link")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def git(worktree: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(worktree), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        fail(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout


def repository_slug(worktree: Path) -> str:
    remote = git(worktree, "remote", "get-url", "origin").strip()
    match = re.search(r"github\.com(?::|/)([^/]+)/([^/]+?)(?:\.git)?$", remote)
    if match:
        return f"{match.group(1)}/{match.group(2)}"
    return worktree.name


def repo_blob(worktree: Path, mode: str, target_ref: str, source: str) -> str:
    spec = f":{source}" if mode == "STAGED" else f"{target_ref}:{source}"
    return git(worktree, "show", spec)


def frozen_blob(
    draft: Any,
    *,
    worktree: Path | None,
    repository_mode: str,
    target_ref: str,
    run_id: str,
) -> dict[str, Any]:
    item = exact_object(
        draft,
        {"id", "kind", "purpose", "source", "revision", "content"},
        "draft blob",
    )
    blob_id = text(item["id"], "blob id")
    kind = item["kind"]
    if kind not in {"INLINE", "REPO_BLOB", "RUN_BLOB"}:
        fail("draft blob kind must be INLINE, REPO_BLOB, or RUN_BLOB")
    purpose = text(item["purpose"], "blob purpose")
    source = text(item["source"], "blob source")
    revision = text(item["revision"], "blob revision")
    supplied = item["content"]
    if kind == "INLINE":
        if not isinstance(supplied, str):
            fail("INLINE blob requires text content")
        content = supplied
    else:
        if supplied is not None:
            fail(f"{kind} content must be derived rather than supplied")
        if worktree is None:
            fail(f"{kind} requires a Git worktree")
        source = safe_path(source, "blob source")
        if kind == "REPO_BLOB":
            if source.startswith(".codex/runs/"):
                fail("run artifacts must use the restricted RUN_BLOB kind")
            content = repo_blob(worktree, repository_mode, target_ref, source)
        else:
            prefix = f".codex/runs/{run_id}/"
            if not source.startswith(prefix) or not SAFE_RUN_REQUIREMENT.fullmatch(PurePosixPath(source).name):
                fail("RUN_BLOB must be a requirement artifact in the current run namespace")
            path = worktree / source
            if path.is_symlink() or not path.is_file():
                fail("RUN_BLOB source must be a nonsymlink regular file")
            content = path.read_text(encoding="utf-8")
    return {
        "id": blob_id,
        "kind": kind,
        "purpose": purpose,
        "source": source,
        "revision": revision,
        "sha256": sha256(content.encode("utf-8")),
        "content": content,
    }


def generated_blob(blob_id: str, kind: str, purpose: str, source: str, revision: str, content: str) -> dict[str, Any]:
    return {
        "id": blob_id,
        "kind": kind,
        "purpose": purpose,
        "source": source,
        "revision": revision,
        "sha256": sha256(content.encode("utf-8")),
        "content": content,
    }


def derive_repository(draft: dict[str, Any]) -> tuple[dict[str, Any], Path | None]:
    repository = exact_object(
        draft,
        {"mode", "repository_id", "worktree", "base_ref", "target_ref"},
        "draft repository",
    )
    mode = repository["mode"]
    if mode not in {"STAGED", "COMMIT", "NONE"}:
        fail("repository.mode must be STAGED, COMMIT, or NONE")
    repository_id = text(repository["repository_id"], "repository id")
    if mode == "NONE":
        if repository["worktree"] is not None or repository["base_ref"] is not None:
            fail("NONE repository mode must not name a worktree or base")
        target_ref = text(repository["target_ref"], "non-Git target ref")
        return {
            "mode": mode,
            "repository_id": repository_id,
            "worktree": None,
            "base_ref": None,
            "target_ref": target_ref,
            "tree": None,
            "changed_path_count": 0,
            "diff": None,
            "name_status": None,
        }, None

    worktree = Path(text(repository["worktree"], "repository worktree"))
    if not worktree.is_absolute() or worktree.resolve(strict=True) != worktree:
        fail("repository worktree must be an existing normalized absolute path")
    top = Path(git(worktree, "rev-parse", "--show-toplevel").strip()).resolve()
    if top != worktree:
        fail("repository worktree must name the Git worktree root")
    if repository_slug(worktree) != repository_id:
        fail("repository id differs from the worktree origin")
    base_ref = git(worktree, "rev-parse", f"{text(repository['base_ref'], 'base ref')}^{{commit}}").strip()
    if not GIT_OID.fullmatch(base_ref):
        fail("base ref did not resolve to a commit")
    if mode == "STAGED":
        if repository["target_ref"] is not None:
            fail("STAGED target_ref is derived and must be null in the draft")
        target_ref = git(worktree, "write-tree").strip()
        tree = target_ref
        diff_args = ("diff", "--cached", "--find-renames", base_ref, "--")
        names_args = ("diff", "--cached", "--name-status", base_ref, "--")
    else:
        target_ref = git(worktree, "rev-parse", f"{text(repository['target_ref'], 'target ref')}^{{commit}}").strip()
        tree = git(worktree, "rev-parse", f"{target_ref}^{{tree}}").strip()
        diff_args = ("diff", "--find-renames", base_ref, target_ref, "--")
        names_args = ("diff", "--name-status", base_ref, target_ref, "--")
    diff = git(worktree, *diff_args)
    name_status = git(worktree, *names_args)
    count = len([line for line in name_status.splitlines() if line])
    if count == 0:
        fail("Git review packet must contain at least one changed path")
    return {
        "mode": mode,
        "repository_id": repository_id,
        "worktree": str(worktree),
        "base_ref": base_ref,
        "target_ref": target_ref,
        "tree": tree,
        "changed_path_count": count,
        "diff": generated_blob("repository-diff", "DIFF", "DIFF", "git-diff", f"{base_ref}..{target_ref}", diff),
        "name_status": generated_blob("repository-name-status", "NAME_STATUS", "NAME_STATUS", "git-name-status", f"{base_ref}..{target_ref}", name_status),
    }, worktree


def freeze_packet(draft: dict[str, Any]) -> dict[str, Any]:
    draft = exact_object(
        draft,
        {
            "schema_version", "packet_id", "workflow", "target", "repository", "role",
            "binding_request", "task", "artifacts", "lineage", "exclusions",
        },
        "draft packet",
    )
    if draft["schema_version"] != SCHEMA_VERSION:
        fail(f"schema_version must be {SCHEMA_VERSION}")
    packet_id = text(draft["packet_id"], "packet id")
    if not PACKET_ID.fullmatch(packet_id):
        fail("packet id contains unsupported characters")
    workflow = exact_object(
        draft["workflow"], {"run_id", "workflow", "target_id", "stage", "context_mode"}, "workflow"
    )
    for field in ("run_id", "workflow", "target_id", "stage"):
        text(workflow[field], f"workflow {field}")
    if workflow["context_mode"] not in team_validation.REVIEW_CONTEXT_MODES:
        fail("invalid review context mode")
    repository, worktree = derive_repository(draft["repository"])

    target = exact_object(draft["target"], {"kind", "id", "revision", "url", "requirements"}, "target")
    for field in ("kind", "id", "revision"):
        text(target[field], f"target {field}")
    optional_text(target["url"], "target url")
    if target["revision"] != repository["target_ref"]:
        fail("target revision differs from the frozen repository/artifact ref")
    if not isinstance(target["requirements"], list) or not target["requirements"]:
        fail("target requirements must be a nonempty array")
    requirements = [
        frozen_blob(item, worktree=worktree, repository_mode=repository["mode"], target_ref=repository["target_ref"], run_id=workflow["run_id"])
        for item in target["requirements"]
    ]
    if any(item["purpose"] != "REQUIREMENT" for item in requirements):
        fail("every target requirement blob must use purpose REQUIREMENT")
    if any(item["revision"] != target["revision"] for item in requirements):
        fail("target requirement revision differs from the frozen target")

    role_draft = exact_object(draft["role"], {"requested_role"}, "role")
    requested_role = text(role_draft["requested_role"], "requested role")
    team = team_validation.validate_team()
    manifest = team_validation.load_toml(team_validation.MANIFEST)
    if requested_role not in team["roles"] or manifest["roles"][requested_role]["kind"] != "reviewer":
        fail("requested role is not a configured reviewer")
    declared = team["roles"][requested_role]
    config_path = f".codex/{manifest['roles'][requested_role]['config']}"
    control_revision = team["policy_bundle"]["digest"]
    if worktree is None:
        config_content = (team_validation.CODEX.parent / config_path).read_text(
            encoding="utf-8"
        )
        config_kind = "INLINE"
        config_revision = control_revision
    else:
        config_content = repo_blob(
            worktree, repository["mode"], repository["target_ref"], config_path
        )
        config_kind = "REPO_BLOB"
        config_revision = repository["target_ref"]
    config = generated_blob(
        "role-config",
        config_kind,
        "ROLE_CONFIG",
        config_path,
        config_revision,
        config_content,
    )
    policy_paths = sorted(set(manifest["roles"][requested_role]["required_policies"]) | {".codex/binding-preflight.md"})
    policies = []
    for index, path in enumerate(policy_paths, 1):
        if worktree is None:
            content = (team_validation.CODEX.parent / path).read_text(
                encoding="utf-8"
            )
            kind = "INLINE"
            revision = control_revision
        else:
            content = repo_blob(
                worktree, repository["mode"], repository["target_ref"], path
            )
            kind = "REPO_BLOB"
            revision = repository["target_ref"]
        policies.append(
            generated_blob(
                f"policy-{index}", kind, "POLICY", path, revision, content
            )
        )

    binding = exact_object(
        draft["binding_request"],
        {"binding_id", "originating_review_id", "originating_binding_id", "originating_thread_id"},
        "binding request",
    )
    text(binding["binding_id"], "binding id")
    for field in ("originating_review_id", "originating_binding_id", "originating_thread_id"):
        optional_text(binding[field], field)
    context_mode = workflow["context_mode"]
    origins = [binding[field] for field in ("originating_review_id", "originating_binding_id", "originating_thread_id")]
    if context_mode == "FRESH_ISOLATED" and any(item is not None for item in origins):
        fail("fresh review packet must not inherit lineage")
    if context_mode != "FRESH_ISOLATED" and any(item is None for item in origins):
        fail("continuation/replacement packet requires complete originating lineage")

    task = exact_object(draft["task"], {"text", "lens", "output_contract"}, "task")
    for field in task:
        text(task[field], f"task {field}")
    if not isinstance(draft["artifacts"], list):
        fail("artifacts must be an array")
    artifacts = [
        frozen_blob(item, worktree=worktree, repository_mode=repository["mode"], target_ref=repository["target_ref"], run_id=workflow["run_id"])
        for item in draft["artifacts"]
    ]
    if any(item["revision"] != target["revision"] for item in artifacts):
        fail("review artifact revision differs from the frozen target")
    lineage = exact_object(draft["lineage"], {"finding_ids", "repair_evidence"}, "lineage")
    finding_ids = strings(lineage["finding_ids"], "finding lineage")
    if not isinstance(lineage["repair_evidence"], list):
        fail("repair evidence must be an array")
    repair_evidence = [
        frozen_blob(item, worktree=worktree, repository_mode=repository["mode"], target_ref=repository["target_ref"], run_id=workflow["run_id"])
        for item in lineage["repair_evidence"]
    ]
    if any(item["purpose"] != "REPAIR" for item in repair_evidence):
        fail("repair evidence blobs must use purpose REPAIR")
    if any(item["revision"] != target["revision"] for item in repair_evidence):
        fail("repair evidence revision differs from the resulting target")
    if context_mode == "FRESH_ISOLATED" and (finding_ids or repair_evidence):
        fail("fresh review packet must not contain remediation lineage")
    if context_mode != "FRESH_ISOLATED" and (not finding_ids or not repair_evidence):
        fail("continuation/replacement packet requires bounded finding and repair evidence")

    exclusions = exact_object(
        draft["exclusions"],
        {
            "worker_or_orchestrator_conclusions", "prior_findings_or_verdicts", "family_dispositions",
            "unrelated_remediation_history", "iteration_framing", "named_green_tests",
            "review_method", "family_policy", "review_synthesis",
        },
        "exclusions",
    )
    mandatory = {
        "worker_or_orchestrator_conclusions", "prior_findings_or_verdicts", "family_dispositions",
        "unrelated_remediation_history", "iteration_framing", "named_green_tests", "review_synthesis",
    }
    if any(exclusions[name] is not True for name in mandatory):
        fail("review packet exclusion declaration is incomplete")
    if requested_role == team_validation.PUBLICATION_CHALLENGE_ROLE:
        if exclusions["review_method"] is not True or exclusions["family_policy"] is not True:
            fail("publication challenge must exclude shared review/family methods")
    all_blobs = [*requirements, config, *policies, repository["diff"], repository["name_status"], *artifacts, *repair_evidence]
    ids = [item["id"] for item in all_blobs if item is not None]
    if len(ids) != len(set(ids)):
        fail("packet blob IDs must be globally unique")

    return {
        "schema_version": SCHEMA_VERSION,
        "packet_id": packet_id,
        "workflow": workflow,
        "target": {**target, "requirements": requirements},
        "repository": repository,
        "role": {
            "requested_role": requested_role,
            "config": config,
            "model": declared["model"],
            "reasoning": declared["reasoning"],
            "permissions": declared["permissions"],
            "required_capabilities": declared["capabilities"],
            "forbidden_capabilities": declared["forbidden_capabilities"],
            "policies": policies,
        },
        "binding_request": {
            "binding_id": binding["binding_id"],
            "allowed_actual_roles": [requested_role, "isolated_cli_reviewer"],
            "launch": {
                "ignore_user_config": True,
                "model": declared["model"],
                "reasoning": declared["reasoning"],
                "approval_policy": "never",
                "sandbox": "read-only",
            },
            "originating_review_id": binding["originating_review_id"],
            "originating_binding_id": binding["originating_binding_id"],
            "originating_thread_id": binding["originating_thread_id"],
        },
        "task": task,
        "artifacts": artifacts,
        "lineage": {"finding_ids": finding_ids, "repair_evidence": repair_evidence},
        "exclusions": exclusions,
    }


def verify_live(packet: dict[str, Any]) -> None:
    try:
        repository = packet["repository"]
        team_validation.validate_review_packet_live(
            packet,
            trusted_worktree=(
                None
                if repository["mode"] == "NONE"
                else Path(repository["worktree"])
            ),
        )
    except team_validation.ValidationError as error:
        raise PacketError(str(error)) from error


def verify_packet(path: Path, expected_digest: str | None) -> tuple[dict[str, Any], str]:
    packet, raw = read_json(path, "packet")
    if raw != canonical_bytes(packet):
        fail("packet bytes are not canonical sorted compact JSON")
    digest = sha256(raw)
    if expected_digest is not None and (not SHA256.fullmatch(expected_digest) or digest != expected_digest):
        fail("packet digest does not match")
    try:
        team_validation.validate_review_packet_snapshot(raw.decode("utf-8"), digest)
    except team_validation.ValidationError as error:
        raise PacketError(str(error)) from error
    verify_live(packet)
    return packet, digest


def create_receipt(args: argparse.Namespace) -> tuple[dict[str, Any], str]:
    packet, digest = verify_packet(args.packet, args.digest)
    role = packet["role"]
    binding = packet["binding_request"]
    actual_role = text(args.actual_role, "actual role")
    if actual_role not in binding["allowed_actual_roles"]:
        fail("actual role is outside the frozen fallback contract")
    if args.model != role["model"]:
        fail("runtime model differs from the frozen reviewer model")
    rank = {"medium": 1, "high": 2}
    if args.reasoning not in rank or rank[args.reasoning] < rank[role["reasoning"]]:
        fail("runtime reasoning is weaker than the frozen reviewer effort")
    if args.permissions != ":read-only":
        fail("runtime reviewer permissions are not read-only")
    verified = sorted(set(args.verified_capability))
    absent = sorted(set(args.absent_capability))
    if not set(role["required_capabilities"]) <= set(verified):
        fail("runtime evidence lacks a required reviewer capability")
    if not set(role["forbidden_capabilities"]) <= set(absent):
        fail("runtime evidence does not disprove forbidden reviewer authority")
    thread_id = text(args.thread_id, "runtime thread id")
    receipt = {
        "schema_version": RECEIPT_VERSION,
        "packet_id": packet["packet_id"],
        "packet_digest": digest,
        "binding_id": binding["binding_id"],
        "requested_role": role["requested_role"],
        "thread_id": thread_id,
        "role": {
            "value": actual_role,
            "state": "VERIFIED" if actual_role == role["requested_role"] else "FALLBACK",
            "provenance": text(args.role_provenance, "role provenance"),
        },
        "model": {
            "value": args.model,
            "state": args.model_state,
            "provenance": text(args.model_provenance, "model provenance"),
        },
        "reasoning": {
            "value": args.reasoning,
            "state": args.reasoning_state,
            "provenance": text(args.reasoning_provenance, "reasoning provenance"),
        },
        "permissions": {
            "value": args.permissions,
            "state": "VERIFIED",
            "provenance": text(args.permissions_provenance, "permissions provenance"),
        },
        "capability_evidence": {
            "verified": verified,
            "absent": absent,
            "provenance": text(args.capability_provenance, "capability provenance"),
        },
        "runtime_evidence": strings(args.runtime_evidence, "runtime evidence", nonempty=True),
    }
    content = canonical_bytes(receipt)
    write_atomic(args.output, content)
    return receipt, sha256(content)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze")
    freeze.add_argument("--draft", type=Path, required=True)
    freeze.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--packet", type=Path, required=True)
    verify.add_argument("--digest")
    receipt = commands.add_parser("receipt")
    receipt.add_argument("--packet", type=Path, required=True)
    receipt.add_argument("--digest", required=True)
    receipt.add_argument("--output", type=Path, required=True)
    receipt.add_argument("--actual-role", required=True)
    receipt.add_argument("--thread-id", required=True)
    receipt.add_argument("--model", required=True)
    receipt.add_argument("--reasoning", choices=["medium", "high"], required=True)
    receipt.add_argument("--permissions", required=True)
    receipt.add_argument("--model-state", choices=["VERIFIED", "FALLBACK"], required=True)
    receipt.add_argument("--reasoning-state", choices=["VERIFIED", "FALLBACK"], required=True)
    receipt.add_argument("--role-provenance", required=True)
    receipt.add_argument("--model-provenance", required=True)
    receipt.add_argument("--reasoning-provenance", required=True)
    receipt.add_argument("--permissions-provenance", required=True)
    receipt.add_argument("--verified-capability", action="append", default=[])
    receipt.add_argument("--absent-capability", action="append", default=[])
    receipt.add_argument("--capability-provenance", required=True)
    receipt.add_argument("--runtime-evidence", action="append", default=[])
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "freeze":
            draft, _raw = read_json(args.draft, "draft")
            packet = freeze_packet(draft)
            content = canonical_bytes(packet)
            if len(content) > MAX_PACKET_BYTES:
                fail("canonical packet exceeds the 20 MiB limit")
            team_validation.validate_review_packet_snapshot(
                content.decode("utf-8"), sha256(content)
            )
            write_atomic(args.output, content)
            digest = sha256(content)
            identifier = packet["packet_id"]
        elif args.command == "verify":
            packet, digest = verify_packet(args.packet, args.digest)
            identifier = packet["packet_id"]
        else:
            receipt, digest = create_receipt(args)
            identifier = receipt["binding_id"]
        print(json.dumps({"result": "PASS", "id": identifier, "sha256": digest}, sort_keys=True))
        return 0
    except (OSError, PacketError, team_validation.ValidationError) as error:
        print(json.dumps({"result": "ERROR", "message": str(error)}, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
