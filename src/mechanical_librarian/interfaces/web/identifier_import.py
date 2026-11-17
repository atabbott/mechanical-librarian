"""Link many works from an identifiers file: preview, import, withdraw (#1478).

`app.py` registers these and bounds the preview's body at 1 MiB; everything
else is here. Each route answers in the contract's order: the session, CSRF
token, Origin and browser build first (the CSRF token is checked in a
dependency, before the body is read), then the body's media type, then the
application's own refusals. A refusal is one closed code and nothing else:
no row text, path or item ID is ever echoed.

The preview reads its body raw, as `text/plain`, so the handler is `async`
and hands the bytes to a worker thread to be decided; the other handlers are
plain functions, which FastAPI runs on a worker thread.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from typing import Annotated, Any, Literal, Self, TypeAlias

from fastapi import Depends, FastAPI, Header, Path, Request, Security
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyCookie
from pydantic import BaseModel, ConfigDict, Field, model_validator

from mechanical_librarian.application.identifier_import import (
    IdentifierImportRefused,
)
from mechanical_librarian.application.review_application import ReviewApplication
from mechanical_librarian.domain.identifier_file import (
    MAX_DISPLAY_CHARACTERS,
    MAX_FILE_BYTES,
    MAX_ROWS,
)

_IdempotencyKey: TypeAlias = Annotated[
    str, Header(min_length=16, max_length=128, pattern=r"^[\x21-\x7e]+$")
]
_IMPORT_ID = r"^identifier_import_[0-9a-f]{64}$"
_Instant: TypeAlias = Annotated[int, Field(ge=0, le=8_640_000_000_000)]
_Count: TypeAlias = Annotated[int, Field(ge=0, le=MAX_ROWS)]
# A row is numbered by its line in the file; the header is line 1.
_Row: TypeAlias = Annotated[int, Field(ge=2, le=MAX_ROWS + 1)]
_Display: TypeAlias = Annotated[str, Field(max_length=MAX_DISPLAY_CHARACTERS)]
_RowReason: TypeAlias = Literal[
    "row_too_long",
    "row_shape",
    "no_item",
    "ambiguous",
    "invalid_isbn",
    "invalid_asin",
    "asin_for_ebook",
    "isbn_for_audiobook",
    "conflicting",
    "repeated",
    "already_linked",
]

# Every refusal's status. A refusal not listed here is a fault (503).
_STATUS = {
    "library_not_found": 404,
    "preview_not_found": 404,
    "import_not_found": 404,
    "preview_stale": 409,
    "preview_imported": 409,
    "idempotency_conflict": 409,
    "import_withdrawn": 409,
    "nothing_to_withdraw": 409,
    "nothing_to_link": 409,
    "unsupported_media_type": 415,
    "file_encoding": 422,
    "file_control_character": 422,
    "file_header": 422,
    "file_too_many_rows": 422,
    "file_no_rows": 422,
    "library_unreadable": 503,
}
_PLAIN_TEXT = re.compile(r"text/plain\s*(?:;\s*charset=\"?utf-8\"?\s*)?", re.I)


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _IdentifierRefusalCode(_Closed):
    code: str = Field(max_length=64)


class _IdentifierRefusal(_Closed):
    """A refused preview, import or withdrawal: one closed code."""

    error: _IdentifierRefusalCode
    schema_version: Literal["review-error-v1"]


class _IdentifierCounts(_Closed):
    """How the file's rows were decided; they add up to its rows."""

    will_link: _Count
    already_linked: _Count
    no_item: _Count
    ambiguous: _Count
    invalid_identifier: _Count
    wrong_medium: _Count
    repeated: _Count
    conflicting: _Count
    unreadable: _Count


class _WillLinkRow(_Closed):
    """A row the import links: the work as Library names it, and its record."""

    row: _Row
    title: _Display
    authors: list[_Display] = Field(max_length=MAX_DISPLAY_CHARACTERS)
    identifier_kind: Literal["isbn", "asin"]
    # The canonical ISBN-13 or ASIN.
    identifier: str = Field(pattern=r"^(?:97[89][0-9]{10}|[0-9A-Z]{10})$")


class _RefusedRow(_Closed):
    row: _Row
    reason: _RowReason


class _IdentifierPreview(_Closed):
    """One held preview (`identifier-preview-v1`)."""

    preview_id: str = Field(min_length=1, max_length=64)
    expires_at: _Instant
    rows: int = Field(ge=1, le=MAX_ROWS)
    counts: _IdentifierCounts
    will_link: list[_WillLinkRow] = Field(max_length=MAX_ROWS)
    refused: list[_RefusedRow] = Field(max_length=MAX_ROWS)
    schema_version: Literal["identifier-preview-v1"]

    @model_validator(mode="after")
    def _counts_add_up(self) -> Self:
        counts = self.counts.model_dump()
        if (
            sum(counts.values()) != self.rows
            or counts["will_link"] != len(self.will_link)
            or len(self.will_link) + len(self.refused) != self.rows
        ):
            raise ValueError("identifier preview counts are inconsistent")
        return self


class _IdentifierPreviewStatus(_Closed):
    """A preview after an uncertain confirm (`identifier-preview-status-v1`).

    `imported` names the import; `open` means the preview may still be
    confirmed; `closed` means it was never imported and is gone.
    """

    state: Literal["imported", "open", "closed"]
    import_id: str | None = Field(pattern=_IMPORT_ID)
    linked: int | None = Field(ge=1, le=MAX_ROWS)

    schema_version: Literal["identifier-preview-status-v1"]

    @model_validator(mode="after")
    def _only_an_import_names_one(self) -> Self:
        if (self.state == "imported") != (self.import_id is not None) or (
            self.import_id is None
        ) != (self.linked is None):
            raise ValueError("identifier preview status is inconsistent")
        return self


class _IdentifierImported(_Closed):
    """What an import recorded (`identifier-import-v1`)."""

    import_id: str = Field(pattern=_IMPORT_ID)
    linked: int = Field(ge=1, le=MAX_ROWS)
    # False when the scan could not be asked for; the works' fresh marks make
    # the next scan prepare them anyway.
    scan_requested: bool
    schema_version: Literal["identifier-import-v1"]


class _IdentifierImportStatus(_Closed):
    """An import's live state, read when its Activity details open."""

    import_id: str = Field(pattern=_IMPORT_ID)
    linked: int = Field(ge=1, le=MAX_ROWS)
    still_linked: _Count
    withdrawn: bool
    schema_version: Literal["identifier-import-v1"]


class _IdentifierImportWithdrawal(_Closed):
    """What Withdraw this import withdrew (`identifier-import-withdrawal-v1`)."""

    import_id: str = Field(pattern=_IMPORT_ID)
    withdrawn: int = Field(ge=1, le=MAX_ROWS)
    scan_requested: bool
    schema_version: Literal["identifier-import-withdrawal-v1"]


_REFUSALS: dict[int | str, dict[str, Any]] = {
    status: {"model": _IdentifierRefusal} for status in (401, 403, 404, 409, 422, 503)
}
_PREVIEW_REFUSALS: dict[int | str, dict[str, Any]] = {
    **_REFUSALS,
    **{status: {"model": _IdentifierRefusal} for status in (413, 415)},
}
_PREVIEW_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "text/plain": {
                "schema": {"type": "string", "maxLength": MAX_FILE_BYTES},
            }
        },
    }
}


def _refused(code: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code}, "schema_version": "review-error-v1"},
        status_code=_STATUS.get(code, 503),
    )


def _request_sha256(kind: str, values: Mapping[str, object]) -> str:
    """The digest an idempotent replay must repeat: the request's own values."""
    return hashlib.sha256(
        json.dumps(
            {"kind": kind, **values}, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _answer(
    call: Callable[[], dict[str, object]], model: type[BaseModel]
) -> BaseModel | JSONResponse:
    """The application's answer, or its one refusal; a fault is `unavailable`.

    After a write's fault the write may or may not have landed: the browser
    reads the preview's or the import's status first, never sends again.
    """
    try:
        return model.model_validate(call())
    except IdentifierImportRefused as error:
        return _refused(error.code)
    except Exception:
        return _refused("unavailable")


def register_routes(
    app: FastAPI,
    application: ReviewApplication,
    session_cookie: APIKeyCookie,
    authenticated_session_cookie: Callable[..., str],
    require_session: Callable[..., str],
) -> None:
    """Add the identifiers file routes; `app.py` owns their body limits."""
    sessions = application

    def owner(
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str | None = Header(default=None),
    ) -> str:
        """The session's actor, its CSRF token checked before the body is read."""
        return require_session(sessions, ml_session, x_csrf_token or "")

    @app.post(
        "/api/v1/libraries/{connection}/{library}/identifier-previews",
        response_model=_IdentifierPreview,
        responses=_PREVIEW_REFUSALS,
        openapi_extra=_PREVIEW_BODY,
    )
    async def preview_identifiers(
        request: Request,
        connection: Annotated[str, Path(max_length=64)],
        library: Annotated[str, Path(max_length=64)],
        ml_session: str = Depends(authenticated_session_cookie),
        _: str = Depends(owner),
    ) -> BaseModel | JSONResponse:
        """Decide an identifiers file against the library. Nothing is recorded."""
        if _PLAIN_TEXT.fullmatch(request.headers.get("content-type", "")) is None:
            return _refused("unsupported_media_type")
        data = await request.body()
        return await run_in_threadpool(
            _answer,
            lambda: application.preview_identifiers(
                ml_session, connection, library, data
            ),
            _IdentifierPreview,
        )

    @app.get(
        "/api/v1/identifier-previews/{preview_id}",
        response_model=_IdentifierPreviewStatus,
        responses=_REFUSALS,
    )
    def identifier_preview_status(
        preview_id: Annotated[str, Path(max_length=64)],
        ml_session: str | None = Security(session_cookie),
    ) -> BaseModel | JSONResponse:
        """Whether the preview was imported, is still open, or closed (S11)."""
        require_session(sessions, ml_session)
        assert ml_session is not None
        return _answer(
            lambda: application.identifier_preview_status(ml_session, preview_id),
            _IdentifierPreviewStatus,
        )

    @app.post(
        "/api/v1/identifier-previews/{preview_id}/import",
        response_model=_IdentifierImported,
        responses=_REFUSALS,
    )
    def import_identifiers(
        preview_id: Annotated[str, Path(max_length=64)],
        idempotency_key: _IdempotencyKey,
        ml_session: str = Depends(authenticated_session_cookie),
        actor: str = Depends(owner),
    ) -> BaseModel | JSONResponse:
        """Link every will-link row of the session's preview, at once (S9)."""
        return _answer(
            lambda: application.import_identifiers(
                ml_session,
                actor,
                preview_id,
                idempotency_key,
                _request_sha256("identifier_import", {"preview_id": preview_id}),
            ),
            _IdentifierImported,
        )

    @app.get(
        "/api/v1/identifier-imports/{import_id}",
        response_model=_IdentifierImportStatus,
        responses=_REFUSALS,
    )
    def identifier_import_status(
        import_id: Annotated[str, Path(max_length=82)],
        ml_session: str | None = Security(session_cookie),
    ) -> BaseModel | JSONResponse:
        """How many of the import's links are still current; never cached."""
        require_session(sessions, ml_session)
        if re.fullmatch(_IMPORT_ID, import_id) is None:
            return _refused("import_not_found")
        return _answer(
            lambda: application.identifier_import_status(import_id),
            _IdentifierImportStatus,
        )

    @app.post(
        "/api/v1/identifier-imports/{import_id}/withdraw",
        response_model=_IdentifierImportWithdrawal,
        responses=_REFUSALS,
    )
    def withdraw_identifier_import(
        import_id: Annotated[str, Path(max_length=82)],
        idempotency_key: _IdempotencyKey,
        ml_session: str = Depends(authenticated_session_cookie),
        actor: str = Depends(owner),
    ) -> BaseModel | JSONResponse:
        """Withdraw each of the import's links that is still current (S11)."""
        if re.fullmatch(_IMPORT_ID, import_id) is None:
            return _refused("import_not_found")
        return _answer(
            lambda: application.withdraw_identifier_import(
                actor,
                import_id,
                idempotency_key,
                _request_sha256("identifier_import_withdraw", {"import_id": import_id}),
            ),
            _IdentifierImportWithdrawal,
        )


__all__ = ["register_routes"]
