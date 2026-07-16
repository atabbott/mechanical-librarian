#!/usr/bin/env python3
"""Fail-closed exact-head pull-request merge controller."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from typing import Any, Protocol, Sequence


MAX_ARTIFACT_BYTES = 1_048_576
_SHA = re.compile(r"[0-9a-f]{40}")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/ -]{0,127}")
_SLICE_REVIEW_TASKS = frozenset({"slice_acceptance", "code_quality"})
_TIER_TWO_REVIEW_TASKS = frozenset({"safety", "publication_challenge"})
_PRE_MERGE_CHECKS = frozenset({"public-gate", "linked-issue"})
_POST_MAIN_CHECKS = frozenset({"public-gate"})
_TRUSTED_CONTROL_PATHS = {
    "review_validator": Path(".codex/scripts/review_packet.py"),
    "run_record_validator": Path(".codex/scripts/run_record.py"),
    "qualification_validator": Path(".codex/scripts/qualification_cache.py"),
    "workflow_manifest": Path(".codex/team.toml"),
}


class MergeGateError(RuntimeError):
    """A pre-mutation merge invariant was absent, stale, or unsuccessful."""


class MutationRecoveryError(MergeGateError):
    """A merge may have occurred and requires explicit recovery."""

    def __init__(
        self,
        phase: str,
        mutation_occurred: bool | str,
        message: str,
        *,
        merge_sha: str | None = None,
        known_state: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.phase = phase
        self.merge_sha = merge_sha
        self.mutation_occurred = mutation_occurred
        self.known_state = {} if known_state is None else known_state


class PostMutationError(MutationRecoveryError):
    """A recoverable verification failed after GitHub confirmed the merge."""

    def __init__(self, phase: str, merge_sha: str, message: str) -> None:
        super().__init__(phase, True, message, merge_sha=merge_sha)


class ServerEnforcementBlocked(MergeGateError):
    """Strict server enforcement is unavailable before mutation."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str = ""


class CommandRunner(Protocol):
    def run(self, argv: Sequence[str], *, check: bool = True) -> CommandResult: ...


class Sleeper(Protocol):
    def sleep(self, seconds: float) -> None: ...


class SubprocessRunner:
    def run(self, argv: Sequence[str], *, check: bool = True) -> CommandResult:
        try:
            completed = subprocess.run(
                tuple(argv), check=False, capture_output=True, text=True, timeout=60
            )
        except (OSError, subprocess.SubprocessError, UnicodeError):
            raise MergeGateError(f"command could not complete: {argv[0]}") from None
        result = CommandResult(completed.returncode, completed.stdout, completed.stderr)
        if check and result.returncode != 0:
            raise MergeGateError(f"command failed: {argv[0]}")
        return result


class TimeSleeper:
    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


@dataclass(frozen=True)
class MergeRequest:
    repository: str
    pull_request: int
    base_ref: str
    base_sha: str
    head_sha: str
    review_repo: Path
    review_evidence: Path
    review_envelopes: tuple[tuple[str, Path], ...]
    run_record: Path
    qualification_evidence: tuple[tuple[str, Path, Path], ...]
    post_merge_attempts: int
    post_merge_interval: float
    dry_run: bool


def _json(raw: str, context: str, expected: type = dict) -> Any:
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, RecursionError):
        raise MergeGateError(f"{context} returned invalid JSON") from None
    if type(value) is not expected:
        raise MergeGateError(f"{context} returned the wrong JSON shape")
    return value


def _validate_request(request: MergeRequest) -> None:
    if _REPOSITORY.fullmatch(request.repository) is None:
        raise MergeGateError("repository must be OWNER/NAME")
    if request.pull_request < 1 or request.base_ref != "main":
        raise MergeGateError("pull request and main base are required")
    if _SHA.fullmatch(request.base_sha) is None or _SHA.fullmatch(request.head_sha) is None:
        raise MergeGateError("base and head must be lowercase full commit SHAs")
    if request.post_merge_attempts < 1 or not 0 <= request.post_merge_interval <= 300:
        raise MergeGateError("post-merge wait bounds are invalid")
    try:
        review_repo = request.review_repo.resolve(strict=True)
    except OSError:
        raise MergeGateError("review repository does not exist") from None
    if not review_repo.is_dir():
        raise MergeGateError("review repository is not a directory")
    envelope_names = [name for name, _ in request.review_envelopes]
    if (
        not envelope_names
        or any(_NAME.fullmatch(name) is None for name in envelope_names)
        or len(envelope_names) != len(set(envelope_names))
    ):
        raise MergeGateError("review envelope names must be valid and unique")
    qualification_names = [name for name, _, _ in request.qualification_evidence]
    if len(qualification_names) != len(set(qualification_names)):
        raise MergeGateError("qualification evidence names must be unique")


def _artifact(path: Path, label: str) -> tuple[bytes, str]:
    try:
        metadata = path.lstat()
        path.resolve(strict=True)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
        ):
            raise MergeGateError(f"{label} must be a non-symlink regular file")
        size = metadata.st_size
        if size > MAX_ARTIFACT_BYTES:
            raise MergeGateError(f"{label} exceeds the size limit")
        raw = path.read_bytes()
    except (OSError, UnicodeError):
        raise MergeGateError(f"{label} could not be read") from None
    return raw, hashlib.sha256(raw).hexdigest()


def _trusted_controls(review_repo: Path) -> dict[str, Path]:
    controls: dict[str, Path] = {}
    for name, relative in _TRUSTED_CONTROL_PATHS.items():
        candidate = review_repo / relative
        try:
            metadata = candidate.lstat()
            resolved = candidate.resolve(strict=True)
        except OSError:
            raise MergeGateError(f"trusted control is unavailable: {relative}") from None
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or resolved != candidate.absolute()
            or review_repo not in resolved.parents
        ):
            raise MergeGateError(f"trusted control is unsafe: {relative}")
        controls[name] = resolved
    return controls


def _seal(
    seals: list[tuple[Path, str, str]], path: Path, label: str
) -> tuple[bytes, str]:
    raw, digest = _artifact(path, label)
    seals.append((path, label, digest))
    return raw, digest


def _verify_seals(seals: list[tuple[Path, str, str]]) -> None:
    for path, label, expected in seals:
        _, actual = _artifact(path, label)
        if actual != expected:
            raise MergeGateError(f"{label} changed during validation")


def _mandatory_review_tasks(record: dict[str, Any]) -> frozenset[str]:
    if record.get("workflow") != "deliver-functional-slice":
        raise MergeGateError("qualified run record uses the wrong workflow")
    tier = record.get("tier")
    if type(tier) is not int or tier not in {0, 1, 2}:
        raise MergeGateError("qualified run record has an invalid tier")
    tasks = set(_SLICE_REVIEW_TASKS)
    if tier == 2:
        tasks.update(_TIER_TWO_REVIEW_TASKS)
    return frozenset(tasks)


def _validate_local_evidence(
    request: MergeRequest, runner: CommandRunner
) -> dict[str, str]:
    review_repo = request.review_repo.resolve(strict=True)
    digests: dict[str, str] = {}
    seals: list[tuple[Path, str, str]] = []
    controls = _trusted_controls(review_repo)
    for name, path in controls.items():
        _, digest = _seal(seals, path, f"trusted control {name}")
        digests[f"control:{name}"] = digest
    try:
        record_raw, record_digest = _seal(
            seals, request.run_record, "qualified run record"
        )
        try:
            record = _json(
                record_raw.decode("utf-8"), "qualified run record"
            )
        except UnicodeError:
            raise MergeGateError(
                "qualified run record is not valid UTF-8"
            ) from None
        target = record.get("target")
        delivery = record.get("delivery")
        expected_url = (
            f"https://github.com/{request.repository}/pull/{request.pull_request}"
        )
        if (
            record.get("schema_version") != 2
            or type(target) is not dict
            or target.get("base_commit") != request.base_sha
            or target.get("final_commit") != request.head_sha
            or type(delivery) is not dict
            or delivery.get("kind") != "PULL_REQUEST"
            or delivery.get("url") != expected_url
        ):
            raise MergeGateError("qualified run record does not bind this merge")
        qualification_digests: dict[str, str] = {}
        command = [
            sys.executable,
            str(controls["run_record_validator"]),
            "validate",
            str(request.run_record),
            "--qualified",
            "--repo",
            ".",
        ]
        for name, receipt, cache_record in request.qualification_evidence:
            _, receipt_digest = _seal(
                seals, receipt, f"{name} qualification receipt"
            )
            _, cache_digest = _seal(
                seals, cache_record, f"{name} qualification cache"
            )
            qualification_digests[
                f"qualification-receipt:{name}"
            ] = receipt_digest
            qualification_digests[f"qualification-cache:{name}"] = cache_digest
            command.extend(
                ("--qualification-evidence", name, str(receipt), str(cache_record))
            )
        runner.run(tuple(command))

        mandatory_tasks = _mandatory_review_tasks(record)
        envelope_names = {name for name, _ in request.review_envelopes}
        if envelope_names != mandatory_tasks:
            raise MergeGateError(
                "review envelope names must exactly match mandatory workflow reviews"
            )

        envelope_digests: dict[str, str] = {}
        _, evidence_digest = _seal(
            seals, request.review_evidence, "review evidence"
        )
        digests["review_evidence"] = evidence_digest
        runner.run((
            sys.executable,
            str(controls["review_validator"]),
            "verify-evidence",
            "--repo",
            str(review_repo),
            "--evidence",
            str(request.review_evidence),
            "--digest",
            evidence_digest,
        ))
        for name, path in request.review_envelopes:
            raw, digest = _seal(seals, path, f"{name} review envelope")
            runner.run((
                sys.executable,
                str(controls["review_validator"]),
                "verify-envelope",
                "--repo",
                str(review_repo),
                "--evidence",
                str(request.review_evidence),
                "--envelope",
                str(path),
                "--digest",
                digest,
            ))
            try:
                envelope = _json(
                    raw.decode("utf-8"), f"{name} review envelope"
                )
            except UnicodeError:
                raise MergeGateError(
                    f"{name} review envelope is not valid UTF-8"
                ) from None
            if envelope.get("task") != name:
                raise MergeGateError(f"review envelope task does not match: {name}")
            digests[f"review:{name}"] = digest
            envelope_digests[name] = digest
        epochs = record.get("review_epochs")
        if type(epochs) is not list:
            raise MergeGateError("qualified run record lacks review epochs")
        reviews = [
            review
            for epoch in epochs
            if type(epoch) is dict and type(epoch.get("reviews")) is list
            for review in epoch["reviews"]
            if type(review) is dict
        ]
        for name in mandatory_tasks:
            matches = [
                review
                for review in reviews
                if review.get("task") == name
                and review.get("result") == "PASS"
                and review.get("evidence_digest") == evidence_digest
                and review.get("envelope_digest") == envelope_digests[name]
                and type(review.get("identity")) is dict
                and review["identity"].get("commit") == request.head_sha
            ]
            if len(matches) != 1:
                raise MergeGateError(
                    f"qualified run record does not bind review: {name}"
                )
        digests["run_record"] = record_digest
        digests.update(qualification_digests)
        return digests
    finally:
        _verify_seals(seals)


def _check_local_checkout(request: MergeRequest, runner: CommandRunner) -> None:
    if runner.run(("git", "status", "--porcelain")).stdout.strip():
        raise MergeGateError("local checkout is not clean")
    if runner.run(("git", "rev-parse", "HEAD")).stdout.strip() != request.head_sha:
        raise MergeGateError("local checkout head does not match")
    repository = runner.run(
        ("gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner")
    ).stdout.strip()
    if repository.casefold() != request.repository.casefold():
        raise MergeGateError("local checkout repository does not match")


def _branch_protection(request: MergeRequest, runner: CommandRunner) -> dict[str, Any]:
    try:
        raw = runner.run((
            "gh", "api", f"repos/{request.repository}/branches/main/protection"
        )).stdout
    except MergeGateError:
        raise ServerEnforcementBlocked(
            "strict server enforcement is unavailable"
        ) from None
    try:
        return _json(raw, "branch protection")
    except MergeGateError as error:
        raise ServerEnforcementBlocked(str(error)) from None


def _check_server_enforcement(request: MergeRequest, protection: dict[str, Any]) -> None:
    statuses = protection.get("required_status_checks")
    reviews = protection.get("required_pull_request_reviews")
    admins = protection.get("enforce_admins")
    conversations = protection.get("required_conversation_resolution")
    force_pushes = protection.get("allow_force_pushes")
    deletions = protection.get("allow_deletions")
    if type(statuses) is not dict or statuses.get("strict") is not True:
        raise ServerEnforcementBlocked(
            "server enforcement lacks strict required checks"
        )
    raw_contexts = statuses.get("contexts")
    raw_checks = statuses.get("checks")
    if (
        type(raw_contexts) is not list
        or any(type(context) is not str for context in raw_contexts)
        or type(raw_checks) is not list
    ):
        raise ServerEnforcementBlocked(
            "server enforcement returned malformed required checks"
        )
    contexts = set(raw_contexts)
    for check in raw_checks:
        if type(check) is not dict or type(check.get("context")) is not str:
            raise ServerEnforcementBlocked(
                "server enforcement returned malformed required checks"
            )
        contexts.add(check["context"])
    if not _PRE_MERGE_CHECKS <= contexts:
        raise ServerEnforcementBlocked(
            "server enforcement lacks a required check"
        )
    bypass_present = (
        type(reviews) is dict and "bypass_pull_request_allowances" in reviews
    )
    bypass = (
        reviews["bypass_pull_request_allowances"] if bypass_present else {}
    )
    bypass_lists_valid = (
        not bypass_present
        or type(bypass) is dict
        and all(
            type(bypass.get(kind)) is list
            for kind in ("users", "teams", "apps")
        )
    )
    if (
        type(reviews) is not dict
        or type(reviews.get("required_approving_review_count")) is not int
        or reviews["required_approving_review_count"] < 1
        or reviews.get("dismiss_stale_reviews") is not True
        or not bypass_lists_valid
        or any(bypass.get(kind) for kind in ("users", "teams", "apps"))
    ):
        raise ServerEnforcementBlocked(
            "server enforcement lacks strict review protection"
        )
    if type(admins) is not dict or admins.get("enabled") is not True:
        raise ServerEnforcementBlocked(
            "server enforcement permits administrative bypass"
        )
    if type(conversations) is not dict or conversations.get("enabled") is not True:
        raise ServerEnforcementBlocked(
            "server enforcement does not require resolved conversations"
        )
    if type(force_pushes) is not dict or force_pushes.get("enabled") is not False:
        raise ServerEnforcementBlocked(
            "server enforcement permits force pushes"
        )
    if type(deletions) is not dict or deletions.get("enabled") is not False:
        raise ServerEnforcementBlocked(
            "server enforcement permits branch deletion"
        )


def _pull_request(request: MergeRequest, runner: CommandRunner) -> dict[str, Any]:
    return _json(
        runner.run(("gh", "api", f"repos/{request.repository}/pulls/{request.pull_request}")).stdout,
        "pull request query",
    )


def _check_pull_request(request: MergeRequest, pull: dict[str, Any]) -> None:
    base, head = pull.get("base"), pull.get("head")
    if pull.get("number") != request.pull_request or pull.get("state") != "open" or pull.get("draft") is not False or pull.get("merged") is not False:
        raise MergeGateError("pull request is not the expected open ready request")
    if (
        type(base) is not dict
        or type(base.get("repo")) is not dict
        or type(base["repo"].get("full_name")) is not str
        or base["repo"]["full_name"].casefold() != request.repository.casefold()
    ):
        raise MergeGateError("pull request repository does not match")
    if base.get("ref") != "main" or base.get("sha") != request.base_sha:
        raise MergeGateError("pull request base does not match")
    if type(head) is not dict or head.get("sha") != request.head_sha:
        raise MergeGateError("pull request head does not match")
    if pull.get("mergeable") is not True:
        raise MergeGateError("GitHub does not report the pull request mergeable")


def _pull_checks(request: MergeRequest, runner: CommandRunner) -> list[dict[str, Any]]:
    result = runner.run((
        "gh", "pr", "checks", str(request.pull_request), "--repo", request.repository,
        "--json", "name,bucket",
    ), check=False)
    if result.returncode not in {0, 8}:
        raise MergeGateError("command failed: gh")
    return _json(result.stdout, "check query", list)


def _check_named_passes(checks: list[dict[str, Any]], required: frozenset[str]) -> None:
    by_name: dict[str, list[dict[str, Any]]] = {}
    for check in checks:
        if type(check) is not dict or type(check.get("name")) is not str:
            raise MergeGateError("check query returned an invalid check")
        by_name.setdefault(check["name"], []).append(check)
    for name in sorted(required):
        matches = by_name.get(name, [])
        if len(matches) != 1 or matches[0].get("bucket") != "pass":
            raise MergeGateError(f"required check is not an unambiguous pass: {name}")


def _review_state(request: MergeRequest, runner: CommandRunner) -> dict[str, Any]:
    owner, name = request.repository.split("/", 1)
    query = (
        "query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name)"
        "{pullRequest(number:$number){reviewDecision reviewThreads(first:100){totalCount "
        "pageInfo{hasNextPage} nodes{isResolved}}}}}"
    )
    document = _json(runner.run((
        "gh", "api", "graphql", "-f", f"owner={owner}", "-f", f"name={name}",
        "-F", f"number={request.pull_request}", "-f", f"query={query}",
    )).stdout, "review query")
    data = document.get("data")
    repository = data.get("repository") if type(data) is dict else None
    pull = repository.get("pullRequest") if type(repository) is dict else None
    if type(pull) is not dict:
        raise MergeGateError("review query returned the wrong JSON shape")
    return pull


def _check_reviews(pull: dict[str, Any]) -> None:
    if pull.get("reviewDecision") != "APPROVED":
        raise MergeGateError("pull request review decision is not APPROVED")
    threads = pull.get("reviewThreads")
    page_info = threads.get("pageInfo") if type(threads) is dict else None
    nodes = threads.get("nodes") if type(threads) is dict else None
    if (
        type(threads) is not dict
        or type(nodes) is not list
        or type(page_info) is not dict
        or page_info.get("hasNextPage") is not False
        or type(threads.get("totalCount")) is not int
        or threads["totalCount"] != len(nodes)
    ):
        raise MergeGateError("review thread query is incomplete")
    if any(
        type(node) is not dict or node.get("isResolved") is not True
        for node in nodes
    ):
        raise MergeGateError("pull request has an unresolved review thread")


def _live_preflight(request: MergeRequest, runner: CommandRunner) -> None:
    _check_server_enforcement(request, _branch_protection(request, runner))
    _check_pull_request(request, _pull_request(request, runner))
    _check_named_passes(_pull_checks(request, runner), _PRE_MERGE_CHECKS)
    _check_reviews(_review_state(request, runner))


def _reconcile_ambiguous_merge(
    request: MergeRequest, runner: CommandRunner, cause: str
) -> str:
    known: dict[str, object] = {}
    try:
        pull = _pull_request(request, runner)
        for source, target in (("state", "pull_request_state"), ("merged", "github_merged")):
            if type(pull.get(source)) in {str, bool}:
                known[target] = pull[source]
        sha = pull.get("merge_commit_sha")
        base = pull.get("base")
        head = pull.get("head")
        base_repo = base.get("repo") if type(base) is dict else None
        identity_matches = (
            pull.get("number") == request.pull_request
            and type(base) is dict
            and type(base_repo) is dict
            and type(base_repo.get("full_name")) is str
            and base_repo["full_name"].casefold() == request.repository.casefold()
            and base.get("ref") == request.base_ref
            and base.get("sha") == request.base_sha
            and type(head) is dict
            and head.get("sha") == request.head_sha
        )
        if (
            identity_matches
            and pull.get("state") == "closed"
            and pull.get("merged") is True
            and type(sha) is str
            and _SHA.fullmatch(sha)
        ):
            return sha
    except MergeGateError:
        pass
    raise MutationRecoveryError(
        "merge_response", "unknown", f"merge response was ambiguous: {cause}",
        known_state=known,
    ) from None


def _merge(request: MergeRequest, runner: CommandRunner) -> str:
    try:
        raw = runner.run((
            "gh", "api", "-X", "PUT",
            f"repos/{request.repository}/pulls/{request.pull_request}/merge",
            "-f", f"sha={request.head_sha}", "-f", "merge_method=squash",
        )).stdout
        result = _json(raw, "merge request")
        sha = result.get("sha")
        if result.get("merged") is True and type(sha) is str and _SHA.fullmatch(sha):
            return sha
        return _reconcile_ambiguous_merge(request, runner, "malformed confirmation")
    except MutationRecoveryError:
        raise
    except MergeGateError as error:
        return _reconcile_ambiguous_merge(request, runner, str(error))


def _commit_checks(repository: str, sha: str, runner: CommandRunner) -> list[dict[str, Any]]:
    raw = runner.run((
        "gh", "api", "--paginate", "--slurp",
        f"repos/{repository}/commits/{sha}/check-runs?filter=latest&per_page=100",
    )).stdout
    pages = _json(raw, "post-merge check query", list)
    checks: list[dict[str, Any]] = []
    for page in pages:
        if type(page) is not dict or type(page.get("check_runs")) is not list:
            raise MergeGateError("post-merge check query returned the wrong shape")
        for check in page["check_runs"]:
            if type(check) is not dict:
                raise MergeGateError("post-merge check query returned an invalid check")
            checks.append({
                "name": check.get("name"),
                "bucket": "pass" if check.get("status") == "completed" and check.get("conclusion") == "success" else "pending" if check.get("status") != "completed" else "fail",
            })
    return checks


def _wait_post_merge_checks(
    request: MergeRequest, merge_sha: str, runner: CommandRunner, sleeper: Sleeper
) -> None:
    for attempt in range(request.post_merge_attempts):
        try:
            checks = _commit_checks(request.repository, merge_sha, runner)
            _check_named_passes(checks, _POST_MAIN_CHECKS)
            return
        except MergeGateError as error:
            if "unambiguous pass" not in str(error) or attempt + 1 == request.post_merge_attempts:
                raise
            sleeper.sleep(request.post_merge_interval)
    raise MergeGateError("post-merge checks did not complete")


def _post_verify(
    request: MergeRequest, merge_sha: str, runner: CommandRunner, sleeper: Sleeper
) -> None:
    try:
        pull = _pull_request(request, runner)
        if pull.get("state") != "closed" or pull.get("merged") is not True or pull.get("merge_commit_sha") != merge_sha:
            raise MergeGateError("post-merge pull request state is inconsistent")
    except MergeGateError as error:
        raise PostMutationError("github_state", merge_sha, str(error)) from None
    remote_ref = "refs/remotes/origin/main"
    try:
        runner.run(("git", "fetch", "--no-tags", "origin", f"+refs/heads/main:{remote_ref}"))
        parents = runner.run(("git", "rev-list", "--parents", "-n", "1", merge_sha)).stdout.split()
        if parents != [merge_sha, request.base_sha]:
            raise MergeGateError("squash merge parent is not the qualified base")
        if runner.run(("git", "merge-base", "--is-ancestor", merge_sha, remote_ref), check=False).returncode != 0:
            raise MergeGateError("merge commit is not contained by remote main")
    except MergeGateError as error:
        raise PostMutationError("git_identity", merge_sha, str(error)) from None
    try:
        _wait_post_merge_checks(request, merge_sha, runner, sleeper)
    except MergeGateError as error:
        raise PostMutationError("post_merge_checks", merge_sha, str(error)) from None


def execute(
    request: MergeRequest, runner: CommandRunner, sleeper: Sleeper
) -> dict[str, object]:
    _validate_request(request)
    evidence_digests = _validate_local_evidence(request, runner)
    _check_local_checkout(request, runner)
    _live_preflight(request, runner)
    if request.dry_run:
        return {
            "base": request.base_sha, "evidence": evidence_digests,
            "head": request.head_sha, "mutation_occurred": False,
            "outcome": "dry_run_passed", "pull_request": request.pull_request,
            "repository": request.repository,
        }
    _live_preflight(request, runner)
    _check_local_checkout(request, runner)
    merge_sha = _merge(request, runner)
    _post_verify(request, merge_sha, runner, sleeper)
    return {
        "base": request.base_sha, "evidence": evidence_digests,
        "head": request.head_sha, "merge_commit": merge_sha,
        "mutation_occurred": True, "outcome": "merged",
        "pull_request": request.pull_request, "repository": request.repository,
    }


def _named_path(value: str) -> tuple[str, Path]:
    name, separator, path = value.partition("=")
    if not separator or not name or not path:
        raise argparse.ArgumentTypeError("expected NAME=PATH")
    return name, Path(path)


def _qualification(value: str) -> tuple[str, Path, Path]:
    parts = value.split("=", 2)
    if len(parts) != 3 or any(not part for part in parts):
        raise argparse.ArgumentTypeError("expected NAME=RECEIPT=CACHE_RECORD")
    return parts[0], Path(parts[1]), Path(parts[2])


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pull-request", required=True, type=int)
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--review-repo", required=True, type=Path)
    parser.add_argument("--review-evidence", required=True, type=Path)
    parser.add_argument("--review-envelope", action="append", required=True, type=_named_path)
    parser.add_argument("--run-record", required=True, type=Path)
    parser.add_argument("--qualification-evidence", action="append", default=[], type=_qualification)
    parser.add_argument("--post-merge-attempts", type=int, default=20)
    parser.add_argument("--post-merge-interval", type=float, default=15)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    request = MergeRequest(
        repository=args.repository, pull_request=args.pull_request,
        base_ref=args.base_ref, base_sha=args.base_sha, head_sha=args.head_sha,
        review_repo=args.review_repo,
        review_evidence=args.review_evidence,
        review_envelopes=tuple(args.review_envelope),
        run_record=args.run_record,
        qualification_evidence=tuple(args.qualification_evidence),
        post_merge_attempts=args.post_merge_attempts,
        post_merge_interval=args.post_merge_interval, dry_run=args.dry_run,
    )
    try:
        result = execute(request, SubprocessRunner(), TimeSleeper())
    except ServerEnforcementBlocked as error:
        result = {
            "error": str(error),
            "mutation_occurred": False,
            "outcome": "BLOCKED",
            "phase": "server_enforcement",
            "pull_request": request.pull_request,
            "repository": request.repository,
        }
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 2
    except MutationRecoveryError as error:
        result = {
            "error": str(error), "mutation_occurred": error.mutation_occurred,
            "outcome": (
                "mutation_state_unknown"
                if error.mutation_occurred == "unknown"
                else "verification_failed"
            ),
            "phase": error.phase, "pull_request": request.pull_request,
            "repository": request.repository,
        }
        if error.merge_sha is not None:
            result["merge_commit"] = error.merge_sha
        if error.known_state:
            result["known_state"] = error.known_state
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 2
    except MergeGateError as error:
        print(f"merge gate failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
