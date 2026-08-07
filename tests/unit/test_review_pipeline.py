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
    PipelineProgress,
    ReviewPipelineError,
    ReviewPreparationPipeline,
    ReviewPreparationSupervisor,
    _provider_failure_disposition,
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

    def test_pause_state_is_durable_and_does_not_remove_committed_work(self) -> None:
        repository = open_repository(self.path)
        repository.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, 10)
        repository.set_review_pipeline_paused(True, 11)
        repository.close()

        reopened = open_repository(self.path)
        self.assertTrue(reopened.review_pipeline_paused())
        action = reopened.claim_review_pipeline_action(12)
        reopened.set_review_pipeline_paused(False, 13)
        self.assertFalse(reopened.review_pipeline_paused())
        reopened.close()
        assert action is not None
        self.assertEqual(action.stage, "discovery")

    def test_pause_atomically_rejects_new_scan_admission(self) -> None:
        repository = open_repository(self.path)
        repository.set_review_pipeline_paused(True, 10)
        with self.assertRaisesRegex(ReviewPipelineError, "paused"):
            repository.request_review_scan(
                "primary", "fiction", "lib-1", _PRODUCER, 11
            )
        count = repository._connection.execute(
            "SELECT COUNT(*) FROM review_preparation_scans"
        ).fetchone()
        repository.close()
        self.assertEqual(count, (0,))

    def test_provider_failures_separate_recovery_from_configuration_validation(self) -> None:
        self.assertEqual(
            _provider_failure_disposition((ProviderStatus.UNAVAILABLE,)),
            ("transient", True),
        )
        self.assertEqual(
            _provider_failure_disposition((ProviderStatus.AUTHENTICATION,)),
            ("configuration", False),
        )
        self.assertEqual(
            _provider_failure_disposition((ProviderStatus.MALFORMED,)),
            ("provider", False),
        )

    def test_thousand_work_three_library_backlog_is_bounded_and_fair(self) -> None:
        repository = open_repository(self.path)
        libraries = (("fiction", 334), ("history", 333), ("science", 333))
        for offset, (library, count) in enumerate(libraries):
            repository.request_review_scan(
                "primary", library, f"lib-{offset}", _PRODUCER, 10 + offset
            )
        for offset, (library, count) in enumerate(libraries):
            discovery = repository.claim_review_pipeline_action(20 + offset)
            assert discovery is not None
            self.assertEqual(discovery.library, library)
            entries = tuple(
                replace(
                    _entry(f"{library}-{index}", f"{index:064x}"),
                    library=library,
                    library_id=f"lib-{offset}",
                    updated_at=1000 + index,
                )
                for index in range(count)
            )
            repository.complete_review_discovery(
                discovery, offset + 1, f"{offset + 1:064x}", entries, 30 + offset
            )
        total = repository._connection.execute(
            "SELECT COUNT(*) FROM review_preparation_work"
        ).fetchone()
        claimed = []
        for timestamp in range(40, 49):
            action = repository.claim_review_pipeline_action(timestamp)
            assert action is not None
            claimed.append(action.library)
            repository.fail_review_pipeline_action(action, "failed", False, timestamp)
        repository.close()
        self.assertEqual(total, (1000,))
        for start in range(0, 9, 3):
            self.assertEqual(set(claimed[start : start + 3]), {item[0] for item in libraries})

    def test_claims_age_backlog_and_reserve_every_fourth_for_recent_work(self) -> None:
        repository = open_repository(self.path)
        repository.request_review_scan(
            "primary", "fiction", "lib-1", _PRODUCER, 10
        )
        discovery = repository.claim_review_pipeline_action(11)
        assert discovery is not None
        repository.complete_review_discovery(
            discovery,
            1,
            "a" * 64,
            (_entry("old", "1" * 64), _entry("recent", "2" * 64)),
            12,
        )
        repository._connection.execute(
            "UPDATE review_preparation_work SET updated_at=10 WHERE item_id='old'"
        )
        repository._connection.execute(
            "UPDATE review_preparation_work SET updated_at=20 WHERE item_id='recent'"
        )
        repository._connection.execute(
            "UPDATE review_preparation_control SET claim_sequence=? WHERE singleton=1",
            (1,),
        )
        repository._connection.commit()
        aged = repository.claim_review_pipeline_action(30)
        assert aged is not None
        repository.fail_review_pipeline_action(aged, "failed", False, 31)
        repository._connection.execute(
            "UPDATE review_preparation_control SET claim_sequence=? WHERE singleton=1",
            (4,),
        )
        repository._connection.execute(
            "UPDATE review_preparation_work SET state='queued',updated_at=10 "
            "WHERE item_id='old'"
        )
        repository._connection.commit()
        recent = repository.claim_review_pipeline_action(32)
        repository.close()
        assert recent is not None
        self.assertEqual((aged.item_id, recent.item_id), ("old", "recent"))

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

        entries[0] = replace(
            entries[0], updated_at=2, source_fingerprint="9" * 64
        )
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
        clock = [30]
        provider_statuses = [
            (ProviderStatus.CANDIDATES, ProviderStatus.UNAVAILABLE),
            (ProviderStatus.CANDIDATES,),
        ]
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
            providers=lambda _: provider_statuses.pop(0),
            propose=lambda report_id: proposed.append(report_id) or "unreachable",
            admit=lambda _: ("no_case", None),
            project=lambda _: True,
            clock=lambda: clock[0],
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
            ("processing", 0, 0),
        )
        repository = open_repository(self.path)
        deadline = repository._connection.execute(
            "SELECT circuit_open_until FROM review_preparation_control"
        ).fetchone()[0]
        repository.close()
        self.assertGreater(deadline, clock[0])
        clock[0] = deadline - 1
        self.assertFalse(pipeline.run_next())
        clock[0] = deadline
        self.assertTrue(pipeline.run_next())
        self.assertTrue(pipeline.run_next())
        self.assertEqual(len(extracted), 2)
        self.assertEqual(proposed, [])
        self.assertTrue(pipeline.run_next())
        self.assertEqual(proposed, [report.report_id])
        repository = open_repository(self.path)
        control = repository._connection.execute(
            "SELECT circuit_failures,circuit_open_until FROM review_preparation_control"
        ).fetchone()
        repository.close()
        self.assertEqual(control, (0, 0))

    def test_persistent_transient_provider_failure_exhausts_automatic_recovery(
        self,
    ) -> None:
        report = _report()
        clock = [30]
        extracted: list[str] = []
        provider_calls = 0

        def publish_report(_: PipelineAction) -> tuple[str, bool]:
            extracted.append(report.report_id)
            repository = open_repository(self.path)
            try:
                repository.publish(report, lambda: True)
            finally:
                repository.close()
            return report.report_id, False

        def unavailable(_: str) -> tuple[ProviderStatus, ...]:
            nonlocal provider_calls
            provider_calls += 1
            return (ProviderStatus.UNAVAILABLE,)

        pipeline = ReviewPreparationPipeline(
            repository_factory=lambda: open_repository(self.path),
            libraries=(("primary", "ebooks", "lib-1", _PRODUCER),),
            discover=lambda *_: (
                1,
                "e" * 64,
                (replace(_entry("item-1", report.file.identity), library="ebooks"),),
            ),
            extract=publish_report,
            providers=unavailable,
            propose=lambda _: (_ for _ in ()).throw(AssertionError()),
            admit=lambda _: (_ for _ in ()).throw(AssertionError()),
            project=lambda _: True,
            clock=lambda: clock[0],
        )
        pipeline.request("primary", "ebooks")
        self.assertTrue(pipeline.run_next())
        for recovery in range(3):
            self.assertTrue(pipeline.run_next())
            self.assertTrue(pipeline.run_next())
            repository = open_repository(self.path)
            deadline = repository._connection.execute(
                "SELECT circuit_open_until FROM review_preparation_control"
            ).fetchone()[0]
            repository.close()
            if recovery < 2:
                self.assertFalse(pipeline.run_next())
                clock[0] = deadline
        self.assertFalse(pipeline.run_next())
        progress = pipeline.progress("primary", "ebooks")
        assert progress is not None
        self.assertEqual(
            (progress.state, progress.checked, progress.blocked),
            ("completed_with_failures", 1, 1),
        )
        self.assertEqual((len(extracted), provider_calls), (3, 3))

    def test_unrelated_provider_success_does_not_shorten_open_circuit(self) -> None:
        repository = open_repository(self.path)
        repository.request_review_scan(
            "primary", "fiction", "lib-1", _PRODUCER, 10
        )
        discovery = repository.claim_review_pipeline_action(11)
        assert discovery is not None
        repository.complete_review_discovery(
            discovery, 1, "a" * 64, (_entry("item-1", "b" * 64),), 12
        )
        extraction = repository.claim_review_pipeline_action(13)
        assert extraction is not None
        repository.advance_review_pipeline_action(extraction, None, 14)
        provider = repository.claim_review_pipeline_action(15)
        assert provider is not None
        repository._connection.execute(
            "UPDATE review_preparation_control SET circuit_failures=2,"
            "circuit_open_until=100 WHERE singleton=1"
        )
        repository.advance_review_pipeline_action(provider, None, 20)
        control = repository._connection.execute(
            "SELECT circuit_failures,circuit_open_until "
            "FROM review_preparation_control WHERE singleton=1"
        ).fetchone()
        repository.close()
        self.assertEqual(control, (2, 100))

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
                action, published.report_id, 15, 30
            ),
        )
        repository.close()

        reopened = open_repository(self.path)
        reopened.recover_review_pipeline(
            (("primary", "ebooks", "lib-1", _PRODUCER),)
        )
        resumed = reopened.claim_review_pipeline_action(16)
        assert resumed is not None
        circuit = reopened._connection.execute(
            "SELECT circuit_open_until FROM review_preparation_control"
        ).fetchone()
        reopened.close()
        self.assertEqual(resumed.stage, "provider")
        self.assertEqual(resumed.report_id, report.report_id)
        self.assertEqual(circuit, (45,))


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

    def review_pipeline_paused(self) -> bool:
        return False

    def set_review_pipeline_paused(self, paused: bool, updated_at: int) -> None:
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


class _ScheduledPipeline:
    libraries = (
        ("primary", "fiction", "lib-1"),
        ("primary", "non-fiction", "lib-2"),
    )

    def __init__(self) -> None:
        self.progresses = {}
        self.requests = []
        self.is_paused = False

    def paused(self):
        return self.is_paused

    def progress(self, connection, library):
        return self.progresses.get((connection, library))

    def request(self, connection, library):
        self.requests.append((connection, library))
        return PipelineProgress("review_scan_" + "a" * 64, connection, library, "queued", 0, 0, 0, 0, False)

    def set_paused(self, paused):
        self.is_paused = paused


class ReviewPipelineSchedulingTests(unittest.TestCase):
    def test_startup_interval_and_pause_admission_are_bounded(self) -> None:
        clock = [100]
        pipeline = _ScheduledPipeline()
        supervisor = ReviewPreparationSupervisor(
            pipeline, polling_interval_seconds=60, clock=lambda: clock[0]
        )
        supervisor._schedule_due()
        self.assertEqual(pipeline.requests, [("primary", "fiction"), ("primary", "non-fiction")])
        pipeline.requests.clear()
        for connection, library, _ in pipeline.libraries:
            pipeline.progresses[(connection, library)] = PipelineProgress(
                "review_scan_" + "b" * 64,
                connection,
                library,
                "completed",
                1,
                1,
                1,
                0,
                False,
                100,
            )
        clock[0] = 159
        supervisor._schedule_due()
        self.assertEqual(pipeline.requests, [])
        clock[0] = 160
        supervisor._schedule_due()
        self.assertEqual(len(pipeline.requests), 2)
        pipeline.requests.clear()
        pipeline.is_paused = True
        clock[0] = 220
        supervisor._schedule_due()
        self.assertEqual(pipeline.requests, [])

    def test_retry_is_limited_to_terminal_actionable_failure(self) -> None:
        pipeline = _ScheduledPipeline()
        supervisor = ReviewPreparationSupervisor(pipeline)
        pipeline.progresses[("primary", "fiction")] = PipelineProgress(
            "review_scan_" + "c" * 64,
            "primary",
            "fiction",
            "completed_with_failures",
            1,
            1,
            0,
            1,
            False,
            100,
        )
        supervisor.retry("primary", "fiction")
        self.assertEqual(pipeline.requests, [("primary", "fiction")])
        pipeline.progresses[("primary", "fiction")] = replace(
            pipeline.progresses[("primary", "fiction")], state="completed"
        )
        with self.assertRaisesRegex(ReviewPipelineError, "retry"):
            supervisor.retry("primary", "fiction")


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
                source_fingerprint="c" * 64,
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
                source_fingerprint="d" * 64,
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
