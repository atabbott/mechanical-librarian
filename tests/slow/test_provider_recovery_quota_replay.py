"""Synthetic #876 replay shaped to the public 2,400-work recovery tail."""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from mechanical_librarian.adapters.persistence.provider_recovery import (
    SQLiteProviderRecoveryStore,
)
from mechanical_librarian.adapters.providers.retry_safety import ProviderRequestPacer
from mechanical_librarian.application.provider_recovery_evidence import (
    classify_provider_recovery_failure,
)
from mechanical_librarian.application.provider_recovery_policy import (
    provider_recovery_decision,
)
from mechanical_librarian.application.provider_recovery_runner import (
    ScopedProviderRecoveryRunner,
)
from mechanical_librarian.application.provider_retry_successor import (
    ProviderRetrySharedFailure,
)
from mechanical_librarian.domain.failure_diagnostics import (
    FailureCategory,
    FailureComponent,
    FailureDiagnostic,
    FailureStage,
)
from mechanical_librarian.domain.metadata_report import (
    ProviderObservation,
    ProviderRequestAccounting,
    ProviderStatus,
)
from mechanical_librarian.domain.provider_recovery import (
    ProviderRecovery,
    ProviderRecoveryJob,
)

_WORKS = 2_400
_REQUESTS_PER_WORK = 4
_DAILY_QUOTA = 1_000


def _job(index: int) -> ProviderRecoveryJob:
    member = f"work-{index:04d}"
    return ProviderRecoveryJob(
        "google",
        "google_books",
        "books",
        1,
        member,
        "primary",
        "books",
        member,
        f"file-{member}",
        f"report-{member}",
        "proposal",
        f"report-{member}",
        "case",
        1,
        "admitted",
        '["work"]',
    )


class _QuotaLimitedRetrySuccessor:
    """Exercise the runner's real retry seam with closed report evidence."""

    def __init__(self, *, now: list[int]) -> None:
        self._now = now
        self._paced_clock = [0.0]
        self._pacer = ProviderRequestPacer(
            clock=lambda: self._paced_clock[0],
            sleep=lambda delay: self._paced_clock.__setitem__(
                0, self._paced_clock[0] + delay
            ),
        )
        self.daily_accounting: list[list[ProviderRequestAccounting]] = []
        self.completed_members: list[str] = []

    @property
    def paced_seconds(self) -> float:
        return self._paced_clock[0]

    def begin_day(self) -> None:
        self.daily_accounting.append([])

    def __call__(
        self,
        _recovery: ProviderRecovery,
        member: ProviderRecoveryJob,
        _renew: object,
    ) -> None:
        today = self.daily_accounting[-1]
        dispatched = sum(
            value.initial_dispatches + value.retry_dispatches for value in today
        )
        if dispatched + _REQUESTS_PER_WORK > _DAILY_QUOTA:
            exhausted = ProviderObservation(
                "google",
                ProviderStatus.RATE_LIMITED,
                authority="google_books",
                diagnostics=(
                    FailureDiagnostic(
                        FailureCategory.DAILY_QUOTA_EXHAUSTED,
                        FailureComponent.PROVIDER,
                        FailureStage.RESPONSE,
                    ),
                ),
                request_accounting=ProviderRequestAccounting(),
            )
            decision = provider_recovery_decision(exhausted, now=self._now[0])
            assert decision is not None
            raise ProviderRetrySharedFailure(*decision)

        for _ in range(_REQUESTS_PER_WORK):
            assert self._pacer.wait()
        successor = ProviderObservation(
            "google",
            ProviderStatus.NO_MATCH,
            authority="google_books",
            request_accounting=ProviderRequestAccounting(
                _REQUESTS_PER_WORK,
                _REQUESTS_PER_WORK,
            ),
        )
        today.append(successor.request_accounting)
        self.completed_members.append(member.member_id)


def test_paced_google_tail_spends_each_daily_quota_and_completes_in_two_weeks() -> None:
    pacific = ZoneInfo("America/Los_Angeles")
    now = [int(datetime(2026, 8, 27, tzinfo=pacific).timestamp())]

    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        store.record(
            ProviderRecovery(
                "google",
                "google_books",
                "books",
                1,
                "waiting",
                0,
                now[0],
            ),
            occurred_at=now[0],
        )
        successor = _QuotaLimitedRetrySuccessor(now=now)
        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=successor,
            classify_failure=classify_provider_recovery_failure,
            clock=lambda: now[0],
        )
        runner.enqueue(
            tuple(_job(index) for index in range(_WORKS)), enqueued_at=now[0]
        )

        run_states: list[str] = []
        deferred_health_attempts: list[int] = []
        while True:
            successor.begin_day()
            result = runner.run_one(now=now[0], token=f"day-{len(run_states):02d}")
            run_states.append(result.state)
            if result.state == "recovered":
                break
            assert result.state == "waiting"
            health = store.snapshot()[0]
            deferred_health_attempts.append(health.attempt)
            assert health.next_eligible_at is not None
            now[0] = health.next_eligible_at

        rows = store._connection.execute(
            "SELECT member_id,state,attempt FROM provider_recovery_jobs "
            "ORDER BY member_id"
        ).fetchall()
        store.close()

    daily_spend = [
        sum(value.initial_dispatches + value.retry_dispatches for value in day)
        for day in successor.daily_accounting
    ]
    daily_logical = [
        sum(value.logical_requests for value in day)
        for day in successor.daily_accounting
    ]
    assert ["waiting"] * 9 + ["recovered"] == run_states
    assert [1_000] * 9 + [600] == daily_spend == daily_logical
    assert 9_600 == sum(daily_spend)
    assert [0] * 9 == deferred_health_attempts
    assert [f"work-{index:04d}" for index in range(_WORKS)] == (
        successor.completed_members
    )
    assert [(f"work-{index:04d}", "complete", 1) for index in range(_WORKS)] == rows
    assert 9_599.0 == successor.paced_seconds
