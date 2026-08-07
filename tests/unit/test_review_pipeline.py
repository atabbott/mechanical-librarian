from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import mechanical_librarian.bootstrap.composition as composition
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.catalogue import CatalogueEntry
from mechanical_librarian.application.metadata_report import (
    MetadataReportError,
    TargetNotFoundError,
)
from mechanical_librarian.application.review_pipeline import (
    PipelineAction,
    ReviewPipelineError,
    ReviewPreparationPipeline,
    ReviewPreparationSupervisor,
)
from mechanical_librarian.domain.metadata_report import ProviderStatus
from mechanical_librarian.domain.proposal_set import evaluate_report
from tests.unit.test_review_case_persistence import _report

_PRODUCER = "f" * 64


class ReviewPipelinePersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "application.sqlite3"
        repository = open_repository(self.path)
        repository.close()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_admission_target_not_found_is_typed_stale(self) -> None:
        repository = open_repository(self.path)
        report = _report()
        repository.publish(report, lambda: True)
        proposal = evaluate_report(report)
        repository.publish_proposal(proposal)
        repository.close()

        with (
            patch.object(composition, "CoverArtifactStore", return_value=object()),
            patch.object(
                composition,
                "_require_report_current_loaded",
                side_effect=TargetNotFoundError("target item is unavailable"),
            ),
        ):
            result = json.loads(
                composition.admit_review_case_loaded(
                    object(), self.path, Path("ignored"), proposal.proposal_set_id
                )
            )

        repository = open_repository(self.path)
        cases = repository._connection.execute(
            "SELECT COUNT(*) FROM review_cases"
        ).fetchone()
        repository.close()
        self.assertEqual(result["status"], "stale")
        self.assertIsNone(result["review_case_id"])
        self.assertEqual(cases, (0,))

    def test_request_discovery_claim_and_restart_are_persisted(self) -> None:
        repository = open_repository(self.path)
        first = repository.request_review_scan(
            "primary", "fiction", "lib-1", _PRODUCER, 10
        )
        duplicate = repository.request_review_scan(
            "primary", "fiction", "lib-1", _PRODUCER, 11
        )
        self.assertEqual(duplicate.scan_id, first.scan_id)
        discovery = repository.claim_review_pipeline_action(12)
        self.assertIsNotNone(discovery)
        assert discovery is not None
        self.assertEqual(discovery.stage, "discovery")
        entries = (
            CatalogueEntry(
                "primary",
                "fiction",
                "lib-1",
                "slow",
                1,
                "Slow synthetic work",
                ("Example Author",),
                True,
                False,
                "singular-supported",
                "1" * 64,
                "/slow.epub",
                "epub",
                "books",
            ),
            CatalogueEntry(
                "primary",
                "fiction",
                "lib-1",
                "fast",
                1,
                "Fast synthetic work",
                ("Example Author",),
                True,
                False,
                "singular-supported",
                "2" * 64,
                "/fast.epub",
                "epub",
                "books",
            ),
        )
        repository.complete_review_discovery(
            discovery, 1, "a" * 64, entries, 13
        )
        one = repository.claim_review_pipeline_action(14)
        two = repository.claim_review_pipeline_action(14)
        self.assertEqual({one.item_id, two.item_id}, {"slow", "fast"})
        repository.close()

        reopened = open_repository(self.path)
        reopened.recover_review_pipeline(
            (("primary", "fiction", "lib-1", _PRODUCER),)
        )
        recovered = {
            reopened.claim_review_pipeline_action(15).item_id,
            reopened.claim_review_pipeline_action(15).item_id,
        }
        self.assertEqual(recovered, {"slow", "fast"})
        progress = reopened.request_review_scan(
            "primary", "fiction", "lib-1", _PRODUCER, 16
        )
        self.assertTrue(progress.rescan_requested)
        reopened.close()

    def test_complete_empty_discovery_reaches_a_bounded_summary(self) -> None:
        repository = open_repository(self.path)
        repository.request_review_scan(
            "primary", "fiction", "lib-1", _PRODUCER, 10
        )
        action = repository.claim_review_pipeline_action(11)
        assert action is not None
        repository.complete_review_discovery(action, 1, "b" * 64, (), 12)
        self.assertIsNone(repository.claim_review_pipeline_action(13))
        progress = repository.review_pipeline_progress("primary", "fiction")
        self.assertIsNotNone(progress)
        assert progress is not None
        self.assertEqual((progress.state, progress.total), ("completed", 0))
        changed_producer = "d" * 64
        repository.recover_review_pipeline(
            (("primary", "fiction", "lib-2", changed_producer),)
        )
        self.assertIsNone(repository.claim_review_pipeline_action(14))
        unchanged = repository.review_pipeline_progress("primary", "fiction")
        assert unchanged is not None
        self.assertEqual(unchanged.state, "completed")
        repository.request_review_scan(
            "primary", "fiction", "lib-2", changed_producer, 15
        )
        refreshed = repository.claim_review_pipeline_action(16)
        assert refreshed is not None
        self.assertEqual(refreshed.library_id, "lib-2")
        self.assertEqual(refreshed.producer_fingerprint, changed_producer)
        repository.close()

    def test_failed_rediscovery_cannot_claim_queued_children(self) -> None:
        repository = open_repository(self.path)
        repository.request_review_scan(
            "primary", "fiction", "lib-1", _PRODUCER, 10
        )
        discovery = repository.claim_review_pipeline_action(11)
        assert discovery is not None
        repository.complete_review_discovery(
            discovery, 1, "a" * 64, (_entry("queued", "9" * 64),), 12
        )
        changed_producer = "d" * 64
        repository.recover_review_pipeline(
            (("primary", "fiction", "lib-2", changed_producer),)
        )
        rediscovery = repository.claim_review_pipeline_action(13)
        assert rediscovery is not None
        self.assertEqual(rediscovery.stage, "discovery")
        repository.fail_review_pipeline_action(
            rediscovery, "transient", True, 14
        )

        self.assertIsNone(repository.claim_review_pipeline_action(15))
        progress = repository.review_pipeline_progress("primary", "fiction")
        child = repository._connection.execute(
            "SELECT state FROM review_preparation_work"
        ).fetchone()
        repository.close()

        assert progress is not None
        self.assertEqual(progress.state, "completed_with_failures")
        self.assertEqual(child, ("queued",))

    def test_third_crashed_stage_is_terminal_instead_of_overflowing_claim(self) -> None:
        repository = open_repository(self.path)
        repository.request_review_scan(
            "primary", "fiction", "lib-1", _PRODUCER, 10
        )
        discovery = repository.claim_review_pipeline_action(11)
        assert discovery is not None
        repository.complete_review_discovery(
            discovery, 1, "a" * 64, (_entry("crashed", "9" * 64),), 12
        )
        action = repository.claim_review_pipeline_action(13)
        assert action is not None
        repository._connection.execute(
            "UPDATE review_preparation_work SET attempt=3 WHERE work_id=?",
            (action.action_id,),
        )
        repository._connection.commit()
        repository.recover_review_pipeline(
            (("primary", "fiction", "lib-1", _PRODUCER),)
        )
        self.assertIsNone(repository.claim_review_pipeline_action(14))
        progress = repository.review_pipeline_progress("primary", "fiction")
        assert progress is not None
        self.assertEqual(
            (progress.state, progress.checked, progress.blocked),
            ("completed_with_failures", 1, 1),
        )
        repository.close()

    def test_complete_scan_tombstones_only_confirmed_absence(self) -> None:
        repository = open_repository(self.path)
        report = _report()
        repository.publish(report, lambda: True)
        proposal = evaluate_report(report)
        repository.publish_proposal(proposal)
        repository.admit_review_case(proposal)
        repository.request_review_scan(
            report.target.connection,
            report.target.library,
            "lib-1",
            _PRODUCER,
            10,
        )
        discovery = repository.claim_review_pipeline_action(11)
        assert discovery is not None
        repository.complete_review_discovery(
            discovery, 1, "c" * 64, (), 12
        )
        self.assertIsNone(repository.claim_review_pipeline_action(13))
        confirmation = repository.claim_review_pipeline_action(14)
        assert confirmation is not None
        self.assertEqual(confirmation.stage, "discovery")
        repository.complete_review_discovery(
            confirmation, 2, "c" * 64, (), 15
        )
        self.assertIsNone(repository.claim_review_pipeline_action(16))
        repository.close()

        connection = sqlite3.connect(self.path)
        pointer = connection.execute(
            "SELECT review_case_id,outcome FROM current_review_case_lineages"
        ).fetchone()
        transition = connection.execute(
            "SELECT actor,kind,reason FROM review_case_transitions "
            "ORDER BY checkpoint DESC LIMIT 1"
        ).fetchone()
        connection.close()
        self.assertEqual(pointer, (None, "tombstoned"))
        self.assertEqual(
            transition, ("review-preparation-v1", "tombstoned", "absent")
        )

        repository = open_repository(self.path)
        reappeared = json.loads(repository.admit_review_case(proposal))
        pointer = repository._connection.execute(
            "SELECT review_case_id,outcome FROM current_review_case_lineages"
        ).fetchone()
        repository.close()
        self.assertEqual(reappeared["status"], "admitted")
        self.assertEqual(pointer, (reappeared["review_case_id"], "admitted"))

    def test_complete_scan_retires_present_item_that_became_ineligible(self) -> None:
        repository = open_repository(self.path)
        report = _report()
        repository.publish(report, lambda: True)
        proposal = evaluate_report(report)
        repository.publish_proposal(proposal)
        admitted = json.loads(repository.admit_review_case(proposal))
        repository.request_review_scan(
            report.target.connection,
            report.target.library,
            "lib-1",
            _PRODUCER,
            10,
        )
        discovery = repository.claim_review_pipeline_action(11)
        assert discovery is not None
        ineligible = replace(
            _entry(report.target.item, report.file.identity),
            library=report.target.library,
            classification="singular-unmounted",
            mount=None,
        )
        repository.complete_review_discovery(
            discovery, 1, "c" * 64, (ineligible,), 12
        )
        self.assertIsNone(repository.claim_review_pipeline_action(13))
        confirmation = repository.claim_review_pipeline_action(14)
        assert confirmation is not None
        repository.complete_review_discovery(
            confirmation, 2, "c" * 64, (ineligible,), 15
        )
        self.assertIsNone(repository.claim_review_pipeline_action(16))
        pointer = repository._connection.execute(
            "SELECT review_case_id,outcome FROM current_review_case_lineages"
        ).fetchone()
        transition = repository._connection.execute(
            "SELECT actor,kind,reason FROM review_case_transitions "
            "ORDER BY checkpoint DESC LIMIT 1"
        ).fetchone()
        reopened = json.loads(repository.admit_review_case(proposal))
        repository.close()

        self.assertEqual(pointer, (None, "retired"))
        self.assertEqual(
            transition, ("review-preparation-v1", "retired", "ineligible")
        )
        self.assertEqual(reopened["status"], "admitted")
        self.assertEqual(reopened["review_case_id"], admitted["review_case_id"])

    def test_confirmation_discovery_cancels_absence_after_reappearance(self) -> None:
        repository = open_repository(self.path)
        report = _report()
        repository.publish(report, lambda: True)
        proposal = evaluate_report(report)
        repository.publish_proposal(proposal)
        admitted = json.loads(repository.admit_review_case(proposal))
        repository.request_review_scan(
            report.target.connection,
            report.target.library,
            "lib-1",
            _PRODUCER,
            10,
        )
        discovery = repository.claim_review_pipeline_action(11)
        assert discovery is not None
        repository.complete_review_discovery(
            discovery, 1, "c" * 64, (), 12
        )
        self.assertIsNone(repository.claim_review_pipeline_action(13))
        confirmation = repository.claim_review_pipeline_action(14)
        assert confirmation is not None
        reappeared = replace(
            _entry(report.target.item, report.file.identity),
            library=report.target.library,
        )
        repository.complete_review_discovery(
            confirmation, 2, "c" * 64, (reappeared,), 15
        )
        pointer = repository._connection.execute(
            "SELECT review_case_id,outcome FROM current_review_case_lineages"
        ).fetchone()
        staged = repository._connection.execute(
            "SELECT COUNT(*) FROM review_preparation_absences"
        ).fetchone()
        repository.close()

        self.assertEqual(pointer, (admitted["review_case_id"], "admitted"))
        self.assertEqual(staged, (0,))

    def test_changed_retirement_candidate_is_discarded_without_third_scan(self) -> None:
        repository = open_repository(self.path)
        report = _report()
        repository.publish(report, lambda: True)
        proposal = evaluate_report(report)
        repository.publish_proposal(proposal)
        admitted = json.loads(repository.admit_review_case(proposal))
        repository.request_review_scan(
            report.target.connection,
            report.target.library,
            "lib-1",
            _PRODUCER,
            10,
        )
        discovery = repository.claim_review_pipeline_action(11)
        assert discovery is not None
        repository.complete_review_discovery(discovery, 1, "c" * 64, (), 12)
        self.assertIsNone(repository.claim_review_pipeline_action(13))
        confirmation = repository.claim_review_pipeline_action(14)
        assert confirmation is not None
        ineligible = replace(
            _entry(report.target.item, report.file.identity),
            library=report.target.library,
            classification="multiple-files",
        )
        repository.complete_review_discovery(
            confirmation, 2, "d" * 64, (ineligible,), 15
        )

        self.assertIsNone(repository.claim_review_pipeline_action(16))
        self.assertIsNone(repository.claim_review_pipeline_action(17))
        pointer = repository._connection.execute(
            "SELECT review_case_id,outcome FROM current_review_case_lineages"
        ).fetchone()
        staged = repository._connection.execute(
            "SELECT COUNT(*) FROM review_preparation_absences"
        ).fetchone()
        repository.close()
        self.assertEqual(pointer, (admitted["review_case_id"], "admitted"))
        self.assertEqual(staged, (0,))

    def test_later_rediscovery_confirms_new_absence_before_tombstone(self) -> None:
        repository = open_repository(self.path)
        report = _report()
        repository.publish(report, lambda: True)
        proposal = evaluate_report(report)
        repository.publish_proposal(proposal)
        admitted = json.loads(repository.admit_review_case(proposal))
        repository.request_review_scan(
            report.target.connection,
            report.target.library,
            "lib-1",
            _PRODUCER,
            10,
        )
        discovery = repository.claim_review_pipeline_action(11)
        assert discovery is not None
        present = replace(
            _entry(report.target.item, report.file.identity),
            library=report.target.library,
        )
        repository.complete_review_discovery(discovery, 1, "c" * 64, (present,), 12)
        repository.request_review_scan(
            report.target.connection,
            report.target.library,
            "lib-1",
            _PRODUCER,
            13,
        )
        work = repository.claim_review_pipeline_action(14)
        while work is not None:
            repository.advance_review_pipeline_action(work, None, 15)
            work = repository.claim_review_pipeline_action(16)
        later = repository.claim_review_pipeline_action(17)
        assert later is not None
        repository.complete_review_discovery(later, 2, "d" * 64, (), 18)

        self.assertIsNone(repository.claim_review_pipeline_action(19))
        confirmation = repository.claim_review_pipeline_action(20)
        assert confirmation is not None
        self.assertEqual(confirmation.stage, "discovery")
        pointer = repository._connection.execute(
            "SELECT review_case_id,outcome FROM current_review_case_lineages"
        ).fetchone()
        self.assertEqual(pointer, (admitted["review_case_id"], "admitted"))

        repository.complete_review_discovery(confirmation, 3, "e" * 64, (), 21)
        self.assertIsNone(repository.claim_review_pipeline_action(22))
        pointer = repository._connection.execute(
            "SELECT review_case_id,outcome FROM current_review_case_lineages"
        ).fetchone()
        repository.close()
        self.assertEqual(pointer, (None, "tombstoned"))

    def test_failed_discovery_cannot_tombstone_current_lineage(self) -> None:
        repository = open_repository(self.path)
        report = _report()
        repository.publish(report, lambda: True)
        proposal = evaluate_report(report)
        repository.publish_proposal(proposal)
        admitted = json.loads(repository.admit_review_case(proposal))
        repository.close()
        pipeline = ReviewPreparationPipeline(
            repository_factory=lambda: open_repository(self.path),
            libraries=(("primary", "ebooks", "lib-1", _PRODUCER),),
            discover=lambda *_: (_ for _ in ()).throw(RuntimeError("synthetic")),
            extract=lambda _: (_ for _ in ()).throw(AssertionError()),
            providers=lambda _: (),
            propose=lambda _: (_ for _ in ()).throw(AssertionError()),
            admit=lambda _: (_ for _ in ()).throw(AssertionError()),
            project=lambda _: True,
            clock=lambda: 20,
        )
        pipeline.request("primary", "ebooks")
        self.assertTrue(pipeline.run_next())
        repository = open_repository(self.path)
        pointer = repository._connection.execute(
            "SELECT review_case_id,outcome FROM current_review_case_lineages"
        ).fetchone()
        repository.close()
        self.assertEqual(pointer, (admitted["review_case_id"], "admitted"))

    def test_later_work_failure_discards_staged_ineligible_retirement(self) -> None:
        repository = open_repository(self.path)
        report = _report()
        repository.publish(report, lambda: True)
        proposal = evaluate_report(report)
        repository.publish_proposal(proposal)
        admitted = json.loads(repository.admit_review_case(proposal))
        repository.request_review_scan(
            report.target.connection,
            report.target.library,
            "lib-1",
            _PRODUCER,
            10,
        )
        discovery = repository.claim_review_pipeline_action(11)
        assert discovery is not None
        repository.complete_review_discovery(
            discovery,
            1,
            "d" * 64,
            (
                replace(
                    _entry(report.target.item, report.file.identity),
                    library=report.target.library,
                    classification="singular-unmounted",
                    mount=None,
                ),
                replace(
                    _entry("later-failure", "9" * 64),
                    library=report.target.library,
                ),
            ),
            12,
        )
        work = repository.claim_review_pipeline_action(13)
        assert work is not None
        repository.fail_review_pipeline_action(work, "failed", False, 14)
        self.assertIsNone(repository.claim_review_pipeline_action(15))
        pointer = repository._connection.execute(
            "SELECT review_case_id,outcome FROM current_review_case_lineages"
        ).fetchone()
        staged = repository._connection.execute(
            "SELECT COUNT(*) FROM review_preparation_absences"
        ).fetchone()
        progress = repository.review_pipeline_progress(
            report.target.connection, report.target.library
        )
        repository.close()
        assert progress is not None
        self.assertEqual(pointer, (admitted["review_case_id"], "admitted"))
        self.assertEqual(staged, (0,))
        self.assertEqual(progress.state, "completed_with_failures")

    def test_one_work_progresses_through_every_persisted_stage(self) -> None:
        report = _report()
        proposal = evaluate_report(report)
        entries = [CatalogueEntry(
            "primary",
            "ebooks",
            "lib-1",
            "item-1",
            1,
            "Synthetic work",
            ("Example Author",),
            True,
            False,
            "singular-supported",
            report.file.identity,
            "/item-1.epub",
            "epub",
            "books",
        )]
        extracted: list[str] = []

        def publish_report(_: PipelineAction):
            extracted.append(report.report_id)
            repository = open_repository(self.path)
            try:
                repository.publish(report, lambda: True)
            finally:
                repository.close()
            return report.report_id, False

        def publish_proposal(_: str) -> str:
            repository = open_repository(self.path)
            try:
                repository.publish_proposal(proposal)
            finally:
                repository.close()
            return proposal.proposal_set_id

        def admit(_: str) -> tuple[str, str | None]:
            repository = open_repository(self.path)
            try:
                result = json.loads(repository.admit_review_case(proposal))
            finally:
                repository.close()
            return result["status"], result.get("review_case_id")

        pipeline = ReviewPreparationPipeline(
            repository_factory=lambda: open_repository(self.path),
            libraries=(("primary", "ebooks", "lib-1", _PRODUCER),),
            discover=lambda *_: (1, "d" * 64, tuple(entries)),
            extract=publish_report,
            providers=lambda _: (ProviderStatus.DISABLED,),
            propose=publish_proposal,
            admit=admit,
            project=lambda case_id: case_id.startswith("review_case_"),
            clock=lambda: 20,
        )
        pipeline.request("primary", "ebooks")
        self.assertEqual(sum(pipeline.run_next() for _ in range(6)), 6)
        self.assertFalse(pipeline.run_next())
        progress = pipeline.progress("primary", "ebooks")
        assert progress is not None
        self.assertEqual(
            (progress.state, progress.checked, progress.total, progress.ready),
            ("completed", 1, 1, 1),
        )
        self.assertEqual(len(extracted), 1)

        pipeline.request("primary", "ebooks")
        self.assertTrue(pipeline.run_next())
        self.assertFalse(pipeline.run_next())
        self.assertEqual(len(extracted), 1)

        entries[0] = replace(entries[0], updated_at=2)
        pipeline.request("primary", "ebooks")
        self.assertTrue(pipeline.run_next())
        self.assertTrue(pipeline.run_next())
        self.assertEqual(len(extracted), 2)

        changed_producer = "e" * 64
        repository = open_repository(self.path)
        repository.recover_review_pipeline(
            (("primary", "ebooks", "lib-1", changed_producer),)
        )
        discovery = repository.claim_review_pipeline_action(40)
        assert discovery is not None
        self.assertEqual(discovery.producer_fingerprint, changed_producer)
        repository.complete_review_discovery(
            discovery, 2, "e" * 64, tuple(entries), 41
        )
        restarted = repository.claim_review_pipeline_action(42)
        assert restarted is not None
        repository.close()
        self.assertEqual(restarted.stage, "extraction")
        self.assertEqual(restarted.producer_fingerprint, changed_producer)
        self.assertIsNone(restarted.report_id)

    def test_provider_technical_failure_stops_before_proposal(self) -> None:
        proposed: list[str] = []
        extracted: list[str] = []
        report = _report()

        def publish_report(_: PipelineAction) -> str:
            extracted.append(report.report_id)
            repository = open_repository(self.path)
            try:
                repository.publish(report, lambda: True)
            finally:
                repository.close()
            return report.report_id, False

        pipeline = ReviewPreparationPipeline(
            repository_factory=lambda: open_repository(self.path),
            libraries=(("primary", "ebooks", "lib-1", _PRODUCER),),
            discover=lambda *_: (
                1,
                "e" * 64,
                (
                    CatalogueEntry(
                        "primary",
                        "ebooks",
                        "lib-1",
                        "item-1",
                        1,
                        "Blocked synthetic work",
                        (),
                        True,
                        False,
                        "singular-supported",
                        report.file.identity,
                        "/item-1.epub",
                        "epub",
                        "books",
                    ),
                ),
            ),
            extract=publish_report,
            providers=lambda _: (
                ProviderStatus.CANDIDATES,
                ProviderStatus.UNAVAILABLE,
            ),
            propose=lambda report_id: proposed.append(report_id) or "unreachable",
            admit=lambda _: ("no_case", None),
            project=lambda _: True,
            clock=lambda: 30,
        )
        pipeline.request("primary", "ebooks")
        self.assertTrue(pipeline.run_next())
        self.assertTrue(pipeline.run_next())
        self.assertTrue(pipeline.run_next())
        self.assertFalse(pipeline.run_next())
        self.assertEqual(proposed, [])
        progress = pipeline.progress("primary", "ebooks")
        assert progress is not None
        self.assertEqual(
            (progress.state, progress.checked, progress.blocked),
            ("completed_with_failures", 1, 1),
        )
        pipeline.request("primary", "ebooks")
        self.assertTrue(pipeline.run_next())
        self.assertTrue(pipeline.run_next())
        self.assertEqual(len(extracted), 2)

    def test_repeated_extraction_currentness_drift_requests_rediscovery(
        self,
    ) -> None:
        entry = replace(
            _entry("item-1", "b" * 64),
            library="ebooks",
        )
        pipeline = ReviewPreparationPipeline(
            repository_factory=lambda: open_repository(self.path),
            libraries=(("primary", "ebooks", "lib-1", _PRODUCER),),
            discover=lambda *_: (1, "e" * 64, (entry,)),
            extract=lambda _: (_ for _ in ()).throw(
                ReviewPipelineError("stale")
            ),
            providers=lambda _: (),
            propose=lambda _: (_ for _ in ()).throw(AssertionError()),
            admit=lambda _: (_ for _ in ()).throw(AssertionError()),
            project=lambda _: True,
            clock=lambda: 30,
        )
        pipeline.request("primary", "ebooks")
        self.assertTrue(pipeline.run_next())
        self.assertEqual(sum(pipeline.run_next() for _ in range(3)), 3)

        repository = open_repository(self.path)
        progress = repository.review_pipeline_progress("primary", "ebooks")
        reports = repository._connection.execute(
            "SELECT COUNT(*) FROM metadata_reports"
        ).fetchone()
        cases = repository._connection.execute(
            "SELECT COUNT(*) FROM review_cases"
        ).fetchone()
        repository.close()

        assert progress is not None
        self.assertTrue(progress.rescan_requested)
        self.assertEqual(reports, (0,))
        self.assertEqual(cases, (0,))

    def test_report_and_extraction_checkpoint_commit_atomically(self) -> None:
        report = _report()
        entry = CatalogueEntry(
            "primary",
            "ebooks",
            "lib-1",
            "item-1",
            1,
            "Synthetic work",
            (),
            True,
            False,
            "singular-supported",
            report.file.identity,
            "/item-1.epub",
            "epub",
            "books",
        )
        repository = open_repository(self.path)
        repository.request_review_scan(
            "primary", "ebooks", "lib-1", _PRODUCER, 10
        )
        discovery = repository.claim_review_pipeline_action(11)
        assert discovery is not None
        repository.complete_review_discovery(
            discovery, 1, "a" * 64, (entry,), 12
        )
        action = repository.claim_review_pipeline_action(13)
        assert action is not None

        def fail_after_checkpoint(value, published) -> None:
            value.complete_review_extraction_publication(
                action, published.report_id, 14
            )
            raise RuntimeError("synthetic rollback")

        with self.assertRaises(MetadataReportError):
            repository.publish(report, lambda: True, None, fail_after_checkpoint)
        self.assertEqual(
            repository._connection.execute(
                "SELECT COUNT(*) FROM metadata_reports"
            ).fetchone(),
            (0,),
        )
        self.assertEqual(
            repository._connection.execute(
                "SELECT stage,state,report_id FROM review_preparation_work"
            ).fetchone(),
            ("extraction", "running", None),
        )
        repository.publish(
            report,
            lambda: True,
            None,
            lambda value, published: value.complete_review_extraction_publication(
                action, published.report_id, 15
            ),
        )
        repository.close()

        reopened = open_repository(self.path)
        reopened.recover_review_pipeline(
            (("primary", "ebooks", "lib-1", _PRODUCER),)
        )
        resumed = reopened.claim_review_pipeline_action(16)
        assert resumed is not None
        reopened.close()
        self.assertEqual(resumed.stage, "provider")
        self.assertEqual(resumed.report_id, report.report_id)


def _entry(item_id: str, file_identity: str) -> CatalogueEntry:
    return CatalogueEntry(
        "primary",
        "fiction",
        "lib-1",
        item_id,
        1,
        "Synthetic work",
        ("Example Author",),
        True,
        False,
        "singular-supported",
        file_identity,
        f"/{item_id}.epub",
        "epub",
        "books",
    )


class _ConcurrentRepository:
    def __init__(self, actions: list[PipelineAction]) -> None:
        self.actions = actions
        self.lock = threading.Lock()
        self.completed: list[str] = []

    def request_review_scan(self, *args):  # pragma: no cover - not used here
        raise AssertionError

    def review_pipeline_progress(self, *args):  # pragma: no cover - not used here
        return None

    def recover_review_pipeline(self, *args) -> None:
        return None

    def claim_review_pipeline_action(self, claimed_at: int):
        with self.lock:
            return None if not self.actions else self.actions.pop(0)

    def complete_review_discovery(self, *args):  # pragma: no cover - not used here
        raise AssertionError

    def advance_review_pipeline_action(self, action, output_id, completed_at):
        with self.lock:
            self.completed.append(action.item_id)

    def fail_review_pipeline_action(self, *args):
        raise AssertionError

    def close(self) -> None:
        return None


class ReviewPipelineConcurrencyTests(unittest.TestCase):
    def test_slow_work_does_not_hold_a_library_wide_stage_barrier(self) -> None:
        slow_started = threading.Event()
        release_slow = threading.Event()
        fast_completed = threading.Event()
        actions = [
            PipelineAction(
                "review_work_" + "1" * 64,
                "review_scan_" + "a" * 64,
                "extraction",
                1,
                "primary",
                "fiction",
                "lib-1",
                _PRODUCER,
                1,
                "b" * 64,
                "slow",
                "1" * 64,
                1,
            ),
            PipelineAction(
                "review_work_" + "2" * 64,
                "review_scan_" + "a" * 64,
                "extraction",
                1,
                "primary",
                "fiction",
                "lib-1",
                _PRODUCER,
                1,
                "b" * 64,
                "fast",
                "2" * 64,
                1,
            ),
        ]
        repository = _ConcurrentRepository(actions)

        def extract(action: PipelineAction):
            if action.item_id == "slow":
                slow_started.set()
                self.assertTrue(release_slow.wait(2))
            else:
                fast_completed.set()
            return "report_" + "3" * 64, False

        pipeline = ReviewPreparationPipeline(
            repository_factory=lambda: repository,
            libraries=(("primary", "fiction", "lib-1", _PRODUCER),),
            discover=lambda *_: (_ for _ in ()).throw(AssertionError()),
            extract=extract,
            providers=lambda _: (ProviderStatus.DISABLED,),
            propose=lambda _: "proposal_set_" + "4" * 64,
            admit=lambda _: ("admitted", "review_case_" + "5" * 64),
            project=lambda _: True,
            clock=lambda: 20,
        )
        supervisor = ReviewPreparationSupervisor(pipeline)
        supervisor.start()
        try:
            self.assertTrue(slow_started.wait(1))
            self.assertTrue(fast_completed.wait(1))
        finally:
            release_slow.set()
            supervisor.stop()


if __name__ == "__main__":
    unittest.main()
