"""The deliberately small installed runtime command surface."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import NoReturn, TextIO

from mechanical_librarian.application.configuration import ConfigurationError
from mechanical_librarian.application.qualification_state_bridge import (
    QUALIFICATION_STATE_BRIDGE_PLANS,
    QualificationStateBridgeRecoveryRequired,
)
from mechanical_librarian.domain.metadata_policy import MetadataPolicyProfile

ReviewServeRunner = Callable[[Path, Path, Path, Path, str, int], None]
PolicyPreviewRunner = Callable[[Path, Path, dict[str, object]], dict[str, object]]
CoverReviewRunner = Callable[
    [Path, Path, Path, str, str, str | None, str | None], dict[str, object]
]
ReviewActivityRunner = Callable[[Path, Path], dict[str, object]]
ProviderRecoveryRunner = Callable[[Path, Path, Path, Path], None]
QualificationStateBridgeRunner = Callable[[Path, Path, Path, str], dict[str, str | int]]
_MAX_PROFILE_BYTES = 16_384


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ValueError("invalid invocation")


def _port(value: str) -> int:
    try:
        port = int(value, 10)
    except ValueError as error:
        raise argparse.ArgumentTypeError("invalid port") from error
    if str(port) != value or not 1 <= port <= 65_535:
        raise argparse.ArgumentTypeError("invalid port")
    return port


def _read_profile(path: Path) -> bytes:
    """Read one bounded regular profile file without following links."""
    descriptor = os.open(
        path,
        os.O_RDONLY
        | os.O_NONBLOCK
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        information = os.fstat(descriptor)
        if (
            not stat.S_ISREG(information.st_mode)
            or not 1 <= information.st_size <= _MAX_PROFILE_BYTES
        ):
            raise ValueError("profile is invalid")
        payload = os.read(descriptor, information.st_size + 1)
        if len(payload) != information.st_size:
            raise ValueError("profile is invalid")
        return payload
    finally:
        os.close(descriptor)


def _parser() -> _Parser:
    parser = _Parser(
        prog="mechanical-librarian", description="Mechanical Librarian runtime"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    review_serve = commands.add_parser(
        "review-serve", help="serve the authenticated owner review interface"
    )
    review_serve.add_argument("--config", required=True, metavar="PATH", type=Path)
    review_serve.add_argument("--database", required=True, metavar="PATH", type=Path)
    review_serve.add_argument("--catalogue", required=True, metavar="PATH", type=Path)
    review_serve.add_argument("--artifacts", required=True, metavar="PATH", type=Path)
    review_serve.add_argument(
        "--host", choices=("127.0.0.1", "::1", "0.0.0.0", "::"), default="127.0.0.1"
    )
    review_serve.add_argument("--port", type=_port, default=8080)
    preview = commands.add_parser(
        "policy-preview", help="preview one canonical metadata policy without mutation"
    )
    preview.add_argument("--config", required=True, metavar="PATH", type=Path)
    preview.add_argument("--database", required=True, metavar="PATH", type=Path)
    preview.add_argument("--profile", required=True, metavar="PATH", type=Path)
    cover = commands.add_parser(
        "cover-review", help="inspect or record one exact current cover consideration"
    )
    cover.add_argument(
        "action", choices=("show", "history", "keep", "select", "no-action")
    )
    cover.add_argument("--config", required=True, metavar="PATH", type=Path)
    cover.add_argument("--database", required=True, metavar="PATH", type=Path)
    cover.add_argument("--artifacts", required=True, metavar="PATH", type=Path)
    cover.add_argument("--case", required=True, metavar="ID")
    cover.add_argument("--artifact", metavar="COVER_ID")
    cover.add_argument("--idempotency-key", metavar="KEY")
    activity = commands.add_parser(
        "review-activity", help="inspect guarded application and recovery history"
    )
    activity.add_argument("--config", required=True, metavar="PATH", type=Path)
    activity.add_argument("--database", required=True, metavar="PATH", type=Path)
    recovery = commands.add_parser(
        "provider-recovery", help="run one durable provider recovery claim"
    )
    recovery.add_argument("--config", required=True, metavar="PATH", type=Path)
    recovery.add_argument("--database", required=True, metavar="PATH", type=Path)
    recovery.add_argument("--catalogue", required=True, metavar="PATH", type=Path)
    recovery.add_argument("--artifacts", required=True, metavar="PATH", type=Path)
    bridge = commands.add_parser(
        "qualification-state-bridge",
        help="reconstruct one exact retained qualification checkpoint while offline",
    )
    bridge.add_argument(
        "plan", choices=QUALIFICATION_STATE_BRIDGE_PLANS, metavar="PLAN"
    )
    bridge.add_argument("--database", required=True, metavar="PATH", type=Path)
    bridge.add_argument("--backup", required=True, metavar="PATH", type=Path)
    bridge.add_argument("--output", required=True, metavar="PATH", type=Path)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    review_serve_runner: ReviewServeRunner | None = None,
    policy_preview_runner: PolicyPreviewRunner | None = None,
    cover_review_runner: CoverReviewRunner | None = None,
    review_activity_runner: ReviewActivityRunner | None = None,
    provider_recovery_runner: ProviderRecoveryRunner | None = None,
    qualification_state_bridge_runner: QualificationStateBridgeRunner | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the closed installed operations without exposing workbench tools."""
    output = sys.stdout if stdout is None else stdout
    errors = sys.stderr if stderr is None else stderr
    try:
        arguments = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    except ValueError:
        errors.write("Review server failed: invalid invocation.\n")
        return 2
    try:
        if arguments.command == "qualification-state-bridge":
            if qualification_state_bridge_runner is None:
                raise RuntimeError("runtime composition unavailable")
            bridge_result = qualification_state_bridge_runner(
                arguments.database,
                arguments.backup,
                arguments.output,
                arguments.plan,
            )
            output.write(
                json.dumps(
                    bridge_result,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
            return 0
        if arguments.command == "review-activity":
            if review_activity_runner is None:
                raise RuntimeError("runtime composition unavailable")
            value = review_activity_runner(arguments.config, arguments.database)
            output.write(
                json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
            return 0
        if arguments.command == "provider-recovery":
            if provider_recovery_runner is None:
                raise RuntimeError("runtime composition unavailable")
            provider_recovery_runner(
                arguments.config,
                arguments.database,
                arguments.catalogue,
                arguments.artifacts,
            )
            return 0
        if arguments.command == "cover-review":
            if cover_review_runner is None:
                raise RuntimeError("runtime composition unavailable")
            if (
                (arguments.action == "select") != (arguments.artifact is not None)
                or (arguments.action in {"show", "history"})
                != (arguments.idempotency_key is None)
                or arguments.action not in {"show", "history"}
                and not (
                    isinstance(arguments.idempotency_key, str)
                    and 16 <= len(arguments.idempotency_key.encode("utf-8")) <= 128
                )
            ):
                raise ValueError("invalid cover review invocation")
            value = cover_review_runner(
                arguments.config,
                arguments.database,
                arguments.artifacts,
                arguments.case,
                arguments.action,
                arguments.artifact,
                arguments.idempotency_key,
            )
            output.write(
                json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
            return 0
        if arguments.command == "policy-preview":
            if policy_preview_runner is None:
                raise RuntimeError("runtime composition unavailable")
            value = json.loads(
                _read_profile(arguments.profile).decode("utf-8", "strict")
            )
            if type(value) is not dict:
                raise ValueError
            MetadataPolicyProfile.from_data(value)
            output.write(
                json.dumps(
                    policy_preview_runner(arguments.config, arguments.database, value),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
            return 0
        if review_serve_runner is None:
            raise RuntimeError("runtime composition unavailable")
        review_serve_runner(
            arguments.config,
            arguments.database,
            arguments.catalogue,
            arguments.artifacts,
            arguments.host,
            arguments.port,
        )
    except QualificationStateBridgeRecoveryRequired:
        errors.write("Qualification-state bridge requires recovery.\n")
        return 1
    except ConfigurationError:
        errors.write("Review server failed: configuration unavailable.\n")
        return 3
    except Exception:
        errors.write(
            "Qualification-state bridge failed safely.\n"
            if arguments.command == "qualification-state-bridge"
            else "Review server failed safely.\n"
        )
        return 1
    return 0


__all__ = ["main"]
