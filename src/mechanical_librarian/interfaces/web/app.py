"""FastAPI adapter for exact-current review and one human decision command."""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import (
    Cookie,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    Security,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)
from fastapi.security import APIKeyCookie
from pydantic import BaseModel, ConfigDict, Field, StrictStr
from starlette.types import Message, Receive, Scope, Send

from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.application.field_effects import GuardedApplicationError
from mechanical_librarian.domain.field_effect import (
    FieldEffectError,
    SUPPORTED_DESTINATIONS,
)
from mechanical_librarian.application.metadata_policy import MetadataPolicyApplicationError, PolicyTransition
from mechanical_librarian.application.operations import OperationsError
from mechanical_librarian.application.review_application import ReviewApplication
from mechanical_librarian.application.review_web import ReviewWebError
from mechanical_librarian.domain.metadata_policy import MetadataPolicyError
from mechanical_librarian.interfaces.web import review_frontend
from mechanical_librarian.interfaces.web.react_assets import (
    ReactAssetError,
    ReactAssetLoader,
    ReactAssetRecovery,
)


_CASE_ID = re.compile(r"^review_case_[0-9a-f]{64}$")
_COOKIE_NAME = "ml_session"


class _LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    password: StrictStr = Field(min_length=1, max_length=1024)


class _ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _SeriesValue(_ClosedModel):
    name: str
    sequence: str | None


class _TargetField(_ClosedModel):
    destination: str
    state: str
    value: str | bool | list[str] | list[_SeriesValue] | None


class _ProposedValue(_ClosedModel):
    kind: str
    value: str | bool | list[str] | list[_SeriesValue]


class _DecisionProjection(_ClosedModel):
    actor: str
    decision_id: str
    destination: str
    occurred_at: int
    outcome: str
    proposal_id: str
    review_case_id: str
    selected_value: _ProposedValue | None
    target_fingerprint: str


class _AutomaticDecisionProjection(_ClosedModel):
    candidate_id: str | None
    decision_id: str
    destination: str
    occurred_at: int
    outcome: str
    proposal_id: str
    review_case_id: str
    selected_value: _ProposedValue | None
    target_fingerprint: str


class _AutomaticPauseRequest(_ClosedModel):
    paused: bool


class _AutomaticRecoveryRequest(_ClosedModel):
    category: StrictStr | None


class _AutomaticSuspendRequest(_ClosedModel):
    category: StrictStr | None = None
    reason: StrictStr = Field(min_length=1, max_length=256)


class _ActivityConsentGrant(_ClosedModel):
    library: StrictStr = Field(min_length=1, max_length=128)
    destinations: list[StrictStr] = Field(min_length=1, max_length=64)


class _ActivityConsentRequest(_ClosedModel):
    grants: list[_ActivityConsentGrant] = Field(min_length=1, max_length=64)


class _ReviewField(_ClosedModel):
    current: _TargetField
    deterministic_disposition: str
    destination: str
    effect_capability: str
    human_decision: _DecisionProjection | None
    automatic_decision: _AutomaticDecisionProjection | None
    manual_hold: bool
    manual_required: bool
    needs_attention: bool
    notices: list[str]
    proposed_values: list[_ProposedValue]
    provenance: list[str]
    proposal_id: str
    target_fingerprint: str
    uncertainty: list[str]


class _TargetIdentity(_ClosedModel):
    authors: list[str]
    connection: str
    item: str
    library: str
    media_kind: str


class _CaseDetailResponse(_ClosedModel):
    current: bool
    fields: list[_ReviewField]
    review_case_id: str
    schema_version: str
    target: _TargetIdentity


class _EvidenceItem(_ClosedModel):
    candidate: str | None
    fact: str
    kind: str
    provider: str | None
    reference: str
    source: str


class _EvidenceResponse(_ClosedModel):
    items: list[_EvidenceItem]
    next_cursor: str | None
    review_case_id: str
    schema_version: str


class _HistoryTransitionItem(_ClosedModel):
    checkpoint: int
    kind: str
    reason: str
    transition_id: str


class _HistoryDecisionItem(_ClosedModel):
    actor: Literal["owner"]
    decision_id: str
    destination: str
    event_id: str
    kind: Literal["human_decision_recorded"]
    occurred_at: int
    outcome: Literal["accept", "keep_current", "reject"]
    review_case_id: str
    schema_version: Literal["human-decision-event-v1"]


class _HistoryShadowItem(_ClosedModel):
    authority: Literal["none"]
    dispositions: dict[str, int]
    kind: Literal["shadow_preview_recorded"]
    occurred_at: int
    origin: Literal["policy_preview"]
    outcome_set_id: str
    review_case_id: str
    schema_version: Literal["shadow-preview-history-event-v1"]


class _HistoryAutomaticItem(_ClosedModel):
    decision_id: str
    destination: str
    kind: Literal["automatic_decision_recorded"]
    occurred_at: int
    origin: Literal["automatic_policy"]
    outcome: Literal["candidate", "keep_current", "ignore"]
    review_case_id: str
    schema_version: Literal["automatic-decision-history-event-v1"]


class _HistoryResponse(_ClosedModel):
    items: list[_HistoryTransitionItem | _HistoryDecisionItem | _HistoryShadowItem | _HistoryAutomaticItem]
    next_cursor: str | None
    review_case_id: str
    schema_version: str


class _SessionResponse(_ClosedModel):
    actor: str
    csrf_token: str
    expires_at: int
    schema_version: str


class _ErrorCode(_ClosedModel):
    code: str


class _ErrorResponse(_ClosedModel):
    error: _ErrorCode
    schema_version: str


class _ReviewCounts(_ClosedModel):
    all: int
    needs_attention: int
    no_attention_needed: int


class _LibraryWork(_ClosedModel):
    authors: list[str]
    attention_count: int
    attention_state: str
    detail_url: str | None
    dispositions: list[str]
    item: str
    library: str
    media_kind: str
    review_case_id: str | None
    title: str
    work_id: str


class _LibraryResponse(_ClosedModel):
    counts: _ReviewCounts
    items: list[_LibraryWork]
    lens: str
    next_cursor: str | None
    query: str | None
    schema_version: str


class _AttentionItem(_ClosedModel):
    authors: list[str]
    destination: str
    detail_url: str
    item: str
    library: str
    media_kind: str
    notices: list[str]
    provenance: list[str]
    review_case_id: str
    title: str
    work_attention_count: int


class _AttentionResponse(_ClosedModel):
    items: list[_AttentionItem]
    next_cursor: str | None
    query: str | None
    schema_version: str
    total: int


class _ActivityPrepared(_ClosedModel):
    automatic: int
    human: int
    total: int


class _ActivityLibrary(_ClosedModel):
    connection: str
    library: str
    prepared: _ActivityPrepared | None = None


class _ActivityDestination(_ClosedModel):
    consented: bool
    destination: str
    state: str


class _ActivityCapability(_ClosedModel):
    connection: str
    destinations: list[_ActivityDestination]
    library: str
    state: str


class _ActivityEvent(_ClosedModel):
    author_label: str = Field(default="Author details are not recorded", max_length=512)
    destination: str
    failure: str | None = None
    library: str
    occurred_at: int | None = None
    origin: str
    state: str
    work_label: str = Field(default="Recorded work", max_length=512)
    work_id: str


class _ActivitySuspension(_ClosedModel):
    connection: str
    destination: str | None
    reason: str
    scope: str
    updated_at: int


class _ActivityExample(_ClosedModel):
    after: object
    before: object
    destination: str
    library: str
    work_id: str


class _ActivityResponse(_ClosedModel):
    backup_ready: bool
    capabilities: list[_ActivityCapability]
    configured_libraries: list[_ActivityLibrary]
    consented_libraries: list[_ActivityLibrary]
    counts: dict[str, int]
    enabled_libraries: list[_ActivityLibrary]
    events: list[_ActivityEvent]
    example: _ActivityExample | None
    last_failure: str | None
    paused: bool
    prepared: _ActivityPrepared
    schema_version: Literal["continuous-field-activity-v1"]
    suspensions: list[_ActivitySuspension]


class _PreparationProgress(_ClosedModel):
    scan_id: str | None
    connection: str
    library: str
    library_id: str
    state: str
    checked: int
    total: int
    ready: int
    blocked: int
    rescan_requested: bool


class _PreparationResponse(_ClosedModel):
    libraries: list[_PreparationProgress]
    paused: bool = False
    active: bool = False
    actionable: bool = False
    schema_version: Literal["review-preparation-v1"] = "review-preparation-v1"


class _DecisionRequest(_ClosedModel):
    destination: StrictStr = Field(min_length=1, max_length=64)
    outcome: Literal["accept", "keep_current", "reject"]
    proposal_id: StrictStr = Field(pattern=r"^review_proposal_[0-9a-f]{64}$")
    selected_value: _ProposedValue | None = None
    target_fingerprint: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")


class _DecisionAcknowledgement(_DecisionProjection):
    schema_version: str


class _PolicyDraftRequest(_ClosedModel):
    name: StrictStr = Field(min_length=1, max_length=80)
    options: dict[StrictStr, StrictStr] = Field(max_length=16)
    destinations: dict[StrictStr, StrictStr] = Field(max_length=16)


class _PolicyDraftResponse(_ClosedModel):
    policy_id: str
    schema_version: Literal["metadata-policy-profile-v2"] = "metadata-policy-profile-v2"


class _PolicyProfile(_ClosedModel):
    destinations: dict[StrictStr, StrictStr] = Field(max_length=16)
    name: StrictStr = Field(min_length=1, max_length=80)
    options: dict[StrictStr, StrictStr] = Field(max_length=16)
    schema_version: Literal["metadata-policy-profile-v2"]


class _PolicyProfileResponse(_ClosedModel):
    policy_id: str
    profile: _PolicyProfile
    schema_version: Literal["metadata-policy-notebook-v1"] = "metadata-policy-notebook-v1"


class _RichFieldPreviewResponse(_ClosedModel):
    current_value: object
    desired_value: object | None
    field: str
    narrowed_for_target: bool
    option: str
    outcome: str
    qualified_value: object | None
    reason: str | None
    target_state: str


class _RichCasePreviewResponse(_ClosedModel):
    fields: list[_RichFieldPreviewResponse]
    proposal_set_id: str
    report_id: str
    resolved_policy_id: str


class _PolicyImpactResponse(_ClosedModel):
    affected_categories: list[str]
    cases: list[_RichCasePreviewResponse]
    cases_total: int
    cases_truncated: bool
    current_unapplied_count: int
    library: str
    resolved_policy_id: str


class _PolicyPreviewResponse(_ClosedModel):
    libraries: list[_PolicyImpactResponse]
    policy_id: str
    schema_version: Literal["metadata-policy-preview-v1"] = "metadata-policy-preview-v1"


class _PolicyActivationRequest(_ClosedModel):
    policy_id: StrictStr = Field(pattern=r"^policy_[0-9a-f]{64}$")
    expected_active_policy_id: StrictStr = Field(pattern=r"^policy_[0-9a-f]{64}$")


class _PolicyTransitionResponse(_ClosedModel):
    transition_id: str
    from_policy_id: str
    to_policy_id: str
    generation: int
    state: str
    completed: int
    total: int
    failed: int
    acknowledged: bool
    schema_version: Literal["metadata-policy-transition-v1"] = "metadata-policy-transition-v1"


class _PolicyActivationResponse(_ClosedModel):
    policy_id: str
    transition: _PolicyTransitionResponse | None
    schema_version: Literal["metadata-policy-activation-v1"] = "metadata-policy-activation-v1"


class _ShadowPostureResponse(_ClosedModel):
    authority: Literal["none", "decision_only"]
    can_leave_preview: Literal[False]
    posture: Literal["review", "preview", "automatic"]
    schema_version: Literal["shadow-posture-v1"]


class _AutomaticSafetyResponse(_ClosedModel):
    paused: bool
    global_suspension: str | None
    category_suspensions: dict[str, str]


class _OperationalResponse(_ClosedModel):
    incidents: list[dict[str, object]]
    readiness: str
    schema_version: str
    summary: dict[str, object]
    timings: list[dict[str, object]]


class _SupportPreviewResponse(_ClosedModel):
    bytes: int
    excluded: list[str]
    payload: dict[str, object]
    schema_version: str


class _ShadowLeaveImpact(_ClosedModel):
    affected_categories: list[str]
    current_unapplied_count: int
    library: str


class _ShadowLeaveResponse(_ClosedModel):
    authority: Literal["none"]
    can_confirm: Literal[False]
    confirmation_required: Literal[True]
    libraries: list[_ShadowLeaveImpact]
    posture: Literal["review", "preview"]
    requested_posture: Literal["qualified_decision_only_unavailable"]
    schema_version: Literal["shadow-posture-leave-preview-v1"]


class _ShadowFieldOutcomeResponse(_ClosedModel):
    action_id: str
    candidate_id: str | None
    citation_ids: list[str]
    combination_ids: list[str]
    destination: str
    disposition: Literal[
        "abstention",
        "candidate",
        "conflict",
        "ignore",
        "keep_current",
        "manual",
        "unsupported",
    ]
    outcome_id: str
    policy_category: str | None
    policy_destination: Literal["ask", "automatic", "ignore", "unsupported"]
    policy_option: str | None
    proposal_id: str
    qualification_candidate_id: str
    reason_code: str
    rule_id: str


class _ShadowOutcomeSetResponse(_ClosedModel):
    authority: Literal["none"]
    catalogue_sha256: str
    decision_policy_sha256: str
    decision_policy_version: str
    evaluated_at: int
    outcome_set_id: str
    outcomes: list[_ShadowFieldOutcomeResponse]
    policy_generation: int
    policy_profile_id: str
    posture: Literal["preview"]
    proposal_set_id: str
    report_id: str
    resolved_policy_id: str
    review_case_id: str
    schema_version: Literal["shadow-outcome-set-v1"]
    source_decision_set_id: str
    target_fingerprint: str


class _ShadowSampleResponse(_ClosedModel):
    authority: Literal["none"]
    items: list[_ShadowOutcomeSetResponse]
    next_cursor: None
    schema_version: Literal["shadow-outcome-sample-v1"]


def _policy_transition_response(value: PolicyTransition) -> _PolicyTransitionResponse:
    return _PolicyTransitionResponse(**{
        "transition_id": value.transition_id,
        "from_policy_id": value.from_policy_id,
        "to_policy_id": value.to_policy_id,
        "generation": value.generation,
        "state": value.state,
        "completed": value.completed,
        "total": value.total,
        "failed": value.failed,
        "acknowledged": value.acknowledged,
    })


def _policy_application_http_error(error: MetadataPolicyApplicationError) -> HTTPException:
    code = str(error)
    status = 409 if "stale" in code or "processing" in code else (422 if "invalid" in code or "unqualified" in code else 503)
    return HTTPException(status, detail={"code": code.replace(" ", "_")})


def _preparation_http_error(
    error: RuntimeError | StopIteration, *, retry: bool = False
) -> HTTPException:
    """Preserve closed preparation controls; collapse unexpected failures."""
    code = getattr(error, "code", "")
    if code == "unavailable":
        return HTTPException(503, detail={"code": code})
    if code == "library":
        return HTTPException(404, detail={"code": code})
    if code == "paused" or (retry and code == "retry"):
        return HTTPException(409, detail={"code": code})
    return HTTPException(503, detail={"code": "persistence"})


def _policy_problem(error: MetadataPolicyError) -> str:
    """Translate closed-domain failures without reflecting input values."""
    return {
        "policy option is unavailable": "policy_option_unavailable",
        "policy destination is unavailable": "policy_destination_unavailable",
        "explicit policy is unsupported": "policy_explicit_unsupported",
    }.get(str(error), "policy_validation")


class _BodyLimitMiddleware:
    def __init__(
        self,
        app: Callable[[Scope, Receive, Send], Awaitable[None]],
        maximum_bytes: int,
        decision_maximum_bytes: int,
    ) -> None:
        self._app = app
        self._maximum_bytes = maximum_bytes
        self._decision_maximum_bytes = decision_maximum_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        is_login = path == "/api/v1/session/login"
        is_decision = bool(
            re.fullmatch(
                r"/api/v1/review-cases/[^/]+/decisions",
                path,
            )
        )
        is_policy_draft = path in {
            "/api/v1/metadata-policy/activate",
            "/api/v1/metadata-policy/drafts",
            "/api/v1/metadata-policy/validate",
            "/api/v1/metadata-policy/preview",
        }
        if scope["type"] != "http" or not (is_login or is_decision or is_policy_draft):
            await self._app(scope, receive, send)
            return
        maximum = self._maximum_bytes if is_login else (16_384 if is_policy_draft else self._decision_maximum_bytes)
        declared = next(
            (
                value
                for name, value in scope.get("headers", [])
                if name.lower() == b"content-length"
            ),
            None,
        )
        if declared is not None:
            try:
                if int(declared) > maximum:
                    await _oversized_body_response(scope, receive, send)
                    return
            except ValueError:
                await _oversized_body_response(scope, receive, send)
                return
        messages: list[Message] = []
        size = 0
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] == "http.disconnect":
                await self._app(scope, _replay(messages), send)
                return
            size += len(message.get("body", b""))
            if size > maximum:
                await _oversized_body_response(scope, receive, send)
                return
            if not message.get("more_body", False):
                break
        await self._app(scope, _replay(messages), send)


def _replay(messages: list[Message]) -> Receive:
    async def receive() -> Message:
        if messages:
            return messages.pop(0)
        return {"type": "http.disconnect"}

    return receive


async def _oversized_body_response(
    scope: Scope, receive: Receive, send: Send
) -> None:
    response = _error(413, "body_too_large")
    _set_security_headers(response, hsts=True)
    await response(scope, receive, send)


def create_review_app(
    application: ReviewApplication,
    configuration: ReviewWebConfiguration,
) -> FastAPI:
    sessions = application
    origin = urlsplit(configuration.canonical_origin)
    assets = ReactAssetLoader()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        application.start()
        try:
            yield
        finally:
            application.stop()

    app = FastAPI(
        title="Mechanical Librarian Review API",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    session_cookie = APIKeyCookie(
        name=_COOKIE_NAME,
        scheme_name="OwnerSession",
        auto_error=False,
    )

    def authenticated_session_cookie(
        request: Request,
        ml_session: str | None = Security(session_cookie),
    ) -> str:
        _require_session(sessions, ml_session)
        assert ml_session is not None
        if (
            request.url.path.startswith("/api/v1/")
            and request.method not in {"GET", "HEAD", "OPTIONS"}
            and request.url.path != "/api/v1/session/login"
        ):
            bundle = assets.load()
            if isinstance(bundle, ReactAssetRecovery):
                raise HTTPException(503, detail={"code": bundle.code})
            if request.headers.get("x-ml-build-identity") != bundle.build_identity:
                raise HTTPException(409, detail={"code": "browser_build_mismatch"})
        return ml_session

    def browser_csrf(session_token: str) -> str:
        try:
            return sessions.issue_browser_csrf(session_token)
        except ReviewWebError as error:
            status_code = {"unauthorized": 401, "persistence": 503}.get(
                error.code, 503
            )
            raise HTTPException(
                status_code, detail={"code": error.code}
            ) from None

    app.add_middleware(
        _BodyLimitMiddleware,
        maximum_bytes=2048,
        decision_maximum_bytes=8192,
    )

    @app.middleware("http")
    async def trust_boundary(request: Request, call_next):
        if request.url.path not in {"/health/live", "/health/ready"}:
            if not _request_matches_origin(request, configuration):
                return _secured_error(400, "invalid_origin")
            if request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get("origin") != configuration.canonical_origin:
                return _secured_error(403, "origin")
        response = await call_next(request)
        _set_security_headers(response, hsts=origin.scheme == "https")
        return response

    @app.get("/health/live", include_in_schema=False)
    def liveness() -> PlainTextResponse:
        return PlainTextResponse("ok\n")

    @app.get("/health/ready", include_in_schema=False)
    def readiness() -> PlainTextResponse:
        ready = application.ready()
        return PlainTextResponse("ready\n" if ready else "draining\n",
                                 status_code=200 if ready else 503)

    _register_session_and_operations_routes(
        app, application, configuration, authenticated_session_cookie
    )
    _register_policy_routes(app, application, authenticated_session_cookie)
    _register_case_and_effect_routes(
        app, application, session_cookie, authenticated_session_cookie
    )
    _register_browse_and_preparation_routes(
        app, application, session_cookie, authenticated_session_cookie
    )
    _register_html_routes(
        app, application, session_cookie, browser_csrf, assets
    )

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, error: HTTPException) -> JSONResponse:
        detail = error.detail
        code = detail.get("code") if type(detail) is dict else "request"
        return _error(error.status_code, str(code))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        if request.url.path.startswith("/api/v1/metadata-policy/"):
            if any(item.get("loc") == ("header", "x-csrf-token") for item in error.errors()):
                return _error(403, "csrf")
            return _error(422, "policy_validation")
        return _error(422, "invalid")

    _document_build_identity_header(app)
    return app


def _document_build_identity_header(app: FastAPI) -> None:
    """Expose the runtime build gate on every authenticated unsafe operation."""
    generate = app.openapi

    def openapi() -> dict[str, object]:
        schema = generate()
        paths = schema.get("paths")
        if not isinstance(paths, dict):
            return schema
        for path, path_item in paths.items():
            if (
                not isinstance(path, str)
                or not path.startswith("/api/v1/")
                or path == "/api/v1/session/login"
                or not isinstance(path_item, dict)
            ):
                continue
            for method in ("post", "put", "patch", "delete"):
                operation = path_item.get(method)
                if not isinstance(operation, dict):
                    continue
                parameters = operation.setdefault("parameters", [])
                if isinstance(parameters, list) and not any(
                    isinstance(parameter, dict)
                    and parameter.get("name") == "x-ml-build-identity"
                    for parameter in parameters
                ):
                    parameters.append(
                        {
                            "description": (
                                "Exact identity of the installed authenticated "
                                "browser build."
                            ),
                            "in": "header",
                            "name": "x-ml-build-identity",
                            "required": True,
                            "schema": {
                                "pattern": "^[0-9a-f]{64}$",
                                "type": "string",
                            },
                        }
                    )
                responses = operation.setdefault("responses", {})
                if isinstance(responses, dict):
                    responses.setdefault(
                        "409",
                        {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/_ErrorResponse"
                                    }
                                }
                            },
                            "description": "Browser build mismatch",
                        },
                    )
        return schema

    app.openapi = openapi  # type: ignore[method-assign]


def _register_session_and_operations_routes(
    app: FastAPI,
    application: ReviewApplication,
    configuration: ReviewWebConfiguration,
    authenticated_session_cookie: Callable[[str | None], str],
) -> None:
    sessions = application

    @app.get("/api/v1/operations/status", response_model=_OperationalResponse,
             responses={status: {"model": _ErrorResponse} for status in (401, 503)})
    def operations_status(
        ml_session: str = Depends(authenticated_session_cookie),
    ) -> _OperationalResponse:
        _require_session(sessions, ml_session)
        if not application.operations_available:
            raise HTTPException(503, detail={"code": "operations_unavailable"})
        try:
            return _OperationalResponse.model_validate(application.operations_status())
        except (OperationsError, FieldEffectError):
            raise HTTPException(503, detail={"code": "operations_unavailable"}) from None

    @app.get("/api/v1/operations/support/preview",
             response_model=_SupportPreviewResponse,
             responses={status: {"model": _ErrorResponse} for status in (401, 503)})
    def support_preview(
        ml_session: str = Depends(authenticated_session_cookie),
    ) -> _SupportPreviewResponse:
        _require_session(sessions, ml_session)
        try:
            if not application.operations_available:
                raise OperationsError("support_unavailable")
            return _SupportPreviewResponse.model_validate(
                application.support_preview()
            )
        except OperationsError:
            raise HTTPException(503, detail={"code": "support_unavailable"}) from None

    @app.post("/api/v1/operations/support/export",
              responses={status: {"model": _ErrorResponse} for status in (401, 403, 422, 503)})
    def support_export(
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> Response:
        _require_session(sessions, ml_session, x_csrf_token)
        try:
            if not application.operations_available:
                raise OperationsError("support_unavailable")
            return Response(application.support_export(),
                            media_type="application/json",
                            headers={"Content-Disposition":
                                     "attachment; filename=mechanical-librarian-support.json"})
        except OperationsError:
            raise HTTPException(503, detail={"code": "support_unavailable"}) from None

    @app.get("/login", include_in_schema=False)
    def login_page() -> HTMLResponse:
        return HTMLResponse(review_frontend.login_html())

    @app.get("/assets/login.css", include_in_schema=False)
    def login_css() -> Response:
        return Response(review_frontend.LOGIN_CSS, media_type="text/css")

    @app.get("/assets/login.js", include_in_schema=False)
    def login_js() -> Response:
        return Response(review_frontend.LOGIN_JS, media_type="text/javascript")

    @app.post(
        "/api/v1/session/login",
        response_model=_SessionResponse,
        responses={
            status: {"model": _ErrorResponse}
            for status in (400, 401, 403, 413, 422, 429, 503)
        },
    )
    def login(payload: _LoginRequest, response: Response) -> _SessionResponse:
        try:
            tokens = sessions.login(payload.password)
        except ReviewWebError as error:
            status_code = {
                "invalid_credentials": 401,
                "persistence": 503,
                "throttled": 429,
            }.get(error.code, 500)
            raise HTTPException(status_code, detail={"code": error.code}) from None
        response.set_cookie(
            _COOKIE_NAME,
            tokens.session_token,
            httponly=True,
            secure=True,
            samesite="strict",
            max_age=configuration.session_lifetime_seconds,
            path="/",
        )
        return _SessionResponse(
            actor="owner",
            csrf_token=tokens.csrf_token,
            expires_at=tokens.expires_at,
            schema_version="review-session-v1",
        )

    @app.post(
        "/api/v1/session/logout",
        status_code=204,
        responses={
            status: {"model": _ErrorResponse}
            for status in (400, 401, 403, 422, 503)
        },
    )
    def logout(
        response: Response,
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> Response:
        try:
            sessions.logout(ml_session, x_csrf_token)
        except ReviewWebError as error:
            status = {
                "csrf": 403,
                "persistence": 503,
                "unauthorized": 401,
            }.get(error.code, 503)
            raise HTTPException(status, detail={"code": error.code}) from None
        response.delete_cookie(_COOKIE_NAME, path="/", secure=True, httponly=True, samesite="strict")
        response.status_code = 204
        return response


def _register_policy_routes(
    app: FastAPI,
    application: ReviewApplication,
    authenticated_session_cookie: Callable[[str | None], str],
) -> None:
    sessions = application
    notebook = application.policy
    automatic_safety = application.automatic_safety

    @app.post(
        "/api/v1/metadata-policy/drafts",
        response_model=_PolicyDraftResponse,
        status_code=201,
        responses={status: {"model": _ErrorResponse} for status in (401, 403, 413, 422, 503)},
    )
    def create_metadata_policy_draft(
        payload: _PolicyDraftRequest,
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> _PolicyDraftResponse:
        _require_session(sessions, ml_session, x_csrf_token)
        try:
            profile = application.create_policy_draft(
                payload.name, payload.options, payload.destinations
            )
            policy_id = profile.policy_id
        except MetadataPolicyError as error:
            raise HTTPException(422, detail={"code": _policy_problem(error)}) from None
        except MetadataPolicyApplicationError as error:
            if str(error) == "policy automatic unqualified":
                raise _policy_application_http_error(error) from None
            raise HTTPException(503, detail={"code": "persistence"}) from None
        except ReviewWebError:
            raise HTTPException(422, detail={"code": "policy_validation"}) from None
        except Exception:
            raise HTTPException(503, detail={"code": "persistence"}) from None
        return _PolicyDraftResponse(policy_id=policy_id)

    @app.get("/api/v1/metadata-policy/state", response_model=_PolicyProfileResponse, responses={status: {"model": _ErrorResponse} for status in (401, 503)})
    def metadata_policy_state(ml_session: str = Depends(authenticated_session_cookie)) -> _PolicyProfileResponse:
        _require_session(sessions, ml_session)
        profile = notebook.active()
        return _PolicyProfileResponse(
            policy_id=profile.policy_id, profile=_PolicyProfile.model_validate(profile.to_data())
        )

    @app.get("/api/v1/metadata-policy/shadow", response_model=_ShadowPostureResponse, responses={status: {"model": _ErrorResponse} for status in (401, 503)})
    def shadow_posture(ml_session: str = Depends(authenticated_session_cookie)) -> _ShadowPostureResponse:
        _require_session(sessions, ml_session)
        try:
            return _ShadowPostureResponse.model_validate(notebook.shadow_state())
        except MetadataPolicyApplicationError:
            raise HTTPException(503, detail={"code": "persistence"}) from None

    @app.post("/api/v1/metadata-policy/shadow/enter", response_model=_ShadowPostureResponse, responses={status: {"model": _ErrorResponse} for status in (401, 403, 422, 503)})
    def enter_shadow_preview(ml_session: str = Depends(authenticated_session_cookie), x_csrf_token: str = Header()) -> _ShadowPostureResponse:
        _require_session(sessions, ml_session, x_csrf_token)
        try:
            return _ShadowPostureResponse.model_validate(notebook.enter_preview(application.now()))
        except MetadataPolicyApplicationError:
            raise HTTPException(503, detail={"code": "persistence"}) from None

    @app.post("/api/v1/metadata-policy/shadow/confirm-automatic", response_model=_ShadowPostureResponse, responses={status: {"model": _ErrorResponse} for status in (401, 403, 422, 503)})
    def confirm_automatic(ml_session: str = Depends(authenticated_session_cookie), x_csrf_token: str = Header()) -> _ShadowPostureResponse:
        _require_session(sessions, ml_session, x_csrf_token)
        try:
            return _ShadowPostureResponse.model_validate(notebook.confirm_automatic(application.now()))
        except MetadataPolicyApplicationError:
            raise HTTPException(422, detail={"code": "automatic_unavailable"}) from None

    def _automatic_safety_response() -> _AutomaticSafetyResponse:
        state = automatic_safety.state()
        return _AutomaticSafetyResponse(paused=state.paused, global_suspension=state.global_suspension, category_suspensions=dict(state.category_suspensions))

    @app.get("/api/v1/metadata-policy/automatic-safety", response_model=_AutomaticSafetyResponse)
    def automatic_safety_state(ml_session: str = Depends(authenticated_session_cookie)) -> _AutomaticSafetyResponse:
        _require_session(sessions, ml_session)
        return _automatic_safety_response()

    @app.post("/api/v1/metadata-policy/automatic-safety/pause", response_model=_AutomaticSafetyResponse, responses={status: {"model": _ErrorResponse} for status in (401, 403, 413, 422, 503)})
    def set_automatic_pause(payload: _AutomaticPauseRequest, ml_session: str = Depends(authenticated_session_cookie), x_csrf_token: str = Header()) -> _AutomaticSafetyResponse:
        _require_session(sessions, ml_session, x_csrf_token)
        try:
            automatic_safety.pause(payload.paused, application.now())
            return _automatic_safety_response()
        except MetadataPolicyApplicationError:
            raise HTTPException(503, detail={"code": "persistence"}) from None
        except Exception:
            raise HTTPException(422, detail={"code": "request"}) from None

    @app.post("/api/v1/metadata-policy/automatic-safety/recover", response_model=_AutomaticSafetyResponse, responses={status: {"model": _ErrorResponse} for status in (401, 403, 413, 422, 503)})
    def recover_automatic_safety(payload: _AutomaticRecoveryRequest, ml_session: str = Depends(authenticated_session_cookie), x_csrf_token: str = Header()) -> _AutomaticSafetyResponse:
        _require_session(sessions, ml_session, x_csrf_token)
        try:
            category = payload.category
            if category is not None and not category:
                raise ValueError
            automatic_safety.recover(application.now(), category)
            return _automatic_safety_response()
        except MetadataPolicyApplicationError:
            raise HTTPException(503, detail={"code": "persistence"}) from None
        except Exception:
            raise HTTPException(422, detail={"code": "request"}) from None

    @app.post("/api/v1/metadata-policy/automatic-safety/suspend", response_model=_AutomaticSafetyResponse, responses={status: {"model": _ErrorResponse} for status in (401, 403, 413, 422, 503)})
    def suspend_automatic_safety(payload: _AutomaticSuspendRequest, ml_session: str = Depends(authenticated_session_cookie), x_csrf_token: str = Header()) -> _AutomaticSafetyResponse:
        _require_session(sessions, ml_session, x_csrf_token)
        try:
            if payload.category is not None and not payload.category:
                raise ValueError
            automatic_safety.suspend(payload.reason, application.now(), payload.category)
            return _automatic_safety_response()
        except MetadataPolicyApplicationError:
            raise HTTPException(503, detail={"code": "persistence"}) from None
        except Exception:
            raise HTTPException(422, detail={"code": "request"}) from None


    @app.get("/api/v1/metadata-policy/shadow/leave-preview", response_model=_ShadowLeaveResponse, responses={status: {"model": _ErrorResponse} for status in (401, 503)})
    def preview_leave_shadow(ml_session: str = Depends(authenticated_session_cookie)) -> _ShadowLeaveResponse:
        _require_session(sessions, ml_session)
        try:
            return _ShadowLeaveResponse.model_validate(notebook.preview_leave())
        except MetadataPolicyApplicationError:
            raise HTTPException(503, detail={"code": "persistence"}) from None

    @app.get("/api/v1/metadata-policy/shadow/outcomes", response_model=_ShadowSampleResponse, responses={status: {"model": _ErrorResponse} for status in (401, 422, 503)})
    def shadow_outcome_sample(limit: int = Query(default=20, ge=1, le=50), ml_session: str = Depends(authenticated_session_cookie)) -> _ShadowSampleResponse:
        _require_session(sessions, ml_session)
        try:
            return _ShadowSampleResponse.model_validate(notebook.shadow_sample(limit))
        except MetadataPolicyApplicationError:
            raise HTTPException(503, detail={"code": "persistence"}) from None

    @app.post("/api/v1/metadata-policy/validate", response_model=_PolicyProfileResponse, responses={status: {"model": _ErrorResponse} for status in (401, 403, 413, 422, 503)})
    def validate_metadata_policy(payload: _PolicyDraftRequest, ml_session: str = Depends(authenticated_session_cookie), x_csrf_token: str = Header()) -> _PolicyProfileResponse:
        _require_session(sessions, ml_session, x_csrf_token)
        try:
            profile = application.validate_policy(
                payload.name, payload.options, payload.destinations
            )
        except MetadataPolicyError as error:
            raise HTTPException(422, detail={"code": _policy_problem(error)}) from None
        except MetadataPolicyApplicationError as error:
            raise _policy_application_http_error(error) from None
        return _PolicyProfileResponse(
            policy_id=profile.policy_id, profile=_PolicyProfile.model_validate(profile.to_data())
        )

    @app.get("/api/v1/metadata-policy/drafts/{policy_id}", response_model=_PolicyProfileResponse, responses={status: {"model": _ErrorResponse} for status in (401, 404, 422, 503)})
    def get_metadata_policy_draft(policy_id: str, ml_session: str = Depends(authenticated_session_cookie)) -> _PolicyProfileResponse:
        _require_session(sessions, ml_session)
        try:
            profile = notebook.read(policy_id)
        except MetadataPolicyApplicationError:
            raise HTTPException(404, detail={"code": "unavailable"}) from None
        return _PolicyProfileResponse(
            policy_id=profile.policy_id, profile=_PolicyProfile.model_validate(profile.to_data())
        )

    @app.post("/api/v1/metadata-policy/preview", response_model=_PolicyPreviewResponse, responses={status: {"model": _ErrorResponse} for status in (401, 403, 413, 422, 503)})
    def preview_metadata_policy(payload: _PolicyDraftRequest, ml_session: str = Depends(authenticated_session_cookie), x_csrf_token: str = Header()) -> _PolicyPreviewResponse:
        _require_session(sessions, ml_session, x_csrf_token)
        try:
            profile, preview = application.preview_policy(
                payload.name, payload.options, payload.destinations
            )
        except MetadataPolicyError as error:
            raise HTTPException(422, detail={"code": _policy_problem(error)}) from None
        except MetadataPolicyApplicationError as error:
            raise _policy_application_http_error(error) from None
        return _PolicyPreviewResponse(policy_id=profile.policy_id, libraries=[_PolicyImpactResponse(affected_categories=list(item.affected_categories), cases=[_RichCasePreviewResponse.model_validate(case.to_data()) for case in item.cases], cases_total=item.cases_total, cases_truncated=item.cases_truncated, current_unapplied_count=item.current_unapplied_count, library=item.library, resolved_policy_id=item.resolved_policy_id) for item in preview.libraries])

    @app.post("/api/v1/metadata-policy/activate", response_model=_PolicyActivationResponse, responses={status: {"model": _ErrorResponse} for status in (401, 403, 409, 413, 422, 503)})
    def activate_metadata_policy(payload: _PolicyActivationRequest, ml_session: str = Depends(authenticated_session_cookie), x_csrf_token: str = Header()) -> _PolicyActivationResponse:
        _require_session(sessions, ml_session, x_csrf_token)
        try:
            result = notebook.activate(
                payload.policy_id,
                payload.expected_active_policy_id,
                application.now(),
            )
        except MetadataPolicyApplicationError as error:
            raise _policy_application_http_error(error) from None
        return _PolicyActivationResponse(policy_id=result.active.policy_id, transition=None if result.transition is None else _policy_transition_response(result.transition))

    @app.get("/api/v1/metadata-policy/transition", response_model=_PolicyTransitionResponse | None, responses={status: {"model": _ErrorResponse} for status in (401, 503)})
    def metadata_policy_transition(ml_session: str = Depends(authenticated_session_cookie)) -> _PolicyTransitionResponse | None:
        try:
            transition = notebook.transition()
        except MetadataPolicyApplicationError as error:
            raise _policy_application_http_error(error) from None
        return None if transition is None else _policy_transition_response(transition)

    @app.post("/api/v1/metadata-policy/transitions/{transition_id}/retry", response_model=_PolicyTransitionResponse, responses={status: {"model": _ErrorResponse} for status in (401, 403, 409, 422, 503)})
    def retry_metadata_policy_transition(transition_id: str, ml_session: str = Depends(authenticated_session_cookie), x_csrf_token: str = Header()) -> _PolicyTransitionResponse:
        _require_session(sessions, ml_session, x_csrf_token)
        try:
            return _policy_transition_response(
                notebook.retry(transition_id, application.now())
            )
        except MetadataPolicyApplicationError as error:
            raise _policy_application_http_error(error) from None

    @app.post("/api/v1/metadata-policy/transitions/{transition_id}/acknowledge", response_model=_PolicyTransitionResponse, responses={status: {"model": _ErrorResponse} for status in (401, 403, 409, 422, 503)})
    def acknowledge_metadata_policy_transition(transition_id: str, ml_session: str = Depends(authenticated_session_cookie), x_csrf_token: str = Header()) -> _PolicyTransitionResponse:
        _require_session(sessions, ml_session, x_csrf_token)
        try:
            return _policy_transition_response(
                notebook.acknowledge(transition_id, application.now())
            )
        except MetadataPolicyApplicationError as error:
            raise _policy_application_http_error(error) from None


def _register_case_and_effect_routes(
    app: FastAPI,
    application: ReviewApplication,
    session_cookie: APIKeyCookie,
    authenticated_session_cookie: Callable[[str | None], str],
) -> None:
    sessions = application

    @app.get(
        "/api/v1/review-cases/{review_case_id}",
        response_model=_CaseDetailResponse,
        responses={
            status: {"model": _ErrorResponse}
            for status in (400, 401, 404, 409, 422)
        },
    )
    def case_detail(
        review_case_id: str,
        ml_session: str | None = Security(session_cookie),
    ) -> _CaseDetailResponse:
        _require_session(sessions, ml_session)
        _require_case_id(review_case_id)
        detail = _case_call(lambda: application.case_detail(review_case_id))
        return _CaseDetailResponse.model_validate(detail.to_data())

    @app.post(
        "/api/v1/review-cases/{review_case_id}/decisions",
        response_model=_DecisionAcknowledgement,
        responses={
            status: {"model": _ErrorResponse}
            for status in (400, 401, 403, 404, 409, 413, 422, 503)
        },
    )
    def record_decision(
        review_case_id: str,
        payload: _DecisionRequest,
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
        idempotency_key: str = Header(
            min_length=16, max_length=128
        ),
    ) -> _DecisionAcknowledgement:
        actor = _require_session(sessions, ml_session, x_csrf_token)
        _require_case_id(review_case_id)
        acknowledgement = _DecisionAcknowledgement.model_validate(
            _decision_call(
                lambda: application.record_decision(
                    review_case_id,
                    proposal_id=payload.proposal_id,
                    target_fingerprint=payload.target_fingerprint,
                    destination=payload.destination,
                    actor=actor,
                    outcome=payload.outcome,
                    selected_value=(
                        None
                        if payload.selected_value is None
                        else payload.selected_value.model_dump(mode="json")
                    ),
                    idempotency_key=idempotency_key,
                )
            )
        )
        return acknowledgement

    def field_effect_call(callable_):
        try:
            return callable_()
        except (GuardedApplicationError, FieldEffectError, RuntimeError) as error:
            code = getattr(error, "code", "unavailable")
            status_code = 404 if code == "not_found" else 409 if code in {
                "claim_conflict", "transition_conflict", "draining", "paused"
            } else 503
            raise HTTPException(status_code, detail={"code": code}) from None

    @app.get("/api/v1/activity", response_model=_ActivityResponse,
             responses={status: {"model": _ErrorResponse} for status in (401, 503)})
    def activity_status(
        ml_session: str = Depends(authenticated_session_cookie),
    ) -> _ActivityResponse:
        _require_session(sessions, ml_session)
        return _ActivityResponse.model_validate(field_effect_call(application.activity))

    @app.post("/api/v1/activity/consent", response_model=_ActivityResponse,
              responses={status: {"model": _ErrorResponse} for status in (401, 403, 409, 422, 503)})
    def enable_guarded_activity(
        payload: _ActivityConsentRequest,
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> _ActivityResponse:
        _require_session(sessions, ml_session, x_csrf_token)
        return _ActivityResponse.model_validate(field_effect_call(
            lambda: application.enable_activity(tuple(
                (grant.library, tuple(grant.destinations))
                for grant in payload.grants
            ))
        ))

    @app.post("/api/v1/activity/{connection}/{library}/revoke",
              response_model=_ActivityResponse,
              responses={status: {"model": _ErrorResponse} for status in (401, 403, 409, 422, 503)})
    def revoke_guarded_activity(
        connection: str,
        library: str,
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> _ActivityResponse:
        _require_session(sessions, ml_session, x_csrf_token)
        return _ActivityResponse.model_validate(field_effect_call(
            lambda: application.revoke_activity(connection, library)
        ))

    @app.post(
        "/api/v1/review-cases/{review_case_id}/fields/{destination}/return-to-automatic",
        status_code=204,
        responses={status: {"model": _ErrorResponse}
                   for status in (401, 403, 404, 409, 422, 503)},
    )
    def return_field_to_automatic(
        review_case_id: str,
        destination: str,
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> Response:
        _require_session(sessions, ml_session, x_csrf_token)
        _require_case_id(review_case_id)
        if destination not in SUPPORTED_DESTINATIONS:
            raise HTTPException(404, detail={"code": "not_found"})
        field_effect_call(
            lambda: application.return_to_automatic(
                review_case_id, destination
            )
        )
        return Response(status_code=204)

    @app.get("/api/v1/field-effects/{plan_id}", response_model=dict[str, object],
             responses={status: {"model": _ErrorResponse} for status in (401, 404, 422, 503)})
    def field_effect_status(plan_id: str,
                            ml_session: str = Depends(authenticated_session_cookie)) -> dict[str, object]:
        if not re.fullmatch(r"effect_plan_[0-9a-f]{64}", plan_id):
            raise HTTPException(404, detail={"code": "not_found"})
        return field_effect_call(lambda: application.field_effect_status(plan_id))

    @app.post("/api/v1/field-effects/{plan_id}/apply", response_model=dict[str, object],
              responses={status: {"model": _ErrorResponse} for status in (401, 403, 404, 409, 422, 503)})
    def apply_field_effect(plan_id: str, ml_session: str = Depends(authenticated_session_cookie),
                           x_csrf_token: str = Header()) -> dict[str, object]:
        _require_session(sessions, ml_session, x_csrf_token)
        if not re.fullmatch(r"effect_plan_[0-9a-f]{64}", plan_id):
            raise HTTPException(404, detail={"code": "not_found"})
        return field_effect_call(lambda: application.apply_field_effect(plan_id))

    @app.post("/api/v1/field-effects/{plan_id}/reconcile", response_model=dict[str, object],
              responses={status: {"model": _ErrorResponse} for status in (401, 403, 404, 409, 422, 503)})
    def reconcile_field_effect(plan_id: str, ml_session: str = Depends(authenticated_session_cookie),
                               x_csrf_token: str = Header()) -> dict[str, object]:
        _require_session(sessions, ml_session, x_csrf_token)
        if not re.fullmatch(r"effect_plan_[0-9a-f]{64}", plan_id):
            raise HTTPException(404, detail={"code": "not_found"})
        return field_effect_call(
            lambda: application.reconcile_field_effect(plan_id)
        )

    @app.post("/api/v1/field-effects/pause", status_code=204,
              responses={status: {"model": _ErrorResponse} for status in (401, 403, 422, 503)})
    def pause_field_effects(ml_session: str = Depends(authenticated_session_cookie),
                            x_csrf_token: str = Header()) -> Response:
        _require_session(sessions, ml_session, x_csrf_token)
        field_effect_call(application.pause_field_effects)
        return Response(status_code=204)

    @app.post("/api/v1/field-effects/resume", status_code=204,
              responses={status: {"model": _ErrorResponse} for status in (401, 403, 422, 503)})
    def resume_field_effects(ml_session: str = Depends(authenticated_session_cookie),
                             x_csrf_token: str = Header()) -> Response:
        _require_session(sessions, ml_session, x_csrf_token)
        field_effect_call(application.resume_field_effects)
        return Response(status_code=204)

    @app.get("/api/v1/openapi.json", include_in_schema=False)
    def openapi_document(
        ml_session: str | None = Security(session_cookie),
    ) -> JSONResponse:
        _require_session(sessions, ml_session)
        return JSONResponse(app.openapi())


def _register_browse_and_preparation_routes(
    app: FastAPI,
    application: ReviewApplication,
    session_cookie: APIKeyCookie,
    authenticated_session_cookie: Callable[[str | None], str],
) -> None:
    sessions = application

    @app.get(
        "/api/v1/library",
        response_model=_LibraryResponse,
        responses={
            status: {"model": _ErrorResponse}
            for status in (400, 401, 422, 503)
        },
    )
    def library(
        lens: str = Query(default="needs_attention", max_length=32),
        q: str | None = Query(default=None, max_length=128),
        limit: int = Query(default=25, ge=1, le=50),
        cursor: str | None = Query(default=None, max_length=128),
        ml_session: str | None = Security(session_cookie),
    ) -> _LibraryResponse:
        _require_session(sessions, ml_session)
        return _LibraryResponse.model_validate(
            _query_call(
                lambda: application.library(
                    lens=lens,
                    search=q,
                    limit=limit,
                    cursor=cursor,
                )
            ).to_data()
        )

    @app.get(
        "/api/v1/attention",
        response_model=_AttentionResponse,
        responses={
            status: {"model": _ErrorResponse}
            for status in (400, 401, 422, 503)
        },
    )
    def attention(
        q: str | None = Query(default=None, max_length=128),
        limit: int = Query(default=25, ge=1, le=50),
        cursor: str | None = Query(default=None, max_length=128),
        ml_session: str | None = Security(session_cookie),
    ) -> _AttentionResponse:
        _require_session(sessions, ml_session)
        return _AttentionResponse.model_validate(
            _query_call(
                lambda: application.attention(
                    search=q,
                    limit=limit,
                    cursor=cursor,
                )
            ).to_data()
        )

    @app.get(
        "/api/v1/review-preparation",
        response_model=_PreparationResponse,
        responses={status: {"model": _ErrorResponse} for status in (401, 503)},
    )
    def review_preparation_status(
        ml_session: str | None = Security(session_cookie),
    ) -> _PreparationResponse:
        _require_session(sessions, ml_session)
        try:
            return _PreparationResponse.model_validate(
                application.preparation().to_data()
            )
        except Exception:
            raise HTTPException(503, detail={"code": "persistence"}) from None

    @app.post(
        "/api/v1/review-preparation/{connection}/{library_name}/scan",
        response_model=_PreparationProgress,
        status_code=202,
        responses={
            status: {"model": _ErrorResponse}
            for status in (400, 401, 403, 404, 409, 422, 503)
        },
    )
    def request_review_scan(
        connection: str,
        library_name: str,
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> _PreparationProgress:
        _require_session(sessions, ml_session, x_csrf_token)
        if not 1 <= len(connection) <= 64 or not 1 <= len(library_name) <= 64:
            raise HTTPException(404, detail={"code": "library"})
        try:
            return _PreparationProgress.model_validate(
                application.request_review_scan(connection, library_name)
            )
        except (RuntimeError, StopIteration) as error:
            raise _preparation_http_error(error) from None
        except Exception:
            raise HTTPException(503, detail={"code": "persistence"}) from None

    def preparation_control(paused: bool, ml_session: str, csrf: str) -> _PreparationResponse:
        _require_session(sessions, ml_session, csrf)
        try:
            return _PreparationResponse.model_validate(
                (
                    application.pause_review_preparation()
                    if paused
                    else application.resume_review_preparation()
                ).to_data()
            )
        except (RuntimeError, StopIteration) as error:
            raise _preparation_http_error(error) from None
        except Exception:
            raise HTTPException(503, detail={"code": "persistence"}) from None

    @app.post(
        "/api/v1/review-preparation/pause",
        response_model=_PreparationResponse,
        responses={status: {"model": _ErrorResponse} for status in (401, 403, 422, 503)},
    )
    def pause_review_preparation(
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> _PreparationResponse:
        return preparation_control(True, ml_session, x_csrf_token)

    @app.post(
        "/api/v1/review-preparation/resume",
        response_model=_PreparationResponse,
        responses={status: {"model": _ErrorResponse} for status in (401, 403, 422, 503)},
    )
    def resume_review_preparation(
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> _PreparationResponse:
        return preparation_control(False, ml_session, x_csrf_token)

    @app.post(
        "/api/v1/review-preparation/{connection}/{library_name}/retry",
        response_model=_PreparationProgress,
        status_code=202,
        responses={status: {"model": _ErrorResponse} for status in (401, 403, 404, 409, 422, 503)},
    )
    def retry_review_preparation(
        connection: str,
        library_name: str,
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> _PreparationProgress:
        _require_session(sessions, ml_session, x_csrf_token)
        if not 1 <= len(connection) <= 64 or not 1 <= len(library_name) <= 64:
            raise HTTPException(404, detail={"code": "library"})
        try:
            return _PreparationProgress.model_validate(
                application.retry_review_scan(connection, library_name)
            )
        except (RuntimeError, StopIteration) as error:
            raise _preparation_http_error(error, retry=True) from None

    @app.get(
        "/api/v1/review-cases/{review_case_id}/evidence",
        response_model=_EvidenceResponse,
        responses={
            status: {"model": _ErrorResponse}
            for status in (400, 401, 404, 409, 422)
        },
    )
    def case_evidence(
        review_case_id: str,
        limit: int = Query(default=50, ge=1, le=50),
        cursor: str | None = Query(default=None, max_length=128),
        ml_session: str | None = Security(session_cookie),
    ) -> _EvidenceResponse:
        _require_session(sessions, ml_session)
        _require_case_id(review_case_id)
        return _EvidenceResponse.model_validate(
            _case_call(
                lambda: application.evidence(
                    review_case_id,
                    limit=limit,
                    cursor=cursor,
                )
            )
        )

    @app.get(
        "/api/v1/review-cases/{review_case_id}/history",
        response_model=_HistoryResponse,
        responses={
            status: {"model": _ErrorResponse}
            for status in (400, 401, 404, 409, 422)
        },
    )
    def case_history(
        review_case_id: str,
        ml_session: str | None = Security(session_cookie),
    ) -> _HistoryResponse:
        _require_session(sessions, ml_session)
        _require_case_id(review_case_id)
        return _HistoryResponse.model_validate(
            _case_call(lambda: application.history(review_case_id))
        )


def _register_html_routes(
    app: FastAPI,
    application: ReviewApplication,
    session_cookie: APIKeyCookie,
    browser_csrf: Callable[[str], str],
    asset_loader: ReactAssetLoader,
) -> None:
    sessions = application

    def shell_response(ml_session: str | None) -> HTMLResponse:
        _require_session(sessions, ml_session)
        assert ml_session is not None
        bundle = asset_loader.load()
        if isinstance(bundle, ReactAssetRecovery):
            return _react_asset_recovery_response()
        try:
            return HTMLResponse(bundle.shell_html(browser_csrf(ml_session)))
        except (OSError, ReactAssetError):
            return _react_asset_recovery_response()

    @app.get("/", include_in_schema=False)
    def root_page() -> RedirectResponse:
        return RedirectResponse(url="/login", status_code=303)

    @app.get("/review/{review_case_id}", include_in_schema=False)
    def review_page(
        review_case_id: str,
        ml_session: str | None = Cookie(default=None),
    ) -> HTMLResponse:
        return shell_response(ml_session)

    @app.get("/library", include_in_schema=False)
    def library_page(
        ml_session: str | None = Cookie(default=None),
    ) -> HTMLResponse:
        return shell_response(ml_session)

    @app.get("/attention", include_in_schema=False)
    def attention_page(
        ml_session: str | None = Cookie(default=None),
    ) -> HTMLResponse:
        return shell_response(ml_session)

    @app.get("/policy", include_in_schema=False)
    def policy_page(
        ml_session: str | None = Cookie(default=None),
    ) -> HTMLResponse:
        return shell_response(ml_session)

    @app.get("/activity", include_in_schema=False)
    def activity_page(
        ml_session: str | None = Cookie(default=None),
    ) -> HTMLResponse:
        return shell_response(ml_session)

    @app.get("/assets/review-app/{asset_path:path}", include_in_schema=False)
    def react_asset(asset_path: str) -> Response:
        bundle = asset_loader.load()
        if isinstance(bundle, ReactAssetRecovery):
            return _react_asset_recovery_response()
        selected = next(
            (asset for asset in bundle.assets if asset.path == asset_path), None
        )
        if selected is None or selected.kind == "shell":
            raise HTTPException(404, detail={"code": "asset_not_found"})
        try:
            payload = bundle.read(asset_path)
        except (OSError, ReactAssetError):
            return _react_asset_recovery_response()
        media_type = "text/css" if selected.kind == "style" else "text/javascript"
        return Response(payload, media_type=media_type)


def _react_asset_recovery_response() -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<title>Review workspace unavailable</title></head><body>"
        "<main><h1>Review workspace unavailable</h1>"
        "<p>Ask the owner to restart after rebuilding the installed assets.</p>"
        "</main></body></html>",
        status_code=503,
    )


def load_owner_password_verifier(path: Path) -> Callable[[str], bool]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    if not getattr(os, "O_NOFOLLOW", 0):
        raise ReviewWebError("password_hash")
    descriptor = os.open(path, flags)
    try:
        information = os.fstat(descriptor)
        if not stat.S_ISREG(information.st_mode) or information.st_mode & 0o077:
            raise ReviewWebError("password_hash")
        payload = os.read(descriptor, 1025)
    finally:
        os.close(descriptor)
    try:
        encoded = payload.decode("utf-8", "strict").strip()
        if not encoded or len(payload) > 1024 or "\n" in encoded or "\r" in encoded:
            raise ValueError
        if (
            not encoded.startswith("$argon2id$")
            or PasswordHasher().check_needs_rehash(encoded)
        ):
            raise ValueError
    except (UnicodeError, ValueError, InvalidHashError) as error:
        raise ReviewWebError("password_hash") from error

    def verify(password: str) -> bool:
        try:
            return PasswordHasher().verify(encoded, password)
        except (VerificationError, InvalidHashError):
            return False

    return verify


def _request_matches_origin(request: Request, configuration: ReviewWebConfiguration) -> bool:
    host = request.headers.get("host", "")
    scheme = request.url.scheme
    forwarded_host = request.headers.get("x-forwarded-host")
    forwarded_proto = request.headers.get("x-forwarded-proto")
    peer = None if request.client is None else request.client.host
    if peer in configuration.trusted_proxy_ips:
        if forwarded_host is not None:
            if "," in forwarded_host:
                return False
            host = forwarded_host
        if forwarded_proto is not None:
            if forwarded_proto not in {"http", "https"}:
                return False
            scheme = forwarded_proto
    elif forwarded_host is not None or forwarded_proto is not None:
        return False
    expected = urlsplit(configuration.canonical_origin)
    return scheme == expected.scheme and host == expected.netloc


def _require_session(
    sessions: ReviewApplication,
    session_token: str | None,
    csrf_token: str | None = None,
) -> str:
    if session_token is None:
        raise HTTPException(401, detail={"code": "unauthorized"})
    try:
        return sessions.authenticate(session_token, csrf_token)
    except ReviewWebError as error:
        status = 403 if error.code == "csrf" else 401
        raise HTTPException(status, detail={"code": error.code}) from None


def _require_case_id(value: str) -> None:
    if _CASE_ID.fullmatch(value) is None:
        raise HTTPException(404, detail={"code": "not_found"})


def _case_call(call: Callable[[], dict[str, object]]) -> dict[str, object]:
    try:
        return call()
    except ReviewWebError as error:
        status = {"request": 400, "stale": 409}.get(error.code, 404)
        raise HTTPException(status, detail={"code": error.code}) from None


def _query_call(call: Callable[[], dict[str, object]]) -> dict[str, object]:
    try:
        return call()
    except ReviewWebError as error:
        status = 400 if error.code == "request" else 503
        raise HTTPException(status, detail={"code": error.code}) from None


def _decision_call(call: Callable[[], dict[str, object]]) -> dict[str, object]:
    try:
        return call()
    except ReviewWebError as error:
        status = {
            "stale": 409,
            "already_decided": 409,
            "idempotency_conflict": 409,
            "invalid": 422,
            "invalid_selection": 422,
            "not_found": 404,
            "unsupported": 422,
            "persistence": 503,
        }.get(error.code, 503)
        raise HTTPException(status, detail={"code": error.code}) from None


def _error(status_code: int, code: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code}, "schema_version": "review-error-v1"},
        status_code=status_code,
    )


def _secured_error(status_code: int, code: str) -> JSONResponse:
    response = _error(status_code, code)
    _set_security_headers(response, hsts=True)
    return response


def _set_security_headers(response: Response, *, hsts: bool) -> None:
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; form-action 'self'; "
        "frame-ancestors 'none'; base-uri 'none'"
    )
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    if hsts:
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
