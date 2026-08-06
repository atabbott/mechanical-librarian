"""FastAPI adapter for exact-current review and one human decision command."""

from __future__ import annotations

import html
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
from fastapi import Cookie, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, StrictStr
from starlette.types import Message, Receive, Scope, Send

from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.application.review_web import (
    BrowseReviewLibrary,
    InspectCurrentReviewCase,
    OwnerSessionService,
    RecordHumanDecision,
    ReviewWebError,
    ReviewWebRepository,
)
from mechanical_librarian.domain.human_decision import HumanDecision


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
    fact: str
    kind: str
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
                r"/api/v1/review-cases/review_case_[0-9a-f]{64}/decisions",
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
        yield
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
        return HTMLResponse(_LOGIN_HTML)

    @app.post(
        "/api/v1/session/login",
        response_model=_SessionResponse,
        responses={
            status: {"model": _ErrorResponse}
            for status in (401, 413, 422, 429, 503)
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
            status: {"model": _ErrorResponse} for status in (401, 403, 422)
        },
    )
    def logout(
        response: Response,
        ml_session: str | None = Cookie(default=None),
        x_csrf_token: str | None = Header(default=None),
    ) -> Response:
        if ml_session is None or x_csrf_token is None:
            raise HTTPException(403, detail={"code": "csrf"})
        try:
            sessions.logout(ml_session, x_csrf_token)
        except ReviewWebError as error:
            status = 403 if error.code == "csrf" else 401
            raise HTTPException(status, detail={"code": error.code}) from None
        response.delete_cookie(_COOKIE_NAME, path="/", secure=True, httponly=True, samesite="strict")
        response.status_code = 204
        return response

    @app.get(
        "/api/v1/review-cases/{review_case_id}",
        response_model=_CaseDetailResponse,
        responses={
            status: {"model": _ErrorResponse}
            for status in (401, 404, 409, 422)
        },
    )
    def case_detail(
        review_case_id: str,
        ml_session: str | None = Cookie(default=None),
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
            for status in (401, 403, 409, 413, 422, 503)
        },
    )
    def record_decision(
        review_case_id: str,
        payload: _DecisionRequest,
        ml_session: str | None = Cookie(default=None),
        x_csrf_token: str | None = Header(default=None),
        idempotency_key: str | None = Header(
            default=None, min_length=16, max_length=128
        ),
    ) -> _DecisionAcknowledgement:
        if x_csrf_token is None:
            raise HTTPException(403, detail={"code": "csrf"})
        actor = _require_session(sessions, ml_session, x_csrf_token)
        _require_case_id(review_case_id)
        if idempotency_key is None:
            raise HTTPException(422, detail={"code": "invalid"})
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
        ml_session: str | None = Cookie(default=None),
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
        ml_session: str | None = Cookie(default=None),
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
        ml_session: str | None = Cookie(default=None),
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
        "/api/v1/review-cases/{review_case_id}/evidence",
        response_model=_EvidenceResponse,
        responses={
            status: {"model": _ErrorResponse}
            for status in (401, 404, 409, 422)
        },
    )
    def case_evidence(
        review_case_id: str,
        ml_session: str | None = Cookie(default=None),
    ) -> _EvidenceResponse:
        _require_session(sessions, ml_session)
        _require_case_id(review_case_id)
        return _EvidenceResponse.model_validate(
            _case_call(lambda: cases.evidence(review_case_id))
        )

    @app.get(
        "/api/v1/review-cases/{review_case_id}/history",
        response_model=_HistoryResponse,
        responses={
            status: {"model": _ErrorResponse}
            for status in (401, 404, 409, 422)
        },
    )
    def case_history(
        review_case_id: str,
        ml_session: str | None = Cookie(default=None),
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
        _require_session(sessions, ml_session)
        _require_case_id(review_case_id)
        return HTMLResponse(_review_html(review_case_id))

    @app.get("/library", include_in_schema=False)
    def library_page(
        ml_session: str | None = Cookie(default=None),
    ) -> HTMLResponse:
        _require_session(sessions, ml_session)
        return HTMLResponse(_LIBRARY_HTML)

    @app.get("/attention", include_in_schema=False)
    def attention_page(
        ml_session: str | None = Cookie(default=None),
    ) -> HTMLResponse:
        _require_session(sessions, ml_session)
        return HTMLResponse(_ATTENTION_HTML)

    @app.get("/assets/review.css", include_in_schema=False)
    def review_css() -> Response:
        return Response(_REVIEW_CSS, media_type="text/css")

    @app.get("/assets/review.js", include_in_schema=False)
    def review_js() -> Response:
        return Response(_REVIEW_JS, media_type="text/javascript")

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

    def revoke_review_session(self, session_digest: str, now: int) -> None:
        with self._lock:
            self._repository.revoke_review_session(session_digest, now)

    def get_review_case(self, review_case_id: str) -> str:
        with self._lock:
            return self._repository.get_review_case(review_case_id)

    def get_review_case_report(self, review_case_id: str) -> str:
        with self._lock:
            return self._repository.get_review_case_report(review_case_id)

    def get_review_model_advice(self, review_case_id: str) -> dict[str, object]:
        with self._lock:
            return self._repository.get_review_model_advice(review_case_id)

    def get_review_case_history(self, review_case_id: str, limit: int) -> tuple[dict[str, object], ...]:
        with self._lock:
            return self._repository.get_review_case_history(review_case_id, limit)

    def list_current_review_case_documents(
        self,
        search: str | None,
        limit: int,
        offset: int,
        attention: bool | None,
    ) -> tuple[tuple[str, str], ...]:
        with self._lock:
            return self._repository.list_current_review_case_documents(
                search, limit, offset, attention
            )

    def count_current_review_cases(
        self, search: str | None
    ) -> tuple[int, int]:
        with self._lock:
            return self._repository.count_current_review_cases(search)

    def list_current_attention_documents(
        self, search: str | None, limit: int, offset: int
    ) -> tuple[tuple[str, str, str], ...]:
        with self._lock:
            return self._repository.list_current_attention_documents(
                search, limit, offset
            )

    def count_current_attention_fields(self, search: str | None) -> int:
        with self._lock:
            return self._repository.count_current_attention_fields(search)

    def get_review_case_decisions(
        self, review_case_id: str
    ) -> tuple[str, ...]:
        with self._lock:
            return self._repository.get_review_case_decisions(review_case_id)

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
        status = 409 if error.code == "stale" else 404
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


def _review_html(review_case_id: str) -> str:
    return f"""<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>Review case</title><link rel=\"stylesheet\" href=\"/assets/review.css\"></head><body><header><nav aria-label=\"Review\"><a href=\"/library\">Library</a><a href=\"/attention\">Attention</a><strong>Detail</strong></nav></header><main id=\"review\" data-review-case-id=\"{html.escape(review_case_id, quote=True)}\" tabindex=\"-1\"><p role=\"status\">Loading current case…</p></main><script src=\"/assets/review.js\" defer></script></body></html>"""


_LOGIN_HTML = """<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>Owner sign in</title><link rel=\"stylesheet\" href=\"/assets/review.css\"></head><body><main><h1>Owner sign in</h1><form id=\"login\"><label>Password <input name=\"password\" type=\"password\" autocomplete=\"current-password\" required></label><button>Sign in</button><p id=\"login-status\" role=\"alert\"></p></form></main><script src=\"/assets/review.js\" defer></script></body></html>"""
_LIBRARY_HTML = """<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>Review library</title><link rel=\"stylesheet\" href=\"/assets/review.css\"></head><body><header><nav aria-label=\"Review\"><strong>Library</strong><a href=\"/attention\">Attention</a></nav></header><main id=\"library\" tabindex=\"-1\"><p role=\"status\">Loading bounded library…</p></main><script src=\"/assets/review.js\" defer></script></body></html>"""
_ATTENTION_HTML = """<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>Attention queue</title><link rel=\"stylesheet\" href=\"/assets/review.css\"></head><body><header><nav aria-label=\"Review\"><a href=\"/library\">Library</a><strong>Attention</strong></nav></header><main id=\"attention\" tabindex=\"-1\"><p role=\"status\">Loading bounded attention queue…</p></main><script src=\"/assets/review.js\" defer></script></body></html>"""
_REVIEW_CSS = """*{box-sizing:border-box}body{margin:0;background:#f4f1ea;color:#171b20;font:16px/1.5 system-ui,sans-serif}header{padding:1rem;background:#17232b;color:#fff}nav{display:flex;gap:1rem}header a{color:#fff}main{max-width:70rem;margin:auto;padding:1.25rem}.field{background:#fff;border:1px solid #c7c3ba;border-radius:.75rem;margin:1rem 0;padding:1rem}.lenses{display:flex;flex-wrap:wrap;gap:.75rem}.lenses a,.next{display:inline-block;padding:.55rem .8rem;background:#fff;border:1px solid #156a85;border-radius:.4rem;color:#124f64}.meta{color:#4a5157}button,input{font:inherit;min-height:44px}input{display:block;width:min(28rem,100%);margin:.5rem 0;padding:.6rem}:focus-visible{outline:3px solid #156a85;outline-offset:3px}@media(max-width:390px){main{padding:.75rem}header{position:sticky;top:0}.field{border-radius:.5rem}}"""
_REVIEW_JS = """const element=(name,text)=>{const value=document.createElement(name);value.textContent=text;return value;};
const root=document.querySelector('#review');
const libraryRoot=document.querySelector('#library');
const attentionRoot=document.querySelector('#attention');
const login=document.querySelector('#login');
if(login){login.addEventListener('submit',async event=>{event.preventDefault();const status=document.querySelector('#login-status');const password=new FormData(login).get('password');const response=await fetch('/api/v1/session/login',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({password})});if(response.ok)location.assign('/library');else status.textContent='Sign in failed.';});}
const searchForm=(query)=>{const form=document.createElement('form');form.method='get';const label=element('label','Search title, library, or item');const input=document.createElement('input');input.name='q';input.value=query||'';input.maxLength=128;label.append(input);form.append(label,element('button','Search'));return form;};
const failPage=(target)=>{const alert=element('p','The bounded review data could not be loaded.');alert.setAttribute('role','alert');target.replaceChildren(element('h1','Review unavailable'),alert);target.focus();};
if(libraryRoot){const parameters=new URLSearchParams(location.search);const lens=parameters.get('lens')||'needs_attention';fetch(`/api/v1/library?${parameters}`).then(async response=>{if(!response.ok)throw new Error();return response.json();}).then(data=>{libraryRoot.replaceChildren(element('h1','Review library'),searchForm(data.query));const lenses=document.createElement('nav');lenses.className='lenses';lenses.setAttribute('aria-label','Library lenses');for(const value of ['needs_attention','all','no_attention_needed']){const link=element('a',`${value.replaceAll('_',' ')} (${data.counts[value]})`);const target=new URLSearchParams();target.set('lens',value);if(data.query)target.set('q',data.query);link.href=`/library?${target}`;if(value===lens)link.setAttribute('aria-current','page');lenses.append(link);}libraryRoot.append(lenses);for(const item of data.items){const section=document.createElement('section');section.className='field';const link=element('a',item.title);link.href=item.detail_url;const heading=document.createElement('h2');heading.append(link);section.append(heading,element('p',`${item.library} · ${item.item}`),element('p',`${item.attention_state.replaceAll('_',' ')} · ${item.attention_count} field(s)`),element('p',`Dispositions: ${item.dispositions.join(', ')}`));libraryRoot.append(section);}if(data.next_cursor){const next=element('a','Next page');next.className='next';parameters.set('cursor',data.next_cursor);next.href=`/library?${parameters}`;libraryRoot.append(next);}libraryRoot.focus();}).catch(()=>failPage(libraryRoot));}
if(attentionRoot){const parameters=new URLSearchParams(location.search);fetch(`/api/v1/attention?${parameters}`).then(async response=>{if(!response.ok)throw new Error();return response.json();}).then(data=>{attentionRoot.replaceChildren(element('h1',`Fields needing attention (${data.total})`),searchForm(data.query));for(const item of data.items){const section=document.createElement('section');section.className='field';const link=element('a',`${item.title} · ${item.destination}`);link.href=item.detail_url;const heading=document.createElement('h2');heading.append(link);section.append(heading,element('p',`${item.library} · ${item.item}`),element('p',`Notices: ${item.notices.join(', ')}`),element('p',`Provenance: ${item.provenance.length?item.provenance.join(', '):'none'}`));attentionRoot.append(section);}if(!data.items.length)attentionRoot.append(element('p','No exact-current field needs a human choice.'));if(data.next_cursor){const next=element('a','Next page');next.className='next';parameters.set('cursor',data.next_cursor);next.href=`/attention?${parameters}`;attentionRoot.append(next);}attentionRoot.focus();}).catch(()=>failPage(attentionRoot));}
if(root){const id=encodeURIComponent(root.dataset.reviewCaseId);const load=async path=>{const response=await fetch(path);if(!response.ok)throw new Error();return response.json();};
Promise.all([load(`/api/v1/review-cases/${id}`),load(`/api/v1/review-cases/${id}/evidence`),load(`/api/v1/review-cases/${id}/history`)]).then(([detail,evidence,history])=>{
root.replaceChildren(element('h1','Current review case'));
root.append(element('p',`Model advice: ${detail.model_advice.state.replaceAll('_',' ')}`));
for(const result of detail.model_advice.results){const advice=document.createElement('section');advice.className='field';advice.append(element('h2','Model advice (not a human decision)'));for(const recommendation of result.recommendations){advice.append(element('h3',recommendation.proposal_id));advice.append(element('p',`Recommendation: ${recommendation.recommendation}`));advice.append(element('p',`Reasons: ${recommendation.reasons.join(', ')}`));advice.append(element('p',`Problems: ${recommendation.problems.length?recommendation.problems.join(', '):'none'}`));advice.append(element('p',`Citations: ${recommendation.citations.length?recommendation.citations.join(', '):'none'}`));}root.append(advice);}
for(const field of detail.fields){const section=document.createElement(field.needs_attention?'section':'details');section.className='field';section.id=`field-${field.destination}`;section.tabIndex=-1;section.append(element(field.needs_attention?'h2':'summary',field.destination));section.append(element('p',`Current (${field.current.state}): ${JSON.stringify(field.current.value)}`));section.append(element('p',`Deterministic disposition: ${field.deterministic_disposition}`));section.append(element('p',`Proposed: ${field.proposed_values.length?field.proposed_values.map(value=>JSON.stringify(value.value)).join(' · '):'none'}`));section.append(element('p',`Provenance: ${field.provenance.length?field.provenance.join(', '):'none'}`));section.append(element('p',`Notices: ${field.notices.length?field.notices.join(', '):'none'}`));section.append(element('p',`Uncertainty: ${field.uncertainty.length?field.uncertainty.join(', '):'none'}`));root.append(section);}
const evidenceSection=document.createElement('section');evidenceSection.className='field';evidenceSection.append(element('h2','Evidence'));for(const item of evidence.items){evidenceSection.append(element('h3',`${item.kind} · ${item.source}`));evidenceSection.append(element('pre',item.fact));}if(!evidence.items.length)evidenceSection.append(element('p','No referenced evidence.'));root.append(evidenceSection);
const historySection=document.createElement('section');historySection.className='field';historySection.append(element('h2','Current-case history'));for(const item of history.items)historySection.append(element('p',`${item.checkpoint}. ${item.kind}: ${item.reason}`));root.append(historySection);const selected=new URLSearchParams(location.search).get('field');const target=selected?document.querySelector(`#field-${CSS.escape(selected)}`):root;target.focus();
}).catch(()=>{const title=element('h1','Case unavailable');const alert=element('p','The exact current case could not be loaded.');alert.setAttribute('role','alert');root.replaceChildren(title,alert);root.focus();});}"""
