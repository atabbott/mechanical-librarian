from __future__ import annotations

import unittest

from mechanical_librarian.domain.lifecycle import (
    ApplyPlanState,
    AuthorizationEvent,
    AuthorizationResult,
    CircuitBreakerState,
    Disposition,
    InvalidTransition,
    JobState,
    ReviewCaseState,
    can_authorize_apply,
    transition,
)


class LifecycleTests(unittest.TestCase):
    def test_documented_job_retry_and_lease_recovery_transitions(self) -> None:
        self.assertEqual(transition(JobState.RUNNING, JobState.RETRY_WAIT, actor="worker", reason="timeout").to_state, JobState.RETRY_WAIT)
        self.assertEqual(transition(JobState.LEASED, JobState.PENDING, actor="scheduler", reason="lease expired").to_state, JobState.PENDING)

    def test_terminal_and_cross_machine_transitions_are_rejected(self) -> None:
        with self.assertRaises(InvalidTransition):
            transition(JobState.SUCCEEDED, JobState.PENDING, actor="worker", reason="retry")
        with self.assertRaises(InvalidTransition):
            transition(ReviewCaseState.OPEN, ReviewCaseState.HUMAN_RESOLVED, actor="policy", reason="skip")

    def test_apply_requires_verification_and_rollback_is_terminal(self) -> None:
        with self.assertRaises(InvalidTransition):
            transition(ApplyPlanState.QUEUED, ApplyPlanState.VERIFIED, actor="apply", reason="skip apply")
        self.assertEqual(transition(ApplyPlanState.APPLYING, ApplyPlanState.ROLLBACK_PENDING, actor="apply", reason="verification failed").to_state, ApplyPlanState.ROLLBACK_PENDING)
        self.assertEqual(transition(ApplyPlanState.ROLLBACK_PENDING, ApplyPlanState.ROLLBACK_FAILED, actor="apply", reason="recovery failed").to_state, ApplyPlanState.ROLLBACK_FAILED)

    def test_breaker_requires_explicit_recovery_path(self) -> None:
        self.assertEqual(transition(CircuitBreakerState.OPEN, CircuitBreakerState.HALF_OPEN, actor="operator", reason="canary").to_state, CircuitBreakerState.HALF_OPEN)
        with self.assertRaises(InvalidTransition):
            transition(CircuitBreakerState.OPEN, CircuitBreakerState.CLOSED, actor="operator", reason="skip canary")

    def test_authorization_cannot_be_granted_by_ai_or_destructive_policy(self) -> None:
        self.assertFalse(can_authorize_apply(disposition=Disposition.NEEDS_REVIEW, human_decision_current=False, destructive=False))
        self.assertTrue(can_authorize_apply(disposition=Disposition.AUTO_APPLY, human_decision_current=False, destructive=False))
        self.assertFalse(can_authorize_apply(disposition=Disposition.AUTO_APPLY, human_decision_current=False, destructive=True))
        self.assertTrue(can_authorize_apply(disposition=Disposition.NEEDS_REVIEW, human_decision_current=True, destructive=True))

    def test_transition_context_is_bounded_and_reproducible(self) -> None:
        record = transition(JobState.PENDING, JobState.LEASED, actor="worker", reason="claim", reproducibility_context=(("job", "job-one"),))
        self.assertTrue(record.reproducibility_context)

    def test_operational_failure_is_not_a_decision_rejection(self) -> None:
        result = AuthorizationResult.operational_failure("temporary policy source unavailable")
        self.assertTrue(result.retryable)
        self.assertFalse(result.terminal)
        self.assertEqual(result.state.value, "operational_failure")

    def test_only_replays_are_marked_idempotent(self) -> None:
        self.assertFalse(AuthorizationResult.authorized(event=AuthorizationEvent.HUMAN_DECISION).idempotent_replay)
        self.assertTrue(AuthorizationResult.authorized().idempotent_replay)


if __name__ == "__main__":
    unittest.main()
