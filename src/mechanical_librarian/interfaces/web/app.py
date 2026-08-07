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
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.security import APIKeyCookie
from pydantic import BaseModel, ConfigDict, Field, StrictStr
from starlette.types import Message, Receive, Scope, Send

from mechanical_librarian.application.configuration import ReviewWebConfiguration
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
from mechanical_librarian.interfaces.web import review_frontend


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


class _ReviewField(_ClosedModel):
    current: _TargetField
    deterministic_disposition: str
    destination: str
    human_decision: _DecisionProjection | None
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


class _HistoryResponse(_ClosedModel):
    items: list[_HistoryTransitionItem | _HistoryDecisionItem]
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
    schema_version: Literal["review-preparation-v1"] = "review-preparation-v1"


class _DecisionRequest(_ClosedModel):
    destination: StrictStr = Field(min_length=1, max_length=64)
    outcome: Literal["accept", "keep_current", "reject"]
    proposal_id: StrictStr = Field(pattern=r"^review_proposal_[0-9a-f]{64}$")
    selected_value: _ProposedValue | None = None
    target_fingerprint: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")


class _DecisionAcknowledgement(_DecisionProjection):
    schema_version: str


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
        if scope["type"] != "http" or not (is_login or is_decision):
            await self._app(scope, receive, send)
            return
        maximum = self._maximum_bytes if is_login else self._decision_maximum_bytes
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
) -> FastAPI:
    serialized_repository = _SerializedReviewRepository(repository)
    verifier = _owner_password_verifier(Path(configuration.owner_password_hash_file))
    sessions = OwnerSessionService(
        serialized_repository,
        verifier,
        clock,
        configuration.session_lifetime_seconds,
    )
    cases = InspectCurrentReviewCase(serialized_repository)
    decisions = RecordHumanDecision(serialized_repository, clock)
    browse = BrowseReviewLibrary(serialized_repository)
    origin = urlsplit(configuration.canonical_origin)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if review_preparation is not None:
            review_preparation.start()
        try:
            yield
        finally:
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
        if request.url.path != "/health/live":
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
            raise HTTPException(503, detail={"code": "unavailable"})
        values: list[_PreparationProgress] = []
        try:
            for connection, library_name, library_id in review_preparation.libraries:
                progress = review_preparation.progress(connection, library_name)
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
        return _PreparationResponse(libraries=values)

    @app.post(
        "/api/v1/review-preparation/{connection}/{library_name}/scan",
        response_model=_PreparationProgress,
        status_code=202,
        responses={
            status: {"model": _ErrorResponse}
            for status in (400, 401, 403, 404, 422, 503)
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
            status = 404 if getattr(error, "code", "") == "library" else 503
            raise HTTPException(status, detail={"code": "library" if status == 404 else "persistence"}) from None
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

    @app.get("/assets/review.css", include_in_schema=False)
    def review_css() -> Response:
        return Response(review_frontend.REVIEW_CSS, media_type="text/css")

    @app.get("/assets/review.js", include_in_schema=False)
    def review_js() -> Response:
        return Response(review_frontend.REVIEW_JS, media_type="text/javascript")

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, error: HTTPException) -> JSONResponse:
        detail = error.detail
        code = detail.get("code") if type(detail) is dict else "request"
        return _error(error.status_code, str(code))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, __: RequestValidationError) -> JSONResponse:
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
