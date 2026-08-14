"""Exact-current SQLite persistence for dormant provider recovery eligibility."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from mechanical_librarian.domain.provider_recovery import ProviderRecovery, ProviderRecoveryError


class ProviderRecoveryStoreError(RuntimeError):
    pass


_HEALTH = """CREATE TABLE provider_recovery_health(connection TEXT NOT NULL,adapter TEXT NOT NULL,scope TEXT NOT NULL,generation INTEGER NOT NULL CHECK(generation>=1),state TEXT NOT NULL CHECK(state IN ('waiting','dormant','checking')),attempt INTEGER NOT NULL CHECK(attempt BETWEEN 0 AND 5),next_eligible_at INTEGER,claim_token TEXT,claim_expires_at INTEGER,PRIMARY KEY(connection,adapter,scope),CHECK((state='waiting')=(next_eligible_at IS NOT NULL)),CHECK((state='checking')=(claim_token IS NOT NULL AND claim_expires_at IS NOT NULL)),CHECK(claim_expires_at IS NULL OR claim_expires_at>=0)) STRICT"""
_MEMBERS = """CREATE TABLE provider_recovery_members(connection TEXT NOT NULL,adapter TEXT NOT NULL,scope TEXT NOT NULL,PRIMARY KEY(connection,adapter,scope)) STRICT"""
_EVENTS = """CREATE TABLE provider_recovery_events(sequence INTEGER PRIMARY KEY,connection TEXT NOT NULL,adapter TEXT NOT NULL,scope TEXT NOT NULL,generation INTEGER NOT NULL CHECK(generation>=1),state TEXT NOT NULL CHECK(state IN ('waiting','dormant','checking')),attempt INTEGER NOT NULL CHECK(attempt BETWEEN 0 AND 5),next_eligible_at INTEGER,occurred_at INTEGER NOT NULL CHECK(occurred_at>=0),CHECK((state='waiting')=(next_eligible_at IS NOT NULL))) STRICT"""
_NO_UPDATE = "CREATE TRIGGER provider_recovery_events_no_update BEFORE UPDATE ON provider_recovery_events BEGIN SELECT RAISE(ABORT, 'append only'); END"
_NO_DELETE = "CREATE TRIGGER provider_recovery_events_no_delete BEFORE DELETE ON provider_recovery_events BEGIN SELECT RAISE(ABORT, 'append only'); END"


class SQLiteProviderRecoveryStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @classmethod
    def open(cls, path: Path) -> "SQLiteProviderRecoveryStore":
        connection = sqlite3.connect(path, isolation_level=None)
        connection.execute("PRAGMA foreign_keys=ON")
        objects = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        if not objects:
            connection.executescript(";".join((_HEALTH, _MEMBERS, _EVENTS, _NO_UPDATE, _NO_DELETE)))
        _validate(connection)
        return cls(connection)

    def close(self) -> None:
        self._connection.close()

    def record(self, recovery: ProviderRecovery, *, occurred_at: int, owner_token: str | None = None) -> None:
        if type(occurred_at) is not int or occurred_at < 0:
            raise ProviderRecoveryStoreError("provider recovery is invalid")
        if owner_token is not None and (type(owner_token) is not str or not 1 <= len(owner_token) <= 128):
            raise ProviderRecoveryStoreError("provider recovery claim is invalid")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            current = self._connection.execute("SELECT generation,state,attempt,next_eligible_at,claim_token,claim_expires_at FROM provider_recovery_health WHERE connection=? AND adapter=? AND scope=?", (recovery.connection, recovery.adapter, recovery.scope)).fetchone()
            if current is not None:
                if current[0] > recovery.generation:
                    raise ProviderRecoveryStoreError("provider recovery generation is stale")
                if current[1] == "checking" and current[5] > occurred_at and current[4] != owner_token:
                    raise ProviderRecoveryStoreError("provider recovery claim is owned")
                if current[0] == recovery.generation and current[:4] != (recovery.generation, recovery.state, recovery.attempt, recovery.next_eligible_at):
                    raise ProviderRecoveryStoreError("provider recovery transition is invalid")
                if current[0] < recovery.generation and current[1] != "dormant":
                    raise ProviderRecoveryStoreError("provider recovery generation is invalid")
            try:
                self._connection.execute("INSERT INTO provider_recovery_members(connection,adapter,scope) VALUES(?,?,?) ON CONFLICT DO NOTHING", (recovery.connection, recovery.adapter, recovery.scope))
                self._connection.execute("INSERT INTO provider_recovery_health(connection,adapter,scope,generation,state,attempt,next_eligible_at,claim_token,claim_expires_at) VALUES(?,?,?,?,?,?,?,NULL,NULL) ON CONFLICT(connection,adapter,scope) DO UPDATE SET generation=excluded.generation,state=excluded.state,attempt=excluded.attempt,next_eligible_at=excluded.next_eligible_at,claim_token=NULL,claim_expires_at=NULL", (recovery.connection, recovery.adapter, recovery.scope, recovery.generation, recovery.state, recovery.attempt, recovery.next_eligible_at))
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

    def claim_one(self, *, generation: int, now: int, token: str, lease_seconds: int = 60) -> ProviderRecovery | None:
        if type(generation) is not int or generation < 1 or type(now) is not int or now < 0 or type(token) is not str or not 1 <= len(token) <= 128 or type(lease_seconds) is not int or not 1 <= lease_seconds <= 300:
            raise ProviderRecoveryStoreError("provider recovery claim is invalid")
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
            rows = self._connection.execute("SELECT connection,adapter,scope,generation,state,attempt,next_eligible_at FROM provider_recovery_health ORDER BY connection,adapter,scope LIMIT ?", (limit,)).fetchall()
            return tuple(ProviderRecovery(*row) for row in rows)
        except (sqlite3.Error, ProviderRecoveryError) as error:
            raise ProviderRecoveryStoreError("provider recovery persistence is unavailable") from error


def _validate(connection: sqlite3.Connection) -> None:
    objects = connection.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
    expected = sorted((("index", "sqlite_autoindex_provider_recovery_health_1", None), ("index", "sqlite_autoindex_provider_recovery_members_1", None), ("table", "provider_recovery_health", _HEALTH), ("table", "provider_recovery_members", _MEMBERS), ("table", "provider_recovery_events", _EVENTS), ("trigger", "provider_recovery_events_no_delete", _NO_DELETE), ("trigger", "provider_recovery_events_no_update", _NO_UPDATE)))
    if objects != expected or connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
        raise ProviderRecoveryStoreError("provider recovery database is unavailable or incompatible")
    try:
        members = set(connection.execute("SELECT connection,adapter,scope FROM provider_recovery_members").fetchall())
        for row in connection.execute("SELECT connection,adapter,scope,generation,state,attempt,next_eligible_at,claim_token,claim_expires_at FROM provider_recovery_health"):
            ProviderRecovery(*row[:7])
            if row[:3] not in members or ((row[4] == "checking") != (type(row[7]) is str and type(row[8]) is int and row[8] >= 0)):
                raise ProviderRecoveryStoreError("provider recovery database is unavailable or incompatible")
        for row in connection.execute("SELECT connection,adapter,scope,generation,state,attempt,next_eligible_at,occurred_at FROM provider_recovery_events"):
            ProviderRecovery(*row[:7])
            if type(row[7]) is not int or row[7] < 0 or (row[4] == "checking" and row[6] is not None):
                raise ProviderRecoveryStoreError("provider recovery database is unavailable or incompatible")
    except (sqlite3.Error, ProviderRecoveryError, TypeError, OverflowError) as error:
        raise ProviderRecoveryStoreError("provider recovery database is unavailable or incompatible") from error


__all__ = ["ProviderRecoveryStoreError", "SQLiteProviderRecoveryStore"]
