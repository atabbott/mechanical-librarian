"""Link many works from an identifiers file: preview, import, withdraw (#1478).

A preview decides every row of the owner's file against the library's current
eligible works and holds the outcome in session memory, for 15 minutes and
one per session, bound to that library and its catalogue digest. Nothing of
the file is held: the preview keeps row numbers, reasons, each will-link
work's item, identifier and display values, and the counts.

Importing records the preview's links, the import and one audit event in one
transaction (`BrowserStateOwner.import_identifiers`); a lost answer is read
back by the preview's status, never sent again. Withdraw this import touches
only the import's links that are still current. The `ReviewApplication`
delegators own the operation gate, the scan request and the live updates.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

from mechanical_librarian.application.failure_diagnostics import (
    emit_owner_events,
    identifier_import_event,
)
from mechanical_librarian.application.session_memory import SessionMemory
from mechanical_librarian.domain.identifier_file import (
    IdentifierCandidate,
    IdentifierFileError,
    IdentifierFileRows,
    WillLink,
    classify_identifier_file,
)

PREVIEW_TTL_SECONDS = 15 * 60
# A preview may answer about 5.5 MB, so few are held at once (A2.1 A14).
MAXIMUM_PREVIEW_SESSIONS = 4
PREVIEW_SCHEMA = "identifier-preview-v1"
PREVIEW_STATUS_SCHEMA = "identifier-preview-status-v1"
IMPORT_SCHEMA = "identifier-import-v1"
WITHDRAWAL_SCHEMA = "identifier-import-withdrawal-v1"


class IdentifierImportRefused(Exception):
    """One closed refusal code; the web layer maps each to its status."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class LibraryCandidates:
    """A library's eligible entries, their digest and its linked items."""

    candidates: tuple[IdentifierCandidate, ...]
    digest: str
    linked: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class IdentifierImportRecord:
    """One recorded import, as its acknowledgement and status read it."""

    import_id: str
    connection_name: str
    library_name: str
    linked: int
    request_sha256: str


class IdentifierImportStore(Protocol):
    """The reads and the two writes, each its own transaction."""

    def identifier_candidates(
        self, connection: str, library: str
    ) -> LibraryCandidates: ...

    def identifier_import_replay(
        self, request_id: str
    ) -> IdentifierImportRecord | None: ...

    def identifier_import_for_preview(
        self, preview_digest: str
    ) -> IdentifierImportRecord | None: ...

    def import_identifiers(
        self,
        connection: str,
        library: str,
        *,
        preview_digest: str,
        catalogue_digest: str,
        rows: int,
        counts: Mapping[str, int],
        links: tuple[WillLink, ...],
        actor: str,
        occurred_at: int,
        request_id: str,
        request_sha256: str,
    ) -> tuple[IdentifierImportRecord, bool]: ...

    def withdraw_identifier_import(
        self,
        import_id: str,
        *,
        actor: str,
        occurred_at: int,
        request_id: str,
        request_sha256: str,
    ) -> tuple[IdentifierImportRecord, int, bool]: ...

    def identifier_import_status(self, import_id: str) -> dict[str, object] | None: ...


def preview_digest(preview_id: str) -> str:
    """What the store keeps of a preview: never its id, only this digest."""
    return hashlib.sha256(
        ("identifier-preview-v1\0" + preview_id).encode("utf-8", "surrogatepass")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class IdentifierPreview:
    """One held preview: the file's outcome for one library, and no row text."""

    preview_id: str = field(repr=False)
    connection: str
    library: str
    expires_at: int
    catalogue_digest: str
    result: IdentifierFileRows

    @property
    def digest(self) -> str:
        return preview_digest(self.preview_id)

    def to_data(self) -> dict[str, object]:
        """The preview's response: counts, will-link rows and refused rows.

        No path, item ID or row text: a will-link row names its work by the
        catalogue's title and authors, as Library does.
        """
        return {
            "preview_id": self.preview_id,
            "expires_at": self.expires_at,
            "rows": self.result.rows,
            "counts": dict(self.result.counts),
            "will_link": [
                {
                    "row": row.row,
                    "title": row.title,
                    "authors": list(row.authors),
                    "identifier_kind": row.identifier_kind,
                    "identifier": row.identifier,
                }
                for row in self.result.will_link
            ],
            "refused": [
                {"row": row.row, "reason": row.reason} for row in self.result.refused
            ],
            "schema_version": PREVIEW_SCHEMA,
        }


class IdentifierImportService:
    """The session-bound previews and the import refusals (S7, S11).

    `library_configured` answers whether a library is in the saved settings;
    `store` is this generation's state owner. A Settings cutover composes a
    new service, so open previews are dropped and a confirm answers
    `preview_not_found`.
    """

    def __init__(
        self,
        *,
        store: IdentifierImportStore,
        library_configured: Callable[[str, str], bool],
        clock: Callable[[], float],
        memory: SessionMemory[IdentifierPreview] | None = None,
    ) -> None:
        self._store = store
        self._library_configured = library_configured
        self._clock = clock
        self._memory = memory or SessionMemory(
            PREVIEW_TTL_SECONDS, clock, maximum_sessions=MAXIMUM_PREVIEW_SESSIONS
        )

    @property
    def store(self) -> IdentifierImportStore:
        return self._store

    def forget(self, session_token: str) -> None:
        """Discard the session's preview, as logout does."""
        self._memory.discard(session_token)

    def require_library(self, connection: str, library: str) -> None:
        if not self._library_configured(connection, library):
            raise IdentifierImportRefused("library_not_found")

    def preview(
        self,
        session_token: str,
        connection: str,
        library: str,
        data: bytes,
        read: LibraryCandidates,
    ) -> dict[str, object]:
        """Decide the file against `read` and hold it, replacing the last one."""
        try:
            result = classify_identifier_file(data, read.candidates, read.linked)
        except IdentifierFileError as error:
            emit_identifier_import("preview", error.code)
            raise IdentifierImportRefused(error.code) from error
        preview = IdentifierPreview(
            secrets.token_urlsafe(32),
            connection,
            library,
            int(self._clock()) + PREVIEW_TTL_SECONDS,
            read.digest,
            result,
        )
        self._memory.put(session_token, preview)
        emit_identifier_import(
            "preview",
            "previewed",
            rows=result.rows,
            linked=result.counts["will_link"],
        )
        return preview.to_data()

    def held(self, session_token: str, preview_id: str) -> IdentifierPreview | None:
        """The session's preview when it is this one and still open."""
        held = self._memory.get(session_token)
        if held is None or not secrets.compare_digest(
            held.preview_id.encode(), str(preview_id).encode()
        ):
            return None
        return held

    def replayed(
        self, request_id: str, request_sha256: str
    ) -> IdentifierImportRecord | None:
        """The import this request already recorded, or `idempotency_conflict`."""
        replay = self._store.identifier_import_replay(request_id)
        if replay is not None and replay.request_sha256 != request_sha256:
            raise IdentifierImportRefused("idempotency_conflict")
        return replay

    def open_preview(self, session_token: str, preview_id: str) -> IdentifierPreview:
        """The preview a confirm imports: `preview_imported` when another
        request already imported it, `preview_not_found` when it is gone."""
        if self._store.identifier_import_for_preview(preview_digest(preview_id)):
            raise IdentifierImportRefused("preview_imported")
        held = self.held(session_token, preview_id)
        if held is None:
            raise IdentifierImportRefused("preview_not_found")
        return held

    def preview_status(self, session_token: str, preview_id: str) -> dict[str, object]:
        """After an uncertain confirm: imported, still open, or closed (S11).

        The store is read first, so an import is found after a restart or
        after its preview closed.
        """
        recorded = self._store.identifier_import_for_preview(preview_digest(preview_id))
        if recorded is not None:
            state, import_id, linked = "imported", recorded.import_id, recorded.linked
        elif self.held(session_token, preview_id) is not None:
            state, import_id, linked = "open", None, None
        else:
            state, import_id, linked = "closed", None, None
        return {
            "state": state,
            "import_id": import_id,
            "linked": linked,
            "schema_version": PREVIEW_STATUS_SCHEMA,
        }

    def import_status(self, import_id: str) -> dict[str, object]:
        status = self._store.identifier_import_status(import_id)
        if status is None:
            raise IdentifierImportRefused("import_not_found")
        return {**status, "schema_version": IMPORT_SCHEMA}


def emit_identifier_import(
    action: str, outcome: str, *, rows: int = 0, linked: int = 0
) -> None:
    """One value-free operational event for a preview, import or withdrawal."""
    emit_owner_events(
        identifier_import_event,
        action=action,
        outcome=outcome,
        rows=rows,
        linked=linked,
    )


__all__ = [
    "IMPORT_SCHEMA",
    "IdentifierImportRecord",
    "IdentifierImportRefused",
    "IdentifierImportService",
    "IdentifierImportStore",
    "IdentifierPreview",
    "LibraryCandidates",
    "WITHDRAWAL_SCHEMA",
    "emit_identifier_import",
    "preview_digest",
]
