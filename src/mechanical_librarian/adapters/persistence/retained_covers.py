"""Bindings and removal obligations for retained originals (#1363, ADR-0093).

A binding is an append-only row written before an automatic cover write that
replaces a populated cover: it binds the original's digest to that plan and
its decision. The copy stays while the plan is verified. A plan that ends any
other way gets a removal obligation in the same transaction as its terminal
state, and the obligation is retried until the bytes are gone.
"""

from __future__ import annotations

import re
import sqlite3

from mechanical_librarian.adapters.persistence.transactions import (
    ConnectionScoped,
    local_operation,
)
from mechanical_librarian.domain.field_effect import (
    FieldEffectError,
    FieldEffectOperation,
)

_DIGEST = re.compile(r"[0-9a-f]{64}")
# The bounded review list of automatic switches: the most recent this many
# verified switches keep their originals. Leaving the list releases the
# original, so continuous switching settles well below the store's hard bound
# of 2,000 entries (#1363, ADR-0093).
MAXIMUM_LISTED_SWITCHES = 1000
# Another binding of the same digest that has no removal still needs the file,
# whether its write is pending or verified.
_SHARED_SQL = (
    "EXISTS (SELECT 1 FROM retained_cover_bindings other "
    "WHERE other.original_digest=binding.original_digest "
    "AND other.plan_id<>binding.plan_id AND NOT EXISTS ("
    "SELECT 1 FROM retained_cover_removals ended WHERE ended.plan_id=other.plan_id))"
)


def schedule_retained_cover_removal(
    connection: sqlite3.Connection, plan_id: str, changed_at: int
) -> None:
    """Oblige removal of a bound original whose plan ended unverified.

    Runs inside the caller's terminal transition, so a plan never ends without
    its obligation. A plan with no binding retained nothing and gets none.
    """
    connection.execute(
        "INSERT INTO retained_cover_removals(plan_id,reason,state,attempt,updated_at) "
        "SELECT plan_id,'unverified','pending',0,? FROM retained_cover_bindings "
        "WHERE plan_id=? ON CONFLICT(plan_id) DO NOTHING",
        (changed_at, plan_id),
    )


def release_unlisted_retained_covers(
    connection: sqlite3.Connection, plan_id: str, changed_at: int
) -> None:
    """List the newly verified switch and release the ones it pushed off.

    Runs inside the transaction that verifies ``plan_id``. The switch takes the
    next listing sequence, so the list keeps the order of verification even
    when switches verify in the same second; verified switches past the list
    bound are released through the same retryable obligation as an unverified
    end. A plan with no binding retained nothing and is not listed.
    """
    connection.execute(
        "INSERT INTO retained_cover_listings(plan_id,sequence) "
        "SELECT plan_id,(SELECT coalesce(max(sequence),0)+1 FROM "
        "retained_cover_listings) FROM retained_cover_bindings WHERE plan_id=? "
        "AND NOT EXISTS (SELECT 1 FROM retained_cover_listings WHERE plan_id=?)",
        (plan_id, plan_id),
    )
    connection.execute(
        "INSERT INTO retained_cover_removals(plan_id,reason,state,attempt,updated_at) "
        "SELECT listing.plan_id,'released','pending',0,? "
        "FROM retained_cover_listings listing WHERE NOT EXISTS ("
        "SELECT 1 FROM retained_cover_removals removal "
        "WHERE removal.plan_id=listing.plan_id) "
        "ORDER BY listing.sequence DESC LIMIT -1 OFFSET ?",
        (changed_at, MAXIMUM_LISTED_SWITCHES),
    )


def release_kept_retained_covers(
    connection: sqlite3.Connection,
    connection_name: str,
    library: str,
    work_id: str,
    changed_at: int,
) -> None:
    """Release a work's listed originals once the owner keeps its cover.

    Runs inside the transaction that records the owner's Keep, so keeping a
    decision frees its copy before the switch leaves the list (Amendment 5).
    Only listed switches are released: a listing exists only for a verified
    switch, and a pending plan's copy is never released here.
    """
    connection.execute(
        "INSERT INTO retained_cover_removals(plan_id,reason,state,attempt,updated_at) "
        "SELECT listing.plan_id,'released','pending',0,? "
        "FROM retained_cover_listings listing "
        "JOIN retained_cover_bindings binding ON binding.plan_id=listing.plan_id "
        "WHERE binding.connection_name=? AND binding.library_name=? "
        "AND binding.work_id=? AND NOT EXISTS (SELECT 1 FROM retained_cover_removals "
        "removal WHERE removal.plan_id=listing.plan_id)",
        (changed_at, connection_name, library, work_id),
    )


class SQLiteRetainedCoverRepository(ConnectionScoped):
    """Record bindings and run the removal obligation for retained originals."""

    @local_operation
    def record_retained_cover(
        self, plan_id: str, original_digest: str, byte_count: int, retained_at: int
    ) -> None:
        if (
            type(original_digest) is not str
            or _DIGEST.fullmatch(original_digest) is None
            or type(byte_count) is not int
            or not 1 <= byte_count <= 8 * 1024 * 1024
            or type(retained_at) is not int
            or retained_at < 0
        ):
            raise FieldEffectError("retained_cover")
        transaction = self._transaction("record_retained_cover")
        try:
            transaction.begin()
            row = self._connection.execute(
                "SELECT document FROM field_effects WHERE plan_id=?", (plan_id,)
            ).fetchone()
            if row is None:
                raise FieldEffectError("not_found")
            operation = FieldEffectOperation.from_json(row[0])
            plan = operation.plan
            if (
                plan.destination != "cover"
                or plan.authority.origin != "automatic_policy"
                or operation.state not in {"planned", "retryable"}
                or plan.pre_value != "cover_" + original_digest
            ):
                raise FieldEffectError("retained_cover")
            existing = self._connection.execute(
                "SELECT original_digest,byte_count FROM retained_cover_bindings "
                "WHERE plan_id=?",
                (plan_id,),
            ).fetchone()
            if existing is not None:
                # A retried or revived plan is the same plan with the same
                # pre-value, so its binding is already the one it needs.
                if tuple(existing) != (original_digest, byte_count):
                    raise FieldEffectError("retained_cover")
            else:
                self._connection.execute(
                    "INSERT INTO retained_cover_bindings(plan_id,decision_id,"
                    "connection_name,library_name,work_id,original_digest,"
                    "byte_count,retained_at) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        plan_id,
                        plan.authority.decision_id,
                        plan.connection,
                        plan.library,
                        plan.work_id,
                        original_digest,
                        byte_count,
                        retained_at,
                    ),
                )
            transaction.commit()
        except (sqlite3.Error, FieldEffectError) as error:
            transaction.rollback()
            if isinstance(error, FieldEffectError):
                raise
            raise FieldEffectError("persistence") from error

    @local_operation
    def pending_retained_cover_removals(
        self, limit: int
    ) -> tuple[tuple[str, str, bool], ...]:
        """Each pending removal's plan, digest and whether another binding shares it."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise FieldEffectError("invalid_limit")
        return tuple(
            (str(plan_id), str(digest), bool(shared))
            for plan_id, digest, shared in self._connection.execute(
                "SELECT removal.plan_id,binding.original_digest," + _SHARED_SQL + " "
                "FROM retained_cover_removals removal "
                "JOIN retained_cover_bindings binding USING(plan_id) "
                "WHERE removal.state='pending' AND removal.attempt<3 "
                "ORDER BY removal.updated_at,removal.plan_id LIMIT ?",
                (limit,),
            ).fetchall()
        )

    @local_operation
    def complete_retained_cover_removal(self, plan_id: str, changed_at: int) -> None:
        self._finish_removal(plan_id, changed_at, "completed")

    @local_operation
    def fail_retained_cover_removal(self, plan_id: str, changed_at: int) -> None:
        self._finish_removal(plan_id, changed_at, "pending")

    @local_operation
    def retained_cover_original(
        self, connection: str, library: str, work_id: str
    ) -> tuple[str, str, str] | None:
        """The newest original kept for a work: its digest, plan and decision.

        Only a verified plan's copy with no removal counts, so a write that is
        pending, or ended without landing, offers nothing to put back. The
        newest is the highest listing sequence, as the release rule orders it,
        so switches verified in the same second or after a clock step keep
        their order.
        """
        row = self._connection.execute(
            "SELECT binding.original_digest,binding.plan_id,binding.decision_id "
            "FROM retained_cover_bindings binding JOIN field_effects effect "
            "USING(plan_id) JOIN retained_cover_listings listing USING(plan_id) "
            "WHERE binding.connection_name=? "
            "AND binding.library_name=? AND binding.work_id=? "
            "AND effect.state='verified' AND NOT EXISTS (SELECT 1 FROM "
            "retained_cover_removals removal WHERE removal.plan_id=binding.plan_id) "
            "ORDER BY listing.sequence DESC LIMIT 1",
            (connection, library, work_id),
        ).fetchone()
        return None if row is None else (str(row[0]), str(row[1]), str(row[2]))

    def _finish_removal(self, plan_id: str, changed_at: int, state: str) -> None:
        if type(changed_at) is not int or changed_at < 0:
            raise FieldEffectError("retained_cover")
        transaction = self._transaction("finish_retained_cover_removal")
        try:
            transaction.begin()
            cursor = self._connection.execute(
                "UPDATE retained_cover_removals SET state=?,attempt=min(attempt+1,3),"
                "updated_at=max(updated_at,?) WHERE plan_id=? AND state='pending'",
                (state, changed_at, plan_id),
            )
            if cursor.rowcount != 1:
                raise FieldEffectError("retained_cover")
            transaction.commit()
        except (sqlite3.Error, FieldEffectError) as error:
            transaction.rollback()
            if isinstance(error, FieldEffectError):
                raise
            raise FieldEffectError("persistence") from error


__all__ = [
    "MAXIMUM_LISTED_SWITCHES",
    "SQLiteRetainedCoverRepository",
    "release_unlisted_retained_covers",
    "schedule_retained_cover_removal",
]
