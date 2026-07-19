"""SQLite persistence for bounded, fenced Hardcover identity work."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from mechanical_librarian.domain.hardcover_identity_work import (
    IdentityWorkClaim,
    IdentityWorkOutcome,
    IdentityWorkReference,
    IdentityWorkState,
    IdentityWorkStatus,
    validate_transition,
)


LEASE_DURATION = timedelta(seconds=15)
MINIMUM_START_LEASE = timedelta(seconds=6)
AGGREGATE_WINDOW = timedelta(seconds=30)
CURRENTNESS_WINDOW = timedelta(hours=24)
MAX_ATTEMPTS = 3


class IdentityWorkPersistenceError(RuntimeError):
    """Value-free durable-work failure."""


class StaleIdentityWorkFence(IdentityWorkPersistenceError):
    """The caller no longer owns the claimed generation."""


class SQLiteHardcoverIdentityWorkRepository:
    """Own atomic generation, lease, attempt, recovery, and publication changes."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        if type(connection) is not sqlite3.Connection:
            raise TypeError("identity work connection is malformed")
        self._connection = connection

    def accept(self, work_key: str, now: datetime) -> IdentityWorkStatus:
        _require_work_key(work_key)
        timestamp = _time(now)
        try:
            with _transaction(self._connection):
                current = self._connection.execute(
                    "SELECT generation,status,current_since,expires_at FROM current_hardcover_identity WHERE work_key=?",
                    (work_key,),
                ).fetchone()
                expired = False
                if current is not None:
                    generation, status, current_since, expires_at = _current_row(current)
                    reference = IdentityWorkReference(work_key, generation)
                    if now < _parse_time(current_since):
                        raise IdentityWorkPersistenceError("identity work chronology is invalid")
                    if status == "matched" and now <= _parse_time(expires_at):
                        return IdentityWorkStatus(IdentityWorkOutcome.MATCHED, reference)
                    if status == "matched":
                        self._connection.execute(
                            "UPDATE current_hardcover_identity SET status='expired' WHERE work_key=? AND status='matched'",
                            (work_key,),
                        )
                        expired = True

                active = self._connection.execute(
                    "SELECT generation,created_at FROM hardcover_identity_generations WHERE work_key=? AND state IN ('pending','claimed','retry_wait')",
                    (work_key,),
                ).fetchone()
                if active is not None:
                    generation = _positive_int(active[0], "generation")
                    if type(active[1]) is not str or now < _parse_time(active[1]):
                        raise IdentityWorkPersistenceError("identity work chronology is invalid")
                    return IdentityWorkStatus(
                        IdentityWorkOutcome.STALE if expired else IdentityWorkOutcome.IN_PROGRESS,
                        IdentityWorkReference(work_key, generation),
                    )

                row = self._connection.execute(
                    "SELECT COALESCE(MAX(generation),0)+1 FROM hardcover_identity_generations WHERE work_key=?",
                    (work_key,),
                ).fetchone()
                generation = _positive_int(row[0] if row else None, "generation")
                deadline = _time(now + AGGREGATE_WINDOW)
                self._connection.execute(
                    "INSERT INTO hardcover_identity_generations(work_key,generation,state,fence,attempt_count,aggregate_started_at,aggregate_deadline,created_at,updated_at) VALUES(?,?,'pending',0,0,?,?,?,?)",
                    (work_key, generation, timestamp, deadline, timestamp, timestamp),
                )
                self._connection.execute(
                    "INSERT INTO hardcover_identity_candidates(work_key,generation,candidate,created_at) VALUES(?,?,1,?)",
                    (work_key, generation, timestamp),
                )
                self._event(work_key, generation, "accepted", None, "pending", 0, timestamp)
                return IdentityWorkStatus(
                    IdentityWorkOutcome.STALE if expired else IdentityWorkOutcome.IN_PROGRESS,
                    IdentityWorkReference(work_key, generation),
                    1,
                )
        except (IdentityWorkPersistenceError, ValueError, TypeError):
            raise
        except (sqlite3.Error, OSError):
            raise IdentityWorkPersistenceError("identity work could not be accepted") from None

    def claim(
        self,
        reference: IdentityWorkReference,
        candidate: int,
        owner: str,
        now: datetime,
        *,
        gate_owned: bool,
    ) -> IdentityWorkClaim:
        _require_reference(reference)
        _candidate(candidate)
        _owner(owner)
        _require_gate(gate_owned)
        timestamp = _time(now)
        lease = _time(now + LEASE_DURATION)
        try:
            with _transaction(self._connection):
                row = self._generation(reference)
                state = _state(row[0])
                if now < _parse_time(str(row[5])):
                    raise IdentityWorkPersistenceError("identity work chronology is invalid")
                if state not in {IdentityWorkState.PENDING, IdentityWorkState.RETRY_WAIT}:
                    raise IdentityWorkPersistenceError("identity work is not claimable")
                fence = _nonnegative_int(row[1], "fence")
                if state is IdentityWorkState.PENDING:
                    fence += 1
                elif self._connection.execute(
                    "SELECT 1 FROM hardcover_identity_claims WHERE work_key=? AND generation=? AND fence=?",
                    (reference.work_key, reference.generation, fence),
                ).fetchone() is not None:
                    fence += 1
                validate_transition(state, IdentityWorkState.CLAIMED)
                self._connection.execute(
                    "INSERT OR IGNORE INTO hardcover_identity_candidates(work_key,generation,candidate,created_at) VALUES(?,?,?,?)",
                    (reference.work_key, reference.generation, candidate, timestamp),
                )
                self._connection.execute(
                    "INSERT INTO hardcover_identity_claims(work_key,generation,fence,candidate,owner,claimed_at,lease_expires_at) VALUES(?,?,?,?,?,?,?)",
                    (reference.work_key, reference.generation, fence, candidate, owner, timestamp, lease),
                )
                changed = self._connection.execute(
                    "UPDATE hardcover_identity_generations SET state='claimed',fence=?,lease_owner=?,lease_expires_at=?,updated_at=? WHERE work_key=? AND generation=? AND state=?",
                    (fence, owner, lease, timestamp, reference.work_key, reference.generation, state.value),
                ).rowcount
                if changed != 1:
                    raise IdentityWorkPersistenceError("identity work claim raced")
                self._event(reference.work_key, reference.generation, "claimed", state.value, "claimed", fence, timestamp)
                return IdentityWorkClaim(reference, candidate, fence, _parse_time(lease))
        except (IdentityWorkPersistenceError, ValueError, TypeError):
            raise
        except (sqlite3.Error, OSError):
            raise IdentityWorkPersistenceError("identity work could not be claimed") from None

    def renew_and_start(
        self,
        claim: IdentityWorkClaim,
        owner: str,
        now: datetime,
        *,
        gate_owned: bool,
    ) -> IdentityWorkClaim:
        _require_claim(claim)
        _owner(owner)
        _require_gate(gate_owned)
        timestamp = _time(now)
        lease = _time(now + LEASE_DURATION)
        try:
            with _transaction(self._connection):
                row = self._generation(claim.reference)
                self._assert_claim(row, claim.fence, owner, now)
                if _parse_time(str(row[4])) - now < MINIMUM_START_LEASE:
                    raise IdentityWorkPersistenceError("identity work lease is too short to start")
                attempts = _nonnegative_int(row[2], "attempt count")
                if attempts >= MAX_ATTEMPTS:
                    raise IdentityWorkPersistenceError("identity work attempts are exhausted")
                attempt = attempts + 1
                self._connection.execute(
                    "INSERT INTO hardcover_identity_attempts(work_key,generation,attempt,fence,candidate,started_at) VALUES(?,?,?,?,?,?)",
                    (claim.reference.work_key, claim.reference.generation, attempt, claim.fence, claim.candidate, timestamp),
                )
                changed = self._connection.execute(
                    "UPDATE hardcover_identity_generations SET attempt_count=?,lease_expires_at=?,updated_at=? WHERE work_key=? AND generation=? AND state='claimed' AND fence=? AND lease_owner=?",
                    (attempt, lease, timestamp, claim.reference.work_key, claim.reference.generation, claim.fence, owner),
                ).rowcount
                if changed != 1:
                    raise StaleIdentityWorkFence("identity work fence is stale")
                self._event(claim.reference.work_key, claim.reference.generation, "started", "claimed", "claimed", claim.fence, timestamp)
                return IdentityWorkClaim(claim.reference, claim.candidate, claim.fence, _parse_time(lease))
        except (IdentityWorkPersistenceError, ValueError, TypeError):
            raise
        except (sqlite3.Error, OSError):
            raise IdentityWorkPersistenceError("identity work could not be started") from None

    def recover_interrupted(
        self,
        claim: IdentityWorkClaim,
        now: datetime,
        *,
        gate_owned: bool,
    ) -> int:
        _require_claim(claim)
        _require_gate(gate_owned)
        timestamp = _time(now)
        try:
            with _transaction(self._connection):
                row = self._generation(claim.reference)
                state = _state(row[0])
                fence = _nonnegative_int(row[1], "fence")
                lease = _parse_time(str(row[4])) if row[4] is not None else None
                if state is not IdentityWorkState.CLAIMED or fence != claim.fence or lease is None:
                    raise StaleIdentityWorkFence("identity work fence is stale")
                if now <= lease:
                    raise IdentityWorkPersistenceError("identity work lease has not expired")
                attempt = _nonnegative_int(row[2], "attempt count")
                changed = self._connection.execute(
                    "UPDATE hardcover_identity_attempts SET resolved_at=?,outcome='interrupted' WHERE work_key=? AND generation=? AND fence=? AND resolved_at IS NULL",
                    (timestamp, claim.reference.work_key, claim.reference.generation, claim.fence),
                ).rowcount
                if changed != 1:
                    raise IdentityWorkPersistenceError("identity work has no started attempt")
                next_candidate = attempt + 1
                if next_candidate > MAX_ATTEMPTS:
                    raise IdentityWorkPersistenceError("identity work attempts are exhausted")
                next_fence = fence + 1
                validate_transition(state, IdentityWorkState.RETRY_WAIT)
                self._connection.execute(
                    "INSERT INTO hardcover_identity_candidates(work_key,generation,candidate,created_at) VALUES(?,?,?,?)",
                    (claim.reference.work_key, claim.reference.generation, next_candidate, timestamp),
                )
                self._connection.execute(
                    "INSERT INTO hardcover_identity_retry_schedules(work_key,generation,fence,candidate,due_at,reason,created_at) VALUES(?,?,?,?,?,'interrupted',?)",
                    (claim.reference.work_key, claim.reference.generation, next_fence, next_candidate, timestamp, timestamp),
                )
                self._connection.execute(
                    "UPDATE hardcover_identity_generations SET state='retry_wait',fence=?,lease_owner=NULL,lease_expires_at=NULL,updated_at=? WHERE work_key=? AND generation=? AND state='claimed' AND fence=?",
                    (next_fence, timestamp, claim.reference.work_key, claim.reference.generation, fence),
                )
                self._event(claim.reference.work_key, claim.reference.generation, "recovered", "claimed", "retry_wait", next_fence, timestamp)
                return next_fence
        except (IdentityWorkPersistenceError, ValueError, TypeError):
            raise
        except (sqlite3.Error, OSError):
            raise IdentityWorkPersistenceError("identity work could not be recovered") from None

    def publish_matched(
        self,
        claim: IdentityWorkClaim,
        owner: str,
        now: datetime,
        observation: object,
        *,
        gate_owned: bool,
        fail_before_commit: bool = False,
    ) -> IdentityWorkStatus:
        _require_claim(claim)
        _owner(owner)
        _require_gate(gate_owned)
        if type(fail_before_commit) is not bool:
            raise TypeError("identity work failure injection is malformed")
        canonical = _canonical_observation(observation)
        observation_id = hashlib.sha256(
            f"{claim.reference.work_key}:{claim.reference.generation}:".encode("ascii")
            + canonical.encode("utf-8")
        ).hexdigest()
        timestamp = _time(now)
        expires = _time(now + CURRENTNESS_WINDOW)
        try:
            with _transaction(self._connection):
                row = self._generation(claim.reference)
                self._assert_claim(row, claim.fence, owner, now)
                attempt = _nonnegative_int(row[2], "attempt count")
                changed = self._connection.execute(
                    "UPDATE hardcover_identity_attempts SET resolved_at=?,outcome='matched' WHERE work_key=? AND generation=? AND attempt=? AND fence=? AND resolved_at IS NULL",
                    (timestamp, claim.reference.work_key, claim.reference.generation, attempt, claim.fence),
                ).rowcount
                if changed != 1:
                    raise IdentityWorkPersistenceError("identity work has no started attempt")
                self._connection.execute(
                    "INSERT INTO hardcover_identity_results(work_key,generation,fence,outcome,published_at) VALUES(?,?,?,'matched',?)",
                    (claim.reference.work_key, claim.reference.generation, claim.fence, timestamp),
                )
                self._connection.execute(
                    "INSERT INTO hardcover_identity_observations(observation_id,work_key,generation,canonical_observation,created_at) VALUES(?,?,?,?,?)",
                    (observation_id, claim.reference.work_key, claim.reference.generation, canonical, timestamp),
                )
                self._connection.execute(
                    "INSERT INTO current_hardcover_identity(work_key,generation,observation_id,status,current_since,expires_at) VALUES(?,?,?,'matched',?,?) ON CONFLICT(work_key) DO UPDATE SET generation=excluded.generation,observation_id=excluded.observation_id,status='matched',current_since=excluded.current_since,expires_at=excluded.expires_at",
                    (claim.reference.work_key, claim.reference.generation, observation_id, timestamp, expires),
                )
                validate_transition(IdentityWorkState.CLAIMED, IdentityWorkState.SUCCEEDED)
                self._connection.execute(
                    "UPDATE hardcover_identity_generations SET state='succeeded',lease_owner=NULL,lease_expires_at=NULL,updated_at=? WHERE work_key=? AND generation=? AND state='claimed' AND fence=?",
                    (timestamp, claim.reference.work_key, claim.reference.generation, claim.fence),
                )
                self._event(claim.reference.work_key, claim.reference.generation, "published", "claimed", "succeeded", claim.fence, timestamp)
                if fail_before_commit:
                    raise IdentityWorkPersistenceError("injected identity publication failure")
                return IdentityWorkStatus(IdentityWorkOutcome.MATCHED, claim.reference, claim.candidate)
        except (IdentityWorkPersistenceError, ValueError, TypeError):
            raise
        except (sqlite3.Error, OSError):
            raise IdentityWorkPersistenceError("identity work could not be published") from None

    def cancel(
        self, reference: IdentityWorkReference, now: datetime, *, gate_owned: bool
    ) -> None:
        _require_reference(reference)
        _require_gate(gate_owned)
        timestamp = _time(now)
        try:
            with _transaction(self._connection):
                row = self._generation(reference)
                state = _state(row[0])
                validate_transition(state, IdentityWorkState.CANCELLED)
                fence = _nonnegative_int(row[1], "fence")
                if now < _parse_time(str(row[5])):
                    raise IdentityWorkPersistenceError("identity work chronology is invalid")
                self._connection.execute(
                    "UPDATE hardcover_identity_attempts SET resolved_at=?,outcome='cancelled' WHERE work_key=? AND generation=? AND resolved_at IS NULL",
                    (timestamp, reference.work_key, reference.generation),
                )
                self._connection.execute(
                    "UPDATE hardcover_identity_generations SET state='cancelled',lease_owner=NULL,lease_expires_at=NULL,updated_at=? WHERE work_key=? AND generation=? AND state=? AND fence=?",
                    (timestamp, reference.work_key, reference.generation, state.value, fence),
                )
                self._connection.execute(
                    "INSERT INTO hardcover_identity_results(work_key,generation,fence,outcome,published_at) VALUES(?,?,?,'cancelled',?)",
                    (reference.work_key, reference.generation, fence, timestamp),
                )
                self._event(reference.work_key, reference.generation, "cancelled", state.value, "cancelled", fence, timestamp)
        except (IdentityWorkPersistenceError, ValueError, TypeError):
            raise
        except (sqlite3.Error, OSError):
            raise IdentityWorkPersistenceError("identity work could not be cancelled") from None

    def _generation(self, reference: IdentityWorkReference) -> tuple[object, ...]:
        row = self._connection.execute(
            "SELECT state,fence,attempt_count,lease_owner,lease_expires_at,updated_at FROM hardcover_identity_generations WHERE work_key=? AND generation=?",
            (reference.work_key, reference.generation),
        ).fetchone()
        if row is None or len(row) != 6:
            raise IdentityWorkPersistenceError("identity work generation is unavailable")
        return row

    def _assert_claim(
        self, row: tuple[object, ...], fence: int, owner: str, now: datetime
    ) -> None:
        if (
            _state(row[0]) is not IdentityWorkState.CLAIMED
            or _nonnegative_int(row[1], "fence") != fence
            or row[3] != owner
            or row[4] is None
            or now < _parse_time(str(row[5]))
            or now > _parse_time(str(row[4]))
        ):
            raise StaleIdentityWorkFence("identity work fence is stale")

    def _event(
        self,
        work_key: str,
        generation: int,
        kind: str,
        before: str | None,
        after: str,
        fence: int,
        timestamp: str,
    ) -> None:
        row = self._connection.execute(
            "SELECT COALESCE(MAX(sequence),0)+1 FROM hardcover_identity_events WHERE work_key=? AND generation=?",
            (work_key, generation),
        ).fetchone()
        sequence = _positive_int(row[0] if row else None, "event sequence")
        self._connection.execute(
            "INSERT INTO hardcover_identity_events(work_key,generation,sequence,kind,from_state,to_state,fence,occurred_at) VALUES(?,?,?,?,?,?,?,?)",
            (work_key, generation, sequence, kind, before, after, fence, timestamp),
        )


def _require_reference(value: object) -> None:
    if type(value) is not IdentityWorkReference:
        raise TypeError("identity work reference is malformed")


def _require_claim(value: object) -> None:
    if type(value) is not IdentityWorkClaim:
        raise TypeError("identity work claim is malformed")


def _require_work_key(value: object) -> None:
    if type(value) is not str or len(value) != 64 or set(value) - set("0123456789abcdef"):
        raise ValueError("identity work key is malformed")


def _candidate(value: object) -> None:
    if type(value) is not int or not 1 <= value <= MAX_ATTEMPTS:
        raise ValueError("identity work candidate is malformed")


def _owner(value: object) -> None:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 64
        or not value[0].islower()
        or set(value) - set("abcdefghijklmnopqrstuvwxyz0123456789_-")
    ):
        raise ValueError("identity work owner is malformed")


def _require_gate(value: object) -> None:
    if value is not True:
        raise IdentityWorkPersistenceError("identity work gate ownership is required")


def _time(value: object) -> str:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("identity work timestamp is malformed")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_time(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        raise IdentityWorkPersistenceError("identity work timestamp is corrupt") from None


def _state(value: object) -> IdentityWorkState:
    try:
        return IdentityWorkState(value)
    except (TypeError, ValueError):
        raise IdentityWorkPersistenceError("identity work state is corrupt") from None


def _positive_int(value: object, label: str) -> int:
    if type(value) is not int or value < 1:
        raise IdentityWorkPersistenceError(f"identity work {label} is corrupt")
    return value


def _nonnegative_int(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise IdentityWorkPersistenceError(f"identity work {label} is corrupt")
    return value


def _current_row(row: tuple[object, ...]) -> tuple[int, str, str, str]:
    if (
        len(row) != 4
        or row[1] not in {"matched", "expired"}
        or type(row[2]) is not str
        or type(row[3]) is not str
    ):
        raise IdentityWorkPersistenceError("current identity work is corrupt")
    return (
        _positive_int(row[0], "generation"),
        str(row[1]),
        str(row[2]),
        str(row[3]),
    )


def _canonical_observation(value: object) -> str:
    if type(value) is not dict or not value or len(value) > 8:
        raise ValueError("identity work observation is malformed")
    try:
        canonical = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        raise ValueError("identity work observation is malformed") from None
    if not 2 <= len(canonical.encode("utf-8")) <= 8192 or json.loads(canonical) != value:
        raise ValueError("identity work observation is malformed")
    return canonical


@contextmanager
def _transaction(connection: sqlite3.Connection) -> Iterator[None]:
    if connection.in_transaction:
        raise sqlite3.OperationalError("nested identity work transaction")
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
        connection.commit()
    except Exception:
        connection.rollback()
        raise


__all__ = [
    "AGGREGATE_WINDOW",
    "CURRENTNESS_WINDOW",
    "IdentityWorkPersistenceError",
    "LEASE_DURATION",
    "MINIMUM_START_LEASE",
    "SQLiteHardcoverIdentityWorkRepository",
    "StaleIdentityWorkFence",
]
