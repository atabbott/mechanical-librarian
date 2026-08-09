"""Deterministic, credential-free adapters for guarded-application qualification."""

from __future__ import annotations

import hashlib
import threading
from typing import Callable

from mechanical_librarian.application.field_effects import (
    CapabilityObservation,
    AuthoritySnapshot,
    TargetEffectResult,
    TargetObservation,
    TargetServiceUnavailable,
)
from mechanical_librarian.domain.field_effect import EffectAuthority, EffectConsent, FieldEffectPlan
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.automatic_decision import AutomaticDecision
from mechanical_librarian.adapters.private_recovery import (
    PrivateCoverRecoveryStore as _PrivateCoverRecoveryStore,
)


class FakeTargetService:
    """A bounded fake target; it deliberately cannot contain service credentials."""

    def __init__(self) -> None:
        self._values: dict[tuple[str, str, str, str], object] = {}
        self._versions: dict[tuple[str, str, str, str], str] = {}
        self._effects: dict[str, TargetEffectResult] = {}
        self._lock = threading.RLock()
        self.fail_next = False
        self.fail_read_next = False
        self.unavailable_scope_next: str | None = None
        self.fail_reads_for: set[str] = set()
        self.fail_read_after_apply_for: set[str] = set()
        self.ambiguous_next: str | None = None
        self.suspension_scope_next: str | None = None
        self.before_apply: Callable[[FieldEffectPlan], None] | None = None
        self.after_apply: Callable[[FieldEffectPlan], None] | None = None

    def seed(self, plan: FieldEffectPlan) -> None:
        with self._lock:
            key = self._key(plan)
            self._values[key] = plan.pre_value
            self._versions[key] = plan.target_version

    def read(self, plan: FieldEffectPlan) -> TargetObservation:
        with self._lock:
            if self.unavailable_scope_next is not None:
                scope, self.unavailable_scope_next = self.unavailable_scope_next, None
                raise TargetServiceUnavailable(scope)
            if self.fail_read_next or plan.plan_id in self.fail_reads_for:
                self.fail_read_next = False
                raise RuntimeError("synthetic target read failure")
            key = self._key(plan)
            return TargetObservation(
                self._values.get(key), self._versions.get(key, "missing")
            )

    def apply(self, plan: FieldEffectPlan) -> TargetEffectResult:
        with self._lock:
            prior = self._effects.get(plan.idempotency_key)
            if prior is not None:
                return prior
            if self.fail_next:
                self.fail_next = False
                raise RuntimeError("synthetic target failure")
            if self.before_apply is not None:
                callback, self.before_apply = self.before_apply, None
                callback(plan)
            observed = self.read(plan)
            if observed.value != plan.pre_value or observed.version != plan.target_version:
                return TargetEffectResult("conflict")
            if self.ambiguous_next is not None:
                behavior, self.ambiguous_next = self.ambiguous_next, None
                if behavior == "desired":
                    self._write(plan, plan.desired_value)
                elif behavior == "third":
                    self._write(plan, "synthetic-third-value")
                result = TargetEffectResult(
                    "ambiguous",
                    self.suspension_scope_next,
                    "unexpected_effect_behavior" if self.suspension_scope_next else None,
                )
                self.suspension_scope_next = None
                if behavior != "pre":
                    self._effects[plan.idempotency_key] = result
                return result
            self._write(plan, plan.desired_value)
            if plan.plan_id in self.fail_read_after_apply_for:
                self.fail_reads_for.add(plan.plan_id)
            if self.after_apply is not None:
                callback, self.after_apply = self.after_apply, None
                callback(plan)
            result = TargetEffectResult("accepted")
            self._effects[plan.idempotency_key] = result
            return result

    def drift(self, plan: FieldEffectPlan, value: object) -> None:
        with self._lock:
            self._write(plan, value)

    def _write(self, plan: FieldEffectPlan, value: object) -> None:
        with self._lock:
            key = self._key(plan)
            self._values[key] = value
            self._versions[key] = hashlib.sha256(repr(value).encode()).hexdigest()[:32]

    @staticmethod
    def _key(plan: FieldEffectPlan) -> tuple[str, str, str, str]:
        return plan.connection, plan.library, plan.work_id, plan.destination


class FakeBackupReadiness:
    ready_value = True
    def ready(self, plan: FieldEffectPlan) -> bool:
        return self.ready_value


class FakeCapabilityObserver:
    def __init__(self, digest: str) -> None:
        self.digest = digest
        self.category = "allowed"
    def observe(self, plan: FieldEffectPlan) -> CapabilityObservation:
        return CapabilityObservation(self.category, self.digest)


class FakeAuthorityCurrentness:
    def __init__(self) -> None:
        self.revoked: set[str] = set()
        self.automatic_values: dict[str, object] = {}
        self.bindings: dict[str, AuthoritySnapshot] = {}
    def bind(self, decision: HumanDecision | AutomaticDecision, *, connection: str,
             library: str, work_id: str, target_version: str,
             source: str = "fake_service") -> None:
        if type(decision) is HumanDecision and decision.outcome == "accept":
            value = decision.selected_value
        elif type(decision) is AutomaticDecision and decision.outcome == "candidate":
            value = self.automatic_values[decision.decision_id]
        else:
            raise ValueError("decision has no effect value")
        self.bindings[decision.decision_id] = AuthoritySnapshot(
            connection, library, work_id, decision.destination,
            decision.review_case_id, decision.target_fingerprint,
            target_version, value, source,
        )
    def resolve(self, decision: HumanDecision | AutomaticDecision) -> AuthoritySnapshot:
        return self.bindings[decision.decision_id]
    def current(self, authority: EffectAuthority) -> bool:
        return authority.decision_id not in self.revoked


class FakeConsentCurrentness:
    def __init__(self) -> None:
        self.revoked: set[str] = set()
        self.allowed: dict[tuple[str, str, str, str], EffectConsent] = {}
    def allow(self, *, connection: str, library: str, destination: str) -> None:
        consent = EffectConsent.create(
            connection=connection, library=library, destination=destination,
            granted_at=1,
        )
        self.allowed[(connection, library, destination, "apply_field")] = consent
    def resolve(self, *, connection: str, library: str,
                destination: str) -> EffectConsent:
        return self.allowed[(connection, library, destination, "apply_field")]
    def current(self, consent: EffectConsent) -> bool:
        return (consent.consent_id not in self.revoked
                and self.allowed.get((consent.connection, consent.library,
                                      consent.destination, consent.action)) == consent)


class FakeDependencyCurrentness:
    current_value = True
    def current(self, digests: tuple[str, ...]) -> bool:
        return self.current_value


class FakePreclaimHook:
    def __init__(self) -> None:
        self.callback: Callable[[FieldEffectPlan], None] | None = None

    def __call__(self, plan: FieldEffectPlan) -> None:
        if self.callback is not None:
            callback, self.callback = self.callback, None
            callback(plan)


class FakeForwardReevaluation:
    def __init__(self) -> None:
        self.emitted: list[str] = []
        self.fail_next = False

    def emit(self, plan: FieldEffectPlan) -> None:
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("synthetic reevaluation failure")
        if plan.plan_id not in self.emitted:
            self.emitted.append(plan.plan_id)


class PrivateCoverRecoveryStore(_PrivateCoverRecoveryStore):
    """Production adapter with one explicit synthetic cleanup fault."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fail_remove_next = False

    def remove(self, plan_id: str) -> None:
        if self.fail_remove_next:
            self.fail_remove_next = False
            raise OSError("synthetic cleanup failure")
        super().remove(plan_id)
