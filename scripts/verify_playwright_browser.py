#!/usr/bin/env python3
"""Verify the exact Playwright headless Chromium payload before execution."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

_EXPECTED_TREE_SHA256 = {
    "darwin-arm64": "ad7a45dccec74e1ceec66d023122a03d2c0351d25c169dfe18c9c4aec49a48b3",
    "linux-x86_64": "8165f22c947b69d93b32288b8d00acde78f11e287da8084906c632c90b55781c",
}


class BrowserVerificationError(RuntimeError):
    """The installed qualification browser is absent or not exact."""


def _platform_key() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "darwin" and machine == "arm64":
        return "darwin-arm64"
    if system == "linux" and machine in {"x86_64", "amd64"}:
        return "linux-x86_64"
    raise BrowserVerificationError("unsupported qualification browser platform")


def _installed_payload() -> Path:
    with sync_playwright() as playwright:
        executable = Path(playwright.chromium.executable_path).resolve()
    chromium_root = next(
        (
            parent
            for parent in executable.parents
            if parent.name.startswith("chromium-")
            and parent.name.removeprefix("chromium-").isdigit()
        ),
        None,
    )
    if chromium_root is None:
        raise BrowserVerificationError("unexpected qualification browser layout")
    revision = chromium_root.name.removeprefix("chromium-")
    shell_root = chromium_root.parent / f"chromium_headless_shell-{revision}"
    payloads = sorted(shell_root.glob("chrome-headless-shell-*"))
    if len(payloads) != 1 or not payloads[0].is_dir():
        raise BrowserVerificationError("unexpected qualification browser layout")
    return payloads[0]


def browser_tree_sha256(payload: Path) -> str:
    if not payload.is_dir():
        raise BrowserVerificationError("qualification browser payload is absent")
    files = sorted(path for path in payload.rglob("*") if path.is_file())
    if not files or any(path.is_symlink() for path in payload.rglob("*")):
        raise BrowserVerificationError("qualification browser payload is invalid")
    digest = hashlib.sha256(b"playwright-browser-tree-v1\0")
    for path in files:
        relative = path.relative_to(payload).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        size = path.stat().st_size
        digest.update(size.to_bytes(8, "big"))
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()


def verify_playwright_browser() -> str:
    key = _platform_key()
    observed = browser_tree_sha256(_installed_payload())
    if observed != _EXPECTED_TREE_SHA256[key]:
        raise BrowserVerificationError("qualification browser digest mismatch")
    return observed


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path)
    parser.add_argument("--platform")
    parser.add_argument("--report", action="store_true")
    values = parser.parse_args(arguments)
    key = values.platform or _platform_key()
    if key not in _EXPECTED_TREE_SHA256:
        raise BrowserVerificationError("unsupported qualification browser platform")
    payload = values.path.resolve() if values.path else _installed_payload()
    observed = browser_tree_sha256(payload)
    if values.report:
        print(f"{key} {observed}")
        return 0
    if observed != _EXPECTED_TREE_SHA256[key]:
        raise BrowserVerificationError("qualification browser digest mismatch")
    print(f"verified {key} {observed}")
    return 0


def cli(arguments: list[str] | None = None) -> int:
    try:
        return main(arguments)
    except Exception:
        print(
            json.dumps(
                {
                    "reason": "browser_verification_failed",
                    "schema": "test-tier-result-v1",
                    "status": "failed",
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(cli())
