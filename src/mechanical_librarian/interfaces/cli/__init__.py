"""Stable, value-free command-line interface."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TextIO

from mechanical_librarian.application.job_heartbeat import (
    HeartbeatInspection,
    HeartbeatOutcome,
    HeartbeatResult,
)
from mechanical_librarian.application.connection_probe import (
    ConnectionInspection,
    ConnectionProbeOutcome,
    ConnectionProbeResult,
)
from mechanical_librarian.application.state_check import (
    StateCheckOutcome,
    StateCheckResult,
)
from mechanical_librarian.application.synthetic_inventory import (
    SyntheticInventoryInspection,
    SyntheticInventoryOutcome,
    SyntheticInventoryResult,
)
from mechanical_librarian.application.inventory_reconciliation import (
    InventoryOutcome,
    InventoryRunResult,
)


StateCheckRunner = Callable[[Path], StateCheckResult]
JobHeartbeatRunner = Callable[[Path], HeartbeatResult]
SyntheticInventoryRunner = Callable[[Path, Path], SyntheticInventoryResult]
ConnectionProbeRunner = Callable[[Path], ConnectionProbeResult]
AudiobookshelfInventoryRunner = Callable[[Path, Path], InventoryRunResult]

_FAILURE_MESSAGES = {
    StateCheckOutcome.INVALID: "State check failed: invalid state.\n",
    StateCheckOutcome.UNAVAILABLE: "State check failed: state is unavailable.\n",
    StateCheckOutcome.INTEGRITY_FAILURE: (
        "State check failed: state integrity could not be verified.\n"
    ),
    StateCheckOutcome.CORRUPT: "State check failed: state is corrupt or incompatible.\n",
    StateCheckOutcome.READBACK_MISMATCH: (
        "State check failed: audit readback did not match.\n"
    ),
    StateCheckOutcome.SETUP_FAILURE: "State check failed: state could not be verified.\n",
    StateCheckOutcome.INTERNAL_FAILURE: "State check failed: internal diagnostic error.\n",
}
_HEARTBEAT_FAILURE = (
    "Job heartbeat failed: durable synthetic recovery could not be verified.\n"
)
_INVENTORY_FAILURE = (
    "Synthetic inventory failed: bounded report-only inventory could not be verified.\n"
)
_CONNECTION_FAILURE = (
    "Connection check failed: configured target could not be verified.\n"
)
_ABS_INVENTORY_FAILURE = "Audiobookshelf inventory failed safely.\n"


def _database_path(value: str) -> Path:
    path = Path(value)
    if path == Path(":memory:"):
        raise argparse.ArgumentTypeError("database must be a durable filesystem path")
    return path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mechanical-librarian",
        description="Mechanical Librarian operator diagnostics",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    state_check = commands.add_parser(
        "state-check",
        help="create or open state and verify durable audit readback",
    )
    state_check.add_argument(
        "--database", required=True, metavar="PATH", type=_database_path
    )
    job_heartbeat = commands.add_parser(
        "job-heartbeat",
        help="run the bounded report-only durable job recovery demonstration",
    )
    job_heartbeat.add_argument(
        "--database", required=True, metavar="PATH", type=_database_path
    )
    inventory = commands.add_parser(
        "synthetic-inventory",
        help="persist and inspect one bounded report-only synthetic inventory item",
    )
    inventory.add_argument("--config", required=True, metavar="PATH", type=Path)
    inventory.add_argument(
        "--database", required=True, metavar="PATH", type=_database_path
    )
    connection = commands.add_parser(
        "connection-check",
        help="probe one configured target authorization and compatibility",
    )
    connection.add_argument("--config", required=True, metavar="PATH", type=Path)
    abs_inventory = commands.add_parser(
        "audiobookshelf-inventory",
        help="run one bounded complete-only Audiobookshelf inventory reconciliation",
    )
    abs_inventory.add_argument("--config", required=True, metavar="PATH", type=Path)
    abs_inventory.add_argument(
        "--database", required=True, metavar="PATH", type=_database_path
    )
    inventory_inspect = commands.add_parser(
        "inventory-inspect",
        help="inspect durable Audiobookshelf inventory scan state without target access",
    )
    inventory_inspect.add_argument("--config", required=True, metavar="PATH", type=Path)
    inventory_inspect.add_argument(
        "--database", required=True, metavar="PATH", type=_database_path
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    state_check_runner: StateCheckRunner,
    job_heartbeat_runner: JobHeartbeatRunner | None = None,
    synthetic_inventory_runner: SyntheticInventoryRunner | None = None,
    connection_probe_runner: ConnectionProbeRunner | None = None,
    audiobookshelf_inventory_runner: AudiobookshelfInventoryRunner | None = None,
    inventory_inspection_runner: AudiobookshelfInventoryRunner | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run one parsed command without exposing paths or exception values."""

    output = sys.stdout if stdout is None else stdout
    errors = sys.stderr if stderr is None else stderr
    arguments = _parser().parse_args(argv)
    if arguments.command in {"audiobookshelf-inventory", "inventory-inspect"}:
        runner = (
            audiobookshelf_inventory_runner
            if arguments.command == "audiobookshelf-inventory"
            else inventory_inspection_runner
        )
        if runner is None:
            errors.write(_ABS_INVENTORY_FAILURE)
            return 1
        try:
            result = runner(arguments.config, arguments.database)
        except Exception:
            errors.write(_ABS_INVENTORY_FAILURE)
            return 1
        if (
            type(result) is not InventoryRunResult
            or type(result.outcome) is not InventoryOutcome
        ):
            errors.write(_ABS_INVENTORY_FAILURE)
            return 1
        destination = output if result.succeeded else errors
        destination.write(
            "Audiobookshelf inventory passed.\n"
            if result.succeeded
            else _ABS_INVENTORY_FAILURE
        )
        destination.write("authority=target-inventory-get-only\n")
        for index, library in enumerate(result.libraries, start=1):
            inspection = library.inspection
            if inspection is None:
                continue
            failure = (
                "none" if inspection.failure_kind is None else inspection.failure_kind
            )
            destination.write(
                f"library={index} scope=configured-library "
                f"state={inspection.state.value} failure={failure}\n"
            )
            destination.write(
                f"items={inspection.item_count} active={inspection.active_count} "
                f"removed={inspection.removed_count} history={inspection.history_count} "
                f"moved={inspection.moved_count} pages={inspection.page_count} "
                f"bytes={inspection.response_bytes}\n"
            )
        return 0 if result.succeeded else 1
    if arguments.command == "connection-check":
        if connection_probe_runner is None:
            errors.write(_CONNECTION_FAILURE)
            return 1
        try:
            result = connection_probe_runner(arguments.config)
        except Exception:
            errors.write(_CONNECTION_FAILURE)
            return 1
        if (
            type(result) is not ConnectionProbeResult
            or type(result.outcome) is not ConnectionProbeOutcome
            or not result.succeeded
            or type(result.inspection) is not ConnectionInspection
        ):
            errors.write(_CONNECTION_FAILURE)
            return 1
        inspection = result.inspection
        version = (
            "unknown"
            if inspection.service_version is None
            else inspection.service_version.value
        )
        output.write("Connection check passed.\n")
        output.write("authority=target-connection-read-only\n")
        output.write("adapter=audiobookshelf scope=configured-target\n")
        output.write(
            f"compatibility={inspection.compatibility.value} version={version}\n"
        )
        output.write(f"health={inspection.health.status.value}\n")
        output.write(
            f"lineage={inspection.adapter_contract.value}/"
            f"{inspection.fixture_revision.value}\n"
        )
        output.write(
            "capabilities="
            + ",".join(
                f"{item.capability.value}:{item.state.value}"
                for item in inspection.capabilities
            )
            + "\n"
        )
        return 0
    if arguments.command == "synthetic-inventory":
        if synthetic_inventory_runner is None:
            errors.write(_INVENTORY_FAILURE)
            return 1
        try:
            result = synthetic_inventory_runner(arguments.config, arguments.database)
        except Exception:
            errors.write(_INVENTORY_FAILURE)
            return 1
        if (
            type(result) is not SyntheticInventoryResult
            or type(result.outcome) is not SyntheticInventoryOutcome
            or not result.succeeded
            or type(result.inspection) is not SyntheticInventoryInspection
        ):
            errors.write(_INVENTORY_FAILURE)
            return 1
        inspection = result.inspection
        output.write("Synthetic inventory passed.\n")
        output.write("authority=synthetic-inventory-read-only\n")
        output.write(
            "scope="
            f"{inspection.adapter.value}/{inspection.connection.value}/"
            f"{inspection.library.value}\n"
        )
        output.write(
            f"snapshot={inspection.snapshot.value} "
            f"fingerprint={inspection.fingerprint.schema.value}:"
            f"{inspection.fingerprint.digest}\n"
        )
        output.write(
            f"items={inspection.item_count} history={inspection.history_count} "
            f"current={inspection.current_count}\n"
        )
        output.write(f"persistence={inspection.repository_outcome.value}\n")
        return 0
    if arguments.command == "job-heartbeat":
        if job_heartbeat_runner is None:
            errors.write(_HEARTBEAT_FAILURE)
            return 1
        try:
            result = job_heartbeat_runner(arguments.database)
        except Exception:
            errors.write(_HEARTBEAT_FAILURE)
            return 1
        if (
            type(result) is not HeartbeatResult
            or type(result.outcome) is not HeartbeatOutcome
            or not result.succeeded
            or type(result.inspection) is not HeartbeatInspection
        ):
            errors.write(_HEARTBEAT_FAILURE)
            return 1
        output.write("Job heartbeat passed.\n")
        output.write(f"checks={','.join(result.checks)}\n")
        output.write("authority=synthetic-report-only\n")
        inspection = result.inspection
        output.write(
            "source="
            f"{inspection.source_id.value} state={inspection.source_state.value} "
            f"version={inspection.source_version} "
            f"digest={inspection.source_digest}\n"
        )
        output.write(
            "replacement="
            f"{inspection.replacement_id.value} state={inspection.replacement_state.value} "
            f"version={inspection.replacement_version} "
            f"digest={inspection.replacement_digest}\n"
        )
        output.write(
            f"retry={inspection.retry_state.value} attempts={inspection.retry_attempts}\n"
        )
        output.write(
            "cancellation="
            f"{inspection.cancellation_state.value} "
            f"job={inspection.cancelled_job_state.value}\n"
        )
        required_states = ",".join(
            f"{stage.value}:{state.value}"
            for stage, state in inspection.required_stage_states
        )
        output.write(
            "required-cancellation="
            f"{inspection.required_cancellation_state.value} stages={required_states}\n"
        )
        output.write(f"negative-fan-in={inspection.negative_state.value}\n")
        return 0
    try:
        result = state_check_runner(arguments.database)
    except Exception:
        errors.write(_FAILURE_MESSAGES[StateCheckOutcome.INTERNAL_FAILURE])
        return 1
    if (
        type(result) is not StateCheckResult
        or type(result.outcome) is not StateCheckOutcome
    ):
        errors.write(_FAILURE_MESSAGES[StateCheckOutcome.INTERNAL_FAILURE])
        return 1
    if result.succeeded:
        output.write("State check passed.\n")
        return 0
    errors.write(
        _FAILURE_MESSAGES.get(
            result.outcome,
            _FAILURE_MESSAGES[StateCheckOutcome.INTERNAL_FAILURE],
        )
    )
    return 1


__all__ = [
    "JobHeartbeatRunner",
    "StateCheckRunner",
    "SyntheticInventoryRunner",
    "AudiobookshelfInventoryRunner",
    "main",
]
