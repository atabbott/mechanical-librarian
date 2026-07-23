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
    MetadataReportError,
    UnsupportedMediaError,
)
from mechanical_librarian.domain.metadata_report import MetadataReport, ReportValueError


Runner = Callable[[Path, Path, str, str, str, int], str]
ReopenRunner = Callable[[Path, str], str]
CurrentReopenRunner = Callable[[Path, str, str, str, str], str]
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
    return parser


def _report_id(value: str) -> str:
    if re.fullmatch(r"report_[0-9a-f]{64}", value) is None:
        raise argparse.ArgumentTypeError("invalid report id")
    return value


def main(argv: Sequence[str] | None = None, *, runner: Runner | None = None, reopen_runner: ReopenRunner | None = None, current_reopen_runner: CurrentReopenRunner | None = None, stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    output = sys.stdout if stdout is None else stdout
    errors = sys.stderr if stderr is None else stderr
    try:
        arguments = _parser().parse_args(None if argv is None else list(argv))
    except _ArgumentFailure:
        errors.write("Metadata report failed: invalid invocation.\n")
        return 2
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
        serialized = runner(arguments.config, arguments.database, arguments.connection, arguments.library, arguments.item, arguments.file_index)
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
    """Validate only the immutable v1 envelope, or the full current v2 model."""
    if type(serialized) is not str:
        raise ReportValueError("report output is invalid")
    try:
        raw = json.loads(serialized)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ReportValueError("report output is invalid") from error
    if type(raw) is not dict or type(raw.get("schema_version")) is not str or type(raw.get("report_id")) is not str:
        raise ReportValueError("report output is invalid")
    if raw["schema_version"] == "metadata-report-v1":
        return raw["report_id"]
    report = MetadataReport.from_json(serialized)
    if report.to_json() != serialized:
        raise ReportValueError("report output is invalid")
    return report.report_id


__all__ = ["main"]
