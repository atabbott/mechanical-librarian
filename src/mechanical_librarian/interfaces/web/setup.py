"""The deliberately small HTTP surface available before first-run completion."""

from __future__ import annotations

import secrets
import time
from collections.abc import Awaitable, Callable
from threading import RLock
from typing import Literal, Protocol

from argon2 import PasswordHasher
from fastapi import Cookie, FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StrictInt,
    StrictStr,
    TypeAdapter,
)
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse

from mechanical_librarian.application.review_web import (
    OwnerSessionService,
    ReviewWebError,
)
from mechanical_librarian.application.reviewer_access import (
    ReviewerAccessApplicationError,
    ReviewerAccessOperations,
    ReviewerAccessRepositoryError,
)
from mechanical_librarian.application.reviewer_contracts import CodexDeviceAuthState
from mechanical_librarian.application.setup_connection import SetupConnectionError
from mechanical_librarian.application.setup_journey import (
    SetupJourneyError,
    SetupJourneyOperation,
)
from mechanical_librarian.domain.owner_settings import LibrarySettings, ProviderSettings
from mechanical_librarian.domain.reviewer_access import OpenRouterModelId
from mechanical_librarian.domain.setup_state import (
    SetupStateDocument,
    claim_token_matches,
)
from mechanical_librarian.domain.validation import (
    OWNER_SESSION_LIFETIME_DEFAULT,
    owner_password,
)
from mechanical_librarian.interfaces.web.app import (
    BodyLimitMiddleware,
    _request_matches_origin,
    client_identity,
    reviewer_failure_response,
)
from mechanical_librarian.interfaces.web.setup_frontend import (
    SETUP_CSS,
    SETUP_HANDOFF_CSS,
    SETUP_HTML,
    SETUP_JS,
    SETUP_ORIGIN_REFUSAL_HTML,
)

_PROVIDER_ADAPTER: TypeAdapter[ProviderSettings] = TypeAdapter(ProviderSettings)
# Outstanding claim nonces, each valid for 600 seconds. One client holds at
# most a tenth of the pool. A reload replaces the page's cookie without spending
# the nonce it held, so a client at its share retires its own oldest nonce
# instead of being refused, and only the global pool can refuse (#1384).
_CLAIM_NONCES_GLOBAL = 100
_CLAIM_NONCES_PER_CLIENT = 10
_SETUP_SESSION_BODY_LIMIT = 2 * 1_024 * 6 + 128 * 6 + 128
_SETUP_BODY_LIMITS = {
    # Two maximum passwords and the bounded claim token may each expand
    # sixfold under JSON escaping. The remaining fixed envelope covers property
    # names and the bounded revision.
    "/api/setup/session": _SETUP_SESSION_BODY_LIMIT,
    "/api/setup/claim": 2_048,
    "/api/setup/connect": 16_384,
    "/api/setup/providers": 262_144,
    "/api/setup/libraries": 262_144,
    "/api/setup/mounts": 262_144,
    "/api/setup/reviewer": 2_048,
    "/api/setup/reviewer/openrouter": 16_384,
    "/api/setup/reviewer/selection": 2_048,
    "/api/setup/reviewer/codex/device": 2_048,
    "/api/setup/reviewer/codex/device/cancel": 2_048,
    "/api/setup/change": 16_384,
    "/api/setup/kick-off": 16_384,
}


class SetupStateStore(Protocol):
    def read(self, bootstrap_verifier: str) -> SetupStateDocument: ...

    def write(
        self,
        document: SetupStateDocument,
        expected_revision: int,
        bootstrap_verifier: str,
        secret_values: dict[str, str | None] | None = None,
    ) -> SetupStateDocument: ...

    def claim_owner(
        self,
        *,
        expected_revision: int,
        owner_verifier: str,
        claim_token: str,
    ) -> SetupStateDocument: ...


class SetupConnectionWriter(Protocol):
    def connect(
        self, expected_revision: int, name: str, endpoint: str, key: str
    ) -> SetupStateDocument: ...


class _LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    password: StrictStr = Field(min_length=1, max_length=1024)
    confirmation: StrictStr | None = Field(default=None, max_length=1024)
    expected_revision: StrictInt | None = Field(default=None, ge=1)
    claim_token: StrictStr | None = Field(
        default=None,
        max_length=128,
        repr=False,
        json_schema_extra={"writeOnly": True},
    )


class _ConnectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: StrictInt = Field(ge=1)
    name: StrictStr = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    endpoint: StrictStr = Field(min_length=8, max_length=2048)
    key: StrictStr = Field(
        min_length=1, max_length=2048, json_schema_extra={"writeOnly": True}
    )


class _ProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: StrictStr = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    # The four audiobook adapters are keyless (ADR-0061): they reach the
    # settings union carrying nothing but their own fields, and the
    # marketplace order stays with the configuration file rather than setup.
    adapter: Literal[
        "hardcover",
        "google_books",
        "open_library",
        "inventaire",
        "audible_catalog",
        "audnexus",
        "audiosilo",
        "itunes_search",
    ]
    enabled: bool
    cover_enabled: bool = False
    contact_email: StrictStr | None = Field(default=None, min_length=3, max_length=254)
    key: StrictStr | None = Field(
        default=None,
        min_length=1,
        max_length=2048,
        json_schema_extra={"writeOnly": True},
    )


class _ProvidersRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: StrictInt = Field(ge=1)
    providers: list[_ProviderRequest] = Field(max_length=32)


class _LibraryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: StrictStr = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    display_name: StrictStr | None = Field(default=None, min_length=1, max_length=256)
    library_id: StrictStr = Field(min_length=1, max_length=128)
    # Audiobookshelf's `book` collection already represents its combined
    # ebook/audiobook kind. The setup owner selects a library, never a media
    # interpretation that can contradict the service.
    media_kind: Literal["book", "ebook", "audiobook"] = "book"
    provider_connections: list[StrictStr] = Field(default_factory=list, max_length=32)


class _LibrariesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: StrictInt = Field(ge=1)
    libraries: list[_LibraryRequest] = Field(min_length=1, max_length=128)


class _MountsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: StrictInt = Field(ge=1)
    service_paths: dict[StrictStr, StrictStr | None] = Field(max_length=128)


class _ChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: StrictInt = Field(ge=1)
    step: Literal["connect", "providers", "libraries", "mounts", "reviewer"]


class _ReviewerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: StrictInt = Field(ge=1)
    outcome: Literal["skipped", "configured"]


class _ReviewerRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: StrictInt = Field(ge=1)


class _ReviewerSelectionRequest(_ReviewerRevisionRequest):
    task: Literal["cover_observation", "description_rewrite"] = "cover_observation"
    engine: Literal["codex"] | None


class _OpenRouterWriteRequest(_ReviewerRevisionRequest):
    task: Literal["cover_observation", "description_rewrite"] = "cover_observation"
    api_key: SecretStr = Field(json_schema_extra={"writeOnly": True})
    model: OpenRouterModelId


class _KickOffRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: StrictInt = Field(ge=1)


def _prefers_document(request: Request) -> bool:
    """State whether this refused request is a browser navigation.

    A navigation renders the body as the whole page, so the refusal has to
    read as a page; every other caller keeps the JSON shape it parses. The
    Accept header states preference by quality value and not by listing order,
    `q=0` states that a type is not acceptable at all, and a wildcard states
    nothing about HTML in particular, so only an exact `text/html` ranked
    strictly above `application/json` is read as a navigation.
    """
    if request.headers.get("sec-fetch-dest") == "document":
        return True
    quality: dict[str, float] = {}
    for entry in request.headers.get("accept", "").split(","):
        media_type, _, parameters = entry.partition(";")
        name = media_type.strip().lower()
        if name not in {"text/html", "application/json"}:
            continue
        weight = 1.0
        for parameter in parameters.split(";"):
            key, separator, raw = parameter.partition("=")
            if not separator or key.strip().lower() != "q":
                continue
            try:
                weight = float(raw.strip())
            except ValueError:
                weight = 0.0
            break
        quality[name] = max(quality.get(name, 0.0), weight)
    document = quality.get("text/html", 0.0)
    return document > 0.0 and document > quality.get("application/json", 0.0)


def _origin_refusal(request: Request) -> Response:
    """Refuse a non-canonical origin in the shape the caller can read.

    The wizard's own assets are refused on that origin too, so a navigation
    would render the JSON body as its entire page and the wizard's recovery
    sentence would never be shown (#1008).
    """
    if _prefers_document(request):
        return HTMLResponse(SETUP_ORIGIN_REFUSAL_HTML, status_code=400)
    return JSONResponse({"detail": {"error": "invalid_origin"}}, status_code=400)


def _secure_setup_response(response: Response, *, hsts: bool) -> Response:
    response.headers["cache-control"] = "private, no-store"
    response.headers["content-security-policy"] = (
        "default-src 'none'; script-src 'self'; style-src 'self'; "
        "img-src 'self'; connect-src 'self'; form-action 'self'; "
        "base-uri 'none'; frame-ancestors 'none'"
    )
    response.headers["referrer-policy"] = "no-referrer"
    response.headers["x-content-type-options"] = "nosniff"
    response.headers["x-frame-options"] = "DENY"
    if hsts:
        response.headers["strict-transport-security"] = "max-age=31536000"
    return response


def create_setup_app(
    sessions: OwnerSessionService,
    state: SetupStateStore,
    bootstrap_verifier: str,
    connection: SetupConnectionWriter | None = None,
    canonical_origin: str = "https://testserver",
    journey: SetupJourneyOperation | None = None,
    reviewer_access: ReviewerAccessOperations | None = None,
    session_lifetime_seconds: int = OWNER_SESSION_LIFETIME_DEFAULT,
    activate: Callable[[int], None] | None = None,
    clock: Callable[[], int] = lambda: int(time.time()),
    token_factory: Callable[[int], str] = secrets.token_urlsafe,
    *,
    trusted_proxy_ips: tuple[str, ...] = (),
    require_forwarding: bool = False,
    retire_claim_token: Callable[[], None] = lambda: None,
) -> FastAPI:
    """Compose no normal route, worker, target client, or write authority."""

    app = FastAPI(docs_url=None, openapi_url=None, redoc_url=None)
    app.add_middleware(
        BodyLimitMiddleware,
        hsts=canonical_origin.startswith("https://"),
        maximum_bytes=2_048,
        decision_maximum_bytes=16_384,
        path_limits=_SETUP_BODY_LIMITS,
    )
    app.state.setup_complete = False
    app.state.claim_nonces = {}
    if reviewer_access is not None:
        reviewer_gate = RLock()

        def serialized_reviewer(call: Callable[[], object]) -> object:
            with reviewer_gate:
                return call()

        reviewer_access.attach_serialization(serialized_reviewer)

    @app.middleware("http")
    async def close_completed_setup(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.scope["path"]
        response: Response
        try:
            if path != "/health/ready" and not _request_matches_origin(
                request,
                canonical_origin,
                trusted_proxy_ips,
                require_forwarding=require_forwarding,
            ):
                response = _origin_refusal(request)
            elif app.state.setup_complete and path != "/health/ready":
                response = PlainTextResponse("not found\n", status_code=404)
            else:
                response = await call_next(request)
        except Exception:
            response = JSONResponse(
                {"detail": {"error": "unavailable"}}, status_code=500
            )
        return _secure_setup_response(
            response, hsts=canonical_origin.startswith("https://")
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, _error: RequestValidationError) -> Response:
        # FastAPI's default answer echoes the refused input, which here can be
        # a password, a service key or the setup claim token (ADR-0086).
        return JSONResponse({"detail": {"error": "invalid_request"}}, status_code=422)

    @app.get("/health/ready", response_class=PlainTextResponse)
    def ready() -> str:
        return "ready\n"

    @app.get("/", response_class=HTMLResponse)
    def shell() -> str:
        return SETUP_HTML

    @app.get("/api/setup/claim")
    def claim_state(request: Request, response: Response) -> dict[str, object]:
        """Mint a bounded host-only synchronizer nonce for first ownership."""
        try:
            document = state.read(bootstrap_verifier)
        except Exception as error:
            raise HTTPException(503, {"error": "unavailable"}) from error
        now = clock()
        client = client_identity(request, trusted_proxy_ips)
        # In memory only: the identity never leaves this process.
        nonces: dict[str, tuple[int, str]] = app.state.claim_nonces
        for value, (expires_at, _) in tuple(nonces.items()):
            if expires_at <= now:
                nonces.pop(value, None)
        held = [value for value, (_, holder) in nonces.items() if holder == client]
        if len(held) >= _CLAIM_NONCES_PER_CLIENT:
            nonces.pop(min(held, key=lambda value: nonces[value][0]))
        if len(nonces) >= _CLAIM_NONCES_GLOBAL:
            raise HTTPException(503, {"error": "unavailable"})
        nonce = token_factory(32)
        if not 32 <= len(nonce) <= 128:
            raise HTTPException(500, {"error": "unavailable"})
        nonces[nonce] = (now + 600, client)
        response.set_cookie(
            "ml_setup_nonce",
            nonce,
            httponly=False,
            secure=canonical_origin.startswith("https://"),
            samesite="strict",
            path="/api/setup/session",
            max_age=600,
        )
        return {
            "claimed": document.claimed,
            "revision": document.revision,
            "nonce": nonce,
        }

    @app.get("/assets/setup.css")
    def setup_css() -> Response:
        return Response(SETUP_CSS, media_type="text/css; charset=utf-8")

    @app.get("/assets/setup-handoff.css")
    def setup_handoff_css() -> Response:
        return Response(SETUP_HANDOFF_CSS, media_type="text/css; charset=utf-8")

    @app.get("/assets/setup.js")
    def setup_js() -> Response:
        return Response(SETUP_JS, media_type="text/javascript; charset=utf-8")

    @app.post("/api/setup/session")
    def login(
        request: _LoginRequest,
        http_request: Request,
        response: Response,
        origin: str | None = Header(default=None),
        x_setup_nonce: str | None = Header(default=None),
        ml_setup_nonce: str | None = Cookie(default=None),
    ) -> dict[str, object]:
        try:
            current = state.read(bootstrap_verifier)
        except Exception as error:
            raise HTTPException(503, {"error": "unavailable"}) from error
        if origin != canonical_origin:
            raise HTTPException(403, {"error": "csrf"})
        if not current.claimed:
            now = clock()
            nonces: dict[str, tuple[int, str]] = app.state.claim_nonces
            if (
                origin != canonical_origin
                or x_setup_nonce is None
                or ml_setup_nonce is None
                or not secrets.compare_digest(x_setup_nonce, ml_setup_nonce)
                or nonces.pop(x_setup_nonce, (0, ""))[0] <= now
            ):
                raise HTTPException(403, {"error": "csrf"})
            claim_token = request.claim_token
            if claim_token is None or not claim_token_matches(
                claim_token, current.claim_token_digest
            ):
                # Refused before Argon2 and outside the login throttle. Nothing
                # is recorded and the token stays valid for a later claim.
                raise HTTPException(403, {"error": "claim_token"})
            if (
                request.expected_revision is None
                or request.confirmation is None
                or request.password != request.confirmation
                or not owner_password(request.password)
            ):
                raise HTTPException(422, {"error": "invalid_password"})
            owner_verifier = PasswordHasher().hash(request.password)
            try:
                state.claim_owner(
                    expected_revision=request.expected_revision,
                    owner_verifier=owner_verifier,
                    claim_token=claim_token,
                )
            except Exception as error:
                # The token matched above, so a refusal here means another
                # claim settled first: the transaction compares it again.
                raise HTTPException(409, {"error": "claimed"}) from error
            retire_claim_token()
            response.delete_cookie(
                "ml_setup_nonce",
                path="/api/setup/session",
                secure=canonical_origin.startswith("https://"),
                samesite="strict",
            )
            # The first session is issued through the ordinary login path
            # below: the claim itself is atomic in the application database,
            # while sessions live in their own database (#912). If session
            # issuance fails here, the claim stands and resubmitting the same
            # form reaches this already-claimed branch.
        try:
            tokens = sessions.login(
                request.password, client_identity(http_request, trusted_proxy_ips)
            )
        except ReviewWebError as error:
            status = (
                429
                if error.code == "throttled"
                else 401
                if error.code == "invalid_credentials"
                else 503
            )
            raise HTTPException(status, {"error": error.code}) from error
        response.set_cookie(
            "ml_session",
            tokens.session_token,
            httponly=True,
            secure=canonical_origin.startswith("https://"),
            samesite="strict",
            path="/",
            max_age=session_lifetime_seconds,
        )
        return {"csrf_token": tokens.csrf_token, "expires_at": tokens.expires_at}

    @app.get("/api/setup/state")
    def setup_state(ml_session: str | None = Cookie(default=None)) -> dict[str, object]:
        if ml_session is None:
            raise HTTPException(401, {"error": "unauthorized"})
        try:
            document = state.read(bootstrap_verifier)
            csrf_token = sessions.issue_browser_csrf(ml_session)
        except ReviewWebError as error:
            status = 401 if error.code == "unauthorized" else 503
            raise HTTPException(status, {"error": error.code}) from error
        return {
            "csrf_token": csrf_token,
            "completed_steps": document.completed_steps,
            "next_step": document.next_step(),
            "revision": document.revision,
            "connection": None
            if document.connection is None
            else document.connection.model_dump(),
            "providers": [value.model_dump() for value in document.providers],
            "libraries": [value.model_dump() for value in document.libraries],
            "mounts": [value.model_dump() for value in document.mounts],
        }

    if connection is not None:

        @app.post("/api/setup/connect")
        def connect(
            request: _ConnectRequest,
            ml_session: str | None = Cookie(default=None),
            x_csrf_token: str | None = Header(default=None),
            origin: str | None = Header(default=None),
        ) -> dict[str, object]:
            if ml_session is None:
                raise HTTPException(401, {"error": "unauthorized"})
            if origin != canonical_origin or x_csrf_token is None:
                raise HTTPException(403, {"error": "csrf"})
            try:
                sessions.authenticate(ml_session, x_csrf_token)
            except ReviewWebError as error:
                status = 403 if error.code == "csrf" else 401
                raise HTTPException(status, {"error": error.code}) from error
            try:
                document = connection.connect(
                    request.expected_revision,
                    request.name,
                    request.endpoint,
                    request.key,
                )
            except SetupConnectionError as error:
                status = (
                    409
                    if error.code == "stale"
                    else 422
                    if error.code == "invalid_endpoint"
                    else 502
                )
                raise HTTPException(status, {"error": error.code}) from error
            return {
                "completed_steps": document.completed_steps,
                "next_step": document.next_step(),
                "revision": document.revision,
            }

    if journey is not None:

        def authorize_read(ml_session: str | None) -> None:
            if ml_session is None:
                raise HTTPException(401, {"error": "unauthorized"})
            try:
                sessions.authenticate(ml_session)
            except ReviewWebError as error:
                raise HTTPException(401, {"error": error.code}) from error

        def authorize_mutation(
            ml_session: str | None, csrf: str | None, origin: str | None
        ) -> None:
            if ml_session is None:
                raise HTTPException(401, {"error": "unauthorized"})
            if csrf is None or origin != canonical_origin:
                raise HTTPException(403, {"error": "csrf"})
            try:
                sessions.authenticate(ml_session, csrf)
            except ReviewWebError as error:
                status = 403 if error.code == "csrf" else 401
                raise HTTPException(status, {"error": error.code}) from error

        def journey_failure(error: SetupJourneyError) -> HTTPException:
            status = (
                409
                if error.code == "stale"
                else 422
                if error.code.startswith("invalid_") or error.code == "mounts_unready"
                else 502
            )
            return HTTPException(status, {"error": error.code})

        def state_response(document: SetupStateDocument) -> dict[str, object]:
            return {
                "completed_steps": document.completed_steps,
                "next_step": document.next_step(),
                "revision": document.revision,
            }

        @app.post("/api/setup/providers")
        def providers(
            request: _ProvidersRequest,
            ml_session: str | None = Cookie(default=None),
            x_csrf_token: str | None = Header(default=None),
            origin: str | None = Header(default=None),
        ) -> dict[str, object]:
            authorize_mutation(ml_session, x_csrf_token, origin)
            values: list[ProviderSettings] = []
            secrets: dict[str, str] = {}
            try:
                for item in request.providers:
                    payload = item.model_dump(exclude={"key", "contact_email"})
                    if item.adapter == "open_library":
                        payload["contact_email"] = item.contact_email
                    values.append(_PROVIDER_ADAPTER.validate_python(payload))
                    if item.key is not None:
                        secrets[f"provider.{item.name}.key"] = item.key
                return state_response(
                    journey.providers(request.expected_revision, values, secrets)
                )
            except (ValueError, SetupJourneyError) as error:
                failure = (
                    error
                    if isinstance(error, SetupJourneyError)
                    else SetupJourneyError("invalid_providers")
                )
                raise journey_failure(failure) from error

        @app.get("/api/setup/libraries")
        def discover_libraries(
            expected_revision: int,
            ml_session: str | None = Cookie(default=None),
        ) -> dict[str, object]:
            authorize_read(ml_session)
            try:
                values = journey.discover(expected_revision)
            except SetupJourneyError as error:
                raise journey_failure(error) from error
            return {
                "libraries": [
                    {
                        **value.model_dump(),
                        "service_path": (
                            value.service_paths[0]
                            if len(value.service_paths) == 1
                            else None
                        ),
                        "service_path_state": (
                            "available"
                            if len(value.service_paths) == 1
                            else "ambiguous"
                            if value.service_paths
                            else "unavailable"
                        ),
                    }
                    for value in values
                ]
            }

        @app.post("/api/setup/libraries")
        def select_libraries(
            request: _LibrariesRequest,
            ml_session: str | None = Cookie(default=None),
            x_csrf_token: str | None = Header(default=None),
            origin: str | None = Header(default=None),
        ) -> dict[str, object]:
            authorize_mutation(ml_session, x_csrf_token, origin)
            document = state.read(bootstrap_verifier)
            if document.connection is None:
                raise journey_failure(SetupJourneyError("stale"))
            try:
                values = [
                    LibrarySettings(
                        name=value.name,
                        display_name=value.display_name,
                        connection=document.connection.name,
                        library_id=value.library_id,
                        media_kind="book",
                        provider_connections=value.provider_connections,
                    )
                    for value in request.libraries
                ]
                return state_response(
                    journey.libraries(request.expected_revision, values)
                )
            except (ValueError, SetupJourneyError) as error:
                failure = (
                    error
                    if isinstance(error, SetupJourneyError)
                    else SetupJourneyError("invalid_libraries")
                )
                raise journey_failure(failure) from error

        @app.post("/api/setup/mounts")
        def mounts(
            request: _MountsRequest,
            ml_session: str | None = Cookie(default=None),
            x_csrf_token: str | None = Header(default=None),
            origin: str | None = Header(default=None),
        ) -> dict[str, object]:
            authorize_mutation(ml_session, x_csrf_token, origin)
            try:
                return state_response(
                    journey.mounts(request.expected_revision, request.service_paths)
                )
            except SetupJourneyError as error:
                raise journey_failure(error) from error

        @app.get("/api/setup/reviewer-access")
        def reviewer_access_state(
            ml_session: str | None = Cookie(default=None),
        ) -> object:
            authorize_read(ml_session)
            if reviewer_access is None:
                raise HTTPException(503, {"error": "reviewer_access_unavailable"})
            try:
                return reviewer_access.read()
            except (
                ReviewerAccessRepositoryError,
                ReviewerAccessApplicationError,
            ) as error:
                raise reviewer_problem(error) from None

        def reviewer_authority() -> ReviewerAccessOperations:
            if reviewer_access is None:
                raise HTTPException(503, {"error": "reviewer_access_unavailable"})
            return reviewer_access

        def reviewer_problem(error: Exception) -> HTTPException:
            status, code = reviewer_failure_response(error)
            return HTTPException(status, {"error": code})

        @app.post("/api/setup/reviewer/openrouter")
        def configure_openrouter(
            request: _OpenRouterWriteRequest,
            ml_session: str | None = Cookie(default=None),
            x_csrf_token: str | None = Header(default=None),
            origin: str | None = Header(default=None),
        ) -> object:
            authorize_mutation(ml_session, x_csrf_token, origin)
            try:
                return reviewer_authority().configure_openrouter(
                    expected_revision=request.expected_revision,
                    api_key=request.api_key.get_secret_value(),
                    model=request.model,
                    task=request.task,
                )
            except (
                ReviewerAccessApplicationError,
                ReviewerAccessRepositoryError,
                ValueError,
            ) as error:
                raise reviewer_problem(error) from None

        @app.post("/api/setup/reviewer/selection")
        def select_reviewer(
            request: _ReviewerSelectionRequest,
            ml_session: str | None = Cookie(default=None),
            x_csrf_token: str | None = Header(default=None),
            origin: str | None = Header(default=None),
        ) -> object:
            authorize_mutation(ml_session, x_csrf_token, origin)
            operations = reviewer_authority()
            try:
                if request.engine == "codex":
                    return operations.select_codex(
                        expected_revision=request.expected_revision
                    )
                return operations.select_none(
                    expected_revision=request.expected_revision
                )
            except (
                ReviewerAccessApplicationError,
                ReviewerAccessRepositoryError,
            ) as error:
                raise reviewer_problem(error) from None

        @app.post("/api/setup/reviewer/codex/device")
        def start_codex_device(
            request: _ReviewerRevisionRequest,
            ml_session: str | None = Cookie(default=None),
            x_csrf_token: str | None = Header(default=None),
            origin: str | None = Header(default=None),
        ) -> object:
            authorize_mutation(ml_session, x_csrf_token, origin)
            try:
                access, device = reviewer_authority().start_codex(
                    expected_revision=request.expected_revision
                )
                return {"access": access, "device": device}
            except (
                ReviewerAccessApplicationError,
                ReviewerAccessRepositoryError,
            ) as error:
                raise reviewer_problem(error) from None

        @app.get("/api/setup/reviewer/codex/device")
        def codex_device_status(
            ml_session: str | None = Cookie(default=None),
        ) -> CodexDeviceAuthState:
            authorize_read(ml_session)
            try:
                return reviewer_authority().codex_status()
            except (
                ReviewerAccessApplicationError,
                ReviewerAccessRepositoryError,
            ) as error:
                raise reviewer_problem(error) from None

        @app.post("/api/setup/reviewer/codex/recheck")
        def recheck_codex(
            ml_session: str | None = Cookie(default=None),
            x_csrf_token: str | None = Header(default=None),
            origin: str | None = Header(default=None),
        ) -> CodexDeviceAuthState:
            authorize_mutation(ml_session, x_csrf_token, origin)
            try:
                return reviewer_authority().recheck_codex()
            except (
                ReviewerAccessApplicationError,
                ReviewerAccessRepositoryError,
            ) as error:
                raise reviewer_problem(error) from None

        @app.post("/api/setup/reviewer/codex/device/cancel")
        def cancel_codex_device(
            ml_session: str | None = Cookie(default=None),
            x_csrf_token: str | None = Header(default=None),
            origin: str | None = Header(default=None),
        ) -> CodexDeviceAuthState:
            authorize_mutation(ml_session, x_csrf_token, origin)
            try:
                return reviewer_authority().cancel_codex()
            except (
                ReviewerAccessApplicationError,
                ReviewerAccessRepositoryError,
            ) as error:
                raise reviewer_problem(error) from None

        @app.post("/api/setup/reviewer")
        def reviewer(
            request: _ReviewerRequest,
            ml_session: str | None = Cookie(default=None),
            x_csrf_token: str | None = Header(default=None),
            origin: str | None = Header(default=None),
        ) -> dict[str, object]:
            authorize_mutation(ml_session, x_csrf_token, origin)
            if reviewer_access is None:
                raise HTTPException(409, {"error": "reviewer_unconfigured"})
            try:
                current = reviewer_access.read()
            except (
                ReviewerAccessRepositoryError,
                ReviewerAccessApplicationError,
            ) as error:
                raise reviewer_problem(error) from None
            if request.outcome == "configured" and current.mapping is None:
                raise HTTPException(409, {"error": "reviewer_unconfigured"})
            try:
                return state_response(
                    journey.reviewer(
                        request.expected_revision, current.revision, request.outcome
                    )
                )
            except SetupJourneyError as error:
                raise journey_failure(error) from error

        @app.post("/api/setup/change")
        def change(
            request: _ChangeRequest,
            ml_session: str | None = Cookie(default=None),
            x_csrf_token: str | None = Header(default=None),
            origin: str | None = Header(default=None),
        ) -> dict[str, object]:
            authorize_mutation(ml_session, x_csrf_token, origin)
            try:
                return state_response(
                    journey.change(request.expected_revision, request.step)
                )
            except SetupJourneyError as error:
                raise journey_failure(error) from error

        @app.post("/api/setup/kick-off", status_code=202)
        def kick_off(
            request: _KickOffRequest,
            ml_session: str | None = Cookie(default=None),
            x_csrf_token: str | None = Header(default=None),
            origin: str | None = Header(default=None),
        ) -> dict[str, object]:
            authorize_mutation(ml_session, x_csrf_token, origin)
            try:
                if activate is None:
                    journey.kick_off(request.expected_revision)
                else:
                    activate(request.expected_revision)
            except SetupJourneyError as error:
                raise journey_failure(error) from error
            except Exception as error:
                raise HTTPException(503, {"error": "activation_failed"}) from error
            app.state.setup_complete = True
            return {
                "status": "active",
                "message": "Nothing in your library changes. Writes stay off until you turn them on in Activity.",
            }

    return app


__all__ = ["SetupConnectionWriter", "SetupStateStore", "create_setup_app"]
