"""One guarded path from immutable decisions to verified field effects."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable, Literal, Protocol

from mechanical_librarian.domain.automatic_decision import AutomaticDecision
from mechanical_librarian.domain.field_effect import (
    EffectAuthority,
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


class TargetService(Protocol):
    def read(self, plan: FieldEffectPlan) -> TargetObservation: ...
    def apply(self, plan: FieldEffectPlan) -> TargetEffectResult: ...


class BackupReadiness(Protocol):
    def ready(self, plan: FieldEffectPlan) -> bool: ...


class CapabilityObserver(Protocol):
    def observe(self, plan: FieldEffectPlan) -> str: ...


class AuthorityCurrentness(Protocol):
    def value(self, decision: HumanDecision | AutomaticDecision) -> object: ...
    def current(self, authority: EffectAuthority) -> bool: ...


class DependencyCurrentness(Protocol):
    def current(self, digests: tuple[str, ...]) -> bool: ...


class CoverRecovery(Protocol):
    def capture(self, plan: FieldEffectPlan) -> None: ...
    def remove(self, plan_id: str) -> None: ...


def authority_from_decision(decision: HumanDecision | AutomaticDecision) -> EffectAuthority | None:
    """Project only exact existing authoritative decisions; advice cannot enter."""
    if type(decision) is HumanDecision:
        if decision.outcome != "accept":
            return None
        return EffectAuthority(decision.decision_id, "human", "candidate", None, None)
    if type(decision) is AutomaticDecision:
        if decision.outcome != "candidate":
            return None
        return EffectAuthority(
            decision.decision_id, "automatic_policy", "candidate",
            decision.resolved_policy_id, decision.qualification_manifest_sha256,
        )
    raise GuardedApplicationError("unauthorized_origin")


class GuardedFieldApplication:
    """Deep effect kernel; preclaim order is deliberately visible and fixed."""

    def __init__(self, repository: FieldEffectRepository, target: TargetService,
                 backup: BackupReadiness, capabilities: CapabilityObserver,
                 authority: AuthorityCurrentness, dependencies: DependencyCurrentness,
                 cover_recovery: CoverRecovery, clock: Callable[[], int],
                 preclaim_hook: Callable[[FieldEffectPlan], None] | None = None) -> None:
        self._repository = repository
        self._target = target
        self._backup = backup
        self._capabilities = capabilities
        self._authority = authority
        self._dependencies = dependencies
        self._cover_recovery = cover_recovery
        self._clock = clock
        self._preclaim_hook = preclaim_hook or (lambda _: None)

    def plan(self, decision: HumanDecision | AutomaticDecision, *, connection: str,
             library: str, work_id: str, destination: str, pre_value: object,
             desired_value: object, target_version: str,
             dependency_digests: tuple[str, ...], capability_digest: str) -> FieldEffectOperation | None:
        projected = authority_from_decision(decision)
        if projected is None:
            return None
        try:
            authoritative_value = self._authority.value(decision)
        except Exception as error:
            raise GuardedApplicationError("decision_value") from error
        if authoritative_value != desired_value:
            raise GuardedApplicationError("decision_value")
        effect_value = project_decision_value(destination, desired_value)
        plan = FieldEffectPlan.create(
            connection=connection, library=library, work_id=work_id,
            destination=destination, pre_value=pre_value, desired_value=effect_value,
            target_version=target_version, dependency_digests=dependency_digests,
            authority=projected, capability_digest=capability_digest,
            created_at=self._clock(),
        )
        if plan.pre_value == plan.desired_value:
            return None
        return self._repository.record_field_effect(
            FieldEffectOperation(plan, updated_at=plan.created_at)
        )

    def apply(self, plan_id: str) -> FieldEffectOperation:
        operation = self._repository.get_field_effect(plan_id)
        if operation.state in {"verified", "stale", "superseded", "abandoned", "reconciling"}:
            return operation
        if operation.state in {"claimed", "applying"}:
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
            return self._repository.transition_field_effect(
                plan.plan_id, "applying", "reconciling", self._clock(), "ambiguous"
            )
        if result.outcome == "conflict":
            return self._repository.transition_field_effect(
                plan.plan_id, "applying", "stale", self._clock(), "target_changed"
            )
        return self._verify(operation)

    def reconcile(self, plan_id: str) -> FieldEffectOperation:
        operation = self._repository.get_field_effect(plan_id)
        if operation.state != "reconciling":
            return operation
        observed = self._target.read(operation.plan)
        if observed.value == operation.plan.desired_value:
            return self._complete(operation, "reconciling")
        if observed.value == operation.plan.pre_value:
            if (self._authority.current(operation.plan.authority)
                    and self._dependencies.current(operation.plan.dependency_digests)
                    and operation.attempt < 3):
                return self._repository.transition_field_effect(
                    plan_id, "reconciling", "retryable", self._clock(), "not_applied"
                )
            return self._repository.transition_field_effect(
                plan_id, "reconciling", "stale", self._clock(), "retry_exhausted"
            )
        return self._repository.transition_field_effect(
            plan_id, "reconciling", "stale", self._clock(), "third_value"
        )

    def supersede(self, decision_id: str) -> int:
        return self._repository.supersede_field_effect(decision_id, self._clock())

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
            self._cover_recovery.remove(plan_id)
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
            self._cover_recovery.remove(operation.plan.plan_id)
        return result

    def _stale(self, operation: FieldEffectOperation, failure: str) -> FieldEffectOperation:
        result = self._repository.transition_field_effect(
            operation.plan.plan_id, operation.state, "stale", self._clock(), failure
        )
        if operation.plan.destination == "cover":
            self._cover_recovery.remove(operation.plan.plan_id)
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
