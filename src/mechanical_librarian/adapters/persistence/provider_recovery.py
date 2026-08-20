"""Exact-current SQLite persistence for dormant provider recovery eligibility."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from mechanical_librarian.domain.provider_recovery import (
    MAX_TIMESTAMP,
    ProviderRecovery,
    ProviderRecoveryError,
    ProviderRecoveryJob,
)


class ProviderRecoveryStoreError(RuntimeError):
    pass


_HEALTH = """CREATE TABLE provider_recovery_health(connection TEXT NOT NULL CHECK(length(connection) BETWEEN 1 AND 64),adapter TEXT NOT NULL CHECK(length(adapter) BETWEEN 1 AND 64),scope TEXT NOT NULL CHECK(length(scope) BETWEEN 1 AND 64),generation INTEGER NOT NULL CHECK(generation>=1),state TEXT NOT NULL CHECK(state IN ('waiting','dormant','checking','fanout','recovered','exhausted')),attempt INTEGER NOT NULL CHECK(attempt BETWEEN 0 AND 5),next_eligible_at INTEGER,claim_token TEXT,claim_expires_at INTEGER,PRIMARY KEY(connection,adapter,scope),FOREIGN KEY(connection,adapter,scope) REFERENCES provider_recovery_members(connection,adapter,scope),CHECK((state='waiting')=(next_eligible_at IS NOT NULL)),CHECK((state IN ('checking','fanout'))=(claim_token IS NOT NULL AND claim_expires_at IS NOT NULL)),CHECK(state IN ('checking','fanout') OR (claim_token IS NULL AND claim_expires_at IS NULL)),CHECK(state!='recovered' OR (attempt=0 AND next_eligible_at IS NULL)),CHECK(claim_token IS NULL OR length(claim_token) BETWEEN 1 AND 128),CHECK(claim_expires_at IS NULL OR claim_expires_at BETWEEN 0 AND 8640000000000),CHECK(next_eligible_at IS NULL OR next_eligible_at BETWEEN 0 AND 8640000000000)) STRICT"""
_MEMBERS = """CREATE TABLE provider_recovery_members(connection TEXT NOT NULL CHECK(length(connection) BETWEEN 1 AND 64),adapter TEXT NOT NULL CHECK(length(adapter) BETWEEN 1 AND 64),scope TEXT NOT NULL CHECK(length(scope) BETWEEN 1 AND 64),PRIMARY KEY(connection,adapter,scope)) STRICT"""
_JOBS = """CREATE TABLE provider_recovery_jobs(connection TEXT NOT NULL,adapter TEXT NOT NULL,scope TEXT NOT NULL,generation INTEGER NOT NULL CHECK(generation>=1),member_id TEXT NOT NULL CHECK(length(member_id) BETWEEN 1 AND 128),target_connection TEXT NOT NULL CHECK(length(target_connection) BETWEEN 1 AND 64),library TEXT NOT NULL CHECK(length(library) BETWEEN 1 AND 128),item TEXT NOT NULL CHECK(length(item) BETWEEN 1 AND 256),file_identity TEXT NOT NULL CHECK(length(file_identity) BETWEEN 1 AND 256),source_report_id TEXT NOT NULL CHECK(length(source_report_id) BETWEEN 1 AND 128),package_proposal_id TEXT CHECK(package_proposal_id IS NULL OR length(package_proposal_id) BETWEEN 1 AND 128),package_report_id TEXT NOT NULL CHECK(length(package_report_id) BETWEEN 1 AND 128),package_case_id TEXT CHECK(package_case_id IS NULL OR length(package_case_id) BETWEEN 1 AND 128),package_checkpoint INTEGER CHECK(package_checkpoint IS NULL OR package_checkpoint>=0),package_outcome TEXT NOT NULL CHECK(package_outcome IN ('report_only','admitted','superseded','no_case','retired')),lane_ids TEXT NOT NULL CHECK(length(lane_ids) BETWEEN 2 AND 4096),state TEXT NOT NULL CHECK(state IN ('queued','running','complete','failed','stale')),attempt INTEGER NOT NULL CHECK(attempt BETWEEN 0 AND 5),claim_token TEXT,claim_expires_at INTEGER,PRIMARY KEY(connection,adapter,scope,generation,member_id),FOREIGN KEY(connection,adapter,scope) REFERENCES provider_recovery_members(connection,adapter,scope),CHECK((package_outcome='report_only' AND package_proposal_id IS NULL AND package_case_id IS NULL AND package_checkpoint IS NULL) OR (package_outcome IN ('admitted','superseded') AND package_proposal_id IS NOT NULL AND package_case_id IS NOT NULL AND package_checkpoint IS NOT NULL) OR (package_outcome IN ('no_case','retired') AND package_proposal_id IS NOT NULL AND package_case_id IS NULL AND package_checkpoint IS NOT NULL)),CHECK((state='running')=(claim_token IS NOT NULL AND claim_expires_at IS NOT NULL)),CHECK(state='running' OR (claim_token IS NULL AND claim_expires_at IS NULL)),CHECK(claim_token IS NULL OR length(claim_token) BETWEEN 1 AND 128),CHECK(claim_expires_at IS NULL OR claim_expires_at BETWEEN 0 AND 8640000000000)) STRICT"""
_EVENTS = """CREATE TABLE provider_recovery_events(sequence INTEGER PRIMARY KEY,connection TEXT NOT NULL,adapter TEXT NOT NULL,scope TEXT NOT NULL,generation INTEGER NOT NULL CHECK(generation>=1),state TEXT NOT NULL CHECK(state IN ('waiting','dormant','checking','fanout','recovered','exhausted')),attempt INTEGER NOT NULL CHECK(attempt BETWEEN 0 AND 5),next_eligible_at INTEGER,occurred_at INTEGER NOT NULL CHECK(occurred_at BETWEEN 0 AND 8640000000000),CHECK((state='waiting')=(next_eligible_at IS NOT NULL)),CHECK(state!='recovered' OR (attempt=0 AND next_eligible_at IS NULL)),CHECK(next_eligible_at IS NULL OR next_eligible_at BETWEEN 0 AND 8640000000000)) STRICT"""
_NO_UPDATE = "CREATE TRIGGER provider_recovery_events_no_update BEFORE UPDATE ON provider_recovery_events BEGIN SELECT RAISE(ABORT, 'append only'); END"
_NO_DELETE = "CREATE TRIGGER provider_recovery_events_no_delete BEFORE DELETE ON provider_recovery_events BEGIN SELECT RAISE(ABORT, 'append only'); END"
_RECOVERY_NAMES = frozenset(
    (
        "provider_recovery_health",
        "provider_recovery_members",
        "provider_recovery_jobs",
        "provider_recovery_events",
        "provider_recovery_events_no_delete",
        "provider_recovery_events_no_update",
        "sqlite_autoindex_provider_recovery_health_1",
        "sqlite_autoindex_provider_recovery_members_1",
        "sqlite_autoindex_provider_recovery_jobs_1",
    )
)
_CREATE = (_MEMBERS, _HEALTH, _JOBS, _EVENTS, _NO_UPDATE, _NO_DELETE)

# The column shapes the schema above guarantees for the leading identity
# columns selected out of ``provider_recovery_health`` and the full immutable
# prefix selected out of ``provider_recovery_jobs``.  ``sqlite3`` hands these
# back as ``Any``; naming the shape lets the dataclass constructors below be
# checked positionally.
_HealthIdentity = tuple[str, str, str, int]
_JobColumns = tuple[
    str,
    str,
    str,
    int,
    str,
    str,
    str,
    str,
    str,
    str,
    str | None,
    str,
    str | None,
    int | None,
    str,
    str,
]


def _blocked_targets(targets: frozenset[str]) -> tuple[str, tuple[str, ...]]:
    if not isinstance(targets, frozenset) or any(
        type(target) is not str or not 1 <= len(target) <= 64 for target in targets
    ):
        raise ProviderRecoveryStoreError("provider recovery claim is invalid")
    if not targets:
        return "", ()
    return " AND target_connection NOT IN (" + ",".join(
        "?" for _ in targets
    ) + ")", tuple(sorted(targets))


def _append_checking(
    connection: sqlite3.Connection, recovery: ProviderRecovery, now: int
) -> None:
    connection.execute(
        "INSERT INTO provider_recovery_events(connection,adapter,scope,generation,state,attempt,next_eligible_at,occurred_at) VALUES(?,?,?,?,?,?,NULL,?)",
        (
            recovery.connection,
            recovery.adapter,
            recovery.scope,
            recovery.generation,
            "checking",
            recovery.attempt,
            now,
        ),
    )


class SQLiteProviderRecoveryStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._lock = threading.RLock()
        # Claim rollback is deliberately process-local.  It only protects the
        # owned, in-flight dispatch; durable history remains the source of
        # truth after a restart.
        self._claim_snapshots: dict[
            tuple[str, str, str, int, str],
            tuple[tuple[object, ...], tuple[object, ...] | None, int | None],
        ] = {}

    @classmethod
    def open(cls, path: Path) -> "SQLiteProviderRecoveryStore":
        connection = sqlite3.connect(
            path, isolation_level=None, check_same_thread=False
        )
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            if not _recovery_objects(connection):
                connection.execute("BEGIN IMMEDIATE")
                try:
                    for statement in _CREATE:
                        connection.execute(statement)
                    connection.execute("COMMIT")
                except Exception:
                    if connection.in_transaction:
                        connection.execute("ROLLBACK")
                    raise
            _validate(connection)
            return cls(connection)
        except ProviderRecoveryStoreError:
            connection.close()
            raise
        except (sqlite3.Error, OverflowError) as error:
            connection.close()
            raise ProviderRecoveryStoreError(
                "provider recovery database is unavailable or incompatible"
            ) from error

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @contextmanager
    def _rollback_on_failure(self, *wrapped: type[Exception]) -> Iterator[None]:
        """Abandon an open transaction and normalise the escaping failure.

        Every caller of this helper wraps ``sqlite3.Error`` and
        ``OverflowError``; past those the set belongs to the caller, so each
        one names the extra types it admits — ``ProviderRecoveryError`` where a
        domain value can still be rejected inside the transaction, and
        ``AttributeError`` additionally where a row is unpacked positionally.
        Passing nothing keeps the narrow set, which is what makes a widening
        visible at the call site rather than implied by which copy was pasted.

        That baseline binds the adopters and is not an invariant of the module.
        ``resume_fanout`` wraps ``ProviderRecoveryError`` but not
        ``OverflowError``, and ``enqueue_jobs`` wraps this exact pair but lets a
        ``ProviderRecoveryStoreError`` past without a rollback.  Both keep their
        epilogue longhand because adopting this one would change what they
        report, so neither should be widened to match the paragraph above.

        The rollback is guarded because a handler may run after the transaction
        has already ended.
        """
        try:
            yield
        except ProviderRecoveryStoreError:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except (sqlite3.Error, OverflowError) + wrapped as error:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise ProviderRecoveryStoreError(
                "provider recovery persistence is unavailable"
            ) from error

    def reconcile_generation(self, *, generation: int, now: int) -> int:
        if (
            type(generation) is not int
            or generation < 1
            or type(now) is not int
            or not 0 <= now <= MAX_TIMESTAMP
        ):
            raise ProviderRecoveryStoreError("provider recovery generation is invalid")
        try:
            with self._lock:
                return self._reconcile_generation(generation, now)
        except ProviderRecoveryStoreError:
            raise
        except (sqlite3.Error, OverflowError) as error:
            raise ProviderRecoveryStoreError(
                "provider recovery persistence is unavailable"
            ) from error

    def require_generation_compatible(self, *, generation: int) -> None:
        """Reject rollback before another durable store is touched."""
        if type(generation) is not int or generation < 1:
            raise ProviderRecoveryStoreError("provider recovery generation is invalid")
        try:
            with self._lock:
                newer = self._connection.execute(
                    "SELECT 1 FROM provider_recovery_health WHERE generation>? LIMIT 1",
                    (generation,),
                ).fetchone()
            if newer is not None:
                raise ProviderRecoveryStoreError(
                    "provider recovery generation is stale"
                )
        except ProviderRecoveryStoreError:
            raise
        except (sqlite3.Error, OverflowError) as error:
            raise ProviderRecoveryStoreError(
                "provider recovery persistence is unavailable"
            ) from error

    def _reconcile_generation(self, generation: int, now: int) -> int:
        with self._rollback_on_failure():
            self._connection.execute("BEGIN IMMEDIATE")
            if (
                self._connection.execute(
                    "SELECT 1 FROM provider_recovery_health WHERE generation>? LIMIT 1",
                    (generation,),
                ).fetchone()
                is not None
            ):
                raise ProviderRecoveryStoreError(
                    "provider recovery generation is stale"
                )
            rows = self._connection.execute(
                "SELECT connection,adapter,scope,generation,state,attempt,next_eligible_at FROM provider_recovery_health WHERE generation<? ORDER BY connection,adapter,scope",
                (generation,),
            ).fetchall()
            for row in rows:
                has_jobs = (
                    self._connection.execute(
                        "SELECT 1 FROM provider_recovery_jobs WHERE connection=? AND adapter=? "
                        "AND scope=? AND generation<=? AND state!='complete' LIMIT 1",
                        (row[0], row[1], row[2], generation),
                    ).fetchone()
                    is not None
                )
                successor = (
                    ProviderRecovery(
                        row[0], row[1], row[2], generation, "waiting", 0, now
                    )
                    if has_jobs
                    else ProviderRecovery(
                        row[0], row[1], row[2], generation, "dormant", row[5], None
                    )
                )
                self._connection.execute(
                    "DELETE FROM provider_recovery_jobs WHERE connection=? AND adapter=? "
                    "AND scope=? AND generation=? AND state!='complete' AND EXISTS ("
                    "SELECT 1 FROM provider_recovery_jobs current WHERE current.connection=provider_recovery_jobs.connection "
                    "AND current.adapter=provider_recovery_jobs.adapter AND current.scope=provider_recovery_jobs.scope "
                    "AND current.generation=? AND current.member_id=provider_recovery_jobs.member_id)",
                    (
                        successor.connection,
                        successor.adapter,
                        successor.scope,
                        row[3],
                        generation,
                    ),
                )
                self._connection.execute(
                    "UPDATE provider_recovery_jobs SET generation=?,state='queued',attempt=0,"
                    "claim_token=NULL,claim_expires_at=NULL WHERE connection=? AND adapter=? "
                    "AND scope=? AND generation=? AND state!='complete'",
                    (
                        generation,
                        successor.connection,
                        successor.adapter,
                        successor.scope,
                        row[3],
                    ),
                )
                if has_jobs:
                    self._connection.execute(
                        "UPDATE provider_recovery_health SET generation=?,state=?,attempt=?,next_eligible_at=?,claim_token=NULL,claim_expires_at=NULL WHERE connection=? AND adapter=? AND scope=?",
                        (
                            successor.generation,
                            successor.state,
                            successor.attempt,
                            successor.next_eligible_at,
                            successor.connection,
                            successor.adapter,
                            successor.scope,
                        ),
                    )
                else:
                    self._connection.execute(
                        "UPDATE provider_recovery_health SET generation=?,state=?,attempt=?,"
                        "next_eligible_at=?,claim_token=NULL,claim_expires_at=NULL "
                        "WHERE connection=? AND adapter=? AND scope=?",
                        (
                            successor.generation,
                            successor.state,
                            successor.attempt,
                            successor.next_eligible_at,
                            successor.connection,
                            successor.adapter,
                            successor.scope,
                        ),
                    )
                self._connection.execute(
                    "INSERT INTO provider_recovery_events(connection,adapter,scope,generation,state,attempt,next_eligible_at,occurred_at) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        successor.connection,
                        successor.adapter,
                        successor.scope,
                        successor.generation,
                        successor.state,
                        successor.attempt,
                        successor.next_eligible_at,
                        now,
                    ),
                )
            self._connection.execute("COMMIT")
            return len(rows)

    def complete(
        self,
        *,
        token: str,
        recovery: ProviderRecovery,
        now: int,
        failure: str,
        retry_after: int | None = None,
    ) -> ProviderRecovery:
        """Record the only permitted post-lease transition; never execute it."""
        if (
            type(token) is not str
            or not 1 <= len(token) <= 128
            or type(now) is not int
            or not 0 <= now <= MAX_TIMESTAMP
            or failure
            not in {
                "transient",
                "rate_limited",
                "authentication",
                "authorization",
                "configuration",
            }
        ):
            raise ProviderRecoveryStoreError("provider recovery claim is invalid")
        try:
            with self._lock:
                return self._complete(token, recovery, now, failure, retry_after)
        except ProviderRecoveryStoreError:
            raise
        except (sqlite3.Error, OverflowError, ProviderRecoveryError) as error:
            raise ProviderRecoveryStoreError(
                "provider recovery persistence is unavailable"
            ) from error

    def complete_success(
        self, *, token: str, recovery: ProviderRecovery, now: int
    ) -> ProviderRecovery:
        """Close a leased scope after its canary successor has published."""
        if (
            type(token) is not str
            or not 1 <= len(token) <= 128
            or type(now) is not int
            or not 0 <= now <= MAX_TIMESTAMP
        ):
            raise ProviderRecoveryStoreError("provider recovery claim is invalid")
        with self._rollback_on_failure(ProviderRecoveryError, AttributeError):
            with self._lock:
                self._connection.execute("BEGIN IMMEDIATE")
                current = self._connection.execute(
                    "SELECT generation,state,attempt,claim_token,claim_expires_at FROM provider_recovery_health WHERE connection=? AND adapter=? AND scope=?",
                    (recovery.connection, recovery.adapter, recovery.scope),
                ).fetchone()
                if (
                    current is None
                    or current[:3]
                    != (recovery.generation, "checking", recovery.attempt)
                    or current[3] != token
                    or current[4] <= now
                ):
                    raise ProviderRecoveryStoreError("provider recovery claim is owned")
                eligible = self._connection.execute(
                    "SELECT 1 FROM provider_recovery_jobs WHERE connection=? AND adapter=? "
                    "AND scope=? AND generation=? AND state IN ('queued','running') LIMIT 1",
                    (
                        recovery.connection,
                        recovery.adapter,
                        recovery.scope,
                        recovery.generation,
                    ),
                ).fetchone()
                if eligible is not None:
                    raise ProviderRecoveryStoreError("provider recovery members remain")
                successor = ProviderRecovery(
                    recovery.connection,
                    recovery.adapter,
                    recovery.scope,
                    recovery.generation,
                    "recovered",
                    0,
                    None,
                )
                _append_checking(self._connection, recovery, now)
                self._connection.execute(
                    "UPDATE provider_recovery_health SET state='recovered',attempt=0,next_eligible_at=NULL,claim_token=NULL,claim_expires_at=NULL WHERE connection=? AND adapter=? AND scope=?",
                    (recovery.connection, recovery.adapter, recovery.scope),
                )
                self._connection.execute(
                    "INSERT INTO provider_recovery_events(connection,adapter,scope,generation,state,attempt,next_eligible_at,occurred_at) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        successor.connection,
                        successor.adapter,
                        successor.scope,
                        successor.generation,
                        successor.state,
                        successor.attempt,
                        successor.next_eligible_at,
                        now,
                    ),
                )
                self._connection.execute("COMMIT")
                self._claim_snapshots.pop(
                    (
                        recovery.connection,
                        recovery.adapter,
                        recovery.scope,
                        recovery.generation,
                        token,
                    ),
                    None,
                )
                return successor

    def complete_missing_jobs(
        self,
        *,
        token: str,
        recovery: ProviderRecovery,
        now: int,
        blocked_targets: frozenset[str] = frozenset(),
    ) -> ProviderRecovery | None:
        """Fail closed when due health has no exact persisted member."""
        if (
            type(token) is not str
            or not 1 <= len(token) <= 128
            or type(now) is not int
            or not 0 <= now <= MAX_TIMESTAMP
        ):
            raise ProviderRecoveryStoreError("provider recovery claim is invalid")
        with self._rollback_on_failure(ProviderRecoveryError):
            with self._lock:
                self._connection.execute("BEGIN IMMEDIATE")
                current = self._connection.execute(
                    "SELECT generation,state,attempt,claim_token,claim_expires_at "
                    "FROM provider_recovery_health WHERE connection=? AND adapter=? AND scope=?",
                    (recovery.connection, recovery.adapter, recovery.scope),
                ).fetchone()
                if (
                    current is None
                    or current[:3]
                    != (recovery.generation, "checking", recovery.attempt)
                    or current[3] != token
                    or current[4] <= now
                ):
                    raise ProviderRecoveryStoreError("provider recovery claim is owned")
                excluded, parameters = _blocked_targets(blocked_targets)
                active = self._connection.execute(
                    "SELECT target_connection,state,attempt FROM provider_recovery_jobs WHERE connection=? AND adapter=? AND scope=? AND generation=? AND state IN ('queued','running')",
                    (
                        recovery.connection,
                        recovery.adapter,
                        recovery.scope,
                        recovery.generation,
                    ),
                ).fetchall()
                unblocked = tuple(
                    (state, attempt)
                    for target, state, attempt in active
                    if target not in blocked_targets
                )
                blocked = tuple(
                    (state, attempt)
                    for target, state, attempt in active
                    if target in blocked_targets
                )
                if unblocked:
                    if any(
                        state != "queued" or attempt < 5 for state, attempt in unblocked
                    ):
                        self._connection.execute("COMMIT")
                        return None
                    changed = self._connection.execute(
                        "UPDATE provider_recovery_jobs SET state='failed',claim_token=NULL,claim_expires_at=NULL WHERE connection=? AND adapter=? AND scope=? AND generation=? AND state='queued' AND attempt>=5"
                        + excluded,
                        (
                            recovery.connection,
                            recovery.adapter,
                            recovery.scope,
                            recovery.generation,
                            *parameters,
                        ),
                    ).rowcount
                    if changed != len(unblocked):
                        raise ProviderRecoveryStoreError(
                            "provider recovery members changed"
                        )
                if blocked:
                    snapshot = self._claim_snapshots.get(
                        (
                            recovery.connection,
                            recovery.adapter,
                            recovery.scope,
                            recovery.generation,
                            token,
                        )
                    )
                    if snapshot is None:
                        raise ProviderRecoveryStoreError(
                            "provider recovery claim snapshot is unavailable"
                        )
                    self._connection.execute(
                        "UPDATE provider_recovery_health SET state=?,attempt=?,next_eligible_at=?,claim_token=?,claim_expires_at=? WHERE connection=? AND adapter=? AND scope=?",
                        (
                            *snapshot[0],
                            recovery.connection,
                            recovery.adapter,
                            recovery.scope,
                        ),
                    )
                    self._connection.execute("COMMIT")
                    del self._claim_snapshots[
                        (
                            recovery.connection,
                            recovery.adapter,
                            recovery.scope,
                            recovery.generation,
                            token,
                        )
                    ]
                    return None
                successor = ProviderRecovery(
                    recovery.connection,
                    recovery.adapter,
                    recovery.scope,
                    recovery.generation,
                    "exhausted",
                    recovery.attempt,
                    None,
                )
                _append_checking(self._connection, recovery, now)
                self._connection.execute(
                    "UPDATE provider_recovery_health SET state='exhausted',"
                    "next_eligible_at=NULL,claim_token=NULL,claim_expires_at=NULL "
                    "WHERE connection=? AND adapter=? AND scope=?",
                    (recovery.connection, recovery.adapter, recovery.scope),
                )
                self._connection.execute(
                    "INSERT INTO provider_recovery_events(connection,adapter,scope,generation,"
                    "state,attempt,next_eligible_at,occurred_at) VALUES(?,?,?,?,?,?,NULL,?)",
                    (
                        successor.connection,
                        successor.adapter,
                        successor.scope,
                        successor.generation,
                        successor.state,
                        successor.attempt,
                        now,
                    ),
                )
                self._connection.execute("COMMIT")
                self._claim_snapshots.pop(
                    (
                        recovery.connection,
                        recovery.adapter,
                        recovery.scope,
                        recovery.generation,
                        token,
                    ),
                    None,
                )
                return successor

    def _complete(
        self,
        token: str,
        recovery: ProviderRecovery,
        now: int,
        failure: str,
        retry_after: int | None,
    ) -> ProviderRecovery:
        with self._rollback_on_failure(ProviderRecoveryError):
            self._connection.execute("BEGIN IMMEDIATE")
            current = self._connection.execute(
                "SELECT generation,state,attempt,claim_token,claim_expires_at FROM provider_recovery_health WHERE connection=? AND adapter=? AND scope=?",
                (recovery.connection, recovery.adapter, recovery.scope),
            ).fetchone()
            if (
                current is None
                or current[:3] != (recovery.generation, "checking", recovery.attempt)
                or current[3] != token
                or current[4] <= now
            ):
                raise ProviderRecoveryStoreError("provider recovery claim is owned")
            base = ProviderRecovery(
                recovery.connection,
                recovery.adapter,
                recovery.scope,
                recovery.generation,
                "waiting",
                recovery.attempt,
                now,
            )
            successor = (
                ProviderRecovery.failed(
                    connection=recovery.connection,
                    adapter=recovery.adapter,
                    scope=recovery.scope,
                    generation=recovery.generation,
                    failure=failure,
                    now=now,
                    retry_after=retry_after,
                )
                if failure in {"authentication", "authorization", "configuration"}
                else base.retry(now=now, retry_after=retry_after)
            )
            _append_checking(self._connection, recovery, now)
            self._connection.execute(
                "UPDATE provider_recovery_health SET state=?,attempt=?,next_eligible_at=?,claim_token=NULL,claim_expires_at=NULL WHERE connection=? AND adapter=? AND scope=?",
                (
                    successor.state,
                    successor.attempt,
                    successor.next_eligible_at,
                    successor.connection,
                    successor.adapter,
                    successor.scope,
                ),
            )
            self._connection.execute(
                "INSERT INTO provider_recovery_events(connection,adapter,scope,generation,state,attempt,next_eligible_at,occurred_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    successor.connection,
                    successor.adapter,
                    successor.scope,
                    successor.generation,
                    successor.state,
                    successor.attempt,
                    successor.next_eligible_at,
                    now,
                ),
            )
            self._connection.execute("COMMIT")
            return successor

    def record(self, recovery: ProviderRecovery, *, occurred_at: int) -> None:
        if type(occurred_at) is not int or occurred_at < 0:
            raise ProviderRecoveryStoreError("provider recovery is invalid")
        try:
            with self._lock:
                self._record(recovery, occurred_at)
                return
        except ProviderRecoveryStoreError:
            raise
        except (sqlite3.Error, OverflowError, ProviderRecoveryError) as error:
            raise ProviderRecoveryStoreError(
                "provider recovery persistence is unavailable"
            ) from error

    def _record(self, recovery: ProviderRecovery, occurred_at: int) -> None:
        with self._rollback_on_failure(ProviderRecoveryError):
            self._connection.execute("BEGIN IMMEDIATE")
            current = self._connection.execute(
                "SELECT generation,state,attempt,next_eligible_at,claim_token,claim_expires_at FROM provider_recovery_health WHERE connection=? AND adapter=? AND scope=?",
                (recovery.connection, recovery.adapter, recovery.scope),
            ).fetchone()
            if current is not None:
                if current[0] > recovery.generation:
                    raise ProviderRecoveryStoreError(
                        "provider recovery generation is stale"
                    )
                if current[1] == "checking":
                    raise ProviderRecoveryStoreError("provider recovery claim is owned")
                same = current[:4] == (
                    recovery.generation,
                    recovery.state,
                    recovery.attempt,
                    recovery.next_eligible_at,
                )
                if current[0] == recovery.generation and not same:
                    raise ProviderRecoveryStoreError(
                        "provider recovery transition is invalid"
                    )
                if current[0] < recovery.generation:
                    raise ProviderRecoveryStoreError(
                        "provider recovery generation is invalid"
                    )
            try:
                self._connection.execute(
                    "INSERT INTO provider_recovery_members(connection,adapter,scope) VALUES(?,?,?) ON CONFLICT DO NOTHING",
                    (recovery.connection, recovery.adapter, recovery.scope),
                )
                self._connection.execute(
                    "INSERT INTO provider_recovery_health(connection,adapter,scope,generation,state,attempt,next_eligible_at,claim_token,claim_expires_at) VALUES(?,?,?,?,?,?,?,NULL,NULL) ON CONFLICT(connection,adapter,scope) DO UPDATE SET generation=excluded.generation,state=excluded.state,attempt=excluded.attempt,next_eligible_at=excluded.next_eligible_at,claim_token=NULL,claim_expires_at=NULL",
                    (
                        recovery.connection,
                        recovery.adapter,
                        recovery.scope,
                        recovery.generation,
                        recovery.state,
                        recovery.attempt,
                        recovery.next_eligible_at,
                    ),
                )
                if current is None or not same:
                    self._connection.execute(
                        "INSERT INTO provider_recovery_events(connection,adapter,scope,generation,state,attempt,next_eligible_at,occurred_at) VALUES(?,?,?,?,?,?,?,?)",
                        (
                            recovery.connection,
                            recovery.adapter,
                            recovery.scope,
                            recovery.generation,
                            recovery.state,
                            recovery.attempt,
                            recovery.next_eligible_at,
                            occurred_at,
                        ),
                    )
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise

    def observe_failure(
        self,
        *,
        connection: str,
        adapter: str,
        scope: str,
        generation: int,
        failure: str,
        now: int,
    ) -> None:
        """Atomically fold one terminal F22 observation into closed health."""
        if failure not in {
            "transient",
            "rate_limited",
            "authentication",
            "authorization",
            "configuration",
        }:
            raise ProviderRecoveryStoreError("provider recovery observation is invalid")
        try:
            observed = ProviderRecovery.failed(
                connection=connection,
                adapter=adapter,
                scope=scope,
                generation=generation,
                failure=failure,
                now=now,
            )
            with self._lock:
                self._observe_failure(observed, now)
                return
        except ProviderRecoveryStoreError:
            raise
        except (sqlite3.Error, OverflowError, ProviderRecoveryError) as error:
            raise ProviderRecoveryStoreError(
                "provider recovery persistence is unavailable"
            ) from error

    def _observe_failure(self, observed: ProviderRecovery, now: int) -> None:
        with self._rollback_on_failure(ProviderRecoveryError):
            self._connection.execute("BEGIN IMMEDIATE")
            current = self._connection.execute(
                "SELECT generation,state,attempt,next_eligible_at FROM provider_recovery_health WHERE connection=? AND adapter=? AND scope=?",
                (observed.connection, observed.adapter, observed.scope),
            ).fetchone()
            if current is not None and current[0] > observed.generation:
                raise ProviderRecoveryStoreError(
                    "provider recovery generation is stale"
                )
            # A checking lease is completed only by complete().  Within one
            # generation, checking and dormant take precedence; dormant
            # observations otherwise replace a waiting observation once.
            successor = observed
            if current is not None:
                if current[0] < observed.generation:
                    raise ProviderRecoveryStoreError(
                        "provider recovery generation is invalid"
                    )
                if current[1] in {"checking", "fanout", "dormant"}:
                    self._connection.execute("COMMIT")
                    return
                if observed.state == "waiting" and current[1] == "waiting":
                    self._connection.execute("COMMIT")
                    return
            self._connection.execute(
                "INSERT INTO provider_recovery_members(connection,adapter,scope) VALUES(?,?,?) ON CONFLICT DO NOTHING",
                (observed.connection, observed.adapter, observed.scope),
            )
            self._connection.execute(
                "INSERT INTO provider_recovery_health(connection,adapter,scope,generation,state,attempt,next_eligible_at,claim_token,claim_expires_at) VALUES(?,?,?,?,?,?,?,NULL,NULL) ON CONFLICT(connection,adapter,scope) DO UPDATE SET generation=excluded.generation,state=excluded.state,attempt=excluded.attempt,next_eligible_at=excluded.next_eligible_at,claim_token=NULL,claim_expires_at=NULL",
                (
                    successor.connection,
                    successor.adapter,
                    successor.scope,
                    successor.generation,
                    successor.state,
                    successor.attempt,
                    successor.next_eligible_at,
                ),
            )
            self._connection.execute(
                "INSERT INTO provider_recovery_events(connection,adapter,scope,generation,state,attempt,next_eligible_at,occurred_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    successor.connection,
                    successor.adapter,
                    successor.scope,
                    successor.generation,
                    successor.state,
                    successor.attempt,
                    successor.next_eligible_at,
                    now,
                ),
            )
            self._connection.execute("COMMIT")

    def claim_one(
        self,
        *,
        generation: int,
        now: int,
        token: str,
        blocked_targets: frozenset[str] = frozenset(),
        lease_seconds: int = 300,
    ) -> ProviderRecovery | None:
        if (
            type(generation) is not int
            or generation < 1
            or type(now) is not int
            or now < 0
            or type(token) is not str
            or not 1 <= len(token) <= 128
            or type(lease_seconds) is not int
            or not 1 <= lease_seconds <= 900
        ):
            raise ProviderRecoveryStoreError("provider recovery claim is invalid")
        try:
            with self._lock:
                return self._claim_one(
                    generation, now, token, blocked_targets, lease_seconds
                )
        except (sqlite3.Error, OverflowError, ProviderRecoveryError) as error:
            raise ProviderRecoveryStoreError(
                "provider recovery persistence is unavailable"
            ) from error

    def _claim_one(
        self,
        generation: int,
        now: int,
        token: str,
        blocked_targets: frozenset[str],
        lease_seconds: int,
    ) -> ProviderRecovery | None:
        """Lease one due scope, keeping the lease once its ``COMMIT`` lands.

        Two rules keep the reported failure truthful.  The rollback is guarded
        because SQLite ends the transaction itself when a statement fails with
        ``SQLITE_FULL``, ``SQLITE_IOERR``, or ``SQLITE_NOMEM``; an unguarded
        one then raises ``cannot rollback - no transaction is active`` and that
        replaces the disk failure the caller needed to see.  The snapshot and
        the return value sit outside the handler because the claim is durable
        once committed and must not be reported as an abandoned transaction.

        A claim whose ``ProviderRecovery`` cannot be constructed therefore
        stays committed and the caller is told the claim failed.  The lease is
        the recovery: the query below reclaims a ``checking`` row once
        ``claim_expires_at`` passes, so the scope returns on its own without a
        compensating write whose own failure nothing could handle.  Reaching
        that path at all needs a row the health table's ``CHECK`` constraints
        admit and the dataclass rejects, which is narrower than it sounds: an
        identity carrying an embedded NUL qualifies, because SQLite's
        ``length()`` stops counting there and Python's ``len()`` does not.  No
        method here writes one, so the cost is one lease period per row some
        other writer put in the table.
        """
        with self._rollback_on_failure(ProviderRecoveryError):
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                excluded, parameters = _blocked_targets(blocked_targets)
                row = self._connection.execute(
                    "SELECT connection,adapter,scope,generation,state,attempt,next_eligible_at,claim_token,claim_expires_at FROM provider_recovery_health h WHERE generation=? AND ((state='waiting' AND next_eligible_at<=?) OR (state='checking' AND claim_expires_at<=?)) AND (NOT EXISTS (SELECT 1 FROM provider_recovery_jobs j WHERE j.connection=h.connection AND j.adapter=h.adapter AND j.scope=h.scope AND j.generation=h.generation AND j.state IN ('queued','running')) OR EXISTS (SELECT 1 FROM provider_recovery_jobs j WHERE j.connection=h.connection AND j.adapter=h.adapter AND j.scope=h.scope AND j.generation=h.generation AND j.state IN ('queued','running')"
                    + excluded
                    + ")) ORDER BY next_eligible_at,connection,adapter,scope LIMIT 1",
                    (generation, now, now, *parameters),
                ).fetchone()
                if row is None:
                    self._connection.execute("COMMIT")
                    return None
                changed = self._connection.execute(
                    "UPDATE provider_recovery_health SET state='checking',next_eligible_at=NULL,claim_token=?,claim_expires_at=? WHERE connection=? AND adapter=? AND scope=? AND ((state='waiting' AND next_eligible_at<=?) OR (state='checking' AND claim_expires_at<=?))",
                    (token, now + lease_seconds, row[0], row[1], row[2], now, now),
                ).rowcount
                if changed != 1:
                    self._connection.execute("COMMIT")
                    return None
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise
            self._claim_snapshots[(*row[:4], token)] = (row[4:], None, None)
            return ProviderRecovery(
                *cast(_HealthIdentity, row[:4]), "checking", row[5], None
            )

    def snapshot(self, *, limit: int = 32) -> tuple[ProviderRecovery, ...]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ProviderRecoveryStoreError("provider recovery query is invalid")
        try:
            with self._lock:
                rows = self._connection.execute(
                    "SELECT connection,adapter,scope,generation,state,attempt,next_eligible_at FROM provider_recovery_health ORDER BY connection,adapter,scope LIMIT ?",
                    (limit,),
                ).fetchall()
            return tuple(ProviderRecovery(*row) for row in rows)
        except (sqlite3.Error, ProviderRecoveryError) as error:
            raise ProviderRecoveryStoreError(
                "provider recovery persistence is unavailable"
            ) from error

    def resume_fanout(
        self,
        *,
        generation: int,
        now: int,
        token: str,
        blocked_targets: frozenset[str] = frozenset(),
        lease_seconds: int = 300,
    ) -> ProviderRecovery | None:
        """Claim one authorized scope before dispatching any remaining sibling."""
        if (
            type(generation) is not int
            or generation < 1
            or type(now) is not int
            or now < 0
            or type(token) is not str
            or not 1 <= len(token) <= 128
            or type(lease_seconds) is not int
            or not 1 <= lease_seconds <= 900
        ):
            raise ProviderRecoveryStoreError("provider recovery generation is invalid")
        try:
            with self._lock:
                self._connection.execute("BEGIN IMMEDIATE")
                excluded, parameters = _blocked_targets(blocked_targets)
                row = self._connection.execute(
                    "SELECT connection,adapter,scope,generation,state,attempt,next_eligible_at,claim_token,claim_expires_at "
                    "FROM provider_recovery_health h WHERE generation=? AND state='fanout' AND claim_expires_at<=? "
                    "AND EXISTS (SELECT 1 FROM provider_recovery_jobs j WHERE j.connection=h.connection AND j.adapter=h.adapter AND j.scope=h.scope AND j.generation=h.generation AND j.state IN ('queued','running')"
                    + excluded
                    + ") "
                    "ORDER BY connection,adapter,scope LIMIT 1",
                    (generation, now, *parameters),
                ).fetchone()
                if row is None:
                    self._connection.execute("COMMIT")
                    return None
                changed = self._connection.execute(
                    "UPDATE provider_recovery_health SET claim_token=?,claim_expires_at=? "
                    "WHERE connection=? AND adapter=? AND scope=? AND generation=? AND state='fanout' AND claim_expires_at<=?",
                    (token, now + lease_seconds, *row[:4], now),
                ).rowcount
                self._connection.execute("COMMIT")
                if changed != 1:
                    return None
                self._claim_snapshots[(*row[:4], token)] = (row[4:], None, None)
                return ProviderRecovery(*row[:7])
        except (sqlite3.Error, ProviderRecoveryError) as error:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise ProviderRecoveryStoreError(
                "provider recovery persistence is unavailable"
            ) from error

    def fanout_pending(self, *, recovery: ProviderRecovery) -> bool:
        try:
            with self._lock:
                return (
                    self._connection.execute(
                        "SELECT 1 FROM provider_recovery_jobs WHERE connection=? AND adapter=? "
                        "AND scope=? AND generation=? AND state IN ('queued','running') LIMIT 1",
                        (
                            recovery.connection,
                            recovery.adapter,
                            recovery.scope,
                            recovery.generation,
                        ),
                    ).fetchone()
                    is not None
                )
        except sqlite3.Error as error:
            raise ProviderRecoveryStoreError(
                "provider recovery persistence is unavailable"
            ) from error

    def activity_snapshot(self) -> tuple[dict[str, int | str | None], ...]:
        try:
            with self._lock:
                rows = self._connection.execute(
                    "SELECT h.state,MIN(h.next_eligible_at),"
                    "CASE WHEN h.state='fanout' THEN count(j.member_id) ELSE count(DISTINCT h.connection||char(0)||h.adapter||char(0)||h.scope) END "
                    "FROM provider_recovery_health h LEFT JOIN provider_recovery_jobs j "
                    "ON j.connection=h.connection AND j.adapter=h.adapter AND j.scope=h.scope "
                    "AND j.generation=h.generation AND j.state IN ('queued','running') "
                    "GROUP BY h.state ORDER BY h.state"
                ).fetchall()
            return tuple(
                {"state": row[0], "next_eligible_at": row[1], "affected_count": row[2]}
                for row in rows
            )
        except ProviderRecoveryStoreError:
            raise
        except sqlite3.Error as error:
            raise ProviderRecoveryStoreError(
                "provider recovery persistence is unavailable"
            ) from error

    def enqueue_jobs(
        self, jobs: tuple[ProviderRecoveryJob, ...], *, enqueued_at: int = 0
    ) -> int:
        """Durably register exact-current failed lanes before any canary runs.

        The full package tuple is deliberately stored with the job.  A caller
        must revalidate it before dispatch; a replayed registration is a no-op.
        """
        if (
            not jobs
            or type(enqueued_at) is not int
            or not 0 <= enqueued_at <= MAX_TIMESTAMP - 7_200
            or tuple(
                sorted(
                    jobs,
                    key=lambda job: (
                        job.connection,
                        job.adapter,
                        job.scope,
                        job.generation,
                        job.member_id,
                    ),
                )
            )
            != jobs
        ):
            raise ProviderRecoveryStoreError("provider recovery jobs are invalid")
        try:
            with self._lock:
                self._connection.execute("BEGIN IMMEDIATE")
                inserted = 0
                for job in jobs:
                    # A job is claimable only inside an enrolled scope.  Normal
                    # report publication has already recorded the exact health
                    # observation, but enqueue must not depend on that order:
                    # a replayed package can arrive first.  The fallback is
                    # starts at F24's two-hour transient cooldown. A current
                    # observation always wins unchanged.
                    self._connection.execute(
                        "INSERT INTO provider_recovery_members(connection,adapter,scope) VALUES(?,?,?) ON CONFLICT DO NOTHING",
                        (job.connection, job.adapter, job.scope),
                    )
                    self._connection.execute(
                        "INSERT INTO provider_recovery_health(connection,adapter,scope,generation,state,attempt,next_eligible_at,claim_token,claim_expires_at) VALUES(?,?,?,?,?,?,?,NULL,NULL) ON CONFLICT(connection,adapter,scope) DO NOTHING",
                        (
                            job.connection,
                            job.adapter,
                            job.scope,
                            job.generation,
                            "waiting",
                            1,
                            enqueued_at + 7_200,
                        ),
                    )
                    values = (
                        job.connection,
                        job.adapter,
                        job.scope,
                        job.generation,
                        job.member_id,
                        job.target_connection,
                        job.library,
                        job.item,
                        job.file_identity,
                        job.source_report_id,
                        job.package_proposal_id,
                        job.package_report_id,
                        job.package_case_id,
                        job.package_checkpoint,
                        job.package_outcome,
                        job.lane_ids,
                        job.state,
                        job.attempt,
                    )
                    result = self._connection.execute(
                        "INSERT INTO provider_recovery_jobs(connection,adapter,scope,generation,member_id,target_connection,library,item,file_identity,source_report_id,package_proposal_id,package_report_id,package_case_id,package_checkpoint,package_outcome,lane_ids,state,attempt,claim_token,claim_expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL) ON CONFLICT(connection,adapter,scope,generation,member_id) DO UPDATE SET target_connection=excluded.target_connection,library=excluded.library,item=excluded.item,file_identity=excluded.file_identity,source_report_id=excluded.source_report_id,package_proposal_id=excluded.package_proposal_id,package_report_id=excluded.package_report_id,package_case_id=excluded.package_case_id,package_checkpoint=excluded.package_checkpoint,package_outcome=excluded.package_outcome,lane_ids=excluded.lane_ids,state=excluded.state,attempt=excluded.attempt,claim_token=NULL,claim_expires_at=NULL WHERE target_connection IS NOT excluded.target_connection OR library IS NOT excluded.library OR item IS NOT excluded.item OR file_identity IS NOT excluded.file_identity OR source_report_id IS NOT excluded.source_report_id OR package_proposal_id IS NOT excluded.package_proposal_id OR package_report_id IS NOT excluded.package_report_id OR package_case_id IS NOT excluded.package_case_id OR package_checkpoint IS NOT excluded.package_checkpoint OR package_outcome IS NOT excluded.package_outcome OR lane_ids IS NOT excluded.lane_ids",
                        values,
                    )
                    inserted += result.rowcount
                self._connection.execute("COMMIT")
                return inserted
        except (sqlite3.Error, OverflowError) as error:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise ProviderRecoveryStoreError(
                "provider recovery persistence is unavailable"
            ) from error

    def claim_job(
        self,
        *,
        recovery: ProviderRecovery,
        token: str,
        now: int,
        blocked_targets: frozenset[str] = frozenset(),
        lease_seconds: int = 300,
    ) -> ProviderRecoveryJob | None:
        if (
            type(token) is not str
            or not token
            or type(now) is not int
            or now < 0
            or type(lease_seconds) is not int
            or not 1 <= lease_seconds <= 900
        ):
            raise ProviderRecoveryStoreError("provider recovery claim is invalid")
        with self._rollback_on_failure():
            with self._lock:
                self._connection.execute("BEGIN IMMEDIATE")
                health = self._connection.execute(
                    "SELECT state,attempt,next_eligible_at,claim_token,claim_expires_at FROM provider_recovery_health WHERE connection=? AND adapter=? AND scope=? AND generation=?",
                    (
                        recovery.connection,
                        recovery.adapter,
                        recovery.scope,
                        recovery.generation,
                    ),
                ).fetchone()
                if (
                    health is None
                    or health[0] not in {"checking", "fanout"}
                    or health[3] != token
                    or health[4] <= now
                ):
                    raise ProviderRecoveryStoreError("provider recovery claim is owned")
                excluded, parameters = _blocked_targets(blocked_targets)
                row = self._connection.execute(
                    "SELECT connection,adapter,scope,generation,member_id,target_connection,library,item,file_identity,source_report_id,package_proposal_id,package_report_id,package_case_id,package_checkpoint,package_outcome,lane_ids,state,attempt,claim_token,claim_expires_at FROM provider_recovery_jobs WHERE connection=? AND adapter=? AND scope=? AND generation=? AND attempt<5 AND (state='queued' OR (state='running' AND claim_expires_at<=?))"
                    + excluded
                    + " ORDER BY member_id LIMIT 1",
                    (
                        recovery.connection,
                        recovery.adapter,
                        recovery.scope,
                        recovery.generation,
                        now,
                        *parameters,
                    ),
                ).fetchone()
                if row is None:
                    self._connection.execute("COMMIT")
                    return None
                if (
                    self._connection.execute(
                        "UPDATE provider_recovery_jobs SET state='running',attempt=attempt+1,claim_token=?,claim_expires_at=? WHERE connection=? AND adapter=? AND scope=? AND generation=? AND member_id=? AND (state='queued' OR (state='running' AND claim_expires_at<=?))",
                        (token, now + lease_seconds, *row[:5], now),
                    ).rowcount
                    != 1
                ):
                    self._connection.execute("COMMIT")
                    return None
                self._connection.execute("COMMIT")
                key = (
                    recovery.connection,
                    recovery.adapter,
                    recovery.scope,
                    recovery.generation,
                    token,
                )
                prior = self._claim_snapshots.get(key)
                health_snapshot = health if prior is None else prior[0]
                event_sequence = None if prior is None else prior[2]
                self._claim_snapshots[key] = (
                    health_snapshot,
                    (row[16], row[17], row[18], row[19]),
                    event_sequence,
                )
                return ProviderRecoveryJob(
                    *cast(_JobColumns, row[:16]), "running", row[17] + 1
                )

    def renew_job(
        self,
        *,
        job: ProviderRecoveryJob,
        token: str,
        now: int,
        lease_seconds: int = 300,
    ) -> None:
        """Renew exact job ownership between bounded composed stages."""
        if (
            type(token) is not str
            or not token
            or type(now) is not int
            or now < 0
            or type(lease_seconds) is not int
            or not 1 <= lease_seconds <= 900
        ):
            raise ProviderRecoveryStoreError("provider recovery claim is invalid")
        with self._rollback_on_failure():
            with self._lock:
                self._connection.execute("BEGIN IMMEDIATE")
                expires_at = now + lease_seconds
                if (
                    self._connection.execute(
                        "UPDATE provider_recovery_jobs SET claim_expires_at=? WHERE connection=? "
                        "AND adapter=? AND scope=? AND generation=? AND member_id=? "
                        "AND state='running' AND claim_token=? AND claim_expires_at>?",
                        (
                            expires_at,
                            job.connection,
                            job.adapter,
                            job.scope,
                            job.generation,
                            job.member_id,
                            token,
                            now,
                        ),
                    ).rowcount
                    != 1
                ):
                    raise ProviderRecoveryStoreError("provider recovery claim is owned")
                health = self._connection.execute(
                    "SELECT state,claim_token,claim_expires_at FROM provider_recovery_health "
                    "WHERE connection=? AND adapter=? AND scope=? AND generation=?",
                    (job.connection, job.adapter, job.scope, job.generation),
                ).fetchone()
                if health is None:
                    raise ProviderRecoveryStoreError("provider recovery claim is owned")
                if health[0] == "checking":
                    if health[1] != token or health[2] <= now:
                        raise ProviderRecoveryStoreError(
                            "provider recovery claim is owned"
                        )
                    if (
                        self._connection.execute(
                            "UPDATE provider_recovery_health SET claim_expires_at=? WHERE connection=? "
                            "AND adapter=? AND scope=? AND generation=? AND state='checking' "
                            "AND claim_token=? AND claim_expires_at>?",
                            (
                                expires_at,
                                job.connection,
                                job.adapter,
                                job.scope,
                                job.generation,
                                token,
                                now,
                            ),
                        ).rowcount
                        != 1
                    ):
                        raise ProviderRecoveryStoreError(
                            "provider recovery claim is owned"
                        )
                elif health[0] == "fanout":
                    if health[1] != token or health[2] <= now:
                        raise ProviderRecoveryStoreError(
                            "provider recovery claim is owned"
                        )
                    if (
                        self._connection.execute(
                            "UPDATE provider_recovery_health SET claim_expires_at=? WHERE connection=? "
                            "AND adapter=? AND scope=? AND generation=? AND state='fanout' "
                            "AND claim_token=? AND claim_expires_at>?",
                            (
                                expires_at,
                                job.connection,
                                job.adapter,
                                job.scope,
                                job.generation,
                                token,
                                now,
                            ),
                        ).rowcount
                        != 1
                    ):
                        raise ProviderRecoveryStoreError(
                            "provider recovery claim is owned"
                        )
                else:
                    raise ProviderRecoveryStoreError("provider recovery claim is owned")
                self._connection.execute("COMMIT")

    def complete_job(
        self, *, job: ProviderRecoveryJob, token: str, now: int, success: bool
    ) -> None:
        with self._rollback_on_failure():
            with self._lock:
                self._connection.execute("BEGIN IMMEDIATE")
                state = "complete" if success else "failed"
                changed = self._connection.execute(
                    "UPDATE provider_recovery_jobs SET state=?,claim_token=NULL,claim_expires_at=NULL WHERE connection=? AND adapter=? AND scope=? AND generation=? AND member_id=? AND state='running' AND claim_token=? AND claim_expires_at>?",
                    (
                        state,
                        job.connection,
                        job.adapter,
                        job.scope,
                        job.generation,
                        job.member_id,
                        token,
                        now,
                    ),
                ).rowcount
                if changed != 1:
                    raise ProviderRecoveryStoreError("provider recovery claim is owned")
                self._connection.execute("COMMIT")

    def complete_stale_job(
        self, *, job: ProviderRecoveryJob, token: str, now: int
    ) -> None:
        with self._rollback_on_failure():
            with self._lock:
                self._connection.execute("BEGIN IMMEDIATE")
                changed = self._connection.execute(
                    "UPDATE provider_recovery_jobs SET state='stale',claim_token=NULL,claim_expires_at=NULL "
                    "WHERE connection=? AND adapter=? AND scope=? AND generation=? AND member_id=? "
                    "AND state='running' AND claim_token=? AND claim_expires_at>?",
                    (
                        job.connection,
                        job.adapter,
                        job.scope,
                        job.generation,
                        job.member_id,
                        token,
                        now,
                    ),
                ).rowcount
                if changed != 1:
                    raise ProviderRecoveryStoreError("provider recovery claim is owned")
                self._connection.execute("COMMIT")

    def release_target_hold(
        self,
        *,
        job: ProviderRecoveryJob,
        token: str,
        recovery: ProviderRecovery,
        now: int,
    ) -> None:
        """Release a target-held lease without consuming provider evidence."""
        with self._rollback_on_failure():
            with self._lock:
                self._connection.execute("BEGIN IMMEDIATE")
                current = self._connection.execute(
                    "SELECT state,attempt,next_eligible_at,claim_token,claim_expires_at FROM provider_recovery_health "
                    "WHERE connection=? AND adapter=? AND scope=? AND generation=?",
                    (
                        recovery.connection,
                        recovery.adapter,
                        recovery.scope,
                        recovery.generation,
                    ),
                ).fetchone()
                if (
                    current is None
                    or current[0] not in {"checking", "fanout"}
                    or current[3] != token
                    or current[4] <= now
                ):
                    raise ProviderRecoveryStoreError("provider recovery claim is owned")
                snapshot = self._claim_snapshots.get(
                    (
                        recovery.connection,
                        recovery.adapter,
                        recovery.scope,
                        recovery.generation,
                        token,
                    )
                )
                if snapshot is None or snapshot[1] is None:
                    raise ProviderRecoveryStoreError(
                        "provider recovery claim snapshot is unavailable"
                    )
                health_snapshot, job_snapshot, event_sequence = snapshot
                changed = self._connection.execute(
                    "UPDATE provider_recovery_jobs SET state=?,attempt=?,claim_token=?,claim_expires_at=? WHERE connection=? AND adapter=? "
                    "AND scope=? AND generation=? AND member_id=? AND state='running' "
                    "AND claim_token=? AND claim_expires_at>?",
                    (
                        *cast("tuple[object, ...]", job_snapshot),
                        job.connection,
                        job.adapter,
                        job.scope,
                        job.generation,
                        job.member_id,
                        token,
                        now,
                    ),
                ).rowcount
                if changed != 1:
                    raise ProviderRecoveryStoreError("provider recovery claim is owned")
                self._connection.execute(
                    "UPDATE provider_recovery_health SET state=?,attempt=?,next_eligible_at=?,claim_token=?,claim_expires_at=? WHERE connection=? AND adapter=? AND scope=?",
                    (
                        *health_snapshot,
                        recovery.connection,
                        recovery.adapter,
                        recovery.scope,
                    ),
                )
                self._connection.execute("COMMIT")
                del self._claim_snapshots[
                    (
                        recovery.connection,
                        recovery.adapter,
                        recovery.scope,
                        recovery.generation,
                        token,
                    )
                ]

    def complete_exhausted_scope(
        self,
        *,
        job: ProviderRecoveryJob,
        token: str,
        recovery: ProviderRecovery,
        now: int,
    ) -> ProviderRecovery:
        """Close a nonretryable canary without admitting sibling egress."""
        with self._rollback_on_failure(ProviderRecoveryError):
            with self._lock:
                self._connection.execute("BEGIN IMMEDIATE")
                current = self._connection.execute(
                    "SELECT state,claim_token,claim_expires_at FROM provider_recovery_health "
                    "WHERE connection=? AND adapter=? AND scope=? AND generation=?",
                    (
                        recovery.connection,
                        recovery.adapter,
                        recovery.scope,
                        recovery.generation,
                    ),
                ).fetchone()
                if (
                    current is None
                    or current[0] != "checking"
                    or current[1] != token
                    or current[2] <= now
                ):
                    raise ProviderRecoveryStoreError("provider recovery claim is owned")
                if (
                    self._connection.execute(
                        "UPDATE provider_recovery_jobs SET state='failed',claim_token=NULL,claim_expires_at=NULL "
                        "WHERE connection=? AND adapter=? AND scope=? AND generation=? "
                        "AND (state='queued' OR (state='running' AND member_id=? AND claim_token=? AND claim_expires_at>?))",
                        (
                            job.connection,
                            job.adapter,
                            job.scope,
                            job.generation,
                            job.member_id,
                            token,
                            now,
                        ),
                    ).rowcount
                    < 1
                ):
                    raise ProviderRecoveryStoreError("provider recovery claim is owned")
                successor = ProviderRecovery(
                    recovery.connection,
                    recovery.adapter,
                    recovery.scope,
                    recovery.generation,
                    "exhausted",
                    recovery.attempt,
                    None,
                )
                _append_checking(self._connection, recovery, now)
                self._connection.execute(
                    "UPDATE provider_recovery_health SET state='exhausted',next_eligible_at=NULL,"
                    "claim_token=NULL,claim_expires_at=NULL WHERE connection=? AND adapter=? AND scope=?",
                    (recovery.connection, recovery.adapter, recovery.scope),
                )
                self._connection.execute(
                    "INSERT INTO provider_recovery_events(connection,adapter,scope,generation,state,attempt,next_eligible_at,occurred_at) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        successor.connection,
                        successor.adapter,
                        successor.scope,
                        successor.generation,
                        successor.state,
                        successor.attempt,
                        None,
                        now,
                    ),
                )
                self._connection.execute("COMMIT")
                self._claim_snapshots.pop(
                    (
                        recovery.connection,
                        recovery.adapter,
                        recovery.scope,
                        recovery.generation,
                        token,
                    ),
                    None,
                )
                return successor

    def complete_canary_failure(
        self,
        *,
        job: ProviderRecoveryJob,
        token: str,
        recovery: ProviderRecovery,
        now: int,
        failure: str,
        retry_after: int | None,
    ) -> ProviderRecovery:
        """Requeue the canary and back off its scope in one transaction."""
        if failure not in {
            "transient",
            "rate_limited",
            "authentication",
            "authorization",
            "configuration",
        }:
            raise ProviderRecoveryStoreError("provider recovery claim is invalid")
        with self._rollback_on_failure(ProviderRecoveryError):
            with self._lock:
                self._connection.execute("BEGIN IMMEDIATE")
                current = self._connection.execute(
                    "SELECT generation,state,attempt,claim_token,claim_expires_at "
                    "FROM provider_recovery_health WHERE connection=? AND adapter=? AND scope=?",
                    (recovery.connection, recovery.adapter, recovery.scope),
                ).fetchone()
                if (
                    current is None
                    or current[:3]
                    != (recovery.generation, "checking", recovery.attempt)
                    or current[3] != token
                    or current[4] <= now
                ):
                    raise ProviderRecoveryStoreError("provider recovery claim is owned")
                changed = self._connection.execute(
                    "UPDATE provider_recovery_jobs SET state='queued',claim_token=NULL,claim_expires_at=NULL "
                    "WHERE connection=? AND adapter=? AND scope=? AND generation=? AND member_id=? "
                    "AND state='running' AND claim_token=? AND claim_expires_at>?",
                    (
                        job.connection,
                        job.adapter,
                        job.scope,
                        job.generation,
                        job.member_id,
                        token,
                        now,
                    ),
                ).rowcount
                if changed != 1:
                    raise ProviderRecoveryStoreError("provider recovery claim is owned")
                base = ProviderRecovery(
                    recovery.connection,
                    recovery.adapter,
                    recovery.scope,
                    recovery.generation,
                    "waiting",
                    recovery.attempt,
                    now,
                )
                successor = (
                    ProviderRecovery.failed(
                        connection=recovery.connection,
                        adapter=recovery.adapter,
                        scope=recovery.scope,
                        generation=recovery.generation,
                        failure=failure,
                        now=now,
                        retry_after=retry_after,
                    )
                    if failure in {"authentication", "authorization", "configuration"}
                    else base.retry(now=now, retry_after=retry_after)
                )
                _append_checking(self._connection, recovery, now)
                self._connection.execute(
                    "UPDATE provider_recovery_health SET state=?,attempt=?,next_eligible_at=?,"
                    "claim_token=NULL,claim_expires_at=NULL WHERE connection=? AND adapter=? AND scope=?",
                    (
                        successor.state,
                        successor.attempt,
                        successor.next_eligible_at,
                        successor.connection,
                        successor.adapter,
                        successor.scope,
                    ),
                )
                self._connection.execute(
                    "INSERT INTO provider_recovery_events(connection,adapter,scope,generation,state,attempt,next_eligible_at,occurred_at) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        successor.connection,
                        successor.adapter,
                        successor.scope,
                        successor.generation,
                        successor.state,
                        successor.attempt,
                        successor.next_eligible_at,
                        now,
                    ),
                )
                self._connection.execute("COMMIT")
                return successor

    def authorize_fanout(
        self,
        *,
        job: ProviderRecoveryJob,
        token: str,
        recovery: ProviderRecovery,
        now: int,
    ) -> ProviderRecovery:
        """Commit canary success before siblings receive independent leases."""
        with self._rollback_on_failure(ProviderRecoveryError):
            with self._lock:
                self._connection.execute("BEGIN IMMEDIATE")
                current = self._connection.execute(
                    "SELECT state,claim_token,claim_expires_at FROM provider_recovery_health "
                    "WHERE connection=? AND adapter=? AND scope=? AND generation=?",
                    (
                        recovery.connection,
                        recovery.adapter,
                        recovery.scope,
                        recovery.generation,
                    ),
                ).fetchone()
                if (
                    current is None
                    or current[0] != "checking"
                    or current[1] != token
                    or current[2] <= now
                ):
                    raise ProviderRecoveryStoreError("provider recovery claim is owned")
                changed = self._connection.execute(
                    "UPDATE provider_recovery_jobs SET state='complete',claim_token=NULL,claim_expires_at=NULL "
                    "WHERE connection=? AND adapter=? AND scope=? AND generation=? AND member_id=? "
                    "AND state='running' AND claim_token=? AND claim_expires_at>?",
                    (
                        job.connection,
                        job.adapter,
                        job.scope,
                        job.generation,
                        job.member_id,
                        token,
                        now,
                    ),
                ).rowcount
                if changed != 1:
                    raise ProviderRecoveryStoreError("provider recovery claim is owned")
                successor = ProviderRecovery(
                    recovery.connection,
                    recovery.adapter,
                    recovery.scope,
                    recovery.generation,
                    "fanout",
                    recovery.attempt,
                    None,
                )
                _append_checking(self._connection, recovery, now)
                self._connection.execute(
                    "UPDATE provider_recovery_health SET state='fanout',next_eligible_at=NULL "
                    "WHERE connection=? AND adapter=? AND scope=? AND generation=? AND state='checking' "
                    "AND claim_token=? AND claim_expires_at>?",
                    (
                        recovery.connection,
                        recovery.adapter,
                        recovery.scope,
                        recovery.generation,
                        token,
                        now,
                    ),
                )
                self._connection.execute(
                    "INSERT INTO provider_recovery_events(connection,adapter,scope,generation,state,attempt,next_eligible_at,occurred_at) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        successor.connection,
                        successor.adapter,
                        successor.scope,
                        successor.generation,
                        successor.state,
                        successor.attempt,
                        None,
                        now,
                    ),
                )
                self._connection.execute("COMMIT")
                self._claim_snapshots.pop(
                    (
                        recovery.connection,
                        recovery.adapter,
                        recovery.scope,
                        recovery.generation,
                        token,
                    ),
                    None,
                )
                return successor

    def finish_fanout(
        self, *, recovery: ProviderRecovery, token: str, now: int
    ) -> ProviderRecovery:
        """Close only after the exact generation has no eligible work."""
        with self._rollback_on_failure(ProviderRecoveryError):
            with self._lock:
                self._connection.execute("BEGIN IMMEDIATE")
                active = self._connection.execute(
                    "SELECT 1 FROM provider_recovery_jobs WHERE connection=? AND adapter=? "
                    "AND scope=? AND generation=? AND state IN ('queued','running') LIMIT 1",
                    (
                        recovery.connection,
                        recovery.adapter,
                        recovery.scope,
                        recovery.generation,
                    ),
                ).fetchone()
                if active is not None:
                    raise ProviderRecoveryStoreError("provider recovery members remain")
                failed = self._connection.execute(
                    "SELECT 1 FROM provider_recovery_jobs WHERE connection=? AND adapter=? "
                    "AND scope=? AND generation=? AND state='failed' LIMIT 1",
                    (
                        recovery.connection,
                        recovery.adapter,
                        recovery.scope,
                        recovery.generation,
                    ),
                ).fetchone()
                state = "exhausted" if failed is not None else "recovered"
                attempt = recovery.attempt if failed is not None else 0
                successor = ProviderRecovery(
                    recovery.connection,
                    recovery.adapter,
                    recovery.scope,
                    recovery.generation,
                    state,
                    attempt,
                    None,
                )
                changed = self._connection.execute(
                    "UPDATE provider_recovery_health SET state=?,attempt=?,next_eligible_at=NULL,claim_token=NULL,claim_expires_at=NULL "
                    "WHERE connection=? AND adapter=? AND scope=? AND generation=? AND state='fanout' "
                    "AND claim_token=? AND claim_expires_at>?",
                    (
                        state,
                        attempt,
                        recovery.connection,
                        recovery.adapter,
                        recovery.scope,
                        recovery.generation,
                        token,
                        now,
                    ),
                ).rowcount
                if changed != 1:
                    raise ProviderRecoveryStoreError(
                        "provider recovery transition is invalid"
                    )
                self._connection.execute(
                    "INSERT INTO provider_recovery_events(connection,adapter,scope,generation,state,attempt,next_eligible_at,occurred_at) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        successor.connection,
                        successor.adapter,
                        successor.scope,
                        successor.generation,
                        successor.state,
                        successor.attempt,
                        None,
                        now,
                    ),
                )
                self._connection.execute("COMMIT")
                return successor

    def interrupt_fanout(
        self,
        *,
        job: ProviderRecoveryJob,
        token: str,
        recovery: ProviderRecovery,
        now: int,
        failure: str,
        retry_after: int | None,
    ) -> ProviderRecovery:
        """Stop sibling egress when provider health fails again."""
        if failure not in {
            "transient",
            "rate_limited",
            "authentication",
            "authorization",
            "configuration",
        }:
            raise ProviderRecoveryStoreError("provider recovery claim is invalid")
        with self._rollback_on_failure(ProviderRecoveryError):
            with self._lock:
                self._connection.execute("BEGIN IMMEDIATE")
                state = self._connection.execute(
                    "SELECT state,attempt,claim_token,claim_expires_at FROM provider_recovery_health WHERE connection=? "
                    "AND adapter=? AND scope=? AND generation=?",
                    (
                        recovery.connection,
                        recovery.adapter,
                        recovery.scope,
                        recovery.generation,
                    ),
                ).fetchone()
                if (
                    state is None
                    or state[:3] != ("fanout", recovery.attempt, token)
                    or state[3] <= now
                ):
                    raise ProviderRecoveryStoreError(
                        "provider recovery transition is invalid"
                    )
                if (
                    self._connection.execute(
                        "UPDATE provider_recovery_jobs SET state='queued',claim_token=NULL,claim_expires_at=NULL "
                        "WHERE connection=? AND adapter=? AND scope=? AND generation=? AND member_id=? "
                        "AND state='running' AND claim_token=? AND claim_expires_at>?",
                        (
                            job.connection,
                            job.adapter,
                            job.scope,
                            job.generation,
                            job.member_id,
                            token,
                            now,
                        ),
                    ).rowcount
                    != 1
                ):
                    raise ProviderRecoveryStoreError("provider recovery claim is owned")
                base = ProviderRecovery(
                    recovery.connection,
                    recovery.adapter,
                    recovery.scope,
                    recovery.generation,
                    "waiting",
                    recovery.attempt,
                    now,
                )
                successor = (
                    ProviderRecovery.failed(
                        connection=recovery.connection,
                        adapter=recovery.adapter,
                        scope=recovery.scope,
                        generation=recovery.generation,
                        failure=failure,
                        now=now,
                        retry_after=retry_after,
                    )
                    if failure in {"authentication", "authorization", "configuration"}
                    else base.retry(now=now, retry_after=retry_after)
                )
                self._connection.execute(
                    "UPDATE provider_recovery_health SET state=?,attempt=?,next_eligible_at=?,"
                    "claim_token=NULL,claim_expires_at=NULL WHERE connection=? AND adapter=? AND scope=?",
                    (
                        successor.state,
                        successor.attempt,
                        successor.next_eligible_at,
                        successor.connection,
                        successor.adapter,
                        successor.scope,
                    ),
                )
                self._connection.execute(
                    "INSERT INTO provider_recovery_events(connection,adapter,scope,generation,state,attempt,next_eligible_at,occurred_at) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        successor.connection,
                        successor.adapter,
                        successor.scope,
                        successor.generation,
                        successor.state,
                        successor.attempt,
                        successor.next_eligible_at,
                        now,
                    ),
                )
                self._connection.execute("COMMIT")
                return successor


def _validate(connection: sqlite3.Connection) -> None:
    objects = _recovery_objects(connection)
    expected = sorted(
        (
            ("index", "sqlite_autoindex_provider_recovery_health_1", None),
            ("index", "sqlite_autoindex_provider_recovery_members_1", None),
            ("index", "sqlite_autoindex_provider_recovery_jobs_1", None),
            ("table", "provider_recovery_health", _HEALTH),
            ("table", "provider_recovery_members", _MEMBERS),
            ("table", "provider_recovery_jobs", _JOBS),
            ("table", "provider_recovery_events", _EVENTS),
            ("trigger", "provider_recovery_events_no_delete", _NO_DELETE),
            ("trigger", "provider_recovery_events_no_update", _NO_UPDATE),
        )
    )
    if objects != expected or connection.execute(
        "PRAGMA integrity_check"
    ).fetchone() != ("ok",):
        raise ProviderRecoveryStoreError(
            "provider recovery database is unavailable or incompatible"
        )
    try:
        members = set(
            connection.execute(
                "SELECT connection,adapter,scope FROM provider_recovery_members"
            ).fetchall()
        )
        health_keys = set(
            connection.execute(
                "SELECT connection,adapter,scope FROM provider_recovery_health"
            ).fetchall()
        )
        if members != health_keys:
            raise ProviderRecoveryStoreError(
                "provider recovery database is unavailable or incompatible"
            )
        for row in connection.execute(
            "SELECT connection,adapter,scope,generation,state,attempt,next_eligible_at,claim_token,claim_expires_at FROM provider_recovery_health"
        ):
            ProviderRecovery(*row[:7])
            if row[:3] not in members or (
                (row[4] in {"checking", "fanout"})
                != (
                    type(row[7]) is str
                    and 1 <= len(row[7]) <= 128
                    and type(row[8]) is int
                    and row[8] >= 0
                )
            ):
                raise ProviderRecoveryStoreError(
                    "provider recovery database is unavailable or incompatible"
                )
        for row in connection.execute(
            "SELECT connection,adapter,scope,generation,state,attempt,next_eligible_at,occurred_at FROM provider_recovery_events"
        ):
            ProviderRecovery(*row[:7])
            if (
                row[:3] not in members
                or type(row[7]) is not int
                or not 0 <= row[7] <= MAX_TIMESTAMP
                or (row[4] == "checking" and row[6] is not None)
            ):
                raise ProviderRecoveryStoreError(
                    "provider recovery database is unavailable or incompatible"
                )
        for row in connection.execute(
            "SELECT connection,adapter,scope,generation,member_id,target_connection,library,item,file_identity,source_report_id,package_proposal_id,package_report_id,package_case_id,package_checkpoint,package_outcome,lane_ids,state,attempt FROM provider_recovery_jobs"
        ):
            ProviderRecoveryJob(*row)
            if row[:3] not in members:
                raise ProviderRecoveryStoreError(
                    "provider recovery database is unavailable or incompatible"
                )
    except (sqlite3.Error, ProviderRecoveryError, TypeError, OverflowError) as error:
        raise ProviderRecoveryStoreError(
            "provider recovery database is unavailable or incompatible"
        ) from error


def _recovery_objects(
    connection: sqlite3.Connection,
) -> list[tuple[str, str, str | None]]:
    placeholders = ",".join("?" for _ in _RECOVERY_NAMES)
    return connection.execute(
        f"SELECT type,name,sql FROM sqlite_master WHERE name IN ({placeholders}) ORDER BY type,name",
        tuple(_RECOVERY_NAMES),
    ).fetchall()


__all__ = [
    "ProviderRecoveryJob",
    "ProviderRecoveryStoreError",
    "SQLiteProviderRecoveryStore",
]
