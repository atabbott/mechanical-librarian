from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from mechanical_librarian.adapters.persistence.metadata_reports import (
    MetadataReportError,
    open_repository,
)
from mechanical_librarian.domain.metadata_policy import (
    DEFAULT_OPTIONS,
    MetadataPolicyProfile,
    OPTION_CATALOGUE,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import MetadataReport, TargetDestination
from mechanical_librarian.domain.proposal_set import ProposalOutcome, evaluate_report
from tests.unit.test_review_pipeline import _PRODUCER, _entry, _report


def _draft() -> MetadataPolicyProfile:
    options = dict(DEFAULT_OPTIONS)
    options["date_precision"] = "full_date"
    return MetadataPolicyProfile.create(
        "Reviewed policy",
        options,
        {
            field: "ignore" if field == "tags" else (
                "unsupported" if field == "explicit" else "ask"
            )
            for field in OPTION_CATALOGUE
        },
    )


class MetadataPolicyActivationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "state.sqlite"

    def tearDown(self) -> None:
        self.directory.cleanup()

    def _current_case_with_completed_work(self):
        repository = open_repository(self.path)
        report = _report()
        repository.publish(report, lambda: True)
        old = evaluate_report(report)
        repository.publish_proposal(old)
        admitted = json.loads(repository.admit_review_case(old))
        repository.request_review_scan(
            report.target.connection, report.target.library, "lib-1", _PRODUCER, 10
        )
        discovery = repository.claim_review_pipeline_action(11)
        assert discovery is not None
        repository.complete_review_discovery(
            discovery,
            1,
            "a" * 64,
            (replace(_entry(report.target.item, report.file.identity), library=report.target.library),),
            12,
        )
        action = repository.claim_review_pipeline_action(13)
        assert action is not None
        repository.advance_review_pipeline_action(action, report.report_id, 14)
        action = repository.claim_review_pipeline_action(15)
        assert action is not None
        repository.advance_review_pipeline_action(action, None, 16)
        action = repository.claim_review_pipeline_action(17)
        assert action is not None
        repository.advance_review_pipeline_action(action, old.proposal_set_id, 18)
        action = repository.claim_review_pipeline_action(19)
        assert action is not None
        repository.advance_review_pipeline_action(action, admitted["review_case_id"], 20)
        action = repository.claim_review_pipeline_action(21)
        assert action is not None
        repository.advance_review_pipeline_action(action, None, 22)
        repository.claim_review_pipeline_action(23)
        return repository, report, old, admitted["review_case_id"]

    def test_atomic_cutover_rewinds_existing_evidence_and_completes_after_restart(self) -> None:
        repository, report, old, old_case_id = self._current_case_with_completed_work()
        draft = _draft()
        repository.create_metadata_policy_draft(draft.canonical_json)
        builtin = MetadataPolicyProfile.builtin()
        resolved = resolve_policy(draft, report.target.library)

        transition = repository.activate_metadata_policy(
            draft.policy_id,
            builtin.policy_id,
            ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
            30,
        )
        assert transition is not None
        self.assertEqual((transition.state, transition.completed, transition.total, transition.failed), ("processing", 0, 1, 0))
        self.assertEqual(repository.current_unapplied_count(report.target.library), 0)
        self.assertEqual(
            repository._connection.execute(
                "SELECT review_case_id,outcome FROM current_review_case_lineages"
            ).fetchone(),
            (None, "retired"),
        )
        self.assertEqual(
            repository._connection.execute(
                "SELECT actor,kind,reason FROM review_case_transitions ORDER BY checkpoint DESC LIMIT 1"
            ).fetchone(),
            ("policy-activation-v1", "retired", "policy_changed"),
        )
        self.assertFalse(json.loads(repository.get_review_case(old_case_id))["current"])
        with self.assertRaises(MetadataReportError):
            repository.publish_proposal(old)

        new_report = MetadataReport.create(
            target=replace(report.target, item="item-new"),
            file=replace(
                report.file, identity="d" * 64, content_digest="e" * 64
            ),
            local_claims=report.local_claims,
            local_reasons=report.local_reasons,
            providers=report.providers,
            cover_sources=report.cover_sources,
        )
        repository.publish(new_report, lambda: True)
        active_resolution = repository.current_resolved_metadata_policy(
            new_report.target.library
        )
        assert active_resolution is not None
        new_proposal = evaluate_report(
            new_report,
            active_resolution.resolved_id,
            dict(active_resolution.destinations),
        )
        repository.publish_proposal(new_proposal)
        repository.admit_review_case(new_proposal)
        unchanged = repository.metadata_policy_transition()
        assert unchanged is not None
        self.assertEqual((unchanged.completed, unchanged.total), (0, 1))
        repository.close()

        reopened = open_repository(self.path, create=False)
        action = reopened.claim_review_pipeline_action(31)
        assert action is not None
        self.assertEqual((action.stage, action.report_id), ("proposal", report.report_id))
        current_policy = reopened.current_resolved_metadata_policy(report.target.library)
        assert current_policy is not None
        successor = evaluate_report(
            report, current_policy.resolved_id, dict(current_policy.destinations)
        )
        tags = next(
            result
            for result in successor.results
            if result.destination is TargetDestination.TAGS
        )
        self.assertEqual((tags.outcome, tags.reasons), (ProposalOutcome.INELIGIBLE, ("policy_ignored",)))
        reopened.publish_proposal(successor)
        reopened.advance_review_pipeline_action(action, successor.proposal_set_id, 32)
        action = reopened.claim_review_pipeline_action(33)
        assert action is not None
        admitted = json.loads(reopened.admit_review_case(successor))
        reopened.advance_review_pipeline_action(action, admitted.get("review_case_id"), 34)
        action = reopened.claim_review_pipeline_action(35)
        assert action is not None
        reopened.advance_review_pipeline_action(action, None, 36)
        completed = reopened.metadata_policy_transition()
        assert completed is not None
        self.assertEqual((completed.state, completed.completed, completed.total, completed.failed), ("completed", 1, 1, 0))
        self.assertNotEqual(admitted.get("review_case_id"), old_case_id)
        acknowledged = reopened.acknowledge_metadata_policy_transition(completed.transition_id, 37)
        self.assertTrue(acknowledged.acknowledged)
        self.assertIsNone(
            reopened.activate_metadata_policy(
                draft.policy_id,
                draft.policy_id,
                ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
                38,
            )
        )
        reopened.close()

    def test_missing_pipeline_work_is_typed_durable_and_retryable(self) -> None:
        repository = open_repository(self.path)
        report = _report()
        repository.publish(report, lambda: True)
        old = evaluate_report(report)
        repository.publish_proposal(old)
        repository.admit_review_case(old)
        draft = _draft()
        repository.create_metadata_policy_draft(draft.canonical_json)
        resolved = resolve_policy(draft, report.target.library)
        transition = repository.activate_metadata_policy(
            draft.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
            40,
        )
        assert transition is not None
        self.assertEqual((transition.state, transition.failed, transition.total), ("completed_with_failures", 1, 1))
        self.assertEqual(
            repository._connection.execute(
                "SELECT state,failure_code FROM metadata_policy_transition_items"
            ).fetchone(),
            ("failed", "pipeline_unavailable"),
        )
        repository.request_review_scan(
            report.target.connection, report.target.library, "lib-1", _PRODUCER, 41
        )
        discovery = repository.claim_review_pipeline_action(42)
        assert discovery is not None
        repository.complete_review_discovery(
            discovery,
            1,
            "b" * 64,
            (replace(_entry(report.target.item, report.file.identity), library=report.target.library),),
            43,
        )
        extraction = repository.claim_review_pipeline_action(44)
        assert extraction is not None
        repository.advance_review_pipeline_action(extraction, report.report_id, 45)
        retried = repository.retry_metadata_policy_transition(transition.transition_id, 46)
        self.assertEqual((retried.state, retried.failed, retried.total), ("processing", 0, 1))
        provider_action = repository.claim_review_pipeline_action(47)
        assert provider_action is not None
        self.assertEqual(provider_action.stage, "provider")
        repository.advance_review_pipeline_action(provider_action, None, 48)
        proposal_action = repository.claim_review_pipeline_action(49)
        assert proposal_action is not None
        self.assertEqual(proposal_action.stage, "proposal")
        current_policy = repository.current_resolved_metadata_policy(report.target.library)
        assert current_policy is not None
        successor = evaluate_report(
            report, current_policy.resolved_id, dict(current_policy.destinations)
        )
        repository.publish_proposal(successor)
        repository.advance_review_pipeline_action(proposal_action, successor.proposal_set_id, 50)
        admission = repository.claim_review_pipeline_action(51)
        assert admission is not None
        admitted = json.loads(repository.admit_review_case(successor))
        repository.advance_review_pipeline_action(admission, admitted.get("review_case_id"), 52)
        projection = repository.claim_review_pipeline_action(53)
        assert projection is not None
        repository.advance_review_pipeline_action(projection, None, 54)
        completed = repository.metadata_policy_transition()
        assert completed is not None
        self.assertEqual((completed.state, completed.completed, completed.total), ("completed", 1, 1))
        with self.assertRaises(MetadataReportError):
            repository.activate_metadata_policy(
                MetadataPolicyProfile.builtin().policy_id,
                MetadataPolicyProfile.builtin().policy_id,
                ((report.target.library, "resolved_policy_" + "0" * 64, resolved.canonical_json),),
                41,
            )
        repository.close()

    def test_changed_inflight_extraction_is_new_work_not_old_cohort_completion(self) -> None:
        repository, report, _, _ = self._current_case_with_completed_work()
        repository.request_review_scan(
            report.target.connection, report.target.library, "lib-2", _PRODUCER, 24
        )
        discovery = repository.claim_review_pipeline_action(25)
        assert discovery is not None
        changed = _report("Changed title", "c", "d")
        repository.complete_review_discovery(
            discovery,
            2,
            "c" * 64,
            (
                replace(
                    _entry(changed.target.item, changed.file.identity),
                    library=changed.target.library,
                    library_id="lib-2",
                    updated_at=2,
                ),
            ),
            26,
        )
        draft = _draft()
        repository.create_metadata_policy_draft(draft.canonical_json)
        resolved = resolve_policy(draft, report.target.library)
        transition = repository.activate_metadata_policy(
            draft.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
            30,
        )
        assert transition is not None
        extraction = repository.claim_review_pipeline_action(31)
        assert extraction is not None
        self.assertEqual("extraction", extraction.stage)
        repository.publish(changed, lambda: True)
        repository.advance_review_pipeline_action(extraction, changed.report_id, 32)
        stale = repository.metadata_policy_transition()
        assert stale is not None
        self.assertEqual(
            (stale.state, stale.completed, stale.total, stale.failed),
            ("completed_with_failures", 0, 1, 1),
        )
        self.assertEqual(
            repository._connection.execute(
                "SELECT report_id,state,failure_code FROM metadata_policy_transition_items"
            ).fetchone(),
            (report.report_id, "failed", "stale"),
        )
        next_action = repository.claim_review_pipeline_action(33)
        assert next_action is not None
        self.assertEqual("provider", next_action.stage)
        repository.close()
