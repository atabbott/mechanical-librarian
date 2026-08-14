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
    CredentialError,
    MetadataReportError,
    TargetNotFoundError,
)
from mechanical_librarian.application.review_pipeline import (
    PipelineAction,
    PipelineProgress,
    ReviewPipelineError,
    ReviewPreparationPipeline,
    ReviewPreparationSupervisor,
)
from mechanical_librarian.application.review_web import BrowseReviewLibrary
from mechanical_librarian.application.operations import StageTimer
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    ProviderObservation,
    ProviderStatus,
)
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

    def test_status_reads_all_latest_library_progress_and_pause_in_one_snapshot(self) -> None:
        repository = open_repository(self.path)
        first = repository.request_review_scan(
            "primary", "fiction", "lib-1", _PRODUCER, 10
        )
        second = repository.request_review_scan(
            "primary", "history", "lib-2", _PRODUCER, 11
        )
        repository.set_review_pipeline_paused(True, 12)
        progress, paused = repository.review_pipeline_status(
            (("primary", "fiction"), ("primary", "history"), ("primary", "missing"))
        )
        repository.close()

        self.assertTrue(paused)
        self.assertEqual(progress, (first, second, None))
        repository = open_repository(self.path)
        with self.assertRaises(MetadataReportError):
            repository.review_pipeline_status((("x" * 65, "fiction"),))
        repository.close()

    def test_actual_provider_credential_failure_projects_once_at_provider_scope(self) -> None:
        repository = open_repository(self.path)
        repository.request_review_scan(
            "primary", "fiction", "lib-1", _PRODUCER, 10
        )
        discovery = repository.claim_review_pipeline_action(11)
        assert discovery is not None
        repository.complete_review_discovery(
            discovery, 1, "a" * 64, (_entry("item", "1" * 64),), 12
        )
        repository.close()
        pipeline = ReviewPreparationPipeline(
            repository_factory=lambda: open_repository(self.path),
            libraries=(("primary", "fiction", "lib-1", _PRODUCER),),
            discover=lambda *_: (_ for _ in ()).throw(AssertionError()),
            extract=lambda *_: (_ for _ in ()).throw(
                CredentialError("credential unavailable", provider="hardcover-main")
            ),
            providers=lambda *_: (_ for _ in ()).throw(AssertionError()),
            propose=lambda *_: (_ for _ in ()).throw(AssertionError()),
            admit=lambda *_: (_ for _ in ()).throw(AssertionError()),
            project=lambda *_: (_ for _ in ()).throw(AssertionError()),
            clock=lambda: 16,
        )
        self.assertTrue(pipeline.run_next())
        repository = open_repository(self.path)
        signals = repository.operational_snapshot()["signals"]
        repository.close()
        self.assertIn({
            "kind": "authentication_failure",
            "cause": "provider_authentication",
            "scope": "connection",
            "connection": "hardcover-main",
            "library": None,
            "category": None,
            "work_id": None,
            "field": None,
        }, signals)

    def test_observed_provider_authentication_remains_in_complete_report(self) -> None:
        base = _report()
        report = MetadataReport.create(
            target=base.target,
            file=base.file,
            local_claims=base.local_claims,
            local_reasons=base.local_reasons,
            providers=(
                ProviderObservation(
                    "google-main", ProviderStatus.AUTHORIZATION
                ),
                ProviderObservation(
                    "hardcover-main", ProviderStatus.AUTHENTICATION
                ),
            ),
        )
        entry = _entry("item", report.file.identity)
        repository = open_repository(self.path)
        repository.request_review_scan(
            "primary", "fiction", "lib-1", _PRODUCER, 10
        )
        discovery = repository.claim_review_pipeline_action(11)
        assert discovery is not None
        repository.complete_review_discovery(
            discovery, 1, "a" * 64, (entry,), 12
        )
        extraction = repository.claim_review_pipeline_action(13)
        assert extraction is not None
        repository.publish(
            report,
            lambda: True,
            None,
            lambda value, published: value.complete_review_extraction_publication(
                extraction,
                published.report_id,
                14,
            ),
        )
        work = repository._connection.execute(
            "SELECT stage,state,failure_code,failure_root "
            "FROM review_preparation_work"
        ).fetchone()
        retained = MetadataReport.from_json(repository.get(report.report_id))
        repository.close()
        self.assertEqual(
            work,
            ("proposal", "queued", None, None),
        )
        self.assertEqual(
            tuple((provider.provider, provider.status) for provider in retained.providers),
            (("google-main", ProviderStatus.AUTHORIZATION), ("hardcover-main", ProviderStatus.AUTHENTICATION)),
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

    def test_oversized_fairness_cursor_fails_closed(self) -> None:
        repository = open_repository(self.path)
        repository._connection.execute("PRAGMA ignore_check_constraints=ON")
        repository._connection.execute(
            "UPDATE review_preparation_control SET last_library=? WHERE singleton=1",
            ("x" * 65,),
        )
        repository._connection.commit()
        repository._connection.execute("PRAGMA ignore_check_constraints=OFF")

        with self.assertRaisesRegex(
            MetadataReportError, "review preparation control is invalid"
        ):
            repository.claim_review_pipeline_action(10)
        repository.close()

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

    def test_advance_atomically_claims_recoverable_next_action(self) -> None:
        repository = open_repository(self.path)
        report = _report()
        repository.publish(report, lambda: True)
        repository.request_review_scan(
            "primary", "ebooks", "lib-1", _PRODUCER, 10
        )
        discovery = repository.claim_review_pipeline_action(11)
        assert discovery is not None
        repository.complete_review_discovery(
            discovery,
            1,
            "a" * 64,
            (replace(_entry("item-1", report.file.identity), library="ebooks"),),
            12,
        )
        extraction = repository.claim_review_pipeline_action(13)
        assert extraction is not None
        provider = repository.advance_review_pipeline_action(
            extraction, report.report_id, 14, True
        )
        assert provider is not None
        self.assertEqual(provider.stage, "provider")
        repository.close()

        reopened = open_repository(self.path)
        reopened.recover_review_pipeline(
            (("primary", "ebooks", "lib-1", _PRODUCER),)
        )
        recovered = reopened.claim_review_pipeline_action(15)
        reopened.close()
        assert recovered is not None
        self.assertEqual(
            (recovered.action_id, recovered.stage, recovered.attempt),
            (provider.action_id, "provider", 2),
        )

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

    def test_repeated_queued_request_advances_its_freshness_anchor(self) -> None:
        repository = open_repository(self.path)
        first = repository.request_review_scan(
            "primary", "fiction", "lib-1", _PRODUCER, 10
        )

        repeated = repository.request_review_scan(
            "primary", "fiction", "lib-1", _PRODUCER, 11
        )

        self.assertEqual(repeated.scan_id, first.scan_id)
        self.assertEqual(repeated.state, "queued")
        self.assertEqual(repeated.updated_at, 11)
        timing = repository._connection.execute(
            "SELECT requested_at,updated_at FROM review_preparation_scans "
            "WHERE scan_id=?",
            (first.scan_id,),
        ).fetchone()
        repository.close()
        self.assertEqual(timing, (11, 11))

    def test_request_during_discovery_forces_a_successor_scan(self) -> None:
        repository = open_repository(self.path)
        repository.request_review_scan(
            "primary", "fiction", "lib-1", _PRODUCER, 10
        )
        discovery = repository.claim_review_pipeline_action(11)
        assert discovery is not None

        requested = repository.request_review_scan(
            "primary", "fiction", "lib-1", _PRODUCER, 12
        )

        self.assertEqual(requested.state, "discovering")
        self.assertTrue(requested.rescan_requested)
        self.assertEqual(requested.updated_at, 12)
        repository.complete_review_discovery(
            discovery, 1, "a" * 64, (), 13
        )
        processing = repository.review_pipeline_progress("primary", "fiction")
        assert processing is not None
        self.assertTrue(processing.rescan_requested)
        self.assertIsNone(repository.claim_review_pipeline_action(14))
        queued = repository.review_pipeline_progress("primary", "fiction")
        assert queued is not None
        self.assertEqual(queued.state, "queued")
        repository.close()

    def test_failed_discovery_preserves_the_requested_successor(self) -> None:
        repository = open_repository(self.path)
        repository.request_review_scan(
            "primary", "fiction", "lib-1", _PRODUCER, 10
        )
        discovery = repository.claim_review_pipeline_action(11)
        assert discovery is not None
        repository.request_review_scan(
            "primary", "fiction", "lib-1", _PRODUCER, 12
        )

        with patch.object(
            repository, "_has_pending_field_effect_manual_return",
            return_value=True,
        ):
            repository.fail_review_pipeline_action(
                discovery, "failed", False, 13
            )

        progress = repository.review_pipeline_progress("primary", "fiction")
        assert progress is not None
        self.assertEqual(progress.state, "queued")
        self.assertFalse(progress.rescan_requested)
        successor = repository.claim_review_pipeline_action(14)
        assert successor is not None
        self.assertEqual(successor.stage, "discovery")
        repository.close()

    def test_failed_processing_preserves_the_requested_successor(self) -> None:
        repository = open_repository(self.path)
        repository.request_review_scan(
            "primary", "fiction", "lib-1", _PRODUCER, 10
        )
        discovery = repository.claim_review_pipeline_action(11)
        assert discovery is not None
        repository.complete_review_discovery(
            discovery, 1, "a" * 64, (_entry("item-1", "1" * 64),), 12
        )
        work = repository.claim_review_pipeline_action(13)
        assert work is not None
        repository.request_review_scan(
            "primary", "fiction", "lib-1", _PRODUCER, 14
        )
        repository.fail_review_pipeline_action(work, "failed", False, 15)

        with patch.object(
            repository, "_has_pending_field_effect_manual_return",
            return_value=True,
        ):
            self.assertIsNone(repository.claim_review_pipeline_action(16))
        progress = repository.review_pipeline_progress("primary", "fiction")
        assert progress is not None
        self.assertEqual(progress.state, "queued")
        successor = repository.claim_review_pipeline_action(17)
        assert successor is not None
        self.assertEqual(successor.stage, "discovery")
        repository.close()

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
        self.assertEqual(repository._connection.execute("SELECT COUNT(*) FROM current_review_packages").fetchone(), (0,))
        self.assertEqual(repository.count_current_review_cases(None), (0, 0))
        self.assertEqual(repository.read_review_attention(None, 10, 0).total, 0)
        repository.close()

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
        package_count = repository._connection.execute(
            "SELECT COUNT(*) FROM current_review_packages"
        ).fetchone()
        library_count = repository.count_current_review_cases(None)
        attention_count = repository.read_review_attention(None, 10, 0).total
        reopened = json.loads(repository.admit_review_case(proposal))
        repository.close()

        self.assertEqual(pointer, (None, "retired"))
        self.assertEqual(
            transition, ("review-preparation-v1", "retired", "ineligible")
        )
        self.assertEqual(package_count, (0,))
        self.assertEqual(library_count, (0, 0))
        self.assertEqual(attention_count, 0)
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
            extract=lambda *_: (_ for _ in ()).throw(AssertionError()),
            providers=lambda *_: (),
            propose=lambda *_: (_ for _ in ()).throw(AssertionError()),
            admit=lambda *_: (_ for _ in ()).throw(AssertionError()),
            project=lambda *_: True,
            clock=lambda: 20,
        )
        pipeline.request("primary", "ebooks")
        self.assertTrue(pipeline.run_next(1))
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

    def test_manual_return_successor_discards_failed_scan_absence_candidates(self) -> None:
        repository = open_repository(self.path)
        report = _report()
        repository.publish(report, lambda: True)
        proposal = evaluate_report(report)
        repository.publish_proposal(proposal)
        admitted = json.loads(repository.admit_review_case(proposal))
        repository.request_review_scan(
            report.target.connection, report.target.library,
            "lib-1", _PRODUCER, 10,
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
        repository.request_review_scan(
            report.target.connection, report.target.library,
            "lib-1", _PRODUCER, 14,
        )
        repository.fail_review_pipeline_action(work, "failed", False, 15)

        with patch.object(
            repository, "_has_pending_field_effect_manual_return",
            return_value=True,
        ):
            self.assertIsNone(repository.claim_review_pipeline_action(16))

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
        self.assertEqual(progress.state, "queued")

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

        def publish_report(repository, __: PipelineAction, ___: bool):
            extracted.append(report.report_id)
            repository.publish(report, lambda: True)
            return report.report_id, False

        def publish_proposal(repository, __: str) -> str:
            repository.publish_proposal(proposal)
            return proposal.proposal_set_id

        def admit(repository, __: str) -> tuple[str, str | None]:
            result = json.loads(repository.admit_review_case(proposal))
            return result["status"], result.get("review_case_id")

        pipeline = ReviewPreparationPipeline(
            repository_factory=lambda: open_repository(self.path),
            libraries=(("primary", "ebooks", "lib-1", _PRODUCER),),
            discover=lambda *_: (1, "d" * 64, tuple(entries)),
            extract=publish_report,
            providers=lambda *_: (ProviderStatus.DISABLED,),
            propose=publish_proposal,
            admit=admit,
            project=lambda _, action: (action.review_case_id or "").startswith("review_case_"),
            clock=lambda: 20,
        )
        pipeline.request("primary", "ebooks")
        self.assertEqual(sum(pipeline.run_next(1) for _ in range(6)), 6)
        self.assertFalse(pipeline.run_next(1))
        progress = pipeline.progress("primary", "ebooks")
        assert progress is not None
        self.assertEqual(
            (progress.state, progress.checked, progress.total, progress.ready),
            ("completed", 1, 1, 1),
        )
        self.assertEqual(len(extracted), 1)

        pipeline.request("primary", "ebooks")
        self.assertTrue(pipeline.run_next(1))
        self.assertFalse(pipeline.run_next(1))
        self.assertEqual(len(extracted), 1)

        entries[0] = replace(
            entries[0], updated_at=2, source_fingerprint="9" * 64
        )
        pipeline.request("primary", "ebooks")
        self.assertTrue(pipeline.run_next(1))
        self.assertTrue(pipeline.run_next(1))
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

    def test_terminal_provider_failure_continues_to_proposal(self) -> None:
        proposed: list[str] = []
        extracted: list[str] = []
        clock = [30]
        provider_statuses = [
            (ProviderStatus.CANDIDATES, ProviderStatus.INCOMPLETE),
            (ProviderStatus.CANDIDATES,),
        ]
        report = _report()

        def publish_report(repository, __: PipelineAction, ___: bool) -> str:
            extracted.append(report.report_id)
            repository.publish(report, lambda: True)
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
            providers=lambda *_: provider_statuses.pop(0),
            propose=lambda _, report_id: proposed.append(report_id) or "unreachable",
            admit=lambda *_: ("no_case", None),
            project=lambda *_: True,
            clock=lambda: clock[0],
        )
        pipeline.request("primary", "ebooks")
        self.assertTrue(pipeline.run_next(1))
        self.assertTrue(pipeline.run_next(1))
        self.assertTrue(pipeline.run_next(1))
        self.assertTrue(pipeline.run_next(1))
        self.assertEqual(proposed, [report.report_id])
        repository = open_repository(self.path)
        control = repository._connection.execute(
            "SELECT circuit_failures,circuit_open_until FROM review_preparation_control"
        ).fetchone()
        repository.close()
        self.assertEqual(control, (0, 0))

    def test_terminal_unavailable_provider_report_continues_to_proposal(
        self,
    ) -> None:
        report = _report()
        clock = [30]
        extracted: list[str] = []
        provider_calls = 0
        proposed: list[str] = []

        def publish_report(
            repository, __: PipelineAction, ___: bool
        ) -> tuple[str, bool]:
            extracted.append(report.report_id)
            repository.publish(report, lambda: True)
            return report.report_id, False

        def unavailable(_, __: str) -> tuple[ProviderStatus, ...]:
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
            propose=lambda _, report_id: proposed.append(report_id) or "proposal-set",
            admit=lambda *_: (_ for _ in ()).throw(AssertionError()),
            project=lambda *_: True,
            clock=lambda: clock[0],
        )
        pipeline.request("primary", "ebooks")
        self.assertTrue(pipeline.run_next(1))
        self.assertTrue(pipeline.run_next(1))
        self.assertTrue(pipeline.run_next(1))
        self.assertTrue(pipeline.run_next(1))
        self.assertEqual(proposed, [report.report_id])
        self.assertEqual((len(extracted), provider_calls), (1, 1))

    def test_first_generation_and_refresh_publish_only_after_projection(self) -> None:
        reports = [
            _report(title="Published old", content_digest="a", file_identity="b"),
            _report(title="Staged successor", content_digest="c", file_identity="d"),
            _report(title="Failed successor", content_digest="e", file_identity="f"),
            _report(title="Recovered successor", content_digest="1", file_identity="2"),
            _report(title="Absent successor", content_digest="3", file_identity="4"),
            _report(title="Retired successor", content_digest="5", file_identity="6"),
        ]
        repository = open_repository(self.path)
        repository.enter_shadow_preview(1)
        repository.close()

        def pipeline_for(
            report: MetadataReport,
            *,
            projection: bool = True,
            now: int = 20,
            database: Path | None = None,
        ) -> ReviewPreparationPipeline:
            proposal = evaluate_report(report)
            selected_database = self.path if database is None else database

            def extract(repository, action: PipelineAction, claim_next: bool):
                repository.publish(
                    report,
                    lambda: True,
                    after_publication=lambda bound, current: bound.complete_review_extraction_publication(
                        action, current.report_id, now, 0, None, claim_next, None
                    ),
                )
                return report.report_id, True

            def propose(repository, _: str) -> str:
                repository.publish_proposal(proposal)
                return proposal.proposal_set_id

            def admit(repository, _: str) -> tuple[str, str | None]:
                value = json.loads(repository.admit_review_case(proposal))
                return value["status"], value.get("review_case_id")

            def project(repository, action: PipelineAction) -> str:
                if not projection:
                    raise RuntimeError("synthetic projection failure")
                outcome = repository.persist_shadow_outcome(
                    action.review_case_id or "", now, staged_action=action
                )
                return "stale" if outcome == "stale" else "complete"

            return ReviewPreparationPipeline(
                repository_factory=lambda: open_repository(selected_database),
                libraries=(("primary", "ebooks", "lib-1", _PRODUCER),),
                discover=lambda *_: (1, report.file.content_digest, (replace(_entry("item-1", report.file.identity), library="ebooks"),)),
                extract=extract,
                providers=lambda *_: (),
                propose=propose,
                admit=admit,
                project=project,
                clock=lambda: now,
            )

        def titles() -> tuple[str, ...]:
            repository = open_repository(self.path)
            rows = repository.read_review_library(None, 10, 0, None).rows
            repository.close()
            return tuple(json.loads(row[1])["target"]["fields"][0]["value"] for row in rows)

        first = pipeline_for(reports[0])
        first.request("primary", "ebooks")
        self.assertEqual(titles(), ())
        for _ in range(5):
            self.assertTrue(first.run_next(1))
            if _ < 4:
                self.assertEqual(titles(), ())
        self.assertEqual(titles(), ("Published old",))
        repository = open_repository(self.path)
        old_case = repository._connection.execute("SELECT review_case_id FROM current_review_packages").fetchone()[0]
        self.assertTrue(json.loads(repository.get_review_case(old_case))["current"])
        repository.close()
        self.assertFalse(first.run_next(1))

        refresh = pipeline_for(reports[1], now=30)
        refresh.request("primary", "ebooks")
        for _ in range(5):
            self.assertTrue(refresh.run_next(1))
            if _ < 4:
                self.assertEqual(titles(), ("Published old",))
                repository = open_repository(self.path)
                self.assertTrue(json.loads(repository.get_review_case(old_case))["current"])
                staged = repository._connection.execute(
                    "SELECT review_case_id FROM current_review_case_lineages"
                ).fetchone()
                if staged is not None and staged[0] not in {None, old_case}:
                    self.assertFalse(
                        json.loads(repository.get_review_case(staged[0]))["current"]
                    )
                repository.close()
        self.assertEqual(titles(), ("Staged successor",))
        repository = open_repository(self.path)
        self.assertFalse(json.loads(repository.get_review_case(old_case))["current"])
        repository.close()
        self.assertFalse(refresh.run_next(1))

        failed_refresh = pipeline_for(reports[2], projection=False, now=40)
        failed_refresh.request("primary", "ebooks")
        for _ in range(5):
            self.assertTrue(failed_refresh.run_next(1))
            self.assertEqual(titles(), ("Staged successor",))
        self.assertFalse(failed_refresh.run_next(1))
        repository = open_repository(self.path)
        staged_case = repository._connection.execute(
            "SELECT review_case_id FROM current_review_case_lineages"
        ).fetchone()[0]
        published_case = repository._connection.execute(
            "SELECT review_case_id FROM current_review_packages"
        ).fetchone()[0]
        self.assertNotEqual(staged_case, published_case)
        self.assertTrue(json.loads(repository.get_review_case(published_case))["current"])
        self.assertFalse(json.loads(repository.get_review_case(staged_case))["current"])
        prior_attention = repository.read_review_attention(None, 10, 0).total
        repository.close()

        reopened = open_repository(self.path)
        page = BrowseReviewLibrary(reopened).library(
            lens="all", search="Staged successor", limit=1, cursor=None
        )
        self.assertEqual(page["counts"]["all"], 1)
        self.assertIsNone(page["next_cursor"])
        self.assertEqual(len(page["items"]), 1)
        self.assertIsNotNone(page["items"][0]["preparation"])
        self.assertEqual(
            reopened.read_review_attention(None, 10, 0).total,
            prior_attention,
        )
        reopened.close()

        reused_report = open_repository(self.path)
        self.assertEqual(
            reused_report._connection.execute(
                "UPDATE review_preparation_work SET report_id=? WHERE state='failed'",
                (reports[1].report_id,),
            ).rowcount,
            1,
        )
        reused_report_page = BrowseReviewLibrary(reused_report).library(
            lens="all", search="Staged successor", limit=1, cursor=None
        )
        self.assertEqual(reused_report_page["counts"]["all"], 1)
        self.assertIsNotNone(reused_report_page["items"][0]["preparation"])
        self.assertEqual(
            reused_report._connection.execute(
                "SELECT report_id FROM current_review_packages"
            ).fetchone(),
            (reports[1].report_id,),
        )
        self.assertEqual(
            reused_report._connection.execute(
                "UPDATE review_preparation_work SET report_id=? WHERE state='failed'",
                (reports[2].report_id,),
            ).rowcount,
            1,
        )
        reused_report.close()

        recovery = pipeline_for(reports[3], now=50)
        recovery.request("primary", "ebooks")
        recovery_steps = 0
        while recovery.run_next(1):
            recovery_steps += 1
            self.assertLessEqual(recovery_steps, 5)
        self.assertGreater(recovery_steps, 0)
        recovered = open_repository(self.path)
        recovered_page = BrowseReviewLibrary(recovered).library(
            lens="all", search="Recovered successor", limit=1, cursor=None
        )
        recovered_case = recovered._connection.execute(
            "SELECT review_case_id FROM current_review_packages"
        ).fetchone()[0]
        self.assertIsNone(recovered_page["items"][0]["preparation"])
        self.assertFalse(json.loads(recovered.get_review_case(published_case))["current"])
        self.assertTrue(json.loads(recovered.get_review_case(recovered_case))["current"])
        recovered.close()

        def fail_successor(report: MetadataReport, now: int) -> None:
            pipeline = pipeline_for(report, projection=False, now=now)
            pipeline.request("primary", "ebooks")
            for _ in range(5):
                self.assertTrue(pipeline.run_next(1))
            self.assertFalse(pipeline.run_next(1))

        def confirm_removal(
            entries: tuple[CatalogueEntry, ...],
            now: int,
            database: Path | None = None,
        ) -> None:
            repository = open_repository(self.path if database is None else database)
            repository.request_review_scan(
                "primary", "ebooks", "lib-1", _PRODUCER, now
            )
            discovery = repository.claim_review_pipeline_action(now + 1)
            assert discovery is not None
            repository.complete_review_discovery(
                discovery, 1, "9" * 64, entries, now + 2
            )
            self.assertIsNone(repository.claim_review_pipeline_action(now + 3))
            confirmation = repository.claim_review_pipeline_action(now + 4)
            assert confirmation is not None
            repository.complete_review_discovery(
                confirmation, 2, "9" * 64, entries, now + 5
            )
            self.assertIsNone(repository.claim_review_pipeline_action(now + 6))
            self.assertEqual(
                repository._connection.execute(
                    "SELECT COUNT(*) FROM current_review_packages"
                ).fetchone(),
                (0,),
            )
            self.assertEqual(repository.count_current_review_cases(None), (0, 0))
            repository.close()

        fail_successor(reports[4], 60)
        confirm_removal((), 70)

        repository = open_repository(self.path)
        recovered_proposal = evaluate_report(reports[3])
        repository.publish(reports[3], lambda: True)
        repository.publish_proposal(recovered_proposal)
        repository.admit_review_case(recovered_proposal)
        repository.close()
        fail_successor(reports[5], 80)
        ineligible = replace(
            _entry(reports[5].target.item, reports[5].file.identity),
            library="ebooks",
            classification="singular-unmounted",
            mount=None,
        )
        confirm_removal((ineligible,), 90)

        for index, entries in enumerate(
            (
                (),
                (
                    replace(
                        _entry(reports[4].target.item, reports[4].file.identity),
                        library="ebooks",
                        classification="singular-unmounted",
                        mount=None,
                    ),
                ),
            )
        ):
            with self.subTest(first_generation_removal=index):
                database = Path(self.temporary.name) / f"first-{index}.sqlite3"
                open_repository(database).close()
                first_failure = pipeline_for(
                    reports[4],
                    projection=False,
                    now=100 + index * 20,
                    database=database,
                )
                first_failure.request("primary", "ebooks")
                for _ in range(5):
                    self.assertTrue(first_failure.run_next(1))
                self.assertFalse(first_failure.run_next(1))
                confirm_removal(entries, 110 + index * 20, database)

    def test_first_generation_real_shadow_projection_precedes_package_cutover(self) -> None:
        report = _report(title="First staged projection", content_digest="a", file_identity="b")
        proposal = evaluate_report(report)

        def extract(repository, action: PipelineAction, claim_next: bool):
            repository.publish(
                report,
                lambda: True,
                after_publication=lambda bound, current: bound.complete_review_extraction_publication(
                    action, current.report_id, 10, 0, None, claim_next, None
                ),
            )
            return report.report_id, True

        def project(repository, action: PipelineAction) -> str:
            result = repository.persist_shadow_outcome(
                action.review_case_id or "", 10, staged_action=action
            )
            observer = sqlite3.connect(self.path)
            try:
                self.assertEqual((0,), observer.execute(
                    "SELECT COUNT(*) FROM shadow_decision_outcomes"
                ).fetchone())
                self.assertEqual((0, 0), observer.execute(
                    "SELECT (SELECT COUNT(*) FROM current_review_packages),"
                    "(SELECT COUNT(*) FROM automatic_decisions)"
                ).fetchone())
            finally:
                observer.close()
            return "complete" if result != "stale" else "stale"

        pipeline = ReviewPreparationPipeline(
            repository_factory=lambda: open_repository(self.path),
            libraries=(("primary", "ebooks", "lib-1", _PRODUCER),),
            discover=lambda *_: (1, report.file.content_digest, (replace(_entry("item-1", report.file.identity), library="ebooks"),)),
            extract=extract,
            providers=lambda *_: (),
            propose=lambda repository, _: (repository.publish_proposal(proposal), proposal.proposal_set_id)[1],
            admit=lambda repository, _: (lambda value: (value["status"], value.get("review_case_id")))(json.loads(repository.admit_review_case(proposal))),
            project=project,
            clock=lambda: 10,
        )
        pipeline.request("primary", "ebooks")
        # Admission owns the staged lineage, but package publication remains
        # gated on the real projection transaction.  In particular, automatic
        # decision-only handling must not make the staged case visible early.
        for _ in range(4):
            self.assertTrue(pipeline.run_next(1))
        repository = open_repository(self.path)
        self.assertEqual(
            ("projection", "queued"),
            repository._connection.execute(
                "SELECT stage,state FROM review_preparation_work"
            ).fetchone(),
        )
        self.assertEqual((0,), repository._connection.execute(
            "SELECT COUNT(*) FROM current_review_packages"
        ).fetchone())
        self.assertEqual((0,), repository._connection.execute(
            "SELECT COUNT(*) FROM automatic_decisions"
        ).fetchone())
        staged_case = repository._connection.execute(
            "SELECT review_case_id FROM current_review_case_lineages"
        ).fetchone()[0]
        self.assertFalse(json.loads(repository.get_review_case(staged_case))["current"])
        self.assertFalse(repository._review_case_accepts_authority(staged_case))
        repository.close()

        self.assertTrue(pipeline.run_next(1))
        repository = open_repository(self.path)
        self.assertEqual(("complete",), repository._connection.execute("SELECT state FROM review_preparation_work").fetchone())
        self.assertEqual((1,), repository._connection.execute("SELECT COUNT(*) FROM current_review_packages").fetchone())
        case_id = repository._connection.execute(
            "SELECT review_case_id FROM current_review_packages"
        ).fetchone()[0]
        repository.close()
        # A restarted worker replays the same immutable outcome and never
        # manufactures another decision, package checkpoint, or completion.
        restarted = open_repository(self.path)
        restarted.enter_shadow_preview(11)
        replay = restarted.persist_shadow_outcome(case_id, 11)
        self.assertTrue(replay.startswith('{"authority":"none"'))
        self.assertEqual((1, 1, 1), restarted._connection.execute(
            "SELECT (SELECT COUNT(*) FROM shadow_decision_outcomes),"
            "(SELECT COUNT(*) FROM current_review_packages),"
            "(SELECT COUNT(*) FROM review_preparation_work WHERE state='complete')"
        ).fetchone())
        restarted.close()

    def test_projection_crash_rolls_back_staged_shadow_outcome_before_requeue(self) -> None:
        report = _report(title="Rollback staged projection", content_digest="c", file_identity="d")
        proposal = evaluate_report(report)

        def extract(repository, action: PipelineAction, claim_next: bool):
            repository.publish(
                report,
                lambda: True,
                after_publication=lambda bound, current: bound.complete_review_extraction_publication(
                    action, current.report_id, 10, 0, None, claim_next, None
                ),
            )
            return report.report_id, True

        def project(repository, action: PipelineAction) -> str:
            self.assertNotEqual("stale", repository.persist_shadow_outcome(
                action.review_case_id or "", 10, staged_action=action
            ))
            observer = sqlite3.connect(self.path)
            try:
                self.assertEqual((0,), observer.execute(
                    "SELECT COUNT(*) FROM shadow_decision_outcomes"
                ).fetchone())
                self.assertEqual((0, 0), observer.execute(
                    "SELECT (SELECT COUNT(*) FROM current_review_packages),"
                    "(SELECT COUNT(*) FROM automatic_decisions)"
                ).fetchone())
            finally:
                observer.close()
            raise RuntimeError("injected projection crash")

        pipeline = ReviewPreparationPipeline(
            repository_factory=lambda: open_repository(self.path),
            libraries=(("primary", "ebooks", "lib-1", _PRODUCER),),
            discover=lambda *_: (1, report.file.content_digest, (
                replace(_entry("item-1", report.file.identity), library="ebooks"),
            )),
            extract=extract,
            providers=lambda *_: (),
            propose=lambda repository, _: (
                repository.publish_proposal(proposal), proposal.proposal_set_id
            )[1],
            admit=lambda repository, _: (lambda value: (
                value["status"], value.get("review_case_id")
            ))(json.loads(repository.admit_review_case(proposal))),
            project=project,
            clock=lambda: 10,
        )
        pipeline.request("primary", "ebooks")
        for _ in range(5):
            self.assertTrue(pipeline.run_next(1))

        observer = open_repository(self.path)
        try:
            self.assertEqual((), observer.list_shadow_outcomes(50))
            self.assertEqual((0, 0), observer._connection.execute(
                "SELECT (SELECT COUNT(*) FROM current_review_packages),"
                "(SELECT COUNT(*) FROM automatic_decisions)"
            ).fetchone())
            self.assertEqual(("failed", "failed"), observer._connection.execute(
                "SELECT state,failure_code FROM review_preparation_work"
            ).fetchone())
        finally:
            observer.close()

    def test_real_projection_stale_lane_does_not_block_mixed_fifty_work_cohort(self) -> None:
        reports: dict[str, MetadataReport] = {}
        report_for_item: dict[str, MetadataReport] = {}
        proposals = {}
        entries = []
        for index in range(50):
            base = _report(
                title=f"Synthetic {index}",
                content_digest=f"{index % 16:x}",
                file_identity=f"{(index + 5) % 16:x}",
                actionable=index % 2 == 0,
            )
            report = MetadataReport.create(
                target=replace(base.target, item=f"item-{index}"),
                file=base.file,
                local_claims=base.local_claims,
                local_reasons=base.local_reasons,
                providers=base.providers,
            )
            reports[report.report_id] = report
            report_for_item[f"item-{index}"] = report
            entries.append(replace(
                _entry(f"item-{index}", report.file.identity), library="ebooks"
            ))
        first = report_for_item["item-0"]
        replacement = MetadataReport.create(
            target=first.target,
            file=replace(first.file, content_digest="f" * 64),
            local_claims=first.local_claims,
            local_reasons=first.local_reasons,
            providers=first.providers,
        )
        projected: list[str] = []
        projection_outcomes: list[str] = []
        stale_projection_items: list[str] = []

        def extract(repository, action: PipelineAction, _: bool):
            report = report_for_item[action.item_id or ""]
            repository.publish(report, lambda: True)
            return report.report_id, False

        def propose(repository, report_id: str) -> str:
            report = reports[report_id]
            proposal = evaluate_report(report)
            proposals[proposal.proposal_set_id] = proposal
            repository.publish_proposal(proposal)
            return proposal.proposal_set_id

        def admit(repository, proposal_id: str) -> tuple[str, str | None]:
            value = json.loads(repository.admit_review_case(proposals[proposal_id]))
            return value["status"], value.get("review_case_id")

        def project(repository, action: PipelineAction) -> str:
            projected.append(action.item_id or "")
            outcome = repository.persist_shadow_outcome(
                action.review_case_id or "", 20, staged_action=action
            )
            projection_outcomes.append(outcome)
            if outcome == "stale":
                stale_projection_items.append(action.item_id or "")
            return "stale" if outcome == "stale" else "complete"

        repository = open_repository(self.path)
        repository.enter_shadow_preview(1)
        repository.close()
        pipeline = ReviewPreparationPipeline(
            repository_factory=lambda: open_repository(self.path),
            libraries=(("primary", "ebooks", "lib-1", _PRODUCER),),
            discover=lambda *_: (1, "a" * 64, tuple(entries)), extract=extract,
            providers=lambda *_: (), propose=propose, admit=admit, project=project,
            clock=lambda: 20,
        )
        pipeline.request("primary", "ebooks")
        for _ in range(512):
            repository = open_repository(self.path)
            state = repository._connection.execute(
                "SELECT stage,state,report_id FROM review_preparation_work "
                "WHERE item_id='item-0'"
            ).fetchone()
            repository.close()
            if state == ("projection", "queued", first.report_id):
                break
            self.assertTrue(pipeline.run_next(1))
        else:
            self.fail("item-0 never reached staged projection")
        # A supported foreground refresh wins the lineage immediately before
        # the worker claims projection.  The worker must discover that exact
        # currentness drift through real staged shadow persistence, not a test
        # projector shortcut.
        repository = open_repository(self.path)
        repository.publish(
            replacement,
            lambda: True,
            first.report_id,
            after_publication=lambda *_: None,
        )
        repository.close()
        while pipeline.run_next(1):
            pass
        repository = open_repository(self.path)
        states = repository._connection.execute(
            "SELECT item_id,state,failure_code FROM review_preparation_work ORDER BY item_id"
        ).fetchall()
        repository.close()
        self.assertIn("stale", projection_outcomes)
        self.assertEqual({"item-0"}, set(stale_projection_items))
        self.assertIn("item-2", projected)
        self.assertEqual(50, len(states))
        self.assertTrue(
            all(state in {"complete", "failed", "stale"} for _, state, _ in states)
        )
        self.assertEqual(("complete", None), {
            item_id: (state, failure_code) for item_id, state, failure_code in states
        }["item-2"])
        self.assertEqual(1, [
            (state, failure_code) for _, state, failure_code in states
        ].count(("stale", "stale")))

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

    def test_terminal_provider_failure_does_not_open_shared_circuit(self) -> None:
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
        repository.fail_review_pipeline_action(provider, "provider", False, 16)
        control = repository._connection.execute(
            "SELECT circuit_failures,circuit_open_until "
            "FROM review_preparation_control WHERE singleton=1"
        ).fetchone()
        work = repository._connection.execute(
            "SELECT state,failure_code FROM review_preparation_work"
        ).fetchone()
        repository.close()
        self.assertEqual(control, (0, 0))
        self.assertEqual(work, ("failed", "provider"))

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
            extract=lambda *_: (_ for _ in ()).throw(
                ReviewPipelineError("stale")
            ),
            providers=lambda *_: (),
            propose=lambda *_: (_ for _ in ()).throw(AssertionError()),
            admit=lambda *_: (_ for _ in ()).throw(AssertionError()),
            project=lambda *_: True,
            clock=lambda: 30,
        )
        pipeline.request("primary", "ebooks")
        self.assertTrue(pipeline.run_next(1))
        self.assertEqual(sum(pipeline.run_next(1) for _ in range(3)), 3)

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
        retry_not_before = reopened._connection.execute(
            "SELECT retry_not_before FROM review_preparation_work"
        ).fetchone()
        reopened.close()
        self.assertEqual(resumed.stage, "proposal")
        self.assertEqual(resumed.report_id, report.report_id)
        self.assertEqual(retry_not_before, (0,))


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

    def begin_review_pipeline_output(self):
        pass

    def take_review_pipeline_claim(self):
        return None

    def rollback_review_pipeline_output(self):
        pass

    def advance_review_pipeline_action(self, action, output_id, completed_at, claim_next=False):
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
        self.status_calls = 0

    def paused(self):
        return self.is_paused

    def progress(self, connection, library):
        return self.progresses.get((connection, library))

    def status(self):
        self.status_calls += 1
        return (
            tuple(
                self.progresses.get((connection, library))
                for connection, library, _ in self.libraries
            ),
            self.is_paused,
        )

    def request(self, connection, library):
        self.requests.append((connection, library))
        return PipelineProgress("review_scan_" + "a" * 64, connection, library, "queued", 0, 0, 0, 0, False)

    def set_paused(self, paused):
        self.is_paused = paused


class ProviderRecoveryPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "reports.sqlite3"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_recovery_successor_uses_normal_projection_and_atomic_package_cutover(self) -> None:
        predecessor = _report(
            title="Visible predecessor", content_digest="a", file_identity="b"
        )
        successor = _report(
            title="Recovered successor", content_digest="a", file_identity="b"
        )
        repository = open_repository(self.path)
        repository.enter_shadow_preview(1)
        repository.close()

        def pipeline_for(report: MetadataReport, now: int) -> ReviewPreparationPipeline:
            proposal = evaluate_report(report)

            def extract(bound, action: PipelineAction, claim_next: bool):
                bound.publish(
                    report, lambda: True,
                    after_publication=lambda current, published: current.complete_review_extraction_publication(
                        action, published.report_id, now, 0, None, claim_next, None
                    ),
                )
                return report.report_id, True

            def propose(bound, _: str) -> str:
                bound.publish_proposal(proposal)
                return proposal.proposal_set_id

            def admit(bound, _: str) -> tuple[str, str | None]:
                value = json.loads(bound.admit_review_case(proposal))
                return value["status"], value.get("review_case_id")

            def project(bound, action: PipelineAction) -> str:
                outcome = bound.persist_shadow_outcome(
                    action.review_case_id or "", now, staged_action=action
                )
                return "stale" if outcome == "stale" else "complete"

            return ReviewPreparationPipeline(
                repository_factory=lambda: open_repository(self.path),
                libraries=(("primary", "ebooks", "lib-1", _PRODUCER),),
                discover=lambda *_: (
                    1, report.file.content_digest,
                    (replace(_entry("item-1", report.file.identity), library="ebooks"),),
                ),
                extract=extract, providers=lambda *_: (), propose=propose,
                admit=admit, project=project, clock=lambda: now,
            )

        initial = pipeline_for(predecessor, 20)
        initial.request("primary", "ebooks")
        for _ in range(5):
            self.assertTrue(initial.run_next(1))
        repository = open_repository(self.path)
        predecessor_package = repository.current_review_package(
            "primary", "ebooks", "item-1"
        )
        self.assertIsNotNone(predecessor_package)
        current_document = repository.current_optional(
            "primary", "ebooks", "item-1", predecessor.file.identity
        )
        self.assertIsNotNone(current_document)
        assert current_document is not None
        expected_current = MetadataReport.from_json(current_document).report_id
        self.assertEqual(predecessor.report_id, expected_current)
        repository.publish(
            successor, lambda: True, expected_current,
            after_publication=lambda bound, report: bound.stage_provider_retry_successor(
                predecessor.report_id, report, 30
            ),
            expected_package=predecessor_package,
        )
        self.assertEqual(
            predecessor_package,
            repository.current_review_package("primary", "ebooks", "item-1"),
        )
        repository.close()

        recovery = pipeline_for(successor, 30)
        self.assertTrue(recovery.run_recovery_successor(successor.report_id))
        repository = open_repository(self.path)
        current = repository.current_review_package("primary", "ebooks", "item-1")
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(successor.report_id, current[1])
        self.assertNotEqual(predecessor_package, current)
        repository.close()


class ReviewPipelineSchedulingTests(unittest.TestCase):
    def test_startup_interval_and_pause_admission_are_bounded(self) -> None:
        clock = [100]
        pipeline = _ScheduledPipeline()
        supervisor = ReviewPreparationSupervisor(
            pipeline, polling_interval_seconds=60, clock=lambda: clock[0]
        )
        supervisor._schedule_due()
        self.assertEqual(pipeline.requests, [("primary", "fiction"), ("primary", "non-fiction")])
        self.assertEqual(pipeline.status_calls, 1)
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
        self.assertEqual(pipeline.status_calls, 2)
        supervisor._schedule_due()
        self.assertEqual(pipeline.status_calls, 2)
        clock[0] = 160
        supervisor._schedule_due()
        self.assertEqual(len(pipeline.requests), 2)
        self.assertEqual(pipeline.status_calls, 3)
        pipeline.requests.clear()
        pipeline.is_paused = True
        clock[0] = 220
        supervisor._schedule_due()
        self.assertEqual(pipeline.requests, [])
        self.assertEqual(pipeline.status_calls, 4)
        supervisor._schedule_due()
        clock[0] = 1_000_000
        supervisor._schedule_due()
        self.assertEqual(pipeline.status_calls, 4)

    def test_terminal_failures_wait_for_owner_action_without_idle_status_reads(
        self,
    ) -> None:
        clock = [100]
        pipeline = _ScheduledPipeline()
        for connection, library, _ in pipeline.libraries:
            pipeline.progresses[(connection, library)] = PipelineProgress(
                "review_scan_" + "c" * 64,
                connection,
                library,
                "completed_with_failures",
                1,
                1,
                0,
                1,
                False,
                100,
            )
        supervisor = ReviewPreparationSupervisor(
            pipeline, polling_interval_seconds=60, clock=lambda: clock[0]
        )

        supervisor._schedule_due()
        clock[0] = 1_000_000
        supervisor._schedule_due()
        supervisor._schedule_due()
        self.assertEqual(pipeline.status_calls, 1)

        supervisor.retry("primary", "fiction")
        supervisor._schedule_due()
        self.assertEqual(pipeline.status_calls, 2)

    def test_lane_zero_rechecks_due_deadline_between_bounded_work_batches(
        self,
    ) -> None:
        clock = [159]
        pipeline = _ScheduledPipeline()
        for connection, library, _ in pipeline.libraries:
            pipeline.progresses[(connection, library)] = PipelineProgress(
                "review_scan_" + "d" * 64,
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
        supervisor = ReviewPreparationSupervisor(
            pipeline, polling_interval_seconds=60, clock=lambda: clock[0]
        )
        supervisor._schedule_due()
        self.assertEqual(pipeline.requests, [])

        def run_next() -> bool:
            clock[0] = 160
            supervisor._stop.set()
            return True

        pipeline.run_next = run_next  # type: ignore[attr-defined]
        supervisor._wake.set()
        supervisor._run(0)
        self.assertEqual(
            pipeline.requests,
            [("primary", "fiction"), ("primary", "non-fiction")],
        )

    def test_lane_zero_wakes_recovery_with_the_supervisor_clock(self) -> None:
        clock = [41]
        pipeline = _ScheduledPipeline()
        woken: list[int] = []
        supervisor = ReviewPreparationSupervisor(
            pipeline, clock=lambda: clock[0], recovery_wake=woken.append
        )
        def run_next() -> bool:
            supervisor._stop.set()
            return False
        pipeline.run_next = run_next  # type: ignore[attr-defined]
        supervisor._wake.set()
        supervisor._run(0)
        self.assertEqual([41], woken)

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
    def test_preparation_stages_emit_exact_advisory_timing_classes(self) -> None:
        class Timings:
            def __init__(self):
                self.values = []
            def append(self, timing):
                self.values.append(timing)
            def list(self):
                return tuple(self.values)

        def action(stage: str, **values) -> PipelineAction:
            return PipelineAction(
                "review_work_" + stage[0] * 64,
                "review_scan_" + "a" * 64,
                stage,
                1,
                "primary",
                "fiction",
                "lib-1",
                _PRODUCER,
                1,
                "b" * 64,
                "item",
                "1" * 64,
                1,
                source_fingerprint="c" * 64,
                **values,
            )

        timings = Timings()
        ticks = iter(range(0, 100_000_000, 1_000_000))
        repository = _ConcurrentRepository([])
        pipeline = ReviewPreparationPipeline(
            repository_factory=lambda: repository,
            libraries=(("primary", "fiction", "lib-1", _PRODUCER),),
            discover=lambda *_: (_ for _ in ()).throw(AssertionError()),
            extract=lambda *_: ("report_" + "1" * 64, False),
            providers=lambda *_: (ProviderStatus.DISABLED,),
            propose=lambda *_: "proposal_set_" + "2" * 64,
            admit=lambda *_: ("admitted", "review_case_" + "3" * 64),
            project=lambda *_: True,
            clock=lambda: 20,
            timing=StageTimer(
                timings, monotonic_ns=lambda: next(ticks), clock=lambda: 21
            ),
        )
        pipeline._run_claimed_action(repository, action("extraction"), claim_next=False)
        pipeline._run_claimed_action(
            repository, action("provider", report_id="report_" + "1" * 64),
            claim_next=False,
        )
        pipeline._run_claimed_action(
            repository, action("proposal", report_id="report_" + "1" * 64),
            claim_next=False,
        )
        pipeline._run_claimed_action(
            repository,
            action("admission", proposal_set_id="proposal_set_" + "2" * 64),
            claim_next=False,
        )
        pipeline._run_claimed_action(
            repository,
            action("projection", review_case_id="review_case_" + "3" * 64),
            claim_next=False,
        )
        self.assertEqual(
            [(value.stage, value.wait_class) for value in timings.values],
            [
                ("policy_evaluation", "none"),
                ("policy_evaluation", "none"),
                ("policy_evaluation", "none"),
            ],
        )

    def test_drain_rejects_initial_claim_and_persisted_claim_boundary_is_serialized(self) -> None:
        action = PipelineAction(
            "review_work_" + "1" * 64, "review_scan_" + "a" * 64,
            "extraction", 1, "primary", "fiction", "lib-1", _PRODUCER,
            1, "b" * 64, "item", "1" * 64, 1,
            source_fingerprint="c" * 64,
        )
        repository = _ConcurrentRepository([action])
        pipeline = ReviewPreparationPipeline(
            repository_factory=lambda: repository,
            libraries=(("primary", "fiction", "lib-1", _PRODUCER),),
            discover=lambda *_: (_ for _ in ()).throw(AssertionError()),
            extract=lambda *_: ("report_" + "1" * 64, False),
            providers=lambda *_: (), propose=lambda *_: "proposal_set_" + "2" * 64,
            admit=lambda *_: ("admitted", None), project=lambda *_: True,
        )
        pipeline.begin_drain()
        self.assertFalse(pipeline.run_next())
        self.assertEqual(repository.actions, [action])

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

        def extract(_, action: PipelineAction, __: bool):
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
            providers=lambda *_: (ProviderStatus.DISABLED,),
            propose=lambda *_: "proposal_set_" + "4" * 64,
            admit=lambda *_: ("admitted", "review_case_" + "5" * 64),
            project=lambda *_: True,
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

    def test_shutdown_deadline_retains_active_worker_and_durable_claim(self) -> None:
        started = threading.Event()
        release = threading.Event()
        action = PipelineAction(
            "review_work_" + "8" * 64, "review_scan_" + "a" * 64,
            "extraction", 1, "primary", "fiction", "lib-1", _PRODUCER,
            1, "b" * 64, "slow", "8" * 64, 1,
            source_fingerprint="c" * 64,
        )
        repository = _ConcurrentRepository([action])

        def extract(*_):
            started.set()
            self.assertTrue(release.wait(3))
            return "report_" + "3" * 64, False

        pipeline = ReviewPreparationPipeline(
            repository_factory=lambda: repository,
            libraries=(("primary", "fiction", "lib-1", _PRODUCER),),
            discover=lambda *_: (_ for _ in ()).throw(AssertionError()),
            extract=extract, providers=lambda *_: (),
            propose=lambda *_: "proposal_set_" + "4" * 64,
            admit=lambda *_: ("admitted", None), project=lambda *_: True,
        )
        supervisor = ReviewPreparationSupervisor(
            pipeline, graceful_shutdown_seconds=1
        )
        supervisor.start()
        self.assertTrue(started.wait(1))
        with self.assertRaisesRegex(ReviewPipelineError, "shutdown_timeout"):
            supervisor.stop()
        self.assertTrue(supervisor._threads)
        self.assertEqual(repository.completed, [])
        release.set()
        supervisor.stop()
        self.assertEqual(repository.completed, ["slow"])
        self.assertEqual(supervisor._threads, ())


if __name__ == "__main__":
    unittest.main()
