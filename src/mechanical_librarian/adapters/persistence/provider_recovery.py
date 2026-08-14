"""Exact-current SQLite persistence for dormant provider recovery eligibility."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from mechanical_librarian.domain.provider_recovery import MAX_TIMESTAMP, ProviderRecovery, ProviderRecoveryError, ProviderRecoveryJob


class ProviderRecoveryStoreError(RuntimeError):
    pass


_HEALTH = """CREATE TABLE provider_recovery_health(connection TEXT NOT NULL CHECK(length(connection) BETWEEN 1 AND 64),adapter TEXT NOT NULL CHECK(length(adapter) BETWEEN 1 AND 64),scope TEXT NOT NULL CHECK(length(scope) BETWEEN 1 AND 64),generation INTEGER NOT NULL CHECK(generation>=1),state TEXT NOT NULL CHECK(state IN ('waiting','dormant','checking','recovered')),attempt INTEGER NOT NULL CHECK(attempt BETWEEN 0 AND 5),next_eligible_at INTEGER,claim_token TEXT,claim_expires_at INTEGER,PRIMARY KEY(connection,adapter,scope),FOREIGN KEY(connection,adapter,scope) REFERENCES provider_recovery_members(connection,adapter,scope),CHECK((state='waiting')=(next_eligible_at IS NOT NULL)),CHECK((state='checking')=(claim_token IS NOT NULL AND claim_expires_at IS NOT NULL)),CHECK(state='checking' OR (claim_token IS NULL AND claim_expires_at IS NULL)),CHECK(state!='recovered' OR (attempt=0 AND next_eligible_at IS NULL)),CHECK(claim_token IS NULL OR length(claim_token) BETWEEN 1 AND 128),CHECK(claim_expires_at IS NULL OR claim_expires_at BETWEEN 0 AND 8640000000000),CHECK(next_eligible_at IS NULL OR next_eligible_at BETWEEN 0 AND 8640000000000)) STRICT"""
_MEMBERS = """CREATE TABLE provider_recovery_members(connection TEXT NOT NULL CHECK(length(connection) BETWEEN 1 AND 64),adapter TEXT NOT NULL CHECK(length(adapter) BETWEEN 1 AND 64),scope TEXT NOT NULL CHECK(length(scope) BETWEEN 1 AND 64),PRIMARY KEY(connection,adapter,scope)) STRICT"""
_JOBS = """CREATE TABLE provider_recovery_jobs(connection TEXT NOT NULL,adapter TEXT NOT NULL,scope TEXT NOT NULL,generation INTEGER NOT NULL CHECK(generation>=1),member_id TEXT NOT NULL CHECK(length(member_id) BETWEEN 1 AND 128),target_connection TEXT NOT NULL CHECK(length(target_connection) BETWEEN 1 AND 64),library TEXT NOT NULL CHECK(length(library) BETWEEN 1 AND 128),item TEXT NOT NULL CHECK(length(item) BETWEEN 1 AND 256),file_identity TEXT NOT NULL CHECK(length(file_identity) BETWEEN 1 AND 256),source_report_id TEXT NOT NULL CHECK(length(source_report_id) BETWEEN 1 AND 128),package_proposal_id TEXT NOT NULL CHECK(length(package_proposal_id) BETWEEN 1 AND 128),package_report_id TEXT NOT NULL CHECK(length(package_report_id) BETWEEN 1 AND 128),package_case_id TEXT NOT NULL CHECK(length(package_case_id) BETWEEN 1 AND 128),package_checkpoint INTEGER NOT NULL CHECK(package_checkpoint>=0),package_outcome TEXT NOT NULL CHECK(length(package_outcome) BETWEEN 1 AND 64),lane_ids TEXT NOT NULL CHECK(length(lane_ids) BETWEEN 2 AND 4096),state TEXT NOT NULL CHECK(state IN ('queued','running','complete','failed')),attempt INTEGER NOT NULL CHECK(attempt BETWEEN 0 AND 5),claim_token TEXT,claim_expires_at INTEGER,PRIMARY KEY(connection,adapter,scope,generation,member_id),FOREIGN KEY(connection,adapter,scope) REFERENCES provider_recovery_members(connection,adapter,scope),CHECK((state='running')=(claim_token IS NOT NULL AND claim_expires_at IS NOT NULL)),CHECK(state='running' OR (claim_token IS NULL AND claim_expires_at IS NULL)),CHECK(claim_token IS NULL OR length(claim_token) BETWEEN 1 AND 128),CHECK(claim_expires_at IS NULL OR claim_expires_at BETWEEN 0 AND 8640000000000)) STRICT"""
_EVENTS = """CREATE TABLE provider_recovery_events(sequence INTEGER PRIMARY KEY,connection TEXT NOT NULL,adapter TEXT NOT NULL,scope TEXT NOT NULL,generation INTEGER NOT NULL CHECK(generation>=1),state TEXT NOT NULL CHECK(state IN ('waiting','dormant','checking','recovered')),attempt INTEGER NOT NULL CHECK(attempt BETWEEN 0 AND 5),next_eligible_at INTEGER,occurred_at INTEGER NOT NULL CHECK(occurred_at BETWEEN 0 AND 8640000000000),CHECK((state='waiting')=(next_eligible_at IS NOT NULL)),CHECK(state!='recovered' OR (attempt=0 AND next_eligible_at IS NULL)),CHECK(next_eligible_at IS NULL OR next_eligible_at BETWEEN 0 AND 8640000000000)) STRICT"""
_NO_UPDATE = "CREATE TRIGGER provider_recovery_events_no_update BEFORE UPDATE ON provider_recovery_events BEGIN SELECT RAISE(ABORT, 'append only'); END"
_NO_DELETE = "CREATE TRIGGER provider_recovery_events_no_delete BEFORE DELETE ON provider_recovery_events BEGIN SELECT RAISE(ABORT, 'append only'); END"
_RECOVERY_NAMES = frozenset(("provider_recovery_health", "provider_recovery_members", "provider_recovery_jobs", "provider_recovery_events", "provider_recovery_events_no_delete", "provider_recovery_events_no_update", "sqlite_autoindex_provider_recovery_health_1", "sqlite_autoindex_provider_recovery_members_1", "sqlite_autoindex_provider_recovery_jobs_1"))
_CREATE = (_MEMBERS, _HEALTH, _JOBS, _EVENTS, _NO_UPDATE, _NO_DELETE)


class SQLiteProviderRecoveryStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._lock = threading.RLock()

    @classmethod
    def open(cls, path: Path) -> "SQLiteProviderRecoveryStore":
        connection = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            if not _recovery_objects(connection):
                connection.execute("BEGIN IMMEDIATE")
                try:
                    for statement in _CREATE:
                        connection.execute(statement)
                    connection.execute("COMMIT")
                except Exception:
                    connection.execute("ROLLBACK")
                    raise
            _validate(connection)
            return cls(connection)
        except ProviderRecoveryStoreError:
            connection.close()
            raise
        except (sqlite3.Error, OverflowError) as error:
            connection.close()
            raise ProviderRecoveryStoreError("provider recovery database is unavailable or incompatible") from error

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def reconcile_generation(self, *, generation: int, now: int) -> int:
        if type(generation) is not int or generation < 1 or type(now) is not int or not 0 <= now <= MAX_TIMESTAMP:
            raise ProviderRecoveryStoreError("provider recovery generation is invalid")
        try:
            with self._lock:
                return self._reconcile_generation(generation, now)
        except ProviderRecoveryStoreError:
            raise
        except (sqlite3.Error, OverflowError) as error:
            raise ProviderRecoveryStoreError("provider recovery persistence is unavailable") from error

    def _reconcile_generation(self, generation: int, now: int) -> int:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            if self._connection.execute("SELECT 1 FROM provider_recovery_health WHERE generation>? LIMIT 1", (generation,)).fetchone() is not None:
                raise ProviderRecoveryStoreError("provider recovery generation is stale")
            rows = self._connection.execute("SELECT connection,adapter,scope,generation,state,attempt,next_eligible_at FROM provider_recovery_health WHERE generation<? ORDER BY connection,adapter,scope", (generation,)).fetchall()
            for row in rows:
                successor = ProviderRecovery(*row).with_generation(generation, now=now)
                self._connection.execute("UPDATE provider_recovery_health SET generation=?,state=?,attempt=?,next_eligible_at=? WHERE connection=? AND adapter=? AND scope=?", (successor.generation, successor.state, successor.attempt, successor.next_eligible_at, successor.connection, successor.adapter, successor.scope))
                self._connection.execute("INSERT INTO provider_recovery_events(connection,adapter,scope,generation,state,attempt,next_eligible_at,occurred_at) VALUES(?,?,?,?,?,?,?,?)", (successor.connection, successor.adapter, successor.scope, successor.generation, successor.state, successor.attempt, successor.next_eligible_at, now))
            self._connection.execute("COMMIT")
            return len(rows)
        except ProviderRecoveryStoreError:
            if self._connection.in_transaction: self._connection.execute("ROLLBACK")
            raise
        except (sqlite3.Error, OverflowError) as error:
            if self._connection.in_transaction: self._connection.execute("ROLLBACK")
            raise ProviderRecoveryStoreError("provider recovery persistence is unavailable") from error

    def complete(self, *, token: str, recovery: ProviderRecovery, now: int, failure: str, retry_after: int | None = None) -> ProviderRecovery:
        """Record the only permitted post-lease transition; never execute it."""
        if (type(token) is not str or not 1 <= len(token) <= 128
                or type(now) is not int or not 0 <= now <= MAX_TIMESTAMP
                or failure not in {"transient", "rate_limited", "authentication", "authorization", "configuration"}):
            raise ProviderRecoveryStoreError("provider recovery claim is invalid")
        try:
            with self._lock:
                return self._complete(token, recovery, now, failure, retry_after)
        except ProviderRecoveryStoreError:
            raise
        except (sqlite3.Error, OverflowError, ProviderRecoveryError) as error:
            raise ProviderRecoveryStoreError("provider recovery persistence is unavailable") from error

    def complete_success(self, *, token: str, recovery: ProviderRecovery, now: int) -> ProviderRecovery:
        """Close a leased scope after its canary successor has published."""
        if (type(token) is not str or not 1 <= len(token) <= 128
                or type(now) is not int or not 0 <= now <= MAX_TIMESTAMP):
            raise ProviderRecoveryStoreError("provider recovery claim is invalid")
        try:
            with self._lock:
                self._connection.execute("BEGIN IMMEDIATE")
                current = self._connection.execute("SELECT generation,state,attempt,claim_token,claim_expires_at FROM provider_recovery_health WHERE connection=? AND adapter=? AND scope=?", (recovery.connection, recovery.adapter, recovery.scope)).fetchone()
                if current is None or current[:3] != (recovery.generation, "checking", recovery.attempt) or current[3] != token or current[4] <= now:
                    raise ProviderRecoveryStoreError("provider recovery claim is owned")
                successor = ProviderRecovery(recovery.connection, recovery.adapter, recovery.scope, recovery.generation, "recovered", 0, None)
                self._connection.execute("UPDATE provider_recovery_health SET state='recovered',attempt=0,next_eligible_at=NULL,claim_token=NULL,claim_expires_at=NULL WHERE connection=? AND adapter=? AND scope=?", (recovery.connection, recovery.adapter, recovery.scope))
                self._connection.execute("INSERT INTO provider_recovery_events(connection,adapter,scope,generation,state,attempt,next_eligible_at,occurred_at) VALUES(?,?,?,?,?,?,?,?)", (successor.connection, successor.adapter, successor.scope, successor.generation, successor.state, successor.attempt, successor.next_eligible_at, now))
                self._connection.execute("COMMIT")
                return successor
        except ProviderRecoveryStoreError:
            if self._connection.in_transaction: self._connection.execute("ROLLBACK")
            raise
        except (sqlite3.Error, OverflowError, ProviderRecoveryError, AttributeError) as error:
            if self._connection.in_transaction: self._connection.execute("ROLLBACK")
            raise ProviderRecoveryStoreError("provider recovery persistence is unavailable") from error

    def _complete(self, token: str, recovery: ProviderRecovery, now: int, failure: str, retry_after: int | None) -> ProviderRecovery:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            current = self._connection.execute("SELECT generation,state,attempt,claim_token,claim_expires_at FROM provider_recovery_health WHERE connection=? AND adapter=? AND scope=?", (recovery.connection, recovery.adapter, recovery.scope)).fetchone()
            if current is None or current[:3] != (recovery.generation, "checking", recovery.attempt) or current[3] != token or current[4] <= now:
                raise ProviderRecoveryStoreError("provider recovery claim is owned")
            base = ProviderRecovery(recovery.connection, recovery.adapter, recovery.scope, recovery.generation, "waiting", recovery.attempt, now)
            successor = ProviderRecovery.failed(connection=recovery.connection, adapter=recovery.adapter, scope=recovery.scope, generation=recovery.generation, failure=failure, now=now, retry_after=retry_after) if failure in {"authentication", "authorization", "configuration"} else base.retry(now=now, retry_after=retry_after)
            self._connection.execute("UPDATE provider_recovery_health SET state=?,attempt=?,next_eligible_at=?,claim_token=NULL,claim_expires_at=NULL WHERE connection=? AND adapter=? AND scope=?", (successor.state, successor.attempt, successor.next_eligible_at, successor.connection, successor.adapter, successor.scope))
            self._connection.execute("INSERT INTO provider_recovery_events(connection,adapter,scope,generation,state,attempt,next_eligible_at,occurred_at) VALUES(?,?,?,?,?,?,?,?)", (successor.connection, successor.adapter, successor.scope, successor.generation, successor.state, successor.attempt, successor.next_eligible_at, now))
            self._connection.execute("COMMIT")
            return successor
        except ProviderRecoveryStoreError:
            if self._connection.in_transaction: self._connection.execute("ROLLBACK")
            raise
        except (sqlite3.Error, OverflowError, ProviderRecoveryError) as error:
            if self._connection.in_transaction: self._connection.execute("ROLLBACK")
            raise ProviderRecoveryStoreError("provider recovery persistence is unavailable") from error

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
            raise ProviderRecoveryStoreError("provider recovery persistence is unavailable") from error

    def _record(self, recovery: ProviderRecovery, occurred_at: int) -> None:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            current = self._connection.execute("SELECT generation,state,attempt,next_eligible_at,claim_token,claim_expires_at FROM provider_recovery_health WHERE connection=? AND adapter=? AND scope=?", (recovery.connection, recovery.adapter, recovery.scope)).fetchone()
            if current is not None:
                if current[0] > recovery.generation:
                    raise ProviderRecoveryStoreError("provider recovery generation is stale")
                if current[1] == "checking":
                    raise ProviderRecoveryStoreError("provider recovery claim is owned")
                same = current[:4] == (recovery.generation, recovery.state, recovery.attempt, recovery.next_eligible_at)
                if current[0] == recovery.generation and not same:
                    raise ProviderRecoveryStoreError("provider recovery transition is invalid")
                if current[0] < recovery.generation:
                    raise ProviderRecoveryStoreError("provider recovery generation is invalid")
            try:
                self._connection.execute("INSERT INTO provider_recovery_members(connection,adapter,scope) VALUES(?,?,?) ON CONFLICT DO NOTHING", (recovery.connection, recovery.adapter, recovery.scope))
                self._connection.execute("INSERT INTO provider_recovery_health(connection,adapter,scope,generation,state,attempt,next_eligible_at,claim_token,claim_expires_at) VALUES(?,?,?,?,?,?,?,NULL,NULL) ON CONFLICT(connection,adapter,scope) DO UPDATE SET generation=excluded.generation,state=excluded.state,attempt=excluded.attempt,next_eligible_at=excluded.next_eligible_at,claim_token=NULL,claim_expires_at=NULL", (recovery.connection, recovery.adapter, recovery.scope, recovery.generation, recovery.state, recovery.attempt, recovery.next_eligible_at))
                if current is None or not same:
                    self._connection.execute("INSERT INTO provider_recovery_events(connection,adapter,scope,generation,state,attempt,next_eligible_at,occurred_at) VALUES(?,?,?,?,?,?,?,?)", (recovery.connection, recovery.adapter, recovery.scope, recovery.generation, recovery.state, recovery.attempt, recovery.next_eligible_at, occurred_at))
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
        except ProviderRecoveryStoreError:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except (sqlite3.Error, OverflowError, ProviderRecoveryError) as error:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise ProviderRecoveryStoreError("provider recovery persistence is unavailable") from error

    def observe_failure(self, *, connection: str, adapter: str, scope: str, generation: int, failure: str, now: int) -> None:
        """Atomically fold one terminal F22 observation into closed health."""
        if failure not in {"transient", "rate_limited", "authentication", "authorization", "configuration"}:
            raise ProviderRecoveryStoreError("provider recovery observation is invalid")
        try:
            observed = ProviderRecovery.failed(connection=connection, adapter=adapter, scope=scope, generation=generation, failure=failure, now=now)
            with self._lock:
                self._observe_failure(observed, now)
                return
        except ProviderRecoveryStoreError:
            raise
        except (sqlite3.Error, OverflowError, ProviderRecoveryError) as error:
            raise ProviderRecoveryStoreError("provider recovery persistence is unavailable") from error

    def _observe_failure(self, observed: ProviderRecovery, now: int) -> None:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            current = self._connection.execute("SELECT generation,state,attempt,next_eligible_at FROM provider_recovery_health WHERE connection=? AND adapter=? AND scope=?", (observed.connection, observed.adapter, observed.scope)).fetchone()
            if current is not None and current[0] > observed.generation:
                raise ProviderRecoveryStoreError("provider recovery generation is stale")
            # A checking lease is completed only by complete().  Within one
            # generation, checking and dormant take precedence; dormant
            # observations otherwise replace a waiting observation once.
            successor = observed
            if current is not None:
                if current[0] < observed.generation:
                    raise ProviderRecoveryStoreError("provider recovery generation is invalid")
                if current[1] in {"checking", "dormant"} or observed.state == "waiting":
                    self._connection.execute("COMMIT")
                    return
            self._connection.execute("INSERT INTO provider_recovery_members(connection,adapter,scope) VALUES(?,?,?) ON CONFLICT DO NOTHING", (observed.connection, observed.adapter, observed.scope))
            self._connection.execute("INSERT INTO provider_recovery_health(connection,adapter,scope,generation,state,attempt,next_eligible_at,claim_token,claim_expires_at) VALUES(?,?,?,?,?,?,?,NULL,NULL) ON CONFLICT(connection,adapter,scope) DO UPDATE SET generation=excluded.generation,state=excluded.state,attempt=excluded.attempt,next_eligible_at=excluded.next_eligible_at,claim_token=NULL,claim_expires_at=NULL", (successor.connection, successor.adapter, successor.scope, successor.generation, successor.state, successor.attempt, successor.next_eligible_at))
            self._connection.execute("INSERT INTO provider_recovery_events(connection,adapter,scope,generation,state,attempt,next_eligible_at,occurred_at) VALUES(?,?,?,?,?,?,?,?)", (successor.connection, successor.adapter, successor.scope, successor.generation, successor.state, successor.attempt, successor.next_eligible_at, now))
            self._connection.execute("COMMIT")
        except ProviderRecoveryStoreError:
            if self._connection.in_transaction: self._connection.execute("ROLLBACK")
            raise
        except (sqlite3.Error, OverflowError, ProviderRecoveryError) as error:
            if self._connection.in_transaction: self._connection.execute("ROLLBACK")
            raise ProviderRecoveryStoreError("provider recovery persistence is unavailable") from error

    def claim_one(self, *, generation: int, now: int, token: str, lease_seconds: int = 60) -> ProviderRecovery | None:
        if type(generation) is not int or generation < 1 or type(now) is not int or now < 0 or type(token) is not str or not 1 <= len(token) <= 128 or type(lease_seconds) is not int or not 1 <= lease_seconds <= 300:
            raise ProviderRecoveryStoreError("provider recovery claim is invalid")
        try:
            with self._lock:
                return self._claim_one(generation, now, token, lease_seconds)
        except (sqlite3.Error, OverflowError, ProviderRecoveryError) as error:
            raise ProviderRecoveryStoreError("provider recovery persistence is unavailable") from error

    def _claim_one(self, generation: int, now: int, token: str, lease_seconds: int) -> ProviderRecovery | None:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute("SELECT connection,adapter,scope,generation,state,attempt,next_eligible_at FROM provider_recovery_health WHERE generation=? AND ((state='waiting' AND next_eligible_at<=?) OR (state='checking' AND claim_expires_at<=?)) ORDER BY next_eligible_at,connection,adapter,scope LIMIT 1", (generation, now, now)).fetchone()
                if row is None:
                    self._connection.execute("COMMIT")
                    return None
                changed = self._connection.execute("UPDATE provider_recovery_health SET state='checking',next_eligible_at=NULL,claim_token=?,claim_expires_at=? WHERE connection=? AND adapter=? AND scope=? AND ((state='waiting' AND next_eligible_at<=?) OR (state='checking' AND claim_expires_at<=?))", (token, now + lease_seconds, row[0], row[1], row[2], now, now)).rowcount
                if changed != 1:
                    self._connection.execute("COMMIT")
                    return None
                self._connection.execute("INSERT INTO provider_recovery_events(connection,adapter,scope,generation,state,attempt,next_eligible_at,occurred_at) VALUES(?,?,?,?,?,?,?,?)", (*row[:4], "checking", row[5], None, now))
                self._connection.execute("COMMIT")
                return ProviderRecovery(*row[:4], "checking", row[5], None)
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
        except (sqlite3.Error, OverflowError, ProviderRecoveryError) as error:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise ProviderRecoveryStoreError("provider recovery persistence is unavailable") from error

    def snapshot(self, *, limit: int = 32) -> tuple[ProviderRecovery, ...]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ProviderRecoveryStoreError("provider recovery query is invalid")
        try:
            with self._lock:
                rows = self._connection.execute("SELECT connection,adapter,scope,generation,state,attempt,next_eligible_at FROM provider_recovery_health ORDER BY connection,adapter,scope LIMIT ?", (limit,)).fetchall()
            return tuple(ProviderRecovery(*row) for row in rows)
        except (sqlite3.Error, ProviderRecoveryError) as error:
            raise ProviderRecoveryStoreError("provider recovery persistence is unavailable") from error

    def activity_snapshot(self) -> tuple[dict[str, int | str | None], ...]:
        try:
            with self._lock:
                rows = self._connection.execute(
                    "SELECT state,MIN(next_eligible_at),count(*) "
                    "FROM provider_recovery_health GROUP BY state ORDER BY state"
                ).fetchall()
            return tuple({"state": row[0], "next_eligible_at": row[1], "affected_count": row[2]} for row in rows)
        except ProviderRecoveryStoreError:
            raise
        except sqlite3.Error as error:
            raise ProviderRecoveryStoreError("provider recovery persistence is unavailable") from error

    def enqueue_jobs(self, jobs: tuple[ProviderRecoveryJob, ...]) -> int:
        """Durably register exact-current failed lanes before any canary runs.

        The full package tuple is deliberately stored with the job.  A caller
        must revalidate it before dispatch; a replayed registration is a no-op.
        """
        if not jobs or tuple(sorted(jobs, key=lambda job: (job.connection, job.adapter, job.scope, job.generation, job.member_id))) != jobs:
            raise ProviderRecoveryStoreError("provider recovery jobs are invalid")
        try:
            with self._lock:
                self._connection.execute("BEGIN IMMEDIATE")
                inserted = 0
                for job in jobs:
                    values = (job.connection, job.adapter, job.scope, job.generation, job.member_id, job.target_connection, job.library, job.item, job.file_identity, job.source_report_id, job.package_proposal_id, job.package_report_id, job.package_case_id, job.package_checkpoint, job.package_outcome, job.lane_ids, job.state, job.attempt)
                    result = self._connection.execute("INSERT INTO provider_recovery_jobs(connection,adapter,scope,generation,member_id,target_connection,library,item,file_identity,source_report_id,package_proposal_id,package_report_id,package_case_id,package_checkpoint,package_outcome,lane_ids,state,attempt,claim_token,claim_expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL) ON CONFLICT(connection,adapter,scope,generation,member_id) DO NOTHING", values)
                    inserted += result.rowcount
                self._connection.execute("COMMIT")
                return inserted
        except (sqlite3.Error, OverflowError) as error:
            if self._connection.in_transaction: self._connection.execute("ROLLBACK")
            raise ProviderRecoveryStoreError("provider recovery persistence is unavailable") from error

    def claim_job(self, *, recovery: ProviderRecovery, token: str, now: int, lease_seconds: int = 60) -> ProviderRecoveryJob | None:
        if type(token) is not str or not token or type(now) is not int or now < 0 or type(lease_seconds) is not int or not 1 <= lease_seconds <= 300:
            raise ProviderRecoveryStoreError("provider recovery claim is invalid")
        try:
            with self._lock:
                self._connection.execute("BEGIN IMMEDIATE")
                row = self._connection.execute("SELECT connection,adapter,scope,generation,member_id,target_connection,library,item,file_identity,source_report_id,package_proposal_id,package_report_id,package_case_id,package_checkpoint,package_outcome,lane_ids,state,attempt FROM provider_recovery_jobs WHERE connection=? AND adapter=? AND scope=? AND generation=? AND (state='queued' OR (state='running' AND claim_expires_at<=?)) ORDER BY member_id LIMIT 1", (recovery.connection, recovery.adapter, recovery.scope, recovery.generation, now)).fetchone()
                if row is None:
                    self._connection.execute("COMMIT"); return None
                if self._connection.execute("UPDATE provider_recovery_jobs SET state='running',attempt=attempt+1,claim_token=?,claim_expires_at=? WHERE connection=? AND adapter=? AND scope=? AND generation=? AND member_id=? AND (state='queued' OR (state='running' AND claim_expires_at<=?))", (token, now + lease_seconds, *row[:5], now)).rowcount != 1:
                    self._connection.execute("COMMIT"); return None
                self._connection.execute("COMMIT")
                return ProviderRecoveryJob(*row[:16], "running", row[17] + 1)
        except (sqlite3.Error, OverflowError) as error:
            if self._connection.in_transaction: self._connection.execute("ROLLBACK")
            raise ProviderRecoveryStoreError("provider recovery persistence is unavailable") from error

    def complete_job(self, *, job: ProviderRecoveryJob, token: str, now: int, success: bool) -> None:
        try:
            with self._lock:
                self._connection.execute("BEGIN IMMEDIATE")
                state = "complete" if success else "failed"
                changed = self._connection.execute("UPDATE provider_recovery_jobs SET state=?,claim_token=NULL,claim_expires_at=NULL WHERE connection=? AND adapter=? AND scope=? AND generation=? AND member_id=? AND state='running' AND claim_token=? AND claim_expires_at>?", (state, job.connection, job.adapter, job.scope, job.generation, job.member_id, token, now)).rowcount
                if changed != 1: raise ProviderRecoveryStoreError("provider recovery claim is owned")
                self._connection.execute("COMMIT")
        except ProviderRecoveryStoreError:
            if self._connection.in_transaction: self._connection.execute("ROLLBACK")
            raise
        except (sqlite3.Error, OverflowError) as error:
            if self._connection.in_transaction: self._connection.execute("ROLLBACK")
            raise ProviderRecoveryStoreError("provider recovery persistence is unavailable") from error


def _validate(connection: sqlite3.Connection) -> None:
    objects = _recovery_objects(connection)
    expected = sorted((("index", "sqlite_autoindex_provider_recovery_health_1", None), ("index", "sqlite_autoindex_provider_recovery_members_1", None), ("index", "sqlite_autoindex_provider_recovery_jobs_1", None), ("table", "provider_recovery_health", _HEALTH), ("table", "provider_recovery_members", _MEMBERS), ("table", "provider_recovery_jobs", _JOBS), ("table", "provider_recovery_events", _EVENTS), ("trigger", "provider_recovery_events_no_delete", _NO_DELETE), ("trigger", "provider_recovery_events_no_update", _NO_UPDATE)))
    if objects != expected or connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
        raise ProviderRecoveryStoreError("provider recovery database is unavailable or incompatible")
    try:
        members = set(connection.execute("SELECT connection,adapter,scope FROM provider_recovery_members").fetchall())
        health_keys = set(connection.execute("SELECT connection,adapter,scope FROM provider_recovery_health").fetchall())
        if members != health_keys:
            raise ProviderRecoveryStoreError("provider recovery database is unavailable or incompatible")
        for row in connection.execute("SELECT connection,adapter,scope,generation,state,attempt,next_eligible_at,claim_token,claim_expires_at FROM provider_recovery_health"):
            ProviderRecovery(*row[:7])
            if row[:3] not in members or ((row[4] == "checking") != (type(row[7]) is str and 1 <= len(row[7]) <= 128 and type(row[8]) is int and row[8] >= 0)):
                raise ProviderRecoveryStoreError("provider recovery database is unavailable or incompatible")
        for row in connection.execute("SELECT connection,adapter,scope,generation,state,attempt,next_eligible_at,occurred_at FROM provider_recovery_events"):
            ProviderRecovery(*row[:7])
            if (row[:3] not in members or type(row[7]) is not int
                    or not 0 <= row[7] <= MAX_TIMESTAMP
                    or (row[4] == "checking" and row[6] is not None)):
                raise ProviderRecoveryStoreError("provider recovery database is unavailable or incompatible")
    except (sqlite3.Error, ProviderRecoveryError, TypeError, OverflowError) as error:
        raise ProviderRecoveryStoreError("provider recovery database is unavailable or incompatible") from error


def _recovery_objects(connection: sqlite3.Connection) -> list[tuple[str, str, str | None]]:
    placeholders = ",".join("?" for _ in _RECOVERY_NAMES)
    return connection.execute(f"SELECT type,name,sql FROM sqlite_master WHERE name IN ({placeholders}) ORDER BY type,name", tuple(_RECOVERY_NAMES)).fetchall()


__all__ = ["ProviderRecoveryJob", "ProviderRecoveryStoreError", "SQLiteProviderRecoveryStore"]
