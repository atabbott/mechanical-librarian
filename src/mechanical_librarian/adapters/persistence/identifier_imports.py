"""Identifiers file imports (#1478): the links, the import and its events.

An import is recorded in one `BEGIN IMMEDIATE` transaction with the catalogue
attached read-only: the library's digest and current links are read again,
the import row inserted, one `file` link recorded per will-link row through
`record_owner_link`, and one `imported` event written. Withdraw this import
appends a `withdraw` row for each of the import's links that is still
current, and one `withdrawn` event. Nothing of the file itself is kept.

Every function here works inside the caller's open transaction except the
reads, which are safe in or out of one.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping

from mechanical_librarian.adapters.persistence.owner_links import (
    IMPORT_ID,
    OwnerLinkChanged,
    OwnerLinkItem,
    current_owner_link_items,
    record_owner_link,
)
from mechanical_librarian.application.identifier_import import (
    IdentifierImportRecord,
    IdentifierImportRefused,
    LibraryCandidates,
)
from mechanical_librarian.domain.identifier_file import (
    COUNT_KEYS,
    IdentifierCandidate,
    WillLink,
)
from mechanical_librarian.domain.owner_link import (
    OwnerLinkAction,
    OwnerLinkTarget,
    owner_search_medium,
)

_COUNT_COLUMNS = ",".join(COUNT_KEYS)
_IMPORT_COLUMNS = (
    "import_id,connection_name,library_name,row_count,"
    + _COUNT_COLUMNS
    + ",actor,created_at,request_id,request_sha256,preview_digest"
)


def library_identifier_candidates(
    connection: sqlite3.Connection,
    fingerprint: str,
    connection_name: str,
    library_name: str,
) -> LibraryCandidates:
    """The library's current eligible entries, read from `retained_catalogue`.

    With them, the library's items that have a current link of any source.
    The digest is SHA-256 over every current entry's item, source
    fingerprint, item path, format and classification (S8), so it moves when
    any of the library's works changes and not when another library's does.
    `library_unreadable` when no current catalogue generation matches.
    """
    try:
        generation = connection.execute(
            "SELECT generation FROM retained_catalogue.catalogue_generations "
            "WHERE current=1 AND fingerprint=?",
            (fingerprint,),
        ).fetchone()
        if generation is None:
            raise IdentifierImportRefused("library_unreadable")
        rows = connection.execute(
            "SELECT item_id,source_fingerprint,item_path,format,classification,"
            "mount,title,authors FROM retained_catalogue.catalogue_entries "
            "WHERE generation=? AND connection=? AND library=? ORDER BY item_id",
            (generation[0], connection_name, library_name),
        ).fetchall()
        candidates = tuple(
            IdentifierCandidate(
                row[0],
                row[2],
                owner_search_medium(row[3]),
                row[6],
                tuple(json.loads(row[7])),
            )
            for row in rows
            if row[4] == "singular-supported" and row[5] is not None
        )
    except (sqlite3.Error, TypeError, ValueError) as error:
        raise IdentifierImportRefused("library_unreadable") from error
    digest = hashlib.sha256(
        json.dumps(
            [list(row[:5]) for row in rows], ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()
    return LibraryCandidates(
        candidates,
        digest,
        current_owner_link_items(connection, connection_name, library_name),
    )


def import_id_for(request_id: str, preview_digest: str) -> str:
    return (
        "identifier_import_"
        + hashlib.sha256((request_id + "\0" + preview_digest).encode()).hexdigest()
    )


def _row_request(kind: str, import_id: str, item_id: str) -> str:
    """A link or withdraw row's own request id, derived so a replay is exact."""
    return (
        f"identifier_import_{kind}_"
        + hashlib.sha256((import_id + "\0" + item_id).encode()).hexdigest()
    )


def _event_id(import_id: str, kind: str) -> str:
    return (
        "identifier_import_event_"
        + hashlib.sha256((import_id + "\0" + kind).encode()).hexdigest()
    )


def identifier_import_by_request(
    connection: sqlite3.Connection, request_id: str
) -> IdentifierImportRecord | None:
    """The import one request recorded, read back for a replay."""
    return _import_where(connection, "request_id=?", request_id)


def identifier_import_by_preview(
    connection: sqlite3.Connection, preview_digest: str
) -> IdentifierImportRecord | None:
    """The import a preview became, if it was imported (S11)."""
    return _import_where(connection, "preview_digest=?", preview_digest)


def _import_where(
    connection: sqlite3.Connection, where: str, value: str
) -> IdentifierImportRecord | None:
    row = connection.execute(
        "SELECT import_id,connection_name,library_name,will_link,request_sha256 "
        f"FROM identifier_imports WHERE {where}",  # noqa: S608 - fixed predicates
        (value,),
    ).fetchone()
    if row is None:
        return None
    if type(row[0]) is not str or IMPORT_ID.fullmatch(row[0]) is None:
        raise IdentifierImportRefused("unavailable")
    return IdentifierImportRecord(row[0], row[1], row[2], row[3], row[4])


def record_identifier_import(
    connection: sqlite3.Connection,
    *,
    connection_name: str,
    library_name: str,
    preview_digest: str,
    catalogue_digest: str,
    current_digest: str,
    rows: int,
    counts: Mapping[str, int],
    links: tuple[WillLink, ...],
    actor: str,
    occurred_at: int,
    request_id: str,
    request_sha256: str,
) -> tuple[IdentifierImportRecord, bool]:
    """Record a preview's links, its import and one `imported` event (S9).

    The caller holds the write transaction with the catalogue attached and
    passes the library's digest it read inside it. A replay of the same
    request answers its import; the second value is True then.
    """
    replay = identifier_import_by_request(connection, request_id)
    if replay is not None:
        if replay.request_sha256 != request_sha256:
            raise IdentifierImportRefused("idempotency_conflict")
        return replay, True
    if identifier_import_by_preview(connection, preview_digest) is not None:
        raise IdentifierImportRefused("preview_imported")
    if not links:
        raise IdentifierImportRefused("nothing_to_link")
    linked = current_owner_link_items(connection, connection_name, library_name)
    if current_digest != catalogue_digest or any(
        link.item_id in linked for link in links
    ):
        raise IdentifierImportRefused("preview_stale")
    import_id = import_id_for(request_id, preview_digest)
    connection.execute(
        f"INSERT INTO identifier_imports({_IMPORT_COLUMNS}) "  # noqa: S608
        f"VALUES({','.join('?' * (len(COUNT_KEYS) + 9))})",
        (
            import_id,
            connection_name,
            library_name,
            rows,
            *(counts[name] for name in COUNT_KEYS),
            actor,
            occurred_at,
            request_id,
            request_sha256,
            preview_digest,
        ),
    )
    for link in links:
        try:
            record_owner_link(
                connection,
                item=OwnerLinkItem(connection_name, library_name, link.item_id),
                action=OwnerLinkAction.LINK,
                expected_current=None,
                target=OwnerLinkTarget(link.scope, None, link.identifier, None),
                source="file",
                medium=link.medium,
                actor=actor,
                occurred_at=occurred_at,
                request_id=_row_request("link", import_id, link.item_id),
                request_sha256=request_sha256,
                import_id=import_id,
            )
        except OwnerLinkChanged as error:
            raise IdentifierImportRefused("preview_stale") from error
    _insert_event(
        connection,
        import_id,
        "imported",
        len(links),
        actor=actor,
        occurred_at=occurred_at,
        request_id=request_id,
        request_sha256=request_sha256,
    )
    return (
        IdentifierImportRecord(
            import_id, connection_name, library_name, len(links), request_sha256
        ),
        False,
    )


def _insert_event(
    connection: sqlite3.Connection,
    import_id: str,
    kind: str,
    affected: int,
    *,
    actor: str,
    occurred_at: int,
    request_id: str,
    request_sha256: str,
) -> None:
    connection.execute(
        "INSERT INTO identifier_import_events(event_id,import_id,kind,occurred_at,"
        "actor,affected,request_id,request_sha256) VALUES(?,?,?,?,?,?,?,?)",
        (
            _event_id(import_id, kind),
            import_id,
            kind,
            occurred_at,
            actor,
            affected,
            request_id,
            request_sha256,
        ),
    )


def withdraw_identifier_import(
    connection: sqlite3.Connection,
    *,
    import_id: str,
    actor: str,
    occurred_at: int,
    request_id: str,
    request_sha256: str,
) -> tuple[IdentifierImportRecord, int, bool]:
    """Withdraw each of the import's links that is still current (S11).

    A link replaced or withdrawn since the import is left as it is. Answers
    the import, how many links were withdrawn and whether this was a replay.
    """
    replay = connection.execute(
        "SELECT import_id,kind,affected,request_sha256 FROM identifier_import_events "
        "WHERE request_id=?",
        (request_id,),
    ).fetchone()
    if replay is not None:
        if replay[1] != "withdrawn" or replay[3] != request_sha256:
            raise IdentifierImportRefused("idempotency_conflict")
        recorded = _import_where(connection, "import_id=?", replay[0])
        if recorded is None:
            raise IdentifierImportRefused("unavailable")
        return recorded, replay[2], True
    recorded = _import_where(connection, "import_id=?", import_id)
    if recorded is None:
        raise IdentifierImportRefused("import_not_found")
    if connection.execute(
        "SELECT 1 FROM identifier_import_events WHERE import_id=? AND kind='withdrawn'",
        (import_id,),
    ).fetchone():
        raise IdentifierImportRefused("import_withdrawn")
    current = connection.execute(
        "SELECT link_row_id,item_id,medium FROM current_owner_links "
        "WHERE import_id=? ORDER BY item_id",
        (import_id,),
    ).fetchall()
    if not current:
        raise IdentifierImportRefused("nothing_to_withdraw")
    for link_row_id, item_id, medium in current:
        record_owner_link(
            connection,
            item=OwnerLinkItem(
                recorded.connection_name, recorded.library_name, item_id
            ),
            action=OwnerLinkAction.WITHDRAW,
            expected_current=link_row_id,
            target=None,
            source="file",
            medium=medium,
            actor=actor,
            occurred_at=occurred_at,
            request_id=_row_request("withdraw", import_id, item_id),
            request_sha256=request_sha256,
            import_id=import_id,
        )
    _insert_event(
        connection,
        import_id,
        "withdrawn",
        len(current),
        actor=actor,
        occurred_at=occurred_at,
        request_id=request_id,
        request_sha256=request_sha256,
    )
    return recorded, len(current), False


def identifier_import_status(
    connection: sqlite3.Connection, import_id: str
) -> dict[str, object] | None:
    """An import's linked count, how many are still current and whether it
    was withdrawn; `None` when no such import exists. Read live, never cached."""
    row = connection.execute(
        "SELECT will_link,(SELECT count(*) FROM current_owner_links link "
        "WHERE link.import_id=held.import_id),"
        "EXISTS(SELECT 1 FROM identifier_import_events event "
        "WHERE event.import_id=held.import_id AND event.kind='withdrawn') "
        "FROM identifier_imports held WHERE held.import_id=?",
        (import_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "import_id": import_id,
        "linked": row[0],
        "still_linked": row[1],
        "withdrawn": bool(row[2]),
    }


__all__ = [
    "identifier_import_by_preview",
    "identifier_import_by_request",
    "identifier_import_status",
    "import_id_for",
    "library_identifier_candidates",
    "record_identifier_import",
    "withdraw_identifier_import",
]
