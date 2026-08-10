"""Typed, closed workbench scenarios composed from existing application operations."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeAlias

from .context import WorkbenchContext
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
        _member(self.action, {"refresh", "list"}, "catalogue action")
        for value, label in ((self.connection, "connection"), (self.library, "library"),
                             (self.library_id, "library id"), (self.selector, "selector")):
            _identifier(value, label)
        if self.action == "refresh":
            _required(self.connection, "connection")
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
class ReviewerRequest:
    action: str
    identity: str
    database: str = "reports.sqlite"
    profile: str = "qualification-profile.json"

    def __post_init__(self) -> None:
        _member(self.action, {"packet-preview", "request", "inspect", "reevaluate"}, "reviewer action")
        _required(self.identity, "reviewer identity")

    @property
    def name(self) -> str:
        return {
            "packet-preview": "reviewer-packet-preview",
            "request": "reviewer-request",
            "inspect": "reviewer-request-inspect",
            "reevaluate": "reviewer-request-reevaluate",
        }[self.action]


@dataclass(frozen=True)
class ReviewerAuthRequest:
    action: str
    profile: str = "qualification-profile.json"

    def __post_init__(self) -> None:
        _member(self.action, {"start", "status", "logout"}, "reviewer authentication action")

    @property
    def name(self) -> str:
        return "reviewer-authentication"


ScenarioRequest: TypeAlias = (
    ReportRequest | ProposalRequest | ReviewCaseRequest | CatalogueRequest
    | BatchRequest | DecisionPreviewRequest | ReviewerRequest | ReviewerAuthRequest
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
    preview_shadow_review_case: Operation
    request_shadow_review: Operation
    inspect_shadow_review_request: Operation
    reevaluate_shadow_review: Operation
    run_reviewer_auth: Operation

    @classmethod
    def production_operations(cls) -> "WorkbenchOperations":
        """Bind source workbench scenarios to the same application composition seams."""
        from mechanical_librarian.bootstrap import composition

        return cls(**{
            name: getattr(composition, name)
            for name in cls.__dataclass_fields__
        })


def _complete(context: WorkbenchContext, name: str, raw: object) -> WorkbenchResult:
    if type(raw) is bytes:
        payload = raw
    elif type(raw) is str:
        parsed = json.loads(raw)
        canonical = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        if canonical != raw:
            raise ValueError("workbench operation returned noncanonical JSON")
        payload = raw.encode("utf-8")
    elif type(raw) is bool:
        payload = json.dumps({"completed": raw}, separators=(",", ":"), sort_keys=True).encode()
    else:
        raise ValueError("workbench operation returned an unsupported result")
    artifact_id, digest = context.store_artifact(payload)
    facts = {
        "artifact_bytes": str(len(payload)),
        "artifact_sha256": digest,
        "status": "completed",
    }
    summary = (
        f"{name} completed; artifact={artifact_id}; sha256={digest}; "
        f"bytes={len(payload)}"
    )
    return WorkbenchResult(name, summary, facts, (artifact_id,))


def _execute(
    context: WorkbenchContext,
    request: ScenarioRequest,
    operations: WorkbenchOperations | None = None,
) -> WorkbenchResult:
    """Execute one closed typed scenario; no arbitrary command or plugin dispatch."""
    selected = operations or WorkbenchOperations.production_operations()
    context.require_write()
    if isinstance(request, ReportRequest):
        if request.action == "observe":
            context.require_network()
            raw = selected.run_metadata_report(
                context.path(request.config), context.path(request.database),
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
        raw = (selected.create_proposal_set(
            context.path(request.config), context.path(request.database),
            context.path(request.artifacts), request.identity,
        ) if request.action == "create" else selected.reopen_proposal_set(
            context.path(request.database), request.identity
        ))
    elif isinstance(request, ReviewCaseRequest):
        raw = (selected.admit_review_case(
            context.path(request.config), context.path(request.database),
            context.path(request.artifacts), request.identity,
        ) if request.action == "admit" else selected.reopen_review_case(
            context.path(request.database), request.identity
        ))
    elif isinstance(request, CatalogueRequest):
        if request.action == "refresh":
            context.require_network()
        raw = selected.run_catalogue(
            context.path(request.config), context.path(request.database), request.action,
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
            context.require_network()
            events: list[dict[str, object]] = []
            raw = selected.run_batch(
                context.path(request.config), context.path(request.catalogue),
                context.path(request.manifest), request.manifest_id,
                context.path(request.database), context.path(request.artifacts),
                events.append,
            )
    elif isinstance(request, DecisionPreviewRequest):
        raw = selected.preview_shadow_decision_case(
            context.path(request.database), request.review_case_id
        )
    elif isinstance(request, ReviewerRequest):
        if request.action in {"request", "reevaluate"}:
            context.require_network()
        operation = {
            "packet-preview": selected.preview_shadow_review_case,
            "request": selected.request_shadow_review,
            "inspect": selected.inspect_shadow_review_request,
            "reevaluate": selected.reevaluate_shadow_review,
        }[request.action]
        raw = operation(
            context.path(request.database), request.identity, context.path(request.profile)
        )
    elif isinstance(request, ReviewerAuthRequest):
        context.require_network()
        raw = selected.run_reviewer_auth(context.path(request.profile), request.action)
    else:
        raise TypeError("unsupported workbench request")
    return _complete(context, request.name, raw)


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
    "BatchRequest", "CatalogueRequest", "DecisionPreviewRequest", "ProposalRequest",
    "ReportRequest", "ReviewCaseRequest", "ReviewerAuthRequest", "ReviewerRequest",
    "ScenarioRequest", "WorkbenchFailure", "WorkbenchOperations", "execute",
]
