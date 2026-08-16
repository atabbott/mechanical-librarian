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
from mechanical_librarian.application.field_effects import GuardedApplicationError
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
                plan=SimpleNamespace(connection="primary", library=library)
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

    def apply(self, plan_id):
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
                plan=SimpleNamespace(connection="primary", library="alpha")
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

    def apply(self, plan_id):
        self.repository.plans.remove(plan_id)
        return self.repository.operations[plan_id]

    def reconcile(self, plan_id):
        self.repository.actions.remove(plan_id)
        return self.repository.operations[plan_id]

    def run_pending_cleanups(self, limit):
        completed = min(limit, self.cleanups)
        self.cleanups -= completed
        return completed


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

    def pending_field_effect_decision_counts(self):
        counts: dict[str, int] = {}
        for value in self.decisions:
            library = "beta" if value.occurred_at == 101 else "alpha"
            counts[library] = counts.get(library, 0) + 1
        return tuple(
            {
                "connection": "primary",
                "library": library,
                "origin": "human",
                "count": count,
            }
            for library, count in sorted(counts.items())
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
            return
        self.suspensions[key] = (reason, changed_at)

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
    repository = _HoldRepository()
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
            {("primary", "alpha"): _Planner()},
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
    progress.state = "completed"
    progress.updated_at = 11
    service.schedule()
    assert repository.hold is None


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


def test_restart_recovery_drains_more_than_one_bounded_page() -> None:
    repository = _RecoveryRepository()
    application = _RecoveryApplication(repository)
    service = ContinuousGuardedApplication(
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
    recovered, actions, cleanups = service.start()
    service.begin_drain()
    assert (recovered, actions, cleanups) == (205, 205, 205)
    assert (repository.plans, repository.actions, application.cleanups) == ([], [], 0)


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
