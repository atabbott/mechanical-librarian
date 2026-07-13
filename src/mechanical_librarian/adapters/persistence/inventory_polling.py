"""SQLite-backed durable scheduling for bounded inventory polls."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterator

from mechanical_librarian.application.job_heartbeat import (
    RepositoryOutcome,
    RepositoryResult,
)
from mechanical_librarian.application.ports import FailureKind, PortFailure
from mechanical_librarian.domain.inventory_polling import (
    PollClaim,
    PollInspection,
    PollPolicy,
    PollRequest,
    PollState,
    retry_delay,
)
from mechanical_librarian.domain.inventory_v2 import ScanUsage


_BLOCKING_FAILURES = {
    FailureKind.AUTHENTICATION,
    FailureKind.AUTHORIZATION,
    FailureKind.NOT_FOUND,
    FailureKind.UNSUPPORTED,
    FailureKind.INCOMPATIBLE,
    FailureKind.INVALID_REQUEST,
    FailureKind.INVALID_RESPONSE,
    FailureKind.INTEGRITY_FAILURE,
}
_RETRYABLE_FAILURES = {
    FailureKind.UNAVAILABLE,
    FailureKind.RATE_LIMITED,
    FailureKind.TIMEOUT,
    FailureKind.CONFLICT,
}


class SQLiteInventoryPollingRepository:
    """Persist poll intent, cumulative budgets, claims, and terminal evidence."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._connection = connection
        self._now = now

    def schedule(
        self, request: PollRequest, now: datetime
    ) -> RepositoryResult[PollInspection]:
        if type(request) is not PollRequest or not _valid_time(now):
            return RepositoryResult(RepositoryOutcome.REJECTED)
        try:
            with _transaction(self._connection):
                duplicate = self._connection.execute(
                    "SELECT poll_id FROM inventory_polls "
                    "WHERE trigger_id=? AND governing_digest=?",
                    (request.trigger_id, request.governing_digest),
                ).fetchone()
                if duplicate is not None:
                    return RepositoryResult(
                        RepositoryOutcome.ACCEPTED,
                        _inspection(self._row(str(duplicate[0]))),
                    )
                blocked = self._connection.execute(
                    "SELECT poll_id FROM inventory_polls "
                    "WHERE scope_digest=? AND governing_digest=? "
                    "AND state='blocked' ORDER BY updated_at DESC,poll_id DESC LIMIT 1",
                    (request.scope_digest, request.governing_digest),
                ).fetchone()
                if blocked is not None:
                    return RepositoryResult(
                        RepositoryOutcome.ACCEPTED,
                        _inspection(self._row(str(blocked[0]))),
                    )
                policy = request.policy
                self._connection.execute(
                    "INSERT INTO inventory_polls(poll_id,trigger_id,scope_digest,"
                    "governing_digest,state,max_attempts,max_elapsed_seconds,lease_seconds,"
                    "initial_backoff_seconds,max_backoff_seconds,max_pages,max_items,max_bytes,"
                    "created_at,elapsed_deadline,updated_at) VALUES (?,?,?,?,'pending',"
                    "?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        request.poll_id,
                        request.trigger_id,
                        request.scope_digest,
                        request.governing_digest,
                        policy.max_attempts,
                        policy.max_elapsed_seconds,
                        policy.lease_seconds,
                        policy.initial_backoff_seconds,
                        policy.max_backoff_seconds,
                        policy.max_pages,
                        policy.max_items,
                        policy.max_bytes,
                        _format(now),
                        _format(now + timedelta(seconds=policy.max_elapsed_seconds)),
                        _format(now),
                    ),
                )
                row = self._row(request.poll_id)
            return RepositoryResult(RepositoryOutcome.ACCEPTED, _inspection(row))
        except sqlite3.IntegrityError:
            return RepositoryResult(RepositoryOutcome.REJECTED)
        except (sqlite3.Error, ValueError):
            return RepositoryResult(RepositoryOutcome.UNAVAILABLE)

    def claim(
        self, poll_id: str, worker_id: str, now: datetime
    ) -> RepositoryResult[PollClaim]:
        if not _identifier(poll_id) or not _identifier(worker_id) or not _valid_time(now):
            return RepositoryResult(RepositoryOutcome.REJECTED)
        try:
            with _transaction(self._connection):
                now = max(now, self._now())
                row = self._row(poll_id)
                if row is None:
                    return RepositoryResult(RepositoryOutcome.NOT_FOUND)
                if now < _parse(row[23]):
                    return RepositoryResult(RepositoryOutcome.REJECTED)
                if row[4] == PollState.RUNNING.value:
                    expires = _parse(row[18])
                    if expires > now:
                        return RepositoryResult(RepositoryOutcome.REJECTED)
                    self._expire_claim(row, now)
                    row = self._row(poll_id)
                if bool(row[20]):
                    self._terminalize(poll_id, PollState.CANCELLED, "cancelled", now)
                    return RepositoryResult(RepositoryOutcome.REJECTED)
                deadline = _parse(row[25])
                if now >= deadline:
                    self._terminalize(
                        poll_id, PollState.FAILED_TERMINAL, "elapsed_budget_exhausted", now
                    )
                    return RepositoryResult(RepositoryOutcome.REJECTED)
                if row[4] not in {
                    PollState.PENDING.value,
                    PollState.RETRY_WAIT.value,
                }:
                    return RepositoryResult(RepositoryOutcome.REJECTED)
                if row[21] is not None and _parse(row[21]) > now:
                    return RepositoryResult(RepositoryOutcome.REJECTED)
                attempt = int(row[5]) + 1
                if attempt > int(row[6]):
                    self._terminalize(
                        poll_id, PollState.FAILED_TERMINAL, "attempt_budget_exhausted", now
                    )
                    return RepositoryResult(RepositoryOutcome.REJECTED)
                fence = int(row[19]) + 1
                lease_expires = min(
                    deadline, now + timedelta(seconds=int(row[8]))
                )
                self._connection.execute(
                    "UPDATE inventory_polls SET state='running',attempt=?,lease_owner=?,"
                    "lease_expires_at=?,fence=?,next_eligible_at=NULL,failure_kind=NULL,"
                    "updated_at=? WHERE poll_id=?",
                    (
                        attempt,
                        worker_id,
                        _format(lease_expires),
                        fence,
                        _format(now),
                        poll_id,
                    ),
                )
                self._connection.execute(
                    "INSERT INTO inventory_poll_attempts(poll_id,attempt,fence,worker_id,"
                    "state,started_at) VALUES (?,?,?,?,'running',?)",
                    (poll_id, attempt, fence, worker_id, _format(now)),
                )
            return RepositoryResult(
                RepositoryOutcome.ACCEPTED,
                PollClaim(poll_id, attempt, fence, worker_id, lease_expires),
            )
        except (sqlite3.Error, ValueError, TypeError):
            return RepositoryResult(RepositoryOutcome.UNAVAILABLE)

    def fail(
        self,
        claim: PollClaim,
        failure: PortFailure,
        usage: ScanUsage,
        now: datetime,
        *,
        compatibility: str | None = None,
    ) -> RepositoryResult[PollInspection]:
        if (
            type(claim) is not PollClaim
            or type(failure) is not PortFailure
            or type(usage) is not ScanUsage
            or not _valid_time(now)
            or compatibility not in {None, "supported", "incompatible", "unknown", "contract_drift"}
        ):
            return RepositoryResult(RepositoryOutcome.REJECTED)
        try:
            with _transaction(self._connection):
                now = max(now, self._now())
                row = self._claimed_row(claim, now)
                if row is None:
                    return RepositoryResult(RepositoryOutcome.REJECTED)
                linked = self._connection.execute(
                    "SELECT COALESCE(SUM(s.page_count),0),"
                    "COALESCE(SUM(s.item_count),0),"
                    "COALESCE(SUM(s.response_bytes),0) FROM inventory_scans s JOIN "
                    "inventory_scan_poll_claims c USING(scan_id) WHERE c.poll_id=? "
                    "AND c.attempt=? AND c.fence=?",
                    (claim.poll_id, claim.attempt, claim.fence),
                ).fetchone()
                linked_usage = ScanUsage(*(int(value) for value in linked))
                usage = ScanUsage(
                    max(usage.pages, linked_usage.pages),
                    max(usage.items, linked_usage.items),
                    max(usage.response_bytes, linked_usage.response_bytes),
                )
                self._connection.execute(
                    "UPDATE inventory_scans SET state='incomplete',failure_kind=?,"
                    "finished_at=? WHERE state='running' AND scan_id IN (SELECT scan_id "
                    "FROM inventory_scan_poll_claims WHERE poll_id=? AND attempt=? AND fence=?)",
                    (
                        failure.kind.value,
                        _format(now),
                        claim.poll_id,
                        claim.attempt,
                        claim.fence,
                    ),
                )
                pages = int(row[13]) + usage.pages
                items = int(row[14]) + usage.items
                response_bytes = int(row[15]) + usage.response_bytes
                exhausted = (
                    pages > int(row[10])
                    or items > int(row[11])
                    or response_bytes > int(row[12])
                )
                pages = min(pages, int(row[10]))
                items = min(items, int(row[11]))
                response_bytes = min(response_bytes, int(row[12]))
                deadline = _parse(row[25])
                local_delay = retry_delay(_policy(row), claim.attempt)
                advised = failure.retry.retry_after
                delay = max(local_delay, advised) if advised is not None else local_delay
                next_eligible = now + delay
                retryable = failure.kind in _RETRYABLE_FAILURES or failure.retry.retryable
                if bool(row[20]) or failure.kind is FailureKind.CANCELLED:
                    state = PollState.CANCELLED
                elif exhausted or failure.kind is FailureKind.RESOURCE_EXHAUSTED:
                    state = PollState.FAILED_TERMINAL
                elif failure.kind in _BLOCKING_FAILURES:
                    state = PollState.BLOCKED
                elif retryable and claim.attempt < int(row[6]) and next_eligible < deadline:
                    state = PollState.RETRY_WAIT
                else:
                    state = PollState.FAILED_TERMINAL
                failure_kind = (
                    FailureKind.RESOURCE_EXHAUSTED.value if exhausted else failure.kind.value
                )
                next_value = _format(next_eligible) if state is PollState.RETRY_WAIT else None
                self._connection.execute(
                    "UPDATE inventory_polls SET state=?,used_pages=?,used_items=?,used_bytes=?,"
                    "lease_owner=NULL,lease_expires_at=NULL,next_eligible_at=?,failure_kind=?,"
                    "compatibility=COALESCE(?,compatibility),updated_at=? WHERE poll_id=?",
                    (
                        state.value,
                        pages,
                        items,
                        response_bytes,
                        next_value,
                        failure_kind,
                        compatibility,
                        _format(now),
                        claim.poll_id,
                    ),
                )
                self._connection.execute(
                    "UPDATE inventory_poll_attempts SET state=?,page_count=?,item_count=?,"
                    "response_bytes=?,failure_kind=?,compatibility=?,finished_at=? "
                    "WHERE poll_id=? AND attempt=? AND fence=? AND state='running'",
                    (
                        state.value,
                        usage.pages,
                        usage.items,
                        usage.response_bytes,
                        failure_kind,
                        compatibility,
                        _format(now),
                        claim.poll_id,
                        claim.attempt,
                        claim.fence,
                    ),
                )
                row = self._row(claim.poll_id)
            return RepositoryResult(RepositoryOutcome.ACCEPTED, _inspection(row))
        except (sqlite3.Error, ValueError, TypeError):
            return RepositoryResult(RepositoryOutcome.UNAVAILABLE)

    def complete(
        self,
        claim: PollClaim,
        usage: ScanUsage,
        now: datetime,
        *,
        compatibility: str = "supported",
    ) -> RepositoryResult[PollInspection]:
        if (
            type(claim) is not PollClaim
            or type(usage) is not ScanUsage
            or not _valid_time(now)
            or compatibility != "supported"
        ):
            return RepositoryResult(RepositoryOutcome.REJECTED)
        try:
            with _transaction(self._connection):
                now = max(now, self._now())
                row = self._claimed_row(claim, now)
                if row is None:
                    return RepositoryResult(RepositoryOutcome.REJECTED)
                totals = (
                    int(row[13]) + usage.pages,
                    int(row[14]) + usage.items,
                    int(row[15]) + usage.response_bytes,
                )
                if totals[0] > int(row[10]) or totals[1] > int(row[11]) or totals[2] > int(row[12]):
                    return RepositoryResult(RepositoryOutcome.REJECTED)
                self._connection.execute(
                    "UPDATE inventory_polls SET state='succeeded',used_pages=?,used_items=?,"
                    "used_bytes=?,lease_owner=NULL,lease_expires_at=NULL,failure_kind=NULL,"
                    "compatibility=?,updated_at=? WHERE poll_id=?",
                    (*totals, compatibility, _format(now), claim.poll_id),
                )
                self._connection.execute(
                    "UPDATE inventory_poll_attempts SET state='succeeded',page_count=?,"
                    "item_count=?,response_bytes=?,compatibility=?,finished_at=? "
                    "WHERE poll_id=? AND attempt=? AND fence=? AND state='running'",
                    (
                        usage.pages,
                        usage.items,
                        usage.response_bytes,
                        compatibility,
                        _format(now),
                        claim.poll_id,
                        claim.attempt,
                        claim.fence,
                    ),
                )
                row = self._row(claim.poll_id)
            return RepositoryResult(RepositoryOutcome.ACCEPTED, _inspection(row))
        except (sqlite3.Error, ValueError, TypeError):
            return RepositoryResult(RepositoryOutcome.UNAVAILABLE)

    def cancel(self, poll_id: str, now: datetime) -> RepositoryResult[PollInspection]:
        if not _identifier(poll_id) or not _valid_time(now):
            return RepositoryResult(RepositoryOutcome.REJECTED)
        try:
            with _transaction(self._connection):
                now = max(now, self._now())
                row = self._row(poll_id)
                if row is None:
                    return RepositoryResult(RepositoryOutcome.NOT_FOUND)
                state = str(row[4])
                terminal = state in {
                    PollState.SUCCEEDED.value,
                    PollState.BLOCKED.value,
                    PollState.FAILED_TERMINAL.value,
                    PollState.CANCELLED.value,
                }
                next_state = state if terminal else (
                    PollState.RUNNING.value if state == PollState.RUNNING.value else PollState.CANCELLED.value
                )
                self._connection.execute(
                    "UPDATE inventory_polls SET cancellation_requested=1,state=?,"
                    "next_eligible_at=NULL,updated_at=? WHERE poll_id=?",
                    (next_state, _format(now), poll_id),
                )
                row = self._row(poll_id)
            return RepositoryResult(RepositoryOutcome.ACCEPTED, _inspection(row))
        except (sqlite3.Error, ValueError):
            return RepositoryResult(RepositoryOutcome.UNAVAILABLE)

    def inspect(self, poll_id: str) -> RepositoryResult[PollInspection]:
        if not _identifier(poll_id):
            return RepositoryResult(RepositoryOutcome.REJECTED)
        try:
            row = self._row(poll_id)
            if row is None:
                return RepositoryResult(RepositoryOutcome.NOT_FOUND)
            return RepositoryResult(RepositoryOutcome.ACCEPTED, _inspection(row))
        except (sqlite3.Error, ValueError):
            return RepositoryResult(RepositoryOutcome.UNAVAILABLE)

    def claim_is_live(self, claim: PollClaim, now: datetime) -> bool:
        try:
            return self._claimed_row(claim, max(now, self._now())) is not None
        except (sqlite3.Error, ValueError, TypeError):
            return False

    def _row(self, poll_id: str):
        return self._connection.execute(
            "SELECT poll_id,trigger_id,scope_digest,governing_digest,state,attempt,"
            "max_attempts,max_elapsed_seconds,lease_seconds,initial_backoff_seconds,"
            "max_pages,max_items,max_bytes,used_pages,used_items,used_bytes,"
            "max_backoff_seconds,lease_owner,lease_expires_at,fence,"
            "cancellation_requested,next_eligible_at,failure_kind,updated_at,"
            "created_at,elapsed_deadline,compatibility FROM inventory_polls WHERE poll_id=?",
            (poll_id,),
        ).fetchone()

    def _claimed_row(self, claim: PollClaim, now: datetime):
        row = self._row(claim.poll_id)
        if (
            row is None
            or row[4] != PollState.RUNNING.value
            or row[5] != claim.attempt
            or row[17] != claim.worker_id
            or row[19] != claim.fence
            or bool(row[20])
            or _parse(row[18]) <= now
            or now < _parse(row[23])
        ):
            return None
        return row

    def _expire_claim(self, row, now: datetime) -> None:
        poll_id = str(row[0])
        attempt = int(row[5])
        fence = int(row[19])
        deadline = _parse(row[25])
        cancelled = bool(row[20])
        consumed = self._connection.execute(
            "SELECT COALESCE(SUM(s.page_count),0),COALESCE(SUM(s.item_count),0),"
            "COALESCE(SUM(s.response_bytes),0) FROM inventory_scans s JOIN "
            "inventory_scan_poll_claims c USING(scan_id) WHERE c.poll_id=? "
            "AND c.attempt=? AND c.fence=?",
            (poll_id, attempt, fence),
        ).fetchone()
        pages, items, response_bytes = (int(value) for value in consumed)
        total_pages = min(int(row[10]), int(row[13]) + pages)
        total_items = min(int(row[11]), int(row[14]) + items)
        total_bytes = min(int(row[12]), int(row[15]) + response_bytes)
        exhausted = (
            int(row[13]) + pages > int(row[10])
            or int(row[14]) + items > int(row[11])
            or int(row[15]) + response_bytes > int(row[12])
        )
        retry = (
            not cancelled
            and not exhausted
            and attempt < int(row[6])
            and now < deadline
        )
        state = (
            PollState.CANCELLED
            if cancelled
            else PollState.RETRY_WAIT if retry else PollState.FAILED_TERMINAL
        )
        failure = (
            "cancelled"
            if cancelled
            else "resource_exhausted"
            if exhausted
            else "lease_expired"
        )
        self._connection.execute(
            "UPDATE inventory_poll_attempts SET state=?,page_count=?,item_count=?,"
            "response_bytes=?,failure_kind=?,finished_at=? "
            "WHERE poll_id=? AND attempt=? AND fence=? AND state='running'",
            (
                "lease_expired" if retry else state.value,
                pages,
                items,
                response_bytes,
                failure,
                _format(now),
                poll_id,
                attempt,
                fence,
            ),
        )
        self._connection.execute(
            "UPDATE inventory_scans SET state='incomplete',failure_kind='interrupted',"
            "finished_at=? WHERE state='running' AND scan_id IN (SELECT scan_id FROM "
            "inventory_scan_poll_claims WHERE poll_id=? AND attempt=? AND fence=?)",
            (_format(now), poll_id, attempt, fence),
        )
        self._connection.execute(
            "UPDATE inventory_polls SET state=?,used_pages=?,used_items=?,used_bytes=?,"
            "lease_owner=NULL,lease_expires_at=NULL,"
            "next_eligible_at=?,failure_kind=?,updated_at=? WHERE poll_id=?",
            (
                state.value,
                total_pages,
                total_items,
                total_bytes,
                _format(now) if retry else None,
                failure,
                _format(now),
                poll_id,
            ),
        )

    def _terminalize(
        self, poll_id: str, state: PollState, failure: str, now: datetime
    ) -> None:
        self._connection.execute(
            "UPDATE inventory_polls SET state=?,lease_owner=NULL,lease_expires_at=NULL,"
            "next_eligible_at=NULL,failure_kind=?,updated_at=? WHERE poll_id=?",
            (state.value, failure, _format(now), poll_id),
        )


def _policy(row) -> PollPolicy:
    return PollPolicy(
        int(row[6]), int(row[7]), int(row[8]), int(row[9]), int(row[16]),
        int(row[10]), int(row[11]), int(row[12]),
    )


def _inspection(row) -> PollInspection:
    if row is None:
        raise ValueError("inventory poll is unavailable")
    return PollInspection(
        str(row[0]),
        PollState(str(row[4])),
        int(row[5]),
        int(row[6]),
        int(row[19]),
        ScanUsage(int(row[13]), int(row[14]), int(row[15])),
        None if row[21] is None else _parse(str(row[21])),
        None if row[22] is None else str(row[22]),
        None if row[26] is None else str(row[26]),
        bool(row[20]),
    )


def _identifier(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= 64
        and value[0].islower()
        and all(character.islower() or character.isdigit() or character in "_-" for character in value)
    )


def _valid_time(value: object) -> bool:
    return type(value) is datetime and value.tzinfo is not None and value.utcoffset() is not None


def _format(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


@contextmanager
def _transaction(connection: sqlite3.Connection) -> Iterator[None]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    else:
        connection.execute("COMMIT")


__all__ = ["SQLiteInventoryPollingRepository"]
