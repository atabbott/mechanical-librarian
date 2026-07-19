"""SQLite persistence for bounded, fenced Hardcover identity work."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from mechanical_librarian.application.hardcover_identity import (
    IdentityWorkPersistenceError,
    StaleIdentityWorkFence,
)
from mechanical_librarian.domain.hardcover_identity_work import (
    IdentityWorkClaim,
    IdentityWorkObservation,
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
                    "SELECT generation,observation_id,status,current_since,expires_at FROM current_hardcover_identity WHERE work_key=?",
                    (work_key,),
                ).fetchone()
                expired = False
                if current is not None:
                    generation, observation_id, status, current_since, expires_at = _current_row(current)
                    reference = IdentityWorkReference(work_key, generation)
                    if now < _parse_time(current_since):
                        raise IdentityWorkPersistenceError("identity work chronology is invalid")
                    if status == "matched" and now <= _parse_time(expires_at):
                        self._validate_current_match(
                            reference, observation_id, current_since, expires_at
                        )
                        return IdentityWorkStatus(IdentityWorkOutcome.MATCHED, reference)
                    if status == "matched":
                        self._validate_current_match(
                            reference, observation_id, current_since, expires_at
                        )
                        self._connection.execute(
                            "UPDATE current_hardcover_identity SET status='expired' WHERE work_key=? AND status='matched'",
                            (work_key,),
                        )
                        expired = True
                    elif status == "expired":
                        expired = True

                active = self._connection.execute(
                    "SELECT generation,created_at,state,aggregate_started_at FROM hardcover_identity_generations WHERE work_key=? AND state IN ('pending','claimed','retry_wait')",
                    (work_key,),
                ).fetchone()
                if active is not None:
                    generation = _positive_int(active[0], "generation")
                    if type(active[1]) is not str or now < _parse_time(active[1]):
                        raise IdentityWorkPersistenceError("identity work chronology is invalid")
                    candidate = None
                    if active[2] == "pending" and active[3] is None:
                        candidate = self._reserve_pending(work_key, generation, timestamp, now)
                    return IdentityWorkStatus(
                        IdentityWorkOutcome.STALE if expired else IdentityWorkOutcome.IN_PROGRESS,
                        IdentityWorkReference(work_key, generation),
                        candidate,
                    )

                row = self._connection.execute(
                    "SELECT COALESCE(MAX(generation),0)+1 FROM hardcover_identity_generations WHERE work_key=?",
                    (work_key,),
                ).fetchone()
                generation = _positive_int(row[0] if row else None, "generation")
                if expired:
                    self._connection.execute(
                        "INSERT INTO hardcover_identity_generations(work_key,generation,state,fence,attempt_count,aggregate_started_at,aggregate_deadline,created_at,updated_at) VALUES(?,?,'pending',0,0,NULL,NULL,?,?)",
                        (work_key, generation, timestamp, timestamp),
                    )
                    self._event(work_key, generation, "expired", None, "pending", 0, timestamp)
                    candidate = None
                else:
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
                    candidate = 1
                return IdentityWorkStatus(
                    IdentityWorkOutcome.STALE if expired else IdentityWorkOutcome.IN_PROGRESS,
                    IdentityWorkReference(work_key, generation),
                    candidate,
                )
        except (IdentityWorkPersistenceError, ValueError, TypeError):
            raise
        except (sqlite3.Error, OSError):
            raise IdentityWorkPersistenceError("identity work could not be accepted") from None

    def claim(
        self,
        reference: IdentityWorkReference,
        owner: str,
        now: datetime,
        *,
        gate_owned: bool,
    ) -> IdentityWorkClaim:
        _require_reference(reference)
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
                    if row[6] is None:
                        self._reserve_pending(
                            reference.work_key, reference.generation, timestamp, now
                        )
                        row = self._generation(reference)
                    fence += 1
                    candidates = self._connection.execute(
                        "SELECT candidate FROM hardcover_identity_candidates WHERE work_key=? AND generation=? ORDER BY candidate",
                        (reference.work_key, reference.generation),
                    ).fetchall()
                    if candidates != [(1,)]:
                        raise IdentityWorkPersistenceError("identity work candidate is corrupt")
                    candidate = 1
                else:
                    candidate = self._retry_candidate(reference, fence, now, row)
                    if self._connection.execute(
                        "SELECT 1 FROM hardcover_identity_claims WHERE work_key=? AND generation=? AND fence=?",
                        (reference.work_key, reference.generation, fence),
                    ).fetchone() is not None:
                        raise IdentityWorkPersistenceError("identity work retry was already claimed")
                validate_transition(state, IdentityWorkState.CLAIMED)
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
    ) -> IdentityWorkClaim | IdentityWorkStatus:
        _require_claim(claim)
        _owner(owner)
        _require_gate(gate_owned)
        timestamp = _time(now)
        lease = _time(now + LEASE_DURATION)
        exhausted = False
        try:
            with _transaction(self._connection):
                row = self._generation(claim.reference)
                self._assert_claim(row, claim, owner, now, lease="live")
                if row[7] is None or _parse_time(str(row[7])) - now < MINIMUM_START_LEASE:
                    self._terminal_failure(
                        claim.reference, claim.fence, timestamp, resolve_attempt=False
                    )
                    exhausted = True
                if exhausted:
                    started = claim
                else:
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
                    started = IdentityWorkClaim(claim.reference, claim.candidate, claim.fence, _parse_time(lease))
            if exhausted:
                return IdentityWorkStatus(
                    IdentityWorkOutcome.RETRY_EXHAUSTED,
                    claim.reference,
                    claim.candidate,
                )
            return started
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
    ) -> int | IdentityWorkStatus:
        _require_claim(claim)
        _require_gate(gate_owned)
        timestamp = _time(now)
        try:
            with _transaction(self._connection):
                row = self._generation(claim.reference)
                self._assert_claim(row, claim, None, now, lease="expired")
                state = IdentityWorkState.CLAIMED
                fence = claim.fence
                attempt = _nonnegative_int(row[2], "attempt count")
                unresolved = self._connection.execute(
                    "SELECT attempt,candidate FROM hardcover_identity_attempts WHERE work_key=? AND generation=? AND fence=? AND resolved_at IS NULL",
                    (claim.reference.work_key, claim.reference.generation, claim.fence),
                ).fetchall()
                if len(unresolved) > 1:
                    raise IdentityWorkPersistenceError("identity work attempts are corrupt")
                if unresolved:
                    if unresolved != [(attempt, claim.candidate)]:
                        raise IdentityWorkPersistenceError("identity work attempt is corrupt")
                    self._connection.execute(
                        "UPDATE hardcover_identity_attempts SET resolved_at=?,outcome='interrupted' WHERE work_key=? AND generation=? AND fence=? AND resolved_at IS NULL",
                        (timestamp, claim.reference.work_key, claim.reference.generation, claim.fence),
                    )
                    next_candidate = attempt + 1
                else:
                    next_candidate = claim.candidate
                deadline = _parse_time(str(row[7])) if row[7] is not None else None
                if next_candidate > MAX_ATTEMPTS or deadline is None or deadline - now < MINIMUM_START_LEASE:
                    self._terminal_failure(
                        claim.reference, claim.fence, timestamp, resolve_attempt=False
                    )
                    return IdentityWorkStatus(
                        IdentityWorkOutcome.RETRY_EXHAUSTED,
                        claim.reference,
                        claim.candidate,
                    )
                next_fence = fence + 1
                validate_transition(state, IdentityWorkState.RETRY_WAIT)
                self._connection.execute(
                    "INSERT OR IGNORE INTO hardcover_identity_candidates(work_key,generation,candidate,created_at) VALUES(?,?,?,?)",
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
        observation: IdentityWorkObservation,
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
                self._assert_claim(row, claim, owner, now, lease="live")
                deadline = _parse_time(str(row[7])) if row[7] is not None else None
                if deadline is None or now > deadline:
                    self._terminal_failure(
                        claim.reference, claim.fence, timestamp, resolve_attempt=True
                    )
                    return IdentityWorkStatus(
                        IdentityWorkOutcome.PERSISTENCE_FAILURE, claim.reference
                    )
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

    def publish_failed(
        self,
        claim: IdentityWorkClaim,
        owner: str,
        now: datetime,
        *,
        retryable: bool,
        gate_owned: bool,
        fail_before_commit: bool = False,
    ) -> IdentityWorkStatus:
        """Finalize one started attempt and atomically retry or fail the generation."""

        _require_claim(claim)
        _owner(owner)
        _require_gate(gate_owned)
        if type(retryable) is not bool or type(fail_before_commit) is not bool:
            raise TypeError("identity work failure publication is malformed")
        timestamp = _time(now)
        try:
            with _transaction(self._connection):
                row = self._generation(claim.reference)
                self._assert_claim(row, claim, owner, now, lease="live")
                attempt = _nonnegative_int(row[2], "attempt count")
                changed = self._connection.execute(
                    "UPDATE hardcover_identity_attempts SET resolved_at=?,outcome='failed' WHERE work_key=? AND generation=? AND attempt=? AND fence=? AND resolved_at IS NULL",
                    (timestamp, claim.reference.work_key, claim.reference.generation, attempt, claim.fence),
                ).rowcount
                if changed != 1:
                    raise IdentityWorkPersistenceError("identity work has no started attempt")
                deadline = _parse_time(str(row[7])) if row[7] is not None else None
                if retryable and attempt < MAX_ATTEMPTS and deadline is not None and deadline - now >= MINIMUM_START_LEASE:
                    candidate = attempt + 1
                    next_fence = claim.fence + 1
                    self._connection.execute(
                        "INSERT INTO hardcover_identity_candidates(work_key,generation,candidate,created_at) VALUES(?,?,?,?)",
                        (claim.reference.work_key, claim.reference.generation, candidate, timestamp),
                    )
                    self._connection.execute(
                        "INSERT INTO hardcover_identity_retry_schedules(work_key,generation,fence,candidate,due_at,reason,created_at) VALUES(?,?,?,?,?,'retryable',?)",
                        (claim.reference.work_key, claim.reference.generation, next_fence, candidate, timestamp, timestamp),
                    )
                    changed = self._connection.execute(
                        "UPDATE hardcover_identity_generations SET state='retry_wait',fence=?,lease_owner=NULL,lease_expires_at=NULL,updated_at=? WHERE work_key=? AND generation=? AND state='claimed' AND fence=? AND lease_owner=?",
                        (next_fence, timestamp, claim.reference.work_key, claim.reference.generation, claim.fence, owner),
                    ).rowcount
                    if changed != 1:
                        raise StaleIdentityWorkFence("identity work fence is stale")
                    self._event(claim.reference.work_key, claim.reference.generation, "retry_scheduled", "claimed", "retry_wait", next_fence, timestamp)
                    result = IdentityWorkStatus(
                        IdentityWorkOutcome.IN_PROGRESS, claim.reference, candidate
                    )
                else:
                    self._terminal_failure(
                        claim.reference, claim.fence, timestamp, resolve_attempt=False
                    )
                    result = IdentityWorkStatus(
                        IdentityWorkOutcome.PERSISTENCE_FAILURE, claim.reference
                    )
                if fail_before_commit:
                    raise IdentityWorkPersistenceError("injected identity failure publication")
                return result
        except (IdentityWorkPersistenceError, ValueError, TypeError):
            raise
        except (sqlite3.Error, OSError):
            raise IdentityWorkPersistenceError("identity work failure could not be published") from None

    def cancel(
        self,
        claim: IdentityWorkClaim,
        owner: str,
        now: datetime,
        *,
        gate_owned: bool,
    ) -> None:
        _require_claim(claim)
        _owner(owner)
        _require_gate(gate_owned)
        timestamp = _time(now)
        try:
            with _transaction(self._connection):
                row = self._generation(claim.reference)
                state = _state(row[0])
                validate_transition(state, IdentityWorkState.CANCELLED)
                self._assert_claim(row, claim, owner, now, lease="current")
                fence = claim.fence
                self._connection.execute(
                    "UPDATE hardcover_identity_attempts SET resolved_at=?,outcome='cancelled' WHERE work_key=? AND generation=? AND resolved_at IS NULL",
                    (timestamp, claim.reference.work_key, claim.reference.generation),
                )
                changed = self._connection.execute(
                    "UPDATE hardcover_identity_generations SET state='cancelled',lease_owner=NULL,lease_expires_at=NULL,updated_at=? WHERE work_key=? AND generation=? AND state='claimed' AND fence=? AND lease_owner=?",
                    (timestamp, claim.reference.work_key, claim.reference.generation, fence, owner),
                ).rowcount
                if changed != 1:
                    raise StaleIdentityWorkFence("identity work fence is stale")
                self._connection.execute(
                    "INSERT INTO hardcover_identity_results(work_key,generation,fence,outcome,published_at) VALUES(?,?,?,'cancelled',?)",
                    (claim.reference.work_key, claim.reference.generation, fence, timestamp),
                )
                self._event(claim.reference.work_key, claim.reference.generation, "cancelled", state.value, "cancelled", fence, timestamp)
        except (IdentityWorkPersistenceError, ValueError, TypeError):
            raise
        except (sqlite3.Error, OSError):
            raise IdentityWorkPersistenceError("identity work could not be cancelled") from None

    def _generation(self, reference: IdentityWorkReference) -> tuple[object, ...]:
        row = self._connection.execute(
            "SELECT state,fence,attempt_count,lease_owner,lease_expires_at,updated_at,aggregate_started_at,aggregate_deadline FROM hardcover_identity_generations WHERE work_key=? AND generation=?",
            (reference.work_key, reference.generation),
        ).fetchone()
        if row is None or len(row) != 8:
            raise IdentityWorkPersistenceError("identity work generation is unavailable")
        return row

    def _retry_candidate(
        self,
        reference: IdentityWorkReference,
        fence: int,
        now: datetime,
        generation_row: tuple[object, ...],
    ) -> int:
        schedules = self._connection.execute(
            "SELECT fence,candidate,due_at,reason,created_at FROM hardcover_identity_retry_schedules "
            "WHERE work_key=? AND generation=? AND fence>=? ORDER BY fence",
            (reference.work_key, reference.generation, fence),
        ).fetchall()
        if len(schedules) != 1 or schedules[0][0] != fence:
            raise IdentityWorkPersistenceError("identity work retry schedule is unavailable")
        _, raw_candidate, raw_due, reason, raw_created = schedules[0]
        candidate = _positive_int(raw_candidate, "candidate")
        if reason not in {"interrupted", "retryable"}:
            raise IdentityWorkPersistenceError("identity work retry schedule is corrupt")
        if type(raw_due) is not str or type(raw_created) is not str:
            raise IdentityWorkPersistenceError("identity work retry schedule is corrupt")
        due = _parse_time(raw_due)
        created = _parse_time(raw_created)
        if created > due or generation_row[5] != raw_created:
            raise IdentityWorkPersistenceError("identity work retry schedule is corrupt")
        if now < due:
            raise IdentityWorkPersistenceError("identity work retry is not due")
        candidate_rows = self._connection.execute(
            "SELECT created_at FROM hardcover_identity_candidates WHERE work_key=? AND generation=? AND candidate=?",
            (reference.work_key, reference.generation, candidate),
        ).fetchall()
        if (
            len(candidate_rows) != 1
            or type(candidate_rows[0][0]) is not str
            or _parse_time(candidate_rows[0][0]) > created
        ):
            raise IdentityWorkPersistenceError("identity work retry candidate is corrupt")
        previous_fence = fence - 1
        if previous_fence < 1:
            raise IdentityWorkPersistenceError("identity work retry fence is corrupt")
        claims = self._connection.execute(
            "SELECT candidate,claimed_at FROM hardcover_identity_claims WHERE work_key=? AND generation=? AND fence=?",
            (reference.work_key, reference.generation, previous_fence),
        ).fetchall()
        if (
            len(claims) != 1
            or type(claims[0][1]) is not str
            or _parse_time(claims[0][1]) > created
        ):
            raise IdentityWorkPersistenceError("identity work retry history is corrupt")
        previous_candidate = _positive_int(claims[0][0], "claim candidate")
        attempts = self._connection.execute(
            "SELECT attempt,candidate,resolved_at,outcome FROM hardcover_identity_attempts WHERE work_key=? AND generation=? AND fence=?",
            (reference.work_key, reference.generation, previous_fence),
        ).fetchall()
        all_attempts = self._connection.execute(
            "SELECT COUNT(*) FROM hardcover_identity_attempts WHERE work_key=? AND generation=?",
            (reference.work_key, reference.generation),
        ).fetchone()
        if (
            all_attempts is None
            or all_attempts != (_nonnegative_int(generation_row[2], "attempt count"),)
            or self._connection.execute(
                "SELECT COUNT(*) FROM hardcover_identity_results WHERE work_key=? AND generation=?",
                (reference.work_key, reference.generation),
            ).fetchone()
            != (0,)
        ):
            raise IdentityWorkPersistenceError("identity work retry history is corrupt")
        events = self._connection.execute(
            "SELECT kind,from_state,to_state,occurred_at FROM hardcover_identity_events "
            "WHERE work_key=? AND generation=? AND fence=? AND kind IN ('recovered','retry_scheduled')",
            (reference.work_key, reference.generation, fence),
        ).fetchall()
        expected_event = (
            "recovered" if reason == "interrupted" else "retry_scheduled",
            "claimed",
            "retry_wait",
            raw_created,
        )
        if events != [expected_event]:
            raise IdentityWorkPersistenceError("identity work retry history is corrupt")
        if attempts:
            if len(attempts) != 1:
                raise IdentityWorkPersistenceError("identity work retry history is corrupt")
            attempt, attempt_candidate, resolved_at, outcome = attempts[0]
            if (
                _positive_int(attempt, "attempt") + 1 != candidate
                or attempt_candidate != previous_candidate
                or type(resolved_at) is not str
                or _parse_time(resolved_at) != created
                or outcome != ("interrupted" if reason == "interrupted" else "failed")
            ):
                raise IdentityWorkPersistenceError("identity work retry history is corrupt")
        elif reason != "interrupted" or candidate != previous_candidate:
            raise IdentityWorkPersistenceError("identity work retry history is corrupt")
        return candidate

    def _validate_current_match(
        self,
        reference: IdentityWorkReference,
        observation_id: str,
        current_since: str,
        expires_at: str,
    ) -> None:
        rows = self._connection.execute(
            "SELECT g.state,g.fence,g.updated_at,r.outcome,r.fence,r.published_at,o.observation_id,o.canonical_observation,o.created_at "
            "FROM hardcover_identity_generations g "
            "JOIN hardcover_identity_results r ON r.work_key=g.work_key AND r.generation=g.generation "
            "JOIN hardcover_identity_observations o ON o.work_key=r.work_key AND o.generation=r.generation "
            "WHERE g.work_key=? AND g.generation=?",
            (reference.work_key, reference.generation),
        ).fetchall()
        if len(rows) != 1:
            raise IdentityWorkPersistenceError("current identity work is inconsistent")
        row = rows[0]
        canonical = row[7]
        if type(canonical) is not str:
            raise IdentityWorkPersistenceError("current identity work is inconsistent")
        try:
            IdentityWorkObservation.from_canonical_json(canonical)
        except ValueError:
            raise IdentityWorkPersistenceError("current identity work is inconsistent") from None
        expected_id = hashlib.sha256(
            f"{reference.work_key}:{reference.generation}:".encode("ascii")
            + canonical.encode("utf-8")
        ).hexdigest()
        terminal_updated = row[2]
        published = row[5]
        try:
            terminal_time = _parse_time(str(terminal_updated))
            published_time = _parse_time(str(published))
            observed_time = _parse_time(str(row[8]))
            current_time = _parse_time(current_since)
            expiry_time = _parse_time(expires_at)
        except IdentityWorkPersistenceError:
            raise IdentityWorkPersistenceError("current identity work is inconsistent") from None
        if (
            row[0] != "succeeded"
            or _positive_int(row[1], "fence") != _positive_int(row[4], "result fence")
            or row[3] != "matched"
            or row[6] != observation_id
            or observation_id != expected_id
            or terminal_time != published_time
            or published_time != observed_time
            or observed_time != current_time
            or expiry_time != published_time + CURRENTNESS_WINDOW
        ):
            raise IdentityWorkPersistenceError("current identity work is inconsistent")

    def _reserve_pending(
        self, work_key: str, generation: int, timestamp: str, now: datetime
    ) -> int:
        deadline = _time(now + AGGREGATE_WINDOW)
        changed = self._connection.execute(
            "UPDATE hardcover_identity_generations SET aggregate_started_at=?,aggregate_deadline=?,updated_at=? WHERE work_key=? AND generation=? AND state='pending' AND aggregate_started_at IS NULL AND aggregate_deadline IS NULL",
            (timestamp, deadline, timestamp, work_key, generation),
        ).rowcount
        if changed != 1:
            raise IdentityWorkPersistenceError("identity work reservation raced")
        self._connection.execute(
            "INSERT INTO hardcover_identity_candidates(work_key,generation,candidate,created_at) VALUES(?,?,1,?)",
            (work_key, generation, timestamp),
        )
        self._event(work_key, generation, "accepted", "pending", "pending", 0, timestamp)
        return 1

    def _terminal_failure(
        self,
        reference: IdentityWorkReference,
        fence: int,
        timestamp: str,
        *,
        resolve_attempt: bool,
    ) -> None:
        if resolve_attempt:
            changed = self._connection.execute(
                "UPDATE hardcover_identity_attempts SET resolved_at=?,outcome='failed' WHERE work_key=? AND generation=? AND fence=? AND resolved_at IS NULL",
                (timestamp, reference.work_key, reference.generation, fence),
            ).rowcount
            if changed != 1:
                raise IdentityWorkPersistenceError("identity work has no started attempt")
        validate_transition(IdentityWorkState.CLAIMED, IdentityWorkState.FAILED)
        changed = self._connection.execute(
            "UPDATE hardcover_identity_generations SET state='failed',lease_owner=NULL,lease_expires_at=NULL,updated_at=? WHERE work_key=? AND generation=? AND state='claimed' AND fence=?",
            (timestamp, reference.work_key, reference.generation, fence),
        ).rowcount
        if changed != 1:
            raise StaleIdentityWorkFence("identity work fence is stale")
        self._connection.execute(
            "INSERT INTO hardcover_identity_results(work_key,generation,fence,outcome,published_at) VALUES(?,?,?,'failed',?)",
            (reference.work_key, reference.generation, fence, timestamp),
        )
        self._event(
            reference.work_key,
            reference.generation,
            "failed",
            "claimed",
            "failed",
            fence,
            timestamp,
        )

    def _assert_claim(
        self,
        row: tuple[object, ...],
        claim: IdentityWorkClaim,
        owner: str | None,
        now: datetime,
        *,
        lease: str,
    ) -> None:
        persisted = self._connection.execute(
            "SELECT candidate,owner,claimed_at,lease_expires_at FROM hardcover_identity_claims "
            "WHERE work_key=? AND generation=? AND fence=?",
            (claim.reference.work_key, claim.reference.generation, claim.fence),
        ).fetchall()
        if len(persisted) != 1:
            raise StaleIdentityWorkFence("identity work fence is stale")
        persisted_candidate, persisted_owner, raw_claimed, raw_initial_lease = persisted[0]
        try:
            claimed_at = _parse_time(str(raw_claimed))
            initial_lease = _parse_time(str(raw_initial_lease))
            current_lease = _parse_time(str(row[4]))
        except IdentityWorkPersistenceError:
            raise StaleIdentityWorkFence("identity work fence is stale") from None
        attempts = self._connection.execute(
            "SELECT started_at,resolved_at,outcome FROM hardcover_identity_attempts "
            "WHERE work_key=? AND generation=? AND fence=?",
            (claim.reference.work_key, claim.reference.generation, claim.fence),
        ).fetchall()
        if not attempts:
            coherent_current_lease = current_lease == initial_lease
        elif len(attempts) == 1:
            started_at, resolved_at, outcome = attempts[0]
            try:
                expected_lease = _parse_time(str(started_at)) + LEASE_DURATION
            except IdentityWorkPersistenceError:
                raise StaleIdentityWorkFence("identity work fence is stale") from None
            coherent_current_lease = (
                resolved_at is None
                and outcome is None
                and current_lease == expected_lease
            )
        else:
            coherent_current_lease = False
        expected_owner = persisted_owner if owner is None else owner
        if (
            _state(row[0]) is not IdentityWorkState.CLAIMED
            or _nonnegative_int(row[1], "fence") != claim.fence
            or persisted_candidate != claim.candidate
            or persisted_owner != expected_owner
            or row[3] != persisted_owner
            or claim.lease_expires_at != current_lease
            or claimed_at > initial_lease
            or initial_lease > current_lease
            or not coherent_current_lease
            or now < _parse_time(str(row[5]))
        ):
            raise StaleIdentityWorkFence("identity work fence is stale")
        if lease == "live" and now > current_lease:
            raise StaleIdentityWorkFence("identity work fence is stale")
        if lease == "expired" and now <= current_lease:
            raise IdentityWorkPersistenceError("identity work lease has not expired")
        if lease not in {"live", "expired", "current"}:
            raise TypeError("identity work lease assertion is malformed")

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


def _current_row(row: tuple[object, ...]) -> tuple[int, str, str, str, str]:
    if (
        len(row) != 5
        or type(row[1]) is not str
        or len(row[1]) != 64
        or set(row[1]) - set("0123456789abcdef")
        or row[2] not in {"matched", "expired"}
        or type(row[3]) is not str
        or type(row[4]) is not str
    ):
        raise IdentityWorkPersistenceError("current identity work is corrupt")
    return (
        _positive_int(row[0], "generation"),
        str(row[1]),
        str(row[2]),
        str(row[3]),
        str(row[4]),
    )


def _canonical_observation(value: object) -> str:
    if type(value) is not IdentityWorkObservation:
        raise TypeError("identity work observation is malformed")
    return value.to_canonical_json()


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
