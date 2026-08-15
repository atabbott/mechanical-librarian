from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from mechanical_librarian.adapters.persistence.metadata_reports import (
    MetadataReportError,
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.application.metadata_policy import (
    MetadataPolicyApplicationError,
    PolicyNotebook,
)
from mechanical_librarian.bootstrap import composition
from mechanical_librarian.domain.field_effect import FieldEffectError
from mechanical_librarian.domain.human_decision import HumanDecision, HumanDecisionError
from mechanical_librarian.domain.metadata_policy import (
    DEFAULT_OPTIONS,
    OPTION_CATALOGUE,
    QUALIFIED_AUTOMATIC,
    MetadataPolicyProfile,
    ResolvedMetadataPolicy,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    TargetDestination,
)
from mechanical_librarian.domain.proposal_set import ProposalOutcome, evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
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

    def test_qualified_automatic_intent_is_persistable_and_unqualified_intent_is_rejected(self) -> None:
        repository = open_repository(self.path)
        destinations = {
            category: "unsupported" if category == "explicit" else "ask"
            for category in OPTION_CATALOGUE
        }
        destinations["title_layout"] = "automatic"
        profile = MetadataPolicyProfile.create(
            "Preview automatic intent", dict(DEFAULT_OPTIONS), destinations
        )
        self.assertEqual(profile.policy_id, repository.create_metadata_policy_draft(profile.canonical_json))
        unqualified_options = dict(DEFAULT_OPTIONS)
        unqualified_options["publisher_preference"] = "imprint"
        unqualified = MetadataPolicyProfile.create(
            "Unqualified automatic intent", unqualified_options,
            {**destinations, "publisher_preference": "automatic"},
        )
        with self.assertRaisesRegex(MetadataReportError, "automatic intent is unqualified"):
            repository.create_metadata_policy_draft(unqualified.canonical_json)

        rich_automatic = MetadataPolicyProfile.create(
            "Rich automatic intent", dict(DEFAULT_OPTIONS),
            {**destinations, "genres": "automatic"},
        )
        notebook = PolicyNotebook(repository, (), {})
        self.assertEqual(rich_automatic, notebook.validate(rich_automatic))
        repository.enter_shadow_preview(1)
        repository.create_metadata_policy_draft(profile.canonical_json)
        resolved = resolve_policy(profile, "ebooks")
        transition = repository.activate_metadata_policy(
            profile.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            (("ebooks", resolved.resolved_id, resolved.canonical_json),),
            2,
        )
        assert transition is not None
        self.assertEqual("completed", transition.state)
        self.assertEqual("preview", repository.shadow_posture())
        self.assertEqual(
            "automatic",
            dict(cast(ResolvedMetadataPolicy, repository.current_resolved_metadata_policy("ebooks")).destinations)[
                "title_layout"
            ],
        )
        self.assertIn("title_layout", QUALIFIED_AUTOMATIC)
        repository.close()

    def test_automatic_posture_accepts_only_exact_qualified_resolutions(self) -> None:
        repository = open_repository(self.path)
        destinations = {
            category: "unsupported" if category == "explicit" else "ask"
            for category in OPTION_CATALOGUE
        }
        destinations["title_layout"] = "automatic"
        first = MetadataPolicyProfile.create(
            "First qualified automatic policy", dict(DEFAULT_OPTIONS), destinations
        )
        repository.enter_shadow_preview(1)
        repository.create_metadata_policy_draft(first.canonical_json)
        first_resolved = resolve_policy(first, "ebooks")
        first_transition = repository.activate_metadata_policy(
            first.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            (("ebooks", first_resolved.resolved_id, first_resolved.canonical_json),),
            2,
        )
        assert first_transition is not None
        self.assertEqual("automatic", repository.activate_qualified_automatic(3))

        second = MetadataPolicyProfile.create(
            "Second qualified automatic policy",
            {**dict(DEFAULT_OPTIONS), "date_precision": "full_date"},
            {**destinations, "date_precision": "automatic"},
        )
        repository.create_metadata_policy_draft(second.canonical_json)
        second_resolved = resolve_policy(second, "ebooks")
        transition = repository.activate_metadata_policy(
            second.policy_id,
            first.policy_id,
            (("ebooks", second_resolved.resolved_id, second_resolved.canonical_json),),
            4,
        )
        self.assertIsNotNone(transition)
        self.assertEqual(
            transition,
            repository.activate_metadata_policy(
                second.policy_id,
                first.policy_id,
                (("ebooks", second_resolved.resolved_id, second_resolved.canonical_json),),
                4,
            ),
        )
        with self.assertRaisesRegex(MetadataReportError, "stale"):
            repository.activate_metadata_policy(
                first.policy_id,
                first.policy_id,
                (("ebooks", first_resolved.resolved_id, first_resolved.canonical_json),),
                5,
            )

        unqualified = resolve_policy(
            second,
            "ebooks",
            {
                "options": {"publisher_preference": "imprint"},
                "destinations": {"publisher_preference": "automatic"},
            },
        )
        with self.assertRaisesRegex(MetadataReportError, "resolution is invalid"):
            repository.activate_metadata_policy(
                second.policy_id,
                second.policy_id,
                (("ebooks", unqualified.resolved_id, unqualified.canonical_json),),
                6,
                reconcile_current=True,
            )
        repository.close()

    def test_library_override_cannot_turn_an_unqualified_option_automatic(self) -> None:
        repository = open_repository(self.path)
        destinations = {
            category: "unsupported" if category == "explicit" else "ask"
            for category in OPTION_CATALOGUE
        }
        destinations["publisher_preference"] = "automatic"
        profile = MetadataPolicyProfile.create(
            "Qualified base option", dict(DEFAULT_OPTIONS), destinations
        )
        repository.create_metadata_policy_draft(profile.canonical_json)
        notebook = PolicyNotebook(
            repository,
            ("ebooks",),
            {"ebooks": {"options": {"publisher_preference": "imprint"}, "destinations": {}}},
        )
        with self.assertRaisesRegex(MetadataPolicyApplicationError, "automatic unqualified"):
            notebook.activate(profile.policy_id, MetadataPolicyProfile.builtin().policy_id, 2)
        repository.close()

    def test_initial_resolution_binding_rejects_an_unqualified_automatic_override(self) -> None:
        repository = open_repository(self.path)
        resolved = resolve_policy(
            MetadataPolicyProfile.builtin(), "ebooks",
            {"options": {"publisher_preference": "imprint"}, "destinations": {}},
        )
        with self.assertRaisesRegex(MetadataReportError, "unqualified"):
            repository.bind_initial_metadata_policy_resolution(resolved.canonical_json)
        self.assertEqual((0,), repository._connection.execute(
            "SELECT COUNT(*) FROM metadata_policy_resolutions"
        ).fetchone())
        repository.close()

    def test_initial_resolution_binding_joins_an_existing_transaction(self) -> None:
        repository = open_repository(self.path)
        resolved = resolve_policy(MetadataPolicyProfile.builtin(), "ebooks")
        repository._connection.execute("BEGIN IMMEDIATE")

        self.assertEqual(
            resolved.resolved_id,
            repository.bind_initial_metadata_policy_resolution(
                resolved.canonical_json
            ),
        )
        self.assertTrue(repository._connection.in_transaction)
        self.assertEqual(
            (resolved.resolved_id,),
            repository._connection.execute(
                "SELECT resolved_policy_id FROM metadata_policy_resolutions "
                "WHERE generation=1 AND library_name='ebooks'"
            ).fetchone(),
        )

        repository._connection.rollback()
        self.assertEqual(
            (0,),
            repository._connection.execute(
                "SELECT COUNT(*) FROM metadata_policy_resolutions "
                "WHERE generation=1 AND library_name='ebooks'"
            ).fetchone(),
        )
        repository.close()

    def _current_case_with_completed_work(self, path: Path | None = None):
        repository = open_repository(self.path if path is None else path)
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

    def _human_decision(
        self, repository, case_id: str, idempotency_key: str
    ) -> HumanDecision:
        case = ReviewCase.create_from_data(
            json.loads(repository.get_review_case(case_id))["case"]
        )
        proposal = next(
            value
            for value in case.proposals
            if value["result"]["outcome"] == "unresolved_choice"
        )
        return HumanDecision.create(
            case,
            proposal_id=proposal["proposal_id"],
            target_fingerprint=case.target_fingerprint,
            destination=proposal["destination"],
            actor="owner",
            outcome="keep_current",
            selected_value=None,
            idempotency_key=idempotency_key,
            occurred_at=24,
        )

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
        self.assertEqual(
            transition,
            repository.activate_metadata_policy(
                draft.policy_id,
                builtin.policy_id,
                ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
                30,
            ),
        )
        follow_up = MetadataPolicyProfile.create(
            "Follow-up policy",
            {**dict(draft.options), "language_rendering": "preserve"},
            dict(draft.destinations),
        )
        repository.create_metadata_policy_draft(follow_up.canonical_json)
        follow_up_resolved = resolve_policy(follow_up, report.target.library)
        with self.assertRaisesRegex(MetadataReportError, "processing"):
            repository.activate_metadata_policy(
                follow_up.policy_id,
                draft.policy_id,
                ((report.target.library, follow_up_resolved.resolved_id, follow_up_resolved.canonical_json),),
                30,
            )
        self.assertEqual(repository.current_unapplied_count(report.target.library), 1)
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
        self.assertTrue(json.loads(repository.get_review_case(old_case_id))["current"])
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
        with self.assertRaisesRegex(MetadataReportError, "processing"):
            reopened.activate_metadata_policy(
                follow_up.policy_id,
                draft.policy_id,
                ((report.target.library, follow_up_resolved.resolved_id, follow_up_resolved.canonical_json),),
                30,
            )
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
        successor_case_id = admitted.get("review_case_id")
        assert type(successor_case_id) is str
        self.assertTrue(json.loads(reopened.get_review_case(successor_case_id))["current"])
        browsed = reopened.list_current_review_case_documents(None, 50, 0, None)
        self.assertIn(
            successor_case_id,
            tuple(ReviewCase.from_json(value[0]).review_case_id for value in browsed),
        )
        packet = reopened.preview_shadow_packet(successor_case_id)
        self.assertEqual(packet.review_case_id, successor_case_id)
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
        next_transition = reopened.activate_metadata_policy(
            follow_up.policy_id,
            draft.policy_id,
            ((report.target.library, follow_up_resolved.resolved_id, follow_up_resolved.canonical_json),),
            39,
        )
        assert next_transition is not None
        self.assertEqual(3, next_transition.generation)
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
        blocked = MetadataPolicyProfile.create(
            "Blocked follow-up",
            {**dict(draft.options), "language_rendering": "preserve"},
            dict(draft.destinations),
        )
        repository.create_metadata_policy_draft(blocked.canonical_json)
        blocked_resolution = resolve_policy(blocked, report.target.library)
        with self.assertRaisesRegex(MetadataReportError, "processing"):
            repository.activate_metadata_policy(
                blocked.policy_id,
                draft.policy_id,
                ((report.target.library, blocked_resolution.resolved_id, blocked_resolution.canonical_json),),
                41,
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

    def test_failed_provider_work_restarts_and_retries_at_safe_proposal_stage(self) -> None:
        repository, report, _, _ = self._current_case_with_completed_work()
        repository._connection.execute(
            "UPDATE review_preparation_work SET stage='provider',state='queued',report_id=?,updated_at=24",
            (report.report_id,),
        )
        repository._connection.execute(
            "UPDATE review_preparation_scans SET state='processing',updated_at=24"
        )
        repository._connection.commit()
        draft = _draft()
        repository.create_metadata_policy_draft(draft.canonical_json)
        resolved = resolve_policy(draft, report.target.library)
        transition = repository.activate_metadata_policy(
            draft.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
            25,
        )
        assert transition is not None
        provider = repository.claim_review_pipeline_action(26)
        assert provider is not None
        self.assertEqual("provider", provider.stage)
        repository.fail_review_pipeline_action(provider, "provider", False, 27)
        failed = repository.metadata_policy_transition()
        assert failed is not None
        self.assertEqual(("completed_with_failures", 1), (failed.state, failed.failed))
        repository.close()

        reopened = open_repository(self.path, create=False)
        retried = reopened.retry_metadata_policy_transition(transition.transition_id, 28)
        self.assertEqual(("processing", 0), (retried.state, retried.failed))
        action = reopened.claim_review_pipeline_action(29)
        assert action is not None
        self.assertEqual(("proposal", report.report_id), (action.stage, action.report_id))
        reopened.close()

    def test_failed_extraction_work_restarts_at_extraction_with_fixed_cohort(self) -> None:
        repository, report, _, _ = self._current_case_with_completed_work()
        repository._connection.execute(
            "UPDATE review_preparation_work SET stage='extraction',state='queued',"
            "attempt=0,recovery_attempt=0,report_id=NULL,proposal_set_id=NULL,"
            "review_case_id=NULL,failure_code=NULL,updated_at=24"
        )
        repository._connection.execute(
            "UPDATE review_preparation_scans SET state='processing',updated_at=24"
        )
        repository._connection.commit()
        draft = _draft()
        repository.create_metadata_policy_draft(draft.canonical_json)
        resolved = resolve_policy(draft, report.target.library)
        transition = repository.activate_metadata_policy(
            draft.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
            25,
        )
        assert transition is not None
        extraction = repository.claim_review_pipeline_action(26)
        assert extraction is not None
        self.assertEqual(("extraction", None), (extraction.stage, extraction.report_id))
        repository.fail_review_pipeline_action(extraction, "failed", False, 27)
        failed = repository.metadata_policy_transition()
        assert failed is not None
        self.assertEqual(("completed_with_failures", 1, 1), (failed.state, failed.failed, failed.total))
        fixed_item = repository._connection.execute(
            "SELECT report_id,work_id,state FROM metadata_policy_transition_items"
        ).fetchone()
        self.assertEqual((report.report_id, extraction.action_id, "failed"), fixed_item)
        repository.close()

        reopened = open_repository(self.path, create=False)
        retried = reopened.retry_metadata_policy_transition(transition.transition_id, 28)
        self.assertEqual(("processing", 0, 1), (retried.state, retried.failed, retried.total))
        self.assertEqual(
            ("extraction", "queued", 0, 0, None, None, None, None),
            reopened._connection.execute(
                "SELECT stage,state,attempt,recovery_attempt,report_id,proposal_set_id,"
                "review_case_id,failure_code FROM review_preparation_work"
            ).fetchone(),
        )
        self.assertEqual(
            (report.report_id, extraction.action_id, "pending"),
            reopened._connection.execute(
                "SELECT report_id,work_id,state FROM metadata_policy_transition_items"
            ).fetchone(),
        )
        action = reopened.claim_review_pipeline_action(29)
        assert action is not None
        self.assertEqual(("extraction", None), (action.stage, action.report_id))
        reopened.close()

    def test_activation_restarts_preexisting_terminal_early_work(self) -> None:
        for stage, expected_stage, expected_report in (
            ("extraction", "extraction", None),
            ("provider", "proposal", "fixed"),
        ):
            with self.subTest(stage=stage):
                path = Path(self.directory.name) / f"terminal-{stage}.sqlite"
                repository, report, _, _ = self._current_case_with_completed_work(path)
                repository._connection.execute(
                    "UPDATE review_preparation_work SET stage=?,state='failed',"
                    "attempt=3,recovery_attempt=2,report_id=?,proposal_set_id=NULL,"
                    "review_case_id=NULL,failure_code='failed',updated_at=24",
                    (stage, None if stage == "extraction" else report.report_id),
                )
                repository._connection.execute(
                    "UPDATE review_preparation_scans SET state='processing',updated_at=24"
                )
                repository._connection.commit()
                draft = _draft()
                repository.create_metadata_policy_draft(draft.canonical_json)
                resolved = resolve_policy(draft, report.target.library)

                transition = repository.activate_metadata_policy(
                    draft.policy_id,
                    MetadataPolicyProfile.builtin().policy_id,
                    ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
                    25,
                )
                assert transition is not None
                self.assertEqual(("processing", 0, 1), (transition.state, transition.failed, transition.total))
                action = repository.claim_review_pipeline_action(26)
                assert action is not None
                self.assertEqual(expected_stage, action.stage)
                self.assertEqual(
                    None if expected_report is None else report.report_id,
                    action.report_id,
                )
                repository.close()

    def test_atomic_extraction_callback_updates_fixed_transition_failure(self) -> None:
        for outcome in ("stale", "provider"):
            with self.subTest(outcome=outcome):
                path = Path(self.directory.name) / f"callback-{outcome}.sqlite"
                repository, report, _, _ = self._current_case_with_completed_work(path)
                repository._connection.execute(
                    "UPDATE review_preparation_work SET stage='extraction',state='queued',"
                    "attempt=0,recovery_attempt=0,report_id=NULL,proposal_set_id=NULL,"
                    "review_case_id=NULL,failure_code=NULL,updated_at=24"
                )
                repository._connection.execute(
                    "UPDATE review_preparation_scans SET state='processing',updated_at=24"
                )
                repository._connection.commit()
                draft = _draft()
                repository.create_metadata_policy_draft(draft.canonical_json)
                resolved = resolve_policy(draft, report.target.library)
                transition = repository.activate_metadata_policy(
                    draft.policy_id,
                    MetadataPolicyProfile.builtin().policy_id,
                    ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
                    25,
                )
                assert transition is not None
                extraction = repository.claim_review_pipeline_action(26)
                assert extraction is not None
                published = (
                    _report("Changed title", "c", "d")
                    if outcome == "stale"
                    else report
                )
                repository.publish(
                    published,
                    lambda: True,
                    None,
                    lambda value, exact: value.complete_review_extraction_publication(
                        extraction,  # noqa: B023 - closure is created and consumed within this iteration
                        exact.report_id,
                        27,
                        provider_failure_code=(
                            "provider" if outcome == "provider" else None  # noqa: B023 - closure is created and consumed within this iteration
                        ),
                    ),
                )
                current = repository.metadata_policy_transition()
                assert current is not None
                if outcome == "stale":
                    self.assertEqual(
                        ("completed_with_failures", 1),
                        (current.state, current.failed),
                    )
                    self.assertEqual(
                        "stale",
                        repository._connection.execute(
                            "SELECT failure_code FROM metadata_policy_transition_items"
                        ).fetchone()[0],
                    )
                else:
                    self.assertEqual(
                        ("processing", 0), (current.state, current.failed)
                    )
                    proposal = repository.claim_review_pipeline_action(28)
                    assert proposal is not None
                    self.assertEqual(proposal.stage, "proposal")
                repository.close()

    def test_new_library_preview_can_establish_next_generation_resolution(self) -> None:
        repository = open_repository(self.path)
        first = _draft()
        repository.create_metadata_policy_draft(first.canonical_json)
        first_resolution = resolve_policy(first, "first")
        transition = repository.activate_metadata_policy(
            first.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            (("first", first_resolution.resolved_id, first_resolution.canonical_json),),
            10,
        )
        assert transition is not None
        self.assertEqual("completed", transition.state)
        second = MetadataPolicyProfile.create(
            "Expanded policy",
            {**dict(first.options), "language_rendering": "preserve"},
            dict(first.destinations),
        )
        repository.create_metadata_policy_draft(second.canonical_json)
        notebook = PolicyNotebook(repository, ("first", "second"), {})

        preview = notebook.preview(second)
        self.assertEqual(("first", "second"), tuple(item.library for item in preview.libraries))
        configuration = SimpleNamespace(
            libraries=(SimpleNamespace(name="second", policy_override=None),)
        )
        with self.assertRaises(MetadataReportError):
            composition._current_resolved_policy(
                configuration, repository, "second"
            )
        activation = notebook.activate(second.policy_id, first.policy_id, 11)
        assert activation.transition is not None
        self.assertEqual(3, activation.transition.generation)
        persisted = repository.current_resolved_metadata_policy("second")
        assert persisted is not None
        self.assertEqual(second.policy_id, persisted.profile_id)
        repository.close()

    def test_generation_one_configured_override_binds_and_replays_through_admission(self) -> None:
        repository = open_repository(self.path)
        report = _report()
        repository.publish(report, lambda: True)
        override = {
            "options": {},
            "destinations": {"tags": "ignore"},
        }
        configuration = SimpleNamespace(
            libraries=(
                SimpleNamespace(
                    name=report.target.library,
                    policy_override=override,
                ),
            )
        )
        resolved = composition._current_resolved_policy(
            configuration, repository, report.target.library
        )
        proposal = evaluate_report(
            report, resolved.resolved_id, dict(resolved.destinations)
        )

        repository.publish_proposal(proposal)
        admitted = json.loads(repository.admit_review_case(proposal))
        self.assertEqual("admitted", admitted["status"])
        tags = next(
            result
            for result in proposal.results
            if result.destination is TargetDestination.TAGS
        )
        self.assertEqual((ProposalOutcome.INELIGIBLE, ("policy_ignored",)), (tags.outcome, tags.reasons))
        persisted = repository.current_resolved_metadata_policy(
            report.target.library
        )
        self.assertEqual(resolved, persisted)
        changed_configuration = SimpleNamespace(
            libraries=(
                SimpleNamespace(
                    name=report.target.library,
                    policy_override={"options": {}, "destinations": {"tags": "ask"}},
                ),
            )
        )
        with self.assertRaises(composition.ReviewCaseError):
            composition._current_resolved_policy(
                changed_configuration, repository, report.target.library
            )
        repository.close()

    def test_activation_and_human_decision_commit_orderings_are_atomic(self) -> None:
        decision_first_path = Path(self.directory.name) / "decision-first.sqlite"
        decision_writer, report, _, case_id = self._current_case_with_completed_work(
            decision_first_path
        )
        decision = self._human_decision(
            decision_writer, case_id, "decision-before-activation-0001"
        )
        activator = SQLiteMetadataReportRepository(
            sqlite3.connect(
                decision_first_path,
                check_same_thread=False,
                isolation_level=None,
            )
        )
        draft = _draft()
        decision_writer.create_metadata_policy_draft(draft.canonical_json)
        resolved = resolve_policy(draft, report.target.library)

        decision_writer.record_human_decision(decision)
        transition = activator.activate_metadata_policy(
            draft.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
            25,
        )
        assert transition is not None
        history = activator.get_review_case_history(case_id, 10)
        self.assertTrue(
            any(value["kind"] == "human_decision_recorded" for value in history)
        )
        self.assertTrue(
            any(
                value["kind"] == "retired" and value["reason"] == "policy_changed"
                for value in history
            )
        )
        self.assertTrue(json.loads(activator.get_review_case(case_id))["current"])
        self.assertEqual(
            (1, 1),
            activator._connection.execute(
                "SELECT (SELECT COUNT(*) FROM human_decisions),"
                "(SELECT COUNT(*) FROM human_decision_events)"
            ).fetchone(),
        )
        decision_writer.close()
        activator.close()

        activation_first_path = Path(self.directory.name) / "activation-first.sqlite"
        stale_writer, report, _, case_id = self._current_case_with_completed_work(
            activation_first_path
        )
        stale_decision = self._human_decision(
            stale_writer, case_id, "decision-after-activation-0001"
        )
        activator = SQLiteMetadataReportRepository(
            sqlite3.connect(
                activation_first_path,
                check_same_thread=False,
                isolation_level=None,
            )
        )
        draft = _draft()
        stale_writer.create_metadata_policy_draft(draft.canonical_json)
        resolved = resolve_policy(draft, report.target.library)

        transition = activator.activate_metadata_policy(
            draft.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
            25,
        )
        assert transition is not None
        with self.assertRaises(HumanDecisionError) as raised:
            stale_writer.record_human_decision(stale_decision)
        self.assertEqual("stale", raised.exception.code)
        self.assertEqual(
            (0, 0),
            stale_writer._connection.execute(
                "SELECT (SELECT COUNT(*) FROM human_decisions),"
                "(SELECT COUNT(*) FROM human_decision_events)"
            ).fetchone(),
        )
        self.assertTrue(json.loads(stale_writer.get_review_case(case_id))["current"])
        stale_writer.close()
        activator.close()

    def test_field_effect_authority_requires_exact_published_checkpoint(self) -> None:
        repository, _, _, case_id = self._current_case_with_completed_work()
        decision = self._human_decision(
            repository, case_id, "checkpoint-authority-0001"
        )
        repository.record_human_decision(decision)
        self.assertEqual(
            repository.field_effect_authority_source(decision)["review_case_id"],
            case_id,
        )
        repository._connection.execute(
            "UPDATE current_review_packages SET checkpoint=checkpoint+2 "
            "WHERE review_case_id=?",
            (case_id,),
        )
        repository._connection.commit()
        self.assertTrue(json.loads(repository.get_review_case(case_id))["current"])
        self.assertFalse(repository._review_case_accepts_authority(case_id))
        with self.assertRaisesRegex(FieldEffectError, "authority_binding"):
            repository.field_effect_authority_source(decision)
        repository.close()

    def test_invisible_stale_case_is_not_in_activation_cohort(self) -> None:
        repository = open_repository(self.path)
        report = _report()
        repository.publish(report, lambda: True)
        proposal = evaluate_report(report)
        repository.publish_proposal(proposal)
        repository.admit_review_case(proposal)
        changed = _report("Changed current title", "a", "c")
        repository.publish(changed, lambda: True, report.report_id)
        self.assertEqual(0, repository.current_unapplied_count(report.target.library))
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
        self.assertEqual(
            ("completed", 0, 0, 0),
            (transition.state, transition.completed, transition.total, transition.failed),
        )
        self.assertEqual(
            (0,),
            repository._connection.execute(
                "SELECT COUNT(*) FROM metadata_policy_transition_items"
            ).fetchone(),
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
