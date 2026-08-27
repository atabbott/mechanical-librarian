from __future__ import annotations

import hashlib
import json
import threading
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import patch

import pytest

from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.application.field_effects import (
    GuardedApplicationError,
    GuardedFieldApplication,
    TargetServiceUnavailable,
)
from mechanical_librarian.domain.automatic_decision import AutomaticDecision
from mechanical_librarian.domain.field_effect import (
    SUPPORTED_DESTINATIONS,
    FieldEffectError,
)
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    ReportValueError,
)
from mechanical_librarian.domain.rich_metadata_qualification import (
    F20_QUALIFICATION_CANDIDATE_SHA256,
)

if TYPE_CHECKING:
    # Imported for the `cast` calls below only. The doubles in this module
    # stand in for the concrete `GuardedFieldApplication` and for the
    # `CapabilityObserver` protocol; each `cast` is a runtime no-op that
    # records which collaborator the double is playing.
    from mechanical_librarian.application.field_effects import (
        CapabilityObserver,
        GuardedFieldApplication,
    )


class _Repository:
    def __init__(self) -> None:
        self.ready = [("plan-a1", "alpha"), ("plan-a2", "alpha"), ("plan-b1", "beta")]
        self.operations = {
            plan_id: SimpleNamespace(
                plan=SimpleNamespace(connection="primary", library=library),
                affected_review_case_ids=(),
            )
            for plan_id, library in self.ready
        }

    def pending_field_effect_decision_documents(self, limit, after=None):
        return ()

    def pending_field_effect_plan_ids(self, limit):
        return ()

    def pending_field_effect_action_plan_ids(self, limit):
        return ()

    def next_field_effect_plan_id(self, last_library):
        if not self.ready:
            return None
        index = next(
            (i for i, (_, library) in enumerate(self.ready) if library != last_library),
            0,
        )
        return self.ready.pop(index)

    def get_field_effect(self, plan_id):
        return self.operations[plan_id]


class _Application:
    def __init__(self, shared) -> None:
        self.shared = shared
        self.applied: list[str] = []

    def apply(self, plan_id, _affected_review_case_ids=None):
        with self.shared["guard"]:
            self.shared["active"] += 1
            self.shared["maximum"] = max(self.shared["maximum"], self.shared["active"])
            self.shared["started"].set()
        self.shared["release"].wait(self.shared.get("wait", 1))
        with self.shared["guard"]:
            self.shared["active"] -= 1
        self.applied.append(plan_id)
        return self.shared["repository"].operations[plan_id]

    def resume_admission(self):
        pass

    def begin_drain(self):
        self.shared["drained"].set()

    def run_pending_cleanups(self, limit):
        return 0


class _RecoveryRepository(_Repository):
    def __init__(self) -> None:
        self.plans = [f"plan-{index}" for index in range(205)]
        self.actions = [f"action-{index}" for index in range(205)]
        self.operations = {
            plan_id: SimpleNamespace(
                plan=SimpleNamespace(connection="primary", library="alpha"),
                affected_review_case_ids=(),
            )
            for plan_id in self.plans + self.actions
        }

    def pending_field_effect_plan_ids(self, limit):
        return tuple(self.plans[:limit])

    def pending_field_effect_action_plan_ids(self, limit):
        return tuple(self.actions[:limit])

    def next_field_effect_plan_id(self, last_library):
        return None


class _RecoveryApplication:
    def __init__(self, repository) -> None:
        self.repository = repository
        self.cleanups = 205

    def resume_admission(self):
        pass

    def begin_drain(self):
        pass

    def apply(self, plan_id, _affected_review_case_ids=None):
        self.repository.plans.remove(plan_id)
        return self.repository.operations[plan_id]

    def reconcile(self, plan_id, _affected_review_case_ids=None):
        self.repository.actions.remove(plan_id)
        return self.repository.operations[plan_id]

    def run_pending_cleanups(self, limit):
        completed = min(limit, self.cleanups)
        self.cleanups -= completed
        return completed


class _StuckRecoveryRepository(_Repository):
    """Pending plans spanning more than one page, none removed by the store."""

    def __init__(self) -> None:
        self.plans = ["plan-stuck", *(f"plan-{index}" for index in range(150))]
        self.actions: list[str] = []
        self.operations = {
            plan_id: SimpleNamespace(
                plan=SimpleNamespace(connection="primary", library="alpha"),
                affected_review_case_ids=(),
            )
            for plan_id in self.plans
        }

    def pending_field_effect_plan_ids(self, limit):
        return tuple(self.plans[:limit])

    def pending_field_effect_action_plan_ids(self, limit):
        return tuple(self.actions[:limit])

    def next_field_effect_plan_id(self, last_library):
        return None


class _StuckRecoveryApplication(_RecoveryApplication):
    """Recovery where named plans always fail and so stay pending forever."""

    def __init__(self, repository, stuck: set[str]) -> None:
        super().__init__(repository)
        self.cleanups = 0
        self.stuck = stuck
        self.attempted: list[str] = []

    def apply(self, plan_id, affected_review_case_ids=None):
        self.attempted.append(plan_id)
        if plan_id in self.stuck:
            raise GuardedApplicationError("authority")
        return super().apply(plan_id, affected_review_case_ids)


class _StallingRecoveryApplication(_RecoveryApplication):
    """Recovery that reports success while leaving every plan pending."""

    def __init__(self, repository) -> None:
        super().__init__(repository)
        self.cleanups = 0

    def apply(self, plan_id, _affected_review_case_ids=None):
        return self.repository.operations[plan_id]


class _PagedRepository:
    def __init__(self, decisions) -> None:
        self.decisions = decisions
        self.suspensions: dict[tuple[str, str, str], tuple[str, int]] = {}

    def pending_field_effect_decision_documents(self, limit, after=None):
        values = self.decisions
        if after is not None:
            values = [
                value
                for value in values
                if (value.occurred_at, value.decision_id) > after
            ]
        return tuple(value.to_json() for value in values[:limit])

    def pending_field_effect_activity_example_document(self):
        return self.decisions[-1].to_json() if self.decisions else None

    def pending_field_effect_decision_counts(self):
        counts: dict[tuple[str, str], int] = {}
        for value in self.decisions:
            library = "beta" if value.occurred_at == 101 else "alpha"
            key = (library, value.destination)
            counts[key] = counts.get(key, 0) + 1
        return tuple(
            {
                "connection": "primary",
                "library": library,
                "origin": "human",
                "destination": destination,
                "count": count,
            }
            for (library, destination), count in sorted(counts.items())
        )

    def current_field_effect_consent(self, connection, library, destination):
        if library == "alpha":
            raise FieldEffectError("consent")
        return object()

    def field_effect_backup_ready(self):
        return True

    def field_effect_activity_page(self, **_query):
        return {
            "events": [],
            "report_documents": {},
            "current_waiting": None,
            "page": 1,
            "page_count": 1,
            "page_size": 25,
            "total": 0,
        }

    def field_effect_activity_summary(self):
        return self.field_effect_activity_data(50)

    def grant_field_effect_consent_matrix(self, *arguments):
        raise AssertionError("consent must not be granted before capability proof")

    def suspend_field_effect_capability(
        self,
        *,
        scope,
        connection,
        destination,
        reason,
        changed_at,
    ):
        key = (scope, connection, "" if scope == "connection" else destination)
        current = self.suspensions.get(key)
        if current is not None and current[0] == "unexpected_effect_behavior":
            return ()
        self.suspensions[key] = (reason, changed_at)
        return ()

    def clear_field_effect_capability_suspension(
        self,
        *,
        scope,
        connection,
        destination,
        reason,
        observed_at,
    ):
        key = (scope, connection, destination)
        current = self.suspensions.get(key)
        if current is not None and current[0] == reason and current[1] < observed_at:
            del self.suspensions[key]

    def field_effect_capability_suspension(self, connection, destination):
        connection_value = self.suspensions.get(("connection", connection, ""))
        destination_value = self.suspensions.get(
            ("destination", connection, destination)
        )
        value = connection_value or destination_value
        return None if value is None else value[0]


class _ActivityPagedRepository(_PagedRepository):
    def field_effect_activity_data(self, _limit):
        return {
            "consented_destinations": [],
            "consented_libraries": [],
            "counts": {},
            "events": [],
            "paused": False,
            "suspensions": [],
        }


class _HoldRepository(_Repository):
    def __init__(self) -> None:
        super().__init__()
        self.hold: tuple[str, str] | None = ("primary", "alpha")
        self.token = "first-hold"
        self.pending: tuple[object, ...] | None = None

    def field_effect_manual_hold_context(self, review_case_id, destination):
        return self.hold

    def field_effect_manual_hold_token(self, review_case_id, destination):
        return self.token if self.hold is not None else None

    def request_field_effect_manual_return(
        self,
        review_case_id,
        destination,
        token,
        scan_id,
        requested_at,
        changed_at,
    ):
        if self.hold is None or token != self.token:
            return False
        self.pending = (
            review_case_id,
            destination,
            *self.hold,
            token,
            scan_id,
            requested_at,
            changed_at,
        )
        return True

    def pending_field_effect_manual_returns(self, limit, after=None):
        return () if self.pending is None else (self.pending,)

    def clear_field_effect_manual_hold(
        self, review_case_id, destination, changed_at, token=None
    ):
        if token != self.token:
            return None
        context = self.hold
        self.hold = None
        self.pending = None
        return context


class _Authority:
    def resolve(self, decision):
        library = "beta" if decision.occurred_at == 101 else "alpha"
        return SimpleNamespace(
            connection="primary",
            library=library,
            work_id="work",
            destination="title",
            pre_value="Before",
            decision_value={"kind": "text", "value": "After"},
            target_fingerprint=decision.target_fingerprint,
            target_version="v1",
            dependency_digests=(decision.target_fingerprint,),
            review_case_id=decision.review_case_id,
            source="persisted_review_case",
        )


def test_activity_resolves_at_most_one_current_example() -> None:
    decisions = _decisions(3)

    class CountingAuthority(_Authority):
        def __init__(self) -> None:
            self.resolved: list[str] = []

        def resolve(self, decision):
            self.resolved.append(decision.decision_id)
            return super().resolve(decision)

    authority = CountingAuthority()
    service = ContinuousGuardedApplication(
        _ActivityPagedRepository(decisions),
        {},
        {},
        authority,
        (("primary", "alpha"), ("primary", "beta")),
        lambda: 1,
        lambda *_: None,
        activity_only=True,
    )

    activity = service.activity()

    assert authority.resolved == [decisions[-1].decision_id]
    assert activity["example"] == {
        "after": "After",
        "before": "Before",
        "destination": "title",
        "library": "alpha",
        "work_id": "work",
    }


def test_activity_projection_runs_inside_the_injected_read_snapshot() -> None:
    events: list[str] = []

    class Repository(_ActivityPagedRepository):
        def field_effect_backup_ready(self):
            events.append("backup")
            return True

        def field_effect_activity_page(self, **query):
            events.append("page")
            return super().field_effect_activity_page(**query)

    def snapshot(call):
        events.append("snapshot_enter")
        result = call()
        events.append("snapshot_exit")
        return result

    service = ContinuousGuardedApplication(
        Repository([]),
        {},
        {},
        _Authority(),
        (("primary", "alpha"),),
        lambda: 1,
        lambda *_: None,
        activity_only=True,
        activity_snapshot=snapshot,
    )

    service.activity()

    assert events == ["backup", "snapshot_enter", "page", "snapshot_exit"]


def test_activity_example_binding_race_is_absent_but_storage_fault_propagates() -> None:
    repository = _ActivityPagedRepository(_decisions(1))

    def service(authority):
        return ContinuousGuardedApplication(
            repository,
            {},
            {},
            authority,
            (("primary", "alpha"),),
            lambda: 1,
            lambda *_: None,
            activity_only=True,
        )

    class RaisingAuthority:
        def __init__(self, code: str) -> None:
            self.code = code

        def resolve(self, _decision):
            raise FieldEffectError(self.code)

    assert service(RaisingAuthority("authority_binding")).activity()["example"] is None
    with pytest.raises(FieldEffectError, match="^persistence$"):
        service(RaisingAuthority("persistence")).activity()


class _Planner:
    def __init__(self) -> None:
        self.planned: list[str] = []

    def plan(self, decision, **values):
        self.planned.append(decision.decision_id)
        return object()


class _AllowedCapability:
    def observe(self, probe):
        return SimpleNamespace(category="allowed", digest="a" * 64)


class _DeniedCapability:
    def observe(self, probe):
        return SimpleNamespace(category="insufficient_permission", digest="b" * 64)


class _MixedCapability:
    def observe(self, probe):
        category = "field_unavailable" if probe.destination == "subtitle" else "allowed"
        return SimpleNamespace(category=category, digest="c" * 64)


class _ConnectionUnavailableCapability:
    def observe(self, _):
        return SimpleNamespace(category="connection_unavailable", digest="d" * 64)


@pytest.mark.parametrize("method_name", ("apply", "reconcile"))
@pytest.mark.parametrize(
    ("affected_review_case_ids", "expected_announcements"),
    (
        (
            ("case-b", "case-a", "case-b"),
            [("activity", None), ("library", "case-a"), ("library", "case-b")],
        ),
        ((), []),
    ),
)
def test_guarded_suspension_commit_flushes_affected_cases_before_propagating(
    method_name, affected_review_case_ids, expected_announcements
) -> None:
    plan = SimpleNamespace(
        plan_id="plan", connection="primary", library="alpha", destination="title"
    )

    class Repository:
        def __init__(self) -> None:
            self.operation = SimpleNamespace(
                plan=plan,
                state="planned" if method_name == "apply" else "reconciling",
                affected_review_case_ids=(),
            )
            self.suspension_committed = False

        def get_field_effect(self, _plan_id):
            return self.operation

        def field_effect_capability_suspension(self, *_):
            return None

        def suspend_field_effect_capability(self, **_):
            self.suspension_committed = True
            return affected_review_case_ids

        def transition_field_effect(self, *_):
            raise GuardedApplicationError("after_suspension")

        def claim_next_field_effect_action(self, *_):
            return (
                None
                if method_name == "apply"
                else {"kind": "targeted_read", "plan_id": "plan"}
            )

        def complete_field_effect_reconciliation(self, *_):
            raise GuardedApplicationError("after_suspension")

    class Target:
        def read(self, _plan):
            raise TargetServiceUnavailable("destination")

        def apply(self, _plan):
            raise AssertionError("capability preclaim must stop before apply")

    repository = Repository()
    guarded = GuardedFieldApplication(
        repository,
        Target(),
        SimpleNamespace(ready=lambda _: True),
        SimpleNamespace(
            observe=lambda _: SimpleNamespace(
                category="field_unavailable", digest="capability"
            )
        ),
        SimpleNamespace(current=lambda _: True),
        SimpleNamespace(current=lambda _: True),
        SimpleNamespace(current=lambda _: True),
        SimpleNamespace(),
        SimpleNamespace(),
        lambda: 1,
    )
    service = ContinuousGuardedApplication(
        repository,
        {("primary", "alpha"): guarded},
        {("primary", "alpha"): _AllowedCapability()},
        _Authority(),
        (("primary", "alpha"),),
        lambda: 1,
        lambda *_: None,
    )
    announcements: list[tuple[str, str | None]] = []
    service.attach_updates(
        lambda topic, case_id: announcements.append((topic, case_id))
    )

    with pytest.raises(GuardedApplicationError, match="after_suspension"):
        getattr(service, method_name)("plan")

    assert repository.suspension_committed
    assert repository.operation.affected_review_case_ids == ()
    assert announcements == expected_announcements

    if affected_review_case_ids:

        def failing_update(*_: object) -> None:
            raise RuntimeError("flush")

        service.attach_updates(failing_update)
        with pytest.raises(GuardedApplicationError, match="after_suspension"):
            getattr(service, method_name)("plan")


def test_preplan_cover_authority_drift_retires_the_selected_consideration() -> None:
    selected = {"kind": "cover", "value": "cover_" + "f" * 64}
    idempotency_digest = hashlib.sha256(b"preplan-cover-drift").hexdigest()
    request = {
        "actor": "owner",
        "destination": "cover",
        "idempotency_digest": idempotency_digest,
        "outcome": "accept",
        "proposal_id": "review_proposal_" + "a" * 64,
        "review_case_id": "review_case_" + "b" * 64,
        "selected_value": selected,
        "target_fingerprint": "c" * 64,
    }
    request_digest = hashlib.sha256(
        json.dumps(
            request, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode()
    ).hexdigest()
    decision_id = (
        "human_decision_"
        + hashlib.sha256(
            json.dumps(
                {"occurred_at": 101, "request_digest": request_digest},
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
    )
    decision = HumanDecision(
        decision_id,
        request["review_case_id"],
        request["proposal_id"],
        request["target_fingerprint"],
        "cover",
        "owner",
        "accept",
        selected,
        idempotency_digest,
        request_digest,
        101,
    )

    class Repository(_PagedRepository):
        retired: list[tuple[str, int]] = []

        def retire_stale_cover_decision(self, decision_id, changed_at):
            self.retired.append((decision_id, changed_at))
            return True

    class DriftedAuthority:
        def resolve(self, _decision):
            raise FieldEffectError("authority_binding")

    repository = Repository([decision])
    service = ContinuousGuardedApplication(
        repository,
        cast(
            "dict[tuple[str, str], GuardedFieldApplication]",
            {("primary", "beta"): _Planner()},
        ),
        {("primary", "beta"): _AllowedCapability()},
        DriftedAuthority(),
        (("primary", "beta"),),
        lambda: 102,
        lambda *_: None,
    )

    assert 0 == service.schedule()
    assert repository.retired == [(decision.decision_id, 102)]

    class FaultedAuthority:
        def resolve(self, _decision):
            raise FieldEffectError("persistence")

    faulted = ContinuousGuardedApplication(
        repository,
        cast(
            "dict[tuple[str, str], GuardedFieldApplication]",
            {("primary", "beta"): _Planner()},
        ),
        {("primary", "beta"): _AllowedCapability()},
        FaultedAuthority(),
        (("primary", "beta"),),
        lambda: 103,
        lambda *_: None,
    )

    # Drift retires the selection; a storage fault must not. It says nothing
    # about the binding, so it propagates rather than writing a stale cover
    # event and swallowing itself.
    with pytest.raises(FieldEffectError) as raised:
        faulted.schedule()
    assert raised.value.code == "persistence"
    assert repository.retired == [(decision.decision_id, 102)]


def _prior_f20_decision() -> AutomaticDecision:
    def digest(value: object) -> str:
        return hashlib.sha256(
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

    current = AutomaticDecision.create(
        review_case_id="review_case_" + "1" * 64,
        report_id="report_" + "2" * 64,
        proposal_set_id="proposal_set_" + "3" * 64,
        proposal_id="review_proposal_" + "4" * 64,
        target_fingerprint="b" * 64,
        destination="genres",
        outcome="candidate",
        candidate_id="alternative_" + "5" * 64,
        resolved_policy_id="resolved_policy_" + "6" * 64,
        policy_generation=1,
        rule_id="shadow_rule_" + "7" * 64,
        policy_category="genres",
        policy_option="add",
        qualification_manifest_sha256=F20_QUALIFICATION_CANDIDATE_SHA256,
        source_outcome_id="shadow_outcome_" + "8" * 64,
        bundle_id=None,
        occurred_at=1,
    )
    core = {
        **current._core(),
        "qualification_manifest_sha256": (
            "901e25432750d3f02df1cd311f5a7146e56d5686bd4b6bc0ac589e5db3eb221a"
        ),
    }
    return AutomaticDecision(
        decision_id="automatic_decision_" + digest({**core, "occurred_at": 1}),
        **core,
        idempotency_digest=digest(core),
        occurred_at=1,
    )


def test_schedule_and_activity_accept_a_prior_persisted_f20_decision() -> None:
    decision = _prior_f20_decision()

    class Repository(_PagedRepository):
        def pending_field_effect_decision_counts(self):
            return (
                {
                    "connection": "primary",
                    "library": "alpha",
                    "origin": "automatic",
                    "destination": "title",
                    "count": 1,
                },
            )

        def field_effect_activity_data(self, _limit):
            return {
                "consented_destinations": [],
                "consented_libraries": [],
                "counts": {},
                "events": [],
                "paused": False,
                "suspensions": [],
            }

    repository = Repository([decision])
    service = ContinuousGuardedApplication(
        repository,
        cast(
            "dict[tuple[str, str], GuardedFieldApplication]",
            {("primary", "alpha"): _Planner()},
        ),
        {("primary", "alpha"): _AllowedCapability()},
        _Authority(),
        (("primary", "alpha"),),
        lambda: 1,
        lambda *_: None,
    )

    assert 0 == service.schedule()
    activity = service.activity()
    assert "continuous-field-activity-v2" == activity["schema_version"]
    assert 1 == activity["prepared"]["automatic"]


def test_return_to_automatic_releases_only_after_current_scan_completes() -> None:
    class ReleaseRepository(_HoldRepository):
        def __init__(self) -> None:
            super().__init__()
            self.decisions = _decisions(1)

        def pending_field_effect_decision_documents(self, limit, after=None):
            values = self.decisions
            if after is not None:
                values = [
                    value
                    for value in values
                    if (value.occurred_at, value.decision_id) > after
                ]
            return tuple(value.to_json() for value in values[:limit])

        def current_field_effect_consent(self, *_context):
            if self.hold is not None:
                raise FieldEffectError("consent")
            return object()

        def suspend_field_effect_capability(self, **_values):
            return ()

        def clear_field_effect_capability_suspension(self, **_values):
            return None

        def field_effect_capability_suspension(self, *_context):
            return None

    repository = ReleaseRepository()
    planner = _Planner()
    scans = []
    progress = SimpleNamespace(
        scan_id="review_scan",
        state="processing",
        rescan_requested=False,
        updated_at=10,
    )
    service = ContinuousGuardedApplication(
        repository,
        cast(
            "dict[tuple[str, str], GuardedFieldApplication]",
            {("primary", "alpha"): planner},
        ),
        {("primary", "alpha"): _AllowedCapability()},
        _Authority(),
        (("primary", "alpha"),),
        lambda: 1,
        lambda *context: scans.append(context) or progress,  # type: ignore[func-returns-value]  # reason: deliberate `append(...) or value` idiom - the recorded call is the point and `append` returning `None` is what lets `progress` be the result.
        scan_progress=lambda *_: progress,
    )
    service.return_to_automatic("review_case", "title")
    assert repository.hold is not None
    assert scans == [("primary", "alpha")]
    service.schedule()
    assert repository.hold is not None
    assert planner.planned == []
    progress.state = "completed"
    progress.updated_at = 11
    service.schedule()
    assert repository.hold is None
    assert planner.planned == [repository.decisions[0].decision_id]


def test_return_to_automatic_recovers_the_persisted_request_after_restart() -> None:
    repository = _HoldRepository()
    progress = SimpleNamespace(
        scan_id="review_scan",
        state="processing",
        rescan_requested=False,
        updated_at=10,
    )
    configured = (("primary", "alpha"),)
    first = ContinuousGuardedApplication(
        repository,
        cast(
            "dict[tuple[str, str], GuardedFieldApplication]",
            {configured[0]: _Planner()},
        ),
        {configured[0]: _AllowedCapability()},
        _Authority(),
        configured,
        lambda: 1,
        lambda *_: progress,
        scan_progress=lambda *_: progress,
    )
    first.return_to_automatic("review_case", "title")
    progress.state = "completed"
    progress.updated_at = 11
    restarted = ContinuousGuardedApplication(
        repository,
        cast(
            "dict[tuple[str, str], GuardedFieldApplication]",
            {configured[0]: _Planner()},
        ),
        {configured[0]: _AllowedCapability()},
        _Authority(),
        configured,
        lambda: 2,
        lambda *_: progress,
        scan_progress=lambda *_: progress,
    )

    restarted.schedule()

    assert repository.hold is None


def test_pending_return_paging_reaches_completed_request_after_first_hundred() -> None:
    repository = _HoldRepository()
    repository.pending = None
    rows = tuple(
        (
            f"review_case_{index:03d}",
            "title",
            "primary",
            "alpha",
            f"hold-{index:03d}",
            "review_scan",
            10,
            index,
        )
        for index in range(101)
    )
    cleared = []

    def pending(limit, after=None):
        selected = rows
        if after is not None:
            selected = tuple(row for row in rows if (row[7], row[0], row[1]) > after)
        return selected[:limit]

    repository.pending_field_effect_manual_returns = pending  # type: ignore[method-assign]  # reason: deliberate per-instance stub of the double's method for this case.
    repository.clear_field_effect_manual_hold = (  # type: ignore[method-assign]  # reason: deliberate per-instance stub of the double's method for this case.
        lambda review_case_id, destination, changed_at, token=None: (
            cleared.append(review_case_id) or ("primary", "alpha")  # type: ignore[func-returns-value]  # reason: deliberate `append(...) or value` idiom - the recorded call is the point and `append` returning `None` is what lets the context tuple be the result.
        )
    )
    progress = SimpleNamespace(
        scan_id="review_scan",
        state="processing",
        rescan_requested=False,
        updated_at=10,
    )
    service = ContinuousGuardedApplication(
        repository,
        cast(
            "dict[tuple[str, str], GuardedFieldApplication]",
            {("primary", "alpha"): _Planner()},
        ),
        {("primary", "alpha"): _AllowedCapability()},
        _Authority(),
        (("primary", "alpha"),),
        lambda: 200,
        lambda *_: progress,
        scan_progress=lambda *_: progress,
    )

    service.schedule()
    progress.state = "completed"
    progress.updated_at = 11
    service.schedule()

    assert cleared == ["review_case_100"]


def test_pending_return_for_removed_library_does_not_block_scheduler() -> None:
    repository = _HoldRepository()
    repository.pending = (
        "review_case",
        "title",
        "primary",
        "removed",
        "first-hold",
        "review_scan",
        10,
        10,
    )
    reads = []
    service = ContinuousGuardedApplication(
        repository,
        cast(
            "dict[tuple[str, str], GuardedFieldApplication]",
            {("primary", "alpha"): _Planner()},
        ),
        {("primary", "alpha"): _AllowedCapability()},
        _Authority(),
        (("primary", "alpha"),),
        lambda: 20,
        lambda *_: SimpleNamespace(),
        scan_progress=lambda *context: reads.append(context),
    )

    service.schedule()

    assert reads == []
    assert repository.hold is not None


def test_failed_or_superseded_scan_never_releases_manual_hold() -> None:
    repository = _HoldRepository()
    progress = SimpleNamespace(
        scan_id="review_scan",
        state="completed_with_failures",
        rescan_requested=False,
        updated_at=10,
    )
    service = ContinuousGuardedApplication(
        repository,
        cast(
            "dict[tuple[str, str], GuardedFieldApplication]",
            {("primary", "alpha"): _Planner()},
        ),
        {("primary", "alpha"): _AllowedCapability()},
        _Authority(),
        (("primary", "alpha"),),
        lambda: 1,
        lambda *_: progress,
        scan_progress=lambda *_: progress,
    )
    service.return_to_automatic("review_case", "title")
    service.schedule()
    assert repository.hold is not None
    progress.state = "completed"
    progress.rescan_requested = True
    progress.updated_at = 11
    service.schedule()
    assert repository.hold is not None


def test_later_manual_hold_cannot_be_released_by_older_scan() -> None:
    repository = _HoldRepository()
    progress = SimpleNamespace(
        scan_id="review_scan",
        state="processing",
        rescan_requested=False,
        updated_at=10,
    )
    service = ContinuousGuardedApplication(
        repository,
        cast(
            "dict[tuple[str, str], GuardedFieldApplication]",
            {("primary", "alpha"): _Planner()},
        ),
        {("primary", "alpha"): _AllowedCapability()},
        _Authority(),
        (("primary", "alpha"),),
        lambda: 1,
        lambda *_: progress,
        scan_progress=lambda *_: progress,
    )
    service.return_to_automatic("review_case", "title")
    repository.token = "later-hold"
    progress.state = "completed"
    progress.updated_at = 11
    service.schedule()
    assert repository.hold is not None


def test_scan_callback_failures_are_bounded_and_leave_the_hold_active() -> None:
    repository = _HoldRepository()

    def failed_request(*_):
        raise RuntimeError("private failure")

    service = ContinuousGuardedApplication(
        repository,
        cast(
            "dict[tuple[str, str], GuardedFieldApplication]",
            {("primary", "alpha"): _Planner()},
        ),
        {("primary", "alpha"): _AllowedCapability()},
        _Authority(),
        (("primary", "alpha"),),
        lambda: 1,
        failed_request,
    )
    with pytest.raises(GuardedApplicationError, match="scan"):
        service.return_to_automatic("review_case", "title")
    assert repository.hold is not None

    progress = SimpleNamespace(
        scan_id="review_scan",
        state="processing",
        rescan_requested=False,
        updated_at=10,
    )
    failing = [True]

    def read_progress(*_):
        if failing[0]:
            raise RuntimeError("private failure")
        return progress

    service = ContinuousGuardedApplication(
        repository,
        cast(
            "dict[tuple[str, str], GuardedFieldApplication]",
            {("primary", "alpha"): _Planner()},
        ),
        {("primary", "alpha"): _AllowedCapability()},
        _Authority(),
        (("primary", "alpha"),),
        lambda: 1,
        lambda *_: progress,
        scan_progress=read_progress,
    )
    service.return_to_automatic("review_case", "title")
    with pytest.raises(GuardedApplicationError, match="scan"):
        service.schedule()
    assert repository.hold is not None
    failing[0] = False
    progress.state = "completed"
    progress.updated_at = 11
    service.schedule()
    assert repository.hold is None


def test_enable_refuses_read_only_capability_before_granting_consent() -> None:
    decision = _decisions(1)[0]
    repository = _PagedRepository([decision])
    service = ContinuousGuardedApplication(
        repository,
        cast(
            "dict[tuple[str, str], GuardedFieldApplication]",
            {("primary", "alpha"): _Planner()},
        ),
        {("primary", "alpha"): _DeniedCapability()},
        _Authority(),
        (("primary", "alpha"),),
        lambda: 1,
        lambda *_: None,
    )
    with pytest.raises(GuardedApplicationError, match="capability"):
        service.enable((("alpha", ("title",)),))
    assert set(repository.suspensions.values()) == {("insufficient_permission", 1)}


def test_enable_grants_only_field_locally_eligible_destinations() -> None:
    decision = _decisions(1)[0]

    class Repository(_PagedRepository):
        def __init__(self):
            super().__init__([decision])
            self.grants = None

        def grant_field_effect_consent_matrix(self, *arguments):
            self.grants = arguments

        def field_effect_activity_data(self, _):
            return {"cleanup": {"pending": 0, "exhausted": 0}, "suspensions": []}

    repository = Repository()
    service = ContinuousGuardedApplication(
        repository,
        cast(
            "dict[tuple[str, str], GuardedFieldApplication]",
            {("primary", "alpha"): _Planner()},
        ),
        {("primary", "alpha"): _MixedCapability()},
        _Authority(),
        (("primary", "alpha"),),
        lambda: 1,
        lambda *_: None,
    )
    eligible = tuple(sorted(set(SUPPORTED_DESTINATIONS) - {"subtitle"}))
    service.enable((("alpha", eligible),))
    assert repository.grants is not None
    assert "subtitle" not in repository.grants[1][0][1]


def test_enable_rejects_a_stale_visible_destination_subset() -> None:
    decision = _decisions(1)[0]

    class Repository(_PagedRepository):
        def __init__(self):
            super().__init__([decision])
            self.grants = None

        def grant_field_effect_consent_matrix(self, *arguments):
            self.grants = arguments

    repository = Repository()
    service = ContinuousGuardedApplication(
        repository,
        cast(
            "dict[tuple[str, str], GuardedFieldApplication]",
            {("primary", "alpha"): _Planner()},
        ),
        {("primary", "alpha"): _MixedCapability()},
        _Authority(),
        (("primary", "alpha"),),
        lambda: 1,
        lambda *_: None,
    )

    with pytest.raises(GuardedApplicationError, match="capability"):
        service.enable((("alpha", ("title",)),))

    assert repository.grants is None


def _readiness_service(repository, authority) -> ContinuousGuardedApplication:
    return ContinuousGuardedApplication(
        repository,
        cast(
            "dict[tuple[str, str], GuardedFieldApplication]",
            {("primary", "alpha"): _Planner()},
        ),
        {("primary", "alpha"): _AllowedCapability()},
        authority,
        (("primary", "alpha"),),
        lambda: 1,
        lambda *_: None,
    )


def test_a_readiness_storage_fault_is_neither_absorbed_nor_cached() -> None:
    decision = _decisions(1)[0]

    class Repository(_PagedRepository):
        def field_effect_activity_data(self, _):
            return {
                "consented_destinations": [],
                "events": [],
                "paused": False,
                "suspensions": [],
            }

    truthful = _readiness_service(Repository([decision]), _Authority())
    expected = truthful.activity()["capabilities"]

    class FaultingOnceAuthority:
        def __init__(self) -> None:
            self.calls = 0
            self.healthy = _Authority()

        def resolve(self, decision):
            self.calls += 1
            if self.calls == 1:
                raise FieldEffectError("persistence")
            return self.healthy.resolve(decision)

    authority = FaultingOnceAuthority()
    service = _readiness_service(Repository([decision]), authority)

    with pytest.raises(FieldEffectError) as raised:
        service.activity()
    assert raised.value.code == "persistence"

    # The clock never advances, so anything written to the 30-second capability
    # cache under the fault would be served to every later caller. A library
    # with no representative reports `not_ready` on every destination, so
    # absorbing the fault would have cached a confident false negative.
    assert service.activity()["capabilities"] == expected
    assert expected[0]["state"] == "allowed"


def test_activity_exposes_incremental_consent_for_a_partially_enabled_library() -> None:
    decision = _decisions(1)[0]

    class Repository(_PagedRepository):
        def current_field_effect_consent(self, connection, library, destination):
            raise AssertionError("Activity must use the bounded consent matrix")

        def field_effect_activity_data(self, _):
            return {
                "consented_destinations": [
                    {
                        "connection": "primary",
                        "library": "alpha",
                        "destination": "title",
                    }
                ],
                "events": [],
                "paused": False,
                "suspensions": [],
            }

    repository = Repository([decision])
    service = ContinuousGuardedApplication(
        repository,
        cast(
            "dict[tuple[str, str], GuardedFieldApplication]",
            {("primary", "alpha"): _Planner()},
        ),
        {("primary", "alpha"): _MixedCapability()},
        _Authority(),
        (("primary", "alpha"),),
        lambda: 1,
        lambda *_: None,
    )

    activity = service.activity()

    assert activity["enabled_libraries"] == [
        {"connection": "primary", "library": "alpha"}
    ]
    capability = activity["capabilities"][0]
    destinations = {
        destination["destination"]: destination
        for destination in capability["destinations"]
    }
    assert destinations["title"]["consented"]
    assert not destinations["authors"]["consented"]
    assert destinations["subtitle"]["state"] == "field_unavailable"


def test_waiting_is_split_by_next_step_and_counted_per_destination() -> None:
    """`46 Waiting safely` is four different next steps, and this separates them.

    An owner reading one number cannot tell consent they can give from a retry
    that time resolves, and cannot tell which permission would carry which
    prepared work. Both are read off state the projection already assembled, so
    a reason can never disagree with the queue it describes (#639).
    """

    class Repository(_PagedRepository):
        def pending_field_effect_decision_counts(self):
            return (
                {
                    "connection": "primary",
                    "library": "alpha",
                    "origin": "human",
                    "destination": "title",
                    "count": 2,
                },
                {
                    "connection": "primary",
                    "library": "alpha",
                    "origin": "automatic",
                    "destination": "authors",
                    "count": 3,
                },
            )

        def field_effect_activity_data(self, _limit):
            return {
                "consented_destinations": [
                    {
                        "connection": "primary",
                        "library": "alpha",
                        "destination": "title",
                    }
                ],
                "counts": {
                    "planned": 4,
                    "retryable": 2,
                    "claimed": 1,
                    "applying": 1,
                },
                "events": [],
                "paused": False,
                "suspensions": [],
            }

    service = ContinuousGuardedApplication(
        Repository([]),
        cast(
            "dict[tuple[str, str], GuardedFieldApplication]",
            {("primary", "alpha"): _Planner()},
        ),
        {("primary", "alpha"): _MixedCapability()},
        _Authority(),
        (("primary", "alpha"),),
        lambda: 1,
        lambda *_: None,
    )

    activity = service.activity()

    assert activity["waiting"] == {
        # Prepared against `authors`, which no consent covers: only the owner
        # resolves this, and the `Enable writes` action is what resolves it.
        "awaiting_permission": 3,
        "scheduled": 4,
        "retrying": 2,
        "held_for_recovery": 0,
        "applying": 2,
    }
    destinations = {
        destination["destination"]: destination
        for destination in activity["capabilities"][0]["destinations"]
    }
    assert destinations["title"]["prepared"] == 2
    assert destinations["authors"]["prepared"] == 3
    assert destinations["series"]["prepared"] == 0


def test_activity_only_projection_uses_only_persisted_capability_state() -> None:
    repository = _PagedRepository([_decisions(1)[0]])
    repository.suspensions[("destination", "primary", "subtitle")] = (
        "field_unavailable",
        1,
    )
    service = ContinuousGuardedApplication(
        repository,
        {},
        {},
        _Authority(),
        (("primary", "alpha"),),
        lambda: 1,
        lambda *_: None,
        activity_only=True,
    )

    readiness = service._capability_readiness(("alpha",), force=True)

    destinations = {
        value["destination"]: value["state"] for value in readiness[0]["destinations"]
    }
    assert "not_ready" == readiness[0]["state"]
    assert "field_unavailable" == destinations["subtitle"]
    assert "not_ready" == destinations["title"]
    assert {
        ("destination", "primary", "subtitle"): ("field_unavailable", 1)
    } == repository.suspensions


def test_activity_only_projection_rejects_capability_observers() -> None:
    with pytest.raises(GuardedApplicationError):
        ContinuousGuardedApplication(
            _PagedRepository([]),
            {},
            {("primary", "alpha"): _MixedCapability()},
            _Authority(),
            (("primary", "alpha"),),
            lambda: 1,
            lambda *_: None,
            activity_only=True,
        )


def test_activity_accepts_activated_recovery_states_and_rejects_unknown_state() -> None:
    class Repository(_PagedRepository):
        def field_effect_activity_data(self, _limit):
            return {
                "consented_destinations": [],
                "consented_libraries": [],
                "counts": {},
                "events": [],
                "paused": False,
                "suspensions": [],
            }

    def service(snapshot):
        return ContinuousGuardedApplication(
            Repository([]),
            {},
            {},
            _Authority(),
            (("primary", "alpha"),),
            lambda: 1,
            lambda *_: None,
            activity_only=True,
            provider_recovery_snapshot=lambda: snapshot,
        )

    snapshot = (
        {"state": "fanout", "affected_count": 3, "next_eligible_at": None},
        {"state": "exhausted", "affected_count": 1, "next_eligible_at": None},
    )
    assert list(snapshot) == service(snapshot).activity()["provider_recovery"]
    with pytest.raises(GuardedApplicationError, match="activity"):
        service(
            ({"state": "unknown", "affected_count": 1, "next_eligible_at": None},)
        ).activity()


def test_activity_strips_valid_cleanup_and_rejects_malformed_cleanup() -> None:
    decision = _decisions(1)[0]

    class Repository(_PagedRepository):
        def __init__(self, cleanup):
            super().__init__([decision])
            self._cleanup = cleanup

        def field_effect_activity_data(self, limit):
            return {
                "cleanup": self._cleanup,
                "consented_destinations": [],
                "consented_libraries": [],
                "counts": {},
                "events": [],
                "paused": False,
                "suspensions": [],
            }

        def field_effect_activity_page(self, **_query):
            data = self.field_effect_activity_data(50)
            return {
                "events": data.get("events", []),
                "report_documents": data.get("report_documents", {}),
                "current_waiting": None,
                "page": 1,
                "page_count": 1,
                "page_size": 25,
                "total": 2,
            }

    def service(cleanup):
        return ContinuousGuardedApplication(
            Repository(cleanup),
            cast(
                "dict[tuple[str, str], GuardedFieldApplication]",
                {("primary", "alpha"): _Planner()},
            ),
            {("primary", "alpha"): _AllowedCapability()},
            _Authority(),
            (("primary", "alpha"),),
            lambda: 1,
            lambda *_: None,
        )

    assert "cleanup" not in service({"pending": 0, "exhausted": 0}).activity()
    with pytest.raises(GuardedApplicationError, match="activity"):
        service({"pending": "zero", "exhausted": 0}).activity()


def test_activity_parses_each_distinct_private_report_once() -> None:
    decision = _decisions(1)[0]

    class Repository(_PagedRepository):
        def field_effect_activity_data(self, limit):
            event = {
                "connection": "primary",
                "destination": "title",
                "failure": None,
                "library": "alpha",
                "occurred_at": 1,
                "origin": "human",
                "report_id": "report-1",
                "state": "verified",
                "work_id": "work",
            }
            return {
                "consented_destinations": [],
                "consented_libraries": [],
                "counts": {},
                "events": [dict(event), dict(event)],
                "paused": False,
                "report_documents": {"report-1": "{}"},
                "suspensions": [],
            }

        def field_effect_activity_page(self, **_query):
            data = self.field_effect_activity_data(50)
            return {
                "events": data["events"],
                "report_documents": data["report_documents"],
                "current_waiting": None,
                "page": 1,
                "page_count": 1,
                "page_size": 25,
                "total": 2,
            }

    service = ContinuousGuardedApplication(
        Repository([decision]),
        cast(
            "dict[tuple[str, str], GuardedFieldApplication]",
            {("primary", "alpha"): _Planner()},
        ),
        {("primary", "alpha"): _AllowedCapability()},
        _Authority(),
        (("primary", "alpha"),),
        lambda: 1,
        lambda *_: None,
    )
    report = SimpleNamespace(
        report_id="report-1",
        target=SimpleNamespace(
            connection="primary",
            library="alpha",
            item="work",
            current_title="Readable work",
            fields=(),
        ),
    )

    with patch(
        "mechanical_librarian.application.continuous_field_effects.MetadataReport.from_json",
        return_value=report,
    ) as parse:
        activity = service.activity()

    parse.assert_called_once_with("{}")
    assert [event["work_label"] for event in activity["events"]] == [
        "Readable work",
        "Readable work",
    ]
    assert "report_documents" not in activity
    assert all("report_id" not in event for event in activity["events"])


def test_activity_refuses_every_stored_report_the_parser_cannot_read() -> None:
    """Every unreadable stored report is `activity`, surrogates included.

    `MetadataReport.from_json` used to run its length guard outside the
    block converting `UnicodeError`, so a stored row holding an unpaired
    surrogate escaped it as `UnicodeEncodeError` and narrowing this handler
    to `ReportValueError` let that reach the owner as an unhandled codec
    fault. The guard now sits under that boundary, so the handler narrows
    and a fault in the parser itself is no longer refused as activity
    (#698).
    """
    decision = _decisions(1)[0]

    def repository_for(report_document: str) -> type[_PagedRepository]:
        class Repository(_PagedRepository):
            def field_effect_activity_data(self, limit):
                event = {
                    "connection": "primary",
                    "destination": "title",
                    "failure": None,
                    "library": "alpha",
                    "occurred_at": 1,
                    "origin": "human",
                    "report_id": "report-1",
                    "state": "verified",
                    "work_id": "work",
                }
                return {
                    "consented_destinations": [],
                    "consented_libraries": [],
                    "counts": {},
                    "events": [dict(event)],
                    "paused": False,
                    "report_documents": {"report-1": report_document},
                    "suspensions": [],
                }

            def field_effect_activity_page(self, **_query):
                data = self.field_effect_activity_data(50)
                return {
                    "events": data["events"],
                    "report_documents": data["report_documents"],
                    "current_waiting": None,
                    "page": 1,
                    "page_count": 1,
                    "page_size": 25,
                    "total": 1,
                }

        return Repository

    # The fix: the surrogate row now reaches the handler as the parser's
    # one declared refusal rather than as a raw codec fault.
    with pytest.raises(ReportValueError):
        MetadataReport.from_json('{"a": "\ud800"}')

    def application(report_document: str) -> ContinuousGuardedApplication:
        return ContinuousGuardedApplication(
            repository_for(report_document)([decision]),
            cast(
                "dict[tuple[str, str], GuardedFieldApplication]",
                {("primary", "alpha"): _Planner()},
            ),
            {("primary", "alpha"): _AllowedCapability()},
            _Authority(),
            (("primary", "alpha"),),
            lambda: 1,
            lambda *_: None,
        )

    for report_document in ('{"a": "\ud800"}', "{}"):
        with pytest.raises(GuardedApplicationError, match="activity"):
            application(report_document).activity()

    # A fault in the parser itself is no longer refused as activity.
    fault = RuntimeError("parser is broken")
    with patch.object(MetadataReport, "from_json", side_effect=fault):
        with pytest.raises(RuntimeError) as surfaced:
            application("{}").activity()
        assert surfaced.value is fault


def test_successful_probe_preserves_unexpected_behavior_suspension() -> None:
    decision = _decisions(1)[0]
    repository = _PagedRepository([decision])
    repository.suspend_field_effect_capability(
        scope="destination",
        connection="primary",
        destination="title",
        reason="unexpected_effect_behavior",
        changed_at=1,
    )
    service = ContinuousGuardedApplication(
        repository,
        cast(
            "dict[tuple[str, str], GuardedFieldApplication]",
            {("primary", "alpha"): _Planner()},
        ),
        {("primary", "alpha"): _AllowedCapability()},
        _Authority(),
        (("primary", "alpha"),),
        lambda: 2,
        lambda *_: None,
    )

    service._capability_readiness(("alpha",), force=True)

    assert (
        repository.suspensions[("destination", "primary", "title")][0]
        == "unexpected_effect_behavior"
    )
    readiness = service._capability_readiness(("alpha",), force=True)[0]
    title = next(
        destination
        for destination in readiness["destinations"]
        if destination["destination"] == "title"
    )
    assert title["state"] == "unexpected_effect_behavior"


@pytest.mark.parametrize(
    ("capability", "state"),
    (
        (_MixedCapability(), "partially_allowed"),
        (_ConnectionUnavailableCapability(), "connection_unavailable"),
    ),
    ids=("mixed", "connection_unavailable"),
)
def test_readiness_is_exactly_field_bounded_and_connection_fail_closed(
    capability: CapabilityObserver, state: str
) -> None:
    decision = _decisions(1)[0]
    repository = _PagedRepository([decision])
    service = ContinuousGuardedApplication(
        repository,
        cast(
            "dict[tuple[str, str], GuardedFieldApplication]",
            {("primary", "alpha"): _Planner()},
        ),
        {("primary", "alpha"): capability},
        _Authority(),
        (("primary", "alpha"),),
        lambda: 1,
        lambda *_: None,
    )
    readiness = service._capability_readiness(("alpha",), force=True)[0]
    assert readiness["state"] == state
    assert len(readiness["destinations"]) == 14
    assert {value["destination"] for value in readiness["destinations"]} == set(
        SUPPORTED_DESTINATIONS
    )


def test_blocked_oldest_page_does_not_starve_later_eligible_work() -> None:
    decisions = _decisions(101)

    repository = _PagedRepository(decisions)
    planners = {("primary", "alpha"): _Planner(), ("primary", "beta"): _Planner()}
    capabilities = {key: _AllowedCapability() for key in planners}
    service = ContinuousGuardedApplication(
        repository,
        planners,
        capabilities,
        _Authority(),
        tuple(planners),
        lambda: 1,
        lambda *_: None,
    )
    assert service.schedule(100) == 0
    assert service.schedule(100) == 1
    assert len(planners[("primary", "beta")].planned) == 1


def test_idle_scheduler_keeps_its_completed_cursor_until_a_real_wake() -> None:
    class ObservedRepository(_PagedRepository):
        def __init__(self) -> None:
            super().__init__(_decisions(100))
            self.afters: list[tuple[int, str] | None] = []

        def pending_field_effect_decision_documents(self, limit, after=None):
            self.afters.append(after)
            return super().pending_field_effect_decision_documents(limit, after)

    repository = ObservedRepository()
    service = ContinuousGuardedApplication(
        repository,
        cast(
            "dict[tuple[str, str], GuardedFieldApplication]",
            {("primary", "alpha"): _Planner()},
        ),
        {("primary", "alpha"): _AllowedCapability()},
        _Authority(),
        (("primary", "alpha"),),
        lambda: 1,
        lambda *_: None,
    )

    assert service.schedule(100) == 0
    assert service.schedule(100) == 0
    assert service.schedule(100) == 0
    assert repository.afters[0] is None
    assert repository.afters[1] == repository.afters[2]
    assert repository.afters[1] is not None

    service.wake()
    assert service.schedule(100) == 0
    assert repository.afters[-1] is None


def test_scheduler_retries_a_candidate_after_a_transient_processing_failure() -> None:
    class ConsentedRepository(_PagedRepository):
        def current_field_effect_consent(self, *_context):
            return object()

    class FailingOnceAuthority(_Authority):
        def __init__(self) -> None:
            self.calls = 0

        def resolve(self, decision):
            self.calls += 1
            if self.calls == 1:
                raise FieldEffectError("persistence")
            return super().resolve(decision)

    repository = ConsentedRepository(_decisions(1))
    planner = _Planner()
    authority = FailingOnceAuthority()
    service = ContinuousGuardedApplication(
        repository,
        cast(
            "dict[tuple[str, str], GuardedFieldApplication]",
            {("primary", "alpha"): planner},
        ),
        {("primary", "alpha"): _AllowedCapability()},
        authority,
        (("primary", "alpha"),),
        lambda: 1,
        lambda *_: None,
    )

    with pytest.raises(FieldEffectError, match="persistence"):
        service.schedule(1)
    assert service.schedule(1) == 1
    assert authority.calls == 2
    assert planner.planned == [repository.decisions[0].decision_id]


def test_capability_recovery_requeues_a_candidate_behind_the_retained_cursor() -> None:
    class ConsentedRepository(_PagedRepository):
        def current_field_effect_consent(self, *_context):
            return object()

    class RecoveringCapability:
        category = "insufficient_permission"

        def observe(self, _probe):
            return SimpleNamespace(category=self.category, digest="a" * 64)

    repository = ConsentedRepository(_decisions(1))
    planner = _Planner()
    capability = RecoveringCapability()
    now = [1]
    service = ContinuousGuardedApplication(
        repository,
        cast(
            "dict[tuple[str, str], GuardedFieldApplication]",
            {("primary", "alpha"): planner},
        ),
        {("primary", "alpha"): capability},
        _Authority(),
        (("primary", "alpha"),),
        lambda: now[0],
        lambda *_: None,
    )

    assert service.schedule(1) == 0
    assert repository.suspensions
    capability.category = "allowed"
    now[0] = 2
    service._capability_readiness(("alpha",), force=True)

    assert repository.suspensions == {}
    assert service.schedule(1) == 1
    assert planner.planned == [repository.decisions[0].decision_id]


def test_background_cycle_bounds_permission_waiting_work_to_one_candidate() -> None:
    class ObservedRepository(_PagedRepository):
        def __init__(self) -> None:
            super().__init__(_decisions(100))
            self.limits: list[int] = []

        def pending_field_effect_decision_documents(self, limit, after=None):
            self.limits.append(limit)
            return super().pending_field_effect_decision_documents(limit, after)

        def next_field_effect_plan_id(self, _last_library):
            return None

    repository = ObservedRepository()
    service = ContinuousGuardedApplication(
        repository,
        cast(
            "dict[tuple[str, str], GuardedFieldApplication]",
            {("primary", "alpha"): _Planner()},
        ),
        {("primary", "alpha"): _AllowedCapability()},
        _Authority(),
        (("primary", "alpha"),),
        lambda: 1,
        lambda *_: None,
    )

    assert not service.run_cycle()
    assert repository.limits == [1]


def _decisions(count: int):
    def digest(value) -> str:
        document = json.dumps(
            value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        )
        return hashlib.sha256(document.encode()).hexdigest()

    def make_decision(index: int) -> HumanDecision:
        review_case_id = "review_case_" + f"{index:064x}"
        proposal_id = "review_proposal_" + f"{index:064x}"
        selected = {"kind": "text", "value": "After"}
        idempotency_digest = hashlib.sha256(f"key-{index}".encode()).hexdigest()
        request = {
            "actor": "owner",
            "destination": "title",
            "idempotency_digest": idempotency_digest,
            "outcome": "accept",
            "proposal_id": proposal_id,
            "review_case_id": review_case_id,
            "selected_value": selected,
            "target_fingerprint": "b" * 64,
        }
        request_digest = digest(request)
        decision_id = "human_decision_" + digest(
            {
                "occurred_at": index,
                "request_digest": request_digest,
            }
        )
        return HumanDecision(
            decision_id,
            review_case_id,
            proposal_id,
            "b" * 64,
            "title",
            "owner",
            "accept",
            selected,
            idempotency_digest,
            request_digest,
            index,
        )

    return [make_decision(index) for index in range(1, count + 1)]


def _recovery_service(repository, application) -> ContinuousGuardedApplication:
    return ContinuousGuardedApplication(
        repository,
        cast(
            "dict[tuple[str, str], GuardedFieldApplication]",
            {("primary", "alpha"): application},
        ),
        cast(
            "dict[tuple[str, str], CapabilityObserver]",
            {("primary", "alpha"): object()},
        ),
        object(),
        (("primary", "alpha"),),
        lambda: 1,
        lambda *_: None,
    )


def test_restart_recovery_drains_more_than_one_bounded_page() -> None:
    repository = _RecoveryRepository()
    application = _RecoveryApplication(repository)
    service = _recovery_service(repository, application)
    recovered, actions, cleanups = service.start()
    service.begin_drain()
    assert (recovered, actions, cleanups) == (205, 205, 205)
    assert (repository.plans, repository.actions, application.cleanups) == ([], [], 0)


def test_unrecoverable_plans_neither_stop_nor_falsely_stall_restart_recovery() -> None:
    repository = _StuckRecoveryRepository()
    application = _StuckRecoveryApplication(repository, {"plan-stuck", "plan-77"})
    service = _recovery_service(repository, application)

    with pytest.raises(ExceptionGroup) as raised:
        service.start()

    # Both failures reach the caller as themselves. Neither is the non-progress
    # guard, which a plan left pending by its own failure would otherwise trip
    # on the next page and turn back into a whole-batch abort.
    assert [getattr(error, "code", None) for error in raised.value.exceptions] == [
        "authority",
        "authority",
    ]
    # Every other plan was recovered, across more than one page, behind them.
    assert repository.plans == ["plan-stuck", "plan-77"]
    assert len(application.attempted) == 151
    assert application.attempted.count("plan-stuck") == 1
    assert service._thread is None


def test_restart_recovery_that_advances_no_plan_still_fails_the_guard() -> None:
    repository = _StuckRecoveryRepository()
    application = _StallingRecoveryApplication(repository)
    service = _recovery_service(repository, application)

    with pytest.raises(GuardedApplicationError) as raised:
        service.start()

    # Nothing failed and nothing advanced: the page repeats, and that is still
    # refused rather than looped on forever.
    assert raised.value.code == "recovery"
    assert len(repository.plans) == 151
    assert service._thread is None


def test_fair_selection_and_instance_wide_mutation_slot() -> None:
    repository = _Repository()
    shared = {
        "active": 0,
        "maximum": 0,
        "guard": threading.Lock(),
        "release": threading.Event(),
        "started": threading.Event(),
        "repository": repository,
        "drained": threading.Event(),
    }
    applications = {
        ("primary", "alpha"): _Application(shared),
        ("primary", "beta"): _Application(shared),
    }
    service = ContinuousGuardedApplication(
        repository,
        applications,
        cast(
            "dict[tuple[str, str], CapabilityObserver]",
            {key: object() for key in applications},
        ),
        object(),
        tuple(applications),
        lambda: 1,
        lambda *_: None,
    )
    shared["release"].set()
    assert service.run_cycle()
    assert service.run_cycle()
    assert applications[("primary", "alpha")].applied == ["plan-a1"]
    assert applications[("primary", "beta")].applied == ["plan-b1"]

    shared["release"].clear()
    first = threading.Thread(target=service.apply, args=("plan-a2",))
    second = threading.Thread(target=service.apply, args=("plan-b1",))
    first.start()
    second.start()
    assert shared["started"].wait(1)
    assert shared["maximum"] == 1
    shared["release"].set()
    first.join()
    second.join()
    assert shared["maximum"] == 1


def test_shutdown_waits_for_a_claimed_operation_within_configured_grace() -> None:
    repository = _Repository()
    shared = {
        "active": 0,
        "maximum": 0,
        "guard": threading.Lock(),
        "release": threading.Event(),
        "started": threading.Event(),
        "repository": repository,
        "drained": threading.Event(),
    }
    application = _Application(shared)
    service = ContinuousGuardedApplication(
        repository,
        cast(
            "dict[tuple[str, str], GuardedFieldApplication]",
            {("primary", "alpha"): application},
        ),
        {("primary", "alpha"): _AllowedCapability()},
        _Authority(),
        (("primary", "alpha"),),
        lambda: 1,
        lambda *_: None,
        graceful_shutdown_seconds=1,
    )
    service.start()
    assert shared["started"].wait(1)
    shared["release"].set()
    service.begin_drain()
    assert shared["drained"].is_set()
    assert service._thread is None


def test_shutdown_timeout_retains_live_claimed_worker() -> None:
    repository = _Repository()
    shared = {
        "active": 0,
        "maximum": 0,
        "guard": threading.Lock(),
        "release": threading.Event(),
        "started": threading.Event(),
        "repository": repository,
        "drained": threading.Event(),
        "wait": 5,
    }
    application = _Application(shared)
    service = ContinuousGuardedApplication(
        repository,
        cast(
            "dict[tuple[str, str], GuardedFieldApplication]",
            {("primary", "alpha"): application},
        ),
        {("primary", "alpha"): _AllowedCapability()},
        _Authority(),
        (("primary", "alpha"),),
        lambda: 1,
        lambda *_: None,
        graceful_shutdown_seconds=1,
    )
    service.start()
    assert shared["started"].wait(1)
    with pytest.raises(GuardedApplicationError, match="shutdown_timeout"):
        service.begin_drain()
    assert shared["drained"].is_set()
    assert service._thread is not None
    assert service._thread is not None
    assert service._thread.is_alive()
    shared["release"].set()
    service.begin_drain()


class _ConsentRepository(_Repository):
    def __init__(self, consented: tuple[str, ...], fault: str | None = None) -> None:
        super().__init__()
        self.consented = consented
        self.fault = fault
        self.asked: list[str] = []

    def current_field_effect_consent(self, connection, library, destination):
        del connection, library
        self.asked.append(destination)
        if self.fault is not None:
            raise FieldEffectError(self.fault)
        if destination not in self.consented:
            raise FieldEffectError("consent")
        return {"consent_id": f"consent-{destination}"}


def _consent_service(repository: _ConsentRepository) -> ContinuousGuardedApplication:
    return ContinuousGuardedApplication(
        repository,
        cast(
            "dict[tuple[str, str], GuardedFieldApplication]",
            {("primary", "alpha"): _Planner()},
        ),
        {("primary", "alpha"): _AllowedCapability()},
        _Authority(),
        (("primary", "alpha"),),
        lambda: 1,
        lambda *_: SimpleNamespace(
            scan_id="review_scan",
            state="completed",
            rescan_requested=False,
            updated_at=10,
        ),
    )


@pytest.mark.parametrize(
    ("consented", "fault", "answer", "asked"),
    (
        ((), None, False, ["title", "series", "cover"]),
        (("series",), None, True, ["title", "series"]),
        (("title",), None, True, ["title"]),
        ((), "persistence", False, ["title"]),
    ),
    ids=("none", "later", "first", "fault"),
)
def test_destination_consent_is_reported_without_granting_anything(
    consented: tuple[str, ...], fault: str | None, answer: bool, asked: list[str]
) -> None:
    """A review surface may read this consent; it may not acquire it.

    The reassurance copy after a decision differs by whether applying has
    already begun, so the surface has to ask. Asking must stop at the first
    consented destination, must fail closed on anything that is not a plain
    refusal, and must leave no consent behind.
    """
    repository = _ConsentRepository(consented, fault)
    service = _consent_service(repository)
    assert (
        service.destinations_consented("primary", "alpha", ("title", "series", "cover"))
        is answer
    )
    assert repository.asked == asked
    # An unconfigured library is answered without asking the store at all.
    repository.asked.clear()
    assert service.destinations_consented("primary", "gamma", ("title",)) is False
    assert repository.asked == []
