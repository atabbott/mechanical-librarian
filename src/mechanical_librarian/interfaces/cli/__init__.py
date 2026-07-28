"""Sole installed command-line interface for neutral metadata evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TextIO

from mechanical_librarian.application.configuration import ConfigurationError
from mechanical_librarian.application.failure_diagnostics import (
    FailureDiagnostic,
    diagnostics_are_canonical,
    reviewer_failure_diagnostic,
)
from mechanical_librarian.application.metadata_report import (
    CredentialError,
    CurrentnessError,
    MetadataReportError,
    UnsupportedMediaError,
)
from mechanical_librarian.domain.metadata_report import MetadataReport, ReportValueError
from mechanical_librarian.domain.proposal_set import ProposalSet, ProposalValueError
from mechanical_librarian.domain.review_case import (
    MAX_REVIEW_CASE_BYTES,
    ReviewAdmissionFailure,
    ReviewAdmissionResult,
    ReviewCase,
)
from mechanical_librarian.application.review_cases import ReviewCaseError
from mechanical_librarian.application.shadow_reviews import ShadowReviewError
from mechanical_librarian.domain.shadow_review import (
    ShadowReviewRequest,
    ShadowReviewResult,
    ShadowReviewValueError,
    canonical_json as shadow_canonical_json,
)

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
ReviewerAuthRunner = Callable[[Path, str], str]
ShadowPreviewRunner = Callable[[Path, str, Path], str]
ShadowRequestRunner = Callable[[Path, str, Path], str]
ShadowInspectRunner = Callable[[Path, str, Path], str]
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
    for action in ("start", "status", "logout"):
        auth = commands.add_parser(
            f"reviewer-auth-{action}",
            help=f"{action} the exact qualification ChatGPT subscription session",
        )
        auth.add_argument(
            "--qualification-profile", required=True, metavar="PATH", type=Path
        )
    shadow_preview = commands.add_parser(
        "shadow-review-preview",
        help="preview one sanitized exact-case reviewer packet without egress",
    )
    shadow_preview.add_argument("--database", required=True, metavar="PATH", type=Path)
    shadow_preview.add_argument(
        "--review-case-id",
        required=True,
        metavar="REVIEW_CASE_ID",
        type=_review_case_id,
    )
    shadow_preview.add_argument(
        "--qualification-profile", required=True, metavar="PATH", type=Path
    )
    shadow_request = commands.add_parser(
        "shadow-review-request",
        help="run one qualification-only reviewer attempt",
    )
    shadow_request.add_argument("--database", required=True, metavar="PATH", type=Path)
    shadow_request.add_argument(
        "--review-case-id",
        required=True,
        metavar="REVIEW_CASE_ID",
        type=_review_case_id,
    )
    shadow_request.add_argument(
        "--qualification-profile", required=True, metavar="PATH", type=Path
    )
    for action in ("inspect", "reevaluate"):
        shadow = commands.add_parser(
            f"shadow-review-{action}",
            help=f"{action} one exact qualification-only review request",
        )
        shadow.add_argument("--database", required=True, metavar="PATH", type=Path)
        shadow.add_argument(
            "--review-request-id",
            required=True,
            metavar="REVIEW_REQUEST_ID",
            type=_review_request_id,
        )
        shadow.add_argument(
            "--qualification-profile", required=True, metavar="PATH", type=Path
        )
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


def _review_case_id(value: str) -> str:
    if re.fullmatch(r"review_case_[0-9a-f]{64}", value) is None:
        raise argparse.ArgumentTypeError("invalid review case id")
    return value


def _review_request_id(value: str) -> str:
    if re.fullmatch(r"review_request_[0-9a-f]{64}", value) is None:
        raise argparse.ArgumentTypeError("invalid review request id")
    return value


def _validated_review_get(serialized: str, review_case_id: str) -> str:
    if (
        type(serialized) is not str
        or not serialized
        or len(serialized.encode("utf-8")) > MAX_REVIEW_CASE_BYTES + 128
    ):
        raise ValueError("review case output is invalid")
    raw = json.loads(serialized)
    if (
        type(raw) is not dict
        or set(raw) != {"case", "current", "schema_version"}
        or raw.get("schema_version") != "review-case-get-v1"
        or type(raw.get("current")) is not bool
        or type(raw.get("case")) is not dict
    ):
        raise ValueError("review case output is invalid")
    case = ReviewCase.create_from_data(raw["case"])
    if case.review_case_id != review_case_id:
        raise ValueError("review case output is invalid")
    canonical = json.dumps(
        {
            "case": case.to_data(),
            "current": raw["current"],
            "schema_version": "review-case-get-v1",
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if canonical != serialized:
        raise ValueError("review case output is invalid")
    return canonical


def _validated_shadow_output(
    command: str,
    serialized: str,
    *,
    review_case_id: str | None = None,
    review_request_id: str | None = None,
) -> str:
    maximum = 1_200_000 if command == "shadow-review-preview" else 12_000_000
    try:
        if (
            type(serialized) is not str
            or not serialized
            or len(serialized.encode("utf-8")) > maximum
        ):
            raise ValueError
        raw = json.loads(
            serialized,
            object_pairs_hook=_unique_cli_object,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
        if type(raw) is not dict or shadow_canonical_json(raw) != serialized:
            raise ValueError
        if command.startswith("reviewer-auth-"):
            expected = {
                "reviewer-auth-start": "started",
                "reviewer-auth-status": "chatgpt",
                "reviewer-auth-logout": "logged_out",
            }[command]
            if (
                set(raw) != {"mode", "schema_version", "status"}
                or raw["mode"] != "qualification_only"
                or raw["schema_version"] != "reviewer-auth-v1"
                or raw["status"] != expected
            ):
                raise ValueError
            return serialized
        if command == "shadow-review-preview":
            if (
                set(raw) != {"mode", "request", "schema_version", "status"}
                or raw["mode"] != "qualification_only"
                or raw["schema_version"] != "shadow-review-preview-v1"
                or raw["status"] != "preview"
                or type(raw["request"]) is not dict
            ):
                raise ValueError
            request = ShadowReviewRequest.from_source_validated_json(
                shadow_canonical_json(raw["request"])
            )
            if request.review_case_id != review_case_id:
                raise ValueError
            return serialized
        if (
            set(raw)
            != {
                "attempt_count",
                "current",
                "diagnostics",
                "mode",
                "review_case_id",
                "review_request_id",
                "schema_version",
                "status",
            }
            or raw["mode"] != "qualification_only"
            or raw["schema_version"] != "shadow-review-inspect-v2"
            or type(raw["current"]) is not bool
            or type(raw["attempt_count"]) is not int
            or not 1 <= raw["attempt_count"] <= 32
            or type(raw["diagnostics"]) is not list
            or len(raw["diagnostics"]) > 20
            or (
                review_case_id is not None
                and raw["review_case_id"] != review_case_id
            )
            or type(raw["review_case_id"]) is not str
            or re.fullmatch(
                r"review_case_[0-9a-f]{64}", raw["review_case_id"]
            )
            is None
            or (
                review_request_id is not None
                and raw["review_request_id"] != review_request_id
            )
            or type(raw["review_request_id"]) is not str
            or re.fullmatch(
                r"review_request_[0-9a-f]{64}", raw["review_request_id"]
            )
            is None
            or raw["status"]
            not in {"pending", "unknown", "failed", "qualification_only"}
        ):
            raise ValueError
        diagnostics = tuple(
            FailureDiagnostic.from_json(shadow_canonical_json(value))
            for value in raw["diagnostics"]
        )
        if (
            tuple(value.to_data() for value in diagnostics)
            != tuple(raw["diagnostics"])
            or not diagnostics_are_canonical(diagnostics)
            or (
                raw["status"] in {"failed", "unknown"}
                and not diagnostics
            )
        ):
            raise ValueError
        return serialized
    except (
        KeyError,
        TypeError,
        ValueError,
        UnicodeError,
        RecursionError,
        json.JSONDecodeError,
        ShadowReviewValueError,
    ) as error:
        raise ShadowReviewError("malformed_output") from error


def _unique_cli_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _shadow_failure(diagnostic: FailureDiagnostic) -> str:
    return json.dumps(
        {
            "diagnostic": diagnostic.to_data(),
            "mode": "qualification_only",
            "schema_version": "shadow-review-failure-v2",
        },
        separators=(",", ":"),
        sort_keys=True,
    )


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


def main(argv: Sequence[str] | None = None, *, runner: Runner | None = None, reopen_runner: ReopenRunner | None = None, current_reopen_runner: CurrentReopenRunner | None = None, preview_runner: PreviewRunner | None = None, catalogue_runner: CatalogueRunner | None = None, batch_preview_runner: BatchPreviewRunner | None = None, batch_inspect_runner: BatchInspectRunner | None = None, batch_run_runner: BatchRunRunner | None = None, proposal_create_runner: ProposalCreateRunner | None = None, proposal_get_runner: ProposalGetRunner | None = None, review_admit_runner: ReviewAdmitRunner | None = None, review_get_runner: ReviewGetRunner | None = None, reviewer_auth_runner: ReviewerAuthRunner | None = None, shadow_preview_runner: ShadowPreviewRunner | None = None, shadow_request_runner: ShadowRequestRunner | None = None, shadow_inspect_runner: ShadowInspectRunner | None = None, shadow_reevaluate_runner: ShadowInspectRunner | None = None, stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    output = sys.stdout if stdout is None else stdout
    errors = sys.stderr if stderr is None else stderr
    raw_arguments = tuple(sys.argv[1:] if argv is None else argv)
    try:
        arguments = _parser().parse_args(list(raw_arguments))
    except _ArgumentFailure:
        if raw_arguments[:1] and (
            raw_arguments[0].startswith("shadow-review-")
            or raw_arguments[0].startswith("reviewer-auth-")
        ):
            errors.write(_shadow_failure(reviewer_failure_diagnostic("profile")) + "\n")
        elif raw_arguments[:1] in {("review-case-admit",), ("review-case-get",)}:
            errors.write(ReviewAdmissionFailure("invalid").to_json() + "\n")
        else:
            errors.write("Metadata report failed: invalid invocation.\n")
        return 2
    if arguments.command.startswith("reviewer-auth-") or arguments.command.startswith(
        "shadow-review-"
    ):
        try:
            if arguments.command.startswith("reviewer-auth-"):
                if reviewer_auth_runner is None:
                    raise ShadowReviewError("runtime")
                serialized = reviewer_auth_runner(
                    arguments.qualification_profile,
                    arguments.command.removeprefix("reviewer-auth-"),
                )
            elif arguments.command == "shadow-review-preview":
                if shadow_preview_runner is None:
                    raise ShadowReviewError("runtime")
                serialized = shadow_preview_runner(
                    arguments.database,
                    arguments.review_case_id,
                    arguments.qualification_profile,
                )
            elif arguments.command == "shadow-review-request":
                if shadow_request_runner is None:
                    raise ShadowReviewError("runtime")
                serialized = shadow_request_runner(
                    arguments.database,
                    arguments.review_case_id,
                    arguments.qualification_profile,
                )
            elif arguments.command == "shadow-review-inspect":
                if shadow_inspect_runner is None:
                    raise ShadowReviewError("runtime")
                serialized = shadow_inspect_runner(
                    arguments.database,
                    arguments.review_request_id,
                    arguments.qualification_profile,
                )
            else:
                if shadow_reevaluate_runner is None:
                    raise ShadowReviewError("runtime")
                serialized = shadow_reevaluate_runner(
                    arguments.database,
                    arguments.review_request_id,
                    arguments.qualification_profile,
                )
            serialized = _validated_shadow_output(
                arguments.command,
                serialized,
                review_case_id=getattr(arguments, "review_case_id", None),
                review_request_id=getattr(
                    arguments, "review_request_id", None
                ),
            )
        except ShadowReviewError as error:
            errors.write(_shadow_failure(error.diagnostic) + "\n")
            return 1
        except MetadataReportError:
            errors.write(
                _shadow_failure(
                    reviewer_failure_diagnostic("persistence")
                )
                + "\n"
            )
            return 1
        except Exception:
            errors.write(_shadow_failure(reviewer_failure_diagnostic("unknown")) + "\n")
            return 1
        output.write(serialized + "\n")
        return 0
    if arguments.command in {"review-case-admit", "review-case-get"}:
        try:
            if arguments.command == "review-case-admit":
                if review_admit_runner is None:
                    raise ValueError
                serialized = review_admit_runner(
                    arguments.config,
                    arguments.database,
                    arguments.artifact_store,
                    arguments.proposal_set_id,
                )
                serialized = ReviewAdmissionResult.from_json(serialized).to_json()
            elif review_get_runner is not None:
                serialized = _validated_review_get(
                    review_get_runner(
                        arguments.database,
                        arguments.review_case_id,
                    ),
                    arguments.review_case_id,
                )
            else:
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
