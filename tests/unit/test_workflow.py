from __future__ import annotations

import unittest

from mechanical_librarian.domain.identifiers import DomainValueError
from mechanical_librarian.domain.lifecycle import JobState as LifecycleJobState
from mechanical_librarian.domain.workflow import (
    Dependency,
    JobState,
    RunState,
    StageDefinition,
    StageId,
    StageVersion,
    StageState,
    WorkflowDefinition,
    WorkflowVersion,
    synthetic_heartbeat_definition,
    validate_run_transition,
    validate_stage_transition,
)


class WorkflowDefinitionTests(unittest.TestCase):
    def test_job_state_reuses_the_authoritative_lifecycle_vocabulary(self) -> None:
        self.assertIs(JobState, LifecycleJobState)

    def test_digest_is_deterministic_across_declaration_order(self) -> None:
        original = synthetic_heartbeat_definition()
        reordered = WorkflowDefinition(
            original.version,
            tuple(reversed(original.stages)),
            tuple(reversed(original.dependencies)),
        )
        self.assertEqual(original.digest, reordered.digest)
        self.assertEqual(
            tuple(stage.value for stage in original.topological_order()),
            ("root", "left", "right", "join"),
        )

    def test_incompatible_definition_changes_version_and_digest(self) -> None:
        original = synthetic_heartbeat_definition()
        changed = synthetic_heartbeat_definition(incompatible=True)
        self.assertNotEqual(original.version, changed.version)
        self.assertNotEqual(original.digest, changed.digest)

    def test_transitive_invalidation_preserves_unaffected_branch(self) -> None:
        changed = synthetic_heartbeat_definition(incompatible=True)
        affected = changed.descendants(frozenset({StageId("left")}))
        self.assertEqual(
            {stage.value for stage in affected},
            {"left", "join"},
        )

    def test_cycle_missing_endpoint_and_duplicate_stage_are_rejected(self) -> None:
        root = StageDefinition(StageId("root"), "root", StageVersion("v1"))
        left = StageDefinition(StageId("left"), "branch", StageVersion("v1"))
        with self.assertRaises(DomainValueError):
            WorkflowDefinition(
                WorkflowVersion("v1"),
                (root, left),
                (
                    Dependency(StageId("root"), StageId("left")),
                    Dependency(StageId("left"), StageId("root")),
                ),
            )
        with self.assertRaises(DomainValueError):
            WorkflowDefinition(
                WorkflowVersion("v1"),
                (root,),
                (Dependency(StageId("root"), StageId("missing")),),
            )
        with self.assertRaises(DomainValueError):
            WorkflowDefinition(WorkflowVersion("v1"), (root, root), ())

    def test_unknown_changed_stage_is_rejected(self) -> None:
        with self.assertRaises(DomainValueError):
            synthetic_heartbeat_definition().descendants(
                frozenset({StageId("unknown")})
            )

    def test_run_and_stage_transition_edges_are_explicit(self) -> None:
        validate_run_transition(RunState.WAITING, RunState.RUNNING)
        validate_run_transition(RunState.SUCCEEDED, RunState.SUPERSEDED)
        validate_stage_transition(StageState.WAITING, StageState.READY)
        validate_stage_transition(StageState.RUNNING, StageState.SUCCEEDED)
        with self.assertRaises(DomainValueError):
            validate_run_transition(RunState.SUCCEEDED, RunState.RUNNING)
        with self.assertRaises(DomainValueError):
            validate_stage_transition(StageState.SUCCEEDED, StageState.READY)


if __name__ == "__main__":
    unittest.main()
