"""FastAPI adapter for exact-current review and one human decision command."""

from __future__ import annotations

import os
import re
import stat
import threading
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, TypeVar
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
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.security import APIKeyCookie
from pydantic import BaseModel, ConfigDict, Field, StrictStr
from starlette.types import Message, Receive, Scope, Send

from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.application.field_effects import (
    GuardedApplicationError,
    GuardedFieldApplication,
)
from mechanical_librarian.domain.field_effect import FieldEffectError
from mechanical_librarian.application.metadata_policy import AutomaticSafetyControls, MetadataPolicyApplicationError, PolicyNotebook, PolicyTransition
from mechanical_librarian.application.operations import (
    OperationalStatus, OperationsError, StageTimer, SupportExporter,
    TimingRepository,
)
from mechanical_librarian.application.review_web import (
    BrowseReviewLibrary,
    CurrentReviewCaseSnapshot,
    InspectCurrentReviewCase,
    OwnerSessionService,
    RecordHumanDecision,
    ReviewAttentionSnapshot,
    ReviewLibrarySnapshot,
    ReviewWebError,
    ReviewWebRepository,
)
from mechanical_librarian.application.review_pipeline import (
    ReviewPipelineError,
    ReviewPreparationSupervisor,
)
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.metadata_policy import MetadataPolicyError, MetadataPolicyProfile
from mechanical_librarian.interfaces.web import review_frontend


_SerializedResult = TypeVar("_SerializedResult")
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


class _ReviewField(_ClosedModel):
    current: _TargetField
    deterministic_disposition: str
    destination: str
    human_decision: _DecisionProjection | None
    automatic_decision: _AutomaticDecisionProjection | None
    manual_required: bool
    needs_attention: bool
    notices: list[str]
    proposed_values: list[_ProposedValue]
    provenance: list[str]
    proposal_id: str
    target_fingerprint: str
    uncertainty: list[str]


class _TargetIdentity(_ClosedModel):
    connection: str
    item: str
    library: str


class _ModelRecommendation(_ClosedModel):
    candidate_id: str | None
    citations: list[str]
    problems: list[str]
    proposal_id: str
    reasons: list[str]
    recommendation: str


class _ModelAdviceResult(_ClosedModel):
    recommendations: list[_ModelRecommendation]
    review_request_id: str


class _ModelAdvice(_ClosedModel):
    results: list[_ModelAdviceResult]
    state: str


class _CaseDetailResponse(_ClosedModel):
    current: bool
    fields: list[_ReviewField]
    model_advice: _ModelAdvice
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
    attention_count: int
    attention_state: str
    detail_url: str
    dispositions: list[str]
    item: str
    library: str
    review_case_id: str
    title: str


class _LibraryResponse(_ClosedModel):
    counts: _ReviewCounts
    items: list[_LibraryWork]
    lens: str
    next_cursor: str | None
    query: str | None
    schema_version: str


class _AttentionItem(_ClosedModel):
    destination: str
    detail_url: str
    item: str
    library: str
    notices: list[str]
    provenance: list[str]
    review_case_id: str
    title: str


class _AttentionResponse(_ClosedModel):
    items: list[_AttentionItem]
    next_cursor: str | None
    query: str | None
    schema_version: str
    total: int


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
    schema_version: Literal["metadata-policy-profile-v1"] = "metadata-policy-profile-v1"


class _PolicyProfile(_ClosedModel):
    destinations: dict[StrictStr, StrictStr] = Field(max_length=16)
    name: StrictStr = Field(min_length=1, max_length=80)
    options: dict[StrictStr, StrictStr] = Field(max_length=16)
    schema_version: Literal["metadata-policy-profile-v1"]


class _PolicyProfileResponse(_ClosedModel):
    policy_id: str
    profile: _PolicyProfile
    schema_version: Literal["metadata-policy-notebook-v1"] = "metadata-policy-notebook-v1"


class _PolicyImpactResponse(_ClosedModel):
    affected_categories: list[str]
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


def _policy_problem(error: MetadataPolicyError) -> str:
    """Translate closed-domain failures without reflecting input values."""
    return {
        "policy option is unavailable": "policy_option_unavailable",
        "policy destination is unavailable": "policy_destination_unavailable",
        "explicit policy is unsupported": "policy_explicit_unsupported",
    }.get(str(error), "policy_validation")


def _require_permitted_policy_destinations(
    profile: MetadataPolicyProfile, notebook: PolicyNotebook
) -> None:
    notebook.validate(profile)


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
    repository: ReviewWebRepository,
    configuration: ReviewWebConfiguration,
    *,
    clock: Callable[[], int] = lambda: int(time.time()),
    close_repository: bool = False,
    review_preparation: ReviewPreparationSupervisor | None = None,
    policy_libraries: tuple[tuple[str, dict[str, object] | None], ...] = (),
    field_application: GuardedFieldApplication | None = None,
    operational_timings: TimingRepository | None = None,
    operational_support: SupportExporter | None = None,
    operational_timer: StageTimer | None = None,
) -> FastAPI:
    serialized_repository = _SerializedReviewRepository(repository)
    operational_status = (
        None if operational_timings is None else OperationalStatus(
            snapshot=lambda: serialized_repository.call_serialized(
                repository.operational_snapshot  # type: ignore[attr-defined]
            ),
            timings=operational_timings,
            support=operational_support,
        )
    )
    verifier = _owner_password_verifier(Path(configuration.owner_password_hash_file))
    sessions = OwnerSessionService(
        serialized_repository,
        verifier,
        clock,
        configuration.session_lifetime_seconds,
    )
    cases = InspectCurrentReviewCase(serialized_repository)
    timing = operational_timer or StageTimer(operational_timings, clock=clock)
    decisions = RecordHumanDecision(serialized_repository, clock, timing)
    if field_application is not None:
        field_application.attach_timing(timing)
    browse = BrowseReviewLibrary(serialized_repository)
    overrides = {name: value for name, value in policy_libraries if value is not None}
    notebook = PolicyNotebook(serialized_repository, tuple(name for name, _ in policy_libraries), overrides)
    automatic_safety = AutomaticSafetyControls(serialized_repository)
    origin = urlsplit(configuration.canonical_origin)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if operational_status is not None:
            operational_status.start()
        if field_application is not None:
            field_application.start()
        if review_preparation is not None:
            review_preparation.start()
        try:
            yield
        finally:
            if operational_status is not None:
                operational_status.begin_drain()
            if field_application is not None:
                field_application.begin_drain()
            if review_preparation is not None:
                review_preparation.stop()
            if close_repository:
                repository.close()  # type: ignore[attr-defined]

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
        ml_session: str | None = Security(session_cookie),
    ) -> str:
        _require_session(sessions, ml_session)
        assert ml_session is not None
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
        ready = operational_status is None or operational_status.ready()
        return PlainTextResponse("ready\n" if ready else "draining\n",
                                 status_code=200 if ready else 503)

    @app.get("/api/v1/operations/status", response_model=_OperationalResponse,
             responses={status: {"model": _ErrorResponse} for status in (401, 503)})
    def operations_status(
        ml_session: str = Depends(authenticated_session_cookie),
    ) -> _OperationalResponse:
        _require_session(sessions, ml_session)
        if operational_status is None:
            raise HTTPException(503, detail={"code": "operations_unavailable"})
        try:
            return _OperationalResponse.model_validate(operational_status.status())
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
            if operational_status is None:
                raise OperationsError("support_unavailable")
            return _SupportPreviewResponse.model_validate(
                operational_status.support_preview()
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
            if operational_status is None:
                raise OperationsError("support_unavailable")
            return Response(operational_status.support_export(),
                            media_type="application/json",
                            headers={"Content-Disposition":
                                     "attachment; filename=mechanical-librarian-support.json"})
        except OperationsError:
            raise HTTPException(503, detail={"code": "support_unavailable"}) from None

    @app.get("/login", include_in_schema=False)
    def login_page() -> HTMLResponse:
        return HTMLResponse(review_frontend.login_html())

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
            candidate = MetadataPolicyProfile.create(payload.name, payload.options, payload.destinations)
            _require_permitted_policy_destinations(candidate, notebook)
            profile = notebook.create(candidate.name, dict(candidate.options), dict(candidate.destinations))
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
            return _ShadowPostureResponse.model_validate(notebook.enter_preview(clock()))
        except MetadataPolicyApplicationError:
            raise HTTPException(503, detail={"code": "persistence"}) from None

    @app.post("/api/v1/metadata-policy/shadow/confirm-automatic", response_model=_ShadowPostureResponse, responses={status: {"model": _ErrorResponse} for status in (401, 403, 422, 503)})
    def confirm_automatic(ml_session: str = Depends(authenticated_session_cookie), x_csrf_token: str = Header()) -> _ShadowPostureResponse:
        _require_session(sessions, ml_session, x_csrf_token)
        try:
            return _ShadowPostureResponse.model_validate(notebook.confirm_automatic(clock()))
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
            automatic_safety.pause(payload.paused, clock())
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
            automatic_safety.recover(clock(), category)
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
            automatic_safety.suspend(payload.reason, clock(), payload.category)
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
            profile = MetadataPolicyProfile.create(payload.name, payload.options, payload.destinations)
            _require_permitted_policy_destinations(profile, notebook)
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
            profile = MetadataPolicyProfile.create(payload.name, payload.options, payload.destinations)
            _require_permitted_policy_destinations(profile, notebook)
            preview = notebook.preview(profile)
        except MetadataPolicyError as error:
            raise HTTPException(422, detail={"code": _policy_problem(error)}) from None
        except MetadataPolicyApplicationError as error:
            raise _policy_application_http_error(error) from None
        return _PolicyPreviewResponse(policy_id=profile.policy_id, libraries=[_PolicyImpactResponse(affected_categories=list(item.affected_categories), current_unapplied_count=item.current_unapplied_count, library=item.library, resolved_policy_id=item.resolved_policy_id) for item in preview.libraries])

    @app.post("/api/v1/metadata-policy/activate", response_model=_PolicyActivationResponse, responses={status: {"model": _ErrorResponse} for status in (401, 403, 409, 413, 422, 503)})
    def activate_metadata_policy(payload: _PolicyActivationRequest, ml_session: str = Depends(authenticated_session_cookie), x_csrf_token: str = Header()) -> _PolicyActivationResponse:
        _require_session(sessions, ml_session, x_csrf_token)
        try:
            result = notebook.activate(payload.policy_id, payload.expected_active_policy_id, clock())
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
            return _policy_transition_response(notebook.retry(transition_id, clock()))
        except MetadataPolicyApplicationError as error:
            raise _policy_application_http_error(error) from None

    @app.post("/api/v1/metadata-policy/transitions/{transition_id}/acknowledge", response_model=_PolicyTransitionResponse, responses={status: {"model": _ErrorResponse} for status in (401, 403, 409, 422, 503)})
    def acknowledge_metadata_policy_transition(transition_id: str, ml_session: str = Depends(authenticated_session_cookie), x_csrf_token: str = Header()) -> _PolicyTransitionResponse:
        _require_session(sessions, ml_session, x_csrf_token)
        try:
            return _policy_transition_response(notebook.acknowledge(transition_id, clock()))
        except MetadataPolicyApplicationError as error:
            raise _policy_application_http_error(error) from None

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
        return _CaseDetailResponse.model_validate(
            _case_call(lambda: cases.run(review_case_id))
        )

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
        return _DecisionAcknowledgement.model_validate(
            _decision_call(
                lambda: decisions.run(
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

    def serialized_field_effect_call(callable_):
        if field_application is None:
            raise HTTPException(503, detail={"code": "unavailable"})
        try:
            return serialized_repository.call_serialized(callable_)
        except (GuardedApplicationError, FieldEffectError) as error:
            code = getattr(error, "code", "persistence")
            status_code = 404 if code == "not_found" else 409 if code in {
                "claim_conflict", "transition_conflict", "draining", "paused"
            } else 503
            raise HTTPException(status_code, detail={"code": code}) from None

    def field_effect_call(callable_):
        return serialized_field_effect_call(lambda: callable_().to_data())

    @app.get("/api/v1/field-effects/{plan_id}", response_model=dict[str, object],
             responses={status: {"model": _ErrorResponse} for status in (401, 404, 422, 503)})
    def field_effect_status(plan_id: str,
                            ml_session: str = Depends(authenticated_session_cookie)) -> dict[str, object]:
        if not re.fullmatch(r"effect_plan_[0-9a-f]{64}", plan_id):
            raise HTTPException(404, detail={"code": "not_found"})
        application = field_application
        if application is None:
            raise HTTPException(503, detail={"code": "unavailable"})
        return field_effect_call(lambda: application.status(plan_id))

    @app.post("/api/v1/field-effects/{plan_id}/apply", response_model=dict[str, object],
              responses={status: {"model": _ErrorResponse} for status in (401, 403, 404, 409, 422, 503)})
    def apply_field_effect(plan_id: str, ml_session: str = Depends(authenticated_session_cookie),
                           x_csrf_token: str = Header()) -> dict[str, object]:
        _require_session(sessions, ml_session, x_csrf_token)
        if not re.fullmatch(r"effect_plan_[0-9a-f]{64}", plan_id):
            raise HTTPException(404, detail={"code": "not_found"})
        application = field_application
        if application is None:
            raise HTTPException(503, detail={"code": "unavailable"})
        return field_effect_call(lambda: application.apply(plan_id))

    @app.post("/api/v1/field-effects/{plan_id}/reconcile", response_model=dict[str, object],
              responses={status: {"model": _ErrorResponse} for status in (401, 403, 404, 409, 422, 503)})
    def reconcile_field_effect(plan_id: str, ml_session: str = Depends(authenticated_session_cookie),
                               x_csrf_token: str = Header()) -> dict[str, object]:
        _require_session(sessions, ml_session, x_csrf_token)
        if not re.fullmatch(r"effect_plan_[0-9a-f]{64}", plan_id):
            raise HTTPException(404, detail={"code": "not_found"})
        application = field_application
        if application is None:
            raise HTTPException(503, detail={"code": "unavailable"})
        return field_effect_call(lambda: application.reconcile(plan_id))

    @app.post("/api/v1/field-effects/pause", status_code=204,
              responses={status: {"model": _ErrorResponse} for status in (401, 403, 422, 503)})
    def pause_field_effects(ml_session: str = Depends(authenticated_session_cookie),
                            x_csrf_token: str = Header()) -> Response:
        _require_session(sessions, ml_session, x_csrf_token)
        if field_application is None:
            raise HTTPException(503, detail={"code": "unavailable"})
        serialized_field_effect_call(
            lambda: field_application.set_paused(True)
        )
        return Response(status_code=204)

    @app.post("/api/v1/field-effects/resume", status_code=204,
              responses={status: {"model": _ErrorResponse} for status in (401, 403, 422, 503)})
    def resume_field_effects(ml_session: str = Depends(authenticated_session_cookie),
                             x_csrf_token: str = Header()) -> Response:
        _require_session(sessions, ml_session, x_csrf_token)
        if field_application is None:
            raise HTTPException(503, detail={"code": "unavailable"})
        serialized_field_effect_call(
            lambda: field_application.set_paused(False)
        )
        return Response(status_code=204)

    @app.get("/api/v1/openapi.json", include_in_schema=False)
    def openapi_document(
        ml_session: str | None = Security(session_cookie),
    ) -> JSONResponse:
        _require_session(sessions, ml_session)
        return JSONResponse(app.openapi())

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
                lambda: browse.library(
                    lens=lens,
                    search=q,
                    limit=limit,
                    cursor=cursor,
                )
            )
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
                lambda: browse.attention(
                    search=q,
                    limit=limit,
                    cursor=cursor,
                )
            )
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
        if review_preparation is None:
            return _PreparationResponse(libraries=[])
        values: list[_PreparationProgress] = []
        try:
            progress_values, paused = review_preparation.status()
            if len(progress_values) != len(review_preparation.libraries):
                raise ReviewPipelineError("state")
            for (connection, library_name, library_id), progress in zip(
                review_preparation.libraries, progress_values, strict=True
            ):
                values.append(
                    _PreparationProgress(
                        scan_id=None if progress is None else progress.scan_id,
                        connection=connection,
                        library=library_name,
                        library_id=library_id,
                        state="idle" if progress is None else progress.state,
                        checked=0 if progress is None else progress.checked,
                        total=0 if progress is None else progress.total,
                        ready=0 if progress is None else progress.ready,
                        blocked=0 if progress is None else progress.blocked,
                        rescan_requested=(
                            False if progress is None else progress.rescan_requested
                        ),
                    )
                )
        except ReviewPipelineError:
            raise HTTPException(503, detail={"code": "persistence"}) from None
        except Exception:
            raise HTTPException(503, detail={"code": "persistence"}) from None
        return _PreparationResponse(
            libraries=values,
            paused=paused,
            active=any(value.state in {"queued", "discovering", "processing"} for value in values),
            actionable=any(value.state == "completed_with_failures" for value in values),
        )

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
        if review_preparation is None:
            raise HTTPException(503, detail={"code": "unavailable"})
        if not 1 <= len(connection) <= 64 or not 1 <= len(library_name) <= 64:
            raise HTTPException(404, detail={"code": "library"})
        try:
            progress = review_preparation.request(connection, library_name)
            library_id = next(
                item[2]
                for item in review_preparation.libraries
                if item[:2] == (connection, library_name)
            )
        except (ReviewPipelineError, StopIteration) as error:
            code = getattr(error, "code", "")
            status = 404 if code == "library" else 409 if code == "paused" else 503
            raise HTTPException(status, detail={"code": code if status != 503 else "persistence"}) from None
        except Exception:
            raise HTTPException(503, detail={"code": "persistence"}) from None
        return _PreparationProgress(
            scan_id=progress.scan_id,
            connection=progress.connection,
            library=progress.library,
            library_id=library_id,
            state=progress.state,
            checked=progress.checked,
            total=progress.total,
            ready=progress.ready,
            blocked=progress.blocked,
            rescan_requested=progress.rescan_requested,
        )

    def preparation_control(paused: bool, ml_session: str, csrf: str) -> _PreparationResponse:
        _require_session(sessions, ml_session, csrf)
        if review_preparation is None:
            raise HTTPException(503, detail={"code": "unavailable"})
        try:
            review_preparation.set_paused(paused)
            return review_preparation_status(ml_session)
        except ReviewPipelineError:
            raise HTTPException(503, detail={"code": "persistence"}) from None
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
        if review_preparation is None:
            raise HTTPException(503, detail={"code": "unavailable"})
        if not 1 <= len(connection) <= 64 or not 1 <= len(library_name) <= 64:
            raise HTTPException(404, detail={"code": "library"})
        try:
            progress = review_preparation.retry(connection, library_name)
            library_id = next(item[2] for item in review_preparation.libraries if item[:2] == (connection, library_name))
        except (ReviewPipelineError, StopIteration) as error:
            code = getattr(error, "code", "library")
            status = 409 if code in {"retry", "paused"} else 404 if code == "library" else 503
            raise HTTPException(status, detail={"code": code}) from None
        return _PreparationProgress(
            scan_id=progress.scan_id, connection=progress.connection,
            library=progress.library, library_id=library_id, state=progress.state,
            checked=progress.checked, total=progress.total, ready=progress.ready,
            blocked=progress.blocked, rescan_requested=progress.rescan_requested,
        )

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
                lambda: cases.evidence(
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
            _case_call(lambda: cases.history(review_case_id))
        )

    @app.get("/review/{review_case_id}", include_in_schema=False)
    def review_page(
        review_case_id: str,
        ml_session: str | None = Cookie(default=None),
    ) -> HTMLResponse:
        _require_case_id(review_case_id)
        _require_session(sessions, ml_session)
        assert ml_session is not None
        return HTMLResponse(
            review_frontend.review_html(review_case_id, browser_csrf(ml_session))
        )

    @app.get("/library", include_in_schema=False)
    def library_page(
        ml_session: str | None = Cookie(default=None),
    ) -> HTMLResponse:
        _require_session(sessions, ml_session)
        assert ml_session is not None
        return HTMLResponse(
            review_frontend.library_html(
                browser_csrf(ml_session), review_preparation is not None
            )
        )

    @app.get("/attention", include_in_schema=False)
    def attention_page(
        ml_session: str | None = Cookie(default=None),
    ) -> HTMLResponse:
        _require_session(sessions, ml_session)
        assert ml_session is not None
        return HTMLResponse(review_frontend.attention_html(browser_csrf(ml_session)))

    @app.get("/policy", include_in_schema=False)
    def policy_page(
        ml_session: str | None = Cookie(default=None),
    ) -> HTMLResponse:
        _require_session(sessions, ml_session)
        assert ml_session is not None
        return HTMLResponse(review_frontend.policy_html(browser_csrf(ml_session)))

    @app.get("/assets/review.css", include_in_schema=False)
    def review_css() -> Response:
        return Response(review_frontend.REVIEW_CSS, media_type="text/css")

    @app.get("/assets/review.js", include_in_schema=False)
    def review_js() -> Response:
        return Response(review_frontend.REVIEW_JS, media_type="text/javascript")

    @app.get("/assets/policy.js", include_in_schema=False)
    def policy_js() -> Response:
        return Response(review_frontend.POLICY_JS, media_type="text/javascript")

    @app.get("/assets/shadow-policy.js", include_in_schema=False)
    def shadow_policy_js() -> Response:
        return Response(
            review_frontend.SHADOW_POLICY_JS, media_type="text/javascript"
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

    return app


def _owner_password_verifier(path: Path) -> Callable[[str], bool]:
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


class _SerializedReviewRepository:
    """Serialize one cross-thread SQLite connection at the HTTP adapter edge."""

    def __init__(self, repository: ReviewWebRepository) -> None:
        self._repository = repository
        self._lock = threading.RLock()

    def call_serialized(
        self, call: Callable[[], _SerializedResult]
    ) -> _SerializedResult:
        with self._lock:
            return call()

    def begin_login(self, now: int, window_seconds: int, maximum_failures: int) -> bool:
        with self._lock:
            return self._repository.begin_login(now, window_seconds, maximum_failures)

    def create_review_session(self, session_digest: str, csrf_digest: str, created_at: int, expires_at: int) -> None:
        with self._lock:
            self._repository.create_review_session(
                session_digest, csrf_digest, created_at, expires_at
            )

    def authenticate_review_session(self, session_digest: str, now: int) -> tuple[str, str, int]:
        with self._lock:
            return self._repository.authenticate_review_session(session_digest, now)

    def rotate_review_session_csrf(
        self, session_digest: str, csrf_digest: str, now: int
    ) -> str:
        with self._lock:
            return self._repository.rotate_review_session_csrf(
                session_digest, csrf_digest, now
            )

    def revoke_review_session(
        self, session_digest: str, csrf_digest: str, now: int
    ) -> str:
        with self._lock:
            return self._repository.revoke_review_session(
                session_digest, csrf_digest, now
            )

    def get_review_case(self, review_case_id: str) -> str:
        with self._lock:
            return self._repository.get_review_case(review_case_id)

    def read_current_review_case(
        self, review_case_id: str, history_limit: int | None
    ) -> CurrentReviewCaseSnapshot:
        with self._lock:
            return self._repository.read_current_review_case(
                review_case_id, history_limit
            )

    def read_review_library(
        self,
        search: str | None,
        limit: int,
        offset: int,
        attention: bool | None,
    ) -> ReviewLibrarySnapshot:
        with self._lock:
            return self._repository.read_review_library(
                search, limit, offset, attention
            )

    def read_review_attention(
        self, search: str | None, limit: int, offset: int
    ) -> ReviewAttentionSnapshot:
        with self._lock:
            return self._repository.read_review_attention(search, limit, offset)

    def review_case_exists(self, review_case_id: str) -> bool:
        with self._lock:
            return self._repository.review_case_exists(review_case_id)

    def find_human_decision(self, idempotency_digest: str) -> str | None:
        with self._lock:
            return self._repository.find_human_decision(idempotency_digest)

    def record_human_decision(self, decision: HumanDecision) -> str:
        with self._lock:
            return self._repository.record_human_decision(decision)

    def record_field_effect(self, operation):
        with self._lock:
            return self._repository.record_field_effect(operation)

    def get_field_effect(self, plan_id: str):
        with self._lock:
            return self._repository.get_field_effect(plan_id)

    def claim_field_effect(self, plan_id: str, expected_state: str, claim_token: str, claimed_at: int):
        with self._lock:
            return self._repository.claim_field_effect(plan_id, expected_state, claim_token, claimed_at)

    def transition_field_effect(self, plan_id: str, expected_state: str, state: str, changed_at: int, failure: str | None = None):
        with self._lock:
            return self._repository.transition_field_effect(plan_id, expected_state, state, changed_at, failure)

    def supersede_field_effect(self, decision_id: str, superseded_at: int) -> int:
        with self._lock:
            return self._repository.supersede_field_effect(decision_id, superseded_at)

    def set_field_effect_paused(self, paused: bool, changed_at: int) -> None:
        with self._lock:
            self._repository.set_field_effect_paused(paused, changed_at)

    def field_effect_paused(self) -> bool:
        with self._lock:
            return self._repository.field_effect_paused()

    def create_metadata_policy_draft(self, document: str) -> str:
        with self._lock:
            return self._repository.create_metadata_policy_draft(document)

    def shadow_posture(self) -> str:
        with self._lock:
            if not hasattr(self._repository, "shadow_posture"):
                return "review"
            return self._repository.shadow_posture()

    def enter_shadow_preview(self, updated_at: int) -> str:
        with self._lock:
            return self._repository.enter_shadow_preview(updated_at)

    def activate_qualified_automatic(self, updated_at: int) -> str:
        with self._lock:
            return self._repository.activate_qualified_automatic(updated_at)

    def automatic_safety_state(self):
        with self._lock:
            return self._repository.automatic_safety_state()

    def set_automatic_safety_state(self, state, updated_at: int) -> None:
        with self._lock:
            self._repository.set_automatic_safety_state(state, updated_at)

    def list_shadow_outcomes(self, limit: int) -> tuple[tuple[str, int], ...]:
        with self._lock:
            if not hasattr(self._repository, "list_shadow_outcomes"):
                return ()
            return self._repository.list_shadow_outcomes(limit)

    def get_metadata_policy_draft(self, policy_id: str) -> str:
        with self._lock:
            return self._repository.get_metadata_policy_draft(policy_id)

    def current_unapplied_count(self, library: str) -> int:
        with self._lock:
            return self._repository.current_unapplied_count(library)

    def active_metadata_policy(self) -> str:
        with self._lock:
            if not hasattr(self._repository, "active_metadata_policy"):
                return MetadataPolicyProfile.builtin().canonical_json
            return self._repository.active_metadata_policy()

    def activate_metadata_policy(self, policy_id: str, expected_active_policy_id: str, resolutions: tuple[tuple[str, str, str], ...], activated_at: int) -> PolicyTransition | None:
        with self._lock:
            return self._repository.activate_metadata_policy(policy_id, expected_active_policy_id, resolutions, activated_at)

    def metadata_policy_transition(self) -> PolicyTransition | None:
        with self._lock:
            return self._repository.metadata_policy_transition()

    def retry_metadata_policy_transition(self, transition_id: str, retried_at: int) -> PolicyTransition:
        with self._lock:
            return self._repository.retry_metadata_policy_transition(transition_id, retried_at)

    def acknowledge_metadata_policy_transition(self, transition_id: str, acknowledged_at: int) -> PolicyTransition:
        with self._lock:
            return self._repository.acknowledge_metadata_policy_transition(transition_id, acknowledged_at)

    def current_resolved_policy_id(self, library: str) -> str:
        with self._lock:
            return self._repository.current_resolved_policy_id(library)

    def current_resolved_metadata_policy(self, library: str):
        with self._lock:
            if not hasattr(self._repository, "current_resolved_metadata_policy"):
                return None
            return self._repository.current_resolved_metadata_policy(library)

    def metadata_policy_resolution_for_preview(self, library: str):
        with self._lock:
            if hasattr(
                self._repository, "metadata_policy_resolution_for_preview"
            ):
                return self._repository.metadata_policy_resolution_for_preview(
                    library
                )
            if not hasattr(self._repository, "current_resolved_metadata_policy"):
                return None
            return self._repository.current_resolved_metadata_policy(library)


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
    sessions: OwnerSessionService,
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
