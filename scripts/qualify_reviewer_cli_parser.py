#!/usr/bin/env python3
"""Qualify pinned reviewer CLI shapes and one synthetic production request."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from mechanical_librarian.adapters.reviewers.codex_shadow import (
    CodexShadowReviewer,
    QualificationProfile,
    ShadowReviewerError,
    _bound_artifact,
    load_qualification_profile,
)
from mechanical_librarian.domain.shadow_review import ShadowReviewRequest

_SCHEMA = "reviewer-cli-parser-probe-v2"
_PROBE_TIMEOUT_SECONDS = 30


class ParserProbeError(RuntimeError):
    """A value-free parser-probe failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def qualify_reviewer_cli_parser(profile_path: Path) -> dict[str, object]:
    """Run three offline shapes and one live synthetic production request."""
    if not sys.platform.startswith("linux"):
        raise ParserProbeError("setup")
    try:
        profile = load_qualification_profile(profile_path)
        CodexShadowReviewer(profile)._require_private_directory(profile.job_root_path)
        with tempfile.TemporaryDirectory(
            prefix="reviewer-cli-parser-", dir=profile.job_root_path
        ) as directory:
            probe_root = Path(directory)
            auth_store = probe_root / "auth"
            job_root = probe_root / "jobs"
            auth_store.mkdir(mode=0o700)
            job_root.mkdir(mode=0o700)
            probe_profile = QualificationProfile(
                profile.codex_path,
                profile.sandbox_path,
                auth_store,
                job_root,
                profile.identity,
            )
            _qualify_probe_profile(probe_profile)
            _qualify_synthetic_canary(profile)
    except ShadowReviewerError as error:
        raise ParserProbeError(error.code) from None
    except OSError:
        raise ParserProbeError("setup") from None
    return {"checks": 4, "schema_version": _SCHEMA, "status": "passed"}


def _qualify_probe_profile(profile: QualificationProfile) -> None:
    reviewer = CodexShadowReviewer(profile)
    with (
        _bound_artifact(
            profile.codex_path, profile.identity.executable_sha256
        ) as codex,
        _bound_artifact(
            profile.sandbox_path, profile.identity.sandbox_sha256
        ) as sandbox,
    ):
        with reviewer._auth_lock(), reviewer._empty_auth_config() as config_fd:
            reviewer._verify_codex(codex, sandbox)
            auth = reviewer._auth_command(codex, sandbox, config_fd)
            for suffix in (
                ("login", "--device-auth", "--help"),
                ("login", "status", "--help"),
                ("logout", "--help"),
            ):
                _run(
                    _without_network(auth + list(suffix)),
                    (codex.descriptor, sandbox.descriptor, config_fd),
                    profile.identity.timeout_seconds,
                )


def _qualify_synthetic_canary(profile: QualificationProfile) -> None:
    """Run one synthetic request through the complete production execution gate."""
    reviewer = CodexShadowReviewer(profile)
    request = ShadowReviewRequest.synthetic_canary(profile.identity)
    dispatch: list[tuple[int, int, str]] = []

    def record_dispatch(pid: int, pgid: int, start_token: str) -> None:
        if (
            dispatch
            or type(pid) is not int
            or type(pgid) is not int
            or pid < 2
            or pgid < 2
            or type(start_token) is not str
            or not start_token.isascii()
            or not start_token.isdigit()
        ):
            raise ShadowReviewerError("runtime")
        dispatch.append((pid, pgid, start_token))

    run_result = reviewer.run(request, record_dispatch)
    run_result.result.validate_for(request)
    if len(dispatch) != 1:
        raise ShadowReviewerError("runtime")


def _run(
    command: list[str],
    descriptors: tuple[int, ...],
    limit: int,
) -> None:
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            pass_fds=descriptors,
            timeout=min(limit, _PROBE_TIMEOUT_SECONDS),
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ParserProbeError("timeout") from None
    except (OSError, subprocess.SubprocessError):
        raise ParserProbeError("setup") from None
    if completed.returncode != 0:
        raise ParserProbeError("parser")


def _without_network(command: list[str]) -> list[str]:
    return [command[0], "--unshare-net", *command[1:]]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Qualify pinned reviewer CLI parser shapes.")
    parser.add_argument("--qualification-profile", required=True, type=Path)
    arguments = parser.parse_args(argv)
    try:
        result = qualify_reviewer_cli_parser(arguments.qualification_profile)
    except ParserProbeError as error:
        result = {"code": error.code, "schema_version": _SCHEMA, "status": "failed"}
        print(json.dumps(result, sort_keys=True, separators=(",", ":")), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
