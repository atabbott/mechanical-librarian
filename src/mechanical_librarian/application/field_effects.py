"""One guarded path from immutable decisions to verified field effects."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable, Literal, Protocol

from mechanical_librarian.domain.automatic_decision import AutomaticDecision
from mechanical_librarian.domain.field_effect import (
    EffectAuthority,
    EffectConsent,
    FieldEffectError,
    FieldEffectOperation,
    FieldEffectPlan,
    project_decision_value,
)
from mechanical_librarian.domain.human_decision import HumanDecision


class GuardedApplicationError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class TargetObservation:
    value: object
    version: str


@dataclass(frozen=True, slots=True)
class TargetEffectResult:
    outcome: Literal["accepted", "ambiguous", "conflict"]


@dataclass(frozen=True, slots=True)
class AuthoritySnapshot:
    connection: str
    library: str
    work_id: str
    destination: str
    review_case_id: str
    target_fingerprint: str
    target_version: str
    decision_value: object
    source: Literal["persisted_review_case", "fake_service"]


class FieldEffectRepository(Protocol):
    def record_field_effect(self, operation: FieldEffectOperation) -> FieldEffectOperation: ...
    def get_field_effect(self, plan_id: str) -> FieldEffectOperation: ...
    def claim_field_effect(self, plan_id: str, expected_state: str, claim_token: str,
                           claimed_at: int) -> FieldEffectOperation: ...
    def transition_field_effect(self, plan_id: str, expected_state: str, state: str,
                                changed_at: int, failure: str | None = None) -> FieldEffectOperation: ...
    def supersede_field_effect(self, decision_id: str, superseded_at: int) -> int: ...
    def set_field_effect_paused(self, paused: bool, changed_at: int) -> None: ...
    def field_effect_paused(self) -> bool: ...
    def revoke_field_effect_authority(self, decision_id: str, changed_at: int) -> int: ...
    def revoke_field_effect_consent(self, consent_id: str, changed_at: int) -> int: ...
    def schedule_field_effect_reconciliation(self, plan_id: str, expected_state: str,
                                             changed_at: int) -> FieldEffectOperation: ...
    def complete_field_effect_reconciliation(self, plan_id: str, state: str,
                                             failure: str | None, changed_at: int,
                                             reevaluate: bool = False) -> FieldEffectOperation: ...
    def field_effect_cleanup_pending(self, plan_id: str) -> bool: ...
    def complete_field_effect_cleanup(self, plan_id: str, changed_at: int) -> None: ...
    def fail_field_effect_cleanup(self, plan_id: str, changed_at: int) -> None: ...
    def claim_next_field_effect_action(self, changed_at: int,
                                       plan_id: str | None = None) -> dict[str, object] | None: ...
    def complete_forward_reevaluation(self, action_id: str, changed_at: int) -> None: ...
    def pending_field_effect_cleanups(self, limit: int) -> tuple[str, ...]: ...


class TargetService(Protocol):
    def read(self, plan: FieldEffectPlan) -> TargetObservation: ...
    def apply(self, plan: FieldEffectPlan) -> TargetEffectResult: ...


class BackupReadiness(Protocol):
    def ready(self, plan: FieldEffectPlan) -> bool: ...


class CapabilityObserver(Protocol):
    def observe(self, plan: FieldEffectPlan) -> str: ...


class AuthorityCurrentness(Protocol):
    def resolve(self, decision: HumanDecision | AutomaticDecision) -> AuthoritySnapshot: ...
    def current(self, authority: EffectAuthority) -> bool: ...


class ConsentCurrentness(Protocol):
    def resolve(self, *, connection: str, library: str,
                destination: str) -> EffectConsent: ...
    def current(self, consent: EffectConsent) -> bool: ...


class DependencyCurrentness(Protocol):
    def current(self, digests: tuple[str, ...]) -> bool: ...


class CoverRecovery(Protocol):
    def capture(self, plan: FieldEffectPlan) -> None: ...
    def remove(self, plan_id: str) -> None: ...


class ForwardReevaluation(Protocol):
    def emit(self, plan: FieldEffectPlan) -> None: ...


def authority_from_decision(decision: HumanDecision | AutomaticDecision) -> EffectAuthority | None:
    """Project only exact existing authoritative decisions; advice cannot enter."""
    if type(decision) is HumanDecision:
        if decision.outcome != "accept":
            return None
        return EffectAuthority(decision.decision_id, decision.review_case_id,
                               decision.target_fingerprint, "fake_service", "human",
                               "candidate", None, None)
    if type(decision) is AutomaticDecision:
        if decision.outcome != "candidate":
            return None
        return EffectAuthority(
            decision.decision_id, decision.review_case_id, decision.target_fingerprint,
            "fake_service", "automatic_policy", "candidate",
            decision.resolved_policy_id, decision.qualification_manifest_sha256,
        )
    raise GuardedApplicationError("unauthorized_origin")


class GuardedFieldApplication:
    """Deep effect kernel; preclaim order is deliberately visible and fixed."""

    def __init__(self, repository: FieldEffectRepository, target: TargetService,
                 backup: BackupReadiness, capabilities: CapabilityObserver,
                 authority: AuthorityCurrentness, dependencies: DependencyCurrentness,
                 consent: ConsentCurrentness, cover_recovery: CoverRecovery,
                 reevaluation: ForwardReevaluation, clock: Callable[[], int],
                 preclaim_hook: Callable[[FieldEffectPlan], None] | None = None,
                 claim_hook: Callable[[FieldEffectPlan], None] | None = None,
                 recovery_hook: Callable[[FieldEffectPlan], None] | None = None) -> None:
        self._repository = repository
        self._target = target
        self._backup = backup
        self._capabilities = capabilities
        self._authority = authority
        self._dependencies = dependencies
        self._consent = consent
        self._cover_recovery = cover_recovery
        self._reevaluation = reevaluation
        self._clock = clock
        self._preclaim_hook = preclaim_hook or (lambda _: None)
        self._claim_hook = claim_hook or (lambda _: None)
        self._recovery_hook = recovery_hook or (lambda _: None)

    def plan(self, decision: HumanDecision | AutomaticDecision, *, connection: str,
             library: str, work_id: str, destination: str, pre_value: object,
             desired_value: object, target_fingerprint: str, target_version: str,
             dependency_digests: tuple[str, ...], capability_digest: str) -> FieldEffectOperation | None:
        projected = authority_from_decision(decision)
        if projected is None:
            return None
        try:
            snapshot = self._authority.resolve(decision)
        except Exception as error:
            raise GuardedApplicationError("decision_value") from error
        expected = (connection, library, work_id, destination, projected.review_case_id,
                    target_fingerprint, target_version)
        actual = (snapshot.connection, snapshot.library, snapshot.work_id,
                  snapshot.destination, snapshot.review_case_id,
                  snapshot.target_fingerprint, snapshot.target_version)
        if (actual != expected or snapshot.decision_value != desired_value
                or decision.destination != destination
                or decision.review_case_id != snapshot.review_case_id
                or decision.target_fingerprint != snapshot.target_fingerprint):
            raise GuardedApplicationError("authority_binding")
        projected = EffectAuthority(
            projected.decision_id, projected.review_case_id,
            projected.target_fingerprint, snapshot.source, projected.origin,
            projected.outcome, projected.policy_identity,
            projected.qualification_identity,
        )
        try:
            consent = self._consent.resolve(
                connection=connection, library=library, destination=destination
            )
        except Exception as error:
            raise GuardedApplicationError("consent") from error
        if not self._consent.current(consent):
            raise GuardedApplicationError("consent")
        effect_value = project_decision_value(destination, desired_value)
        plan = FieldEffectPlan.create(
            connection=connection, library=library, work_id=work_id,
            destination=destination, pre_value=pre_value, desired_value=effect_value,
            target_fingerprint=target_fingerprint, target_version=target_version,
            dependency_digests=dependency_digests, authority=projected,
            consent=consent, capability_digest=capability_digest,
            created_at=self._clock(),
        )
        if plan.pre_value == plan.desired_value:
            return None
        return self._repository.record_field_effect(
            FieldEffectOperation(plan, updated_at=plan.created_at)
        )

    def apply(self, plan_id: str) -> FieldEffectOperation:
        operation = self._repository.get_field_effect(plan_id)
        if operation.state in {"verified", "stale", "superseded", "abandoned"}:
            self._recover_cleanup(operation)
            return operation
        if operation.state == "reconciling":
            return self.reconcile(plan_id)
        if operation.state == "claimed":
            return self._execute_claimed(operation)
        if operation.state == "applying":
            operation = self._repository.transition_field_effect(
                plan_id, operation.state, "reconciling", self._clock(), "restart_readback"
            )
            return self.reconcile(plan_id)
        plan = operation.plan
        guarded = self._preclaim(operation)
        if guarded is not None:
            return guarded
        if plan.destination == "cover":
            try:
                self._cover_recovery.capture(plan)
            except Exception:
                return self._stale(operation, "cover_recovery")
        # Refresh every guard at the last feasible point before the repository's
        # atomic pause/state claim. The target independently enforces its own CAS.
        self._preclaim_hook(plan)
        guarded = self._preclaim(operation)
        if guarded is not None:
            return guarded
        token = hashlib.sha256(f"{plan.plan_id}:{operation.attempt + 1}".encode()).hexdigest()
        try:
            self._claim_hook(plan)
            operation = self._repository.claim_field_effect(
                plan.plan_id, operation.state, token, self._clock()
            )
        except Exception as error:
            if plan.destination == "cover":
                try:
                    current = self._repository.get_field_effect(plan.plan_id)
                    if current.state in {"stale", "superseded", "abandoned"}:
                        self._cover_recovery.remove(plan.plan_id)
                except Exception:
                    pass
            raise GuardedApplicationError("claim_conflict") from error
        return self._execute_claimed(operation)

    def _execute_claimed(self, operation: FieldEffectOperation) -> FieldEffectOperation:
        plan = operation.plan
        operation = self._repository.transition_field_effect(
            plan.plan_id, "claimed", "applying", self._clock()
        )
        try:
            result = self._target.apply(plan)
        except Exception:
            return self._repository.transition_field_effect(
                plan.plan_id, "applying", "retryable", self._clock(), "target_failure"
            )
        if result.outcome == "ambiguous":
            operation = self._repository.schedule_field_effect_reconciliation(
                plan.plan_id, "applying", self._clock()
            )
            self._recovery_hook(plan)
            self.run_pending_actions(limit=1, plan_id=plan.plan_id)
            return self.status(plan.plan_id)
        if result.outcome == "conflict":
            return self._stale(operation, "target_changed")
        return self._verify(operation)

    def reconcile(self, plan_id: str) -> FieldEffectOperation:
        self.run_pending_actions(limit=1, plan_id=plan_id)
        return self.status(plan_id)

    def _reconcile_action(self, plan_id: str) -> FieldEffectOperation:
        operation = self._repository.get_field_effect(plan_id)
        if operation.state != "reconciling":
            return operation
        observed = self._target.read(operation.plan)
        if observed.value == operation.plan.desired_value:
            result = self._repository.complete_field_effect_reconciliation(
                plan_id, "verified", None, self._clock()
            )
            self._recover_cleanup(result)
            return result
        if observed.value == operation.plan.pre_value:
            if (self._authority.current(operation.plan.authority)
                    and self._dependencies.current(operation.plan.dependency_digests)
                    and operation.attempt < 3):
                return self._repository.complete_field_effect_reconciliation(
                    plan_id, "retryable", "not_applied", self._clock()
                )
            result = self._repository.complete_field_effect_reconciliation(
                plan_id, "stale", "retry_exhausted", self._clock()
            )
            self._recover_cleanup(result)
            return result
        result = self._repository.complete_field_effect_reconciliation(
            plan_id, "stale", "third_value", self._clock(), reevaluate=True
        )
        self._recover_cleanup(result)
        return result

    def supersede(self, decision_id: str) -> int:
        count = self._repository.revoke_field_effect_authority(decision_id, self._clock())
        self.run_pending_cleanups()
        return count

    def revoke_consent(self, consent_id: str) -> int:
        count = self._repository.revoke_field_effect_consent(consent_id, self._clock())
        self.run_pending_cleanups()
        return count

    def status(self, plan_id: str) -> FieldEffectOperation:
        return self._repository.get_field_effect(plan_id)

    def set_paused(self, paused: bool) -> None:
        self._repository.set_field_effect_paused(paused, self._clock())

    def abandon(self, plan_id: str) -> FieldEffectOperation:
        operation = self._repository.get_field_effect(plan_id)
        if operation.state not in {"planned", "retryable"}:
            raise GuardedApplicationError("claimed")
        result = self._repository.transition_field_effect(
            plan_id, operation.state, "abandoned", self._clock(), "owner_abandoned"
        )
        if operation.plan.destination == "cover":
            self._recover_cleanup(result)
        return result

    def _verify(self, operation: FieldEffectOperation) -> FieldEffectOperation:
        observed = self._target.read(operation.plan)
        if observed.value == operation.plan.desired_value:
            return self._complete(operation, "applying")
        return self._repository.transition_field_effect(
            operation.plan.plan_id, "applying", "reconciling", self._clock(), "readback"
        )

    def _complete(self, operation: FieldEffectOperation, expected: str) -> FieldEffectOperation:
        result = self._repository.transition_field_effect(
            operation.plan.plan_id, expected, "verified", self._clock()
        )
        if operation.plan.destination == "cover":
            self._recover_cleanup(result)
        return result

    def _stale(self, operation: FieldEffectOperation, failure: str) -> FieldEffectOperation:
        result = self._repository.transition_field_effect(
            operation.plan.plan_id, operation.state, "stale", self._clock(), failure
        )
        if operation.plan.destination == "cover":
            self._recover_cleanup(result)
        return result

    def _preclaim(self, operation: FieldEffectOperation) -> FieldEffectOperation | None:
        """Evaluate current guards in their fixed order without claiming."""
        plan = operation.plan
        if not self._backup.ready(plan):
            return self._stale(operation, "backup")
        if self._capabilities.observe(plan) != plan.capability_digest:
            return self._stale(operation, "capability")
        if not self._authority.current(plan.authority):
            return self._stale(operation, "authority")
        if not self._consent.current(plan.consent):
            return self._stale(operation, "consent")
        if not self._dependencies.current(plan.dependency_digests):
            return self._stale(operation, "dependency")
        observed = self._target.read(plan)
        if observed.value != plan.pre_value or observed.version != plan.target_version:
            return self._stale(operation, "target_changed")
        if operation.attempt >= 3:
            return self._stale(operation, "circuit_open")
        if self._repository.field_effect_paused():
            raise GuardedApplicationError("paused")
        return None

    def _recover_cleanup(self, operation: FieldEffectOperation) -> None:
        if (operation.plan.destination != "cover"
                or not self._repository.field_effect_cleanup_pending(operation.plan.plan_id)):
            return
        try:
            self._cover_recovery.remove(operation.plan.plan_id)
        except Exception:
            self._repository.fail_field_effect_cleanup(
                operation.plan.plan_id, self._clock()
            )
            return
        self._repository.complete_field_effect_cleanup(
            operation.plan.plan_id, self._clock()
        )

    def run_pending_actions(self, *, limit: int = 100,
                            plan_id: str | None = None) -> int:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise GuardedApplicationError("invalid_limit")
        completed = 0
        for _ in range(limit):
            action = self._repository.claim_next_field_effect_action(
                self._clock(), plan_id
            )
            if action is None:
                break
            if action["kind"] == "targeted_read":
                self._reconcile_action(action["plan_id"])
            else:
                operation = self.status(action["plan_id"])
                self._reevaluation.emit(operation.plan)
                self._repository.complete_forward_reevaluation(
                    action["action_id"], self._clock()
                )
            completed += 1
        return completed

    def run_pending_cleanups(self, *, limit: int = 100) -> int:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise GuardedApplicationError("invalid_limit")
        plan_ids = self._repository.pending_field_effect_cleanups(limit)
        for plan_id in plan_ids:
            self._recover_cleanup(self.status(plan_id))
        return len(plan_ids)

    def recover_pending(self, *, limit: int = 100) -> tuple[int, int]:
        """Bounded restart entrypoint for the two closed recovery obligations."""
        action_count = cleanup_count = 0
        failures: list[Exception] = []
        try:
            action_count = self.run_pending_actions(limit=limit)
        except Exception as error:
            failures.append(error)
        try:
            cleanup_count = self.run_pending_cleanups(limit=limit)
        except Exception as error:
            failures.append(error)
        if len(failures) == 1:
            raise failures[0]
        if failures:
            raise ExceptionGroup("pending recovery failures", failures)
        return action_count, cleanup_count
