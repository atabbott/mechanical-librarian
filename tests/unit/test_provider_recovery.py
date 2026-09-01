from __future__ import annotations

import ast
import sqlite3
import tempfile
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Literal, cast
from unittest.mock import patch

import pytest

import mechanical_librarian.adapters.persistence.provider_recovery as recovery_persistence
import mechanical_librarian.application.provider_recovery_activation as recovery_activation
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.persistence.provider_recovery import (
    ProviderRecoveryJob,
    ProviderRecoveryStoreError,
    SQLiteProviderRecoveryStore,
)
from mechanical_librarian.application.metadata_report import CredentialError
from mechanical_librarian.application.provider_recovery_activation import (
    ProviderRecoveryActivation,
    ProviderRecoveryRepository,
)
from mechanical_librarian.application.provider_recovery_runner import (
    ScopedProviderRecoveryRunner,
)
from mechanical_librarian.application.provider_retry_successor import (
    ProviderRetrySuccessorError,
)
from mechanical_librarian.application.review_pipeline import ReviewPipelineError
from mechanical_librarian.bootstrap.composition import (
    _provider_recovery_jobs,
    _record_provider_recovery_observations,
)
from mechanical_librarian.domain.metadata_report import ProviderStatus
from mechanical_librarian.domain.provider_recovery import (
    MAX_TIMESTAMP,
    ProviderRecovery,
    ProviderRecoveryError,
)
from tests.support.review_cases import _report

_COMMON_JOB_PREFIX = (
    "google",
    "google_books",
    "books",
    1,
    "member",
    "primary",
    "books",
    "item",
    "file",
    "report",
)


def _job(member_id: str) -> ProviderRecoveryJob:
    return ProviderRecoveryJob(
        "google",
        "google_books",
        "books",
        1,
        member_id,
        "primary",
        "books",
        member_id,
        f"file-{member_id}",
        f"report-{member_id}",
        "proposal",
        f"report-{member_id}",
        "case",
        1,
        "admitted",
        '["work"]',
    )


def _due_store(path: Path, *scopes: str) -> SQLiteProviderRecoveryStore:
    """Build exact due health for recovery-dispatch tests."""
    store = SQLiteProviderRecoveryStore.open(path)
    for scope in scopes or ("books",):
        store.record(
            ProviderRecovery.failed(
                connection="google",
                adapter="google_books",
                scope=scope,
                generation=1,
                failure="transient",
                now=0,
                retry_after=1,
            ),
            occurred_at=0,
        )
    return store


@pytest.mark.parametrize(
    "package",
    (
        (None, "report", None, None, "report_only"),
        ("proposal", "report", "case", 1, "admitted"),
        ("proposal", "report", "case", 1, "superseded"),
        ("proposal", "report", None, 1, "no_case"),
        ("proposal", "report", None, 1, "retired"),
    ),
    ids=("report_only", "admitted", "superseded", "no_case", "retired"),
)
def test_job_preserves_each_canonical_current_review_package_shape(package) -> None:
    common = _COMMON_JOB_PREFIX
    assert package == (
        ProviderRecoveryJob(
            *common,
            *cast(tuple[str | None, str, str | None, int | None, str], package),
            '["work"]',
        ).package_proposal_id,
        ProviderRecoveryJob(
            *common,
            *cast(tuple[str | None, str, str | None, int | None, str], package),
            '["work"]',
        ).package_report_id,
        ProviderRecoveryJob(
            *common,
            *cast(tuple[str | None, str, str | None, int | None, str], package),
            '["work"]',
        ).package_case_id,
        ProviderRecoveryJob(
            *common,
            *cast(tuple[str | None, str, str | None, int | None, str], package),
            '["work"]',
        ).package_checkpoint,
        ProviderRecoveryJob(
            *common,
            *cast(tuple[str | None, str, str | None, int | None, str], package),
            '["work"]',
        ).package_outcome,
    )


def test_job_rejects_a_case_id_the_report_only_outcome_forbids() -> None:
    # Split from the loop above, which became `parametrize`: this assertion sat
    # after the loop and is not one of its variants.
    common = _COMMON_JOB_PREFIX
    with pytest.raises(ProviderRecoveryError):
        ProviderRecoveryJob(
            *common, None, "report", "case", 1, "report_only", '["work"]'
        )


class _DispatchReason(str):
    pass


class _DispatchRank(int):
    pass


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("dispatch_reason", ["blanket_scan"]),
        ("dispatch_reason", _DispatchReason("blanket_scan")),
        ("dispatch_rank", True),
        ("dispatch_rank", _DispatchRank(3)),
    ),
    ids=("reason-list", "reason-subclass", "rank-bool", "rank-subclass"),
)
def test_job_rejects_noncanonical_dispatch_types_with_domain_error(
    field: str, value: object
) -> None:
    with pytest.raises(ProviderRecoveryError, match="job is invalid"):
        replace(_job("member"), **{field: value})


def test_enqueue_enrolls_an_unseen_member_and_health_before_its_job() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        assert 1 == store.enqueue_jobs((_job("unseen"),), enqueued_at=3)
        assert [("google", "google_books", "books")] == store._connection.execute(
            "SELECT connection,adapter,scope FROM provider_recovery_members"
        ).fetchall()
        assert [("google", "google_books", "books", 1, "waiting", 1, 7_203)] == (
            store._connection.execute(
                "SELECT connection,adapter,scope,generation,state,attempt,next_eligible_at "
                "FROM provider_recovery_health"
            ).fetchall()
        )
        assert [("unseen", "queued")] == store._connection.execute(
            "SELECT member_id,state FROM provider_recovery_jobs"
        ).fetchall()
        assert [("waiting", 1, 7_203, 3)] == store._connection.execute(
            "SELECT state,attempt,next_eligible_at,occurred_at "
            "FROM provider_recovery_events"
        ).fetchall()


def test_replayed_registration_preserves_ordinal_but_current_refresh_renews_it() -> (
    None
):
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        original = _job("member")
        assert 1 == store.enqueue_jobs((original,), enqueued_at=3)
        assert 0 == store.enqueue_jobs((original,), enqueued_at=4)
        assert [("member", 1)] == store._connection.execute(
            "SELECT member_id,enrollment_ordinal FROM provider_recovery_jobs"
        ).fetchall()
        store._connection.execute(
            "UPDATE provider_recovery_jobs SET state='failed',attempt=5 WHERE member_id='member'"
        )
        refreshed = replace(
            original,
            source_report_id="report-refreshed",
            package_report_id="report-refreshed",
        )
        assert 1 == store.enqueue_jobs((refreshed,), enqueued_at=5)
        assert [("member", "queued", 0, 2)] == store._connection.execute(
            "SELECT member_id,state,attempt,enrollment_ordinal FROM provider_recovery_jobs"
        ).fetchall()


def test_frozen_fanout_cohort_defers_later_arrivals_without_starving_them() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        store.record(
            ProviderRecovery.failed(
                connection="google",
                adapter="google_books",
                scope="books",
                generation=1,
                failure="transient",
                now=0,
                retry_after=1,
            ),
            occurred_at=0,
        )
        store.enqueue_jobs((_job("canary"), _job("sibling")), enqueued_at=0)
        dispatched: list[str] = []

        def retry(_, job: ProviderRecoveryJob, __) -> None:
            dispatched.append(job.member_id)
            if job.member_id == "sibling":
                store.enqueue_jobs((_job("later"),), enqueued_at=1)

        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=retry,
            classify_failure=lambda _: ("transient", None),
        )
        assert "waiting" == runner.run_one(now=1, token="first").state
        assert ["canary", "sibling"] == dispatched
        assert [("later", "queued")] == store._connection.execute(
            "SELECT member_id,state FROM provider_recovery_jobs WHERE member_id='later'"
        ).fetchall()
        assert "recovered" == runner.run_one(now=1, token="second").state
        assert ["canary", "sibling", "later"] == dispatched


def test_constrained_replay_dispatches_decided_field_reasons_before_blanket_scan() -> (
    None
):
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        store.record(
            ProviderRecovery.failed(
                connection="google",
                adapter="google_books",
                scope="books",
                generation=1,
                failure="transient",
                now=0,
                retry_after=1,
            ),
            occurred_at=0,
        )
        jobs = (
            replace(_job("a-blanket"), dispatch_reason="blanket_scan", dispatch_rank=3),
            replace(
                _job("b-disagreement"),
                dispatch_reason="settleable_disagreement",
                dispatch_rank=2,
            ),
            replace(
                _job("c-zero"),
                dispatch_reason="zero_provider_evidence",
                dispatch_rank=1,
            ),
            replace(
                _job("d-corroboration"),
                dispatch_reason="missing_corroboration",
                dispatch_rank=0,
            ),
        )
        store.enqueue_jobs(jobs, enqueued_at=0)
        dispatched: list[tuple[str, str]] = []
        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=lambda _, job, __: dispatched.append(
                (job.member_id, job.dispatch_reason)
            ),
            classify_failure=lambda _: ("transient", None),
        )
        assert "recovered" == runner.run_one(now=1, token="owner").state
        assert [
            ("d-corroboration", "missing_corroboration"),
            ("c-zero", "zero_provider_evidence"),
            ("b-disagreement", "settleable_disagreement"),
            ("a-blanket", "blanket_scan"),
        ] == dispatched
        assert [
            ("d-corroboration", "missing_corroboration"),
            ("c-zero", "zero_provider_evidence"),
        ] == dispatched[:2]
        budget = 2
        decided = {"missing_corroboration", "zero_provider_evidence"}
        ranked_yield = sum(reason in decided for _, reason in dispatched[:budget])
        blanket_yield = sum(
            job.dispatch_reason in decided
            for job in sorted(jobs, key=lambda job: job.member_id)[:budget]
        )
        assert 2 == ranked_yield
        assert 0 == blanket_yield
        assert ranked_yield > blanket_yield
        store.close()


def test_same_provider_budget_orders_across_library_scopes() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = _due_store(
            Path(directory) / "recovery.sqlite", "library-a", "library-z"
        )
        store.record(
            ProviderRecovery.failed(
                connection="alpha",
                adapter="hardcover",
                scope="library-0",
                generation=1,
                failure="transient",
                now=0,
                retry_after=1,
            ),
            occurred_at=0,
        )
        independent = replace(
            _job("independent-blanket"),
            connection="alpha",
            adapter="hardcover",
            scope="library-0",
            library="library-0",
        )
        blanket = replace(
            _job("a-blanket"),
            scope="library-a",
            library="library-a",
            dispatch_reason="blanket_scan",
            dispatch_rank=3,
        )
        corroboration = replace(
            _job("z-corroboration"),
            scope="library-z",
            library="library-z",
            dispatch_reason="missing_corroboration",
            dispatch_rank=0,
        )
        store.enqueue_jobs((independent, blanket, corroboration), enqueued_at=0)
        dispatched: list[tuple[str, str, str]] = []
        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=lambda recovery, job, _: dispatched.append(
                (recovery.connection, job.scope, job.member_id)
            ),
            classify_failure=lambda _: ("transient", None),
        )
        assert "recovered" == runner.run_one(now=1, token="first").state
        assert [("alpha", "library-0", "independent-blanket")] == dispatched
        assert "recovered" == runner.run_one(now=1, token="second").state
        assert [
            ("alpha", "library-0", "independent-blanket"),
            ("google", "library-z", "z-corroboration"),
            ("google", "library-a", "a-blanket"),
        ] == dispatched
        assert "idle" == runner.run_one(now=1, token="third").state
        store.close()


def test_same_provider_equal_member_rank_uses_stable_scope_tie() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = _due_store(
            Path(directory) / "recovery.sqlite", "library-z", "library-a"
        )
        jobs = []
        for scope in ("library-z", "library-a"):
            jobs.append(replace(_job("same-member"), scope=scope, library=scope))
        store.enqueue_jobs(
            tuple(sorted(jobs, key=lambda job: job.scope)), enqueued_at=0
        )
        claimed = store.claim_one(generation=1, now=1, token="owner")
        assert claimed is not None
        assert "library-a" == claimed.scope
        first = store.claim_job(recovery=claimed, token="owner", now=1)
        assert first is not None
        assert "library-a" == first.scope
        store.authorize_fanout(job=first, token="owner", recovery=claimed, now=1)
        second = store.claim_job(recovery=claimed, token="owner", now=1)
        assert second is not None
        assert "library-z" == second.scope
        store.close()


def test_later_high_rank_scope_waits_behind_frozen_blanket_member() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = _due_store(
            Path(directory) / "recovery.sqlite",
            "library-a",
            "library-z",
            "library-new",
        )
        first = replace(
            _job("first"),
            scope="library-z",
            library="library-z",
            dispatch_reason="missing_corroboration",
            dispatch_rank=0,
        )
        blanket = replace(
            _job("blanket"),
            scope="library-a",
            library="library-a",
            dispatch_reason="blanket_scan",
            dispatch_rank=3,
        )
        later = replace(
            _job("later"),
            scope="library-new",
            library="library-new",
            dispatch_reason="missing_corroboration",
            dispatch_rank=0,
        )
        store.enqueue_jobs((blanket, first), enqueued_at=0)
        dispatched: list[str] = []

        def retry(_, job: ProviderRecoveryJob, __) -> None:
            dispatched.append(job.member_id)
            if job.member_id == "first":
                assert 1 == store.enqueue_jobs((later,), enqueued_at=1)

        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=retry,
            classify_failure=lambda _: ("transient", None),
        )
        assert "recovered" == runner.run_one(now=1, token="first-cohort").state
        assert ["first", "blanket"] == dispatched
        assert "recovered" == runner.run_one(now=1, token="later-cohort").state
        assert ["first", "blanket", "later"] == dispatched
        store.close()


def test_provider_cohort_has_one_owner_and_reclaims_after_crash() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "recovery.sqlite"
        first_store = _due_store(path, "library-a", "library-z")
        first_store.enqueue_jobs(
            (
                replace(_job("a"), scope="library-a", library="library-a"),
                replace(_job("z"), scope="library-z", library="library-z"),
            ),
            enqueued_at=0,
        )
        claimed = first_store.claim_one(
            generation=1, now=1, token="first", lease_seconds=1
        )
        assert claimed is not None
        running = first_store.claim_job(
            recovery=claimed, token="first", now=1, lease_seconds=1
        )
        assert running is not None
        assert (
            1
            == first_store._connection.execute(
                "SELECT COUNT(*) FROM provider_recovery_health "
                "WHERE state IN ('checking','fanout')"
            ).fetchone()[0]
        )

        competing = SQLiteProviderRecoveryStore.open(path)
        assert competing.claim_one(generation=1, now=1, token="competing") is None
        first_epoch = running.cohort_epoch
        first_store.close()

        reclaimed = competing.claim_one(generation=1, now=2, token="reclaimed")
        assert reclaimed is not None
        resumed = competing.claim_job(recovery=reclaimed, token="reclaimed", now=2)
        assert resumed is not None
        assert first_epoch == resumed.cohort_epoch
        assert running.member_id == resumed.member_id
        competing.close()


def test_expired_drained_fanout_closes_before_later_cohort_dispatch() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = _due_store(Path(directory) / "recovery.sqlite")
        store.enqueue_jobs((_job("canary"),), enqueued_at=0)
        claimed = store.claim_one(generation=1, now=1, token="first")
        assert claimed is not None
        canary = store.claim_job(recovery=claimed, token="first", now=1)
        assert canary is not None
        store.authorize_fanout(job=canary, token="first", recovery=claimed, now=1)
        store.enqueue_jobs((_job("later"),), enqueued_at=2)
        store._connection.execute(
            "UPDATE provider_recovery_health SET claim_expires_at=2"
        )
        dispatched: list[str] = []
        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=lambda _, job, __: dispatched.append(job.member_id),
            classify_failure=lambda _: ("transient", None),
        )
        assert "waiting" == runner.run_one(now=2, token="second").state
        assert [] == dispatched
        assert "recovered" == runner.run_one(now=2, token="third").state
        assert ["later"] == dispatched
        store.close()


def test_generation_reconciliation_clears_live_provider_cohort() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = _due_store(Path(directory) / "recovery.sqlite")
        store.enqueue_jobs((_job("canary"), _job("sibling")), enqueued_at=0)
        claimed = store.claim_one(generation=1, now=1, token="first")
        assert claimed is not None
        canary = store.claim_job(recovery=claimed, token="first", now=1)
        assert canary is not None
        store.authorize_fanout(job=canary, token="first", recovery=claimed, now=1)
        assert 1 == store.reconcile_generation(generation=2, now=2)
        assert [(2, "waiting", None)] == store._connection.execute(
            "SELECT generation,state,cohort_cutoff FROM provider_recovery_health"
        ).fetchall()
        dispatched: list[str] = []
        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=2,
            retry=lambda _, job, __: dispatched.append(job.member_id),
            classify_failure=lambda _: ("transient", None),
        )
        assert "recovered" == runner.run_one(now=2, token="second").state
        assert ["sibling"] == dispatched
        store.close()


def test_reopen_rejects_cohort_cutoff_beyond_provider_enrollment() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "recovery.sqlite"
        store = _due_store(path)
        store.enqueue_jobs((_job("canary"),), enqueued_at=0)
        claimed = store.claim_one(generation=1, now=1, token="owner")
        assert claimed is not None
        canary = store.claim_job(recovery=claimed, token="owner", now=1)
        assert canary is not None
        store.authorize_fanout(job=canary, token="owner", recovery=claimed, now=1)
        store.close()
        connection = sqlite3.connect(path)
        connection.execute("UPDATE provider_recovery_health SET cohort_cutoff=2")
        connection.commit()
        connection.close()
        with pytest.raises(ProviderRecoveryStoreError, match="incompatible"):
            SQLiteProviderRecoveryStore.open(path)


@pytest.mark.parametrize(
    "initial_success", (True, False), ids=("recovered", "exhausted")
)
def test_changed_terminal_enrollment_reopens_but_identical_replay_does_not(
    initial_success: bool,
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = _due_store(Path(directory) / "recovery.sqlite")
        original = _job("member")
        store.enqueue_jobs((original,), enqueued_at=0)
        calls: list[str] = []

        def retry(_, job: ProviderRecoveryJob, __) -> None:
            calls.append(job.source_report_id)
            if not initial_success and len(calls) == 1:
                raise RuntimeError("terminal")

        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=retry,
            classify_failure=lambda _: ("terminal", None),
        )
        expected = "recovered" if initial_success else "exhausted"
        assert expected == runner.run_one(now=1, token="first").state
        assert 0 == store.enqueue_jobs((original,), enqueued_at=2)
        assert expected == store.snapshot()[0].state
        changed = replace(
            original,
            source_report_id="report-current",
            package_report_id="report-current",
        )
        assert 1 == store.enqueue_jobs((changed,), enqueued_at=2)
        assert "waiting" == store.snapshot()[0].state
        assert "recovered" == runner.run_one(now=2, token="second").state
        assert ["report-member", "report-current"] == calls
        store.close()


def test_nonretryable_canary_leaves_current_and_delayed_cohorts_queued() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = _due_store(Path(directory) / "recovery.sqlite")
        store.enqueue_jobs((_job("canary"), _job("current")), enqueued_at=0)

        def fail_after_delayed_enrollment(*_args: object) -> None:
            store.enqueue_jobs((_job("delayed"),), enqueued_at=1)
            raise RuntimeError("terminal")

        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=fail_after_delayed_enrollment,
            classify_failure=lambda _: ("terminal", None),
        )
        assert "waiting" == runner.run_one(now=1, token="first").state
        assert [
            ("canary", "failed"),
            ("current", "queued"),
            ("delayed", "queued"),
        ] == store._connection.execute(
            "SELECT member_id,state FROM provider_recovery_jobs ORDER BY member_id"
        ).fetchall()
        store.close()


def test_nonretryable_canary_releases_other_provider_scopes_without_egress() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = _due_store(
            Path(directory) / "recovery.sqlite", "library-a", "library-z"
        )
        canary = replace(
            _job("canary"),
            scope="library-z",
            library="library-z",
            dispatch_reason="missing_corroboration",
            dispatch_rank=0,
        )
        sibling = replace(_job("sibling"), scope="library-a", library="library-a")
        store.enqueue_jobs((sibling, canary), enqueued_at=0)
        calls: list[str] = []

        def retry(_, job: ProviderRecoveryJob, __) -> None:
            calls.append(job.member_id)
            if job.member_id == "canary":
                raise RuntimeError("terminal")

        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=retry,
            classify_failure=lambda _: ("terminal", None),
        )
        assert "exhausted" == runner.run_one(now=1, token="canary-owner").state
        assert ["canary"] == calls
        assert [
            ("library-a", "waiting", "sibling", "queued", None),
            ("library-z", "exhausted", "canary", "failed", 1),
        ] == store._connection.execute(
            "SELECT h.scope,h.state,j.member_id,j.state,j.cohort_epoch "
            "FROM provider_recovery_health h JOIN provider_recovery_jobs j "
            "USING(connection,adapter,scope) ORDER BY h.scope"
        ).fetchall()
        assert "recovered" == runner.run_one(now=1, token="sibling-owner").state
        assert ["canary", "sibling"] == calls
        store.close()


def test_cover_fetching_is_exact_and_treats_retry_wait_as_quiescent() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        store.enqueue_jobs((_job("target"),), enqueued_at=3)

        # A queued job in retry/backoff is quiescent, and adjacent report
        # identities cannot borrow its provider state.
        assert not store.cover_fetching(
            target_connection="primary",
            library="books",
            item="target",
            source_report_id="report-target",
        )
        assert not store.cover_fetching(
            target_connection="primary",
            library="books",
            item="target",
            source_report_id="report-other",
        )

        claimed = store.claim_one(generation=1, now=7_203, token="owner")
        assert claimed is not None
        assert store.cover_fetching(
            target_connection="primary",
            library="books",
            item="target",
            source_report_id="report-target",
        )
        store.close()


@pytest.mark.parametrize("enqueued_at", (True, -1, MAX_TIMESTAMP - 7_199))
def test_enqueue_rejects_noncanonical_or_overflowing_admission_timestamps(
    enqueued_at: object,
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        with pytest.raises(ProviderRecoveryStoreError, match="jobs are invalid"):
            store.enqueue_jobs((_job("invalid-time"),), enqueued_at=enqueued_at)
        store.close()


def test_enqueue_is_idempotent_and_preserves_observed_current_health() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        observed = ProviderRecovery.failed(
            connection="google",
            adapter="google_books",
            scope="books",
            generation=1,
            failure="transient",
            now=7,
            retry_after=19,
        )
        store.record(observed, occurred_at=7)
        assert 1 == store.enqueue_jobs((_job("current"),), enqueued_at=0)
        assert 0 == store.enqueue_jobs((_job("current"),), enqueued_at=0)
        assert [("waiting", 1, 26)] == store._connection.execute(
            "SELECT state,attempt,next_eligible_at FROM provider_recovery_health"
        ).fetchall()
        assert [("waiting", 1, 26, 7)] == store._connection.execute(
            "SELECT state,attempt,next_eligible_at,occurred_at "
            "FROM provider_recovery_events"
        ).fetchall()
        store.close()


def test_concurrent_enqueue_enrolls_one_scope_before_either_job_is_visible() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "recovery.sqlite"
        SQLiteProviderRecoveryStore.open(path).close()
        first = SQLiteProviderRecoveryStore.open(path)
        second = SQLiteProviderRecoveryStore.open(path)
        barrier = threading.Barrier(2)
        failures: list[BaseException] = []

        def enqueue(store: SQLiteProviderRecoveryStore, member: str) -> None:
            try:
                barrier.wait()
                store.enqueue_jobs((_job(member),), enqueued_at=0)
            except BaseException as error:
                failures.append(error)

        workers = (
            threading.Thread(target=enqueue, args=(first, "first")),
            threading.Thread(target=enqueue, args=(second, "second")),
        )
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()

        assert [] == failures
        assert [("google", "google_books", "books")] == first._connection.execute(
            "SELECT connection,adapter,scope FROM provider_recovery_members"
        ).fetchall()
        assert [("waiting", 1, 7_200)] == first._connection.execute(
            "SELECT state,attempt,next_eligible_at FROM provider_recovery_health"
        ).fetchall()
        assert [("first",), ("second",)] == first._connection.execute(
            "SELECT member_id FROM provider_recovery_jobs ORDER BY member_id"
        ).fetchall()
        first.close()
        second.close()


def test_drain_retains_a_failing_row_and_delivers_a_younger_row() -> None:
    class Repository:
        def __init__(self) -> None:
            self.pages = 0
            self.rows = [
                (
                    f"bad-{index:03d}",
                    "primary",
                    "books",
                    "one",
                    None,
                    "report-1",
                    None,
                    None,
                    "report_only",
                    100 + index,
                )
                for index in range(100)
            ] + [
                (
                    "good",
                    "primary",
                    "books",
                    "two",
                    None,
                    "report-2",
                    None,
                    None,
                    "report_only",
                    200,
                ),
            ]

        def pending_provider_recovery_outbox(self, *, limit: int, after=None):
            assert limit == 100
            self.pages += 1
            return tuple(
                sorted(
                    (
                        row
                        for row in self.rows
                        if after is None or (row[9], row[0]) > after
                    ),
                    key=lambda row: (row[9], row[0]),
                )[:limit]
            )

        def close(self) -> None:
            return None

    repository = Repository()
    activation = ProviderRecoveryActivation(
        configuration=cast(object, SimpleNamespace()),
        repository_factory=lambda: cast(ProviderRecoveryRepository, repository),
        observers=cast(object, lambda *_args: None),
        provider_resolver=cast(object, lambda *_args: None),
        pipeline=lambda: None,
        enqueue=lambda _jobs, _enqueued_at: 0,
        clock=lambda: 2,
    )

    def deliver(
        _repository: object,
        row: tuple[
            str, str, str, str, str | None, str, str | None, int | None, str, int
        ],
    ) -> None:
        if row[0].startswith("bad"):
            raise ReviewPipelineError("recovery")
        repository.rows.remove(row)

    with patch.object(activation, "_deliver", side_effect=deliver):
        result = activation.drain_outbox(cast(ProviderRecoveryRepository, repository))

    assert ("persistence",) == result
    assert [f"bad-{index:03d}" for index in range(100)] == [
        row[0] for row in repository.rows
    ]
    assert repository.pages >= 2


def test_activation_replay_after_ack_failure_keeps_recovery_state_idempotent() -> None:
    class CommitFailingConnection:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self._connection = connection

        @property
        def in_transaction(self) -> bool:
            return self._connection.in_transaction

        def execute(self, statement: str, parameters: tuple[object, ...] = ()):
            return self._connection.execute(statement, parameters)

        def commit(self) -> None:
            raise sqlite3.OperationalError("injected acknowledgement failure")

        def rollback(self) -> None:
            self._connection.rollback()

    class Repository:
        def __init__(self, delegate, package) -> None:
            self._delegate = delegate
            self._package = package

        def __getattr__(self, name: str):
            return getattr(self._delegate, name)

        def current_review_package(self, *_args):
            return self._package

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "application.sqlite3"
        repository = open_repository(path)
        report = _report()
        repository.publish(report, lambda: True)
        package = (None, report.report_id, None, None, "report_only")
        repository._recovery_outbox.enqueue(
            report.target.connection,
            report.target.library,
            report.target.item,
            package,
            7,
        )
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        activation = ProviderRecoveryActivation(
            configuration=cast(object, SimpleNamespace()),
            repository_factory=lambda: cast(ProviderRecoveryRepository, repository),
            observers=cast(object, lambda *_args: None),
            provider_resolver=cast(object, lambda *_args: None),
            pipeline=lambda: None,
            enqueue=lambda jobs, enqueued_at: store.enqueue_jobs(
                jobs, enqueued_at=enqueued_at
            ),
            clock=lambda: 7,
        )
        wrapped = Repository(repository, package)
        pending = repository.pending_provider_recovery_outbox()
        assert 1 == len(pending)
        with (
            patch.object(
                recovery_activation,
                "provider_recovery_jobs",
                return_value=(_job("outbox"),),
            ),
            patch.object(
                repository._recovery_outbox,
                "_connection",
                cast(
                    sqlite3.Connection, CommitFailingConnection(repository._connection)
                ),
            ),
        ):
            assert ("persistence",) == activation.drain_outbox(
                cast(ProviderRecoveryRepository, wrapped)
            )
        before = (
            tuple(store._connection.execute("SELECT * FROM provider_recovery_health")),
            tuple(store._connection.execute("SELECT * FROM provider_recovery_jobs")),
        )
        dispatches: list[str] = []
        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=lambda _, job, __: dispatches.append(job.member_id),
            classify_failure=lambda _: ("transient", None),
        )
        assert "idle" == runner.run_one(now=7, token="before-replay").state
        assert pending == repository.pending_provider_recovery_outbox()
        with patch.object(
            recovery_activation,
            "provider_recovery_jobs",
            return_value=(_job("outbox"),),
        ):
            assert () == activation.drain_outbox(
                cast(ProviderRecoveryRepository, wrapped)
            )
        after = (
            tuple(store._connection.execute("SELECT * FROM provider_recovery_health")),
            tuple(store._connection.execute("SELECT * FROM provider_recovery_jobs")),
        )
        assert before == after
        assert () == repository.pending_provider_recovery_outbox()
        assert "idle" == runner.run_one(now=7, token="after-replay").state
        assert [] == dispatches
        repository.close()
        store.close()


def test_activation_runner_claims_one_canary_then_fans_out_current_members_once() -> (
    None
):
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        store.record(
            ProviderRecovery.failed(
                connection="google",
                adapter="google_books",
                scope="books",
                generation=1,
                failure="transient",
                now=0,
                retry_after=1,
            ),
            occurred_at=0,
        )
        calls: list[str] = []
        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=lambda _, member, __: calls.append(member.source_report_id),
            classify_failure=lambda _: ("transient", None),
        )
        runner.enqueue(
            (_job("canary"), _job("sibling-a"), _job("sibling-b")), enqueued_at=0
        )
        result = runner.run_one(now=1, token="owner")
        assert ("recovered", 2) == (result.state, result.fanout)
        assert "idle" == runner.run_one(now=1, token="other").state
        # The second invocation is idle: the first scope was atomically closed.
        assert ["report-canary", "report-sibling-a", "report-sibling-b"] == calls
        assert "recovered" == store.snapshot()[0].state
        store.close()


def test_due_health_without_an_exact_member_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        store.record(
            ProviderRecovery.failed(
                connection="google",
                adapter="google_books",
                scope="books",
                generation=1,
                failure="transient",
                now=0,
                retry_after=1,
            ),
            occurred_at=0,
        )

        def _must_not_run(*_args: object) -> None:
            raise AssertionError("provider must not run")

        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=_must_not_run,
            classify_failure=lambda _: ("transient", None),
        )

        assert "exhausted" == runner.run_one(now=1, token="owner").state
        assert "exhausted" == store.snapshot()[0].state
        assert "recovered" not in [
            row[0]
            for row in store._connection.execute(
                "SELECT state FROM provider_recovery_events"
            )
        ]
        store.close()


def test_interrupted_fanout_does_not_repeat_the_completed_canary() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        store.record(
            ProviderRecovery.failed(
                connection="google",
                adapter="google_books",
                scope="books",
                generation=1,
                failure="transient",
                now=0,
                retry_after=1,
            ),
            occurred_at=0,
        )
        calls: list[str] = []

        def retry(_, member, __):
            calls.append(member.member_id)
            if member.member_id == "sibling-a":
                raise KeyboardInterrupt()

        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=retry,
            classify_failure=lambda _: ("transient", None),
        )
        runner.enqueue(
            (_job("canary"), _job("sibling-a"), _job("sibling-b")), enqueued_at=0
        )
        with pytest.raises(KeyboardInterrupt):
            runner.run_one(now=1, token="owner")
        # The canary job is durably complete while the provider owner lease
        # remains in fanout state. A new claimant waits for that lease to
        # expire, then continues without recontacting the canary.
        resumed: list[str] = []
        next_runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=lambda _, member, __: resumed.append(member.member_id),
            classify_failure=lambda _: ("transient", None),
        )
        assert "idle" == next_runner.run_one(now=62, token="next").state
        assert "recovered" == next_runner.run_one(now=302, token="next-later").state
        assert ["sibling-a", "sibling-b"] == resumed
        assert ["canary", "sibling-a"] == calls
        store.close()


def test_activation_runner_failure_has_zero_fanout_and_schedules_backoff() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        store.record(
            ProviderRecovery.failed(
                connection="google",
                adapter="google_books",
                scope="books",
                generation=1,
                failure="transient",
                now=0,
                retry_after=1,
            ),
            occurred_at=0,
        )
        calls: list[str] = []

        def retry(_, member, __):
            calls.append(member.source_report_id)
            raise RuntimeError("unavailable")

        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=retry,
            classify_failure=lambda _: ("transient", None),
        )
        runner.enqueue((_job("canary"), _job("sibling")), enqueued_at=0)
        assert "waiting" == runner.run_one(now=1, token="owner").state
        assert ["report-canary"] == calls
        current = store.snapshot()[0]
        assert ("waiting", 2, 14_401) == (
            current.state,
            current.attempt,
            current.next_eligible_at,
        )
        store.close()


def test_burst_throttle_defers_canary_without_spending_health_or_job_attempt() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        store.record(
            ProviderRecovery.failed(
                connection="google",
                adapter="google_books",
                scope="books",
                generation=1,
                failure="burst_throttled",
                now=0,
                retry_after=1,
            ),
            occurred_at=0,
        )

        def retry(*_args):
            raise RuntimeError("burst throttled")

        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=retry,
            classify_failure=lambda _: ("burst_throttled", 100),
        )
        runner.enqueue((_job("canary"), _job("sibling")), enqueued_at=0)
        assert "waiting" == runner.run_one(now=1, token="owner").state
        current = store.snapshot()[0]
        assert ("waiting", 0, 101) == (
            current.state,
            current.attempt,
            current.next_eligible_at,
        )
        jobs = store._connection.execute(
            "SELECT state,attempt FROM provider_recovery_jobs ORDER BY member_id"
        ).fetchall()
        assert [("queued", 0), ("queued", 0)] == jobs
        store.close()


def test_target_hold_canary_restores_exact_health_job_and_event_snapshot() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        store.record(
            ProviderRecovery.failed(
                connection="google",
                adapter="google_books",
                scope="books",
                generation=1,
                failure="transient",
                now=0,
                retry_after=1,
            ),
            occurred_at=0,
        )
        store.enqueue_jobs((_job("canary"),), enqueued_at=0)
        before = (
            tuple(store._connection.execute("SELECT * FROM provider_recovery_health")),
            tuple(store._connection.execute("SELECT * FROM provider_recovery_jobs")),
            tuple(store._connection.execute("SELECT * FROM provider_recovery_events")),
        )
        samples = 0

        def blocked_targets() -> frozenset[str]:
            nonlocal samples
            samples += 1
            return frozenset() if samples == 1 else frozenset(("primary",))

        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=lambda *_: (_ for _ in ()).throw(RuntimeError("target held")),
            classify_failure=lambda _: ("target_held", None),
            blocked_targets=blocked_targets,
        )
        assert "target_held" == runner.run_one(now=1, token="owner").state
        after = (
            tuple(store._connection.execute("SELECT * FROM provider_recovery_health")),
            tuple(store._connection.execute("SELECT * FROM provider_recovery_jobs")),
            tuple(store._connection.execute("SELECT * FROM provider_recovery_events")),
        )
        assert before == after
        assert 1 == samples
        assert (
            store._connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='provider_recovery_events_no_delete'"
            ).fetchone()
            is not None
        )
        store.close()


def test_target_hold_fanout_restores_exact_health_job_and_event_snapshot() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        store.record(
            ProviderRecovery.failed(
                connection="google",
                adapter="google_books",
                scope="books",
                generation=1,
                failure="transient",
                now=0,
                retry_after=1,
            ),
            occurred_at=0,
        )
        store.enqueue_jobs((_job("canary"), _job("sibling")), enqueued_at=0)
        claimed = store.claim_one(generation=1, now=1, token="owner")
        assert claimed is not None
        canary = store.claim_job(recovery=claimed, token="owner", now=1)
        assert canary is not None
        store.authorize_fanout(job=canary, token="owner", recovery=claimed, now=1)
        store._connection.execute(
            "UPDATE provider_recovery_health SET claim_token='expired',claim_expires_at=1"
        )
        before = (
            tuple(store._connection.execute("SELECT * FROM provider_recovery_health")),
            tuple(store._connection.execute("SELECT * FROM provider_recovery_jobs")),
            tuple(store._connection.execute("SELECT * FROM provider_recovery_events")),
        )
        samples = 0

        def blocked_targets() -> frozenset[str]:
            nonlocal samples
            samples += 1
            return frozenset() if samples == 1 else frozenset(("primary",))

        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=lambda *_: (_ for _ in ()).throw(RuntimeError("target held")),
            classify_failure=lambda _: ("target_held", None),
            blocked_targets=blocked_targets,
        )
        assert "target_held" == runner.run_one(now=2, token="owner").state
        after = (
            tuple(store._connection.execute("SELECT * FROM provider_recovery_health")),
            tuple(store._connection.execute("SELECT * FROM provider_recovery_jobs")),
            tuple(store._connection.execute("SELECT * FROM provider_recovery_events")),
        )
        assert before == after
        assert 1 == samples
        store.close()


def test_non_target_outcomes_append_checking_before_the_terminal_event() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        store.record(
            ProviderRecovery.failed(
                connection="google",
                adapter="google_books",
                scope="books",
                generation=1,
                failure="transient",
                now=0,
                retry_after=1,
            ),
            occurred_at=0,
        )
        store.enqueue_jobs((_job("canary"),), enqueued_at=0)
        success = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=lambda *_: None,
            classify_failure=lambda _: ("transient", None),
        )
        assert "recovered" == success.run_one(now=1, token="success").state
        states = [
            row[0]
            for row in store._connection.execute(
                "SELECT state FROM provider_recovery_events ORDER BY sequence"
            )
        ]
        assert ["waiting", "checking", "fanout", "recovered"] == states
        store.close()

    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        store.record(
            ProviderRecovery.failed(
                connection="google",
                adapter="google_books",
                scope="books",
                generation=1,
                failure="transient",
                now=0,
                retry_after=1,
            ),
            occurred_at=0,
        )
        store.enqueue_jobs((_job("canary"),), enqueued_at=0)
        failure = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=lambda *_: (_ for _ in ()).throw(RuntimeError("transient")),
            classify_failure=lambda _: ("transient", None),
        )
        assert "waiting" == failure.run_one(now=1, token="failure").state
        states = [
            row[0]
            for row in store._connection.execute(
                "SELECT state FROM provider_recovery_events ORDER BY sequence"
            )
        ]
        assert ["waiting", "checking", "waiting"] == states
        store.close()


def test_blocked_target_emits_nothing_then_recovers_on_a_later_tick() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        store.record(
            ProviderRecovery.failed(
                connection="google",
                adapter="google_books",
                scope="books",
                generation=1,
                failure="transient",
                now=0,
                retry_after=1,
            ),
            occurred_at=0,
        )
        store.enqueue_jobs((_job("canary"),), enqueued_at=0)
        blocked = frozenset(("primary",))
        calls: list[str] = []
        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=lambda _, job, __: calls.append(job.member_id),
            classify_failure=lambda _: ("transient", None),
            blocked_targets=lambda: blocked,
        )
        before = (
            tuple(store._connection.execute("SELECT * FROM provider_recovery_health")),
            tuple(store._connection.execute("SELECT * FROM provider_recovery_jobs")),
            tuple(store._connection.execute("SELECT * FROM provider_recovery_events")),
        )
        assert "idle" == runner.run_one(now=1, token="blocked").state
        after = (
            tuple(store._connection.execute("SELECT * FROM provider_recovery_health")),
            tuple(store._connection.execute("SELECT * FROM provider_recovery_jobs")),
            tuple(store._connection.execute("SELECT * FROM provider_recovery_events")),
        )
        assert before == after
        assert [] == calls
        blocked = frozenset()
        assert "recovered" == runner.run_one(now=2, token="recovered").state
        assert ["canary"] == calls
        store.close()


def test_empty_scope_ignores_an_unrelated_held_unstamped_job() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        store.record(
            ProviderRecovery.failed(
                connection="google",
                adapter="google_books",
                scope="library-a",
                generation=1,
                failure="transient",
                now=0,
                retry_after=1,
            ),
            occurred_at=0,
        )
        store.record(
            ProviderRecovery.failed(
                connection="google",
                adapter="google_books",
                scope="library-b",
                generation=1,
                failure="transient",
                now=0,
                retry_after=100,
            ),
            occurred_at=0,
        )
        held = replace(
            _job("held"),
            scope="library-b",
            library="library-b",
            target_connection="held-target",
        )
        store.enqueue_jobs((held,), enqueued_at=0)
        blocked = frozenset(("held-target",))
        calls: list[str] = []
        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=lambda _, member, __: calls.append(member.member_id),
            classify_failure=lambda _: ("transient", None),
            blocked_targets=lambda: blocked,
        )
        assert "exhausted" == runner.run_one(now=1, token="empty-owner").state
        assert "idle" == runner.run_one(now=2, token="finite").state
        assert [
            ("library-a", "exhausted", None),
            ("library-b", "waiting", 100),
        ] == store._connection.execute(
            "SELECT scope,state,next_eligible_at FROM provider_recovery_health ORDER BY scope"
        ).fetchall()
        assert [("held", "queued", None)] == store._connection.execute(
            "SELECT member_id,state,cohort_epoch FROM provider_recovery_jobs"
        ).fetchall()
        assert [] == calls
        assert "idle" == runner.run_one(now=100, token="still-held").state
        blocked = frozenset()
        assert "recovered" == runner.run_one(now=100, token="future").state
        assert ["held"] == calls
        store.close()


def test_blocked_active_member_releases_mixed_scope_after_open_member_exhausts() -> (
    None
):
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        store.record(
            ProviderRecovery.failed(
                connection="google",
                adapter="google_books",
                scope="books",
                generation=1,
                failure="transient",
                now=0,
                retry_after=1,
            ),
            occurred_at=0,
        )
        blocked = _job("blocked")
        exhausted = replace(_job("open"), target_connection="secondary", attempt=5)
        store.enqueue_jobs((blocked, exhausted), enqueued_at=0)
        held = frozenset(("primary",))
        calls: list[str] = []
        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=lambda _, job, __: calls.append(job.member_id),
            classify_failure=lambda _: ("transient", None),
            blocked_targets=lambda: held,
        )
        assert "idle" == runner.run_one(now=1, token="mixed").state
        assert ("waiting", 1, 1) == (
            store.snapshot()[0].state,
            store.snapshot()[0].attempt,
            store.snapshot()[0].next_eligible_at,
        )
        assert [
            ("blocked", "queued", 0),
            ("open", "failed", 5),
        ] == store._connection.execute(
            "SELECT member_id,state,attempt FROM provider_recovery_jobs ORDER BY member_id"
        ).fetchall()
        assert ["waiting"] == [
            row[0]
            for row in store._connection.execute(
                "SELECT state FROM provider_recovery_events ORDER BY sequence"
            )
        ]
        held = frozenset()
        assert "exhausted" == runner.run_one(now=2, token="released").state
        assert ["blocked"] == calls
        assert ["waiting", "checking", "fanout", "exhausted"] == [
            row[0]
            for row in store._connection.execute(
                "SELECT state FROM provider_recovery_events ORDER BY sequence"
            )
        ]
        store.close()


def test_attempt_five_job_is_never_persisted_as_attempt_six() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        store.record(
            ProviderRecovery.failed(
                connection="google",
                adapter="google_books",
                scope="books",
                generation=1,
                failure="transient",
                now=0,
                retry_after=1,
            ),
            occurred_at=0,
        )
        store.enqueue_jobs((replace(_job("canary"), attempt=5),), enqueued_at=0)

        def _must_not_dispatch(*_: object) -> None:
            raise AssertionError("exhausted member must not dispatch")

        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=_must_not_dispatch,
            classify_failure=lambda _: ("transient", None),
        )
        assert "exhausted" == runner.run_one(now=1, token="owner").state
        assert [("failed", 5)] == store._connection.execute(
            "SELECT state,attempt FROM provider_recovery_jobs"
        ).fetchall()
        assert "exhausted" == store.snapshot()[0].state
        assert "idle" == runner.run_one(now=302, token="later").state
        assert ["waiting", "checking", "exhausted"] == [
            row[0]
            for row in store._connection.execute(
                "SELECT state FROM provider_recovery_events ORDER BY sequence"
            )
        ]
        store.close()


def test_mixed_cohort_terminalizes_max_attempt_member_before_freeze() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "recovery.sqlite"
        store = _due_store(path, "library-a", "library-b")
        eligible = replace(
            _job("eligible"),
            scope="library-a",
            library="library-a",
            dispatch_reason="missing_corroboration",
            dispatch_rank=0,
        )
        exhausted = replace(
            _job("exhausted"),
            scope="library-b",
            library="library-b",
            attempt=5,
        )
        store.enqueue_jobs((eligible, exhausted), enqueued_at=0)
        calls: list[str] = []
        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=lambda _, member, __: calls.append(member.member_id),
            classify_failure=lambda _: ("transient", None),
        )
        assert "recovered" == runner.run_one(now=1, token="eligible-owner").state
        assert ["eligible"] == calls
        assert [
            ("eligible", "complete", 1, 1),
            ("exhausted", "failed", 5, None),
        ] == store._connection.execute(
            "SELECT member_id,state,attempt,cohort_epoch FROM provider_recovery_jobs "
            "ORDER BY member_id"
        ).fetchall()
        store.close()

        store = SQLiteProviderRecoveryStore.open(path)
        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=lambda _, member, __: calls.append(member.member_id),
            classify_failure=lambda _: ("transient", None),
        )
        assert "exhausted" == runner.run_one(now=2, token="empty-exhausted").state
        assert "idle" == runner.run_one(now=303, token="finite").state
        assert ["eligible"] == calls
        assert [
            ("library-a", "recovered"),
            ("library-b", "exhausted"),
        ] == store._connection.execute(
            "SELECT scope,state FROM provider_recovery_health ORDER BY scope"
        ).fetchall()
        store.close()


def test_credential_failure_is_dormant_not_requeued() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        store.record(
            ProviderRecovery.failed(
                connection="google",
                adapter="google_books",
                scope="books",
                generation=1,
                failure="transient",
                now=0,
                retry_after=1,
            ),
            occurred_at=0,
        )
        store.enqueue_jobs((_job("canary"),), enqueued_at=0)
        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=lambda *_: (_ for _ in ()).throw(CredentialError("credential")),
            classify_failure=lambda error: (
                "authentication" if isinstance(error, CredentialError) else "transient",
                None,
            ),
        )
        assert "waiting" == runner.run_one(now=1, token="owner").state
        assert "dormant" == store.snapshot()[0].state
        assert store.claim_one(generation=1, now=100_000, token="later") is None
        store.close()


def test_terminal_successor_failure_exhausts_without_requeue() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        store.record(
            ProviderRecovery.failed(
                connection="google",
                adapter="google_books",
                scope="books",
                generation=1,
                failure="transient",
                now=0,
                retry_after=1,
            ),
            occurred_at=0,
        )
        store.enqueue_jobs((_job("canary"), _job("sibling")), enqueued_at=0)
        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=lambda *_: (_ for _ in ()).throw(
                ProviderRetrySuccessorError("terminal")
            ),
            classify_failure=lambda error: (
                "terminal"
                if isinstance(error, ProviderRetrySuccessorError)
                else "transient",
                None,
            ),
        )
        assert "waiting" == runner.run_one(now=1, token="owner").state
        assert "waiting" == store.snapshot()[0].state
        assert {"failed", "queued"} == {
            row[0]
            for row in store._connection.execute(
                "SELECT state FROM provider_recovery_jobs"
            )
        }
        store.close()


def test_reenqueue_refreshes_current_package_and_invalidates_running_claim() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        store.record(
            ProviderRecovery.failed(
                connection="google",
                adapter="google_books",
                scope="books",
                generation=1,
                failure="transient",
                now=0,
                retry_after=1,
            ),
            occurred_at=0,
        )
        original = _job("member")
        current = replace(
            original,
            file_identity="current-file",
            source_report_id="current-report",
            package_report_id="current-package",
            lane_ids='["current"]',
        )
        assert 1 == store.enqueue_jobs((original,), enqueued_at=0)
        claimed = store.claim_one(generation=1, now=1, token="owner")
        assert claimed is not None
        running = store.claim_job(recovery=claimed, token="owner", now=1)
        assert running is not None
        assert 0 == store.enqueue_jobs((original,), enqueued_at=0)
        assert ("running", 1, "owner") == store._connection.execute(
            "SELECT state,attempt,claim_token FROM provider_recovery_jobs"
        ).fetchone()
        assert 1 == store.enqueue_jobs((current,), enqueued_at=0)
        assert (
            "current-file",
            "current-report",
            "current-package",
            '["current"]',
            "queued",
            0,
        ) == store._connection.execute(
            "SELECT file_identity,source_report_id,package_report_id,lane_ids,state,attempt "
            "FROM provider_recovery_jobs"
        ).fetchone()
        store.close()
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        with pytest.raises(ProviderRecoveryStoreError):
            store.complete_job(job=running, token="owner", now=1, success=True)
        missing = store.complete_missing_jobs(token="owner", recovery=claimed, now=1)
        assert missing is not None
        assert "waiting" == missing.state
        claimed = store.claim_one(generation=1, now=1, token="refreshed-owner")
        assert claimed is not None
        refreshed = store.claim_job(recovery=claimed, token="refreshed-owner", now=1)
        assert refreshed is not None
        store.complete_job(job=refreshed, token="refreshed-owner", now=1, success=True)
        assert 0 == store.enqueue_jobs((current,), enqueued_at=0)
        assert ("complete", 1, None) == store._connection.execute(
            "SELECT state,attempt,claim_token FROM provider_recovery_jobs"
        ).fetchone()
        store.close()


def test_rejected_scope_claim_rolls_back_before_valid_claimant() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        store.record(
            ProviderRecovery.failed(
                connection="google",
                adapter="google_books",
                scope="books",
                generation=1,
                failure="transient",
                now=0,
                retry_after=1,
            ),
            occurred_at=0,
        )
        store.enqueue_jobs((_job("canary"),), enqueued_at=0)
        claimed = store.claim_one(generation=1, now=1, token="owner")
        assert claimed is not None
        with pytest.raises(ProviderRecoveryStoreError):
            store.claim_job(recovery=claimed, token="other", now=1)
        assert not store._connection.in_transaction
        assert store.claim_job(recovery=claimed, token="owner", now=1) is not None
        store.close()


def test_generation_reconciliation_keeps_current_duplicate_member() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        store.record(
            ProviderRecovery.failed(
                connection="google",
                adapter="google_books",
                scope="books",
                generation=1,
                failure="transient",
                now=0,
                retry_after=1,
            ),
            occurred_at=0,
        )
        stale = _job("member")
        current = replace(
            stale,
            generation=2,
            source_report_id="current-report",
            package_report_id="current-package",
        )
        store.enqueue_jobs((stale,), enqueued_at=0)
        store.enqueue_jobs((current,), enqueued_at=0)
        assert 1 == store.reconcile_generation(generation=2, now=2)
        assert [(2, "current-report", "current-package")] == store._connection.execute(
            "SELECT generation,source_report_id,package_report_id "
            "FROM provider_recovery_jobs"
        ).fetchall()
        assert "waiting" == store.snapshot()[0].state
        store.close()


def test_shared_failure_during_fanout_stops_uncontacted_siblings_and_reopens_scope() -> (
    None
):
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        store.record(
            ProviderRecovery.failed(
                connection="google",
                adapter="google_books",
                scope="books",
                generation=1,
                failure="transient",
                now=0,
                retry_after=1,
            ),
            occurred_at=0,
        )
        calls: list[str] = []

        def retry(_, member, __):
            calls.append(member.member_id)
            if member.member_id == "sibling-a":
                raise RuntimeError("rate limited")

        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=retry,
            classify_failure=lambda _: ("rate_limited", 19),
        )
        runner.enqueue(
            (_job("canary"), _job("sibling-a"), _job("sibling-b")), enqueued_at=0
        )
        result = runner.run_one(now=1, token="owner")
        assert ("waiting", 0) == (result.state, result.fanout)
        assert ["canary", "sibling-a"] == calls
        health = store.snapshot()[0]
        assert ("waiting", 2, 20) == (
            health.state,
            health.attempt,
            health.next_eligible_at,
        )
        states = dict(
            store._connection.execute(
                "SELECT member_id,state FROM provider_recovery_jobs"
            ).fetchall()
        )
        assert {
            "canary": "complete",
            "sibling-a": "queued",
            "sibling-b": "queued",
        } == states
        store.close()


def test_shared_failure_keeps_late_unstamped_work_in_another_scope_reachable() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = _due_store(
            Path(directory) / "recovery.sqlite", "library-a", "library-b"
        )
        first = replace(
            _job("first"),
            scope="library-a",
            library="library-a",
            dispatch_reason="missing_corroboration",
            dispatch_rank=0,
        )
        failing = replace(_job("failing"), scope="library-b", library="library-b")
        late = replace(_job("late"), scope="library-a", library="library-a")
        assert 2 == store.enqueue_jobs((first, failing), enqueued_at=0)
        calls: list[str] = []
        interrupted = False

        def retry(_, member, __):
            nonlocal interrupted
            calls.append(member.member_id)
            if member.member_id == "first":
                # Claiming the provider cohort fixed its finite membership
                # before this independently published package arrived.
                assert 1 == store.enqueue_jobs((late,), enqueued_at=1)
            if member.member_id == "failing" and not interrupted:
                interrupted = True
                raise RuntimeError("rate limited")

        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=retry,
            classify_failure=lambda _: ("rate_limited", 19),
        )
        assert "waiting" == runner.run_one(now=1, token="owner").state
        assert ["first", "failing"] == calls
        assert [
            ("library-a", "waiting", 20, "late", "queued", None),
            ("library-b", "waiting", 20, "failing", "queued", None),
        ] == store._connection.execute(
            "SELECT h.scope,h.state,h.next_eligible_at,j.member_id,j.state,j.cohort_epoch "
            "FROM provider_recovery_health h JOIN provider_recovery_jobs j "
            "USING(connection,adapter,scope) WHERE j.state='queued' ORDER BY h.scope"
        ).fetchall()

        calls.clear()
        assert "recovered" == runner.run_one(now=20, token="next").state
        assert ["failing", "late"] == calls
        assert [
            ("library-a", "recovered"),
            ("library-b", "recovered"),
        ] == store._connection.execute(
            "SELECT scope,state FROM provider_recovery_health ORDER BY scope"
        ).fetchall()
        store.close()


def test_participating_scope_keeps_newer_authoritative_quota_deadline() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = _due_store(
            Path(directory) / "recovery.sqlite", "library-a", "library-b"
        )
        first = replace(
            _job("first"),
            scope="library-a",
            library="library-a",
            dispatch_reason="missing_corroboration",
            dispatch_rank=0,
        )
        second = replace(_job("second"), scope="library-b", library="library-b")
        store.enqueue_jobs((first, second), enqueued_at=0)
        calls: list[str] = []
        observed_health: list[tuple[object, ...]] = []

        def retry(_, member, __):
            calls.append(member.member_id)
            if member.member_id == "second":
                observed_health.append(
                    tuple(
                        store._connection.execute(
                            "SELECT scope,state,next_eligible_at FROM provider_recovery_health ORDER BY scope"
                        ).fetchall()
                    )
                )
                store.observe_failure(
                    connection="google",
                    adapter="google_books",
                    scope="library-b",
                    generation=1,
                    failure="quota_exhausted",
                    now=1,
                    retry_after=100,
                )
                observed_health.append(
                    tuple(
                        store._connection.execute(
                            "SELECT scope,state,next_eligible_at FROM provider_recovery_health ORDER BY scope"
                        ).fetchall()
                    )
                )

        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=retry,
            classify_failure=lambda _: ("transient", None),
        )
        assert "recovered" == runner.run_one(now=1, token="owner").state
        assert ["first", "second"] == calls
        assert [
            (
                ("library-a", "fanout", None),
                ("library-b", "waiting", 1),
            ),
            (
                ("library-a", "fanout", None),
                ("library-b", "waiting", 101),
            ),
        ] == observed_health
        assert [
            ("library-a", "recovered", None),
            ("library-b", "waiting", 101),
        ] == store._connection.execute(
            "SELECT scope,state,next_eligible_at FROM provider_recovery_health ORDER BY scope"
        ).fetchall()
        assert "idle" == runner.run_one(now=100, token="too-soon").state
        assert ["first", "second"] == calls
        store.close()


def test_canary_failure_defers_a_new_same_provider_scope() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = _due_store(Path(directory) / "recovery.sqlite", "library-a")
        first = replace(
            _job("first"),
            scope="library-a",
            library="library-a",
            dispatch_reason="missing_corroboration",
            dispatch_rank=0,
        )
        late = replace(_job("late"), scope="library-c", library="library-c")
        store.enqueue_jobs((first,), enqueued_at=0)
        calls: list[str] = []
        interrupted = False

        def retry(_, member, __):
            nonlocal interrupted
            calls.append(member.member_id)
            if not interrupted:
                interrupted = True
                store.enqueue_jobs((late,), enqueued_at=1)
                raise RuntimeError("rate limited")

        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=retry,
            classify_failure=lambda _: ("rate_limited", 19),
        )
        assert "waiting" == runner.run_one(now=1, token="owner").state
        assert [
            ("library-a", "waiting", 20),
            ("library-c", "waiting", 20),
        ] == store._connection.execute(
            "SELECT scope,state,next_eligible_at FROM provider_recovery_health ORDER BY scope"
        ).fetchall()
        assert [("late", None)] == store._connection.execute(
            "SELECT member_id,cohort_epoch FROM provider_recovery_jobs WHERE member_id='late'"
        ).fetchall()
        assert "idle" == runner.run_one(now=19, token="too-soon").state
        assert ["first"] == calls
        store.close()


def test_canary_failure_preserves_a_queued_dormant_auth_scope() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = _due_store(Path(directory) / "recovery.sqlite", "library-a")
        first = replace(
            _job("first"),
            scope="library-a",
            library="library-a",
            dispatch_reason="missing_corroboration",
            dispatch_rank=0,
        )
        dormant = replace(_job("dormant"), scope="library-d", library="library-d")
        store.observe_failure(
            connection="google",
            adapter="google_books",
            scope="library-d",
            generation=1,
            failure="authentication",
            now=0,
        )
        store.enqueue_jobs((first, dormant), enqueued_at=0)
        calls: list[str] = []
        interrupted = False

        def retry(_, member, __):
            nonlocal interrupted
            calls.append(member.member_id)
            if not interrupted:
                interrupted = True
                raise RuntimeError("rate limited")

        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=retry,
            classify_failure=lambda _: ("rate_limited", 19),
        )
        assert "waiting" == runner.run_one(now=1, token="owner").state
        assert [
            ("library-a", "waiting", 20),
            ("library-d", "dormant", None),
        ] == store._connection.execute(
            "SELECT scope,state,next_eligible_at FROM provider_recovery_health ORDER BY scope"
        ).fetchall()
        assert [("dormant", "queued", None)] == store._connection.execute(
            "SELECT member_id,state,cohort_epoch FROM provider_recovery_jobs "
            "WHERE member_id='dormant'"
        ).fetchall()
        assert "recovered" == runner.run_one(now=20, token="next").state
        assert ["first", "first"] == calls
        assert "idle" == runner.run_one(now=100_000, token="same-generation").state
        assert ["first", "first"] == calls
        store.close()


def test_canary_failure_preserves_a_frozen_scope_that_becomes_dormant() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = _due_store(
            Path(directory) / "recovery.sqlite", "library-a", "library-b"
        )
        first = replace(
            _job("first"),
            scope="library-a",
            library="library-a",
            dispatch_reason="missing_corroboration",
            dispatch_rank=0,
        )
        dormant = replace(_job("dormant"), scope="library-b", library="library-b")
        store.enqueue_jobs((first, dormant), enqueued_at=0)
        calls: list[str] = []
        interrupted = False

        def retry(_, member, __):
            nonlocal interrupted
            calls.append(member.member_id)
            if not interrupted:
                interrupted = True
                frozen = store._connection.execute(
                    "SELECT h.state,j.cohort_epoch FROM provider_recovery_health h "
                    "JOIN provider_recovery_jobs j USING(connection,adapter,scope) "
                    "WHERE h.scope='library-b'"
                ).fetchone()
                assert frozen == ("waiting", 1)
                store.observe_failure(
                    connection="google",
                    adapter="google_books",
                    scope="library-b",
                    generation=1,
                    failure="authentication",
                    now=1,
                )
                raise RuntimeError("rate limited")

        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=retry,
            classify_failure=lambda _: ("rate_limited", 19),
        )
        assert "waiting" == runner.run_one(now=1, token="owner").state
        assert [
            ("library-a", "waiting", 20),
            ("library-b", "dormant", None),
        ] == store._connection.execute(
            "SELECT scope,state,next_eligible_at FROM provider_recovery_health ORDER BY scope"
        ).fetchall()
        assert [("dormant", "queued", None)] == store._connection.execute(
            "SELECT member_id,state,cohort_epoch FROM provider_recovery_jobs "
            "WHERE member_id='dormant'"
        ).fetchall()
        assert "recovered" == runner.run_one(now=20, token="next").state
        assert ["first", "first"] == calls
        assert "idle" == runner.run_one(now=100_000, token="same-generation").state
        assert ["first", "first"] == calls
        store.close()


def test_fanout_failure_defers_a_new_same_provider_scope() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = _due_store(
            Path(directory) / "recovery.sqlite", "library-a", "library-b"
        )
        first = replace(
            _job("first"),
            scope="library-a",
            library="library-a",
            dispatch_reason="missing_corroboration",
            dispatch_rank=0,
        )
        failing = replace(_job("failing"), scope="library-b", library="library-b")
        late = replace(_job("late"), scope="library-c", library="library-c")
        store.enqueue_jobs((first, failing), enqueued_at=0)
        calls: list[str] = []

        def retry(_, member, __):
            calls.append(member.member_id)
            if member.member_id == "failing":
                store.enqueue_jobs((late,), enqueued_at=1)
                raise RuntimeError("rate limited")

        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=retry,
            classify_failure=lambda _: ("rate_limited", 19),
        )
        assert "waiting" == runner.run_one(now=1, token="owner").state
        assert ["first", "failing"] == calls
        assert [
            ("library-a", "recovered", None),
            ("library-b", "waiting", 20),
            ("library-c", "waiting", 20),
        ] == store._connection.execute(
            "SELECT scope,state,next_eligible_at FROM provider_recovery_health ORDER BY scope"
        ).fetchall()
        assert [("late", None)] == store._connection.execute(
            "SELECT member_id,cohort_epoch FROM provider_recovery_jobs WHERE member_id='late'"
        ).fetchall()
        assert "idle" == runner.run_one(now=19, token="too-soon").state
        assert ["first", "failing"] == calls
        store.close()


def test_fanout_failure_preserves_a_queued_dormant_auth_scope() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = _due_store(
            Path(directory) / "recovery.sqlite", "library-a", "library-b"
        )
        first = replace(
            _job("first"),
            scope="library-a",
            library="library-a",
            dispatch_reason="missing_corroboration",
            dispatch_rank=0,
        )
        failing = replace(_job("failing"), scope="library-b", library="library-b")
        dormant = replace(_job("dormant"), scope="library-d", library="library-d")
        store.observe_failure(
            connection="google",
            adapter="google_books",
            scope="library-d",
            generation=1,
            failure="authorization",
            now=0,
        )
        store.enqueue_jobs((first, failing, dormant), enqueued_at=0)
        calls: list[str] = []
        interrupted = False

        def retry(_, member, __):
            nonlocal interrupted
            calls.append(member.member_id)
            if member.member_id == "failing" and not interrupted:
                interrupted = True
                raise RuntimeError("rate limited")

        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=retry,
            classify_failure=lambda _: ("rate_limited", 19),
        )
        assert "waiting" == runner.run_one(now=1, token="owner").state
        assert ["first", "failing"] == calls
        assert [
            ("library-a", "recovered", None),
            ("library-b", "waiting", 20),
            ("library-d", "dormant", None),
        ] == store._connection.execute(
            "SELECT scope,state,next_eligible_at FROM provider_recovery_health ORDER BY scope"
        ).fetchall()
        assert [("dormant", "queued", None)] == store._connection.execute(
            "SELECT member_id,state,cohort_epoch FROM provider_recovery_jobs "
            "WHERE member_id='dormant'"
        ).fetchall()
        assert "recovered" == runner.run_one(now=20, token="next").state
        assert ["first", "failing", "failing"] == calls
        assert "idle" == runner.run_one(now=100_000, token="same-generation").state
        assert ["first", "failing", "failing"] == calls
        store.close()


def test_fanout_failure_preserves_a_frozen_scope_that_becomes_dormant() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = _due_store(
            Path(directory) / "recovery.sqlite", "library-a", "library-b"
        )
        first = replace(
            _job("first"),
            scope="library-a",
            library="library-a",
            dispatch_reason="missing_corroboration",
            dispatch_rank=0,
        )
        failing = replace(
            _job("failing"),
            scope="library-a",
            library="library-a",
            dispatch_reason="zero_provider_evidence",
            dispatch_rank=1,
        )
        dormant = replace(_job("dormant"), scope="library-b", library="library-b")
        store.enqueue_jobs((failing, first, dormant), enqueued_at=0)
        calls: list[str] = []
        interrupted = False

        def retry(_, member, __):
            nonlocal interrupted
            calls.append(member.member_id)
            if member.member_id == "failing" and not interrupted:
                interrupted = True
                frozen = store._connection.execute(
                    "SELECT h.state,j.cohort_epoch FROM provider_recovery_health h "
                    "JOIN provider_recovery_jobs j USING(connection,adapter,scope) "
                    "WHERE h.scope='library-b'"
                ).fetchone()
                assert frozen == ("waiting", 1)
                store.observe_failure(
                    connection="google",
                    adapter="google_books",
                    scope="library-b",
                    generation=1,
                    failure="authorization",
                    now=1,
                )
                raise RuntimeError("rate limited")

        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=retry,
            classify_failure=lambda _: ("rate_limited", 19),
        )
        assert "waiting" == runner.run_one(now=1, token="owner").state
        assert ["first", "failing"] == calls
        assert [
            ("library-a", "waiting", 20),
            ("library-b", "dormant", None),
        ] == store._connection.execute(
            "SELECT scope,state,next_eligible_at FROM provider_recovery_health ORDER BY scope"
        ).fetchall()
        assert [("dormant", "queued", None)] == store._connection.execute(
            "SELECT member_id,state,cohort_epoch FROM provider_recovery_jobs "
            "WHERE member_id='dormant'"
        ).fetchall()
        assert "recovered" == runner.run_one(now=20, token="next").state
        assert ["first", "failing", "failing"] == calls
        assert "idle" == runner.run_one(now=100_000, token="same-generation").state
        assert ["first", "failing", "failing"] == calls
        store.close()


def test_fanout_skips_member_that_becomes_dormant_after_freeze() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = _due_store(
            Path(directory) / "recovery.sqlite", "library-a", "library-b"
        )
        first = replace(
            _job("first"),
            scope="library-a",
            library="library-a",
            dispatch_reason="missing_corroboration",
            dispatch_rank=0,
        )
        dormant = replace(_job("dormant"), scope="library-b", library="library-b")
        store.enqueue_jobs((first, dormant), enqueued_at=0)
        calls: list[str] = []

        def retry(_, member, __):
            calls.append(member.member_id)
            if member.member_id == "first":
                assert [("library-b", "waiting", 1)] == store._connection.execute(
                    "SELECT h.scope,h.state,j.cohort_epoch FROM provider_recovery_health h "
                    "JOIN provider_recovery_jobs j USING(connection,adapter,scope) "
                    "WHERE j.member_id='dormant'"
                ).fetchall()
                store.observe_failure(
                    connection="google",
                    adapter="google_books",
                    scope="library-b",
                    generation=1,
                    failure="authentication",
                    now=1,
                )

        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=retry,
            classify_failure=lambda _: ("transient", None),
        )
        assert "recovered" == runner.run_one(now=1, token="owner").state
        assert ["first"] == calls
        assert [("library-b", "dormant", None)] == store._connection.execute(
            "SELECT scope,state,next_eligible_at FROM provider_recovery_health "
            "WHERE scope='library-b'"
        ).fetchall()
        assert [("dormant", "queued", 0, None)] == store._connection.execute(
            "SELECT member_id,state,attempt,cohort_epoch FROM provider_recovery_jobs "
            "WHERE member_id='dormant'"
        ).fetchall()
        assert "idle" == runner.run_one(now=100_000, token="same-generation").state
        assert ["first"] == calls
        store.close()


def test_fanout_defers_member_with_a_newer_scope_deadline_after_freeze() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = _due_store(
            Path(directory) / "recovery.sqlite", "library-a", "library-b"
        )
        first = replace(
            _job("first"),
            scope="library-a",
            library="library-a",
            dispatch_reason="missing_corroboration",
            dispatch_rank=0,
        )
        deferred = replace(_job("deferred"), scope="library-b", library="library-b")
        store.enqueue_jobs((first, deferred), enqueued_at=0)
        calls: list[str] = []

        def retry(_, member, __):
            calls.append(member.member_id)
            if member.member_id == "first":
                store.observe_failure(
                    connection="google",
                    adapter="google_books",
                    scope="library-b",
                    generation=1,
                    failure="quota_exhausted",
                    now=1,
                    retry_after=100,
                )

        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=retry,
            classify_failure=lambda _: ("transient", None),
        )
        assert "recovered" == runner.run_one(now=1, token="owner").state
        assert ["first"] == calls
        assert [("library-b", "waiting", 101)] == store._connection.execute(
            "SELECT scope,state,next_eligible_at FROM provider_recovery_health "
            "WHERE scope='library-b'"
        ).fetchall()
        assert [("deferred", "queued", 0, None)] == store._connection.execute(
            "SELECT member_id,state,attempt,cohort_epoch FROM provider_recovery_jobs "
            "WHERE member_id='deferred'"
        ).fetchall()
        assert "idle" == runner.run_one(now=100, token="too-soon").state
        assert ["first"] == calls
        assert "recovered" == runner.run_one(now=101, token="future").state
        assert ["first", "deferred"] == calls
        store.close()


def test_later_successful_cohort_keeps_an_earlier_terminal_lane_exhausted() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = _due_store(Path(directory) / "recovery.sqlite")
        store.enqueue_jobs((_job("terminal"),), enqueued_at=0)
        calls: list[str] = []

        def retry(_, member, __):
            calls.append(member.member_id)
            if member.member_id == "terminal":
                raise RuntimeError("terminal")

        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=retry,
            classify_failure=lambda _: ("terminal", None),
        )
        assert "exhausted" == runner.run_one(now=1, token="first").state
        store.enqueue_jobs((_job("later"),), enqueued_at=2)
        eligible = store.snapshot()[0].next_eligible_at
        assert eligible is not None
        assert "exhausted" == runner.run_one(now=eligible, token="second").state
        assert ["terminal", "later"] == calls
        assert [("terminal", "failed"), ("later", "complete")] == (
            store._connection.execute(
                "SELECT member_id,state FROM provider_recovery_jobs ORDER BY member_id DESC"
            ).fetchall()
        )
        assert "exhausted" == store.snapshot()[0].state
        store.close()


def test_burst_throttle_during_fanout_defers_without_spending_attempts() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        store.record(
            ProviderRecovery.failed(
                connection="google",
                adapter="google_books",
                scope="books",
                generation=1,
                failure="transient",
                now=0,
                retry_after=1,
            ),
            occurred_at=0,
        )

        def retry(_, member, __):
            if member.member_id == "sibling-a":
                raise RuntimeError("burst throttled")

        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=retry,
            classify_failure=lambda _: ("burst_throttled", 100),
        )
        runner.enqueue(
            (_job("canary"), _job("sibling-a"), _job("sibling-b")), enqueued_at=0
        )
        assert "waiting" == runner.run_one(now=1, token="owner").state
        health = store.snapshot()[0]
        assert ("waiting", 1, 101) == (
            health.state,
            health.attempt,
            health.next_eligible_at,
        )
        jobs = store._connection.execute(
            "SELECT member_id,state,attempt FROM provider_recovery_jobs ORDER BY member_id"
        ).fetchall()
        assert [
            ("canary", "complete", 1),
            ("sibling-a", "queued", 0),
            ("sibling-b", "queued", 0),
        ] == jobs
        store.close()


def test_nonretryable_canary_exhausts_scope_without_sibling_egress() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        store.record(
            ProviderRecovery.failed(
                connection="google",
                adapter="google_books",
                scope="books",
                generation=1,
                failure="transient",
                now=0,
                retry_after=1,
            ),
            occurred_at=0,
        )
        calls: list[str] = []

        def retry(_, member, __):
            calls.append(member.member_id)
            raise RuntimeError("disabled")

        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=retry,
            classify_failure=lambda _: ("disabled", None),
        )
        runner.enqueue((_job("canary"), _job("sibling")), enqueued_at=0)
        assert "waiting" == runner.run_one(now=1, token="owner").state
        assert ["canary"] == calls
        assert "waiting" == store.snapshot()[0].state
        assert {"failed", "queued"} == {
            row[0]
            for row in store._connection.execute(
                "SELECT state FROM provider_recovery_jobs"
            ).fetchall()
        }
        store.close()


def test_bootstrap_preserves_unrelated_objects_and_rejects_partial_or_rolled_back_schema() -> (
    None
):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "recovery.sqlite"
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE unrelated(value TEXT) STRICT")
        connection.close()
        store = SQLiteProviderRecoveryStore.open(path)
        assert ("unrelated",) == store._connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='unrelated'"
        ).fetchone()
        store.close()

        partial = Path(directory) / "partial.sqlite"
        sqlite3.connect(partial).execute(
            "CREATE TABLE provider_recovery_health(value TEXT)"
        ).connection.close()
        with pytest.raises(ProviderRecoveryStoreError):
            SQLiteProviderRecoveryStore.open(partial)

        rolled_back = Path(directory) / "rolled-back.sqlite"
        with patch.object(
            recovery_persistence,
            "_CREATE",
            recovery_persistence._CREATE + ("INVALID SQL",),
        ):
            with pytest.raises(ProviderRecoveryStoreError):
                SQLiteProviderRecoveryStore.open(rolled_back)
        connection = sqlite3.connect(rolled_back)
        assert (
            []
            == connection.execute(
                "SELECT name FROM sqlite_master WHERE name LIKE 'provider_recovery_%'"
            ).fetchall()
        )
        connection.close()


def test_bootstrap_rejects_pre_cutoff_recovery_schema_for_a_clean_rebuild() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "pre-cutoff.sqlite"
        connection = sqlite3.connect(path)
        connection.execute(recovery_persistence._MEMBERS)
        connection.execute(
            recovery_persistence._HEALTH.replace(
                "CHECK(cohort_cutoff IS NULL OR cohort_cutoff>=0)",
                "CHECK(cohort_cutoff IS NULL OR cohort_cutoff>0)",
            )
        )
        for statement in recovery_persistence._CREATE[2:]:
            connection.execute(statement)
        connection.commit()
        connection.close()
        with pytest.raises(ProviderRecoveryStoreError, match="incompatible"):
            SQLiteProviderRecoveryStore.open(path)


def test_shared_store_serializes_worker_observation_and_closes_after_shutdown() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        failures: list[Exception] = []

        def observe() -> None:
            try:
                store.observe_failure(
                    connection="primary",
                    adapter="hardcover",
                    scope="books",
                    generation=1,
                    failure="authentication",
                    now=0,
                )
                store.activity_snapshot()
            except Exception as error:
                failures.append(error)

        worker = threading.Thread(target=observe)
        worker.start()
        worker.join()
        assert [] == failures
        store.reconcile_generation(generation=2, now=1)
        assert store.claim_one(generation=2, now=1, token="owner") is None
        assert "dormant" == store.snapshot()[0].state
        store.close()
        with pytest.raises(ProviderRecoveryStoreError):
            store.activity_snapshot()


@pytest.mark.parametrize(
    "failure",
    ("authentication", "authorization"),
    ids=("authentication", "authorization"),
)
def test_authentication_and_authorization_jobs_wake_only_with_generation(
    failure,
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        store.observe_failure(
            connection="google",
            adapter="google_books",
            scope="books",
            generation=1,
            failure=failure,
            now=0,
        )
        store.enqueue_jobs((_job("canary"),), enqueued_at=0)
        assert store.claim_one(generation=1, now=10_000, token="too-soon") is None
        assert 1 == store.reconcile_generation(generation=2, now=20)
        calls: list[str] = []
        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=2,
            retry=lambda _, member, __: calls.append(member.member_id),
            classify_failure=lambda _: ("transient", None),
        )
        assert "recovered" == runner.run_one(now=20).state
        assert ["canary"] == calls
        assert [(2, "complete")] == store._connection.execute(
            "SELECT generation,state FROM provider_recovery_jobs"
        ).fetchall()
        store.close()


@pytest.mark.parametrize(
    "status",
    (ProviderStatus.AUTHENTICATION, ProviderStatus.AUTHORIZATION),
    ids=("authentication", "authorization"),
)
def test_terminal_authentication_and_authorization_lanes_create_exact_jobs(
    status,
) -> None:
    configuration = SimpleNamespace(
        providers=(SimpleNamespace(name="google", adapter="google_books"),),
        provider_recovery_generation=1,
    )
    with tempfile.TemporaryDirectory() as directory:
        report = SimpleNamespace(
            report_id="report-auth",
            target=SimpleNamespace(
                connection="primary", library="books", item="item-1"
            ),
            file=SimpleNamespace(identity="file-1"),
            providers=(
                SimpleNamespace(
                    provider="google",
                    lanes=(SimpleNamespace(identity="isbn-lane", status=status),),
                ),
            ),
        )
        jobs = _provider_recovery_jobs(
            report,
            package=(None, "report-auth", None, None, "report_only"),
            configuration=configuration,
            scope="books",
        )
        assert 1 == len(jobs)
        assert '["isbn-lane"]' == jobs[0].lane_ids
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        store.observe_failure(
            connection="google",
            adapter="google_books",
            scope="books",
            generation=1,
            failure=status.value,
            now=0,
        )
        assert 1 == store.enqueue_jobs(jobs, enqueued_at=0)
        assert [
            ("report-auth", '["isbn-lane"]', "queued")
        ] == store._connection.execute(
            "SELECT source_report_id,lane_ids,state FROM provider_recovery_jobs"
        ).fetchall()
        store.close()


def test_stale_canary_retires_only_itself_before_current_canary() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        store.record(
            ProviderRecovery.failed(
                connection="google",
                adapter="google_books",
                scope="books",
                generation=1,
                failure="transient",
                now=0,
                retry_after=1,
            ),
            occurred_at=0,
        )
        calls: list[str] = []

        def retry(_, member, __):
            calls.append(member.member_id)
            if member.member_id == "a-stale":
                raise RuntimeError("stale")

        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=retry,
            classify_failure=lambda error: (
                "stale" if str(error) == "stale" else "transient",
                None,
            ),
        )
        runner.enqueue((_job("a-stale"), _job("b-current")), enqueued_at=0)
        result = runner.run_one(now=1, token="owner")
        assert ("recovered", 0) == (result.state, result.fanout)
        assert ["a-stale", "b-current"] == calls
        assert [
            ("a-stale", "stale"),
            ("b-current", "complete"),
        ] == store._connection.execute(
            "SELECT member_id,state FROM provider_recovery_jobs ORDER BY member_id"
        ).fetchall()
        store.close()


def test_stage_renewal_prevents_manual_reclaim_beyond_initial_lease() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        store.record(
            ProviderRecovery.failed(
                connection="google",
                adapter="google_books",
                scope="books",
                generation=1,
                failure="transient",
                now=0,
                retry_after=1,
            ),
            occurred_at=0,
        )
        store.enqueue_jobs((_job("canary"),), enqueued_at=0)
        clock = [1]
        competing: list[str] = []
        competitor = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=lambda _, member, __: competing.append(member.member_id),
            classify_failure=lambda _: ("transient", None),
            clock=lambda: clock[0],
        )

        def retry(_, member, renew):
            assert "canary" == member.member_id
            clock[0] = 250
            renew()
            clock[0] = 301
            assert "idle" == competitor.run_one(now=301).state
            clock[0] = 400
            renew()

        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=1,
            retry=retry,
            classify_failure=lambda _: ("transient", None),
            clock=lambda: clock[0],
        )
        assert "recovered" == runner.run_one(now=1, token="owner").state
        assert [] == competing
        store.close()


def test_transient_retry_and_generation_wake_are_closed() -> None:
    recovery = ProviderRecovery.failed(
        connection="primary",
        adapter="hardcover",
        scope="books",
        generation=1,
        failure="transient",
        now=10,
    )
    assert ("waiting", 1, 7210) == (
        recovery.state,
        recovery.attempt,
        recovery.next_eligible_at,
    )
    assert 14410 == recovery.retry(now=10).next_eligible_at
    sequence = ProviderRecovery("primary", "hardcover", "books", 1, "waiting", 0, 0)
    delays = []
    for _ in range(5):
        sequence = sequence.retry(now=0)
        delays.append(sequence.next_eligible_at)
    assert [7_200, 14_400, 28_800, 57_600, 86_400] == delays
    dormant = ProviderRecovery.failed(
        connection="primary",
        adapter="hardcover",
        scope="books",
        generation=1,
        failure="authentication",
        now=10,
    )
    assert "dormant" == dormant.state
    assert "waiting" == dormant.with_generation(2).state


# Split from the tail of `test_transient_retry_and_generation_wake_are_closed`,
# whose `subTest` loop over these three values is a variant set rather than a
# step of that test's accumulating retry walk.
@pytest.mark.parametrize(
    "value",
    (0, 86401, True),
    ids=("zero", "one_over_maximum", "boolean"),
)
def test_failed_rejects_a_retry_after_outside_the_permitted_range(value) -> None:
    with pytest.raises(ProviderRecoveryError):
        ProviderRecovery.failed(
            connection="primary",
            adapter="hardcover",
            scope="books",
            generation=1,
            failure="rate_limited",
            now=10,
            retry_after=value,
        )


def test_atomic_claim_restart_and_corruption_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "recovery.sqlite"
        store = SQLiteProviderRecoveryStore.open(path)
        store.record(
            ProviderRecovery.failed(
                connection="primary",
                adapter="hardcover",
                scope="books",
                generation=1,
                failure="transient",
                now=0,
                retry_after=1,
            ),
            occurred_at=0,
        )
        assert store.claim_one(generation=1, now=0, token="a") is None
        assert store.claim_one(generation=1, now=1, token="a") is not None
        assert store.claim_one(generation=1, now=1, token="b") is None
        with pytest.raises(ProviderRecoveryStoreError):
            store.record(
                ProviderRecovery.failed(
                    connection="primary",
                    adapter="hardcover",
                    scope="books",
                    generation=1,
                    failure="authentication",
                    now=2,
                ),
                occurred_at=2,
            )
        # An interrupted worker can only be reclaimed after its bounded
        # lease; the claim operation itself never invokes a provider.
        assert store.claim_one(generation=1, now=61, token="b") is None
        assert store.claim_one(generation=1, now=301, token="b") is not None
        store.close()
        reopened = SQLiteProviderRecoveryStore.open(path)
        assert "checking" == reopened.snapshot()[0].state
        reopened.close()
        connection = sqlite3.connect(path)
        connection.execute("DROP TRIGGER provider_recovery_events_no_delete")
        connection.close()
        with pytest.raises(ProviderRecoveryStoreError):
            SQLiteProviderRecoveryStore.open(path)


def test_mixed_lane_health_preserves_provider_connection_identity() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        report = SimpleNamespace(
            providers=(
                SimpleNamespace(
                    provider="catalogue-a",
                    status=ProviderStatus.CANDIDATES,
                    lanes=(SimpleNamespace(status=ProviderStatus.AUTHENTICATION),),
                ),
                SimpleNamespace(
                    provider="catalogue-b",
                    status=ProviderStatus.UNAVAILABLE,
                    lanes=(),
                ),
                SimpleNamespace(
                    provider="excluded",
                    status=ProviderStatus.DISABLED,
                    lanes=(),
                ),
            )
        )
        _record_provider_recovery_observations(
            store,
            report,
            adapters={
                "catalogue-a": "hardcover",
                "catalogue-b": "hardcover",
                "excluded": "open_library",
            },
            scope="books",
            generation=1,
            now=0,
        )
        assert {
            ("catalogue-a", "hardcover", "books", "dormant"),
            ("catalogue-b", "hardcover", "books", "waiting"),
        } == {
            (item.connection, item.adapter, item.scope, item.state)
            for item in store.snapshot(limit=2)
        }
        store.close()


def test_owner_completion_and_backoff_transition_are_atomic() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        first = ProviderRecovery.failed(
            connection="primary",
            adapter="hardcover",
            scope="books",
            generation=1,
            failure="transient",
            now=0,
            retry_after=2,
        )
        store.record(first, occurred_at=0)
        claimed = store.claim_one(generation=1, now=2, token="owner")
        assert claimed is not None
        with pytest.raises(TypeError):
            store.record(
                ProviderRecovery("primary", "hardcover", "books", 1, "waiting", 2, 6),
                occurred_at=2,
                owner_token="owner",
            )  # type: ignore[call-arg]  # reason: the test proves `record` rejects an unknown `owner_token` keyword at runtime with TypeError.
        with pytest.raises(ProviderRecoveryStoreError):
            store.record(
                ProviderRecovery("primary", "hardcover", "books", 1, "waiting", 2, 6),
                occurred_at=2,
            )
        waiting = ProviderRecovery("primary", "hardcover", "books", 1, "waiting", 2, 6)
        with pytest.raises(ProviderRecoveryStoreError):
            store.complete(
                token="other",
                recovery=claimed,
                now=2,
                failure="transient",
                retry_after=4,
            )
        assert waiting == store.complete(
            token="owner", recovery=claimed, now=2, failure="transient", retry_after=4
        )
        assert ("waiting", 2, 6) == (
            store.snapshot()[0].state,
            store.snapshot()[0].attempt,
            store.snapshot()[0].next_eligible_at,
        )
        # An idempotent same state does not make another event.
        store.record(waiting, occurred_at=2)
        assert (
            3
            == store._connection.execute(
                "SELECT count(*) FROM provider_recovery_events"
            ).fetchone()[0]
        )
        store.close()


def test_membership_mismatch_fails_before_use() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "recovery.sqlite"
        store = SQLiteProviderRecoveryStore.open(path)
        store.record(
            ProviderRecovery.failed(
                connection="primary",
                adapter="hardcover",
                scope="books",
                generation=1,
                failure="authentication",
                now=0,
            ),
            occurred_at=0,
        )
        store.close()
        connection = sqlite3.connect(path)
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("DELETE FROM provider_recovery_members")
        connection.commit()
        connection.close()
        with pytest.raises(ProviderRecoveryStoreError):
            SQLiteProviderRecoveryStore.open(path)


def test_timestamp_bound_accepts_exact_maximum_and_rejects_one_over() -> None:
    assert (
        MAX_TIMESTAMP
        == ProviderRecovery(
            "primary", "hardcover", "books", 1, "waiting", 1, MAX_TIMESTAMP
        ).next_eligible_at
    )
    with pytest.raises(ProviderRecoveryError):
        ProviderRecovery(
            "primary", "hardcover", "books", 1, "waiting", 1, MAX_TIMESTAMP + 1
        )


def test_startup_generation_reconciles_every_older_state_and_rejects_newer() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        dormant = ProviderRecovery.failed(
            connection="primary",
            adapter="hardcover",
            scope="books",
            generation=1,
            failure="authentication",
            now=0,
        )
        waiting = ProviderRecovery.failed(
            connection="other",
            adapter="hardcover",
            scope="books",
            generation=1,
            failure="transient",
            now=0,
            retry_after=1,
        )
        checking = ProviderRecovery.failed(
            connection="aaa",
            adapter="hardcover",
            scope="books",
            generation=1,
            failure="transient",
            now=0,
            retry_after=1,
        )
        store.record(dormant, occurred_at=0)
        store.record(waiting, occurred_at=0)
        store.record(checking, occurred_at=0)
        assert store.claim_one(generation=1, now=1, token="owner") is not None
        assert 3 == store.reconcile_generation(generation=2, now=9)
        rows = {item.connection: item for item in store.snapshot(limit=3)}
        assert (2, "dormant", 0, None) == (
            rows["primary"].generation,
            rows["primary"].state,
            rows["primary"].attempt,
            rows["primary"].next_eligible_at,
        )
        assert (2, "dormant", 1, None) == (
            rows["other"].generation,
            rows["other"].state,
            rows["other"].attempt,
            rows["other"].next_eligible_at,
        )
        assert (2, "dormant", 1, None) == (
            rows["aaa"].generation,
            rows["aaa"].state,
            rows["aaa"].attempt,
            rows["aaa"].next_eligible_at,
        )
        assert [
            ("aaa", 2, "dormant", 1, None),
            ("other", 2, "dormant", 1, None),
            ("primary", 2, "dormant", 0, None),
        ] == store._connection.execute(
            "SELECT connection,generation,state,attempt,next_eligible_at FROM provider_recovery_events WHERE generation=2 ORDER BY connection"
        ).fetchall()
        before = (
            store.snapshot(limit=3),
            store._connection.execute(
                "SELECT count(*) FROM provider_recovery_events"
            ).fetchone()[0],
        )
        with pytest.raises(ProviderRecoveryStoreError):
            store.reconcile_generation(generation=1, now=10)
        assert before == (
            store.snapshot(limit=3),
            store._connection.execute(
                "SELECT count(*) FROM provider_recovery_events"
            ).fetchone()[0],
        )
        store.close()


def test_health_observation_is_idempotent_and_never_completes_a_claim() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        kwargs = {
            "connection": "primary",
            "adapter": "hardcover",
            "scope": "books",
            "generation": 1,
        }
        store.observe_failure(**kwargs, failure="transient", now=0)
        first = store.snapshot()[0]
        store.observe_failure(**kwargs, failure="rate_limited", now=10)
        assert first == store.snapshot()[0]
        assert (
            1
            == store._connection.execute(
                "SELECT count(*) FROM provider_recovery_events"
            ).fetchone()[0]
        )
        store.observe_failure(**kwargs, failure="authentication", now=10)
        dormant = store.snapshot()[0]
        store.observe_failure(**kwargs, failure="authorization", now=11)
        assert dormant == store.snapshot()[0]
        assert (
            2
            == store._connection.execute(
                "SELECT count(*) FROM provider_recovery_events"
            ).fetchone()[0]
        )

        checking = ProviderRecovery.failed(
            connection="other",
            adapter="hardcover",
            scope="books",
            generation=1,
            failure="transient",
            now=0,
            retry_after=1,
        )
        store.record(checking, occurred_at=0)
        claimed = store.claim_one(generation=1, now=1, token="owner")
        assert claimed is not None
        events = store._connection.execute(
            "SELECT count(*) FROM provider_recovery_events"
        ).fetchone()[0]
        store.observe_failure(
            connection="other",
            adapter="hardcover",
            scope="books",
            generation=1,
            failure="authentication",
            now=1,
        )
        assert (
            "checking"
            == {item.connection: item for item in store.snapshot(limit=2)}[
                "other"
            ].state
        )
        assert (
            events
            == store._connection.execute(
                "SELECT count(*) FROM provider_recovery_events"
            ).fetchone()[0]
        )
        store.close()


@pytest.mark.parametrize(
    ("first_failure", "first_delay", "second_failure", "second_delay"),
    (
        ("burst_throttled", 100, "quota_exhausted", 90_000),
        ("quota_exhausted", 90_000, "burst_throttled", 100),
    ),
    ids=("burst_then_quota", "quota_then_burst"),
)
def test_latest_authoritative_deferral_replaces_waiting_deadline(
    first_failure: str,
    first_delay: int,
    second_failure: str,
    second_delay: int,
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        kwargs = {
            "connection": "primary",
            "adapter": "hardcover",
            "scope": "books",
            "generation": 1,
        }
        store.observe_failure(
            **kwargs,
            failure=first_failure,
            now=10,
            retry_after=first_delay,
        )
        store.observe_failure(
            **kwargs,
            failure=second_failure,
            now=20,
            retry_after=second_delay,
        )
        assert [("waiting", 0, 20 + second_delay)] == store._connection.execute(
            "SELECT state,attempt,next_eligible_at FROM provider_recovery_health"
        ).fetchall()
        assert [
            ("waiting", 0, 10 + first_delay, 10),
            ("waiting", 0, 20 + second_delay, 20),
        ] == store._connection.execute(
            "SELECT state,attempt,next_eligible_at,occurred_at "
            "FROM provider_recovery_events ORDER BY sequence"
        ).fetchall()
        store.close()


def test_completion_rejects_exact_expiry_and_excluded_failure_without_mutation() -> (
    None
):
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        recovery = ProviderRecovery.failed(
            connection="primary",
            adapter="hardcover",
            scope="books",
            generation=1,
            failure="transient",
            now=0,
            retry_after=1,
        )
        store.record(recovery, occurred_at=0)
        claimed = store.claim_one(generation=1, now=1, token="owner", lease_seconds=1)
        assert claimed is not None
        before = (
            store.snapshot(),
            store._connection.execute(
                "SELECT count(*) FROM provider_recovery_events"
            ).fetchone()[0],
        )
        with pytest.raises(ProviderRecoveryStoreError):
            store.complete(token="owner", recovery=claimed, now=2, failure="transient")
        assert before == (
            store.snapshot(),
            store._connection.execute(
                "SELECT count(*) FROM provider_recovery_events"
            ).fetchone()[0],
        )
        with pytest.raises(ProviderRecoveryStoreError):
            store.complete(token="owner", recovery=claimed, now=1, failure="no_match")
        assert before == (
            store.snapshot(),
            store._connection.execute(
                "SELECT count(*) FROM provider_recovery_events"
            ).fetchone()[0],
        )
        store.close()


def test_completion_caps_retry_storm_and_activity_aggregates_without_truncation() -> (
    None
):
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        for number in range(33):
            recovery = ProviderRecovery.failed(
                connection=f"c{number}",
                adapter="hardcover",
                scope="books",
                generation=1,
                failure="authentication",
                now=0,
            )
            store.record(recovery, occurred_at=0)
        assert 1 == len(store.activity_snapshot())
        store.record(
            ProviderRecovery.failed(
                connection="storm",
                adapter="hardcover",
                scope="books",
                generation=1,
                failure="transient",
                now=0,
                retry_after=1,
            ),
            occurred_at=0,
        )
        current = store.claim_one(generation=1, now=1, token="owner")
        assert current is not None
        assert current is not None
        for attempt in range(5):
            current = store.complete(
                token="owner", recovery=current, now=10 + attempt, failure="transient"
            )
            if attempt < 4:
                current = store.claim_one(
                    generation=1, now=current.next_eligible_at or 0, token="owner"
                )
                assert current is not None
        assert ("waiting", 5, 10 + 4 + 86_400) == (
            current.state,
            current.attempt,
            current.next_eligible_at,
        )


def test_activity_aggregates_128_distinct_deadlines_without_truncation() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        for number in range(128):
            store.record(
                ProviderRecovery.failed(
                    connection=f"waiting-{number}",
                    adapter="hardcover",
                    scope="books",
                    generation=1,
                    failure="transient",
                    now=0,
                    retry_after=number + 1,
                ),
                occurred_at=0,
            )
        store.observe_failure(
            connection="dormant",
            adapter="hardcover",
            scope="books",
            generation=1,
            failure="authentication",
            now=0,
        )
        assert store.claim_one(generation=1, now=1, token="owner") is not None
        assert (
            {"state": "checking", "next_eligible_at": None, "affected_count": 1},
            {"state": "dormant", "next_eligible_at": None, "affected_count": 1},
            {"state": "waiting", "next_eligible_at": 2, "affected_count": 127},
        ) == store.activity_snapshot()
        store.close()


def test_activity_snapshot_exposes_closed_dispatch_reasons_to_its_projection() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        store.enqueue_jobs(
            (
                replace(
                    _job("reason"),
                    dispatch_reason="missing_corroboration",
                    dispatch_rank=0,
                ),
            ),
            enqueued_at=0,
        )
        assert [
            {
                "state": "waiting",
                "next_eligible_at": 7_200,
                "affected_count": 1,
                "dispatch_reasons": ["missing_corroboration"],
            }
        ] == list(store.activity_snapshot())


def test_activity_projects_non_owner_cohort_work_only_under_provider_owner() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = _due_store(
            Path(directory) / "recovery.sqlite", "library-a", "library-b"
        )
        first = replace(
            _job("first"),
            scope="library-a",
            library="library-a",
            dispatch_reason="missing_corroboration",
            dispatch_rank=0,
        )
        second = replace(_job("second"), scope="library-b", library="library-b")
        store.enqueue_jobs((first, second), enqueued_at=0)
        claimed = store.claim_one(generation=1, now=1, token="owner")
        assert claimed is not None
        assert (
            {
                "state": "checking",
                "next_eligible_at": None,
                "affected_count": 1,
                "dispatch_reasons": ["missing_corroboration"],
            },
        ) == store.activity_snapshot()
        canary = store.claim_job(recovery=claimed, token="owner", now=1)
        assert canary is not None
        fanout = store.authorize_fanout(
            job=canary, token="owner", recovery=claimed, now=1
        )
        assert (
            {
                "state": "fanout",
                "next_eligible_at": None,
                "affected_count": 1,
                "dispatch_reasons": ["blanket_scan"],
            },
        ) == store.activity_snapshot()
        member = store.claim_job(recovery=fanout, token="owner", now=1)
        assert member is not None
        store.complete_job(job=member, token="owner", now=1, success=True)
        store.finish_fanout(recovery=fanout, token="owner", now=1)
        store.close()


def test_orphan_shape_valid_event_rejects_store_open() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "recovery.sqlite"
        store = SQLiteProviderRecoveryStore.open(path)
        store.observe_failure(
            connection="known",
            adapter="hardcover",
            scope="books",
            generation=1,
            failure="authentication",
            now=0,
        )
        store.close()
        connection = sqlite3.connect(path)
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "INSERT INTO provider_recovery_events(connection,adapter,scope,generation,state,attempt,next_eligible_at,occurred_at) VALUES(?,?,?,?,?,?,?,?)",
            ("orphan", "hardcover", "books", 1, "dormant", 0, None, 0),
        )
        connection.commit()
        connection.close()
        with pytest.raises(ProviderRecoveryStoreError):
            SQLiteProviderRecoveryStore.open(path)


class _CommitFailingConnection:
    """Delegate to a real connection while failing ``COMMIT`` on demand.

    The store's connection is the seam the transaction epilogue must cross, so
    substituting it at construction keeps the failure forced from outside the
    store rather than by replacing one of its own members.  ``commit_error`` is
    writable after construction so a test can set a claim up through the same
    proxy and only then arm the failure it wants the epilogue to meet.
    """

    def __init__(
        self, connection: sqlite3.Connection, *, commit_error: BaseException | None
    ) -> None:
        self._connection = connection
        self.commit_error = commit_error
        self.statements: list[str] = []

    @property
    def in_transaction(self) -> bool:
        return self._connection.in_transaction

    def execute(
        self, statement: str, parameters: tuple[object, ...] = ()
    ) -> sqlite3.Cursor:
        self.statements.append(statement)
        if self.commit_error is not None and statement == "COMMIT":
            raise self.commit_error
        return self._connection.execute(statement, parameters)

    def close(self) -> None:
        self._connection.close()


class _WriteFailingConnection(_CommitFailingConnection):
    """Fail one real write by ordinal, without coupling a test to SQL text."""

    def __init__(self, connection: sqlite3.Connection, *, write_number: int) -> None:
        super().__init__(connection, commit_error=None)
        self._write_number = write_number
        self._writes = 0

    def execute(
        self, statement: str, parameters: tuple[object, ...] = ()
    ) -> sqlite3.Cursor:
        if statement not in {"BEGIN IMMEDIATE", "COMMIT", "ROLLBACK"}:
            self._writes += 1
            if self._writes == self._write_number:
                raise sqlite3.OperationalError("injected write failure")
        return super().execute(statement, parameters)


@pytest.mark.parametrize("write_number", (2, 3, 4), ids=("health", "event", "job"))
def test_enqueue_rolls_back_each_enrollment_or_job_write_failure(
    write_number: int,
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "recovery.sqlite"
        SQLiteProviderRecoveryStore.open(path).close()
        connection = sqlite3.connect(
            path, isolation_level=None, check_same_thread=False
        )
        connection.execute("PRAGMA foreign_keys=ON")
        proxy = _WriteFailingConnection(connection, write_number=write_number)
        store = SQLiteProviderRecoveryStore(cast(sqlite3.Connection, proxy))

        with pytest.raises(
            ProviderRecoveryStoreError, match="persistence is unavailable"
        ):
            store.enqueue_jobs((_job("fault"),), enqueued_at=7)

        assert not proxy.in_transaction
        assert [(0, 0, 0)] == connection.execute(
            "SELECT (SELECT count(*) FROM provider_recovery_members),"
            "(SELECT count(*) FROM provider_recovery_health),"
            "(SELECT count(*) FROM provider_recovery_jobs)"
        ).fetchall()
        store.close()


def test_enqueue_enrolls_each_exact_input_pair_without_cross_scope_reuse() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        jobs = (
            replace(_job("google-audio"), scope="audio", library="audio"),
            _job("google-books"),
            replace(
                _job("hardcover-books"),
                connection="hardcover",
                adapter="hardcover",
            ),
        )
        assert 3 == store.enqueue_jobs(jobs, enqueued_at=9)
        assert [
            ("google", "google_books", "audio"),
            ("google", "google_books", "books"),
            ("hardcover", "hardcover", "books"),
        ] == store._connection.execute(
            "SELECT connection,adapter,scope FROM provider_recovery_members "
            "ORDER BY connection,adapter,scope"
        ).fetchall()
        assert [
            ("google", "google_books", "audio", 7_209),
            ("google", "google_books", "books", 7_209),
            ("hardcover", "hardcover", "books", 7_209),
        ] == store._connection.execute(
            "SELECT connection,adapter,scope,next_eligible_at "
            "FROM provider_recovery_health ORDER BY connection,adapter,scope"
        ).fetchall()
        store.close()


def _store_over(
    directory: str, *, generation: int, commit_error: BaseException | None
) -> tuple[SQLiteProviderRecoveryStore, _CommitFailingConnection]:
    path = Path(directory) / "recovery.sqlite"
    prepared = SQLiteProviderRecoveryStore.open(path)
    prepared.record(
        ProviderRecovery.failed(
            connection="google",
            adapter="google_books",
            scope="books",
            generation=generation,
            failure="transient",
            now=0,
            retry_after=1,
        ),
        occurred_at=0,
    )
    prepared.close()
    connection = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    connection.execute("PRAGMA foreign_keys=ON")
    proxy = _CommitFailingConnection(connection, commit_error=commit_error)
    return SQLiteProviderRecoveryStore(cast(sqlite3.Connection, proxy)), proxy


# Pin the shared commit/rollback epilogue the helper's adopters reuse.
#
# Not every write path is one.  ``resume_fanout`` and ``enqueue_jobs`` keep
# their epilogue longhand, for the reasons ``_rollback_on_failure`` records,
# and neither is covered here.
def test_commit_failure_rolls_back_and_reports_persistence_unavailable() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store, proxy = _store_over(
            directory,
            generation=1,
            commit_error=sqlite3.OperationalError("disk I/O error"),
        )
        with pytest.raises(ProviderRecoveryStoreError) as raised:
            store.reconcile_generation(generation=2, now=10)
        assert "provider recovery persistence is unavailable" == str(raised.value)
        assert isinstance(raised.value.__cause__, sqlite3.OperationalError)
        assert "ROLLBACK" in proxy.statements
        assert not proxy.in_transaction
        store.close()


def test_store_failure_rolls_back_and_keeps_its_own_identity() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store, proxy = _store_over(directory, generation=2, commit_error=None)
        with pytest.raises(ProviderRecoveryStoreError) as raised:
            store.reconcile_generation(generation=1, now=10)
        assert "provider recovery generation is stale" == str(raised.value)
        assert raised.value.__cause__ is None
        assert "ROLLBACK" in proxy.statements
        assert "COMMIT" not in proxy.statements
        assert not proxy.in_transaction
        store.close()


# Prove each caught set is the one its sites wrapped before the widening.
#
# ``_rollback_on_failure`` now takes the extra types explicitly, so there are
# three distinct sets in the module and one test per set below.  Each drives
# a real write path over the commit seam and asserts both halves: that the
# named types are wrapped and rolled back, and that the types the set does
# *not* name still escape untouched.  Without the negative half a widening
# could re-scope the six narrow sites and every assertion would still pass.
def test_narrow_set_lets_a_domain_failure_escape_unwrapped() -> None:
    """``_rollback_on_failure()`` — the six sites #662 consolidated."""
    with tempfile.TemporaryDirectory() as directory:
        store, proxy = _store_over(
            directory,
            generation=1,
            commit_error=ProviderRecoveryError("domain rejection"),
        )
        with pytest.raises(ProviderRecoveryError):
            store.reconcile_generation(generation=2, now=10)
        assert "ROLLBACK" not in proxy.statements
        store.close()


def test_domain_set_wraps_a_domain_failure_and_rolls_back() -> None:
    """``_rollback_on_failure(ProviderRecoveryError)`` — the ten sites."""
    with tempfile.TemporaryDirectory() as directory:
        store, proxy = _store_over(
            directory,
            generation=1,
            commit_error=ProviderRecoveryError("domain rejection"),
        )
        with pytest.raises(ProviderRecoveryStoreError) as raised:
            store.observe_failure(
                connection="google",
                adapter="google_books",
                scope="books",
                generation=1,
                failure="transient",
                now=10,
            )
        assert "provider recovery persistence is unavailable" == str(raised.value)
        assert isinstance(raised.value.__cause__, ProviderRecoveryError)
        # ``observe_failure``'s own outer handler wraps the same type but
        # never rolls back, so the ROLLBACK is what proves the shared
        # epilogue — not the caller — admitted this failure.
        assert "ROLLBACK" in proxy.statements
        assert not proxy.in_transaction
        store.close()


def test_domain_set_lets_an_attribute_error_escape_unwrapped() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store, proxy = _store_over(
            directory,
            generation=1,
            commit_error=AttributeError("no such member"),
        )
        with pytest.raises(AttributeError):
            store.observe_failure(
                connection="google",
                adapter="google_books",
                scope="books",
                generation=1,
                failure="transient",
                now=10,
            )
        assert "ROLLBACK" not in proxy.statements
        store.close()


def test_attribute_set_wraps_an_attribute_error_and_rolls_back() -> None:
    """``_rollback_on_failure(ProviderRecoveryError, AttributeError)``."""
    with tempfile.TemporaryDirectory() as directory:
        store, proxy = _store_over(directory, generation=1, commit_error=None)
        claimed = store.claim_one(generation=1, now=10, token="token")
        assert claimed is not None
        proxy.commit_error = AttributeError("no such member")
        with pytest.raises(ProviderRecoveryStoreError) as raised:
            store.complete_success(token="token", recovery=claimed, now=11)
        assert "provider recovery persistence is unavailable" == str(raised.value)
        assert isinstance(raised.value.__cause__, AttributeError)
        assert "ROLLBACK" in proxy.statements
        assert not proxy.in_transaction
        store.close()


def test_every_call_site_names_the_set_it_wrapped_before() -> None:
    """Pin every ``_rollback_on_failure`` call, not merely every caller.

    The behavioural tests above cover one representative site per set; this
    covers the rest, so adopting the helper somewhere new — or adding a type
    to an existing call — cannot quietly re-scope a site under them.  Each
    value is a list of argument lists rather than one argument list: a
    function may hold more than one call, and keying by function alone
    would let a second call overwrite the first and assert nothing about
    it.
    """
    module = ast.parse(Path(recovery_persistence.__file__).read_text())

    def adopts(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_rollback_on_failure"
        )

    sites: dict[str, list[list[str]]] = {}
    for parent in ast.walk(module):
        if not isinstance(parent, ast.FunctionDef):
            continue
        for node in ast.walk(parent):
            if adopts(node):
                sites.setdefault(parent.name, []).append(
                    [ast.unparse(argument) for argument in node.args]
                )
    # Every call must be attributed to exactly one function. Walking each
    # function separately counts a call inside a nested function twice and
    # a call outside any function not at all; the module has neither today,
    # and this is what fails if either arrives.
    assert len([node for node in ast.walk(module) if adopts(node)]) == sum(
        len(calls) for calls in sites.values()
    )
    assert {
        # The six #662 consolidated. Their set stays narrow.
        "_reconcile_generation": [[]],
        "claim_job": [[]],
        "renew_job": [[]],
        "complete_job": [[]],
        "complete_stale_job": [[]],
        "release_target_hold": [[]],
        # The ten this change adopted.
        "complete_missing_jobs": [["ProviderRecoveryError"]],
        "_complete": [["ProviderRecoveryError"]],
        "_record": [["ProviderRecoveryError"]],
        "_observe_failure": [["ProviderRecoveryError"]],
        "_claim_one": [["ProviderRecoveryError"]],
        "complete_exhausted_scope": [["ProviderRecoveryError"]],
        "complete_canary_failure": [["ProviderRecoveryError"]],
        "authorize_fanout": [["ProviderRecoveryError"]],
        "finish_fanout": [["ProviderRecoveryError"]],
        "interrupt_fanout": [["ProviderRecoveryError"]],
        # The one that also admits a positional row unpack.
        "complete_success": [["ProviderRecoveryError", "AttributeError"]],
    } == sites


def _full_store(directory: str) -> SQLiteProviderRecoveryStore:
    """Open a store whose database cannot grow by one more page.

    ``max_page_count`` produces the same ``SQLITE_FULL`` a full disk produces,
    and SQLite ends the transaction itself on that error rather than leaving it
    open.  That is the state the two inner handlers have to survive: by the
    time they run, the transaction they mean to abandon is already gone.  Tiny
    pages and maximum-length identities make the limit bite on the first write
    rather than on however much slack the last page happens to hold.
    """
    path = Path(directory) / "recovery.sqlite"
    prepared = sqlite3.connect(path, isolation_level=None)
    prepared.execute("PRAGMA page_size=512")
    prepared.execute("VACUUM")
    prepared.close()
    store = SQLiteProviderRecoveryStore.open(path)
    for index in range(60):
        store.record(
            ProviderRecovery(
                "c" * 60 + f"{index:04d}", "a" * 64, "s" * 64, 1, "waiting", 0, 1
            ),
            occurred_at=0,
        )
    pages = store._connection.execute("PRAGMA page_count").fetchone()[0]
    store._connection.execute(f"PRAGMA max_page_count={pages}")
    return store


# Pin the two write paths that abandon their transaction inline.
#
# ``_claim_one`` and ``_record`` keep a nested handler instead of the shared
# epilogue, so they need the same guard the shared one carries.  Without it
# the ``ROLLBACK`` they issue against an already-closed transaction raises,
# and that failure -- not the one that ended the transaction -- becomes the
# ``__cause__`` the operator reads.
def test_a_full_database_reports_the_disk_failure_from_a_claim() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = _full_store(directory)
        with pytest.raises(ProviderRecoveryStoreError) as raised:
            store.claim_one(generation=1, now=10, token="t" * 128)
        assert isinstance(raised.value.__cause__, sqlite3.OperationalError)
        assert "database or disk is full" == str(raised.value.__cause__)
        assert not store._connection.in_transaction
        store.close()


def test_a_full_database_reports_the_disk_failure_from_a_record() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = _full_store(directory)
        with pytest.raises(ProviderRecoveryStoreError) as raised:
            store.record(
                ProviderRecovery("z" * 64, "a" * 64, "s" * 64, 1, "waiting", 0, 1),
                occurred_at=1,
            )
        assert isinstance(raised.value.__cause__, sqlite3.OperationalError)
        assert "database or disk is full" == str(raised.value.__cause__)
        assert not store._connection.in_transaction
        store.close()


def _schema_page_count() -> int:
    """The pages a complete recovery schema occupies at 512-byte pages.

    Measured rather than written down, so the cap below stays one page short
    of what ``_CREATE`` needs however SQLite lays those pages out.
    """
    with tempfile.TemporaryDirectory() as directory:
        connection = sqlite3.connect(
            Path(directory) / "measure.sqlite", isolation_level=None
        )
        connection.execute("PRAGMA page_size=512")
        connection.execute("VACUUM")
        connection.execute("BEGIN IMMEDIATE")
        for statement in recovery_persistence._CREATE:
            connection.execute(statement)
        connection.execute("COMMIT")
        pages = connection.execute("PRAGMA page_count").fetchone()[0]
        connection.close()
    return cast(int, pages)


def test_a_full_database_reports_the_disk_failure_from_schema_creation() -> None:
    """The third site: ``open`` creates the schema on a disk with no room.

    ``open`` owns the connection it fails on, so the cap is applied as that
    connection is handed over rather than afterwards.  One page short of the
    measured schema makes the last statement raise ``SQLITE_FULL`` after the
    transaction has dirtied enough pages that SQLite abandons it itself; the
    unguarded ``ROLLBACK`` then raised ``cannot rollback - no transaction is
    active`` and buried the disk failure as ``__context__`` (#715).
    """
    limit = _schema_page_count() - 1
    connect = sqlite3.connect

    def _capped(
        database: Path,
        *,
        isolation_level: Literal["DEFERRED", "EXCLUSIVE", "IMMEDIATE"] | None,
        check_same_thread: bool,
    ) -> sqlite3.Connection:
        connection = connect(
            database,
            isolation_level=isolation_level,
            check_same_thread=check_same_thread,
        )
        connection.execute("PRAGMA page_size=512")
        connection.execute(f"PRAGMA max_page_count={limit}")
        return connection

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "recovery.sqlite"
        with patch.object(sqlite3, "connect", _capped):
            with pytest.raises(ProviderRecoveryStoreError) as raised:
                SQLiteProviderRecoveryStore.open(path)

    assert "provider recovery database is unavailable or incompatible" == str(
        raised.value
    )
    assert isinstance(raised.value.__cause__, sqlite3.OperationalError)
    assert "database or disk is full" == str(raised.value.__cause__)


def test_a_rejected_claim_argument_still_abandons_its_open_transaction() -> None:
    """The other half of the guard: an open transaction is still rolled back.

    ``claim_one`` validates ``blocked_targets`` after ``BEGIN IMMEDIATE``,
    and the ``ProviderRecoveryStoreError`` it raises is caught by neither
    outer handler.  That makes the inner one load-bearing on exactly this
    path, so guarding it must not turn it off.
    """
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "recovery.sqlite"
        store = SQLiteProviderRecoveryStore.open(path)
        with pytest.raises(ProviderRecoveryStoreError) as raised:
            store.claim_one(
                generation=1,
                now=10,
                token="owner",
                blocked_targets=frozenset({"x" * 65}),
            )
        assert "provider recovery claim is invalid" == str(raised.value)
        assert not store._connection.in_transaction
        store.close()


def test_a_committed_claim_survives_a_failure_building_its_return_value() -> None:
    """The recorded decision: the lease is kept, and it expires on its own."""

    def _reject(*_: object) -> ProviderRecovery:
        raise ProviderRecoveryError("row is not a valid recovery")

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "recovery.sqlite"
        store = SQLiteProviderRecoveryStore.open(path)
        store.record(
            ProviderRecovery("google", "google_books", "books", 1, "waiting", 0, 1),
            occurred_at=0,
        )
        with patch.object(recovery_persistence, "ProviderRecovery", _reject):
            with pytest.raises(ProviderRecoveryStoreError) as raised:
                store.claim_one(generation=1, now=10, token="owner")
        assert isinstance(raised.value.__cause__, ProviderRecoveryError)
        assert ("checking", "owner") == store._connection.execute(
            "SELECT state,claim_token FROM provider_recovery_health"
        ).fetchone()
        assert not store._connection.in_transaction
        assert store.claim_one(generation=1, now=1_000, token="later") is not None
        store.close()
