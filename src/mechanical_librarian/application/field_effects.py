"""One guarded path from immutable decisions to verified field effects."""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, replace
from typing import Callable, Literal, Protocol, cast

from mechanical_librarian.application.operations import StageTimer
from mechanical_librarian.domain.automatic_decision import (
    AutomaticDecision,
    qualification_manifest_is_current,
)
from mechanical_librarian.domain.cover_automatic_decision import CoverAutomaticDecision
from mechanical_librarian.domain.description_advice import AuthoredDescriptionDecision
from mechanical_librarian.domain.field_effect import (
    CONNECTION_REPLAN_LIMIT,
    EffectAuthority,
    EffectConsent,
    FieldEffectError,
    FieldEffectOperation,
    FieldEffectPlan,
    SubtitleCompanion,
    field_value_version,
    project_decision_value,
)
from mechanical_librarian.domain.human_decision import HumanDecision


class GuardedApplicationError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class TargetServiceUnavailable(RuntimeError):
    def __init__(
        self,
        scope: Literal["connection", "destination"],
        reason: Literal[
            "connection_unavailable",
            "insufficient_permission",
            "field_unavailable",
            "unexpected_effect_behavior",
        ]
        | None = None,
    ) -> None:
        resolved = reason or (
            "connection_unavailable" if scope == "connection" else "field_unavailable"
        )
        super().__init__(resolved)
        self.scope = scope
        self.reason = resolved


@dataclass(frozen=True, slots=True)
class TargetObservation:
    value: object
    version: str
    subtitle_companion: TargetObservation | None = None

    def matches(
        self, plan: FieldEffectPlan, *, desired: bool, check_version: bool = False
    ) -> bool:
        if self.value != (plan.desired_value if desired else plan.pre_value):
            return False
        # The desired value's version is the planned shape a landed write
        # leaves behind; the pre-value's is the version the plan captured.
        if check_version and self.version != (
            field_value_version(plan.destination, plan.desired_value)
            if desired
            else plan.target_version
        ):
            return False
        companion = plan.subtitle_plan()
        if companion is None:
            return self.subtitle_companion is None
        return self.subtitle_companion is not None and self.subtitle_companion.matches(
            companion, desired=desired, check_version=check_version
        )


@dataclass(frozen=True, slots=True)
class TargetEffectResult:
    outcome: Literal["accepted", "ambiguous", "conflict"]
    suspension_scope: Literal["connection", "destination"] | None = None
    suspension_reason: (
        Literal[
            "connection_unavailable",
            "insufficient_permission",
            "unexpected_effect_behavior",
        ]
        | None
    ) = None

    def __post_init__(self) -> None:
        if (self.suspension_scope is None) != (self.suspension_reason is None):
            raise ValueError("target suspension disposition is incomplete")


@dataclass(frozen=True, slots=True)
class CapabilityObservation:
    category: Literal[
        "allowed",
        "insufficient_permission",
        "connection_unavailable",
        "field_unavailable",
    ]
    digest: str
    # Whether the connection's own identity check was answered, so a lost
    # connection can be told from an item or key the service refused. It is
    # not capability evidence and never enters `digest` (#1402).
    connection_reached: bool = False


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
    pre_value: object = None
    dependency_digests: tuple[str, ...] = ()
    paired_bundle_id: str | None = None
    paired_decision_id: str | None = None


class FieldEffectRepository(Protocol):
    def record_field_effect(
        self, operation: FieldEffectOperation
    ) -> FieldEffectOperation: ...
    def get_field_effect(self, plan_id: str) -> FieldEffectOperation: ...
    def claim_field_effect(
        self, plan_id: str, expected_state: str, claim_token: str, claimed_at: int
    ) -> FieldEffectOperation: ...
    def transition_field_effect(
        self,
        plan_id: str,
        expected_state: str,
        state: str,
        changed_at: int,
        failure: str | None = None,
    ) -> FieldEffectOperation: ...
    def supersede_field_effect(self, decision_id: str, superseded_at: int) -> int: ...
    def set_field_effect_paused(self, paused: bool, changed_at: int) -> None: ...
    def field_effect_paused(self) -> bool: ...
    def revoke_field_effect_authority(
        self, decision_id: str, changed_at: int
    ) -> int: ...
    def revoke_field_effect_consent(self, consent_id: str, changed_at: int) -> int: ...
    def schedule_field_effect_reconciliation(
        self,
        plan_id: str,
        expected_state: str,
        changed_at: int,
        failure: str = "ambiguous",
    ) -> FieldEffectOperation: ...
    def complete_field_effect_reconciliation(
        self,
        plan_id: str,
        state: str,
        failure: str | None,
        changed_at: int,
        reevaluate: bool = False,
    ) -> FieldEffectOperation: ...
    def field_effect_cleanup_pending(self, plan_id: str) -> bool: ...
    def complete_field_effect_cleanup(self, plan_id: str, changed_at: int) -> None: ...
    def fail_field_effect_cleanup(self, plan_id: str, changed_at: int) -> None: ...
    def claim_next_field_effect_action(
        self, changed_at: int, plan_id: str | None = None
    ) -> dict[str, object] | None: ...
    def complete_forward_reevaluation(
        self, action_id: str, changed_at: int
    ) -> None: ...
    def pending_field_effect_cleanups(self, limit: int) -> tuple[str, ...]: ...
    def pending_field_effect_plan_ids(self, limit: int) -> tuple[str, ...]: ...
    def retire_stale_cover_decision(
        self, decision_id: str, changed_at: int
    ) -> bool: ...
    def field_effect_event_documents(self, plan_id: str) -> tuple[str, ...]: ...
    def field_effect_connection_stales(self, decision_id: str) -> int: ...
    def suspend_field_effect_capability(
        self,
        *,
        scope: str,
        connection: str,
        destination: str,
        reason: str,
        changed_at: int,
    ) -> tuple[str, ...]: ...
    def field_effect_capability_suspension(
        self, connection: str, destination: str
    ) -> str | None: ...
    def grant_field_effect_consent_matrix(
        self,
        connection: str,
        grants: tuple[tuple[str, tuple[str, ...]], ...],
        granted_at: int,
    ) -> tuple[EffectConsent, ...]: ...
    def clear_field_effect_capability_suspension(
        self,
        *,
        scope: str,
        connection: str,
        destination: str,
        reason: str,
        observed_at: int,
    ) -> None: ...


class TargetService(Protocol):
    def read(self, plan: FieldEffectPlan) -> TargetObservation: ...
    def apply(self, plan: FieldEffectPlan) -> TargetEffectResult: ...


class BackupReadiness(Protocol):
    def ready(self, plan: FieldEffectPlan) -> bool: ...


class CapabilityObserver(Protocol):
    def observe(self, plan: FieldEffectPlan) -> CapabilityObservation: ...


class AuthorityCurrentness(Protocol):
    def resolve(
        self,
        decision: HumanDecision
        | AutomaticDecision
        | CoverAutomaticDecision
        | AuthoredDescriptionDecision,
    ) -> AuthoritySnapshot: ...
    def current(self, authority: EffectAuthority) -> bool: ...


class ConsentCurrentness(Protocol):
    def resolve(
        self, *, connection: str, library: str, destination: str
    ) -> EffectConsent: ...
    def current(self, consent: EffectConsent) -> bool: ...


class DependencyCurrentness(Protocol):
    def current(self, digests: tuple[str, ...]) -> bool: ...


class CoverRecovery(Protocol):
    def capture(self, plan: FieldEffectPlan) -> None: ...
    def remove(self, plan_id: str) -> None: ...


class CoverArtifactReader(Protocol):
    def read_original_digest(self, digest: str) -> bytes: ...


class ForwardReevaluation(Protocol):
    def emit(self, plan: FieldEffectPlan) -> None: ...


def authority_from_decision(
    decision: HumanDecision
    | AutomaticDecision
    | CoverAutomaticDecision
    | AuthoredDescriptionDecision,
    *,
    allow_corrective_keep_current: bool = False,
) -> EffectAuthority | None:
    """Project only exact existing authoritative decisions; advice cannot enter."""
    if type(decision) is AuthoredDescriptionDecision:
        return EffectAuthority(
            decision.decision_id,
            decision.review_case_id,
            decision.target_fingerprint,
            "fake_service",
            decision.origin,
            "candidate",
            decision.resolved_policy_id
            if decision.origin == "automatic_policy"
            else None,
            decision.qualification_manifest_sha256
            if decision.origin == "automatic_policy"
            else None,
        )
    if type(decision) is HumanDecision:
        if decision.application == "recorded_only":
            # A decision retained from before audiobook writes existed is
            # history, never work. The scheduler and the pending-write SQL both
            # filter these out before it gets here, so reaching this point is a
            # defect rather than ordinary traffic, and it refuses by name
            # instead of returning the same `None` an unaccepted decision
            # returns (#968).
            raise GuardedApplicationError("recorded_only")
        # An owner-entered value writes exactly the value it carries, as an
        # accept does (ADR-0085); a withdraw carries none.
        if decision.outcome not in {"accept", "owner_entered"} and not (
            allow_corrective_keep_current and decision.outcome == "keep_current"
        ):
            return None
        return EffectAuthority(
            decision.decision_id,
            decision.review_case_id,
            decision.target_fingerprint,
            "fake_service",
            "human",
            "candidate",
            None,
            None,
        )
    if (
        type(decision) is AutomaticDecision
        or type(decision) is CoverAutomaticDecision
        and decision.qualification_identity is not None
    ) and not qualification_manifest_is_current(
        decision.policy_category,
        decision.policy_option,
        decision.qualification_manifest_sha256,
    ):
        return None
    if type(decision) in (AutomaticDecision, CoverAutomaticDecision):
        automatic = cast(AutomaticDecision | CoverAutomaticDecision, decision)
        if automatic.outcome != "candidate":
            return None
        return EffectAuthority(
            automatic.decision_id,
            automatic.review_case_id,
            automatic.target_fingerprint,
            "fake_service",
            "automatic_policy",
            "candidate",
            automatic.resolved_policy_id,
            automatic.qualification_manifest_sha256,
        )
    raise GuardedApplicationError("unauthorized_origin")


class GuardedFieldApplication:
    """Deep effect kernel; preclaim order is deliberately visible and fixed."""

    def __init__(
        self,
        repository: FieldEffectRepository,
        target: TargetService,
        backup: BackupReadiness,
        capabilities: CapabilityObserver,
        authority: AuthorityCurrentness,
        dependencies: DependencyCurrentness,
        consent: ConsentCurrentness,
        cover_recovery: CoverRecovery,
        reevaluation: ForwardReevaluation,
        clock: Callable[[], int],
        preclaim_hook: Callable[[FieldEffectPlan], None] | None = None,
        claim_hook: Callable[[FieldEffectPlan], None] | None = None,
        recovery_hook: Callable[[FieldEffectPlan], None] | None = None,
        timing: StageTimer | None = None,
    ) -> None:
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
        self._timing = timing or StageTimer(None)
        self._draining = threading.Event()
        self._admission_lock = threading.Lock()

    def attach_timing(self, timing: StageTimer) -> None:
        """Attach advisory diagnostics without changing the effect authority."""
        self._timing = timing

    def start(self) -> tuple[int, int, int]:
        """Resume admission and complete bounded exact-current restart work."""
        self.resume_admission()
        return self.recover_startup()

    def resume_admission(self) -> None:
        """Resume this target without consuming repository-global recovery work."""
        with self._admission_lock:
            self._draining.clear()

    def begin_drain(self) -> None:
        """Reject new claims while allowing an already-claimed effect to finish."""
        with self._admission_lock:
            self._draining.set()

    def plan(
        self,
        decision: HumanDecision
        | AutomaticDecision
        | CoverAutomaticDecision
        | AuthoredDescriptionDecision,
        *,
        connection: str,
        library: str,
        work_id: str,
        destination: str,
        pre_value: object,
        desired_value: object,
        target_fingerprint: str,
        target_version: str,
        dependency_digests: tuple[str, ...],
        capability_digest: str,
        allow_corrective_keep_current: bool = False,
    ) -> FieldEffectOperation | None:
        plan = self._prepare_plan(
            decision,
            connection=connection,
            library=library,
            work_id=work_id,
            destination=destination,
            pre_value=pre_value,
            desired_value=desired_value,
            target_fingerprint=target_fingerprint,
            target_version=target_version,
            dependency_digests=dependency_digests,
            capability_digest=capability_digest,
            allow_corrective_keep_current=allow_corrective_keep_current,
        )
        if plan is None:
            return None
        return self._repository.record_field_effect(
            FieldEffectOperation(plan, updated_at=plan.created_at)
        )

    def plan_title_subtitle(
        self,
        title: AutomaticDecision,
        subtitle: AutomaticDecision,
        *,
        title_capability_digest: str,
        subtitle_capability_digest: str,
    ) -> FieldEffectOperation:
        left = self._authority.resolve(title)
        right = self._authority.resolve(subtitle)
        if (
            type(title) is not AutomaticDecision
            or type(subtitle) is not AutomaticDecision
            or title.destination != "title"
            or subtitle.destination != "subtitle"
            or left.paired_bundle_id is None
            or left.paired_bundle_id != right.paired_bundle_id
            or left.paired_decision_id != subtitle.decision_id
            or right.paired_decision_id != title.decision_id
            or (
                left.connection,
                left.library,
                left.work_id,
                left.review_case_id,
                left.target_fingerprint,
            )
            != (
                right.connection,
                right.library,
                right.work_id,
                right.review_case_id,
                right.target_fingerprint,
            )
        ):
            raise GuardedApplicationError("authority_binding")
        plans = []
        for decision, snapshot, capability in (
            (title, left, title_capability_digest),
            (subtitle, right, subtitle_capability_digest),
        ):
            plan = self._prepare_plan(
                decision,
                connection=snapshot.connection,
                library=snapshot.library,
                work_id=snapshot.work_id,
                destination=snapshot.destination,
                pre_value=snapshot.pre_value,
                desired_value=snapshot.decision_value,
                target_fingerprint=snapshot.target_fingerprint,
                target_version=snapshot.target_version,
                dependency_digests=snapshot.dependency_digests,
                capability_digest=capability,
                paired_bundle_id=left.paired_bundle_id,
            )
            if plan is None:
                raise GuardedApplicationError("authority_binding")
            plans.append(plan)
        primary, companion = plans
        paired = FieldEffectPlan.create(
            connection=primary.connection,
            library=primary.library,
            work_id=primary.work_id,
            destination="title",
            pre_value=primary.pre_value,
            desired_value=primary.desired_value,
            target_fingerprint=primary.target_fingerprint,
            target_version=primary.target_version,
            dependency_digests=primary.dependency_digests,
            authority=primary.authority,
            consent=primary.consent,
            capability_digest=primary.capability_digest,
            created_at=primary.created_at,
            subtitle_companion=SubtitleCompanion(
                left.paired_bundle_id,
                companion.pre_value,
                cast(str, companion.desired_value),
                companion.target_version,
                companion.dependency_digests,
                companion.authority,
                companion.consent,
                companion.capability_digest,
            ),
        )
        return self._repository.record_field_effect(
            FieldEffectOperation(paired, updated_at=paired.created_at)
        )

    def _prepare_plan(
        self,
        decision: HumanDecision
        | AutomaticDecision
        | CoverAutomaticDecision
        | AuthoredDescriptionDecision,
        *,
        connection: str,
        library: str,
        work_id: str,
        destination: str,
        pre_value: object,
        desired_value: object,
        target_fingerprint: str,
        target_version: str,
        dependency_digests: tuple[str, ...],
        capability_digest: str,
        allow_corrective_keep_current: bool = False,
        paired_bundle_id: str | None = None,
    ) -> FieldEffectPlan | None:
        projected = authority_from_decision(
            decision, allow_corrective_keep_current=allow_corrective_keep_current
        )
        if projected is None:
            return None
        try:
            snapshot = self._authority.resolve(decision)
        except Exception as error:
            # The seam declares no error type, so the breadth stays; only the
            # code changes. A resolver that names its own failure keeps that
            # name, and just an unnamed one falls back to the refusal code, so a
            # storage fault is never reported as an unacceptable decision.
            # Either way the decision's authority is unresolved, so no plan row
            # is recorded and the cause is chained rather than discarded.
            raise GuardedApplicationError(
                error.code if isinstance(error, FieldEffectError) else "decision_value"
            ) from error
        if snapshot.paired_bundle_id != paired_bundle_id:
            raise GuardedApplicationError("paired_authority_required")
        expected = (
            connection,
            library,
            work_id,
            destination,
            projected.review_case_id,
            target_fingerprint,
            target_version,
        )
        actual = (
            snapshot.connection,
            snapshot.library,
            snapshot.work_id,
            snapshot.destination,
            snapshot.review_case_id,
            snapshot.target_fingerprint,
            snapshot.target_version,
        )
        if (
            actual != expected
            or snapshot.decision_value != desired_value
            or decision.destination != destination
            or decision.review_case_id != snapshot.review_case_id
            or decision.target_fingerprint != snapshot.target_fingerprint
        ):
            raise GuardedApplicationError("authority_binding")
        projected = EffectAuthority(
            projected.decision_id,
            projected.review_case_id,
            projected.target_fingerprint,
            snapshot.source,
            projected.origin,
            projected.outcome,
            projected.policy_identity,
            projected.qualification_identity,
        )
        try:
            consent = self._consent.resolve(
                connection=connection, library=library, destination=destination
            )
        except Exception as error:
            # Same undeclared seam, same rule. A repository that has already
            # decided this is a withdrawn consent says so with that code and
            # keeps it; a storage fault carries its own code out instead of
            # being dressed as a refusal. Neither records a plan row.
            #
            # The fallback cannot be `consent`: since #678 that code asserts a
            # state conflict, and an exception nobody named is precisely the
            # case where the recorded state is unknown. It names the seam that
            # could not answer instead, and stays 503.
            raise GuardedApplicationError(
                error.code
                if isinstance(error, FieldEffectError)
                else "consent_unavailable"
            ) from error
        # A resolved but no-longer-current consent is the genuine refusal this
        # method owns, and it keeps `consent` whatever the resolver reported.
        if not self._consent.current(consent):
            raise GuardedApplicationError("consent")
        effect_value = project_decision_value(destination, desired_value)
        plan = FieldEffectPlan.create(
            connection=connection,
            library=library,
            work_id=work_id,
            destination=destination,
            pre_value=pre_value,
            desired_value=effect_value,
            target_fingerprint=target_fingerprint,
            target_version=target_version,
            dependency_digests=dependency_digests,
            authority=projected,
            consent=consent,
            capability_digest=capability_digest,
            created_at=self._clock(),
        )
        if plan.pre_value == plan.desired_value:
            return None
        return plan

    def apply(
        self, plan_id: str, affected_review_case_ids: set[str] | None = None
    ) -> FieldEffectOperation:
        operation = self._repository.get_field_effect(plan_id)
        if operation.state in {"verified", "stale", "superseded", "abandoned"}:
            self._recover_cleanup(operation)
            return operation
        if operation.state == "reconciling":
            return self.reconcile(plan_id, affected_review_case_ids)
        if operation.state == "claimed":
            # ``claimed -> applying`` precedes the target request, so a plan
            # found here sent nothing. Returning it to ``retryable`` hands it
            # to the ordinary scheduler, which applies it only through every
            # guard and a fresh atomic claim (#1382).
            return self._repository.transition_field_effect(
                plan_id, "claimed", "retryable", self._clock(), "not_sent"
            )
        if operation.state == "applying":
            operation = self._repository.schedule_field_effect_reconciliation(
                plan_id, operation.state, self._clock(), "restart_readback"
            )
            self._recovery_hook(operation.plan)
            return self.reconcile(plan_id, affected_review_case_ids)
        if self._draining.is_set():
            raise GuardedApplicationError("draining")
        plan = operation.plan
        guarded = self._preclaim(operation, affected_review_case_ids)
        if guarded is not None:
            return guarded
        if plan.destination == "cover":
            try:
                self._cover_recovery.capture(plan)
            except Exception:
                # No restorable pre-image means no cover write may proceed, and
                # the refusal is durable: the plan is staled with this failure
                # before returning, so nothing reaches the target unrecorded.
                return self._stale(operation, "cover_recovery")
        # Refresh every guard at the last feasible point before the repository's
        # atomic pause/state claim. Target behavior is adapter-specific.
        self._preclaim_hook(plan)
        guarded = self._preclaim(operation, affected_review_case_ids)
        if guarded is not None:
            return guarded
        token = hashlib.sha256(
            f"{plan.plan_id}:{operation.attempt + 1}".encode()
        ).hexdigest()
        try:
            self._claim_hook(plan)
            with self._admission_lock:
                if self._draining.is_set():
                    raise GuardedApplicationError("draining")
                operation = self._repository.claim_field_effect(
                    plan.plan_id, operation.state, token, self._clock()
                )
        except GuardedApplicationError:
            raise
        except Exception as error:
            # The repository names exactly why the claim failed and only its own
            # ``claim_conflict`` is contention. Carry every other code through
            # unchanged so a pause, revocation, suspension, or storage fault is
            # never reported to callers or history as a lost race.
            self._discard_cover_capture(plan)
            raise GuardedApplicationError(
                error.code if isinstance(error, FieldEffectError) else "claim_failed"
            ) from error
        return self._execute_claimed(operation, affected_review_case_ids)

    def _discard_cover_capture(self, plan: FieldEffectPlan) -> None:
        """Drop a captured cover whose plan already reached a terminal state.

        Runs while a claim failure is being handled, so a fault raised here
        chains onto that failure instead of replacing or hiding it.
        """
        if plan.destination != "cover":
            return
        try:
            current = self._repository.get_field_effect(plan.plan_id)
            if current.state in {"stale", "superseded", "abandoned"}:
                self._cover_recovery.remove(plan.plan_id)
        except FieldEffectError as error:
            # A vanished row is the one benign outcome: there is no terminal
            # state left to observe and no obligation this path can discharge.
            if error.code == "not_found":
                return
            raise GuardedApplicationError("cover_recovery") from error
        except Exception as error:
            # Neither seam declares an error type and ``apply`` owes its callers
            # a guarded error, so convert instead of letting a raw filesystem or
            # adapter fault escape; the cleanup fault still wins the code.
            raise GuardedApplicationError("cover_recovery") from error

    def _execute_claimed(
        self,
        operation: FieldEffectOperation,
        affected_review_case_ids: set[str] | None,
    ) -> FieldEffectOperation:
        plan = operation.plan
        operation = self._repository.transition_field_effect(
            plan.plan_id, "claimed", "applying", self._clock()
        )
        if any(
            not self._authority.current(member.authority)
            for member in (plan, plan.subtitle_plan())
            if member is not None
        ):
            return self._stale(operation, "authority")
        try:
            with self._timing.measure("apply", "target"):
                result = self._target.apply(plan)
        except Exception as error:
            # Target errors are adapter-shaped and the seam declares none, so
            # breadth is the contract. Every one persists a typed suspension
            # when it carries one and lands the operation on ``retryable`` with
            # a recorded failure; none is discarded.
            target_error_affected = self._persist_target_suspension(
                plan, error, affected_review_case_ids
            )
            return self._with_affected_review_cases(
                self._repository.transition_field_effect(
                    plan.plan_id,
                    "applying",
                    "retryable",
                    self._clock(),
                    "target_failure",
                ),
                target_error_affected,
            )
        affected: tuple[str, ...] = ()
        if result.suspension_scope is not None:
            assert result.suspension_reason is not None
            affected = self._suspend_field_effect_capability(
                scope=result.suspension_scope,
                connection=plan.connection,
                destination=plan.destination,
                reason=result.suspension_reason,
                changed_at=self._clock(),
                affected_review_case_ids=affected_review_case_ids,
            )
        if result.outcome == "ambiguous":
            operation = self._repository.schedule_field_effect_reconciliation(
                plan.plan_id, "applying", self._clock()
            )
            self._recovery_hook(plan)
            inline = self._run_next_field_effect_action(
                plan.plan_id, affected_review_case_ids
            )
            return self._with_affected_review_cases(
                self.status(plan.plan_id),
                affected + (() if inline is None else inline.affected_review_case_ids),
            )
        if result.outcome == "conflict":
            return self._with_affected_review_cases(
                self._stale(operation, "target_changed"), affected
            )
        return self._with_affected_review_cases(
            self._verify(operation, affected_review_case_ids), affected
        )

    def reconcile(
        self, plan_id: str, affected_review_case_ids: set[str] | None = None
    ) -> FieldEffectOperation:
        return self._run_next_field_effect_action(
            plan_id, affected_review_case_ids
        ) or self.status(plan_id)

    def _reconcile_action(
        self, plan_id: str, affected_review_case_ids: set[str] | None
    ) -> FieldEffectOperation:
        operation = self._repository.get_field_effect(plan_id)
        if operation.state != "reconciling":
            return operation
        try:
            with self._timing.measure("reconciliation", "recovery"):
                observed = self._target.read(operation.plan)
        except Exception as error:
            # Same undeclared target seam as ``_execute_claimed``: the failure
            # is recorded on the operation as ``target_failure`` and the action
            # is completed, so the reconciliation obligation is never stranded.
            affected = self._persist_target_suspension(
                operation.plan, error, affected_review_case_ids
            )
            return self._with_affected_review_cases(
                self._repository.complete_field_effect_reconciliation(
                    plan_id, "retryable", "target_failure", self._clock()
                ),
                affected,
            )
        if observed.matches(operation.plan, desired=True):
            result = self._repository.complete_field_effect_reconciliation(
                plan_id, "verified", None, self._clock()
            )
            self._recover_cleanup(result)
            return result
        if observed.matches(operation.plan, desired=False):
            if (
                all(
                    self._authority.current(member.authority)
                    and (
                        operation.plan.subtitle_companion is None
                        or self._consent.current(member.consent)
                    )
                    and self._dependencies.current(member.dependency_digests)
                    for member in (operation.plan, operation.plan.subtitle_plan())
                    if member is not None
                )
                and operation.attempt < 3
            ):
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
        count = self._repository.revoke_field_effect_authority(
            decision_id, self._clock()
        )
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

    def _verify(
        self,
        operation: FieldEffectOperation,
        affected_review_case_ids: set[str] | None,
    ) -> FieldEffectOperation:
        try:
            with self._timing.measure("verification", "target"):
                observed = self._target.read(operation.plan)
        except Exception as error:
            # The same undeclared target seam, and breadth is required here:
            # any readback failure whatsoever leaves the write outcome unknown.
            affected = self._persist_target_suspension(
                operation.plan, error, affected_review_case_ids
            )
            # The write may already have succeeded. Persist a targeted read;
            # never infer failure and blindly write the desired value again.
            return self._with_affected_review_cases(
                self._repository.schedule_field_effect_reconciliation(
                    operation.plan.plan_id,
                    "applying",
                    self._clock(),
                    "target_failure",
                ),
                affected,
            )
        if observed.matches(operation.plan, desired=True):
            return self._complete(operation, "applying")
        return self._repository.schedule_field_effect_reconciliation(
            operation.plan.plan_id, "applying", self._clock(), "readback"
        )

    def _complete(
        self, operation: FieldEffectOperation, expected: str
    ) -> FieldEffectOperation:
        result = self._repository.transition_field_effect(
            operation.plan.plan_id, expected, "verified", self._clock()
        )
        if operation.plan.destination == "cover":
            self._recover_cleanup(result)
        return result

    def _stale(
        self, operation: FieldEffectOperation, failure: str
    ) -> FieldEffectOperation:
        result = self._repository.transition_field_effect(
            operation.plan.plan_id, operation.state, "stale", self._clock(), failure
        )
        if operation.plan.destination == "cover":
            self._recover_cleanup(result)
        return result

    def _preclaim(
        self,
        operation: FieldEffectOperation,
        affected_review_case_ids: set[str] | None,
    ) -> FieldEffectOperation | None:
        """Evaluate current guards in their fixed order without claiming."""
        plan = operation.plan
        if not self._backup.ready(plan):
            return self._stale(operation, "backup")
        for member in (plan, plan.subtitle_plan()):
            if member is None:
                continue
            suspension = self._repository.field_effect_capability_suspension(
                member.connection, member.destination
            )
            # A lost connection is the one suspension time can resolve, so it
            # alone waits for the fresh observation below instead of staling
            # the plan; every other reason still refuses here.
            if suspension is not None and suspension != "connection_unavailable":
                return self._stale(operation, "capability")
            observed_at = None if suspension is None else self._clock()
            capability = self._capabilities.observe(member)
            if capability.category == "allowed" and observed_at is not None:
                self._repository.clear_field_effect_capability_suspension(
                    scope="connection",
                    connection=member.connection,
                    destination="",
                    reason="connection_unavailable",
                    observed_at=observed_at,
                )
                # Re-read rather than assume. The clear removes only rows older
                # than this observation, so a lost connection recorded in the
                # same clock second survives it: refuse without staling, as
                # the atomic claim does, and let the next pass observe again.
                # A destination-scoped suspension behind it still stales.
                remaining = self._repository.field_effect_capability_suspension(
                    member.connection, member.destination
                )
                if remaining == "connection_unavailable":
                    raise GuardedApplicationError("capability")
                if remaining is not None:
                    return self._stale(operation, "capability")
            if capability.category != "allowed":
                scope = (
                    "connection"
                    if capability.category == "connection_unavailable"
                    else "destination"
                )
                affected = self._suspend_field_effect_capability(
                    scope=scope,
                    connection=member.connection,
                    destination=member.destination,
                    reason=capability.category,
                    changed_at=self._clock(),
                    affected_review_case_ids=affected_review_case_ids,
                )
                return self._with_affected_review_cases(
                    self._stale(
                        operation,
                        self._connection_failure(plan)
                        if scope == "connection"
                        else "capability",
                    ),
                    affected,
                )
            if capability.digest != member.capability_digest:
                return self._stale(operation, "capability")
            if not self._authority.current(member.authority):
                return self._stale(operation, "authority")
            if not self._consent.current(member.consent):
                return self._stale(operation, "consent")
            if not self._dependencies.current(member.dependency_digests):
                return self._stale(operation, "dependency")
        try:
            observed = self._target.read(plan)
        except Exception as error:
            # Breadth only reaches the typed suspension record: every error that
            # is not a declared target unavailability is re-raised unchanged, so
            # this handler classifies nothing it has not recognised.
            affected = self._persist_target_suspension(
                plan, error, affected_review_case_ids
            )
            if isinstance(error, TargetServiceUnavailable):
                return self._with_affected_review_cases(
                    self._stale(
                        operation,
                        self._connection_failure(plan)
                        if error.scope == "connection"
                        else "capability",
                    ),
                    affected,
                )
            raise
        if not observed.matches(plan, desired=False, check_version=True):
            # A retry whose earlier request may have landed, and did: record
            # what this read shows instead of a change it did not make. Only
            # history can say a request may have been sent, so a plan that
            # never reached `applying` is not taken for one (#1402).
            if (
                (operation.state, operation.failure) == ("retryable", "target_failure")
                and observed.matches(plan, desired=True, check_version=True)
                and any(
                    json.loads(event)["kind"] == "applying"
                    for event in self._repository.field_effect_event_documents(
                        plan.plan_id
                    )
                )
            ):
                return self._complete(operation, "retryable")
            return self._stale(operation, "target_changed")
        if operation.attempt >= 3:
            return self._stale(operation, "circuit_open")
        if self._repository.field_effect_paused():
            raise GuardedApplicationError("paused")
        if self._draining.is_set():
            raise GuardedApplicationError("draining")
        return None

    def _connection_failure(self, plan: FieldEffectPlan) -> str:
        """Name a stale caused only by a lost connection, within its bound.

        A cover plan keeps `capability`: its stale retires the owner's cover
        consideration and schedules recovery cleanup, which a later plan
        cannot take back, so covers stay terminal as before (#1402).
        """
        if plan.destination == "cover":
            return "capability"
        return (
            "connection"
            if self._repository.field_effect_connection_stales(
                plan.authority.decision_id
            )
            < CONNECTION_REPLAN_LIMIT
            else "connection_exhausted"
        )

    def _persist_target_suspension(
        self,
        plan: FieldEffectPlan,
        error: Exception,
        affected_review_case_ids: set[str] | None,
    ) -> tuple[str, ...]:
        if not isinstance(error, TargetServiceUnavailable):
            return ()
        return self._suspend_field_effect_capability(
            scope=error.scope,
            connection=plan.connection,
            destination=plan.destination,
            reason=error.reason,
            changed_at=self._clock(),
            affected_review_case_ids=affected_review_case_ids,
        )

    def _suspend_field_effect_capability(
        self,
        *,
        scope: str,
        connection: str,
        destination: str,
        reason: str,
        changed_at: int,
        affected_review_case_ids: set[str] | None,
    ) -> tuple[str, ...]:
        affected = self._repository.suspend_field_effect_capability(
            scope=scope,
            connection=connection,
            destination=destination,
            reason=reason,
            changed_at=changed_at,
        )
        if affected_review_case_ids is not None:
            affected_review_case_ids.update(affected)
        return affected

    @staticmethod
    def _with_affected_review_cases(
        operation: FieldEffectOperation, affected_review_case_ids: tuple[str, ...]
    ) -> FieldEffectOperation:
        return (
            operation
            if not affected_review_case_ids
            else replace(
                operation,
                affected_review_case_ids=tuple(sorted(set(affected_review_case_ids))),
            )
        )

    def _recover_cleanup(self, operation: FieldEffectOperation) -> bool:
        if (
            operation.plan.destination != "cover"
            or not self._repository.field_effect_cleanup_pending(operation.plan.plan_id)
        ):
            return False
        try:
            self._cover_recovery.remove(operation.plan.plan_id)
        except Exception:
            # Any removal fault keeps the obligation ``pending`` with an
            # incremented attempt, so restart recovery retries it. This is the
            # durable cleanup lane; unlike the claim path it has somewhere to
            # record the failure and nothing to mask.
            self._repository.fail_field_effect_cleanup(
                operation.plan.plan_id, self._clock()
            )
            return False
        self._repository.complete_field_effect_cleanup(
            operation.plan.plan_id, self._clock()
        )
        return True

    def run_pending_actions(
        self, *, limit: int = 100, plan_id: str | None = None
    ) -> int:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise GuardedApplicationError("invalid_limit")
        completed = 0
        for _ in range(limit):
            action = self._repository.claim_next_field_effect_action(
                self._clock(), plan_id
            )
            if action is None:
                break
            self._run_claimed_field_effect_action(action)
            completed += 1
        return completed

    def _run_next_field_effect_action(
        self, plan_id: str, affected_review_case_ids: set[str] | None = None
    ) -> FieldEffectOperation | None:
        action = self._repository.claim_next_field_effect_action(self._clock(), plan_id)
        return (
            None
            if action is None
            else self._run_claimed_field_effect_action(action, affected_review_case_ids)
        )

    def _run_claimed_field_effect_action(
        self,
        action: dict[str, object],
        affected_review_case_ids: set[str] | None = None,
    ) -> FieldEffectOperation | None:
        if action["kind"] == "targeted_read":
            return self._reconcile_action(
                cast("str", action["plan_id"]), affected_review_case_ids
            )
        operation = self.status(cast("str", action["plan_id"]))
        self._reevaluation.emit(operation.plan)
        self._repository.complete_forward_reevaluation(
            cast("str", action["action_id"]), self._clock()
        )
        return None

    def run_pending_cleanups(self, *, limit: int = 100) -> int:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise GuardedApplicationError("invalid_limit")
        plan_ids = self._repository.pending_field_effect_cleanups(limit)
        return sum(self._recover_cleanup(self.status(plan_id)) for plan_id in plan_ids)

    def recover_pending(self, *, limit: int = 100) -> tuple[int, int]:
        """Bounded restart entrypoint for the two closed recovery obligations."""
        action_count = cleanup_count = 0
        failures: list[Exception] = []
        # Both obligations are attempted even when the first fails. Breadth here
        # collects rather than swallows: every failure is re-raised below, alone
        # or as a group, so neither lane can hide the other's fault.
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

    def recover_startup(self, *, limit: int = 100) -> tuple[int, int, int]:
        """Recover claimed/applying/reconciling effects, actions and cleanup."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise GuardedApplicationError("invalid_limit")
        recovered = 0
        actions = cleanups = 0
        failures: list[Exception] = []
        # Every plan in the batch is attempted even after one fails, and the
        # two pending obligations are attempted after that, on the collecting
        # discipline `recover_pending` already sets: one wedged plan can neither
        # leave the rest of the batch unrecovered nor hide their faults. The
        # count stays exactly what reached a recovered state.
        for plan_id in self._repository.pending_field_effect_plan_ids(limit):
            try:
                self.apply(plan_id)
            except Exception as error:
                failures.append(error)
                continue
            recovered += 1
        try:
            actions, cleanups = self.recover_pending(limit=limit)
        except Exception as error:
            failures.append(error)
        if len(failures) == 1:
            raise failures[0]
        if failures:
            raise ExceptionGroup("startup recovery failures", failures)
        return recovered, actions, cleanups
