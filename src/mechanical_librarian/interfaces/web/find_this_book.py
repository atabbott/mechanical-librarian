"""Find this book: the owner's search, preview, choose and withdraw routes (#1477).

`app.py` registers these and bounds their bodies; everything else is here.
Each route answers in the contract's order: the session, CSRF token, Origin
and browser build first (the CSRF token is checked in a dependency, before the
body is read), then the body's shape, then the application's own refusals in
the order S11 and S16 give them. A refusal of the search or the link write is
one closed code, with the retry time, deferral reason or failure kind where
the code has one, and nothing else.

The handlers are plain functions, so FastAPI runs each on a worker thread: a
search may wait ten seconds on the shared pacer and twenty on its source, and
never inside the event loop.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from typing import Annotated, Any, Literal, Self, TypeAlias

from fastapi import Depends, FastAPI, Header, Query, Request, Response, Security
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyCookie
from pydantic import BaseModel, ConfigDict, Field, model_validator

from mechanical_librarian.application.owner_search import (
    OwnerSearchRefused,
    OwnerSearchRequest,
)
from mechanical_librarian.application.review_application import (
    ReviewApplication,
    ReviewApplicationError,
)
from mechanical_librarian.application.review_web import (
    OwnerLinkViewSchemaVersion,
    ReviewWebError,
)

_Provider: TypeAlias = Literal[
    "audible_catalog", "google_books", "hardcover", "inventaire", "open_library"
]
_Scope: TypeAlias = Literal["work", "edition", "recording"]
_Lane: TypeAlias = Literal["title_authors", "isbn", "asin"]
_Text: TypeAlias = Annotated[str, Field(max_length=4_096)]
_Address: TypeAlias = Annotated[str, Field(min_length=1, max_length=512)]
_IdempotencyKey: TypeAlias = Annotated[
    str, Header(min_length=16, max_length=128, pattern=r"^[\x21-\x7e]+$")
]
_LINK_ROW_ID = r"^owner_link_[0-9a-f]{64}$"
_Instant: TypeAlias = Annotated[int, Field(ge=0, le=8_640_000_000_000)]

# Every refusal's status. A refusal not listed here is a fault (503).
_STATUS = {
    "work_not_found": 404,
    "search_not_found": 404,
    "preview_unavailable": 404,
    "find_not_offered": 409,
    "work_changed": 409,
    "link_changed": 409,
    "idempotency_conflict": 409,
    "search_in_flight": 409,
    "provider_not_offered": 422,
    "title_invalid": 422,
    "author_invalid": 422,
    "isbn_invalid": 422,
    "asin_invalid": 422,
    "card_not_choosable": 422,
    "scope_not_offered": 422,
    "search_limit": 429,
    "provider_deferred": 429,
    "provider_busy": 503,
    "search_failed": 502,
}


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _RefusalCode(_Closed):
    code: str = Field(max_length=64)
    retry_at: _Instant | None = None
    reason: Literal["rate_limited", "quota_exhausted", "key"] | None = None
    kind: Literal["unavailable", "unreadable", "key"] | None = None


class _Refusal(_Closed):
    """A refused search, preview or link write: one closed code."""

    error: _RefusalCode
    schema_version: Literal["review-error-v1"]


class _OwnerLinkSource(_Closed):
    provider: _Provider
    lanes: list[_Lane] = Field(max_length=3)
    marketplaces: list[Annotated[str, Field(max_length=16)]] | None = Field(
        default=None, max_length=16
    )


class _OwnerLinkPrefill(_Closed):
    title: str | None = Field(max_length=4_096)
    authors: list[_Text] = Field(max_length=4)
    isbn: str | None = Field(max_length=13)
    asin: str | None = Field(max_length=10)


class _OwnerLinkCurrent(_Closed):
    link_row_id: str = Field(pattern=_LINK_ROW_ID)
    scope: _Scope
    # A file link (#1478) names no source and keeps no title.
    source: Literal["search", "file"]
    provider: _Provider | None
    title: str | None = Field(min_length=1, max_length=512)
    creators: list[Annotated[str, Field(max_length=512)]] = Field(max_length=10)
    # The edition's ISBN or the recording's ASIN; None for a whole book.
    identifier: str | None = Field(max_length=13)
    state: Literal[
        "linked",
        "preparing",
        "preparation_failed",
        "waiting",
        "contradicted",
        "needs_replacing",
    ]
    # A contradicted link's re-check, or why a link needs replacing.
    reason: (
        Literal[
            "format",
            "runtime",
            "narrators",
            "variant",
            "missing",
            "merged",
            "source_disabled",
            "medium_changed",
        ]
        | None
    )
    retry_at: _Instant | None
    paused: bool

    @model_validator(mode="after")
    def _a_file_link_names_no_source(self) -> Self:
        if (self.source == "file") != (self.provider is None) or (
            self.provider is None
        ) != (self.title is None):
            raise ValueError("owner link source is inconsistent")
        return self


class _OwnerLinkView(_Closed):
    """The selected work's offer and link (`owner-link-view-v2`)."""

    offered: bool
    sources: list[_OwnerLinkSource] = Field(max_length=5)
    prefill: _OwnerLinkPrefill
    link: _OwnerLinkCurrent | None
    schema_version: OwnerLinkViewSchemaVersion


class _OwnerSearchBody(_Closed):
    connection: _Address
    library: _Address
    item: _Address
    provider: str = Field(max_length=64)
    lane: _Lane
    title: _Text | None = None
    authors: list[_Text] = Field(default_factory=list, max_length=16)
    isbn: str | None = Field(default=None, max_length=64)
    asin: str | None = Field(default=None, max_length=64)
    marketplace: str | None = Field(default=None, max_length=32)


class _CardScopeWithheld(_Closed):
    scope: _Scope
    reason: Literal[
        "no_isbn",
        "format_print",
        "format_audio",
        "no_runtime",
        "runtime",
        "narrators",
        "variant",
    ]


class _CardContradiction(_Closed):
    code: Literal[
        "identifier", "compilation", "language", "creators_disjoint", "runtime"
    ]
    identifier: Literal["isbn", "asin"] | None = None
    record: str | int | None = None
    work: str | int | None = None
    language: str | None = Field(default=None, max_length=64)


class _OwnerSearchCard(_Closed):
    position: int = Field(ge=0, le=9)
    title: str = Field(max_length=512)
    subtitle: str | None = Field(max_length=512)
    creators: list[Annotated[str, Field(max_length=512)]] = Field(max_length=10)
    narrators: list[Annotated[str, Field(max_length=512)]] = Field(max_length=10)
    year: int | None
    publisher: str | None = Field(max_length=512)
    language: str | None = Field(max_length=64)
    isbn: str | None = Field(max_length=13)
    asin: str | None = Field(max_length=10)
    runtime_seconds: int | None = Field(ge=0)
    choosable: bool
    scopes: list[_Scope] = Field(max_length=3)
    withheld: list[_CardScopeWithheld] = Field(max_length=3)
    contradictions: list[_CardContradiction] = Field(max_length=5)
    preview: bool


class _OwnerSearchResponse(_Closed):
    """One held search's cards (`owner-search-v1`)."""

    search_id: str = Field(max_length=64)
    provider: _Provider
    lane: _Lane
    marketplace: str | None = Field(max_length=16)
    cards: list[_OwnerSearchCard] = Field(max_length=10)
    more: bool
    dropped: int = Field(ge=0, le=11)
    schema_version: Literal["owner-search-v1"]


class _OwnerLinkChoiceBody(_Closed):
    position: int = Field(ge=0, le=99)
    scope: _Scope


class _OwnerLinkWithdrawBody(_Closed):
    connection: _Address
    library: _Address
    item: _Address
    link_row_id: str = Field(pattern=_LINK_ROW_ID)


class _OwnerLinkChange(_Closed):
    """What a choose or withdraw recorded (`owner-link-change-v1`)."""

    link_row_id: str = Field(pattern=_LINK_ROW_ID)
    action: Literal["link", "replace", "withdraw"]
    # False when the scan could not be asked for; the work's fresh mark makes
    # the next scan prepare it anyway (S20).
    scan_requested: bool
    schema_version: Literal["owner-link-change-v1"]


_REFUSALS: dict[int | str, dict[str, Any]] = {
    status: {"model": _Refusal} for status in (400, 401, 403, 404, 409, 413, 422, 503)
}


def _refused(error: OwnerSearchRefused) -> JSONResponse:
    return JSONResponse(
        {
            "error": {
                "code": error.code,
                **({"retry_at": error.retry_at} if error.retry_at is not None else {}),
                **({"reason": error.reason} if error.reason is not None else {}),
                **({"kind": error.kind} if error.kind is not None else {}),
            },
            "schema_version": "review-error-v1",
        },
        status_code=_STATUS.get(error.code, 503),
    )


def _unavailable() -> JSONResponse:
    return JSONResponse(
        {"error": {"code": "unavailable"}, "schema_version": "review-error-v1"},
        status_code=503,
    )


def _request_sha256(kind: str, values: Mapping[str, object]) -> str:
    """The digest an idempotent replay must repeat: the request's own values."""
    return hashlib.sha256(
        json.dumps(
            {"kind": kind, **values}, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def register_routes(
    app: FastAPI,
    application: ReviewApplication,
    session_cookie: APIKeyCookie,
    authenticated_session_cookie: Callable[..., str],
    require_session: Callable[..., str],
) -> None:
    """Add the Find this book routes; `app.py` owns their body limits."""
    sessions = application

    def owner(
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str | None = Header(default=None),
    ) -> str:
        """The session's actor, its CSRF token checked before the body is read."""
        return require_session(sessions, ml_session, x_csrf_token or "")

    @app.get(
        "/api/v1/library/link",
        response_model=_OwnerLinkView,
        responses=_REFUSALS,
    )
    def owner_link_view(
        connection: str = Query(max_length=512),
        library: str = Query(max_length=512),
        item: str = Query(max_length=512),
        ml_session: str | None = Security(session_cookie),
    ) -> _OwnerLinkView | JSONResponse:
        """Read-only: whether Find this book is offered, and the current link."""
        require_session(sessions, ml_session)
        try:
            view = application.owner_link_view(connection, library, item)
        except OwnerSearchRefused as error:
            return _refused(error)
        except (ReviewApplicationError, ReviewWebError):
            return _unavailable()
        return _OwnerLinkView.model_validate(view)

    @app.post(
        "/api/v1/library/find/searches",
        response_model=_OwnerSearchResponse,
        responses={**_REFUSALS, 429: {"model": _Refusal}, 502: {"model": _Refusal}},
    )
    def search_owner_link(
        payload: _OwnerSearchBody,
        ml_session: str = Depends(authenticated_session_cookie),
        _: str = Depends(owner),
    ) -> _OwnerSearchResponse | JSONResponse:
        """Search one source for the work's book (S11). Nothing is recorded."""
        try:
            result = application.search_owner_link(
                ml_session,
                OwnerSearchRequest(
                    connection=payload.connection,
                    library=payload.library,
                    item=payload.item,
                    provider=payload.provider,
                    lane=payload.lane,
                    title=payload.title,
                    authors=tuple(payload.authors),
                    isbn=payload.isbn,
                    asin=payload.asin,
                    marketplace=payload.marketplace,
                ),
            )
        except OwnerSearchRefused as error:
            return _refused(error)
        except (ReviewApplicationError, ReviewWebError):
            return _unavailable()
        return _OwnerSearchResponse.model_validate(result)

    @app.get(
        "/api/v1/library/find/searches/{search_id}/previews/{position}",
        response_class=Response,
        responses={
            200: {
                "content": {
                    "image/png": {"schema": {"type": "string", "format": "binary"}}
                }
            },
            **_REFUSALS,
        },
    )
    def owner_link_preview(
        search_id: str,
        position: int,
        request: Request,
        ml_session: str | None = Security(session_cookie),
    ) -> Response:
        """A card's cover preview, held in memory only (S14)."""
        require_session(sessions, ml_session)
        assert ml_session is not None
        fetch_site = request.headers.get("sec-fetch-site")
        if fetch_site is not None and fetch_site != "same-origin":
            return _refused(OwnerSearchRefused("preview_unavailable"))
        try:
            payload = application.owner_link_preview(
                ml_session, search_id[:64], position
            )
        except OwnerSearchRefused as error:
            return _refused(error)
        except (ReviewApplicationError, ReviewWebError):
            return _unavailable()
        return Response(
            content=payload,
            media_type="image/png",
            headers={
                "Cache-Control": "private, no-store",
                "Content-Security-Policy": "default-src 'none'",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post(
        "/api/v1/library/find/searches/{search_id}/choice",
        response_model=_OwnerLinkChange,
        responses=_REFUSALS,
    )
    def choose_owner_link(
        search_id: str,
        payload: _OwnerLinkChoiceBody,
        idempotency_key: _IdempotencyKey,
        ml_session: str = Depends(authenticated_session_cookie),
        actor: str = Depends(owner),
    ) -> _OwnerLinkChange | JSONResponse:
        """Link the work to the chosen card's record (S16)."""
        values = {
            "position": payload.position,
            "scope": payload.scope,
            "search_id": search_id,
        }
        try:
            answer = application.choose_owner_link(
                ml_session,
                actor,
                search_id,
                payload.position,
                payload.scope,
                idempotency_key,
                _request_sha256("choose", values),
            )
        except OwnerSearchRefused as error:
            return _refused(error)
        except Exception:
            # The write may or may not have landed; the owner checks first.
            return _unavailable()
        return _OwnerLinkChange.model_validate(answer)

    @app.post(
        "/api/v1/library/link/withdraw",
        response_model=_OwnerLinkChange,
        responses=_REFUSALS,
    )
    def withdraw_owner_link(
        payload: _OwnerLinkWithdrawBody,
        idempotency_key: _IdempotencyKey,
        ml_session: str = Depends(authenticated_session_cookie),
        actor: str = Depends(owner),
    ) -> _OwnerLinkChange | JSONResponse:
        """Withdraw the work's current link, named by its row (S16)."""
        try:
            answer = application.withdraw_owner_link(
                actor,
                payload.connection,
                payload.library,
                payload.item,
                payload.link_row_id,
                idempotency_key,
                _request_sha256("withdraw", payload.model_dump()),
            )
        except OwnerSearchRefused as error:
            return _refused(error)
        except Exception:
            return _unavailable()
        return _OwnerLinkChange.model_validate(answer)


__all__ = ["register_routes"]
