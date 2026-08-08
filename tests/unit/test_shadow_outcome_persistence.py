from __future__ import annotations

import json
import hashlib
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.metadata_policy import (
    MAX_SHADOW_SAMPLE_RESPONSE_BYTES,
    PolicyNotebook,
)
from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    LibraryConfiguration,
    ReviewWebConfiguration,
    TargetConnectionConfiguration,
)
from mechanical_librarian.bootstrap import composition
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.metadata_policy import (
    DEFAULT_OPTIONS,
    OPTION_CATALOGUE,
    MetadataPolicyProfile,
    human_review_destinations,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import ClaimKind, ClaimScope, LocalClaim
from mechanical_librarian.domain.shadow_outcome import ShadowOutcomeSet
from mechanical_librarian.domain.automatic_decision import AutomaticSafetyState
from tests.unit.test_bibliographic_bundles import (
    _bibliographic_report,
    _source,
)
from tests.unit.test_proposal_set import _fields, _report as _proposal_report
from tests.unit.test_review_case_persistence import _report


def _case(repository):
    report = _report()
    repository.publish(report, lambda: True)
    proposal = evaluate_report(report)
    repository.publish_proposal(proposal)
    admitted = json.loads(repository.admit_review_case(proposal))
    return admitted["review_case_id"]


def _running_projection(repository, case_id: str) -> str:
    case = json.loads(repository.get_review_case(case_id))["case"]
    scan_id = "review_scan_" + ("1" * 64)
    work_id = "review_work_" + ("2" * 64)
    repository._connection.execute(
        "INSERT INTO review_preparation_scans(scan_id,connection_name,library_name,"
        "library_id,producer_fingerprint,state,requested_at,updated_at,"
        "catalogue_generation,catalogue_fingerprint,total) "
        "VALUES(?,?,?,?,?,'processing',1,1,1,?,1)",
        (scan_id, "primary", "ebooks", "library-1", "3" * 64, "4" * 64),
    )
    repository._connection.execute(
        "INSERT INTO review_preparation_work(work_id,scan_id,ordinal,connection_name,"
        "library_name,library_id,item_id,file_identity,source_revision,"
        "source_fingerprint,producer_fingerprint,catalogue_generation,"
        "catalogue_fingerprint,stage,state,attempt,recovery_attempt,report_id,"
        "proposal_set_id,review_case_id,failure_code,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'projection','running',1,0,?,?,?,NULL,1)",
        (
            work_id,
            scan_id,
            0,
            "primary",
            "ebooks",
            "library-1",
            "item-1",
            case["file_identity"],
            1,
            "5" * 64,
            "3" * 64,
            1,
            "4" * 64,
            case["report_id"],
            case["proposal_set_id"],
            case_id,
        ),
    )
    repository._connection.commit()
    return work_id


def _digest(value: object) -> str:
    serialized = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _preview_automatic_profile() -> MetadataPolicyProfile:
    destinations = {
        category: "unsupported" if category == "explicit" else "ask"
        for category in OPTION_CATALOGUE
    }
    destinations.update(
        title_layout="automatic",
        publisher_preference="automatic",
        tags="ignore",
    )
    return MetadataPolicyProfile.create(
        "Preview automatic intent", dict(DEFAULT_OPTIONS), destinations
    )


def _pipeline_configuration() -> ApplicationConfiguration:
    return ApplicationConfiguration(
        (
            TargetConnectionConfiguration(
                "primary", "https://example.invalid", "/run/target-token"
            ),
        ),
        (LibraryConfiguration("ebooks", "primary", "library-1", "book", None, ()),),
        (),
        (),
        review=ReviewWebConfiguration(
            "https://review.example.invalid", "/run/owner-password"
        ),
    )


def _maximum_reference_outcome(repository) -> ShadowOutcomeSet:
    case_id = _case(repository)
    repository.enter_shadow_preview(1)
    base = ShadowOutcomeSet.from_json(
        repository.persist_shadow_outcome(case_id, 2)
    )
    citations = tuple(f"local_{index:064x}" for index in range(256))
    combinations = tuple(
        f"bibliographic_combination_{index:064x}" for index in range(256)
    )
    fields = []
    for field in base.outcomes:
        identity = field._identity_data()
        identity["citation_ids"] = list(citations)
        identity["combination_ids"] = list(combinations)
        identity["qualification_candidate_id"] = (
            "qualification_candidate_"
            + _digest(
                {
                    "action_id": field.action_id,
                    "candidate_id": field.candidate_id,
                    "catalogue_sha256": base.catalogue_sha256,
                    "citation_ids": list(citations),
                    "combination_ids": list(combinations),
                    "rule_id": field.rule_id,
                }
            )
        )
        fields.append(
            replace(
                field,
                outcome_id="shadow_outcome_" + _digest(identity),
                citation_ids=citations,
                combination_ids=combinations,
                qualification_candidate_id=identity[
                    "qualification_candidate_id"
                ],
            )
        )
    identity = base._identity_data()
    identity["outcomes"] = [field.to_data() for field in fields]
    return replace(
        base,
        outcome_set_id="shadow_outcome_set_" + _digest(identity),
        outcomes=tuple(fields),
    )


class ShadowOutcomePersistenceTests(unittest.TestCase):
    def test_preview_automatic_matrix_preserves_human_policy_and_records_outcomes(self) -> None:
        publisher = LocalClaim.create(
            kind=ClaimKind.PUBLISHER,
            observed="Selected EPUB Press",
            canonical="Selected EPUB Press",
            excerpt="Selected EPUB Press",
            applicability="not_applicable",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            scope=ClaimScope.EDITION,
        )
        local_title = LocalClaim.create(
            kind=ClaimKind.TITLE,
            observed="Secret History",
            canonical="Secret History",
            excerpt="Secret History",
            applicability="not_applicable",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            scope=ClaimScope.WORK,
        )
        source = _source(
            "catalogue",
            "edition-1",
            titles=("Secret History",),
            subtitle="A Mistborn Novella",
        )
        reports = (
            _report(),
            _proposal_report(
                title=None,
                fields=_fields(None),
                local_claims=(publisher,),
            ),
            _bibliographic_report(
                current_title="secret history", providers=(source,)
            ),
            _bibliographic_report(
                current_title="secret history",
                providers=(source,),
                local_claims=(local_title,),
            ),
        )
        recorded: set[str] = set()
        for report in reports:
            with self.subTest(report=report.report_id), tempfile.TemporaryDirectory() as temporary:
                repository = open_repository(Path(temporary) / "reports.sqlite")
                profile = _preview_automatic_profile()
                repository.enter_shadow_preview(1)
                repository.create_metadata_policy_draft(profile.canonical_json)
                resolved = resolve_policy(profile, report.target.library)
                repository.activate_metadata_policy(
                    profile.policy_id,
                    MetadataPolicyProfile.builtin().policy_id,
                    ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
                    2,
                )
                effective = human_review_destinations(resolved)
                self.assertEqual("ask", effective["title_layout"])
                self.assertEqual("ignore", effective["tags"])
                self.assertEqual("unsupported", effective["explicit"])
                manual_profile = MetadataPolicyProfile.create(
                    "Equivalent human review",
                    dict(profile.options),
                    effective,
                )
                equivalent = resolve_policy(manual_profile, report.target.library)
                proposal = evaluate_report(
                    report, resolved.resolved_id, effective
                )
                manual_proposal = evaluate_report(
                    report,
                    equivalent.resolved_id,
                    dict(equivalent.destinations),
                )
                self.assertEqual(proposal.results, manual_proposal.results)
                self.assertEqual(
                    proposal.bibliographic_combinations,
                    manual_proposal.bibliographic_combinations,
                )
                repository.publish(report, lambda: True)
                repository.publish_proposal(proposal)
                admitted = json.loads(repository.admit_review_case(proposal))
                self.assertEqual("admitted", admitted["status"])
                default_packet = repository.preview_shadow_packet(
                    admitted["review_case_id"]
                )
                preview_packet = repository._persisted_shadow_packet_source(
                    admitted["review_case_id"], include_policy_ignored=True
                )[1]
                self.assertNotIn(
                    "tags",
                    {value["destination"] for value in default_packet.proposals},
                )
                self.assertIn(
                    "tags",
                    {value["destination"] for value in preview_packet.proposals},
                )
                outcome = ShadowOutcomeSet.from_json(
                    repository.persist_shadow_outcome(
                        admitted["review_case_id"], 3
                    )
                )
                recorded.update(value.disposition for value in outcome.outcomes)
                self.assertEqual("none", outcome.authority)
                repository.close()
        with tempfile.TemporaryDirectory() as temporary:
            repository = open_repository(Path(temporary) / "reports.sqlite")
            report = _report()
            profile = MetadataPolicyProfile.create(
                "Manual review",
                dict(DEFAULT_OPTIONS),
                {
                    category: "unsupported" if category == "explicit" else "ask"
                    for category in OPTION_CATALOGUE
                },
            )
            repository.enter_shadow_preview(1)
            repository.create_metadata_policy_draft(profile.canonical_json)
            resolved = resolve_policy(profile, report.target.library)
            repository.activate_metadata_policy(
                profile.policy_id,
                MetadataPolicyProfile.builtin().policy_id,
                ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
                2,
            )
            repository.publish(report, lambda: True)
            proposal = evaluate_report(
                report, resolved.resolved_id, human_review_destinations(resolved)
            )
            repository.publish_proposal(proposal)
            admitted = json.loads(repository.admit_review_case(proposal))
            outcome = ShadowOutcomeSet.from_json(
                repository.persist_shadow_outcome(admitted["review_case_id"], 3)
            )
            recorded.update(value.disposition for value in outcome.outcomes)
            repository.close()
        self.assertEqual(
            {
                "candidate",
                "keep_current",
                "conflict",
                "abstention",
                "unsupported",
                "ignore",
                "manual",
            },
            recorded,
        )

    def test_composed_projection_succeeds_replays_and_reports_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "reports.sqlite"
            repository = open_repository(database)
            case_id = _case(repository)
            repository.enter_shadow_preview(1)
            supervisor = composition.create_review_pipeline(
                _pipeline_configuration(),
                database,
                root / "catalogue.sqlite",
                root / "artifacts",
            )

            self.assertEqual(
                "complete", supervisor._pipeline._project(repository, case_id)
            )
            self.assertEqual(
                "complete", supervisor._pipeline._project(repository, case_id)
            )
            current = _report("Current title")
            prior_report_id = json.loads(repository.get_review_case(case_id))["case"][
                "report_id"
            ]
            repository.publish(current, lambda: True, prior_report_id)
            self.assertEqual(
                "stale", supervisor._pipeline._project(repository, case_id)
            )
            self.assertEqual(1, len(repository.list_shadow_outcomes(50)))
            repository.close()

    def test_review_posture_composed_stale_projection_restarts_instead_of_completing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "reports.sqlite"
            repository = open_repository(database)
            old_case_id = _case(repository)
            old_case = json.loads(repository.get_review_case(old_case_id))["case"]
            work_id = _running_projection(repository, old_case_id)
            current_report = _report("Current title")
            repository.publish(current_report, lambda: True, old_case["report_id"])
            current_proposal = evaluate_report(current_report)
            repository.publish_proposal(current_proposal)
            repository.admit_review_case(current_proposal)
            supervisor = composition.create_review_pipeline(
                _pipeline_configuration(),
                database,
                root / "catalogue.sqlite",
                root / "artifacts",
            )

            self.assertEqual("automatic", repository.shadow_posture())
            self.assertEqual(
                "stale", supervisor._pipeline._project(repository, old_case_id)
            )
            self.assertEqual(
                "stale", supervisor._pipeline._project(repository, old_case_id)
            )
            self.assertEqual(
                ("proposal", "queued", current_report.report_id, None, None),
                repository._connection.execute(
                    "SELECT stage,state,report_id,proposal_set_id,review_case_id "
                    "FROM review_preparation_work WHERE work_id=?",
                    (work_id,),
                ).fetchone(),
            )
            self.assertEqual((), repository.list_shadow_outcomes(50))
            self.assertEqual(
                (1,),
                repository._connection.execute(
                    "SELECT COUNT(*) FROM review_preparation_work"
                ).fetchone(),
            )
            repository.close()

    def test_automatic_bootstrap_and_preview_replay_one_exact_set(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "reports.sqlite"
            repository = open_repository(path)
            case_id = _case(repository)

            self.assertEqual("automatic", repository.shadow_posture())
            bootstrap = repository.metadata_policy_transition()
            self.assertIsNotNone(bootstrap)
            assert bootstrap is not None
            self.assertFalse(bootstrap.acknowledged)
            self.assertFalse(hasattr(repository, "record_automatic_decision"))
            automatic = repository.persist_shadow_outcome(case_id, 10)
            self.assertTrue(automatic.startswith('{"authority":"none"'))
            self.assertEqual(((automatic, 10),), repository.list_shadow_outcomes(50))

            self.assertEqual("preview", repository.enter_shadow_preview(11))
            first = repository.persist_shadow_outcome(case_id, 12)
            second = repository.persist_shadow_outcome(case_id, 13)
            self.assertEqual(first, second)
            outcome = ShadowOutcomeSet.from_json(first)
            self.assertEqual("none", outcome.authority)
            self.assertEqual("preview", outcome.posture)
            self.assertEqual(case_id, outcome.review_case_id)
            self.assertTrue({value.disposition for value in outcome.outcomes}.issubset({"candidate", "keep_current", "abstention", "conflict", "unsupported"}))
            self.assertTrue(
                all(value.qualification_candidate_id.startswith("qualification_candidate_") for value in outcome.outcomes)
            )
            self.assertEqual(
                len(outcome.outcomes),
                len({value.qualification_candidate_id for value in outcome.outcomes}),
            )
            self.assertEqual(((first, 10),), repository.list_shadow_outcomes(50))
            history = repository.get_review_case_history(case_id, 50)
            self.assertEqual("shadow_preview_recorded", history[0]["kind"])
            self.assertEqual("none", history[0]["authority"])
            repository.close()

            connection = sqlite3.connect(path)
            self.assertEqual(
                (1,),
                connection.execute(
                    "SELECT COUNT(*) FROM shadow_decision_outcomes"
                ).fetchone(),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE shadow_decision_outcomes SET evaluated_at=99"
                )
            connection.close()

    def test_automatic_safety_update_preserves_a_caller_transaction_and_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "reports.sqlite"
            repository = open_repository(path)
            repository._connection.execute("BEGIN IMMEDIATE")
            repository.set_automatic_safety_state(
                AutomaticSafetyState(False, None, (("title_layout", "admission_failed"),)), 9
            )
            self.assertTrue(repository._connection.in_transaction)
            repository._connection.commit()
            repository.close()
            reopened = open_repository(path)
            self.assertEqual(
                AutomaticSafetyState(False, None, (("title_layout", "admission_failed"),)),
                reopened.automatic_safety_state(),
            )
            reopened.close()

    def test_shadow_history_orders_human_and_preview_events_by_time(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = open_repository(Path(temporary) / "reports.sqlite")
            case_id = _case(repository)
            repository.enter_shadow_preview(1)
            repository.persist_shadow_outcome(case_id, 5)
            # A second deterministic set is a replay, so it cannot manufacture a
            # newer chronology merely because delivery repeats.
            repository.persist_shadow_outcome(case_id, 50)
            events = repository.get_review_case_history(case_id, 50)
            shadow = [value for value in events if value["kind"] == "shadow_preview_recorded"]
            self.assertEqual([5], [value["occurred_at"] for value in shadow])
            repository.close()

    def test_sample_bounds_limit_fifty_at_maximum_reference_cardinality(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = open_repository(Path(temporary) / "reports.sqlite")
            outcome = _maximum_reference_outcome(repository)
            repository.close()

        class SampleRepository:
            def list_shadow_outcomes(self, limit):
                return tuple((outcome.to_json(), index) for index in range(limit))

        sample = PolicyNotebook(SampleRepository(), (), {}).shadow_sample(50)
        serialized = json.dumps(
            sample, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        self.assertGreater(len(sample["items"]), 0)
        self.assertLess(len(sample["items"]), 50)
        self.assertLessEqual(len(serialized), MAX_SHADOW_SAMPLE_RESPONSE_BYTES)
        first = sample["items"][0]["outcomes"][0]
        self.assertEqual(256, len(first["citation_ids"]))
        self.assertEqual(256, len(first["combination_ids"]))

    def test_canonical_hostile_shadow_row_fails_closed_without_exposure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = open_repository(Path(temporary) / "reports.sqlite")
            case_id = _case(repository)
            repository.enter_shadow_preview(1)
            persisted = ShadowOutcomeSet.from_json(
                repository.persist_shadow_outcome(case_id, 2)
            ).to_data()
            field = dict(persisted["outcomes"][0])
            field.update(
                {
                    "destination": "/private/owner/library",
                    "policy_category": None,
                    "policy_destination": "unsupported",
                    "policy_option": None,
                    "disposition": "unsupported",
                }
            )
            field["rule_id"] = "shadow_rule_" + _digest(
                {
                    "category": None,
                    "decision_policy_sha256": persisted[
                        "decision_policy_sha256"
                    ],
                    "decision_policy_version": persisted[
                        "decision_policy_version"
                    ],
                    "destination": "unsupported",
                    "option": None,
                }
            )
            field["qualification_candidate_id"] = (
                "qualification_candidate_"
                + _digest(
                    {
                        "action_id": field["action_id"],
                        "candidate_id": field["candidate_id"],
                        "catalogue_sha256": persisted["catalogue_sha256"],
                        "citation_ids": field["citation_ids"],
                        "combination_ids": field["combination_ids"],
                        "rule_id": field["rule_id"],
                    }
                )
            )
            field_identity = dict(field)
            del field_identity["outcome_id"]
            field["outcome_id"] = "shadow_outcome_" + _digest(field_identity)
            persisted["outcomes"][0] = field
            outcome_identity = dict(persisted)
            del outcome_identity["outcome_set_id"]
            persisted["outcome_set_id"] = "shadow_outcome_set_" + _digest(
                outcome_identity
            )
            hostile = json.dumps(
                persisted,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            repository._connection.execute(
                "DROP TRIGGER shadow_decision_outcomes_no_update"
            )
            repository._connection.execute(
                "UPDATE shadow_decision_outcomes "
                "SET outcome_set_id=?,document=?",
                (persisted["outcome_set_id"], hostile),
            )
            repository._connection.commit()

            with self.assertRaisesRegex(
                Exception, "shadow sample is unavailable"
            ) as sample_error:
                PolicyNotebook(repository, (), {}).shadow_sample(50)
            with self.assertRaisesRegex(
                Exception, "review history is unavailable"
            ) as history_error:
                repository.get_review_case_history(case_id, 50)
            self.assertNotIn("/private/owner/library", str(sample_error.exception))
            self.assertNotIn("/private/owner/library", str(history_error.exception))
            self.assertEqual(
                (1,),
                repository._connection.execute(
                    "SELECT COUNT(*) FROM shadow_decision_outcomes"
                ).fetchone(),
            )
            repository.close()

    def test_report_only_currentness_race_restarts_the_same_projection_row(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = open_repository(Path(temporary) / "reports.sqlite")
            old_case_id = _case(repository)
            old_case = json.loads(repository.get_review_case(old_case_id))["case"]
            work_id = _running_projection(repository, old_case_id)
            current_report = _report("Current title")
            repository.publish(
                current_report, lambda: True, old_case["report_id"]
            )
            connection = repository._connection
            repository.enter_shadow_preview(2)

            self.assertEqual(
                (old_case["report_id"], old_case_id),
                connection.execute(
                    "SELECT report_id,review_case_id FROM current_review_case_lineages"
                ).fetchone(),
            )
            self.assertEqual("stale", repository.persist_shadow_outcome(old_case_id, 3))
            self.assertEqual("stale", repository.persist_shadow_outcome(old_case_id, 4))
            self.assertEqual((), repository.list_shadow_outcomes(50))
            self.assertEqual(
                ("proposal", "queued", current_report.report_id, None, None),
                connection.execute(
                    "SELECT stage,state,report_id,proposal_set_id,review_case_id FROM review_preparation_work WHERE work_id=?",
                    (work_id,),
                ).fetchone(),
            )
            self.assertEqual(
                (1,),
                connection.execute(
                    "SELECT COUNT(*) FROM review_preparation_work"
                ).fetchone(),
            )
            repository.close()

    def test_stale_recovery_currentness_failure_rolls_back_owned_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = open_repository(Path(temporary) / "reports.sqlite")
            old_case_id = _case(repository)
            old_case = json.loads(repository.get_review_case(old_case_id))["case"]
            work_id = _running_projection(repository, old_case_id)
            current_report = _report("Current title")
            repository.publish(current_report, lambda: True, old_case["report_id"])
            repository._connection.execute(
                "UPDATE current_metadata_reports SET report_id=?",
                (old_case["report_id"],),
            )
            repository._connection.commit()

            with self.assertRaisesRegex(
                Exception, "shadow outcome currentness is unavailable"
            ):
                repository.persist_shadow_outcome(old_case_id, 3)
            self.assertFalse(repository._connection.in_transaction)
            self.assertEqual(
                (
                    "projection",
                    "running",
                    old_case["report_id"],
                    old_case["proposal_set_id"],
                    old_case_id,
                ),
                repository._connection.execute(
                    "SELECT stage,state,report_id,proposal_set_id,review_case_id "
                    "FROM review_preparation_work WHERE work_id=?",
                    (work_id,),
                ).fetchone(),
            )
            repository._connection.execute("BEGIN IMMEDIATE")
            repository._connection.rollback()
            repository.close()


if __name__ == "__main__":
    unittest.main()
