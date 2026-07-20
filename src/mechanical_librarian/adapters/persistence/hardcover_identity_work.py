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
    IdentityWorkReadiness,
    IdentityWorkObservation,
    IdentityWorkOutcome,
    IdentityWorkReference,
    IdentityWorkRetryToken,
    IdentityWorkState,
    IdentityWorkStatus,
    TERMINAL_STATES,
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

    def accept(
        self,
        work_key: str,
        now: datetime,
        *,
        egress_limit: int = MAX_ATTEMPTS,
    ) -> IdentityWorkStatus:
        _require_work_key(work_key)
        _require_egress_limit(egress_limit)
        timestamp = _time(now)
        try:
            with _transaction(self._connection):
                current = self._connection.execute(
                    "SELECT generation,observation_id,status,current_since,expires_at FROM current_hardcover_identity WHERE work_key=?",
                    (work_key,),
                ).fetchone()
                expired = False
                report_stale = False
                expired_generation: int | None = None
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
                        report_stale = True
                        expired_generation = generation
                    elif status == "expired":
                        expired = True
                        expired_generation = generation

                active = self._connection.execute(
                    "SELECT g.generation,g.created_at,g.state,g.aggregate_started_at,"
                    "p.max_starts,g.attempt_count "
                    "FROM hardcover_identity_generations AS g "
                    "LEFT JOIN hardcover_identity_egress_policies AS p "
                    "ON p.work_key=g.work_key AND p.generation=g.generation "
                    "WHERE g.work_key=? AND g.state IN ('pending','claimed','retry_wait')",
                    (work_key,),
                ).fetchone()
                if active is not None:
                    active_generation = _positive_int(active[0], "generation")
                    if type(active[1]) is not str or now < _parse_time(active[1]):
                        raise IdentityWorkPersistenceError("identity work chronology is invalid")
                    max_starts = (
                        None
                        if active[4] is None
                        else _positive_int(active[4], "egress limit")
                    )
                    attempts = _nonnegative_int(active[5], "attempt count")
                    if egress_limit == MAX_ATTEMPTS or (
                        max_starts == 1 and attempts >= 1
                    ):
                        candidate = None
                        if active[2] == "pending" and active[3] is None:
                            candidate = self._reserve_pending(
                                work_key, active_generation, timestamp, now
                            )
                        return IdentityWorkStatus(
                            IdentityWorkOutcome.STALE
                            if report_stale
                            else IdentityWorkOutcome.IN_PROGRESS,
                            IdentityWorkReference(work_key, active_generation),
                            candidate,
                        )

                one_shot = self._connection.execute(
                    "SELECT g.generation,g.state,g.fence,g.attempt_count,g.updated_at "
                    "FROM hardcover_identity_generations AS g "
                    "JOIN hardcover_identity_egress_policies AS p "
                    "ON p.work_key=g.work_key AND p.generation=g.generation "
                    "WHERE g.work_key=? AND p.max_starts=1 AND g.attempt_count>=1 "
                    "ORDER BY g.generation DESC LIMIT 1",
                    (work_key,),
                ).fetchone()
                if one_shot is not None:
                    generation = _positive_int(one_shot[0], "generation")
                    state = _state(one_shot[1])
                    fence = _positive_int(one_shot[2], "fence")
                    attempts = _positive_int(one_shot[3], "attempt count")
                    if (
                        state not in TERMINAL_STATES
                        or attempts != 1
                        or type(one_shot[4]) is not str
                        or now < _parse_time(one_shot[4])
                    ):
                        raise IdentityWorkPersistenceError(
                            "identity work one-shot authority is inconsistent"
                        )
                    outcome = {
                        IdentityWorkState.SUCCEEDED: "matched",
                        IdentityWorkState.FAILED: "failed",
                        IdentityWorkState.CANCELLED: "cancelled",
                    }[state]
                    result = self._connection.execute(
                        "SELECT fence,outcome,published_at FROM hardcover_identity_results "
                        "WHERE work_key=? AND generation=?",
                        (work_key, generation),
                    ).fetchall()
                    if result != [(fence, outcome, one_shot[4])]:
                        raise IdentityWorkPersistenceError(
                            "identity work one-shot authority is inconsistent"
                        )
                    if egress_limit == 1 or not (
                        state is IdentityWorkState.SUCCEEDED
                        and expired_generation is not None
                    ):
                        return IdentityWorkStatus(
                            IdentityWorkOutcome.RETRY_EXHAUSTED,
                            IdentityWorkReference(work_key, generation),
                        )

                if active is not None:
                    candidate = None
                    if active[2] == "pending" and active[3] is None:
                        candidate = self._reserve_pending(
                            work_key, active_generation, timestamp, now
                        )
                    return IdentityWorkStatus(
                        IdentityWorkOutcome.STALE
                        if report_stale
                        else IdentityWorkOutcome.IN_PROGRESS,
                        IdentityWorkReference(work_key, active_generation),
                        candidate,
                    )

                row = self._connection.execute(
                    "SELECT COALESCE(MAX(generation),0)+1 FROM hardcover_identity_generations WHERE work_key=?",
                    (work_key,),
                ).fetchone()
                generation = _positive_int(row[0] if row else None, "generation")
                if expired:
                    report_stale = True
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
                    IdentityWorkOutcome.STALE
                    if report_stale
                    else IdentityWorkOutcome.IN_PROGRESS,
                    IdentityWorkReference(work_key, generation),
                    candidate,
                )
        except (IdentityWorkPersistenceError, ValueError, TypeError):
            raise
        except (sqlite3.Error, OSError):
            raise IdentityWorkPersistenceError("identity work could not be accepted") from None

    def inspect(
        self, reference: IdentityWorkReference, now: datetime
    ) -> IdentityWorkReadiness:
        """Read one exact generation without changing its lifecycle state."""

        _require_reference(reference)
        _time(now)
        try:
            with _transaction(self._connection):
                row = self._generation(reference)
                state = _state(row[0])
                if type(row[5]) is not str or now < _parse_time(row[5]):
                    raise IdentityWorkPersistenceError(
                        "identity work chronology is invalid"
                    )
                if type(row[7]) is not str:
                    raise IdentityWorkPersistenceError(
                        "identity work aggregate deadline is unavailable"
                    )
                deadline = _parse_time(row[7])
                fence = _nonnegative_int(row[1], "fence")
                attempts = _nonnegative_int(row[2], "attempt count")
                if state is IdentityWorkState.SUCCEEDED:
                    current_rows = self._connection.execute(
                        "SELECT generation,observation_id,status,current_since,expires_at "
                        "FROM current_hardcover_identity WHERE work_key=?",
                        (reference.work_key,),
                    ).fetchall()
                    if len(current_rows) != 1:
                        raise IdentityWorkPersistenceError(
                            "current identity work is inconsistent"
                        )
                    (
                        current_generation,
                        observation_id,
                        status,
                        current_since,
                        expires_at,
                    ) = _current_row(current_rows[0])
                    if (
                        current_generation != reference.generation
                        or status != "matched"
                        or now > _parse_time(expires_at)
                    ):
                        raise IdentityWorkPersistenceError(
                            "current identity work is inconsistent"
                        )
                    self._validate_current_match(
                        reference,
                        observation_id,
                        current_since,
                        expires_at,
                    )
                    return IdentityWorkReadiness(reference, state, deadline)
                if state in TERMINAL_STATES:
                    results = self._connection.execute(
                        "SELECT outcome,fence,published_at "
                        "FROM hardcover_identity_results "
                        "WHERE work_key=? AND generation=?",
                        (reference.work_key, reference.generation),
                    ).fetchall()
                    expected = {
                        IdentityWorkState.SUCCEEDED: "matched",
                        IdentityWorkState.FAILED: "failed",
                        IdentityWorkState.CANCELLED: "cancelled",
                    }[state]
                    if (
                        len(results) != 1
                        or results[0][0] != expected
                        or _positive_int(results[0][1], "result fence") != fence
                        or type(results[0][2]) is not str
                        or _parse_time(results[0][2]) != _parse_time(row[5])
                    ):
                        raise IdentityWorkPersistenceError(
                            "identity work terminal result is inconsistent"
                        )
                    return IdentityWorkReadiness(reference, state, deadline)
                if state is IdentityWorkState.PENDING:
                    candidates = self._connection.execute(
                        "SELECT candidate FROM hardcover_identity_candidates WHERE work_key=? AND generation=? ORDER BY candidate",
                        (reference.work_key, reference.generation),
                    ).fetchall()
                    if candidates != [(1,)]:
                        raise IdentityWorkPersistenceError(
                            "identity work candidate is unavailable"
                        )
                    return IdentityWorkReadiness(reference, state, deadline)
                if state is IdentityWorkState.RETRY_WAIT:
                    schedule = self._connection.execute(
                        "SELECT candidate,due_at FROM hardcover_identity_retry_schedules WHERE work_key=? AND generation=? AND fence=?",
                        (reference.work_key, reference.generation, fence),
                    ).fetchall()
                    if len(schedule) != 1 or type(schedule[0][1]) is not str:
                        raise IdentityWorkPersistenceError(
                            "identity work retry schedule is unavailable"
                        )
                    candidate = _positive_int(schedule[0][0], "candidate")
                    due = _parse_time(schedule[0][1])
                    if candidate != attempts + 1 or due > deadline:
                        raise IdentityWorkPersistenceError(
                            "identity work retry schedule is corrupt"
                        )
                    return IdentityWorkReadiness(
                        reference,
                        state,
                        deadline,
                        retry_token=IdentityWorkRetryToken(
                            reference,
                            candidate,
                            fence,
                            due,
                        ),
                    )
                claim_rows = self._connection.execute(
                    "SELECT candidate,lease_expires_at FROM hardcover_identity_claims WHERE work_key=? AND generation=? AND fence=?",
                    (reference.work_key, reference.generation, fence),
                ).fetchall()
                if len(claim_rows) != 1 or type(claim_rows[0][1]) is not str:
                    raise IdentityWorkPersistenceError(
                        "identity work claim is unavailable"
                    )
                candidate = _positive_int(claim_rows[0][0], "candidate")
                active_claim = IdentityWorkClaim(
                    reference, candidate, fence, _parse_time(claim_rows[0][1])
                )
                return IdentityWorkReadiness(
                    reference,
                    state,
                    deadline,
                    active_claim=active_claim,
                )
        except (IdentityWorkPersistenceError, ValueError, TypeError):
            raise
        except (sqlite3.Error, OSError):
            raise IdentityWorkPersistenceError(
                "identity work could not be inspected"
            ) from None

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
        egress_limit: int = MAX_ATTEMPTS,
    ) -> IdentityWorkClaim | IdentityWorkStatus:
        _require_claim(claim)
        _owner(owner)
        _require_gate(gate_owned)
        if type(egress_limit) is not int or egress_limit not in {1, MAX_ATTEMPTS}:
            raise TypeError("identity work egress limit is malformed")
        timestamp = _time(now)
        lease = _time(now + LEASE_DURATION)
        exhausted = False
        try:
            with _transaction(self._connection):
                row = self._generation(claim.reference)
                current_lease, already_started = self._assert_claim(
                    row, claim, owner, now, lease="start"
                )
                self._bind_egress_limit(
                    claim.reference,
                    egress_limit,
                    timestamp,
                )
                if already_started:
                    return IdentityWorkClaim(
                        claim.reference,
                        claim.candidate,
                        claim.fence,
                        current_lease,
                    )
                if row[7] is None or _parse_time(str(row[7])) - now < MINIMUM_START_LEASE:
                    self._terminal_failure(
                        claim.reference, claim.fence, timestamp, resolve_attempt=False
                    )
                    exhausted = True
                if exhausted:
                    started = claim
                else:
                    attempts = _nonnegative_int(row[2], "attempt count")
                    if attempts >= egress_limit:
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
                    egress_limit = self._read_egress_limit(claim.reference)
                else:
                    next_candidate = claim.candidate
                    egress_limit = MAX_ATTEMPTS
                deadline = _parse_time(str(row[7])) if row[7] is not None else None
                if (
                    next_candidate > egress_limit
                    or deadline is None
                    or deadline - now < MINIMUM_START_LEASE
                ):
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
        retry_delay_seconds: int | None = 0,
        gate_owned: bool,
        fail_before_commit: bool = False,
    ) -> IdentityWorkStatus:
        """Finalize one started attempt and atomically retry or fail the generation."""

        _require_claim(claim)
        _owner(owner)
        _require_gate(gate_owned)
        if (
            type(retryable) is not bool
            or type(fail_before_commit) is not bool
            or (
                retry_delay_seconds is not None
                and (
                    type(retry_delay_seconds) is not int
                    or not 0 <= retry_delay_seconds <= 30
                )
            )
            or (not retryable and retry_delay_seconds not in {0, None})
        ):
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
                egress_limit = self._read_egress_limit(claim.reference)
                due = (
                    now + timedelta(seconds=retry_delay_seconds)
                    if retry_delay_seconds is not None
                    else None
                )
                if (
                    retryable
                    and due is not None
                    and attempt < egress_limit
                    and deadline is not None
                    and deadline - due >= MINIMUM_START_LEASE
                ):
                    candidate = attempt + 1
                    next_fence = claim.fence + 1
                    self._connection.execute(
                        "INSERT INTO hardcover_identity_candidates(work_key,generation,candidate,created_at) VALUES(?,?,?,?)",
                        (claim.reference.work_key, claim.reference.generation, candidate, timestamp),
                    )
                    self._connection.execute(
                        "INSERT INTO hardcover_identity_retry_schedules(work_key,generation,fence,candidate,due_at,reason,created_at) VALUES(?,?,?,?,?,'retryable',?)",
                        (
                            claim.reference.work_key,
                            claim.reference.generation,
                            next_fence,
                            candidate,
                            _time(due),
                            timestamp,
                        ),
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
                        IdentityWorkOutcome.RETRY_EXHAUSTED
                        if retryable
                        else IdentityWorkOutcome.PERSISTENCE_FAILURE,
                        claim.reference,
                    )
                if fail_before_commit:
                    raise IdentityWorkPersistenceError("injected identity failure publication")
                return result
        except (IdentityWorkPersistenceError, ValueError, TypeError):
            raise
        except (sqlite3.Error, OSError):
            raise IdentityWorkPersistenceError("identity work failure could not be published") from None

    def cancel_available(
        self,
        reference: IdentityWorkReference,
        now: datetime,
    ) -> None:
        """Atomically cancel pending or retry-wait work without inventing a claim."""

        _require_reference(reference)
        timestamp = _time(now)
        try:
            with _transaction(self._connection):
                row = self._generation(reference)
                state = _state(row[0])
                if state not in {
                    IdentityWorkState.PENDING,
                    IdentityWorkState.RETRY_WAIT,
                }:
                    raise IdentityWorkPersistenceError(
                        "identity work is not directly cancellable"
                    )
                if now < _parse_time(str(row[5])):
                    raise IdentityWorkPersistenceError(
                        "identity work chronology is invalid"
                    )
                validate_transition(state, IdentityWorkState.CANCELLED)
                fence = _nonnegative_int(row[1], "fence")
                terminal_fence = max(fence, 1)
                changed = self._connection.execute(
                    "UPDATE hardcover_identity_generations "
                    "SET state='cancelled',fence=?,lease_owner=NULL,lease_expires_at=NULL,updated_at=? "
                    "WHERE work_key=? AND generation=? AND state=? AND fence=?",
                    (
                        terminal_fence,
                        timestamp,
                        reference.work_key,
                        reference.generation,
                        state.value,
                        fence,
                    ),
                ).rowcount
                if changed != 1:
                    raise IdentityWorkPersistenceError(
                        "identity work cancellation raced"
                    )
                self._connection.execute(
                    "INSERT INTO hardcover_identity_results(work_key,generation,fence,outcome,published_at) "
                    "VALUES(?,?,?,'cancelled',?)",
                    (
                        reference.work_key,
                        reference.generation,
                        terminal_fence,
                        timestamp,
                    ),
                )
                self._event(
                    reference.work_key,
                    reference.generation,
                    "cancelled",
                    state.value,
                    "cancelled",
                    terminal_fence,
                    timestamp,
                )
        except (IdentityWorkPersistenceError, ValueError, TypeError):
            raise
        except (sqlite3.Error, OSError):
            raise IdentityWorkPersistenceError(
                "identity work could not be cancelled"
            ) from None

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
                self._assert_claim(row, claim, owner, now, lease="strict_live")
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

    def _bind_egress_limit(
        self,
        reference: IdentityWorkReference,
        egress_limit: int,
        timestamp: str,
    ) -> None:
        rows = self._connection.execute(
            "SELECT max_starts FROM hardcover_identity_egress_policies "
            "WHERE work_key=? AND generation=?",
            (reference.work_key, reference.generation),
        ).fetchall()
        if not rows:
            self._connection.execute(
                "INSERT INTO hardcover_identity_egress_policies("
                "work_key,generation,max_starts,created_at) VALUES(?,?,?,?)",
                (
                    reference.work_key,
                    reference.generation,
                    egress_limit,
                    timestamp,
                ),
            )
            return
        if rows != [(egress_limit,)]:
            raise IdentityWorkPersistenceError(
                "identity work egress authority changed"
            )

    def _read_egress_limit(self, reference: IdentityWorkReference) -> int:
        rows = self._connection.execute(
            "SELECT max_starts FROM hardcover_identity_egress_policies "
            "WHERE work_key=? AND generation=?",
            (reference.work_key, reference.generation),
        ).fetchall()
        if len(rows) != 1:
            raise IdentityWorkPersistenceError(
                "identity work egress authority is unavailable"
            )
        limit = _positive_int(rows[0][0], "egress limit")
        if limit not in {1, MAX_ATTEMPTS}:
            raise IdentityWorkPersistenceError(
                "identity work egress authority is corrupt"
            )
        return limit

    def _retry_candidate(
        self,
        reference: IdentityWorkReference,
        fence: int,
        now: datetime,
        generation_row: tuple[object, ...],
    ) -> int:
        schedules = self._connection.execute(
            "SELECT fence,candidate,due_at,reason,created_at FROM hardcover_identity_retry_schedules "
            "WHERE work_key=? AND generation=? ORDER BY fence",
            (reference.work_key, reference.generation),
        ).fetchall()
        if fence < 2 or [row[0] for row in schedules] != list(range(2, fence + 1)):
            raise IdentityWorkPersistenceError("identity work retry schedule is unavailable")
        parsed_schedules: dict[int, tuple[int, datetime, str, datetime, str]] = {}
        for raw_fence, raw_candidate, raw_due, reason, raw_created in schedules:
            schedule_fence = _positive_int(raw_fence, "retry fence")
            schedule_candidate = _positive_int(raw_candidate, "candidate")
            if (
                schedule_candidate > MAX_ATTEMPTS
                or reason not in {"interrupted", "retryable"}
                or type(raw_due) is not str
                or type(raw_created) is not str
            ):
                raise IdentityWorkPersistenceError(
                    "identity work retry schedule is corrupt"
                )
            due = _parse_time(raw_due)
            created = _parse_time(raw_created)
            if created > due:
                raise IdentityWorkPersistenceError(
                    "identity work retry schedule is corrupt"
                )
            parsed_schedules[schedule_fence] = (
                schedule_candidate,
                due,
                reason,
                created,
                raw_created,
            )

        candidate, due, _, _, raw_created = parsed_schedules[fence]
        if (
            candidate > MAX_ATTEMPTS
            or _nonnegative_int(generation_row[1], "fence") != fence
            or generation_row[5] != raw_created
        ):
            raise IdentityWorkPersistenceError("identity work retry schedule is corrupt")
        if now < due:
            raise IdentityWorkPersistenceError("identity work retry is not due")

        candidate_rows = self._connection.execute(
            "SELECT candidate,created_at FROM hardcover_identity_candidates "
            "WHERE work_key=? AND generation=? ORDER BY candidate",
            (reference.work_key, reference.generation),
        ).fetchall()
        if [row[0] for row in candidate_rows] != list(range(1, candidate + 1)):
            raise IdentityWorkPersistenceError("identity work retry candidate is corrupt")
        candidate_created: dict[int, datetime] = {}
        for raw_number, raw_candidate_created in candidate_rows:
            if type(raw_candidate_created) is not str:
                raise IdentityWorkPersistenceError(
                    "identity work retry candidate is corrupt"
                )
            candidate_created[_positive_int(raw_number, "candidate")] = _parse_time(
                raw_candidate_created
            )

        created_rows = self._connection.execute(
            "SELECT created_at FROM hardcover_identity_generations "
            "WHERE work_key=? AND generation=?",
            (reference.work_key, reference.generation),
        ).fetchall()
        if (
            len(created_rows) != 1
            or type(created_rows[0][0]) is not str
            or type(generation_row[6]) is not str
            or type(generation_row[7]) is not str
        ):
            raise IdentityWorkPersistenceError("identity work retry history is corrupt")
        generation_created = _parse_time(created_rows[0][0])
        aggregate_started = _parse_time(generation_row[6])
        aggregate_deadline = _parse_time(generation_row[7])
        if (
            generation_created > aggregate_started
            or aggregate_deadline != aggregate_started + AGGREGATE_WINDOW
            or candidate_created[1] != aggregate_started
        ):
            raise IdentityWorkPersistenceError("identity work retry history is corrupt")

        events = self._connection.execute(
            "SELECT sequence,kind,from_state,to_state,fence,occurred_at "
            "FROM hardcover_identity_events WHERE work_key=? AND generation=? "
            "ORDER BY sequence",
            (reference.work_key, reference.generation),
        ).fetchall()
        expected_events: list[tuple[int, str, str | None, str, int, str]] = []
        standard_prefix = (
            1,
            "accepted",
            None,
            "pending",
            0,
            _time(aggregate_started),
        )
        deferred_prefix = [
            (1, "expired", None, "pending", 0, _time(generation_created)),
            (2, "accepted", "pending", "pending", 0, _time(aggregate_started)),
        ]
        if events[:1] == [standard_prefix] and generation_created == aggregate_started:
            expected_events.append(standard_prefix)
        elif events[:2] == deferred_prefix:
            expected_events.extend(deferred_prefix)
        else:
            raise IdentityWorkPersistenceError("identity work retry history is corrupt")

        claims = self._connection.execute(
            "SELECT fence,candidate,owner,claimed_at,lease_expires_at FROM hardcover_identity_claims "
            "WHERE work_key=? AND generation=? ORDER BY fence",
            (reference.work_key, reference.generation),
        ).fetchall()
        if [row[0] for row in claims] != list(range(1, fence)):
            raise IdentityWorkPersistenceError("identity work retry history is corrupt")
        parsed_claims: dict[int, tuple[int, datetime, datetime]] = {}
        for (
            raw_claim_fence,
            raw_claim_candidate,
            raw_owner,
            raw_claimed,
            raw_lease,
        ) in claims:
            claim_fence = _positive_int(raw_claim_fence, "claim fence")
            claim_candidate = _positive_int(raw_claim_candidate, "claim candidate")
            if (
                claim_candidate > MAX_ATTEMPTS
                or type(raw_owner) is not str
                or not 1 <= len(raw_owner) <= 64
                or not raw_owner[0].islower()
                or set(raw_owner)
                - set("abcdefghijklmnopqrstuvwxyz0123456789_-")
                or type(raw_claimed) is not str
                or type(raw_lease) is not str
            ):
                raise IdentityWorkPersistenceError(
                    "identity work retry history is corrupt"
                )
            claimed = _parse_time(raw_claimed)
            lease = _parse_time(raw_lease)
            if (
                claim_candidate not in candidate_created
                or candidate_created[claim_candidate] > claimed
                or lease != claimed + LEASE_DURATION
            ):
                raise IdentityWorkPersistenceError(
                    "identity work retry history is corrupt"
                )
            if claim_fence == 1:
                if claim_candidate != 1:
                    raise IdentityWorkPersistenceError(
                        "identity work retry history is corrupt"
                    )
            else:
                scheduled_candidate, scheduled_due, _, _, _ = parsed_schedules[
                    claim_fence
                ]
                if claim_candidate != scheduled_candidate or claimed < scheduled_due:
                    raise IdentityWorkPersistenceError(
                        "identity work retry history is corrupt"
                    )
            parsed_claims[claim_fence] = (claim_candidate, claimed, lease)

        attempts = self._connection.execute(
            "SELECT attempt,fence,candidate,started_at,resolved_at,outcome "
            "FROM hardcover_identity_attempts WHERE work_key=? AND generation=? "
            "ORDER BY attempt",
            (reference.work_key, reference.generation),
        ).fetchall()
        attempt_count = _nonnegative_int(generation_row[2], "attempt count")
        if (
            attempt_count > MAX_ATTEMPTS
            or [row[0] for row in attempts]
            != list(range(1, attempt_count + 1))
            or self._connection.execute(
                "SELECT COUNT(*) FROM hardcover_identity_results WHERE work_key=? AND generation=?",
                (reference.work_key, reference.generation),
            ).fetchone()
            != (0,)
        ):
            raise IdentityWorkPersistenceError("identity work retry history is corrupt")
        attempts_by_fence: dict[int, tuple[int, datetime, datetime, str]] = {}
        for (
            raw_attempt,
            raw_attempt_fence,
            raw_attempt_candidate,
            raw_started,
            raw_resolved,
            outcome,
        ) in attempts:
            attempt = _positive_int(raw_attempt, "attempt")
            attempt_fence = _positive_int(raw_attempt_fence, "attempt fence")
            attempt_candidate = _positive_int(raw_attempt_candidate, "attempt candidate")
            claim = parsed_claims.get(attempt_fence)
            if (
                claim is None
                or attempt_fence in attempts_by_fence
                or attempt_candidate != attempt
                or attempt_candidate != claim[0]
                or type(raw_started) is not str
                or type(raw_resolved) is not str
                or outcome not in {"failed", "interrupted"}
            ):
                raise IdentityWorkPersistenceError(
                    "identity work retry history is corrupt"
                )
            started = _parse_time(raw_started)
            resolved = _parse_time(raw_resolved)
            if not claim[1] <= started <= claim[2] or resolved < started:
                raise IdentityWorkPersistenceError(
                    "identity work retry history is corrupt"
                )
            if (
                outcome == "failed"
                and resolved > started + LEASE_DURATION
                or outcome == "interrupted"
                and resolved <= started + LEASE_DURATION
            ):
                raise IdentityWorkPersistenceError(
                    "identity work retry history is corrupt"
                )
            attempts_by_fence[attempt_fence] = (
                attempt,
                started,
                resolved,
                outcome,
            )

        for claim_fence, (
            claim_candidate,
            claimed,
            claim_lease,
        ) in parsed_claims.items():
            expected_events.append(
                (
                    len(expected_events) + 1,
                    "claimed",
                    "pending" if claim_fence == 1 else "retry_wait",
                    "claimed",
                    claim_fence,
                    _time(claimed),
                )
            )
            schedule_fence = claim_fence + 1
            schedule_candidate, _, reason, created, schedule_timestamp = (
                parsed_schedules[schedule_fence]
            )
            attempt = attempts_by_fence.get(claim_fence)
            if attempt is None:
                coherent = (
                    reason == "interrupted"
                    and schedule_candidate == claim_candidate
                    and created > claim_lease
                )
            else:
                attempt_number, started, resolved, outcome = attempt
                coherent = (
                    schedule_candidate == attempt_number + 1
                    and resolved == created
                    and outcome
                    == ("interrupted" if reason == "interrupted" else "failed")
                )
                expected_events.append(
                    (
                        len(expected_events) + 1,
                        "started",
                        "claimed",
                        "claimed",
                        claim_fence,
                        _time(started),
                    )
                )
            if (
                not coherent
                or schedule_candidate not in candidate_created
                or candidate_created[schedule_candidate] > created
            ):
                raise IdentityWorkPersistenceError(
                    "identity work retry history is corrupt"
                )
            if schedule_candidate > claim_candidate:
                if (
                    schedule_candidate != claim_candidate + 1
                    or candidate_created[schedule_candidate] != created
                ):
                    raise IdentityWorkPersistenceError(
                        "identity work retry history is corrupt"
                    )
            elif schedule_candidate != claim_candidate:
                raise IdentityWorkPersistenceError(
                    "identity work retry history is corrupt"
                )
            expected_events.append(
                (
                    len(expected_events) + 1,
                    "recovered" if reason == "interrupted" else "retry_scheduled",
                    "claimed",
                    "retry_wait",
                    schedule_fence,
                    schedule_timestamp,
                )
            )

        if events != expected_events:
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
    ) -> tuple[datetime, bool]:
        persisted = self._connection.execute(
            "SELECT candidate,owner,claimed_at,lease_expires_at FROM hardcover_identity_claims "
            "WHERE work_key=? AND generation=? AND fence=?",
            (claim.reference.work_key, claim.reference.generation, claim.fence),
        ).fetchall()
        if len(persisted) != 1:
            raise StaleIdentityWorkFence("identity work fence is stale")
        persisted_candidate, persisted_owner, raw_claimed, raw_initial_lease = persisted[0]
        try:
            persisted_candidate_number = _positive_int(
                persisted_candidate, "candidate"
            )
            _candidate(persisted_candidate_number)
        except (IdentityWorkPersistenceError, TypeError, ValueError):
            raise StaleIdentityWorkFence("identity work fence is stale") from None
        try:
            claimed_at = _parse_time(str(raw_claimed))
            initial_lease = _parse_time(str(raw_initial_lease))
            current_lease = _parse_time(str(row[4]))
        except IdentityWorkPersistenceError:
            raise StaleIdentityWorkFence("identity work fence is stale") from None
        candidate_rows = self._connection.execute(
            "SELECT created_at FROM hardcover_identity_candidates "
            "WHERE work_key=? AND generation=? AND candidate=?",
            (
                claim.reference.work_key,
                claim.reference.generation,
                persisted_candidate_number,
            ),
        ).fetchall()
        claimed_events = self._connection.execute(
            "SELECT e.from_state,e.to_state,e.fence,c.candidate,e.occurred_at "
            "FROM hardcover_identity_events e JOIN hardcover_identity_claims c "
            "ON c.work_key=e.work_key AND c.generation=e.generation AND c.fence=e.fence "
            "WHERE e.work_key=? AND e.generation=? AND e.kind='claimed' AND e.fence=?",
            (claim.reference.work_key, claim.reference.generation, claim.fence),
        ).fetchall()
        expected_claimed_from = "pending" if claim.fence == 1 else "retry_wait"
        try:
            candidate_created = (
                _parse_time(str(candidate_rows[0][0]))
                if len(candidate_rows) == 1 and type(candidate_rows[0][0]) is str
                else None
            )
            claim_origin_is_coherent = (
                candidate_created is not None
                and candidate_created <= claimed_at
                and initial_lease == claimed_at + LEASE_DURATION
                and claimed_events
                == [
                    (
                        expected_claimed_from,
                        "claimed",
                        claim.fence,
                        persisted_candidate_number,
                        _time(claimed_at),
                    )
                ]
            )
            if claim.fence == 1:
                claim_origin_is_coherent = (
                    claim_origin_is_coherent and persisted_candidate_number == 1
                )
            else:
                schedules = self._connection.execute(
                    "SELECT candidate,due_at,created_at FROM hardcover_identity_retry_schedules "
                    "WHERE work_key=? AND generation=? AND fence=?",
                    (
                        claim.reference.work_key,
                        claim.reference.generation,
                        claim.fence,
                    ),
                ).fetchall()
                if (
                    len(schedules) != 1
                    or schedules[0][0] != persisted_candidate_number
                    or type(schedules[0][1]) is not str
                    or type(schedules[0][2]) is not str
                ):
                    claim_origin_is_coherent = False
                else:
                    due_at = _parse_time(schedules[0][1])
                    schedule_created = _parse_time(schedules[0][2])
                    claim_origin_is_coherent = (
                        claim_origin_is_coherent
                        and schedule_created <= due_at <= claimed_at
                        and candidate_created is not None
                        and candidate_created <= schedule_created
                    )
        except IdentityWorkPersistenceError:
            raise StaleIdentityWorkFence("identity work fence is stale") from None
        attempts = self._connection.execute(
            "SELECT attempt,candidate,started_at,resolved_at,outcome FROM hardcover_identity_attempts "
            "WHERE work_key=? AND generation=? AND fence=?",
            (claim.reference.work_key, claim.reference.generation, claim.fence),
        ).fetchall()
        started_events = self._connection.execute(
            "SELECT from_state,to_state,occurred_at FROM hardcover_identity_events "
            "WHERE work_key=? AND generation=? AND fence=? AND kind='started'",
            (claim.reference.work_key, claim.reference.generation, claim.fence),
        ).fetchall()
        attempt_count = _nonnegative_int(row[2], "attempt count")
        if not attempts:
            coherent_current_lease = (
                attempt_count == persisted_candidate_number - 1
                and current_lease == initial_lease
                and not started_events
            )
            already_started = False
            exact_updated_at = claimed_at
        elif len(attempts) == 1:
            attempt, candidate, started_at, resolved_at, outcome = attempts[0]
            try:
                started_time = _parse_time(str(started_at))
                expected_lease = started_time + LEASE_DURATION
            except IdentityWorkPersistenceError:
                raise StaleIdentityWorkFence("identity work fence is stale") from None
            coherent_current_lease = (
                attempt == attempt_count
                and attempt_count >= 1
                and candidate == persisted_candidate_number
                and attempt == persisted_candidate_number
                and resolved_at is None
                and outcome is None
                and claimed_at <= started_time <= initial_lease
                and current_lease == expected_lease
                and started_events == [("claimed", "claimed", started_at)]
            )
            already_started = True
            exact_updated_at = started_time
        else:
            coherent_current_lease = False
            already_started = False
            exact_updated_at = claimed_at
        expected_owner = persisted_owner if owner is None else owner
        exact_lease = claim.lease_expires_at == current_lease
        if lease == "start" and already_started:
            exact_lease = claim.lease_expires_at in {initial_lease, current_lease}
        if (
            _state(row[0]) is not IdentityWorkState.CLAIMED
            or _nonnegative_int(row[1], "fence") != claim.fence
            or persisted_candidate != claim.candidate
            or persisted_owner != expected_owner
            or row[3] != persisted_owner
            or not exact_lease
            or not claim_origin_is_coherent
            or not coherent_current_lease
            or _parse_time(str(row[5])) != exact_updated_at
            or now < _parse_time(str(row[5]))
        ):
            raise StaleIdentityWorkFence("identity work fence is stale")
        if lease == "live" and now > current_lease:
            raise StaleIdentityWorkFence("identity work fence is stale")
        if lease == "start" and now > current_lease:
            raise StaleIdentityWorkFence("identity work fence is stale")
        if lease == "strict_live" and now >= current_lease:
            raise StaleIdentityWorkFence("identity work fence is stale")
        if lease == "expired" and now <= current_lease:
            raise IdentityWorkPersistenceError("identity work lease has not expired")
        if lease not in {"live", "expired", "current", "start", "strict_live"}:
            raise TypeError("identity work lease assertion is malformed")
        return current_lease, already_started

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


def _require_egress_limit(value: object) -> None:
    if type(value) is not int or value not in {1, MAX_ATTEMPTS}:
        raise ValueError("identity work egress limit is malformed")


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
    except BaseException as error:
        if connection.in_transaction:
            try:
                connection.rollback()
            except BaseException:
                error.add_note("identity work transaction rollback failed")
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
