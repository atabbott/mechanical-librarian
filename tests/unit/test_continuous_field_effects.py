from __future__ import annotations

import hashlib
import json
import threading
import unittest
from types import SimpleNamespace

from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.application.field_effects import GuardedApplicationError
from mechanical_librarian.domain.field_effect import FieldEffectError
from mechanical_librarian.domain.human_decision import HumanDecision


class _Repository:
    def __init__(self) -> None:
        self.ready = [("plan-a1", "alpha"), ("plan-a2", "alpha"), ("plan-b1", "beta")]
        self.operations = {
            plan_id: SimpleNamespace(plan=SimpleNamespace(connection="primary", library=library))
            for plan_id, library in self.ready
        }

    def pending_field_effect_decision_documents(self, limit, after=None):
        return ()

    def next_field_effect_plan_id(self, last_library):
        if not self.ready:
            return None
        index = next((i for i, (_, library) in enumerate(self.ready)
                      if library != last_library), 0)
        return self.ready.pop(index)

    def get_field_effect(self, plan_id):
        return self.operations[plan_id]


class _Application:
    def __init__(self, shared) -> None:
        self.shared = shared
        self.applied = []

    def apply(self, plan_id):
        with self.shared["guard"]:
            self.shared["active"] += 1
            self.shared["maximum"] = max(self.shared["maximum"], self.shared["active"])
            self.shared["started"].set()
        self.shared["release"].wait(1)
        with self.shared["guard"]:
            self.shared["active"] -= 1
        self.applied.append(plan_id)
        return self.shared["repository"].operations[plan_id]


class _RecoveryRepository(_Repository):
    def __init__(self) -> None:
        self.plans = [f"plan-{index}" for index in range(205)]
        self.actions = [f"action-{index}" for index in range(205)]
        self.operations = {
            plan_id: SimpleNamespace(plan=SimpleNamespace(connection="primary", library="alpha"))
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

    def pending_field_effect_decision_documents(self, limit, after=None):
        values = self.decisions
        if after is not None:
            values = [value for value in values
                      if (value.occurred_at, value.decision_id) > after]
        return tuple(value.to_json() for value in values[:limit])

    def pending_field_effect_decision_counts(self):
        counts = {}
        for value in self.decisions:
            library = "beta" if value.occurred_at == 101 else "alpha"
            counts[library] = counts.get(library, 0) + 1
        return tuple({
            "connection": "primary", "library": library,
            "origin": "human", "count": count,
        } for library, count in sorted(counts.items()))

    def current_field_effect_consent(self, connection, library, destination):
        if library == "alpha":
            raise FieldEffectError("consent")
        return object()

    def field_effect_backup_ready(self):
        return True

    def grant_field_effect_consents(self, *arguments):
        raise AssertionError("consent must not be granted before capability proof")


class _HoldRepository(_Repository):
    def __init__(self) -> None:
        super().__init__()
        self.hold = ("primary", "alpha")

    def field_effect_manual_hold_context(self, review_case_id, destination):
        return self.hold

    def clear_field_effect_manual_hold(self, review_case_id, destination, changed_at):
        context = self.hold
        self.hold = None
        return context


class _Authority:
    def resolve(self, decision):
        library = "beta" if decision.occurred_at == 101 else "alpha"
        return SimpleNamespace(
            connection="primary", library=library, work_id="work",
            destination="title", pre_value="Before",
            decision_value={"kind": "text", "value": "After"},
            target_fingerprint=decision.target_fingerprint,
            target_version="v1", dependency_digests=(decision.target_fingerprint,),
            review_case_id=decision.review_case_id, source="persisted_review_case",
        )


class _Planner:
    def __init__(self) -> None:
        self.planned = []

    def plan(self, decision, **values):
        self.planned.append(decision.decision_id)
        return object()


class _AllowedCapability:
    def observe(self, probe):
        return SimpleNamespace(category="allowed", digest="a" * 64)


class _DeniedCapability:
    def observe(self, probe):
        return SimpleNamespace(category="insufficient_permission", digest="b" * 64)


class ContinuousGuardedApplicationTests(unittest.TestCase):
    def test_return_to_automatic_requests_scan_before_clearing_hold(self) -> None:
        repository = _HoldRepository()
        scans = []
        service = ContinuousGuardedApplication(
            repository, {("primary", "alpha"): _Planner()},
            {("primary", "alpha"): _AllowedCapability()}, _Authority(),
            (("primary", "alpha"),), lambda: 1,
            lambda *context: scans.append(context),
        )
        service.return_to_automatic("review_case", "title")
        self.assertIsNone(repository.hold)
        self.assertEqual(scans, [("primary", "alpha")])

    def test_enable_refuses_read_only_capability_before_granting_consent(self) -> None:
        decision = self._decisions(1)[0]
        repository = _PagedRepository([decision])
        service = ContinuousGuardedApplication(
            repository, {("primary", "alpha"): _Planner()},
            {("primary", "alpha"): _DeniedCapability()}, _Authority(),
            (("primary", "alpha"),), lambda: 1, lambda *_: None,
        )
        with self.assertRaisesRegex(GuardedApplicationError, "capability"):
            service.enable(("alpha",))

    def test_blocked_oldest_page_does_not_starve_later_eligible_work(self) -> None:
        decisions = self._decisions(101)

        repository = _PagedRepository(decisions)
        planners = {("primary", "alpha"): _Planner(),
                    ("primary", "beta"): _Planner()}
        capabilities = {key: _AllowedCapability() for key in planners}
        service = ContinuousGuardedApplication(
            repository, planners, capabilities, _Authority(), tuple(planners),
            lambda: 1, lambda *_: None,
        )
        self.assertEqual(service.schedule(100), 0)
        self.assertEqual(service.schedule(100), 1)
        self.assertEqual(len(planners[("primary", "beta")].planned), 1)

    @staticmethod
    def _decisions(count: int):
        def digest(value) -> str:
            document = json.dumps(value, ensure_ascii=True, separators=(",", ":"),
                                  sort_keys=True)
            return hashlib.sha256(document.encode()).hexdigest()

        def make_decision(index: int) -> HumanDecision:
            review_case_id = "review_case_" + f"{index:064x}"
            proposal_id = "review_proposal_" + f"{index:064x}"
            selected = {"kind": "text", "value": "After"}
            idempotency_digest = hashlib.sha256(f"key-{index}".encode()).hexdigest()
            request = {
                "actor": "owner", "destination": "title",
                "idempotency_digest": idempotency_digest, "outcome": "accept",
                "proposal_id": proposal_id, "review_case_id": review_case_id,
                "selected_value": selected, "target_fingerprint": "b" * 64,
            }
            request_digest = digest(request)
            decision_id = "human_decision_" + digest({
                "occurred_at": index, "request_digest": request_digest,
            })
            return HumanDecision(
                decision_id, review_case_id, proposal_id, "b" * 64, "title",
                "owner", "accept", selected, idempotency_digest,
                request_digest, index,
            )

        return [make_decision(index) for index in range(1, count + 1)]

    def test_restart_recovery_drains_more_than_one_bounded_page(self) -> None:
        repository = _RecoveryRepository()
        application = _RecoveryApplication(repository)
        service = ContinuousGuardedApplication(
            repository, {("primary", "alpha"): application},
            {("primary", "alpha"): object()}, object(),
            (("primary", "alpha"),), lambda: 1, lambda *_: None,
        )
        recovered, actions, cleanups = service.start()
        service.begin_drain()
        self.assertEqual((recovered, actions, cleanups), (205, 205, 205))
        self.assertEqual((repository.plans, repository.actions, application.cleanups),
                         ([], [], 0))

    def test_fair_selection_and_instance_wide_mutation_slot(self) -> None:
        repository = _Repository()
        shared = {
            "active": 0, "maximum": 0, "guard": threading.Lock(),
            "release": threading.Event(), "started": threading.Event(),
            "repository": repository,
        }
        applications = {
            ("primary", "alpha"): _Application(shared),
            ("primary", "beta"): _Application(shared),
        }
        service = ContinuousGuardedApplication(
            repository, applications, {key: object() for key in applications},
            object(), tuple(applications), lambda: 1, lambda *_: None,
        )
        shared["release"].set()
        self.assertTrue(service.run_cycle())
        self.assertTrue(service.run_cycle())
        self.assertEqual(applications[("primary", "alpha")].applied, ["plan-a1"])
        self.assertEqual(applications[("primary", "beta")].applied, ["plan-b1"])

        shared["release"].clear()
        first = threading.Thread(target=service.apply, args=("plan-a2",))
        second = threading.Thread(target=service.apply, args=("plan-b1",))
        first.start(); second.start()
        self.assertTrue(shared["started"].wait(1))
        self.assertEqual(shared["maximum"], 1)
        shared["release"].set()
        first.join(); second.join()
        self.assertEqual(shared["maximum"], 1)


if __name__ == "__main__":
    unittest.main()
