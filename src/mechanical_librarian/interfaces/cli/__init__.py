"""Sole installed command-line interface for neutral metadata evidence."""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TextIO

from mechanical_librarian.application.configuration import ConfigurationError
from mechanical_librarian.application.metadata_report import (
    CredentialError,
    CurrentnessError,
    UnsupportedMediaError,
)
from mechanical_librarian.domain.metadata_report import MetadataReport, ReportValueError
from mechanical_librarian.domain.proposal_set import ProposalSet, ProposalValueError
from mechanical_librarian.domain.review_case import ReviewAdmissionFailure
from mechanical_librarian.application.review_cases import ReviewCaseError

Runner = Callable[[Path, Path, Path, str, str, str, int], str]
ReopenRunner = Callable[[Path, str], str]
CurrentReopenRunner = Callable[[Path, str, str, str, str], str]
PreviewRunner = Callable[[Path, Path, str, str, str], bytes]
CatalogueRunner = Callable[[Path, Path, str, str | None, str | None, str | None, str | None, int], str]
BatchPreviewRunner = Callable[[Path, Path, Path, tuple[tuple[str, str, str, str], ...], bool], str]
BatchInspectRunner = Callable[[Path], str]
BatchRunRunner = Callable[[Path, Path, Path, str, Path, Path, Callable[[dict[str, object]], None]], bool]
ProposalCreateRunner = Callable[[Path, Path, Path, str], str]
ProposalGetRunner = Callable[[Path, str], str]
ReviewAdmitRunner = Callable[[Path, Path, Path, str], str]
ReviewGetRunner = Callable[[Path, str], str]
_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class _ArgumentFailure(ValueError):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _ArgumentFailure("invalid invocation")


def _name(value: str) -> str:
    if _NAME.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("invalid name")
    return value


def _item(value: str) -> str:
    if not 1 <= len(value) <= 512 or value.strip() != value or any(character in value for character in "\x00/\\") or any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value):
        raise argparse.ArgumentTypeError("invalid item")
    return value


def _index(value: str) -> int:
    try:
        result = int(value, 10)
    except ValueError:
        raise argparse.ArgumentTypeError("invalid index") from None
    if str(result) != value or not 0 <= result <= 10_000:
        raise argparse.ArgumentTypeError("invalid index")
    return result


def _parser() -> _Parser:
    parser = _Parser(prog="mechanical-librarian", description="Neutral metadata evidence reporting")
    commands = parser.add_subparsers(dest="command", required=True)
    report = commands.add_parser("metadata-report", help="observe and persist one selected EPUB metadata report")
    report.add_argument("--config", required=True, metavar="PATH", type=Path)
    report.add_argument("--database", required=True, metavar="PATH", type=Path)
    report.add_argument("--artifact-store", required=True, metavar="PATH", type=Path)
    report.add_argument("--connection", required=True, metavar="NAME", type=_name)
    report.add_argument("--library", required=True, metavar="NAME", type=_name)
    report.add_argument("--item", required=True, metavar="ITEM_ID", type=_item)
    report.add_argument("--file-index", required=True, metavar="INDEX", type=_index)
    reopen = commands.add_parser("metadata-report-get", help="reopen one persisted metadata report")
    reopen.add_argument("--database", required=True, metavar="PATH", type=Path)
    selector = reopen.add_mutually_exclusive_group(required=True)
    selector.add_argument("--report-id", metavar="REPORT_ID", type=_report_id)
    selector.add_argument("--current", action="store_true")
    reopen.add_argument("--connection", type=_name)
    reopen.add_argument("--library", type=_name)
    reopen.add_argument("--item", type=_item)
    reopen.add_argument("--file-identity", metavar="SHA256")
    preview = commands.add_parser("metadata-report-cover-preview", help="read one report-bound cover preview")
    preview.add_argument("--database", required=True, metavar="PATH", type=Path)
    preview.add_argument("--artifact-store", required=True, metavar="PATH", type=Path)
    preview.add_argument("--report-id", required=True, metavar="REPORT_ID", type=_report_id)
    preview.add_argument("--artifact-id", required=True, metavar="ARTIFACT_ID", type=_artifact_id)
    preview.add_argument("--preview-variant", required=True, metavar="VARIANT", type=_preview_variant)
    proposal = commands.add_parser(
        "proposal-set-create",
        help="create or reopen one ProposalSet from an exact current report",
    )
    proposal.add_argument("--config", required=True, metavar="PATH", type=Path)
    proposal.add_argument("--database", required=True, metavar="PATH", type=Path)
    proposal.add_argument(
        "--artifact-store", required=True, metavar="PATH", type=Path
    )
    proposal.add_argument(
        "--report-id", required=True, metavar="REPORT_ID", type=_report_id
    )
    proposal_get = commands.add_parser(
        "proposal-set-get", help="reopen one exact persisted ProposalSet"
    )
    proposal_get.add_argument(
        "--database", required=True, metavar="PATH", type=Path
    )
    review_admit = commands.add_parser("review-case-admit", help="admit one exact current ProposalSet")
    review_admit.add_argument("--config", required=True, metavar="PATH", type=Path)
    review_admit.add_argument("--database", required=True, metavar="PATH", type=Path)
    review_admit.add_argument("--artifact-store", required=True, metavar="PATH", type=Path)
    review_admit.add_argument("--proposal-set-id", required=True, metavar="PROPOSAL_SET_ID", type=_proposal_set_id)
    review_get = commands.add_parser("review-case-get", help="reopen one immutable review case")
    review_get.add_argument("--database", required=True, metavar="PATH", type=Path)
    review_get.add_argument("--review-case-id", required=True, metavar="REVIEW_CASE_ID", type=lambda value: value if re.fullmatch(r"review_case_[0-9a-f]{64}", value) else (_ for _ in ()).throw(argparse.ArgumentTypeError("invalid review case id")))
    proposal_get.add_argument(
        "--proposal-set-id",
        required=True,
        metavar="PROPOSAL_SET_ID",
        type=_proposal_set_id,
    )
    catalogue = commands.add_parser("catalogue", help="refresh or inspect the current book catalogue")
    catalogue.add_argument("--config", required=True, metavar="PATH", type=Path)
    catalogue.add_argument("--database", required=True, metavar="PATH", type=Path)
    catalogue.add_argument("action", choices=("refresh", "list", "get"))
    catalogue.add_argument("--connection", type=_name)
    catalogue.add_argument("--library", type=_name)
    catalogue.add_argument("--library-id", type=_item)
    catalogue.add_argument("--item", type=_item)
    catalogue.add_argument("--filter", choices=("eligible", "ineligible", "ebook", "audio"))
    catalogue.add_argument("--limit", type=_catalogue_limit, default=100)
    batch = commands.add_parser("batch", help="preview, inspect, or run one immutable foreground evidence batch")
    batch_commands = batch.add_subparsers(dest="batch_action", required=True)
    preview_batch = batch_commands.add_parser("preview", help="create one immutable batch manifest")
    preview_batch.add_argument("--config", required=True, metavar="PATH", type=Path)
    preview_batch.add_argument("--catalogue", required=True, metavar="PATH", type=Path)
    preview_batch.add_argument("--manifest", required=True, metavar="PATH", type=Path)
    group = preview_batch.add_mutually_exclusive_group(required=True)
    group.add_argument("--select", action="append", nargs=4, type=_opaque, default=[])
    group.add_argument("--all-eligible", action="store_true")
    inspect_batch = batch_commands.add_parser("inspect", help="inspect one immutable batch manifest")
    inspect_batch.add_argument("--manifest", required=True, metavar="PATH", type=Path)
    run_batch = batch_commands.add_parser("run", help="run one immutable foreground batch")
    run_batch.add_argument("--config", required=True, metavar="PATH", type=Path)
    run_batch.add_argument("--catalogue", required=True, metavar="PATH", type=Path)
    run_batch.add_argument("--manifest", required=True, metavar="PATH", type=Path)
    run_batch.add_argument("--manifest-id", required=True, metavar="SHA256")
    run_batch.add_argument("--database", required=True, metavar="PATH", type=Path)
    run_batch.add_argument("--artifact-store", required=True, metavar="PATH", type=Path)
    return parser


def _report_id(value: str) -> str:
    if re.fullmatch(r"report_[0-9a-f]{64}", value) is None:
        raise argparse.ArgumentTypeError("invalid report id")
    return value


def _artifact_id(value: str) -> str:
    if re.fullmatch(r"cover_[0-9a-f]{64}", value) is None:
        raise argparse.ArgumentTypeError("invalid artifact id")
    return value


def _proposal_set_id(value: str) -> str:
    if re.fullmatch(r"proposal_set_[0-9a-f]{64}", value) is None:
        raise argparse.ArgumentTypeError("invalid proposal set id")
    return value


def _preview_variant(value: str) -> str:
    if value != "preview-320x480-rgba-png-v1":
        raise argparse.ArgumentTypeError("invalid preview variant")
    return value


def _catalogue_limit(value: str) -> int:
    try:
        result = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("invalid catalogue limit") from error
    if not 1 <= result <= 500:
        raise argparse.ArgumentTypeError("invalid catalogue limit")
    return result


def _opaque(value: str) -> str:
    if not 1 <= len(value) <= 512 or value.strip() != value or any(character in value for character in "\x00/\\") or any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value):
        raise argparse.ArgumentTypeError("invalid selection")
    return value


def main(argv: Sequence[str] | None = None, *, runner: Runner | None = None, reopen_runner: ReopenRunner | None = None, current_reopen_runner: CurrentReopenRunner | None = None, preview_runner: PreviewRunner | None = None, catalogue_runner: CatalogueRunner | None = None, batch_preview_runner: BatchPreviewRunner | None = None, batch_inspect_runner: BatchInspectRunner | None = None, batch_run_runner: BatchRunRunner | None = None, proposal_create_runner: ProposalCreateRunner | None = None, proposal_get_runner: ProposalGetRunner | None = None, review_admit_runner: ReviewAdmitRunner | None = None, review_get_runner: ReviewGetRunner | None = None, stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    output = sys.stdout if stdout is None else stdout
    errors = sys.stderr if stderr is None else stderr
    raw_arguments = tuple(sys.argv[1:] if argv is None else argv)
    try:
        arguments = _parser().parse_args(list(raw_arguments))
    except _ArgumentFailure:
        if raw_arguments[:1] in {("review-case-admit",), ("review-case-get",)}:
            errors.write(ReviewAdmissionFailure("invalid").to_json() + "\n")
        else:
            errors.write("Metadata report failed: invalid invocation.\n")
        return 2
    if arguments.command in {"review-case-admit", "review-case-get"}:
        try:
            serialized = (review_admit_runner(arguments.config, arguments.database, arguments.artifact_store, arguments.proposal_set_id) if arguments.command == "review-case-admit" and review_admit_runner else review_get_runner(arguments.database, arguments.review_case_id) if arguments.command == "review-case-get" and review_get_runner else None)
            if type(serialized) is not str or json.loads(serialized) is None:
                raise ValueError
        except ReviewCaseError as error:
            errors.write(ReviewAdmissionFailure(error.code).to_json() + "\n")
            return 1
        except Exception:
            errors.write(ReviewAdmissionFailure("persistence").to_json() + "\n")
            return 1
        output.write(serialized + "\n")
        return 0
    if arguments.command in {"proposal-set-create", "proposal-set-get"}:
        try:
            if arguments.command == "proposal-set-create":
                if proposal_create_runner is None:
                    raise ValueError
                serialized = proposal_create_runner(
                    arguments.config,
                    arguments.database,
                    arguments.artifact_store,
                    arguments.report_id,
                )
                proposal_set = ProposalSet.from_json(serialized)
                if proposal_set.report_id != arguments.report_id:
                    raise ProposalValueError("proposal output is invalid")
            else:
                if proposal_get_runner is None:
                    raise ValueError
                serialized = proposal_get_runner(
                    arguments.database, arguments.proposal_set_id
                )
                proposal_set = ProposalSet.from_json(serialized)
                if proposal_set.proposal_set_id != arguments.proposal_set_id:
                    raise ProposalValueError("proposal output is invalid")
            if proposal_set.to_json() != serialized:
                raise ProposalValueError("proposal output is invalid")
        except Exception:
            errors.write("Proposal set failed safely.\n")
            return 1
        output.write(serialized + "\n")
        return 0
    if arguments.command == "batch":
        try:
            if arguments.batch_action == "preview":
                if batch_preview_runner is None:
                    raise ValueError
                serialized = batch_preview_runner(arguments.config, arguments.catalogue, arguments.manifest, tuple(tuple(value) for value in arguments.select), arguments.all_eligible)
            elif arguments.batch_action == "inspect":
                if batch_inspect_runner is None:
                    raise ValueError
                serialized = batch_inspect_runner(arguments.manifest)
            else:
                if batch_run_runner is None:
                    raise ValueError
                if re.fullmatch(r"[0-9a-f]{64}", arguments.manifest_id) is None:
                    raise ValueError
                def emit(line: dict[str, object]) -> None:
                    import json
                    output.write(json.dumps(line, separators=(",", ":")) + "\n")
                    output.flush()
                ok = batch_run_runner(arguments.config, arguments.catalogue, arguments.manifest, arguments.manifest_id, arguments.database, arguments.artifact_store, emit)
                return 0 if ok else 1
        except (ConfigurationError, CredentialError):
            errors.write("Batch failed: configuration or credential unavailable.\n")
            return 3
        except Exception:
            errors.write("Batch failed safely.\n")
            return 1
        output.write(serialized + "\n")
        return 0
    if arguments.command == "catalogue":
        if catalogue_runner is None or (arguments.action == "get" and None in (arguments.connection, arguments.library, arguments.library_id, arguments.item)):
            errors.write("Catalogue failed safely.\n")
            return 1
        try:
            serialized = catalogue_runner(arguments.config, arguments.database, arguments.action, arguments.connection, arguments.library, arguments.library_id, arguments.item if arguments.action == "get" else arguments.filter, arguments.limit)
        except (ConfigurationError, CredentialError):
            errors.write("Catalogue failed: configuration or credential unavailable.\n")
            return 3
        except Exception:
            errors.write("Catalogue failed safely.\n")
            return 1
        output.write(serialized + "\n")
        return 0
    if arguments.command == "metadata-report-cover-preview":
        if preview_runner is None or getattr(output, "isatty", lambda: False)():
            errors.write("Metadata report failed safely.\n")
            return 1
        try:
            preview = preview_runner(arguments.database, arguments.artifact_store, arguments.report_id, arguments.artifact_id, arguments.preview_variant)
            if type(preview) is not bytes or not preview:
                raise ValueError
            output.buffer.write(preview) if hasattr(output, "buffer") else output.write(preview.decode("latin1"))
        except Exception:
            errors.write("Metadata report failed safely.\n")
            return 1
        return 0
    if arguments.command == "metadata-report-get":
        if (arguments.current and current_reopen_runner is None) or (not arguments.current and reopen_runner is None):
            errors.write("Metadata report failed safely.\n")
            return 1
        try:
            if arguments.current:
                if None in (arguments.connection, arguments.library, arguments.item, arguments.file_identity) or re.fullmatch(r"[0-9a-f]{64}", arguments.file_identity) is None:
                    raise ValueError
                serialized = current_reopen_runner(arguments.database, arguments.connection, arguments.library, arguments.item, arguments.file_identity)
            else:
                serialized = reopen_runner(arguments.database, arguments.report_id)
            report_id = _persisted_report_id(serialized)
            if not arguments.current and report_id != arguments.report_id:
                raise ReportValueError("report output is invalid")
        except Exception:
            errors.write("Metadata report failed safely.\n")
            return 1
        output.write(serialized)
        output.write("\n")
        return 0
    if runner is None:
        errors.write("Metadata report failed safely.\n")
        return 1
    try:
        serialized = runner(arguments.config, arguments.database, arguments.artifact_store, arguments.connection, arguments.library, arguments.item, arguments.file_index)
        report = MetadataReport.from_json(serialized)
        if report.to_json() != serialized:
            raise ReportValueError("report output is invalid")
    except UnsupportedMediaError:
        errors.write("Metadata report failed: unsupported media.\n")
        return 4
    except (ConfigurationError, CredentialError):
        errors.write("Metadata report failed: configuration or credential unavailable.\n")
        return 3
    except CurrentnessError:
        errors.write("Metadata report failed: source changed before publication.\n")
        return 5
    except Exception:
        errors.write("Metadata report failed safely.\n")
        return 1
    output.write(serialized)
    output.write("\n")
    return 0


def _persisted_report_id(serialized: object) -> str:
    """Validate the one supported persisted report model."""
    report = MetadataReport.from_json(serialized)
    if report.to_json() != serialized:
        raise ReportValueError("report output is invalid")
    return report.report_id


__all__ = ["main"]
