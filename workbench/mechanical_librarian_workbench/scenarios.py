"""Typed, closed workbench scenarios composed from existing application operations."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

from .context import WorkbenchContext
from .cover_packets import prepare_cover_packet
from .results import WorkbenchResult

Operation = Callable[..., object]


class WorkbenchFailure(Exception):
    """One bounded diagnostic which never includes operation exception text."""

    def __init__(self, category: str) -> None:
        _member(category, {"failed", "invalid", "refused"}, "failure category")
        self.category = category
        super().__init__(f"workbench scenario {category}")


def _member(value: str, allowed: set[str], label: str) -> None:
    if value not in allowed:
        raise ValueError(f"invalid {label}")


def _identifier(value: str | None, label: str) -> None:
    if value is not None and (
        not 1 <= len(value) <= 512
        or value.strip() != value
        or "\x00" in value
        or "/" in value
        or "\\" in value
    ):
        raise ValueError(f"invalid {label}")


def _required(value: str | None, label: str) -> str:
    _identifier(value, label)
    if value is None:
        raise ValueError(f"{label} is required")
    return value


@dataclass(frozen=True)
class ReportRequest:
    action: str
    database: str = "reports.sqlite"
    artifacts: str = "artifacts"
    config: str = "config.json"
    connection: str | None = None
    library: str | None = None
    item: str | None = None
    file_index: int = 0
    report_id: str | None = None
    file_identity: str | None = None
    artifact_id: str | None = None
    preview_variant: str = "preview-320x480-rgba-png-v1"

    def __post_init__(self) -> None:
        _member(self.action, {"observe", "inspect", "inspect-current", "cover"}, "report action")
        for value, label in ((self.connection, "connection"), (self.library, "library"),
                             (self.item, "item"), (self.report_id, "report id"),
                             (self.artifact_id, "artifact id")):
            _identifier(value, label)
        if type(self.file_index) is not int or not 0 <= self.file_index <= 10_000:
            raise ValueError("invalid file index")
        required = {
            "observe": ((self.connection, "connection"), (self.library, "library"),
                        (self.item, "item")),
            "inspect": ((self.report_id, "report id"),),
            "inspect-current": ((self.connection, "connection"), (self.library, "library"),
                                (self.item, "item"), (self.file_identity, "file identity")),
            "cover": ((self.report_id, "report id"), (self.artifact_id, "artifact id")),
        }[self.action]
        for value, label in required:
            _required(value, label)

    @property
    def name(self) -> str:
        return {"observe": "report-observe", "inspect": "report-inspect",
                "inspect-current": "report-inspect", "cover": "cover-inspect"}[self.action]


@dataclass(frozen=True)
class ProposalRequest:
    action: str
    identity: str
    database: str = "reports.sqlite"
    artifacts: str = "artifacts"
    config: str = "config.json"

    def __post_init__(self) -> None:
        _member(self.action, {"create", "inspect"}, "proposal action")
        _required(self.identity, "proposal identity")

    @property
    def name(self) -> str:
        return "proposal-create" if self.action == "create" else "proposal-inspect"


@dataclass(frozen=True)
class ReviewCaseRequest:
    action: str
    identity: str
    database: str = "reports.sqlite"
    artifacts: str = "artifacts"
    config: str = "config.json"

    def __post_init__(self) -> None:
        _member(self.action, {"admit", "inspect"}, "review-case action")
        _required(self.identity, "review-case identity")

    @property
    def name(self) -> str:
        return "case-admit" if self.action == "admit" else "case-inspect"


@dataclass(frozen=True)
class CatalogueRequest:
    action: str
    database: str = "catalogue.sqlite"
    config: str = "config.json"
    connection: str | None = None
    library: str | None = None
    library_id: str | None = None
    selector: str | None = None
    limit: int = 100

    def __post_init__(self) -> None:
        _member(self.action, {"refresh", "list", "get"}, "catalogue action")
        for value, label in ((self.connection, "connection"), (self.library, "library"),
                             (self.library_id, "library id"), (self.selector, "selector")):
            _identifier(value, label)
        if self.action == "refresh":
            _required(self.connection, "connection")
        elif self.action == "get":
            for value, label in (
                (self.connection, "connection"),
                (self.library, "library"),
                (self.library_id, "library id"),
                (self.selector, "selector"),
            ):
                _required(value, label)
        if type(self.limit) is not int or not 1 <= self.limit <= 500:
            raise ValueError("invalid catalogue limit")

    @property
    def name(self) -> str:
        return f"catalogue-{self.action}"


@dataclass(frozen=True)
class BatchRequest:
    action: str
    manifest: str = "batch.json"
    config: str = "config.json"
    catalogue: str = "catalogue.sqlite"
    database: str = "reports.sqlite"
    artifacts: str = "artifacts"
    manifest_id: str | None = None
    requested: tuple[tuple[str, str, str, str], ...] = ()
    all_eligible: bool = False

    def __post_init__(self) -> None:
        _member(self.action, {"preview", "inspect", "run"}, "batch action")
        if self.manifest_id is not None and re.fullmatch(r"[0-9a-f]{64}", self.manifest_id) is None:
            raise ValueError("invalid manifest id")
        if len(self.requested) > 1000 or any(len(item) != 4 for item in self.requested):
            raise ValueError("invalid batch selection")
        if self.action == "run":
            _required(self.manifest_id, "manifest id")

    @property
    def name(self) -> str:
        return f"batch-{self.action}"


@dataclass(frozen=True)
class DecisionPreviewRequest:
    review_case_id: str
    database: str = "reports.sqlite"

    def __post_init__(self) -> None:
        _required(self.review_case_id, "review case id")

    @property
    def name(self) -> str:
        return "decision-preview"


@dataclass(frozen=True)
class ContainedReviewerRequest:
    action: str
    profile: str = "contained-reviewer-profile.json"
    instruction: str | None = None
    input_document: str | None = None
    output_schema: str | None = None

    def __post_init__(self) -> None:
        _member(self.action, {"invoke", "auth-start", "auth-status", "auth-logout"}, "reviewer action")
        if self.action == "invoke":
            _required(self.instruction, "reviewer instruction")
            _required(self.input_document, "reviewer input")
            _required(self.output_schema, "reviewer output schema")
        elif any(value is not None for value in (self.instruction, self.input_document, self.output_schema)):
            raise ValueError("authentication does not accept reviewer input")

    @property
    def name(self) -> str:
        return "contained-reviewer"


@dataclass(frozen=True)
class CoverPacketRequest:
    report_id: str
    database: str = "reports.sqlite"
    artifacts: str = "artifacts"
    profile: str = "contained-reviewer-profile.json"
    instruction: str = "cover-reviewer-instruction.txt"
    output_schema: str = "cover-reviewer-output-schema.json"

    def __post_init__(self) -> None:
        _required(self.report_id, "report id")
        for value, label in (
            (self.database, "database"),
            (self.artifacts, "artifacts"),
            (self.profile, "reviewer profile"),
            (self.instruction, "reviewer instruction"),
            (self.output_schema, "reviewer output schema"),
        ):
            _required(value, label)

    @property
    def name(self) -> str:
        return "cover-packet-review"


ScenarioRequest: TypeAlias = (
    ReportRequest | ProposalRequest | ReviewCaseRequest | CatalogueRequest
    | BatchRequest | DecisionPreviewRequest | ContainedReviewerRequest | CoverPacketRequest
)


@dataclass(frozen=True)
class WorkbenchOperations:
    run_metadata_report: Operation
    reopen_metadata_report: Operation
    reopen_current_metadata_report: Operation
    reopen_cover_preview: Operation
    create_proposal_set: Operation
    reopen_proposal_set: Operation
    admit_review_case: Operation
    reopen_review_case: Operation
    run_catalogue: Operation
    preview_batch: Operation
    inspect_batch: Operation
    run_batch: Operation
    preview_shadow_decision_case: Operation
    run_contained_reviewer: Operation
    run_cover_packet_reviewer: Operation

    @classmethod
    def production_operations(cls) -> "WorkbenchOperations":
        """Bind source workbench scenarios to the same application composition seams."""
        from mechanical_librarian.bootstrap import composition

        from .reviewer import run_contained_reviewer, run_cover_packet_reviewer

        return cls(**{
            name: {
                "run_contained_reviewer": run_contained_reviewer,
                "run_cover_packet_reviewer": run_cover_packet_reviewer,
            }[name] if name.startswith("run_") and name.endswith("_reviewer")
            else getattr(composition, name)
            for name in cls.__dataclass_fields__
        })


def _complete(
    context: WorkbenchContext,
    name: str,
    raw: object,
    *,
    status: str = "completed",
) -> WorkbenchResult:
    _member(status, {"completed", "failed"}, "result status")
    if type(raw) is bytes:
        payload = raw
    elif type(raw) is str:
        json.loads(raw)
        payload = raw.encode("utf-8")
    elif type(raw) is bool:
        payload = json.dumps({"completed": raw}, separators=(",", ":"), sort_keys=True).encode()
    else:
        raise ValueError("workbench operation returned an unsupported result")
    artifact_id, digest = context.store_artifact(payload)
    facts = {
        "artifact_bytes": str(len(payload)),
        "artifact_sha256": digest,
        "status": status,
    }
    summary = (
        f"{name} {status}; artifact={artifact_id}; sha256={digest}; "
        f"bytes={len(payload)}"
    )
    return WorkbenchResult(name, summary, facts, (artifact_id,))


def _admit_configuration(
    context: WorkbenchContext,
    relative: str,
    *additional_authority: Path,
) -> tuple[Path, tuple[Path, ...]]:
    config = context.path(relative)
    config_payload, private = context.require_isolated_configuration(config)
    bound = context.require_network(
        config,
        *additional_authority,
        *private,
        private_tail=len(private),
        first_payload=config_payload,
    )
    private_offset = 1 + len(additional_authority)
    private_snapshots = tuple(
        context.authority_snapshot(payload)
        for payload in bound[private_offset:]
    )
    return (
        context.execution_configuration_snapshot(
            bound[0], private, private_snapshots
        ),
        tuple(
            context.authority_snapshot(payload)
            for payload in bound[1:1 + len(additional_authority)]
        ),
    )


def _execute(
    context: WorkbenchContext,
    request: ScenarioRequest,
    operations: WorkbenchOperations | None = None,
) -> WorkbenchResult:
    """Execute one closed typed scenario; no arbitrary command or plugin dispatch."""
    selected = operations or WorkbenchOperations.production_operations()
    result_status = "completed"
    context.require_write()
    if isinstance(request, ReportRequest):
        if request.action == "observe":
            admitted_config, _ = _admit_configuration(context, request.config)
            raw = selected.run_metadata_report(
                admitted_config, context.path(request.database),
                context.path(request.artifacts), request.connection, request.library,
                request.item, request.file_index,
            )
        elif request.action == "inspect":
            raw = selected.reopen_metadata_report(
                context.path(request.database), request.report_id
            )
        elif request.action == "inspect-current":
            raw = selected.reopen_current_metadata_report(
                context.path(request.database), request.connection, request.library,
                request.item, request.file_identity,
            )
        else:
            raw = selected.reopen_cover_preview(
                context.path(request.database), context.path(request.artifacts),
                request.report_id, request.artifact_id, request.preview_variant,
            )
    elif isinstance(request, ProposalRequest):
        if request.action == "create":
            admitted_config, _ = _admit_configuration(context, request.config)
            raw = selected.create_proposal_set(
                admitted_config,
                context.path(request.database),
                context.path(request.artifacts), request.identity,
            )
        else:
            raw = selected.reopen_proposal_set(
                context.path(request.database), request.identity
            )
    elif isinstance(request, ReviewCaseRequest):
        if request.action == "admit":
            admitted_config, _ = _admit_configuration(context, request.config)
            raw = selected.admit_review_case(
                admitted_config,
                context.path(request.database),
                context.path(request.artifacts), request.identity,
            )
        else:
            raw = selected.reopen_review_case(
                context.path(request.database), request.identity
            )
    elif isinstance(request, CatalogueRequest):
        if request.action == "refresh":
            config, _ = _admit_configuration(context, request.config)
        else:
            config = context.path(request.config)
        raw = selected.run_catalogue(
            config, context.path(request.database), request.action,
            request.connection, request.library, request.library_id, request.selector,
            request.limit,
        )
    elif isinstance(request, BatchRequest):
        if request.action == "preview":
            raw = selected.preview_batch(
                context.path(request.config), context.path(request.catalogue),
                context.path(request.manifest), request.requested, request.all_eligible,
            )
        elif request.action == "inspect":
            raw = selected.inspect_batch(context.path(request.manifest))
        else:
            manifest = context.path(request.manifest)
            admitted_config, admitted_additional = _admit_configuration(
                context, request.config, manifest
            )
            admitted_manifest = admitted_additional[0]
            events: list[dict[str, object]] = []
            completed = selected.run_batch(
                admitted_config, context.path(request.catalogue), admitted_manifest,
                request.manifest_id,
                context.path(request.database), context.path(request.artifacts),
                events.append,
            )
            if type(completed) is not bool:
                raise ValueError("batch operation returned an invalid completion state")
            raw = json.dumps(
                {"completed": completed, "events": events},
                ensure_ascii=False, separators=(",", ":"), sort_keys=True,
            )
            if not completed:
                result_status = "failed"
    elif isinstance(request, DecisionPreviewRequest):
        raw = selected.preview_shadow_decision_case(
            context.path(request.database), request.review_case_id
        )
    elif isinstance(request, CoverPacketRequest):
        database = context.path(request.database)
        artifacts = context.path(request.artifacts)
        report_document = selected.reopen_metadata_report(database, request.report_id)
        if type(report_document) is not str:
            raise ValueError("cover packet report is invalid")

        def read_preview(artifact_id: str, variant: str) -> bytes:
            preview = selected.reopen_cover_preview(
                database, artifacts, request.report_id, artifact_id, variant
            )
            if type(preview) is not bytes:
                raise ValueError("cover packet preview is invalid")
            return preview

        packet = prepare_cover_packet(
            report_document,
            read_preview,
        )
        packet_snapshot = context.authority_snapshot(packet.document.encode("utf-8"))
        paths = (
            context.path(request.profile),
            context.path(request.instruction),
            context.path(request.output_schema),
            packet_snapshot,
        )
        bound = context.require_network(*paths, private_first=True, private_tail=1)
        raw = selected.run_cover_packet_reviewer(
            bound[0], bound[1], bound[3], bound[2],
            tuple((value.reference, value.digest, value.payload) for value in packet.images),
        )
    elif isinstance(request, ContainedReviewerRequest):
        paths = tuple(
            context.path(value) for value in
            (request.profile, request.instruction, request.input_document, request.output_schema)
            if value is not None
        )
        bound = context.require_network(*paths, private_first=True)
        raw = selected.run_contained_reviewer(
            bound[0], request.action,
            bound[1] if request.instruction is not None else None,
            bound[2] if request.input_document is not None else None,
            bound[3] if request.output_schema is not None else None,
        )
    else:
        raise TypeError("unsupported workbench request")
    return _complete(context, request.name, raw, status=result_status)


def execute(
    context: WorkbenchContext,
    request: ScenarioRequest,
    operations: WorkbenchOperations | None = None,
) -> WorkbenchResult:
    """Execute through one redacting failure boundary."""
    try:
        return _execute(context, request, operations)
    except PermissionError:
        raise WorkbenchFailure("refused") from None
    except (TypeError, ValueError):
        raise WorkbenchFailure("invalid") from None
    except Exception:
        raise WorkbenchFailure("failed") from None


__all__ = [
    "BatchRequest", "CatalogueRequest", "ContainedReviewerRequest", "DecisionPreviewRequest", "ProposalRequest",
    "ReportRequest", "ReviewCaseRequest",
    "ScenarioRequest", "WorkbenchFailure", "WorkbenchOperations", "execute",
]
