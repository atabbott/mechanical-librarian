#!/usr/bin/env python3
"""Run fast tests from a clean exact candidate in native non-root Linux."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4


def verify_source(root: Path, sha: str) -> None:
    if re.fullmatch(r"[0-9a-f]{40}", sha) is None:
        raise RuntimeError("source_identity_invalid")
    head = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    dirty = subprocess.check_output(
        ["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=all"],
        text=True,
    )
    if head != sha or dirty:
        raise RuntimeError("source_identity_mismatch_or_dirty")


def image_recipe(dockerfile: str, uid: int, gid: int) -> str:
    """Accept only the three literal digest-bound forms this helper consumes."""
    pins: list[str] = []
    for stage, prefix in (("build", "python"), ("frontend-build", "node")):
        lines = [
            line
            for line in dockerfile.splitlines()
            if re.search(rf"\bAS\s+{stage}\s*$", line, re.I)
        ]
        pattern = rf"FROM ({prefix}:[a-zA-Z0-9_.-]+@sha256:[0-9a-f]{{64}}) AS {stage}"
        if len(lines) != 1 or (match := re.fullmatch(pattern, lines[0])) is None:
            raise RuntimeError("unsupported_linux_toolchain_pin")
        pins.append(match[1])
    lines = [line for line in dockerfile.splitlines() if "/usr/local/bin/uv" in line]
    if (
        len(lines) != 1
        or (
            match := re.fullmatch(
                r"COPY --from=(ghcr.io/astral-sh/uv:[0-9.]+@sha256:[0-9a-f]{64}) /uv /usr/local/bin/uv",
                lines[0],
            )
        )
        is None
    ):
        raise RuntimeError("unsupported_linux_toolchain_pin")
    return f"""FROM {pins[1]} AS node
FROM {pins[0]}
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from={match[1]} /uv /usr/local/bin/uv
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx && apt-get update && apt-get install -y --no-install-recommends git openssl && rm -rf /var/lib/apt/lists/*
RUN groupadd --non-unique --gid {gid} qualifier && useradd --uid {uid} --gid qualifier --create-home qualifier
USER {uid}:{gid}
ENV HOME=/home/qualifier UV_LINK_MODE=copy UV_CACHE_DIR=/cache UV_PROJECT_ENVIRONMENT=/workspace/venv
ENV PATH=/workspace/venv/bin:/usr/local/bin:/usr/bin:/bin
WORKDIR /workspace/source
"""


# Executed by the freshly synced interpreter, before pytest imports the source.
_PROVENANCE = """
import json, os, pathlib, platform, pwd, sqlite3, subprocess, sys
import mechanical_librarian
root = pathlib.Path('/workspace/source')
assert pathlib.Path(mechanical_librarian.__file__).resolve().is_relative_to(root / 'src')
assert os.getuid() != 0
assert platform.system() == 'Linux'
assert subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip() == sys.argv[1]
assert pathlib.Path(pwd.getpwuid(os.getuid()).pw_dir).is_dir()
p = pathlib.Path('/workspace/permission-probe')
p.write_text('unchanged')
p.chmod(0o400)
try:
    p.write_text('forbidden')
except PermissionError:
    pass
else:
    raise RuntimeError('linux_permission_probe_failed')
assert p.read_text() == 'unchanged'
p.unlink()
print(json.dumps(dict(source_sha=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(), python=platform.python_version(), sqlite=sqlite3.sqlite_version, architecture=platform.machine(), uid=os.getuid(), source_import=True, permission_refused=True)), flush=True)
"""


def run(root: Path, sha: str, cache: Path, timeout: int, job_timeout: int) -> None:
    if os.getuid() == 0:
        raise RuntimeError("linux_fast_requires_nonroot_host")
    verify_source(root, sha)
    recipe = image_recipe((root / "Dockerfile").read_text(), os.getuid(), os.getgid())
    image = (
        "mechanical-librarian-linux-fast:" + hashlib.sha256(recipe.encode()).hexdigest()
    )
    architecture = subprocess.check_output(
        ["docker", "info", "--format", "{{.Architecture}}"], text=True, timeout=30
    ).strip()
    architecture = {"aarch64": "arm64", "x86_64": "amd64"}.get(
        architecture, architecture
    )
    if architecture not in {"arm64", "amd64"}:
        raise RuntimeError("unsupported_native_linux_architecture")
    deadline = time.monotonic() + job_timeout

    def execute(command: list[str]) -> None:
        subprocess.run(
            command, check=True, timeout=max(0.001, deadline - time.monotonic())
        )

    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="linux-fast-") as temporary:
        workspace = Path(temporary)
        source = workspace / "source"
        name = "linux-fast-" + uuid4().hex
        try:
            execute(
                ["git", "clone", "--local", "--no-hardlinks", str(root), str(source)]
            )
            execute(["git", "-C", str(source), "checkout", "--detach", sha])
            verify_source(source, sha)
            if (
                not (source / ".git").is_dir()
                or (source / ".git/objects/info/alternates").exists()
            ):
                raise RuntimeError("linux_fast_clone_not_standalone")
            build = workspace / "image"
            build.mkdir()
            (build / "Dockerfile").write_text(recipe)
            execute(
                [
                    "docker",
                    "build",
                    "--platform",
                    "linux/" + architecture,
                    "--tag",
                    image,
                    str(build),
                ]
            )
            command = (
                "uv sync --locked --all-groups && "
                "python -c " + shlex.quote(_PROVENANCE) + " " + sha + " && "
                f"python scripts/run_test_tier.py fast --test-timeout-seconds {timeout}"
            )
            execute(
                [
                    "docker",
                    "run",
                    "--init",
                    "--platform",
                    "linux/" + architecture,
                    "--rm",
                    "--name",
                    name,
                    "--mount",
                    f"type=bind,src={workspace},dst=/workspace",
                    "--mount",
                    f"type=bind,src={cache.resolve()},dst=/cache",
                    image,
                    "sh",
                    "-eu",
                    "-c",
                    command,
                ]
            )
            verify_source(source, sha)
        finally:
            subprocess.run(
                ["docker", "rm", "--force", name],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
            verify_source(root, sha)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--cache-directory", type=Path, required=True)
    parser.add_argument("--test-timeout-seconds", type=int, default=180)
    parser.add_argument("--job-timeout-seconds", type=int, default=1800)
    args = parser.parse_args(argv)
    if args.test_timeout_seconds <= 0 or args.job_timeout_seconds <= 0:
        parser.error("timeouts must be positive integers")
    started = time.monotonic()
    try:
        run(
            Path(__file__).resolve().parents[1],
            args.source_sha,
            args.cache_directory,
            args.test_timeout_seconds,
            args.job_timeout_seconds,
        )
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        print(f"Linux fast failed: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "linux_fast": "passed",
                "source_sha": args.source_sha,
                "seconds": round(time.monotonic() - started, 3),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
