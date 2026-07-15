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
from mechanical_librarian.application.inventory_polling import (
    InventoryPollOutcome,
    InventoryPollResult,
)
from mechanical_librarian.application.local_evidence import (
    EpubPackageReportRunner,
    internal_failure_report,
    render_local_evidence_text,
)
from mechanical_librarian.application.title_proposals import (
    SCENARIOS,
    SyntheticTitleProposalReport,
    render_title_proposal_text,
)
from mechanical_librarian.domain.local_evidence import (
    EpubPackageLimits,
    LocalEvidenceCapability,
    LocalEvidenceOutcome,
    LocalEvidenceReport,
)
from mechanical_librarian.domain.inventory import SecretReference


StateCheckRunner = Callable[[Path], StateCheckResult]
JobHeartbeatRunner = Callable[[Path], HeartbeatResult]
SyntheticInventoryRunner = Callable[[Path, Path], SyntheticInventoryResult]
ConnectionProbeRunner = Callable[[Path], ConnectionProbeResult]
AudiobookshelfInventoryRunner = Callable[[Path, Path], InventoryRunResult]
AudiobookshelfPollRunner = Callable[[Path, Path, str], InventoryPollResult]
InventoryPollInspectionRunner = Callable[[Path, str], InventoryPollResult]
SyntheticTitleProposalRunner = Callable[[Path, str], SyntheticTitleProposalReport]
HardcoverTitleProposalRunner = Callable[[Path, Path | None], SyntheticTitleProposalReport]

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
_ABS_POLL_FAILURE = "Audiobookshelf poll failed safely.\n"
_TITLE_PROPOSAL_FAILURE = "Synthetic title preview failed safely.\n"
_TITLE_PROPOSAL_INVALID = "Synthetic title preview invocation is invalid.\n"
_HARDCOVER_TITLE_PROPOSAL_FAILURE = "Hardcover title preview failed safely.\n"
_HARDCOVER_TITLE_PROPOSAL_INVALID = "Hardcover title preview invocation is invalid.\n"
_EPUB_PACKAGE_EXIT = {
    LocalEvidenceOutcome.INVALID_INVOCATION: 2,
    LocalEvidenceOutcome.SUCCESS: 0,
    LocalEvidenceOutcome.NO_APPLICABLE_EVIDENCE: 0,
    LocalEvidenceOutcome.INVALID_CONFIGURATION: 3,
    LocalEvidenceOutcome.INVALID_REFERENCE: 3,
    LocalEvidenceOutcome.UNAVAILABLE_SOURCE: 3,
    LocalEvidenceOutcome.UNSUPPORTED_FORMAT: 4,
    LocalEvidenceOutcome.UNSUPPORTED_VERSION: 4,
    LocalEvidenceOutcome.UNSUPPORTED_COMPRESSION: 4,
    LocalEvidenceOutcome.UNSUPPORTED_MULTIPLE_RENDITIONS: 4,
    LocalEvidenceOutcome.ENCRYPTED: 4,
    LocalEvidenceOutcome.MALFORMED: 5,
    LocalEvidenceOutcome.INTEGRITY_FAILURE: 5,
    LocalEvidenceOutcome.RESOURCE_LIMIT: 5,
    LocalEvidenceOutcome.TIMEOUT: 5,
    LocalEvidenceOutcome.CANCELLATION: 5,
    LocalEvidenceOutcome.UNSTABLE_SOURCE: 5,
    LocalEvidenceOutcome.CHILD_FAILURE: 70,
    LocalEvidenceOutcome.INTERNAL_FAILURE: 70,
}


class _EpubArgumentError(ValueError):
    pass


class _EpubArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _EpubArgumentError(message)


class _TitleArgumentError(ValueError):
    pass


class _TitleArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _TitleArgumentError(message)


def _epub_parser() -> argparse.ArgumentParser:
    parser = _EpubArgumentParser(prog="mechanical-librarian epub-package-report")
    parser.add_argument("--config", required=True, metavar="PATH", type=Path)
    parser.add_argument("--mount", required=True, metavar="NAME", type=_bounded_identifier)
    parser.add_argument("--file", required=True, metavar="RELATIVE")
    parser.add_argument("--format", choices=("json", "text"), default="json")
    return parser


def _title_proposal_parser() -> argparse.ArgumentParser:
    parser = _TitleArgumentParser(
        prog="mechanical-librarian synthetic-title-proposal-report"
    )
    parser.add_argument("--database", required=True, metavar="PATH", type=_database_path)
    parser.add_argument("--scenario", required=True, choices=tuple(SCENARIOS))
    parser.add_argument("--format", choices=("json", "text"), default="json")
    return parser


def _hardcover_title_proposal_parser() -> argparse.ArgumentParser:
    parser = _TitleArgumentParser(
        prog="mechanical-librarian hardcover-title-proposal-report"
    )
    parser.add_argument("--database", required=True, metavar="PATH", type=_database_path)
    parser.add_argument(
        "--secret-reference", metavar="PATH", type=_provider_secret_reference
    )
    parser.add_argument("--format", choices=("json", "text"), default="json")
    return parser


def _write_epub_report(
    report: LocalEvidenceReport,
    output_format: str,
    output: TextIO,
    errors: TextIO,
) -> int:
    exit_status = _EPUB_PACKAGE_EXIT[report.outcome]
    destination = output if exit_status == 0 else errors
    destination.write(
        report.to_json() + "\n"
        if output_format == "json"
        else render_local_evidence_text(report)
    )
    return exit_status


def _database_path(value: str) -> Path:
    path = Path(value)
    if path == Path(":memory:"):
        raise argparse.ArgumentTypeError("database must be a durable filesystem path")
    return path


def _provider_secret_reference(value: str) -> Path:
    try:
        reference = SecretReference(value)
        path = Path(reference.value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("secret reference is invalid") from None
    if not path.is_absolute():
        raise argparse.ArgumentTypeError("secret reference must be absolute")
    return path


def _bounded_identifier(value: str) -> str:
    if (
        not 1 <= len(value) <= 64
        or not value[0].islower()
        or any(
            not (character.islower() or character.isdigit() or character in "_-")
            for character in value
        )
    ):
        raise argparse.ArgumentTypeError("identifier is invalid")
    return value


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
    poll = commands.add_parser(
        "audiobookshelf-poll",
        help="run or resume one durable bounded Audiobookshelf poll trigger",
    )
    poll.add_argument("--config", required=True, metavar="PATH", type=Path)
    poll.add_argument(
        "--database", required=True, metavar="PATH", type=_database_path
    )
    poll.add_argument(
        "--trigger", required=True, metavar="ID", type=_bounded_identifier
    )
    poll_inspect = commands.add_parser(
        "inventory-poll-inspect",
        help="inspect durable poll state without configuration, credentials, or target access",
    )
    poll_inspect.add_argument(
        "--database", required=True, metavar="PATH", type=_database_path
    )
    poll_inspect.add_argument(
        "--poll-id", required=True, metavar="ID", type=_bounded_identifier
    )
    epub_package = commands.add_parser(
        "epub-package-report",
        help="emit one bounded cited EPUB package-evidence report",
    )
    epub_package.add_argument("--config", required=True, metavar="PATH", type=Path)
    epub_package.add_argument(
        "--mount", required=True, metavar="NAME", type=_bounded_identifier
    )
    epub_package.add_argument("--file", required=True, metavar="RELATIVE")
    epub_package.add_argument(
        "--format", choices=("json", "text"), default="json"
    )
    title_proposal = commands.add_parser(
        "synthetic-title-proposal-report",
        help="emit one persisted apply-ineligible synthetic title proposal preview",
    )
    title_proposal.add_argument(
        "--database", required=True, metavar="PATH", type=_database_path
    )
    title_proposal.add_argument("--scenario", required=True, choices=tuple(SCENARIOS))
    title_proposal.add_argument("--format", choices=("json", "text"), default="json")
    hardcover_title = commands.add_parser(
        "hardcover-title-proposal-report",
        help="emit one persisted apply-ineligible Hardcover title proof of concept",
    )
    hardcover_title.add_argument(
        "--database", required=True, metavar="PATH", type=_database_path
    )
    hardcover_title.add_argument(
        "--secret-reference", metavar="PATH", type=_provider_secret_reference
    )
    hardcover_title.add_argument("--format", choices=("json", "text"), default="json")
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
    audiobookshelf_poll_runner: AudiobookshelfPollRunner | None = None,
    inventory_poll_inspection_runner: InventoryPollInspectionRunner | None = None,
    epub_package_report_runner: EpubPackageReportRunner | None = None,
    synthetic_title_proposal_runner: SyntheticTitleProposalRunner | None = None,
    hardcover_title_proposal_runner: HardcoverTitleProposalRunner | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run one parsed command without exposing paths or exception values."""

    output = sys.stdout if stdout is None else stdout
    errors = sys.stderr if stderr is None else stderr
    raw_arguments = list(sys.argv[1:] if argv is None else argv)
    if raw_arguments[:1] == ["hardcover-title-proposal-report"]:
        try:
            title_arguments = _hardcover_title_proposal_parser().parse_args(
                raw_arguments[1:]
            )
        except _TitleArgumentError:
            errors.write(_HARDCOVER_TITLE_PROPOSAL_INVALID)
            return 2
        if hardcover_title_proposal_runner is None:
            errors.write(_HARDCOVER_TITLE_PROPOSAL_FAILURE)
            return 1
        try:
            report = hardcover_title_proposal_runner(
                title_arguments.database, title_arguments.secret_reference
            )
        except (KeyboardInterrupt, Exception):
            errors.write(_HARDCOVER_TITLE_PROPOSAL_FAILURE)
            return 1
        if type(report) is not SyntheticTitleProposalReport:
            errors.write(_HARDCOVER_TITLE_PROPOSAL_FAILURE)
            return 1
        try:
            rendered = (
                report.to_json() + "\n"
                if title_arguments.format == "json"
                else render_title_proposal_text(report)
            )
        except Exception:
            errors.write(_HARDCOVER_TITLE_PROPOSAL_FAILURE)
            return 1
        output.write(rendered)
        return 0
    if raw_arguments[:1] == ["synthetic-title-proposal-report"]:
        try:
            title_arguments = _title_proposal_parser().parse_args(raw_arguments[1:])
        except _TitleArgumentError:
            errors.write(_TITLE_PROPOSAL_INVALID)
            return 2
        if synthetic_title_proposal_runner is None:
            errors.write(_TITLE_PROPOSAL_FAILURE)
            return 1
        try:
            report = synthetic_title_proposal_runner(
                title_arguments.database, title_arguments.scenario
            )
        except (KeyboardInterrupt, Exception):
            errors.write(_TITLE_PROPOSAL_FAILURE)
            return 1
        if type(report) is not SyntheticTitleProposalReport:
            errors.write(_TITLE_PROPOSAL_FAILURE)
            return 1
        try:
            rendered = (
                report.to_json() + "\n"
                if title_arguments.format == "json"
                else render_title_proposal_text(report)
            )
        except Exception:
            errors.write(_TITLE_PROPOSAL_FAILURE)
            return 1
        output.write(rendered)
        return 0
    if raw_arguments[:1] == ["epub-package-report"]:
        requested_format = "json"
        index = 1
        while index < len(raw_arguments):
            argument = raw_arguments[index]
            candidate: str | None = None
            if argument.startswith("--format="):
                candidate = argument.partition("=")[2]
            elif argument == "--format" and index + 1 < len(raw_arguments):
                index += 1
                candidate = raw_arguments[index]
            if candidate in {"json", "text"}:
                requested_format = candidate
            index += 1
        try:
            epub_arguments = _epub_parser().parse_args(raw_arguments[1:])
        except _EpubArgumentError:
            return _write_epub_report(
                LocalEvidenceReport(
                    LocalEvidenceOutcome.INVALID_INVOCATION,
                    LocalEvidenceCapability.PACKAGE,
                    EpubPackageLimits(),
                ),
                requested_format,
                output,
                errors,
            )
        try:
            report = (
                internal_failure_report()
                if epub_package_report_runner is None
                else epub_package_report_runner(
                    epub_arguments.config,
                    epub_arguments.mount,
                    epub_arguments.file,
                )
            )
        except KeyboardInterrupt:
            report = LocalEvidenceReport(
                LocalEvidenceOutcome.CANCELLATION,
                LocalEvidenceCapability.PACKAGE,
                EpubPackageLimits(),
            )
        except Exception:
            report = internal_failure_report()
        if type(report) is not LocalEvidenceReport:
            report = internal_failure_report()
        return _write_epub_report(
            report,
            epub_arguments.format,
            output,
            errors,
        )
    arguments = _parser().parse_args(argv)
    if arguments.command in {"audiobookshelf-poll", "inventory-poll-inspect"}:
        runner = (
            audiobookshelf_poll_runner
            if arguments.command == "audiobookshelf-poll"
            else inventory_poll_inspection_runner
        )
        if runner is None:
            errors.write(_ABS_POLL_FAILURE)
            return 1
        try:
            result = (
                runner(arguments.config, arguments.database, arguments.trigger)
                if arguments.command == "audiobookshelf-poll"
                else runner(arguments.database, arguments.poll_id)
            )
        except Exception:
            errors.write(_ABS_POLL_FAILURE)
            return 1
        if (
            type(result) is not InventoryPollResult
            or type(result.outcome) is not InventoryPollOutcome
            or result.inspection is None
        ):
            errors.write(_ABS_POLL_FAILURE)
            return 1
        inspection = result.inspection
        destination = output if result.succeeded else errors
        destination.write(
            "Audiobookshelf poll passed.\n"
            if result.succeeded
            else _ABS_POLL_FAILURE
        )
        destination.write("authority=target-inventory-get-only\n")
        destination.write(
            f"poll={inspection.poll_id} state={inspection.state.value} "
            f"attempt={inspection.attempt}/{inspection.max_attempts} "
            f"fence={inspection.fence}\n"
        )
        destination.write(
            f"pages={inspection.usage.pages} items={inspection.usage.items} "
            f"bytes={inspection.usage.response_bytes} "
            f"compatibility={inspection.compatibility or 'unknown'} "
            f"failure={inspection.failure_kind or 'none'}\n"
        )
        return 0 if result.succeeded else 1
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
    "EpubPackageReportRunner",
    "SyntheticTitleProposalRunner",
    "main",
]
