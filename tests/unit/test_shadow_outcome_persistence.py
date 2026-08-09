from __future__ import annotations

import json
import hashlib
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mechanical_librarian.adapters.persistence.metadata_reports import MetadataReportError, open_repository
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
from mechanical_librarian.domain.automatic_decision import AutomaticDecisionError, AutomaticSafetyState
from mechanical_librarian.domain.human_decision import HumanDecision, HumanDecisionError
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.application.review_web import InspectCurrentReviewCase
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


def _synthetic_automatic_tuple(repository, case_id: str, category: str):
    """A value-free qualified projection packet for persistence race coverage.

    The production projection is separately tested from real reports above.  This
    small packet isolates the append-only admission boundary without importing a
    corpus value or manufacturing a metadata write.
    """
    case = ReviewCase.create_from_data(json.loads(repository.get_review_case(case_id))["case"])
    members, option = (
        (("title", "subtitle", "series"), "title_and_subtitle")
        if category == "title_layout"
        else (("publishedDate", "publishedYear"), "full_date")
    )
    shared = ("bibliographic_combination_" + "a" * 64,)
    fields = tuple(
        SimpleNamespace(
            disposition="candidate",
            policy_category=category,
            policy_option=option,
            policy_destination="automatic",
            destination=destination,
            combination_ids=shared,
            proposal_id="review_proposal_" + f"{index:064x}",
            candidate_id="alternative_" + f"{index + 16:064x}",
            rule_id="shadow_rule_" + f"{index + 32:064x}",
            outcome_id="shadow_outcome_" + f"{index + 48:064x}",
        )
        for index, destination in enumerate(members)
    )
    policy_id = repository.current_resolved_policy_id(case.target["library"])
    assert policy_id is not None
    generation = repository._connection.execute(
        "SELECT generation FROM metadata_policy_state WHERE singleton=1"
    ).fetchone()[0]
    return SimpleNamespace(
        outcomes=fields,
        review_case_id=case_id,
        report_id=case.report_id,
        proposal_set_id=case.proposal_set_id,
        target_fingerprint=case.target_fingerprint,
        resolved_policy_id=policy_id,
        policy_generation=generation,
    )


def _human_title_decision(repository, case_id: str, occurred_at: int) -> HumanDecision:
    case = ReviewCase.create_from_data(json.loads(repository.get_review_case(case_id))["case"])
    proposal = next(value for value in case.proposals if value["destination"] == "title")
    result = proposal["result"]
    assert isinstance(result, dict)
    alternatives = result.get("alternatives")
    assert isinstance(alternatives, list) and alternatives
    selected = alternatives[0]["value"]
    assert isinstance(selected, dict)
    return HumanDecision.create(
        case,
        proposal_id=proposal["proposal_id"],
        target_fingerprint=case.target_fingerprint,
        destination="title",
        actor="owner",
        outcome="accept",
        selected_value=selected,
        idempotency_key="synthetic-human-title-key-0001",
        occurred_at=occurred_at,
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

    def test_preview_reconciliation_failure_rolls_back_the_posture_and_automatic_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = open_repository(Path(temporary) / "reports.sqlite")
            case_id = _case(repository)
            repository._record_automatic_outcomes(
                _synthetic_automatic_tuple(repository, case_id, "title_layout"), 2
            )
            with patch.object(
                repository,
                "activate_metadata_policy",
                side_effect=MetadataReportError("reconciliation failed"),
            ):
                with self.assertRaisesRegex(MetadataReportError, "reconciliation failed"):
                    repository.enter_shadow_preview(3)
            self.assertEqual("automatic", repository.shadow_posture())
            self.assertEqual((3,), repository._connection.execute(
                "SELECT COUNT(*) FROM automatic_decisions"
            ).fetchone())
            repository.close()

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

    def test_automatic_authority_binding_failure_is_global_and_keeps_shadow_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "reports.sqlite"
            repository = open_repository(path)
            case_id = _case(repository)
            # The construction seam reports malformed manifest/source/identity
            # bindings as an authority error. It must fail closed globally,
            # while the report-only observation survives the savepoint rollback.
            with patch.object(
                repository,
                "_record_automatic_outcomes",
                side_effect=AutomaticDecisionError("source_binding_invalid"),
            ):
                persisted = repository.persist_shadow_outcome(case_id, 9)
            self.assertTrue(persisted.startswith('{"authority":"none"'))
            self.assertEqual(
                "automatic_authority_failed",
                repository.automatic_safety_state().global_suspension,
            )
            self.assertEqual((), repository.automatic_safety_state().category_suspensions)
            self.assertEqual(((persisted, 9),), repository.list_shadow_outcomes(50))
            repository.close()
            reopened = open_repository(path)
            self.assertEqual(
                "automatic_authority_failed",
                reopened.automatic_safety_state().global_suspension,
            )
            reopened.close()

    def test_explicit_bundle_safety_failure_is_category_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = open_repository(Path(temporary) / "reports.sqlite")
            case_id = _case(repository)
            # Category is reserved for the bundle-coherence classifier; it
            # must not become the fallback for authority-binding failures.
            with patch.object(
                repository,
                "_record_automatic_outcomes",
                side_effect=AutomaticDecisionError("category:title_layout"),
            ):
                repository.persist_shadow_outcome(case_id, 10)
            safety = repository.automatic_safety_state()
            self.assertIsNone(safety.global_suspension)
            self.assertEqual(
                (("title_layout", "automatic_admission_failed"),),
                safety.category_suspensions,
            )
            repository.close()

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

    def test_human_authority_supersedes_automatic_without_global_suspension(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = open_repository(Path(temporary) / "reports.sqlite")
            case_id = _case(repository)
            repository.record_human_decision(_human_title_decision(repository, case_id, 1))
            repository._record_automatic_outcomes(
                _synthetic_automatic_tuple(repository, case_id, "title_layout"), 2
            )
            self.assertEqual(
                (1, 0),
                tuple(
                    repository._connection.execute(
                        "SELECT (SELECT COUNT(*) FROM human_decisions),"
                        "(SELECT COUNT(*) FROM automatic_decisions)"
                    ).fetchone()
                ),
            )
            self.assertEqual(AutomaticSafetyState(False, None, ()), repository.automatic_safety_state())
            repository.close()

        with tempfile.TemporaryDirectory() as temporary:
            repository = open_repository(Path(temporary) / "reports.sqlite")
            case_id = _case(repository)
            repository._record_automatic_outcomes(
                _synthetic_automatic_tuple(repository, case_id, "title_layout"), 2
            )
            repository.record_human_decision(_human_title_decision(repository, case_id, 3))
            self.assertEqual((1, 3), tuple(repository._connection.execute(
                "SELECT (SELECT COUNT(*) FROM human_decisions),(SELECT COUNT(*) FROM automatic_decisions)"
            ).fetchone()))
            self.assertEqual(
                ("primary", "ebooks"),
                repository.field_effect_manual_hold_context(case_id, "title"),
            )
            self.assertEqual(
                AutomaticSafetyState(False, None, ()), repository.automatic_safety_state()
            )
            repository.close()

    def test_automatic_tuple_replay_is_exact_and_date_and_title_bundles_are_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "reports.sqlite"
            repository = open_repository(path)
            case_id = _case(repository)
            title = _synthetic_automatic_tuple(repository, case_id, "title_layout")
            dates = _synthetic_automatic_tuple(repository, case_id, "date_precision")
            repository._record_automatic_outcomes(title, 7)
            first = repository.get_review_case_decisions(case_id)
            repository._record_automatic_outcomes(title, 99)
            repository._record_automatic_outcomes(dates, 8)
            documents = tuple(json.loads(value) for value in repository.get_review_case_decisions(case_id))
            self.assertEqual(first, repository.get_review_case_decisions(case_id)[:3])
            self.assertEqual(5, len(documents))
            for destinations in (("title", "subtitle", "series"), ("publishedDate", "publishedYear")):
                bundle = [value for value in documents if value["destination"] in destinations]
                self.assertEqual(set(destinations), {value["destination"] for value in bundle})
                self.assertEqual(1, len({value["bundle_id"] for value in bundle}))
            repository.close()
            reopened = open_repository(path)
            self.assertEqual(tuple(value["decision_id"] for value in documents), tuple(
                json.loads(value)["decision_id"] for value in reopened.get_review_case_decisions(case_id)
            ))
            reopened.close()

    def test_incoherent_automatic_tuple_suspends_only_its_category_and_safety_recovery_restores_admission(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = open_repository(Path(temporary) / "reports.sqlite")
            case_id = _case(repository)
            incoherent = _synthetic_automatic_tuple(repository, case_id, "title_layout")
            incoherent.outcomes[1].combination_ids = ("bibliographic_combination_" + "b" * 64,)
            with self.assertRaisesRegex(AutomaticDecisionError, "category:title_layout"):
                repository._record_automatic_outcomes(incoherent, 3)
            repository.set_automatic_safety_state(
                AutomaticSafetyState(False, None, (("title_layout", "automatic_admission_failed"),)), 4
            )
            repository._record_automatic_outcomes(
                _synthetic_automatic_tuple(repository, case_id, "title_layout"), 5
            )
            self.assertEqual((0,), repository._connection.execute(
                "SELECT COUNT(*) FROM automatic_decisions"
            ).fetchone())
            repository.set_automatic_safety_state(AutomaticSafetyState(False, None, ()), 6)
            repository._record_automatic_outcomes(
                _synthetic_automatic_tuple(repository, case_id, "title_layout"), 7
            )
            self.assertEqual((3,), repository._connection.execute(
                "SELECT COUNT(*) FROM automatic_decisions"
            ).fetchone())
            repository.close()

    def test_nonautomatic_policy_destination_never_constructs_an_automatic_decision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = open_repository(Path(temporary) / "reports.sqlite")
            case_id = _case(repository)
            ignored = SimpleNamespace(
                outcomes=(SimpleNamespace(
                    disposition="candidate", policy_category="tags", policy_option="catalogue",
                    policy_destination="ignore", destination="tags", combination_ids=(),
                    proposal_id="review_proposal_" + "a" * 64,
                    candidate_id="alternative_" + "b" * 64,
                    rule_id="shadow_rule_" + "c" * 64,
                    outcome_id="shadow_outcome_" + "d" * 64,
                ),),
                review_case_id=case_id,
                report_id=ReviewCase.create_from_data(json.loads(repository.get_review_case(case_id))["case"]).report_id,
                proposal_set_id=ReviewCase.create_from_data(json.loads(repository.get_review_case(case_id))["case"]).proposal_set_id,
                target_fingerprint=ReviewCase.create_from_data(json.loads(repository.get_review_case(case_id))["case"]).target_fingerprint,
                resolved_policy_id=repository.current_resolved_policy_id("ebooks"),
                policy_generation=1,
            )
            repository._record_automatic_outcomes(ignored, 2)
            self.assertEqual((0,), repository._connection.execute(
                "SELECT COUNT(*) FROM automatic_decisions"
            ).fetchone())
            self.assertEqual(AutomaticSafetyState(False, None, ()), repository.automatic_safety_state())
            repository.close()

    def test_paused_or_category_suspended_automatic_tuple_remains_in_every_attention_projection(self) -> None:
        local_title = LocalClaim.create(
            kind=ClaimKind.TITLE, observed="Secret History", canonical="Secret History",
            excerpt="Secret History", applicability="not_applicable", checksum="not_applicable",
            applicability_basis="not_applicable", scope=ClaimScope.WORK,
        )
        source = _source("catalogue", "edition-1", titles=("Secret History",), subtitle="A Mistborn Novella")
        report = _bibliographic_report(
            current_title="secret history", providers=(source,), local_claims=(local_title,)
        )
        profile = _preview_automatic_profile()

        def admit(repository):
            repository.enter_shadow_preview(1)
            repository.create_metadata_policy_draft(profile.canonical_json)
            resolved = resolve_policy(profile, report.target.library)
            repository.activate_metadata_policy(profile.policy_id, MetadataPolicyProfile.builtin().policy_id, ((report.target.library, resolved.resolved_id, resolved.canonical_json),), 2)
            proposal = evaluate_report(report, resolved.resolved_id, human_review_destinations(resolved))
            title = next(value for value in proposal.results if value.destination.value == "title")
            self.assertNotEqual("unresolved_choice", title.to_data()["outcome"])
            self.assertNotIn("no_safe_bibliographic_combination", title.to_data()["reasons"])
            repository.publish(report, lambda: True)
            repository.publish_proposal(proposal)
            return json.loads(repository.admit_review_case(proposal))["review_case_id"]

        with tempfile.TemporaryDirectory() as temporary:
            baseline = open_repository(Path(temporary) / "reports.sqlite")
            case_id = admit(baseline)
            title = next(field for field in InspectCurrentReviewCase(baseline).run(case_id)["fields"] if field["destination"] == "title")
            self.assertFalse(title["needs_attention"])
            baseline.close()
        for safety in (
            AutomaticSafetyState(True, None, ()),
            AutomaticSafetyState(False, None, (("title_layout", "admission_failed"),)),
            AutomaticSafetyState(False, "authority_failed", ()),
        ):
            with self.subTest(safety=safety), tempfile.TemporaryDirectory() as temporary:
                repository = open_repository(Path(temporary) / "reports.sqlite")
                case_id = admit(repository)
                repository.set_automatic_safety_state(safety, 1)
                repository.persist_shadow_outcome(case_id, 2)
                self.assertEqual((0,), repository._connection.execute(
                    "SELECT COUNT(*) FROM automatic_decisions"
                ).fetchone())
                detail = InspectCurrentReviewCase(repository).run(case_id)
                title = next(field for field in detail["fields"] if field["destination"] == "title")
                self.assertTrue(title["needs_attention"])
                library = repository.read_review_library(None, 50, 0, True)
                self.assertEqual(1, library.total)
                self.assertEqual(1, library.attention_total)
                attention = repository.read_review_attention(None, 50, 0)
                self.assertGreaterEqual(attention.total, 1)
                self.assertIn("title", tuple(row[2] for row in attention.rows))
                repository.close()

    def test_bootstrap_transition_acknowledgement_is_durable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "reports.sqlite"
            repository = open_repository(path)
            transition = repository.metadata_policy_transition()
            assert transition is not None
            acknowledged = repository.acknowledge_metadata_policy_transition(transition.transition_id, 4)
            self.assertTrue(acknowledged.acknowledged)
            repository.close()
            reopened = open_repository(path)
            persisted = reopened.metadata_policy_transition()
            assert persisted is not None
            self.assertTrue(persisted.acknowledged)
            reopened.close()


if __name__ == "__main__":
    unittest.main()
