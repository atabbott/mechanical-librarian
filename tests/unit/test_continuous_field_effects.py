from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace

from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)


class _Repository:
    def __init__(self) -> None:
        self.ready = [("plan-a1", "alpha"), ("plan-a2", "alpha"), ("plan-b1", "beta")]
        self.operations = {
            plan_id: SimpleNamespace(plan=SimpleNamespace(connection="primary", library=library))
            for plan_id, library in self.ready
        }

    def pending_field_effect_decision_documents(self, limit):
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


class ContinuousGuardedApplicationTests(unittest.TestCase):
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
