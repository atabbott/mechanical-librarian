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
from typing import cast

from mechanical_librarian.adapters.persistence.review_preparation import (
    mark_preparation_work_fresh,
)
from mechanical_librarian.adapters.persistence.transactions import TransactionScope
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.metadata_report import canonical_json
from mechanical_librarian.domain.owner_link import (
    LINK_SOURCES_BY_MEDIUM,
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
# The message the insert trigger aborts with when the row does not follow the
# item's current link (schema `owner_links_validate_insert`).
_SEQUENCE_REFUSAL = "owner link does not follow the current link"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_COLUMNS = (
    "link_row_id,connection_name,library_name,item_id,sequence,action,source,"
    "medium,scope,provider,record_identity,display_title,display_creators,actor,"
    "occurred_at,request_id,request_sha256,link_digest,import_id"
)
# An identifiers file import (#1478), which a file row is recorded under.
IMPORT_ID = re.compile(r"identifier_import_[0-9a-f]{64}")


class OwnerLinkStoreError(MetadataReportError):
    """The link store refused a write or holds a row it cannot read."""


class OwnerLinkChanged(OwnerLinkStoreError):
    """The item's current link is not the one the caller saw (`link_changed`)."""


class OwnerLinkIdempotencyConflict(OwnerLinkStoreError):
    """A request id already recorded a different request (`idempotency_conflict`)."""


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
    # The identifiers file import that made this row (#1478): a file link, or
    # a withdrawal made by Withdraw this import. `None` for every other row.
    import_id: str | None = None


def _source_valid(
    source: object, target: OwnerLinkTarget | None, medium: str, import_id: object
) -> bool:
    """A row's source agrees with its import and its target's source (#1478).

    A search row names a source the medium can link and no import; a file
    row names its import and, unless it withdraws, no source.
    """
    if source == "file":
        return (
            type(import_id) is str
            and IMPORT_ID.fullmatch(import_id) is not None
            and (target is None or target.provider is None)
        )
    return (
        source == "search"
        and import_id is None
        and (target is None or target.provider in LINK_SOURCES_BY_MEDIUM[medium])
    )


def _item_valid(item: OwnerLinkItem) -> bool:
    """Whether the item key fits the table's bounds, checked before insert."""
    return type(item) is OwnerLinkItem and all(
        type(value) is str and 1 <= len(value.encode()) <= limit
        for value, limit in (
            (item.connection_name, 128),
            (item.library_name, 128),
            (item.item_id, 512),
        )
    )


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
    import_id: str | None = None,
) -> OwnerLinkRow:
    """Append one link, replace or withdraw row and mark the work fresh.

    `expected_current` is the current link row the caller decided against, or
    `None` when it saw no link; any other current row refuses `link_changed`.
    A `file` row names the identifiers file import it is recorded under
    (#1478). The caller owns the transaction, which must already be open.
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
        or not _item_valid(item)
        or not _source_valid(source, target, medium, import_id)
        or (source == "file" and action is OwnerLinkAction.REPLACE)
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
        "display_creators": None
        if target is None or target.provider is None
        else list(target.display_creators),
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
        **({} if import_id is None else {"import_id": import_id}),
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
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                if values["display_creators"] is None
                else json.dumps(values["display_creators"], ensure_ascii=False),
                actor,
                occurred_at,
                request_id,
                request_sha256,
                digest,
                import_id,
            ),
        )
    except sqlite3.IntegrityError as error:
        # Only the sequence trigger says another link landed first; anything
        # else (a reused request id) is a refused row, never `link_changed`.
        if str(error) == _SEQUENCE_REFUSAL:
            raise OwnerLinkChanged("owner link changed") from error
        raise OwnerLinkStoreError("owner link row is invalid") from error
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
        import_id,
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


def owner_link_by_request(
    connection: sqlite3.Connection, request_id: str
) -> OwnerLinkRow | None:
    """The row one request recorded, read back for an idempotent replay (S16)."""
    try:
        row = connection.execute(
            f"SELECT {_COLUMNS} FROM owner_links WHERE request_id=?",  # noqa: S608
            (request_id,),
        ).fetchone()
    except sqlite3.Error as error:
        raise OwnerLinkStoreError("persisted owner link is unavailable") from error
    return None if row is None else _owner_link_row(row)


def owner_link_rows(
    connection: sqlite3.Connection, item: OwnerLinkItem
) -> tuple[OwnerLinkRow, ...]:
    """Every link row of one item, oldest first, each checked as it is read.

    History reads these; a row outside its grammar fails the read closed.
    """
    try:
        rows = connection.execute(
            f"SELECT {_COLUMNS} FROM owner_links WHERE connection_name=? "  # noqa: S608
            "AND library_name=? AND item_id=? ORDER BY sequence",
            (item.connection_name, item.library_name, item.item_id),
        ).fetchall()
    except sqlite3.Error as error:
        raise OwnerLinkStoreError("persisted owner link is unavailable") from error
    read = tuple(_owner_link_row(row) for row in rows)
    if [row.sequence for row in read] != list(range(1, len(read) + 1)) or any(
        row.item != item for row in read
    ):
        raise OwnerLinkStoreError("persisted owner link is invalid")
    return read


def current_owner_link_row(
    connection: sqlite3.Connection, item: OwnerLinkItem
) -> OwnerLinkRow | None:
    """The item's current link row with its display snapshot, or `None`."""
    try:
        row = connection.execute(
            f"SELECT {_COLUMNS} FROM current_owner_links WHERE "  # noqa: S608
            "connection_name=? AND library_name=? AND item_id=?",
            (item.connection_name, item.library_name, item.item_id),
        ).fetchone()
    except sqlite3.Error as error:
        raise OwnerLinkStoreError("persisted owner link is unavailable") from error
    return None if row is None else _owner_link_row(row)


def _owner_link_row(row: tuple[object, ...]) -> OwnerLinkRow:
    """One persisted row, refused unless every value reads back in grammar."""
    try:
        (
            link_row_id,
            connection_name,
            library_name,
            item_id,
            sequence,
            action,
            source,
            medium,
            scope,
            provider,
            identity,
            title,
            creators,
            actor,
            occurred_at,
            request_id,
            request_sha256,
            digest,
            import_id,
        ) = row
        item = OwnerLinkItem(
            cast(str, connection_name), cast(str, library_name), cast(str, item_id)
        )
        chosen = OwnerLinkAction(cast(str, action))
        target = (
            None
            if chosen is OwnerLinkAction.WITHDRAW
            else OwnerLinkTarget(
                OwnerLinkScope(cast(str, scope)),
                cast("str | None", provider),
                cast(str, identity),
                cast("str | None", title),
                () if creators is None else tuple(json.loads(cast(str, creators))),
            )
        )
        valid = (
            type(link_row_id) is str
            and type(sequence) is int
            and sequence >= 1
            and source in OWNER_LINK_SOURCES
            and medium in OWNER_LINK_MEDIA
            and _source_valid(source, target, medium, import_id)
            and (target is None or (creators is None) == (target.provider is None))
            and type(actor) is str
            and type(occurred_at) is int
            and occurred_at >= 0
            and type(request_id) is str
            and _REQUEST_ID.fullmatch(request_id) is not None
            and type(request_sha256) is str
            and _SHA256.fullmatch(request_sha256) is not None
            and _item_valid(item)
            and (
                (
                    target is None
                    and (scope, provider, identity, title, creators, digest)
                    == (None,) * 6
                )
                or (
                    target is not None
                    and digest
                    == link_digest(
                        medium, target.scope, target.identity, target.provider
                    )
                )
            )
        )
    except (OwnerLinkError, KeyError, TypeError, ValueError) as error:
        raise OwnerLinkStoreError("persisted owner link is invalid") from error
    if not valid:
        raise OwnerLinkStoreError("persisted owner link is invalid")
    return OwnerLinkRow(
        cast(str, link_row_id),
        item,
        cast(int, sequence),
        chosen,
        cast(str, source),
        cast(str, medium),
        target,
        cast(str, actor),
        cast(int, occurred_at),
        cast(str, request_id),
        cast(str, request_sha256),
        cast("str | None", digest),
        cast("str | None", import_id),
    )


def current_owner_link_items(
    connection: sqlite3.Connection, connection_name: str, library_name: str
) -> frozenset[str]:
    """The item IDs of one library that have a current link of any source.

    Safe inside the caller's transaction (#1478).
    """
    try:
        rows = connection.execute(
            "SELECT item_id FROM current_owner_links "
            "WHERE connection_name=? AND library_name=?",
            (connection_name, library_name),
        ).fetchall()
    except sqlite3.Error as error:
        raise OwnerLinkStoreError("persisted owner link is unavailable") from error
    if any(type(row[0]) is not str for row in rows):
        raise OwnerLinkStoreError("persisted owner link is invalid")
    return frozenset(row[0] for row in rows)


def choose_owner_link(
    connection: sqlite3.Connection,
    *,
    item: OwnerLinkItem,
    expected_current: str | None,
    target: OwnerLinkTarget,
    medium: str,
    actor: str,
    occurred_at: int,
    request_id: str,
    request_sha256: str,
    transaction: TransactionScope | None = None,
) -> tuple[OwnerLinkRow, bool]:
    """Record a chosen record as the item's link in its own transaction (S16).

    A link when the owner saw none, otherwise a replace of the row they saw.
    The second value is True when this request was already recorded and the
    stored row is answered again.
    """
    return _write_owner_link(
        connection,
        transaction,
        item=item,
        action=OwnerLinkAction.LINK
        if expected_current is None
        else OwnerLinkAction.REPLACE,
        expected_current=expected_current,
        target=target,
        medium=medium,
        actor=actor,
        occurred_at=occurred_at,
        request_id=request_id,
        request_sha256=request_sha256,
    )


def withdraw_owner_link(
    connection: sqlite3.Connection,
    *,
    item: OwnerLinkItem,
    expected_current: str,
    medium: str,
    actor: str,
    occurred_at: int,
    request_id: str,
    request_sha256: str,
    transaction: TransactionScope | None = None,
) -> tuple[OwnerLinkRow, bool]:
    """Withdraw the item's current link in its own transaction (S16)."""
    return _write_owner_link(
        connection,
        transaction,
        item=item,
        action=OwnerLinkAction.WITHDRAW,
        expected_current=expected_current,
        target=None,
        medium=medium,
        actor=actor,
        occurred_at=occurred_at,
        request_id=request_id,
        request_sha256=request_sha256,
    )


def _write_owner_link(
    connection: sqlite3.Connection,
    transaction: TransactionScope | None,
    *,
    item: OwnerLinkItem,
    action: OwnerLinkAction,
    expected_current: str | None,
    target: OwnerLinkTarget | None,
    medium: str,
    actor: str,
    occurred_at: int,
    request_id: str,
    request_sha256: str,
) -> tuple[OwnerLinkRow, bool]:
    """BEGIN IMMEDIATE, answer a replay from its row, else record and commit.

    The same request id with the same digest answers the stored row; with
    another digest it is `idempotency_conflict`. Nothing is written by either.
    """
    scope = transaction or TransactionScope(connection, "owner_link_write")
    scope.begin()
    try:
        replay = owner_link_by_request(connection, request_id)
        if replay is not None:
            if replay.request_sha256 != request_sha256:
                raise OwnerLinkIdempotencyConflict("owner link request conflicts")
            scope.commit()
            return replay, True
        row = record_owner_link(
            connection,
            item=item,
            action=action,
            expected_current=expected_current,
            target=target,
            source="search",
            medium=medium,
            actor=actor,
            occurred_at=occurred_at,
            request_id=request_id,
            request_sha256=request_sha256,
        )
        scope.commit()
    except BaseException:
        scope.rollback()
        raise
    return row, False


def owner_link_bound(connection: sqlite3.Connection, review_case_id: str) -> bool:
    """Whether a case's report binds the item's current link (S27, Q6, L9).

    True when the report's link digest is the current link's, both absent
    included; a report and a link that disagree in any way, one present and
    the other absent included, read False. The caller holds the write
    transaction, so no link write lands between this read and its commit.
    """
    try:
        row = connection.execute(
            "SELECT json_type(report.document,'$.owner_link'),"
            "json_extract(report.document,'$.owner_link.digest'),"
            "(SELECT link.link_digest FROM current_owner_links link "
            "WHERE link.connection_name=report.connection_name "
            "AND link.library_name=report.library_name "
            "AND link.item_id=report.item_id) "
            "FROM review_cases review JOIN metadata_reports report "
            "ON report.report_id=review.report_id WHERE review.review_case_id=?",
            (review_case_id,),
        ).fetchone()
    except sqlite3.Error as error:
        raise OwnerLinkStoreError("persisted owner link is unavailable") from error
    if row is None:
        # No such case: its own stale check answers, never this one.
        return True
    kind, bound, current = row
    if kind in {None, "null"}:
        return current is None
    if kind != "object" or type(bound) is not str:
        raise OwnerLinkStoreError("persisted owner link is invalid")
    return bool(bound == current)
