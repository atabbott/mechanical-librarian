"""The provider-recovery outbox aggregate.

Owns the `provider_recovery_outbox` table: the durable record that one exact
package cutover must still be handed to provider recovery, and the idempotent
acknowledgement that it was.

The row shape's identity and validation stay in `schema.py`, because open-time
total validation checks this table like every other and must not depend on an
aggregate module.
"""

from __future__ import annotations

import re
import sqlite3

from mechanical_librarian.adapters.persistence.schema import (
    _provider_recovery_outbox_id,
    _validate_provider_recovery_outbox_row,
)
from mechanical_librarian.adapters.persistence.transactions import ConnectionScoped
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.provider_recovery import MAX_TIMESTAMP
from mechanical_librarian.domain.validation import bounded_integer

_OUTBOX_CURSOR_ID = re.compile(r"recovery_outbox_[0-9a-f]{64}")
_OUTBOX_TIMESTAMP = bounded_integer(0, MAX_TIMESTAMP)


class SQLiteRecoveryOutboxRepository(ConnectionScoped):
    """Persist and settle exact package cutovers awaiting provider recovery."""

    def enqueue(
        self,
        connection: str,
        library: str,
        item: str,
        # The selected current_review_case_lineages columns, in order:
        # proposal_set_id, report_id, review_case_id, checkpoint, outcome.
        pointer: tuple[str | None, str, str | None, int | None, str],
        created_at: int,
    ) -> None:
        """Record one exact cutover inside the caller's open transaction.

        Deliberately no transaction scope: the review pipeline calls this from
        inside the transaction that also writes `current_review_packages`, and
        the cutover must be atomic with it.
        """
        outbox_id = _provider_recovery_outbox_id(
            connection,
            library,
            item,
            *pointer,
        )
        self._connection.execute(
            "INSERT INTO provider_recovery_outbox(outbox_id,connection_name,"
            "library_name,item_id,proposal_set_id,report_id,review_case_id,"
            "checkpoint,outcome,created_at,delivered_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,NULL) ON CONFLICT(outbox_id) DO NOTHING",
            (outbox_id, connection, library, item, *pointer, created_at),
        )

    def pending_provider_recovery_outbox(
        self,
        *,
        limit: int = 100,
        after: tuple[int, str] | None = None,
    ) -> tuple[tuple[object, ...], ...]:
        """Return bounded exact package cutovers awaiting recovery enqueue."""
        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or after is not None
            and (
                type(after) is not tuple
                or len(after) != 2
                or not _OUTBOX_TIMESTAMP(after[0])
                or type(after[1]) is not str
                or _OUTBOX_CURSOR_ID.fullmatch(after[1]) is None
            )
        ):
            raise MetadataReportError("provider recovery outbox query is invalid")
        try:
            cursor = (-1, "") if after is None else after
            rows = self._connection.execute(
                "SELECT outbox_id,connection_name,library_name,item_id,"
                "proposal_set_id,report_id,review_case_id,checkpoint,outcome,created_at "
                "FROM provider_recovery_outbox WHERE delivered_at IS NULL "
                "AND (created_at>? OR (created_at=? AND outbox_id>?)) "
                "ORDER BY created_at,outbox_id LIMIT ?",
                (cursor[0], cursor[0], cursor[1], limit),
            ).fetchall()
            for row in rows:
                _validate_provider_recovery_outbox_row(row)
            return tuple(rows)
        except MetadataReportError:
            raise
        except sqlite3.Error as error:
            raise MetadataReportError(
                "provider recovery outbox is unavailable"
            ) from error

    def complete_provider_recovery_outbox(
        self,
        outbox_id: str,
        *,
        delivered_at: int,
    ) -> None:
        """Mark one exact cutover delivered only after idempotent enqueue."""
        if (
            type(outbox_id) is not str
            or type(delivered_at) is not int
            or delivered_at < 0
        ):
            raise MetadataReportError("provider recovery outbox completion is invalid")
        transaction = self._transaction("complete_provider_recovery_outbox")
        try:
            transaction.begin()
            row = self._connection.execute(
                "SELECT outbox_id,connection_name,library_name,item_id,"
                "proposal_set_id,report_id,review_case_id,checkpoint,outcome,created_at,"
                "delivered_at FROM provider_recovery_outbox WHERE outbox_id=?",
                (outbox_id,),
            ).fetchone()
            if row is None:
                raise MetadataReportError("provider recovery outbox changed")
            _validate_provider_recovery_outbox_row(row[:10])
            if row[10] is None:
                if (
                    delivered_at < row[9]
                    or self._connection.execute(
                        "UPDATE provider_recovery_outbox SET delivered_at=? "
                        "WHERE outbox_id=? AND delivered_at IS NULL",
                        (delivered_at, outbox_id),
                    ).rowcount
                    != 1
                ):
                    raise MetadataReportError("provider recovery outbox changed")
            elif type(row[10]) is not int or row[10] < row[9]:
                raise MetadataReportError("provider recovery outbox changed")
            transaction.commit()
        except MetadataReportError:
            transaction.rollback()
            raise
        except sqlite3.Error as error:
            transaction.rollback()
            raise MetadataReportError(
                "provider recovery outbox is unavailable"
            ) from error


__all__ = ["SQLiteRecoveryOutboxRepository"]
