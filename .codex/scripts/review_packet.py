#!/usr/bin/env python3
"""Create and verify a small canonical packet for an independent review."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any


SHA = re.compile(r"^[0-9a-f]{40}$")


class PacketError(ValueError):
    pass


def fail(message: str) -> None:
    raise PacketError(message)


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments], cwd=repo, text=True, capture_output=True, check=False
    )
    if result.returncode:
        fail(result.stderr.strip() or f"git {' '.join(arguments)} failed")
    return result.stdout


def repository_root(value: Path) -> Path:
    root = value.resolve(strict=True)
    top = Path(git(root, "rev-parse", "--show-toplevel").strip()).resolve()
    if root != top:
        fail("--repo must name the Git worktree root")
    return root


def safe_path(repo: Path, value: str) -> Path:
    logical = PurePosixPath(value)
    if logical.is_absolute() or not logical.parts or ".." in logical.parts:
        fail(f"unsafe requirement path: {value}")
    path = repo.joinpath(*logical.parts)
    if path.is_symlink() or not path.is_file() or repo not in path.resolve().parents:
        fail(f"requirement is not a regular worktree file: {value}")
    return path


def changed_paths(name_status: str) -> list[str]:
    paths: list[str] = []
    for line in name_status.splitlines():
        fields = line.split("\t")
        if len(fields) < 2:
            fail("invalid Git name-status output")
        path = fields[-1]
        safe = PurePosixPath(path)
        if safe.is_absolute() or ".." in safe.parts:
            fail("unsafe changed path")
        paths.append(path)
    return sorted(set(paths))


def repository_evidence(repo: Path, mode: str, base_value: str, target_value: str | None) -> dict[str, Any]:
    base = git(repo, "rev-parse", f"{base_value}^{{commit}}").strip()
    if not SHA.fullmatch(base):
        fail("base did not resolve to a commit")
    if mode == "STAGED":
        if target_value is not None:
            fail("STAGED mode does not accept --target")
        target = None
        diff = git(repo, "diff", "--cached", "--no-ext-diff", "--no-color", "--find-renames", base, "--")
        names = git(repo, "diff", "--cached", "--no-ext-diff", "--name-status", base, "--")
    else:
        if target_value is None:
            fail("COMMIT mode requires --target")
        target = git(repo, "rev-parse", f"{target_value}^{{commit}}").strip()
        if not SHA.fullmatch(target):
            fail("target did not resolve to a commit")
        diff = git(repo, "diff", "--no-ext-diff", "--no-color", "--find-renames", base, target, "--")
        names = git(repo, "diff", "--no-ext-diff", "--name-status", base, target, "--")
    paths = changed_paths(names)
    if not paths:
        fail("review packet target has no changed paths")
    return {
        "mode": mode,
        "base": base,
        "target": target,
        "diff": diff,
        "diff_sha256": digest(diff.encode()),
        "name_status": names,
        "name_status_sha256": digest(names.encode()),
        "changed_paths": paths,
    }


def validate_shape(packet: Any) -> dict[str, Any]:
    if not isinstance(packet, dict) or set(packet) != {
        "schema_version", "workflow", "task", "lens", "repository", "requirements", "exclusions"
    }:
        fail("invalid packet keys")
    if packet["schema_version"] != 1:
        fail("unsupported packet schema")
    for field in ("workflow", "task", "lens"):
        if not isinstance(packet[field], str) or not packet[field].strip():
            fail(f"packet {field} is required")
    repository = packet["repository"]
    expected_repository = {
        "mode", "base", "target", "diff", "diff_sha256", "name_status",
        "name_status_sha256", "changed_paths",
    }
    if not isinstance(repository, dict) or set(repository) != expected_repository:
        fail("invalid repository evidence")
    if repository["mode"] not in {"STAGED", "COMMIT"} or not SHA.fullmatch(repository["base"]):
        fail("invalid repository mode/base")
    if repository["mode"] == "COMMIT":
        if not isinstance(repository["target"], str) or not SHA.fullmatch(repository["target"]):
            fail("invalid committed target")
    elif repository["target"] is not None:
        fail("staged packet unexpectedly names a target")
    for field in ("diff", "diff_sha256", "name_status", "name_status_sha256"):
        if not isinstance(repository[field], str):
            fail(f"invalid repository {field}")
    if not isinstance(repository["changed_paths"], list):
        fail("invalid repository changed paths")
    if digest(repository["diff"].encode()) != repository["diff_sha256"]:
        fail("packet diff digest mismatch")
    if digest(repository["name_status"].encode()) != repository["name_status_sha256"]:
        fail("packet name-status digest mismatch")
    if repository["changed_paths"] != changed_paths(repository["name_status"]):
        fail("packet changed paths differ from name-status")
    if not isinstance(packet["requirements"], list):
        fail("requirements must be a list")
    for item in packet["requirements"]:
        if not isinstance(item, dict) or set(item) != {"path", "content", "sha256"}:
            fail("invalid packet requirement")
        if not all(isinstance(item[field], str) for field in ("path", "content", "sha256")):
            fail("invalid packet requirement fields")
        if digest(item["content"].encode()) != item["sha256"]:
            fail("requirement digest mismatch")
    expected_exclusions = [
        "worker_conclusions", "prior_verdicts", "remediation_history", "named_green_tests"
    ]
    if packet["exclusions"] != expected_exclusions:
        fail("packet exclusions are incomplete")
    return packet


def create(args: argparse.Namespace) -> tuple[dict[str, Any], str]:
    repo = repository_root(args.repo)
    repository = repository_evidence(repo, args.mode, args.base, args.target)
    requirements = []
    for value in sorted(set(args.requirement)):
        path = safe_path(repo, value)
        content = path.read_text(encoding="utf-8")
        requirements.append({"path": value, "content": content, "sha256": digest(content.encode())})
    packet = validate_shape(
        {
            "schema_version": 1,
            "workflow": args.workflow,
            "task": args.task,
            "lens": args.lens,
            "repository": repository,
            "requirements": requirements,
            "exclusions": [
                "worker_conclusions", "prior_verdicts", "remediation_history", "named_green_tests"
            ],
        }
    )
    raw = canonical(packet)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{args.output.name}.", dir=args.output.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
        os.replace(temporary, args.output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return packet, digest(raw)


def verify(path: Path, repo_value: Path, expected_digest: str | None) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    try:
        packet = json.loads(raw)
    except json.JSONDecodeError as error:
        raise PacketError(f"invalid packet JSON: {error}") from error
    validate_shape(packet)
    if raw != canonical(packet):
        fail("packet bytes are not canonical")
    actual_digest = digest(raw)
    if expected_digest is not None and actual_digest != expected_digest:
        fail("packet digest mismatch")
    repo = repository_root(repo_value)
    recorded = packet["repository"]
    live = repository_evidence(repo, recorded["mode"], recorded["base"], recorded["target"])
    if live != recorded:
        fail("packet repository evidence drifted")
    for item in packet["requirements"]:
        content = safe_path(repo, item["path"]).read_text(encoding="utf-8")
        if content != item["content"] or digest(content.encode()) != item["sha256"]:
            fail(f"requirement drifted: {item['path']}")
    return packet, actual_digest


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    subparsers = result.add_subparsers(dest="command", required=True)
    create_parser = subparsers.add_parser("create")
    create_parser.add_argument("--repo", type=Path, default=Path.cwd())
    create_parser.add_argument("--mode", choices=["STAGED", "COMMIT"], required=True)
    create_parser.add_argument("--base", required=True)
    create_parser.add_argument("--target")
    create_parser.add_argument("--workflow", required=True)
    create_parser.add_argument("--task", required=True)
    create_parser.add_argument("--lens", required=True)
    create_parser.add_argument("--requirement", action="append", default=[])
    create_parser.add_argument("--output", type=Path, required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--repo", type=Path, default=Path.cwd())
    verify_parser.add_argument("--packet", type=Path, required=True)
    verify_parser.add_argument("--digest")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "create":
            _, value = create(args)
        else:
            _, value = verify(args.packet, args.repo, args.digest)
    except (OSError, UnicodeError, PacketError) as error:
        print(f"FAIL: {error}", file=__import__("sys").stderr)
        return 1
    print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
