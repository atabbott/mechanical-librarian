"""The owner-link store (#1477, ADR-0104).

`owner_links` is append-only and each row is its own audit record: who
changed the link, when, under which request, and to what. An item's current
link is its highest-sequence row unless that row withdrew it; SQL triggers
hold the sequence and the one-current-link rule, so no caller can break them.

`record_owner_link` writes inside the caller's open transaction and never
begins or commits one, so a bulk caller may record many links in one
transaction. It marks the work for preparation in the same transaction; the
caller asks for the library's scan after it commits.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass

from mechanical_librarian.adapters.persistence.review_preparation import (
    mark_preparation_work_fresh,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.metadata_report import canonical_json
from mechanical_librarian.domain.owner_link import (
    OWNER_LINK_MEDIA,
    OWNER_LINK_SOURCES,
    OwnerLink,
    OwnerLinkAction,
    OwnerLinkError,
    OwnerLinkScope,
    OwnerLinkTarget,
    link_digest,
)

_REQUEST_ID = re.compile(r"[\x21-\x7e]{16,128}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_COLUMNS = (
    "link_row_id,connection_name,library_name,item_id,sequence,action,source,"
    "medium,scope,provider,record_identity,display_title,display_creators,actor,"
    "occurred_at,request_id,request_sha256,link_digest"
)


class OwnerLinkStoreError(MetadataReportError):
    """The link store refused a write or holds a row it cannot read."""


class OwnerLinkChanged(OwnerLinkStoreError):
    """The item's current link is not the one the caller saw (`link_changed`)."""


@dataclass(frozen=True, slots=True)
class OwnerLinkItem:
    """The catalogue item a link is keyed on: its lineage, not a file."""

    connection_name: str
    library_name: str
    item_id: str


@dataclass(frozen=True, slots=True)
class OwnerLinkRow:
    link_row_id: str
    item: OwnerLinkItem
    sequence: int
    action: OwnerLinkAction
    source: str
    medium: str
    target: OwnerLinkTarget | None
    actor: str
    occurred_at: int
    request_id: str
    request_sha256: str
    link_digest: str | None


def record_owner_link(
    connection: sqlite3.Connection,
    *,
    item: OwnerLinkItem,
    action: OwnerLinkAction,
    expected_current: str | None,
    target: OwnerLinkTarget | None,
    source: str,
    medium: str,
    actor: str,
    occurred_at: int,
    request_id: str,
    request_sha256: str,
) -> OwnerLinkRow:
    """Append one link, replace or withdraw row and mark the work fresh.

    `expected_current` is the current link row the caller decided against, or
    `None` when it saw no link; any other current row refuses `link_changed`.
    The caller owns the transaction, which must already be open.
    """
    if not connection.in_transaction:
        raise OwnerLinkStoreError("owner link needs an open transaction")
    if (
        type(action) is not OwnerLinkAction
        or source not in OWNER_LINK_SOURCES
        or medium not in OWNER_LINK_MEDIA
        or (target is None) != (action is OwnerLinkAction.WITHDRAW)
        or (target is not None and type(target) is not OwnerLinkTarget)
        or type(actor) is not str
        or not 1 <= len(actor.encode()) <= 128
        or type(occurred_at) is not int
        or occurred_at < 0
        or type(request_id) is not str
        or _REQUEST_ID.fullmatch(request_id) is None
        or type(request_sha256) is not str
        or _SHA256.fullmatch(request_sha256) is None
    ):
        raise OwnerLinkStoreError("owner link row is invalid")
    if (expected_current is None) != (action is OwnerLinkAction.LINK):
        raise OwnerLinkChanged("owner link changed")
    current = _latest(connection, item)
    current_id = None if current is None or current[1] == "withdraw" else current[0]
    if current_id != expected_current:
        raise OwnerLinkChanged("owner link changed")
    sequence = 1 if current is None else current[2] + 1
    digest = (
        None
        if target is None
        else link_digest(medium, target.scope, target.identity, target.provider)
    )
    values = {
        "action": action.value,
        "actor": actor,
        "connection_name": item.connection_name,
        "display_creators": None if target is None else list(target.display_creators),
        "display_title": None if target is None else target.display_title,
        "item_id": item.item_id,
        "library_name": item.library_name,
        "link_digest": digest,
        "medium": medium,
        "occurred_at": occurred_at,
        "provider": None if target is None else target.provider,
        "record_identity": None if target is None else target.identity,
        "request_id": request_id,
        "request_sha256": request_sha256,
        "scope": None if target is None else target.scope.value,
        "sequence": sequence,
        "source": source,
    }
    row_id = (
        "owner_link_"
        + hashlib.sha256(
            ("owner-link-row-v1\0" + canonical_json(values)).encode()
        ).hexdigest()
    )
    try:
        connection.execute(
            f"INSERT INTO owner_links({_COLUMNS}) "  # noqa: S608 - fixed column list
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                row_id,
                item.connection_name,
                item.library_name,
                item.item_id,
                sequence,
                action.value,
                source,
                medium,
                values["scope"],
                values["provider"],
                values["record_identity"],
                values["display_title"],
                None
                if target is None
                else json.dumps(list(target.display_creators), ensure_ascii=False),
                actor,
                occurred_at,
                request_id,
                request_sha256,
                digest,
            ),
        )
    except sqlite3.IntegrityError as error:
        raise OwnerLinkChanged("owner link changed") from error
    mark_preparation_work_fresh(
        connection, item.connection_name, item.library_name, item.item_id
    )
    return OwnerLinkRow(
        row_id,
        item,
        sequence,
        action,
        source,
        medium,
        target,
        actor,
        occurred_at,
        request_id,
        request_sha256,
        digest,
    )


def _latest(
    connection: sqlite3.Connection, item: OwnerLinkItem
) -> tuple[str, str, int] | None:
    row = connection.execute(
        "SELECT link_row_id,action,sequence FROM owner_links WHERE connection_name=? "
        "AND library_name=? AND item_id=? ORDER BY sequence DESC LIMIT 1",
        (item.connection_name, item.library_name, item.item_id),
    ).fetchone()
    if row is None:
        return None
    if type(row[0]) is not str or type(row[1]) is not str or type(row[2]) is not int:
        raise OwnerLinkStoreError("persisted owner link is invalid")
    return row[0], row[1], row[2]


def current_owner_link(
    connection: sqlite3.Connection,
    connection_name: str,
    library_name: str,
    item_id: str,
) -> OwnerLink | None:
    """The item's current link as preparation reads it, or `None`."""
    try:
        row = connection.execute(
            "SELECT link_row_id,medium,scope,provider,record_identity,link_digest "
            "FROM current_owner_links WHERE connection_name=? AND library_name=? "
            "AND item_id=?",
            (connection_name, library_name, item_id),
        ).fetchone()
    except sqlite3.Error as error:
        raise OwnerLinkStoreError("persisted owner link is unavailable") from error
    if row is None:
        return None
    try:
        link = OwnerLink(row[0], row[1], OwnerLinkScope(row[2]), row[3], row[4])
    except (OwnerLinkError, ValueError) as error:
        raise OwnerLinkStoreError("persisted owner link is invalid") from error
    if link.digest != row[5]:
        raise OwnerLinkStoreError("persisted owner link is invalid")
    return link
