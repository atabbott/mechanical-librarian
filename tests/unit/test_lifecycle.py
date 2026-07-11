from __future__ import annotations

import unittest

import mechanical_librarian.domain as domain
from mechanical_librarian.domain.evidence import Disposition as EvidenceDisposition
from mechanical_librarian.domain.lifecycle import (
    ApplyPlanState,
    AuthorizationEvent,
    AuthorizationResult,
    AuthorizationState,
    BulkDecisionBatchState,
    CircuitBreakerState,
    Disposition,
    InvalidTransition,
    JobState,
    ProposalDecisionState,
    ReviewCaseState,
    Transition,
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
        self.assertFalse(AuthorizationResult.rejected("invalid authorization").idempotent_replay)

    def test_disposition_has_one_exact_identity_on_every_import_surface(self) -> None:
        self.assertIs(Disposition, EvidenceDisposition)
        self.assertIs(domain.Disposition, EvidenceDisposition)

    def test_every_direct_transition_matches_the_complete_legal_edge_table(self) -> None:
        documented_edges = {
            JobState: {
                JobState.PENDING: frozenset({JobState.LEASED, JobState.CANCELLED}),
                JobState.LEASED: frozenset(
                    {JobState.RUNNING, JobState.PENDING, JobState.CANCELLED}
                ),
                JobState.RUNNING: frozenset(
                    {
                        JobState.SUCCEEDED,
                        JobState.RETRY_WAIT,
                        JobState.FAILED_TERMINAL,
                        JobState.CANCELLED,
                        JobState.PENDING,
                    }
                ),
                JobState.RETRY_WAIT: frozenset(
                    {JobState.PENDING, JobState.CANCELLED}
                ),
            },
            ReviewCaseState: {
                ReviewCaseState.OPEN: frozenset(
                    {
                        ReviewCaseState.AUTO_RESOLVED,
                        ReviewCaseState.NEEDS_REVIEW,
                        ReviewCaseState.UNSUPPORTED,
                        ReviewCaseState.SUPERSEDED,
                    }
                ),
                ReviewCaseState.NEEDS_REVIEW: frozenset(
                    {ReviewCaseState.HUMAN_RESOLVED, ReviewCaseState.SUPERSEDED}
                ),
            },
            ProposalDecisionState: {
                ProposalDecisionState.PROPOSED: frozenset(
                    set(ProposalDecisionState) - {ProposalDecisionState.PROPOSED}
                )
            },
            BulkDecisionBatchState: {
                BulkDecisionBatchState.PREVIEWED: frozenset(
                    {
                        BulkDecisionBatchState.COMMITTED,
                        BulkDecisionBatchState.STALE,
                        BulkDecisionBatchState.EXPIRED,
                        BulkDecisionBatchState.CANCELLED,
                    }
                )
            },
            ApplyPlanState: {
                ApplyPlanState.DRAFT: frozenset(
                    {
                        ApplyPlanState.VALIDATED,
                        ApplyPlanState.STALE,
                        ApplyPlanState.CANCELLED,
                    }
                ),
                ApplyPlanState.VALIDATED: frozenset(
                    {
                        ApplyPlanState.BACKUP_READY,
                        ApplyPlanState.STALE,
                        ApplyPlanState.CANCELLED,
                    }
                ),
                ApplyPlanState.BACKUP_READY: frozenset(
                    {
                        ApplyPlanState.QUEUED,
                        ApplyPlanState.STALE,
                        ApplyPlanState.CANCELLED,
                    }
                ),
                ApplyPlanState.QUEUED: frozenset(
                    {
                        ApplyPlanState.APPLYING,
                        ApplyPlanState.STALE,
                        ApplyPlanState.CANCELLED,
                    }
                ),
                ApplyPlanState.APPLYING: frozenset(
                    {
                        ApplyPlanState.VERIFIED,
                        ApplyPlanState.ROLLBACK_PENDING,
                        ApplyPlanState.FAILED,
                    }
                ),
                ApplyPlanState.ROLLBACK_PENDING: frozenset(
                    {ApplyPlanState.ROLLED_BACK, ApplyPlanState.ROLLBACK_FAILED}
                ),
            },
            CircuitBreakerState: {
                CircuitBreakerState.CLOSED: frozenset({CircuitBreakerState.OPEN}),
                CircuitBreakerState.OPEN: frozenset(
                    {CircuitBreakerState.HALF_OPEN}
                ),
                CircuitBreakerState.HALF_OPEN: frozenset(
                    {CircuitBreakerState.CLOSED, CircuitBreakerState.OPEN}
                ),
            },
        }
        for state_type, table in documented_edges.items():
            for current in state_type:
                for target in state_type:
                    legal = target in table.get(current, frozenset())
                    with self.subTest(current=current, target=target):
                        if legal:
                            self.assertEqual(
                                Transition(current, target, "operator", "documented edge").to_state,
                                target,
                            )
                        else:
                            with self.assertRaises(InvalidTransition):
                                Transition(current, target, "operator", "illegal edge")

    def test_transition_rejects_substitute_mutable_and_unsafe_inputs(self) -> None:
        with self.assertRaises(InvalidTransition):
            Transition(JobState.PENDING, ReviewCaseState.OPEN, "operator", "wrong machine")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            Transition(JobState.PENDING, JobState.LEASED, " operator", "claim")
        with self.assertRaises(ValueError):
            Transition(JobState.PENDING, JobState.LEASED, "operator", "claim", [("job", "one")])  # type: ignore[arg-type]
        context = [("job", "one")]
        record = Transition(JobState.PENDING, JobState.LEASED, "operator", "claim", tuple(context))
        context.append(("later", "mutation"))
        self.assertEqual(record.reproducibility_context, (("job", "one"),))

    def test_direct_authorization_results_accept_only_legal_combinations(self) -> None:
        legal = {
            (AuthorizationState.AUTHORIZED, AuthorizationEvent.AUTOMATIC_POLICY, False, False, False): "authorization is current",
            (AuthorizationState.AUTHORIZED, AuthorizationEvent.HUMAN_DECISION, False, False, False): "authorization is current",
            (AuthorizationState.AUTHORIZED, AuthorizationEvent.IDEMPOTENT_REPLAY, False, False, True): "authorization is current",
            (AuthorizationState.REJECTED, AuthorizationEvent.HUMAN_DECISION, False, True, False): "invalid authorization",
            (AuthorizationState.STALE, AuthorizationEvent.STALE_INPUT, False, True, False): "review case evidence changed",
            (AuthorizationState.SUPERSEDED, AuthorizationEvent.STALE_INPUT, False, True, False): "review case was replaced",
            (AuthorizationState.OPERATIONAL_FAILURE, AuthorizationEvent.OPERATIONAL_FAILURE, True, False, False): "temporary policy source unavailable",
        }
        for state in AuthorizationState:
            for event in AuthorizationEvent:
                for retryable in (False, True):
                    for terminal in (False, True):
                        for replay in (False, True):
                            values = (state, event, retryable, terminal, replay)
                            with self.subTest(values=values):
                                if values in legal:
                                    AuthorizationResult(
                                        state,
                                        event,
                                        legal[values],
                                        retryable,
                                        terminal,
                                        replay,
                                    )
                                else:
                                    with self.assertRaises(ValueError):
                                        AuthorizationResult(state, event, "authorization is current", retryable, terminal, replay)
        all_reasons = {
            "authorization is current",
            "authorization requires an applicable disposition",
            "automatic authorization cannot carry a human decision",
            "automatic authorization requires policy qualification",
            "automatic disposition is no longer resolved",
            "automatic policy qualification is no longer current",
            "destructive work has no automatic authorization path",
            "human authorization cannot carry automatic policy evidence",
            "human decision is no longer current",
            "invalid authorization",
            "review case evidence changed",
            "review case was replaced",
            "temporary policy source unavailable",
        }
        for values, correct_reason in legal.items():
            for reason in all_reasons:
                with self.subTest(values=values, reason=reason):
                    if reason == correct_reason or (
                        values[0] is AuthorizationState.REJECTED
                        and reason
                        in {
                            "authorization requires an applicable disposition",
                            "automatic authorization cannot carry a human decision",
                            "automatic authorization requires policy qualification",
                            "destructive work has no automatic authorization path",
                            "human authorization cannot carry automatic policy evidence",
                            "invalid authorization",
                        }
                    ) or (
                        values[0] is AuthorizationState.STALE
                        and reason
                        in {
                            "automatic disposition is no longer resolved",
                            "automatic policy qualification is no longer current",
                            "human decision is no longer current",
                            "review case evidence changed",
                        }
                    ):
                        AuthorizationResult(*values[:2], reason, *values[2:])
                    else:
                        with self.assertRaises(ValueError):
                            AuthorizationResult(*values[:2], reason, *values[2:])
        with self.assertRaises(ValueError):
            AuthorizationResult(
                AuthorizationState.AUTHORIZED, AuthorizationEvent.HUMAN_DECISION,
                "bounded reason", 0, False, False,  # type: ignore[arg-type]
            )
        with self.assertRaises(ValueError):
            AuthorizationResult.rejected("opaque provider payload")


if __name__ == "__main__":
    unittest.main()
