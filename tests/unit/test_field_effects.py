from __future__ import annotations

import os
import json
import sqlite3
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from mechanical_librarian.adapters.field_effect_fakes import (
    FakeAuthorityCurrentness,
    FakeBackupReadiness,
    FakeCapabilityObserver,
    FakeConsentCurrentness,
    FakeDependencyCurrentness,
    FakeForwardReevaluation,
    FakePreclaimHook,
    FakeTargetService,
    PrivateCoverRecoveryStore,
)
from mechanical_librarian.adapters.persistence.metadata_reports import (
    MetadataReportError,
    open_repository,
)
from mechanical_librarian.application.field_effects import GuardedFieldApplication
from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.application.operations import StageTimer, aggregate_incidents
from tests.unit.review_app_fixture import (
    TEST_REACT_BUILD_IDENTITY,
    create_transport_test_app,
)
from mechanical_librarian.domain.field_effect import (
    EffectAuthority,
    EffectConsent,
    FieldEffectError,
    FieldEffectOperation,
    FieldEffectPlan,
    SUPPORTED_DESTINATIONS,
)
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FileFacts,
    LocalClaim,
    MetadataReport,
    TargetFacts,
    default_target_fields,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.automatic_decision import (
    AutomaticDecision,
    F19A_QUALIFICATION_MANIFEST_SHA256,
)


CAPABILITY = "c" * 64
DEPENDENCY = "d" * 64


class Clock:
    def __init__(self) -> None:
        self.value = 10
    def __call__(self) -> int:
        self.value += 1
        return self.value


class TimingCollector:
    def __init__(self) -> None:
        self.values = []

    def append(self, timing) -> None:
        self.values.append(timing)

    def list(self):
        return tuple(self.values)


def decision(destination: str, selected: object, suffix: str = "a") -> HumanDecision:
    return HumanDecision(
        "human_decision_" + suffix * 64, "review_case_" + "b" * 64,
        "review_proposal_" + "c" * 64, "d" * 64, destination, "owner",
        "accept", selected, "e" * 64, "f" * 64, 1,
    )


def envelope(destination: str, value: object) -> dict[str, object]:
    kind = (
        "list" if destination in {"authors", "genres", "tags"}
        else "series" if destination == "series"
        else "cover" if destination == "cover"
        else "text"
    )
    return {"kind": kind, "value": value}


def metadata_report(title: str, content: str, file_identity: str,
                    item_id: str = "1234567890abcdef") -> MetadataReport:
    claim = LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed="Exact evaluator candidate",
        canonical="Exact evaluator candidate",
        excerpt="Exact evaluator candidate",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.WORK,
    )
    return MetadataReport.create(
        target=TargetFacts(
            "primary", "books", item_id, default_target_fields(title)
        ),
        file=FileFacts(0, content * 64, file_identity * 64),
        local_claims=(claim,),
        local_reasons=("opening_matter_title",),
        providers=(),
    )


class FieldEffectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        os.chmod(root, 0o700)
        self.database_path = root / "effects.sqlite"
        self.repository = open_repository(self.database_path, cross_thread=True)
        self.target = FakeTargetService()
        self.authority = FakeAuthorityCurrentness()
        self.dependencies = FakeDependencyCurrentness()
        self.backup = FakeBackupReadiness()
        self.capabilities = FakeCapabilityObserver(CAPABILITY)
        self.consent = FakeConsentCurrentness(
            self.repository.field_effect_consent_envelope_identity
        )
        self.preclaim = FakePreclaimHook()
        self.claim_hook = FakePreclaimHook()
        self.recovery_hook = FakePreclaimHook()
        self.reevaluation = FakeForwardReevaluation()
        self.recovery = PrivateCoverRecoveryStore(root / "recovery", lambda _: b"old-cover")
        self.clock = Clock()
        self.service = GuardedFieldApplication(
            self.repository, self.target, self.backup,
            self.capabilities, self.authority,
            self.dependencies, self.consent, self.recovery, self.reevaluation,
            self.clock,
            self.preclaim, self.claim_hook, self.recovery_hook,
        )

    def tearDown(self) -> None:
        self.repository.close()
        self.temporary.cleanup()

    def test_consent_is_invalidated_when_resolved_policy_semantics_change(self) -> None:
        repository_type = type(self.repository)
        with patch.object(repository_type, "current_resolved_policy_id", return_value="a" * 64):
            first = self.repository.grant_field_effect_consents(
                "primary", ("books",), ("title",), 1
            )[0]
            self.assertEqual(
                self.repository.current_field_effect_consent("primary", "books", "title"),
                first,
            )
        with patch.object(repository_type, "current_resolved_policy_id", return_value="b" * 64):
            with self.assertRaises(FieldEffectError):
                self.repository.current_field_effect_consent("primary", "books", "title")
            self.assertFalse(self.repository.field_effect_consent_is_current(first))
            second = self.repository.grant_field_effect_consents(
                "primary", ("books",), ("title",), 2
            )[0]
            self.assertNotEqual(first.consent_id, second.consent_id)

    def test_unused_continuous_consent_survives_exact_schema_validation(self) -> None:
        consent = self.repository.grant_field_effect_consents(
            "primary", ("books",), ("title",), 1
        )[0]
        self.repository.close()

        self.repository = open_repository(self.database_path, cross_thread=True)

        self.assertEqual(
            consent,
            self.repository.current_field_effect_consent(
                "primary", "books", "title"
            ),
        )

    def test_matrix_consent_rolls_back_on_second_library_policy_failure(self) -> None:
        original = self.repository.grant_field_effect_consents(
            "primary", ("books",), ("title", "subtitle"), 0,
        )
        repository_type = type(self.repository)
        with patch.object(
            repository_type, "current_resolved_policy_id",
            side_effect=("a" * 64, MetadataReportError("policy")),
        ):
            with self.assertRaisesRegex(FieldEffectError, "persistence"):
                self.repository.grant_field_effect_consent_matrix(
                    "primary", (("books", ("title",)), ("other", ("subtitle",))), 1,
                )
        self.assertEqual(
            self.repository._connection.execute(
                "SELECT count(*) FROM field_effect_consents WHERE current=1"
            ).fetchone(),
            (2,),
        )
        self.assertTrue(all(
            self.repository.field_effect_consent_is_current(consent)
            for consent in original
        ))
        self.assertFalse(self.repository._connection.in_transaction)

    def test_matrix_consent_replaces_prior_library_destination_set(self) -> None:
        original = self.repository.grant_field_effect_consents(
            "primary", ("books",), ("title", "subtitle"), 1,
        )

        current = self.repository.grant_field_effect_consent_matrix(
            "primary", (("books", ("title",)),), 2,
        )

        self.assertEqual(tuple(value.destination for value in current), ("title",))
        self.assertTrue(self.repository.field_effect_consent_is_current(current[0]))
        self.assertFalse(self.repository.field_effect_consent_is_current(original[1]))
        with self.assertRaisesRegex(FieldEffectError, "consent"):
            self.repository.current_field_effect_consent(
                "primary", "books", "subtitle"
            )

    def test_stale_success_does_not_clear_newer_capability_suspension(self) -> None:
        self.repository.suspend_field_effect_capability(
            scope="destination", connection="primary", destination="title",
            reason="field_unavailable", changed_at=10,
        )
        self.repository.clear_field_effect_capability_suspension(
            scope="destination", connection="primary", destination="title",
            reason="field_unavailable", observed_at=9,
        )
        self.assertEqual(
            self.repository.field_effect_capability_suspension("primary", "title"),
            "field_unavailable",
        )
        self.repository.clear_field_effect_capability_suspension(
            scope="destination", connection="primary", destination="title",
            reason="field_unavailable", observed_at=10,
        )
        self.assertEqual(
            self.repository.field_effect_capability_suspension("primary", "title"),
            "field_unavailable",
        )
        self.repository.suspend_field_effect_capability(
            scope="destination", connection="primary", destination="title",
            reason="unexpected_effect_behavior", changed_at=11,
        )
        self.repository.clear_field_effect_capability_suspension(
            scope="destination", connection="primary", destination="title",
            reason="field_unavailable", observed_at=12,
        )
        self.assertEqual(
            self.repository.field_effect_capability_suspension("primary", "title"),
            "unexpected_effect_behavior",
        )
        self.repository.suspend_field_effect_capability(
            scope="destination", connection="primary", destination="title",
            reason="insufficient_permission", changed_at=12,
        )
        self.assertEqual(
            self.repository.field_effect_capability_suspension("primary", "title"),
            "unexpected_effect_behavior",
        )

    def test_manual_hold_requires_explicit_return_and_later_human_wins(self) -> None:
        report = metadata_report("Old title", "1", "2")
        self.repository.publish(report, lambda: True)
        proposal_set = evaluate_report(report)
        self.repository.publish_proposal(proposal_set)
        case_id = json.loads(self.repository.admit_review_case(proposal_set))["review_case_id"]
        case = ReviewCase.create_from_data(
            json.loads(self.repository.get_review_case(case_id))["case"]
        )
        proposal = next(value for value in case.proposals
                        if value["result"]["outcome"] == "unresolved_choice")
        selected = proposal["result"]["alternatives"][0]["value"]
        human = HumanDecision.create(
            case, proposal_id=proposal["proposal_id"],
            target_fingerprint=case.target_fingerprint,
            destination=proposal["destination"], actor="owner", outcome="accept",
            selected_value=selected, idempotency_key="manual-hold-human-0001",
            occurred_at=10,
        )
        automatic = AutomaticDecision.create(
            review_case_id=case.review_case_id, report_id=case.report_id,
            proposal_set_id=case.proposal_set_id,
            proposal_id=proposal["proposal_id"],
            target_fingerprint=case.target_fingerprint,
            destination=proposal["destination"], outcome="candidate",
            candidate_id=proposal["result"]["alternatives"][0]["alternative_id"],
            resolved_policy_id=self.repository.current_resolved_policy_id("books"),
            policy_generation=1, rule_id="shadow_rule_" + "1" * 64,
            policy_category="title_layout", policy_option="preserve",
            qualification_manifest_sha256=F19A_QUALIFICATION_MANIFEST_SHA256,
            source_outcome_id="shadow_outcome_" + "3" * 64, occurred_at=9,
            bundle_id="automatic_bundle_" + "4" * 64,
        )
        self.repository._connection.execute(
            "INSERT INTO automatic_decisions(decision_id,review_case_id,destination,"
            "idempotency_digest,occurred_at,document) VALUES(?,?,?,?,?,?)",
            (automatic.decision_id, automatic.review_case_id, automatic.destination,
             automatic.idempotency_digest, automatic.occurred_at, automatic.to_json()),
        )
        non_candidate = AutomaticDecision.create(
            review_case_id=case.review_case_id, report_id=case.report_id,
            proposal_set_id=case.proposal_set_id,
            proposal_id=proposal["proposal_id"],
            target_fingerprint=case.target_fingerprint, destination="subtitle",
            outcome="keep_current", candidate_id=None,
            resolved_policy_id=self.repository.current_resolved_policy_id("books"),
            policy_generation=1, rule_id="shadow_rule_" + "2" * 64,
            policy_category="title_layout", policy_option="preserve",
            qualification_manifest_sha256=F19A_QUALIFICATION_MANIFEST_SHA256,
            source_outcome_id="shadow_outcome_" + "5" * 64, occurred_at=8,
            bundle_id="automatic_bundle_" + "6" * 64,
        )
        self.repository._connection.execute(
            "INSERT INTO automatic_decisions(decision_id,review_case_id,destination,"
            "idempotency_digest,occurred_at,document) VALUES(?,?,?,?,?,?)",
            (non_candidate.decision_id, non_candidate.review_case_id,
             non_candidate.destination, non_candidate.idempotency_digest,
             non_candidate.occurred_at, non_candidate.to_json()),
        )
        self.repository._connection.commit()

        consent = self.repository.grant_field_effect_consents(
            "primary", ("books",), (proposal["destination"],), 9
        )[0]
        authority = EffectAuthority(
            automatic.decision_id, automatic.review_case_id,
            automatic.target_fingerprint, "persisted_review_case",
            "automatic_policy", "candidate", automatic.resolved_policy_id,
            automatic.qualification_manifest_sha256,
        )
        plan = FieldEffectPlan.create(
            connection="primary", library="books", work_id="1234567890abcdef",
            destination=proposal["destination"], pre_value="Old title",
            desired_value=selected["value"], target_fingerprint=case.target_fingerprint,
            target_version="version-before-manual", dependency_digests=(DEPENDENCY,),
            authority=authority, consent=consent, capability_digest=CAPABILITY,
            created_at=9,
        )
        automatic_operation = FieldEffectOperation(plan, updated_at=9)
        self.repository.record_field_effect(automatic_operation)
        self.repository.record_human_decision(human)
        stale_hold_token = self.repository.field_effect_manual_hold_token(
            case_id, proposal["destination"]
        )
        self.assertIsNotNone(stale_hold_token)
        assert stale_hold_token is not None
        return_scan = self.repository.request_review_scan(
            "primary", "books", "library-books", "a" * 64, 10
        )
        self.repository._connection.execute(
            "UPDATE review_preparation_scans SET state='completed_with_failures',"
            "refresh_count=3,updated_at=11 WHERE scan_id=?",
            (return_scan.scan_id,),
        )
        self.repository._connection.commit()
        self.assertTrue(self.repository.request_field_effect_manual_return(
            case_id, proposal["destination"], stale_hold_token,
            return_scan.scan_id, 11, 11,
        ))
        expected_pending = ((
            case_id, proposal["destination"], "primary", "books",
            stale_hold_token, return_scan.scan_id, 11, 11,
        ),)
        self.assertEqual(
            self.repository.pending_field_effect_manual_returns(100),
            expected_pending,
        )
        requeued = self.repository.review_pipeline_progress("primary", "books")
        assert requeued is not None
        self.assertEqual(requeued.state, "queued")
        refresh_count = self.repository._connection.execute(
            "SELECT refresh_count FROM review_preparation_scans WHERE scan_id=?",
            (return_scan.scan_id,),
        ).fetchone()
        self.assertEqual(refresh_count, (0,))
        self.repository.close()
        self.repository = open_repository(
            self.database_path, create=False, cross_thread=True
        )
        self.assertEqual(
            self.repository.pending_field_effect_manual_returns(100),
            expected_pending,
        )

        self.assertEqual(self.repository.get_field_effect(plan.plan_id).state, "superseded")
        self.assertEqual(
            self.repository.field_effect_manual_hold_context(case_id, proposal["destination"]),
            ("primary", "books"),
        )
        self.assertEqual(len(self.repository.pending_field_effect_decision_documents(10)), 1)
        self.assertEqual(
            self.repository.clear_field_effect_manual_hold(
                case_id, proposal["destination"], 12
            ),
            ("primary", "books"),
        )
        self.assertIsNone(
            self.repository.field_effect_manual_hold_context(case_id, proposal["destination"])
        )
        pending = self.repository.pending_field_effect_decision_documents(10)
        self.assertEqual(len(pending), 1)
        self.assertIn(automatic.decision_id, pending[0])

        returned = self.repository.record_field_effect(automatic_operation)
        self.assertEqual(returned.state, "planned")
        self.assertEqual(self.repository.get_field_effect(plan.plan_id).state, "planned")
        self.assertEqual(
            tuple(json.loads(value)["kind"] for value in
                  self.repository.field_effect_event_documents(plan.plan_id)),
            ("planned", "superseded", "planned"),
        )

        release_old_scan = threading.Event()
        old_scan_waiting = threading.Event()
        stale_release_result = []

        def complete_old_scan() -> None:
            old_scan_waiting.set()
            self.assertTrue(release_old_scan.wait(2))
            stale_release_result.append(
                self.repository.clear_field_effect_manual_hold(
                    case_id, proposal["destination"], 13, stale_hold_token
                )
            )

        old_scan = threading.Thread(target=complete_old_scan)
        old_scan.start()
        self.assertTrue(old_scan_waiting.wait(1))

        later_human = HumanDecision.create(
            case, proposal_id=proposal["proposal_id"],
            target_fingerprint=case.target_fingerprint,
            destination=proposal["destination"], actor="owner", outcome="accept",
            selected_value=selected, idempotency_key="manual-hold-human-0002",
            occurred_at=13,
        )
        self.repository.record_human_decision(later_human)
        self.assertEqual(self.repository.get_field_effect(plan.plan_id).state, "superseded")
        self.assertEqual(
            self.repository.field_effect_manual_hold_context(case_id, proposal["destination"]),
            ("primary", "books"),
        )
        current_hold_token = self.repository.field_effect_manual_hold_token(
            case_id, proposal["destination"]
        )
        self.assertIsNotNone(current_hold_token)
        self.assertNotEqual(stale_hold_token, current_hold_token)
        release_old_scan.set()
        old_scan.join(2)
        self.assertFalse(old_scan.is_alive())
        self.assertEqual(stale_release_result, [None])
        self.repository.close()
        self.repository = open_repository(
            self.database_path, create=False, cross_thread=True
        )
        self.assertEqual(
            self.repository.field_effect_manual_hold_token(
                case_id, proposal["destination"]
            ),
            current_hold_token,
        )
        self.assertEqual(
            self.repository.field_effect_manual_hold_context(
                case_id, proposal["destination"]
            ),
            ("primary", "books"),
        )

        replacement = metadata_report("Replacement title", "3", "4")
        self.repository.publish(replacement, lambda: True)
        replacement_proposal = evaluate_report(replacement)
        self.repository.publish_proposal(replacement_proposal)
        self.repository.admit_review_case(replacement_proposal)
        self.assertEqual(
            self.repository.pending_field_effect_decision_documents(10), ()
        )

    def plan(self, destination: str = "title", pre: object = "old",
             desired: object = "new", suffix: str = "a") -> FieldEffectOperation:
        selected = envelope(destination, desired)
        source = decision(destination, selected, suffix)
        self.authorize(source)
        result = self.service.plan(
            source, connection="primary",
            library="books", work_id="book-1", destination=destination,
            pre_value=pre, desired_value=selected, target_fingerprint="d" * 64,
            target_version="version-1",
            dependency_digests=(DEPENDENCY,), capability_digest=CAPABILITY,
        )
        assert result is not None
        self.target.seed(result.plan)
        return result

    def authorize(self, source: HumanDecision | AutomaticDecision, *,
                  work_id: str = "book-1", target_version: str = "version-1") -> None:
        self.authority.bind(
            source, connection="primary", library="books", work_id=work_id,
            target_version=target_version,
        )
        self.consent.allow(
            connection="primary", library="books", destination=source.destination
        )

    def test_every_supported_shape_plans_and_no_ops_do_not_mutate(self) -> None:
        values = {
            "title": "x", "subtitle": "x", "authors": ["A"],
            "series": [{"name": "S", "sequence": "2.5"}], "genres": ["G"],
            "publishedYear": "2026", "publishedDate": "2026-01-02",
            "publisher": "P", "description": "D", "isbn": "1",
            "asin": "A", "language": "en", "tags": ["T"],
            "cover": "cover_" + "a" * 64,
        }
        self.assertEqual(SUPPORTED_DESTINATIONS, set(values))
        for index, (destination, desired) in enumerate(values.items()):
            with self.subTest(destination=destination):
                suffix = format(index + 1, "x")[-1]
                selected = envelope(destination, desired)
                source = decision(destination, selected, suffix)
                self.authorize(source, work_id=f"book-{index}")
                result = self.service.plan(
                    source, connection="primary",
                    library="books", work_id=f"book-{index}", destination=destination,
                    pre_value=None, desired_value=selected, target_fingerprint="d" * 64,
                    target_version="version-1",
                    dependency_digests=(DEPENDENCY,), capability_digest=CAPABILITY,
                )
                self.assertEqual(result.plan.destination, destination)
        same = decision("title", envelope("title", "same"), "f")
        self.authorize(same, work_id="same-book")
        self.assertIsNone(self.service.plan(
            same, connection="primary", library="books",
            work_id="same-book", destination="title", pre_value="same",
            desired_value=envelope("title", "same"), target_fingerprint="d" * 64,
            target_version="version-1",
            dependency_digests=(DEPENDENCY,), capability_digest=CAPABILITY,
        ))

    def test_qualified_automatic_origin_uses_the_identical_plan_and_effect_path(self) -> None:
        automatic = AutomaticDecision.create(
            review_case_id="review_case_" + "a" * 64,
            report_id="report_" + "b" * 64,
            proposal_set_id="proposal_set_" + "c" * 64,
            proposal_id="review_proposal_" + "d" * 64,
            target_fingerprint="e" * 64, destination="title",
            outcome="candidate", candidate_id="alternative_" + "f" * 64,
            resolved_policy_id="resolved_policy_" + "0" * 64,
            policy_generation=1, rule_id="shadow_rule_" + "1" * 64,
            policy_category="title_layout", policy_option="preserve",
            qualification_manifest_sha256=F19A_QUALIFICATION_MANIFEST_SHA256,
            source_outcome_id="shadow_outcome_" + "3" * 64,
            bundle_id="automatic_bundle_" + "4" * 64, occurred_at=1,
        )
        self.authority.automatic_values[automatic.decision_id] = envelope("title", "new")
        self.authorize(automatic, work_id="auto-book")
        result = self.service.plan(
            automatic, connection="primary", library="books", work_id="auto-book",
            destination="title", pre_value="old", desired_value=envelope("title", "new"),
            target_fingerprint="e" * 64, target_version="version-1",
            dependency_digests=(DEPENDENCY,),
            capability_digest=CAPABILITY,
        )
        assert result is not None
        self.assertEqual(result.plan.authority.origin, "automatic_policy")
        self.target.seed(result.plan)
        self.assertEqual(self.service.apply(result.plan.plan_id).state, "verified")

    def test_effect_modules_cannot_construct_audiobookshelf_or_media_writes(self) -> None:
        roots = [
            Path("src/mechanical_librarian/domain/field_effect.py"),
            Path("src/mechanical_librarian/application/field_effects.py"),
            Path("src/mechanical_librarian/adapters/field_effect_fakes.py"),
        ]
        joined = "\n".join(path.read_text(encoding="utf-8") for path in roots)
        self.assertNotIn("audiobookshelf", joined.lower())
        self.assertNotIn("media_write", joined.lower())
        keep = decision("title", None, "e")
        object.__setattr__(keep, "outcome", "keep_current")
        self.assertIsNone(self.service.plan(
            keep, connection="primary", library="books", work_id="keep-book",
            destination="title", pre_value="old", desired_value="new",
            target_fingerprint="d" * 64, target_version="version-1",
            dependency_digests=(DEPENDENCY,),
            capability_digest=CAPABILITY,
        ))

    def test_verified_sibling_survives_failure_and_restart(self) -> None:
        first = self.plan(suffix="1")
        second = self.plan(destination="subtitle", suffix="2")
        self.assertEqual(self.service.apply(first.plan.plan_id).state, "verified")
        self.target.fail_next = True
        self.assertEqual(self.service.apply(second.plan.plan_id).state, "retryable")
        self.repository.close()
        self.repository = open_repository(Path(self.temporary.name) / "effects.sqlite", create=False, cross_thread=True)
        self.service._repository = self.repository
        self.assertEqual(self.service.status(first.plan.plan_id).state, "verified")
        self.assertEqual(self.service.apply(second.plan.plan_id).state, "verified")

    def test_plan_retry_ignores_nonidentity_creation_time(self) -> None:
        original = self.plan(suffix="9")
        plan = original.plan
        retried_plan = FieldEffectPlan.create(
            connection=plan.connection, library=plan.library,
            work_id=plan.work_id, destination=plan.destination,
            pre_value=plan.pre_value, desired_value=plan.desired_value,
            target_fingerprint=plan.target_fingerprint,
            target_version=plan.target_version,
            dependency_digests=plan.dependency_digests,
            authority=plan.authority, consent=plan.consent,
            capability_digest=plan.capability_digest,
            created_at=plan.created_at + 100,
        )
        self.assertEqual(retried_plan.plan_id, plan.plan_id)
        self.assertNotEqual(retried_plan.created_at, plan.created_at)
        reopened = self.repository.record_field_effect(
            FieldEffectOperation(
                retried_plan, updated_at=retried_plan.created_at
            )
        )
        self.assertEqual(reopened, original)
        self.assertEqual(self.repository._connection.execute(
            "SELECT COUNT(*) FROM field_effect_events WHERE plan_id=?",
            (plan.plan_id,),
        ).fetchone(), (1,))

    def test_record_rejects_every_noninitial_operation_without_audit_rows(self) -> None:
        source = self.plan(suffix="8").plan
        plan = FieldEffectPlan.create(
            connection=source.connection, library=source.library,
            work_id="unrecorded-book", destination=source.destination,
            pre_value=source.pre_value, desired_value=source.desired_value,
            target_fingerprint=source.target_fingerprint,
            target_version=source.target_version,
            dependency_digests=source.dependency_digests,
            authority=source.authority, consent=source.consent,
            capability_digest=source.capability_digest,
            created_at=source.created_at + 1,
        )
        initial = FieldEffectOperation(plan, updated_at=plan.created_at)
        invalid = (
            replace(initial, state="claimed", attempt=1, claim_token="a" * 64),
            replace(initial, attempt=1),
            replace(initial, failure="target_failure"),
            replace(initial, claim_token="b" * 64),
            replace(initial, updated_at=plan.created_at + 1),
        )
        for operation in invalid:
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(
                    FieldEffectError, "invalid_initial_operation"
                ):
                    self.repository.record_field_effect(operation)
                self.assertEqual(self.repository._connection.execute(
                    "SELECT COUNT(*) FROM field_effects WHERE plan_id=?",
                    (plan.plan_id,),
                ).fetchone(), (0,))
                self.assertEqual(self.repository._connection.execute(
                    "SELECT COUNT(*) FROM field_effect_events WHERE plan_id=?",
                    (plan.plan_id,),
                ).fetchone(), (0,))

    def test_fake_target_serializes_compare_and_write_for_direct_callers(self) -> None:
        first = self.plan(suffix="7").plan
        second = FieldEffectPlan.create(
            connection=first.connection, library=first.library,
            work_id=first.work_id, destination=first.destination,
            pre_value=first.pre_value, desired_value="competing-value",
            target_fingerprint=first.target_fingerprint,
            target_version=first.target_version,
            dependency_digests=first.dependency_digests,
            authority=first.authority, consent=first.consent,
            capability_digest=first.capability_digest,
            created_at=first.created_at,
        )
        entered = threading.Event()
        release = threading.Event()
        second_done = threading.Event()
        outcomes: dict[str, object] = {}

        def hold_first(_: FieldEffectPlan) -> None:
            entered.set()
            if not release.wait(2):
                raise RuntimeError("test release timeout")

        self.target.before_apply = hold_first
        first_thread = threading.Thread(
            target=lambda: outcomes.setdefault("first", self.target.apply(first))
        )

        def apply_second() -> None:
            outcomes["second"] = self.target.apply(second)
            second_done.set()

        second_thread = threading.Thread(target=apply_second)
        first_thread.start()
        self.assertTrue(entered.wait(1))
        second_thread.start()
        self.assertFalse(second_done.wait(0.1))
        release.set()
        first_thread.join(2)
        second_thread.join(2)
        self.assertFalse(first_thread.is_alive())
        self.assertFalse(second_thread.is_alive())
        self.assertEqual(outcomes["first"].outcome, "accepted")
        self.assertEqual(outcomes["second"].outcome, "conflict")

    def test_preclaim_failures_are_field_local_and_pause_is_atomic(self) -> None:
        stale = self.plan(suffix="3")
        ready = self.plan(destination="subtitle", suffix="4")
        self.dependencies.current_value = False
        self.assertEqual(self.service.apply(stale.plan.plan_id).failure, "dependency")
        self.dependencies.current_value = True
        self.service.set_paused(True)
        with self.assertRaises(Exception):
            self.service.apply(ready.plan.plan_id)
        self.assertEqual(self.service.status(ready.plan.plan_id).state, "planned")
        self.service.set_paused(False)
        self.assertEqual(self.service.apply(ready.plan.plan_id).state, "verified")

    def test_capability_failures_persist_the_smallest_scope_and_close_claim_races(self) -> None:
        title = self.plan(suffix="1")
        subtitle = self.plan(destination="subtitle", suffix="2")
        self.capabilities.category = "field_unavailable"
        self.assertEqual(self.service.apply(title.plan.plan_id).failure, "capability")
        self.assertEqual(
            self.repository.field_effect_capability_suspension("primary", "title"),
            "field_unavailable",
        )
        self.capabilities.category = "allowed"
        self.assertEqual(self.service.apply(subtitle.plan.plan_id).state, "verified")

        authors = self.plan(destination="authors", pre=["old"], desired=["new"], suffix="3")
        self.capabilities.category = "connection_unavailable"
        self.assertEqual(self.service.apply(authors.plan.plan_id).failure, "capability")
        self.capabilities.category = "allowed"
        genres = self.plan(destination="genres", pre=["old"], desired=["new"], suffix="4")
        self.assertEqual(self.service.apply(genres.plan.plan_id).failure, "capability")

    def test_atomic_claim_and_unexpected_effect_behavior_honor_durable_suspension(self) -> None:
        operation = self.plan(suffix="5")
        self.claim_hook.callback = lambda _: self.repository.suspend_field_effect_capability(
            scope="destination", connection="primary", destination="title",
            reason="field_unavailable", changed_at=self.clock(),
        )
        with self.assertRaises(Exception):
            self.service.apply(operation.plan.plan_id)
        self.assertEqual(self.service.status(operation.plan.plan_id).state, "planned")

        other = self.plan(destination="subtitle", suffix="6")
        self.target.ambiguous_next = "pre"
        self.target.suspension_scope_next = "destination"
        self.service.apply(other.plan.plan_id)
        self.assertEqual(
            self.repository.field_effect_capability_suspension("primary", "subtitle"),
            "unexpected_effect_behavior",
        )

    def test_final_target_read_persists_typed_connection_unavailability(self) -> None:
        operation = self.plan(suffix="8")
        self.target.unavailable_scope_next = "connection"
        result = self.service.apply(operation.plan.plan_id)
        self.assertEqual((result.state, result.failure), ("stale", "capability"))
        self.assertEqual(
            self.repository.field_effect_capability_suspension("primary", "title"),
            "connection_unavailable",
        )

    def test_verification_and_reconciliation_persist_typed_unexpected_behavior(self) -> None:
        operation = self.plan(suffix="9")

        def fail_verification(_: object) -> None:
            self.target.unavailable_scope_next = "destination"
            self.target.unavailable_reason_next = "unexpected_effect_behavior"

        self.target.after_apply = fail_verification
        result = self.service.apply(operation.plan.plan_id)
        self.assertEqual(result.state, "reconciling")
        self.assertEqual(
            self.repository.field_effect_capability_suspension("primary", "title"),
            "unexpected_effect_behavior",
        )
        self.target.unavailable_scope_next = "destination"
        self.target.unavailable_reason_next = "unexpected_effect_behavior"
        self.assertEqual(self.service.reconcile(operation.plan.plan_id).state, "retryable")

    def test_audit_documents_are_the_persisted_gap_free_event_sequence(self) -> None:
        operation = self.plan(suffix="7")
        verified = self.service.apply(operation.plan.plan_id)
        documents = self.repository.field_effect_event_documents(operation.plan.plan_id)
        events = [json.loads(document) for document in documents]
        self.assertEqual([event["sequence"] for event in events], [1, 2, 3, 4])
        self.assertEqual([event["kind"] for event in events],
                         ["planned", "claimed", "applying", "verified"])
        self.assertEqual(events[-1]["state"], verified.state)

    def test_ambiguous_desired_pre_and_third_reconcile_without_blind_retry(self) -> None:
        desired = self.plan(suffix="5")
        self.target.ambiguous_next = "desired"
        self.assertEqual(self.service.apply(desired.plan.plan_id).state, "verified")

        pre = self.plan(destination="subtitle", suffix="6")
        self.target.ambiguous_next = "pre"
        self.assertEqual(self.service.apply(pre.plan.plan_id).state, "retryable")
        self.assertEqual(self.service.apply(pre.plan.plan_id).state, "verified")

        third = self.plan(destination="publisher", suffix="7")
        self.target.ambiguous_next = "third"
        result = self.service.apply(third.plan.plan_id)
        self.assertEqual((result.state, result.failure), ("stale", "third_value"))
        actions = self.repository._connection.execute(
            "SELECT kind,state FROM field_effect_actions WHERE plan_id=? ORDER BY kind",
            (third.plan.plan_id,),
        ).fetchall()
        self.assertEqual(actions, [("forward_reevaluation", "pending"),
                                   ("targeted_read", "completed")])
        self.reevaluation.fail_next = True
        with self.assertRaisesRegex(RuntimeError, "reevaluation failure"):
            self.service.recover_pending()
        self.assertEqual(self.repository._connection.execute(
            "SELECT state FROM field_effect_actions WHERE plan_id=? "
            "AND kind='forward_reevaluation'", (third.plan.plan_id,),
        ).fetchone(), ("claimed",))
        self.repository.close()
        self.repository = open_repository(
            Path(self.temporary.name) / "effects.sqlite", create=False,
            cross_thread=True,
        )
        self.service._repository = self.repository
        self.assertEqual(self.service.recover_pending(), (1, 0))
        self.assertEqual(self.reevaluation.emitted, [third.plan.plan_id])
        self.assertEqual(self.repository._connection.execute(
            "SELECT state FROM field_effect_actions WHERE plan_id=? "
            "AND kind='forward_reevaluation'", (third.plan.plan_id,),
        ).fetchone(), ("completed",))

    def test_target_compare_and_set_rejects_a_race_without_overwrite(self) -> None:
        operation = self.plan(suffix="0")
        self.target.before_apply = lambda plan: self.target.drift(plan, "external")
        result = self.service.apply(operation.plan.plan_id)
        self.assertEqual((result.state, result.failure), ("stale", "target_changed"))
        self.assertEqual(self.target.read(operation.plan).value, "external")

    def test_planning_rejects_rebound_decision_coordinates_and_missing_consent(self) -> None:
        selected = envelope("title", "new")
        source = decision("title", selected, "f")
        self.authorize(source)
        cases = (
            {"destination": "subtitle"}, {"work_id": "other-work"},
            {"target_fingerprint": "e" * 64}, {"target_version": "version-2"},
        )
        base = {"connection": "primary", "library": "books", "work_id": "book-1",
                "destination": "title", "pre_value": "old", "desired_value": selected,
                "target_fingerprint": "d" * 64, "target_version": "version-1",
                "dependency_digests": (DEPENDENCY,), "capability_digest": CAPABILITY}
        for change in cases:
            with self.subTest(change=change):
                with self.assertRaisesRegex(Exception, "authority_binding"):
                    self.service.plan(source, **(base | change))
        unconsented = decision("publisher", envelope("publisher", "new"), "0")
        self.authority.bind(
            unconsented, connection="primary", library="books", work_id="book-1",
            target_version="version-1",
        )
        with self.assertRaisesRegex(Exception, "consent"):
            self.service.plan(
                unconsented, connection="primary", library="books", work_id="book-1",
                destination="publisher", pre_value="old",
                desired_value=envelope("publisher", "new"),
                target_fingerprint="d" * 64, target_version="version-1",
                dependency_digests=(DEPENDENCY,), capability_digest=CAPABILITY,
            )

    def test_repository_authority_and_consent_revocation_win_the_claim_race(self) -> None:
        authority = self.plan(suffix="1")
        self.claim_hook.callback = lambda plan: self.repository.revoke_field_effect_authority(
            plan.authority.decision_id, self.clock()
        )
        with self.assertRaisesRegex(Exception, "claim_conflict"):
            self.service.apply(authority.plan.plan_id)
        self.assertEqual(self.service.status(authority.plan.plan_id).state, "superseded")
        self.assertEqual(self.target.read(authority.plan).value, authority.plan.pre_value)

        consent = self.plan(destination="subtitle", suffix="2")
        self.claim_hook.callback = lambda plan: self.repository.revoke_field_effect_consent(
            plan.consent.consent_id, self.clock()
        )
        with self.assertRaisesRegex(Exception, "claim_conflict"):
            self.service.apply(consent.plan.plan_id)
        self.assertEqual(self.service.status(consent.plan.plan_id).state, "superseded")
        self.assertEqual(self.target.read(consent.plan).value, consent.plan.pre_value)

    def test_semantic_consent_drift_wins_the_atomic_claim_race(self) -> None:
        operation = self.plan(suffix="e")
        repository_type = type(self.repository)
        with patch.object(
            repository_type,
            "field_effect_consent_envelope_identity",
            return_value="f" * 64,
        ):
            with self.assertRaisesRegex(Exception, "claim_conflict"):
                self.service.apply(operation.plan.plan_id)
        self.assertEqual(self.service.status(operation.plan.plan_id).state, "planned")
        self.assertEqual(
            self.target.read(operation.plan).value, operation.plan.pre_value
        )

    def test_newer_report_wins_claim_before_case_retirement(self) -> None:
        report = metadata_report("Old title", "1", "2")
        self.repository.publish(report, lambda: True)
        proposal = evaluate_report(report)
        self.repository.publish_proposal(proposal)
        case_id = json.loads(self.repository.admit_review_case(proposal))["review_case_id"]
        case = ReviewCase.create_from_data(
            json.loads(self.repository.get_review_case(case_id))["case"]
        )
        candidate = next(
            item for item in case.proposals
            if item["result"]["outcome"] == "unresolved_choice"
        )
        selected = candidate["result"]["alternatives"][0]["value"]
        source = HumanDecision.create(
            case,
            proposal_id=candidate["proposal_id"],
            target_fingerprint=case.target_fingerprint,
            destination=candidate["destination"],
            actor="owner",
            outcome="accept",
            selected_value=selected,
            idempotency_key="field-effect-newer-report-race",
            occurred_at=1,
        )
        self.repository.record_human_decision(source)
        self.authority.bind(
            source, connection="primary", library="books",
            work_id=report.target.item,
            target_version=case.target_fingerprint,
            source="persisted_review_case",
        )
        self.consent.allow(
            connection="primary", library="books", destination=source.destination
        )
        operation = self.service.plan(
            source, connection="primary", library="books",
            work_id=report.target.item, destination=source.destination,
            pre_value="Old title", desired_value=selected,
            target_fingerprint=case.target_fingerprint,
            target_version=case.target_fingerprint,
            dependency_digests=(DEPENDENCY,), capability_digest=CAPABILITY,
        )
        assert operation is not None
        self.target.seed(operation.plan)
        newer = metadata_report("New title", "3", "4")
        self.claim_hook.callback = lambda _: self.repository.publish(
            newer, lambda: True
        )
        with self.assertRaisesRegex(Exception, "claim_conflict"):
            self.service.apply(operation.plan.plan_id)
        self.assertEqual(self.service.status(operation.plan.plan_id).state, "planned")
        self.assertEqual(self.target.read(operation.plan).value, "Old title")

    def test_accepted_readback_mismatch_schedules_durable_targeted_read(self) -> None:
        operation = self.plan(suffix="4")
        self.target.after_apply = lambda plan: self.target.drift(
            plan, "synthetic-third-value"
        )
        result = self.service.apply(operation.plan.plan_id)
        self.assertEqual((result.state, result.failure), ("reconciling", "readback"))
        self.assertEqual(self.repository._connection.execute(
            "SELECT state FROM field_effect_actions WHERE plan_id=? "
            "AND kind='targeted_read'", (operation.plan.plan_id,),
        ).fetchone(), ("pending",))
        self.repository.close()
        self.repository = open_repository(
            Path(self.temporary.name) / "effects.sqlite", create=False,
            cross_thread=True,
        )
        self.service._repository = self.repository
        self.assertEqual(self.service.recover_pending(), (2, 0))
        self.assertEqual(self.service.status(operation.plan.plan_id).state, "stale")

    def test_applying_restart_schedules_targeted_read_before_recovery(self) -> None:
        operation = self.plan(suffix="5")
        claimed = self.repository.claim_field_effect(
            operation.plan.plan_id, "planned", "a" * 64, self.clock()
        )
        self.repository.transition_field_effect(
            operation.plan.plan_id, claimed.state, "applying", self.clock()
        )
        self.recovery_hook.callback = lambda _: (_ for _ in ()).throw(
            RuntimeError("synthetic restart")
        )
        with self.assertRaisesRegex(RuntimeError, "synthetic restart"):
            self.service.apply(operation.plan.plan_id)
        self.assertEqual(self.repository._connection.execute(
            "SELECT state FROM field_effect_actions WHERE plan_id=? "
            "AND kind='targeted_read'", (operation.plan.plan_id,),
        ).fetchone(), ("pending",))
        self.repository.close()
        self.repository = open_repository(
            Path(self.temporary.name) / "effects.sqlite", create=False,
            cross_thread=True,
        )
        self.service._repository = self.repository
        self.assertEqual(self.service.recover_pending(), (1, 0))
        self.assertEqual(self.service.status(operation.plan.plan_id).state, "retryable")

    def test_persisted_targeted_read_reopens_after_restart(self) -> None:
        operation = self.plan(suffix="3")
        self.target.ambiguous_next = "desired"
        self.recovery_hook.callback = lambda _: (_ for _ in ()).throw(
            RuntimeError("synthetic restart")
        )
        with self.assertRaisesRegex(RuntimeError, "synthetic restart"):
            self.service.apply(operation.plan.plan_id)
        self.assertEqual(self.service.status(operation.plan.plan_id).state, "reconciling")
        self.repository.close()
        self.repository = open_repository(
            Path(self.temporary.name) / "effects.sqlite", create=False, cross_thread=True
        )
        self.service._repository = self.repository
        self.assertEqual(self.service.recover_pending(), (1, 0))
        self.assertEqual(self.service.status(operation.plan.plan_id).state, "verified")
        self.assertEqual(self.repository._connection.execute(
            "SELECT state,attempt FROM field_effect_actions WHERE plan_id=? AND kind='targeted_read'",
            (operation.plan.plan_id,),
        ).fetchone(), ("completed", 1))

    def test_final_preclaim_refresh_blocks_guard_drift_and_leaves_siblings_local(self) -> None:
        cases = (
            ("pause", lambda plan: self.service.set_paused(True)),
            ("authority", lambda plan: self.authority.revoked.add(plan.authority.decision_id)),
            ("capability", lambda plan: setattr(self.capabilities, "digest", "e" * 64)),
            ("dependency", lambda plan: setattr(self.dependencies, "current_value", False)),
        )
        for index, (failure, mutate) in enumerate(cases, start=1):
            with self.subTest(failure=failure):
                operation = self.plan(destination="subtitle", suffix=format(index, "x"))
                self.preclaim.callback = mutate
                if failure == "pause":
                    with self.assertRaisesRegex(Exception, "paused"):
                        self.service.apply(operation.plan.plan_id)
                    result = self.service.status(operation.plan.plan_id)
                    self.service.set_paused(False)
                    self.assertEqual((result.state, result.attempt), ("planned", 0))
                else:
                    result = self.service.apply(operation.plan.plan_id)
                    self.assertEqual(
                        (result.state, result.failure, result.attempt),
                        ("stale", failure, 0),
                    )
                self.assertEqual(self.target.read(operation.plan).value, operation.plan.pre_value)
                self.capabilities.digest = CAPABILITY
                self.dependencies.current_value = True

        sibling = self.plan(destination="publisher", suffix="e")
        self.assertEqual(self.service.apply(sibling.plan.plan_id).state, "verified")

    def test_supersession_does_not_interrupt_claimed_and_cover_bytes_are_private(self) -> None:
        planned = self.plan(suffix="8")
        self.assertEqual(self.service.supersede(planned.plan.authority.decision_id), 1)
        self.assertEqual(self.service.status(planned.plan.plan_id).state, "superseded")

        claimed = self.plan(destination="subtitle", suffix="a")
        self.repository.claim_field_effect(
            claimed.plan.plan_id, "planned", "1" * 64, self.clock()
        )
        self.assertEqual(self.service.supersede(claimed.plan.authority.decision_id), 0)
        self.assertEqual(self.service.status(claimed.plan.plan_id).state, "claimed")
        self.assertEqual(self.service.apply(claimed.plan.plan_id).state, "verified")

        cover_value = "cover_" + "9" * 64
        cover = self.plan("cover", None, cover_value, "9")
        self.assertEqual(self.service.apply(cover.plan.plan_id).state, "verified")
        self.assertFalse(self.recovery.exists(cover.plan.plan_id))
        rows = self.repository._connection.execute(
            "SELECT document FROM field_effect_events WHERE plan_id=?",
            (cover.plan.plan_id,),
        ).fetchall()
        self.assertNotIn("old-cover", repr(rows))

    def test_retry_exhaustion_opens_the_field_circuit_without_rolling_back_siblings(self) -> None:
        operation = self.plan(suffix="c")
        for attempt in range(3):
            self.target.fail_next = True
            result = self.service.apply(operation.plan.plan_id)
            self.assertEqual((result.state, result.attempt), ("retryable", attempt + 1))
        result = self.service.apply(operation.plan.plan_id)
        self.assertEqual((result.state, result.failure), ("stale", "circuit_open"))

    def test_effect_stages_emit_advisory_timing_and_drain_rejects_only_new_claims(self) -> None:
        timings = TimingCollector()
        ticks = iter(range(0, 100_000_000, 1_000_000))
        self.service.attach_timing(StageTimer(
            timings, monotonic_ns=lambda: next(ticks), clock=lambda: 99
        ))
        verified = self.plan(suffix="1")
        self.assertEqual(self.service.apply(verified.plan.plan_id).state, "verified")
        ambiguous = self.plan(destination="publisher", suffix="2")
        self.target.ambiguous_next = "desired"
        self.assertEqual(self.service.apply(ambiguous.plan.plan_id).state, "verified")
        self.assertEqual(
            [(value.stage, value.wait_class) for value in timings.values],
            [
                ("apply", "target"),
                ("verification", "target"),
                ("apply", "target"),
                ("reconciliation", "recovery"),
            ],
        )

        planned = self.plan(destination="subtitle", suffix="3")
        self.service.begin_drain()
        with self.assertRaisesRegex(Exception, "draining"):
            self.service.apply(planned.plan.plan_id)
        self.assertEqual(self.service.status(planned.plan.plan_id).state, "planned")

    def test_drain_closes_the_final_check_to_atomic_claim_race(self) -> None:
        operation = self.plan(suffix="4")
        hook_entered = threading.Event()
        release_hook = threading.Event()
        failures = []

        def block_before_final_admission(_):
            hook_entered.set()
            self.assertTrue(release_hook.wait(2))

        self.claim_hook.callback = block_before_final_admission

        def apply() -> None:
            try:
                self.service.apply(operation.plan.plan_id)
            except Exception as error:
                failures.append(error)

        worker = threading.Thread(target=apply)
        worker.start()
        self.assertTrue(hook_entered.wait(1))
        self.service.begin_drain()
        release_hook.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(
            [getattr(error, "code", None) for error in failures], ["draining"]
        )
        self.assertEqual(
            self.service.status(operation.plan.plan_id).state, "planned"
        )
        self.assertEqual(self.target.read(operation.plan).value, operation.plan.pre_value)

    def test_startup_recovers_claimed_applying_and_reconciling_checkpoints(self) -> None:
        claimed = self.plan(suffix="1")
        self.repository.claim_field_effect(
            claimed.plan.plan_id, "planned", "1" * 64, self.clock()
        )
        applying = self.plan(destination="subtitle", suffix="2")
        self.repository.claim_field_effect(
            applying.plan.plan_id, "planned", "2" * 64, self.clock()
        )
        self.repository.transition_field_effect(
            applying.plan.plan_id, "claimed", "applying", self.clock()
        )
        reconciling = self.plan(destination="publisher", suffix="3")
        self.repository.claim_field_effect(
            reconciling.plan.plan_id, "planned", "3" * 64, self.clock()
        )
        self.repository.transition_field_effect(
            reconciling.plan.plan_id, "claimed", "applying", self.clock()
        )
        self.repository.schedule_field_effect_reconciliation(
            reconciling.plan.plan_id, "applying", self.clock()
        )

        self.assertEqual(self.service.start(), (3, 0, 0))
        self.assertEqual(self.service.status(claimed.plan.plan_id).state, "verified")
        self.assertEqual(self.service.status(applying.plan.plan_id).state, "retryable")
        self.assertEqual(self.service.status(reconciling.plan.plan_id).state, "retryable")

    def test_startup_contains_reconciliation_read_failure_and_continues_siblings(self) -> None:
        first = self.plan(destination="subtitle", suffix="5")
        second = self.plan(destination="publisher", suffix="6")
        for operation, token in ((first, "5"), (second, "6")):
            self.repository.claim_field_effect(
                operation.plan.plan_id, "planned", token * 64, self.clock()
            )
            self.repository.transition_field_effect(
                operation.plan.plan_id, "claimed", "applying", self.clock()
            )
            self.repository.schedule_field_effect_reconciliation(
                operation.plan.plan_id, "applying", self.clock()
            )
        self.target.fail_read_next = True

        self.assertEqual(self.service.start(), (2, 0, 0))
        failed = self.service.status(first.plan.plan_id)
        sibling = self.service.status(second.plan.plan_id)
        self.assertEqual((failed.state, failed.failure), ("retryable", "target_failure"))
        self.assertEqual((sibling.state, sibling.failure), ("retryable", "not_applied"))

    def test_startup_contains_verification_read_failure_after_successful_apply(self) -> None:
        first = self.plan(destination="subtitle", suffix="7")
        sibling = self.plan(destination="publisher", suffix="8")
        for operation, token in ((first, "7"), (sibling, "8")):
            self.repository.claim_field_effect(
                operation.plan.plan_id, "planned", token * 64, self.clock()
            )

        self.target.fail_read_after_apply_for.add(first.plan.plan_id)

        self.assertEqual(self.service.start(), (2, 1, 0))
        failed = self.service.status(first.plan.plan_id)
        continued = self.service.status(sibling.plan.plan_id)
        self.assertEqual(
            (failed.state, failed.failure), ("retryable", "target_failure")
        )
        self.assertEqual((continued.state, continued.failure), ("verified", None))
        recovery_signal = next(
            value
            for value in self.repository.operational_snapshot()["signals"]
            if value["work_id"] == first.plan.work_id
            and value["field"] == first.plan.destination
        )
        self.assertEqual(
            (recovery_signal["kind"], recovery_signal["cause"]),
            ("automatic_recovery", "in_progress"),
        )
        self.target.fail_reads_for.remove(first.plan.plan_id)
        self.assertEqual(
            self.target.read(first.plan).value, first.plan.desired_value
        )

    def test_shared_backup_and_service_failures_project_above_field_scope(self) -> None:
        failures = (
            (self.plan(destination="title", suffix="a"), "backup"),
            (self.plan(destination="subtitle", suffix="b"), "backup"),
            (self.plan(destination="publisher", suffix="c"), "capability"),
            (self.plan(destination="description", suffix="d"), "capability"),
            (self.plan(destination="isbn", suffix="e"), "third_value"),
            (self.plan(destination="asin", suffix="f"), "third_value"),
        )
        for operation, failure in failures:
            self.repository.transition_field_effect(
                operation.plan.plan_id,
                "planned",
                "stale",
                self.clock(),
                failure,
            )

        signals = self.repository.operational_snapshot()["signals"]
        backup = [value for value in signals if value["kind"] == "backup_failure"]
        service = [
            value for value in signals
            if value["kind"] == "service_incompatibility"
        ]
        currentness = [
            value for value in signals
            if value["cause"] == "currentness_conflict"
        ]
        self.assertEqual(
            {(value["scope"], value["library"], value["work_id"], value["field"])
             for value in backup},
            {("library", "books", None, None)},
        )
        self.assertEqual(
            {(value["scope"], value["library"], value["work_id"], value["field"])
             for value in service},
            {("connection", None, None, None)},
        )
        self.assertEqual(
            {(value["scope"], value["work_id"], value["field"])
             for value in currentness},
            {("field", "book-1", "isbn"), ("field", "book-1", "asin")},
        )
        incidents = aggregate_incidents(signals)
        self.assertEqual(
            sorted(
                (value.kind, value.count)
                for value in incidents
                if value.kind in {
                    "backup_failure", "service_incompatibility",
                    "owner_action_required",
                }
            ),
            [
                ("backup_failure", 2),
                ("owner_action_required", 1),
                ("owner_action_required", 1),
                ("service_incompatibility", 2),
            ],
        )

    def test_cover_recovery_survives_restart_and_rejects_permission_drift(self) -> None:
        cover = self.plan(
            "cover", None, "cover_" + "7" * 64, "d"
        )
        self.recovery.capture(cover.plan)
        restarted = PrivateCoverRecoveryStore(
            Path(self.temporary.name) / "recovery",
            lambda _: (_ for _ in ()).throw(RuntimeError("must not reread")),
        )
        temporary_file = next((Path(self.temporary.name) / "recovery").iterdir()).with_suffix(
            ".recovery.tmp"
        )
        temporary_file.write_bytes(b"incomplete")
        os.chmod(temporary_file, 0o600)
        restarted.capture(cover.plan)
        self.assertFalse(temporary_file.exists())
        recovery_file = next((Path(self.temporary.name) / "recovery").iterdir())
        os.chmod(recovery_file, 0o644)
        with self.assertRaises(ValueError):
            restarted.capture(cover.plan)
        self.assertFalse(recovery_file.exists())

        absent_root = Path(self.temporary.name) / "absent-recovery"
        absent = PrivateCoverRecoveryStore(absent_root, lambda _: None)
        absent.capture(cover.plan)
        self.assertEqual(next(absent_root.iterdir()).read_bytes(), b"ABSENT\n")

    def test_cover_terminal_cleanup_obligation_survives_failure_and_restart(self) -> None:
        cover = self.plan("cover", None, "cover_" + "6" * 64, "4")
        self.recovery.fail_remove_next = True
        self.assertEqual(self.service.apply(cover.plan.plan_id).state, "verified")
        self.assertTrue(self.recovery.exists(cover.plan.plan_id))
        self.assertTrue(self.repository.field_effect_cleanup_pending(cover.plan.plan_id))
        self.assertEqual(self.repository._connection.execute(
            "SELECT state,attempt FROM field_effect_cleanups WHERE plan_id=?",
            (cover.plan.plan_id,),
        ).fetchone(), ("pending", 1))
        self.repository.close()
        self.repository = open_repository(
            Path(self.temporary.name) / "effects.sqlite", create=False, cross_thread=True
        )
        self.service._repository = self.repository
        self.assertEqual(self.service.recover_pending(), (0, 1))
        self.assertEqual(self.service.status(cover.plan.plan_id).state, "verified")
        self.assertFalse(self.recovery.exists(cover.plan.plan_id))
        self.assertFalse(self.repository.field_effect_cleanup_pending(cover.plan.plan_id))

    def test_failing_restart_action_cannot_starve_pending_cover_cleanup(self) -> None:
        third = self.plan(destination="publisher", suffix="3")
        self.target.ambiguous_next = "third"
        self.assertEqual(self.service.apply(third.plan.plan_id).state, "stale")

        cover = self.plan("cover", None, "cover_" + "7" * 64, "7")
        self.recovery.fail_remove_next = True
        self.assertEqual(self.service.apply(cover.plan.plan_id).state, "verified")
        self.assertTrue(self.repository.field_effect_cleanup_pending(cover.plan.plan_id))

        self.reevaluation.fail_next = True
        with self.assertRaisesRegex(RuntimeError, "reevaluation failure"):
            self.service.recover_pending()
        self.assertFalse(self.recovery.exists(cover.plan.plan_id))
        self.assertFalse(self.repository.field_effect_cleanup_pending(cover.plan.plan_id))
        self.assertEqual(self.repository._connection.execute(
            "SELECT state FROM field_effect_actions WHERE plan_id=? "
            "AND kind='forward_reevaluation'", (third.plan.plan_id,),
        ).fetchone(), ("claimed",))

        self.assertEqual(self.service.recover_pending(), (1, 0))
        self.assertEqual(self.reevaluation.emitted, [third.plan.plan_id])

    def test_supersede_runs_cover_cleanup_and_retains_a_failed_obligation(self) -> None:
        cover = self.plan("cover", None, "cover_" + "5" * 64, "8")
        self.recovery.capture(cover.plan)
        self.recovery.fail_remove_next = True
        self.assertEqual(self.service.supersede(cover.plan.authority.decision_id), 1)
        self.assertTrue(self.repository.field_effect_cleanup_pending(cover.plan.plan_id))
        self.assertTrue(self.recovery.exists(cover.plan.plan_id))
        self.assertEqual(self.service.recover_pending(), (0, 1))
        self.assertFalse(self.recovery.exists(cover.plan.plan_id))

    def test_exact_current_schema_rejects_drift(self) -> None:
        self.repository.close()
        path = Path(self.temporary.name) / "effects.sqlite"
        connection = sqlite3.connect(path)
        connection.execute("ALTER TABLE field_effects ADD COLUMN drift TEXT")
        connection.commit()
        connection.close()
        with self.assertRaises(Exception):
            open_repository(path, create=False)
        self.repository = open_repository(Path(self.temporary.name) / "fresh.sqlite")

    def test_existing_database_rejects_extra_operation_members_without_mutation(self) -> None:
        operation = self.plan(suffix="5")
        raw = operation.to_data()
        raw["legacy"] = True
        self._corrupt_and_reject(
            "UPDATE field_effects SET document=? WHERE plan_id=?",
            (json.dumps(raw), operation.plan.plan_id),
        )

    def test_existing_database_rejects_row_document_mismatch(self) -> None:
        operation = self.plan(suffix="6")
        self._corrupt_and_reject(
            "UPDATE field_effects SET work_id='other-work' WHERE plan_id=?",
            (operation.plan.plan_id,),
        )

    def test_existing_database_rejects_malformed_persisted_action(self) -> None:
        operation = self.plan(suffix="7")
        self.target.ambiguous_next = "desired"
        self.service.apply(operation.plan.plan_id)
        row = self.repository._connection.execute(
            "SELECT action_id,document FROM field_effect_actions WHERE plan_id=?",
            (operation.plan.plan_id,),
        ).fetchone()
        raw = json.loads(row[1])
        raw["unknown"] = "legacy"
        self._corrupt_and_reject(
            "UPDATE field_effect_actions SET document=? WHERE action_id=?",
            (json.dumps(raw), row[0]),
        )

    def test_existing_database_rejects_boolean_action_attempt(self) -> None:
        operation = self.plan(suffix="9")
        self.target.ambiguous_next = "desired"
        self.service.apply(operation.plan.plan_id)
        row = self.repository._connection.execute(
            "SELECT action_id,document FROM field_effect_actions WHERE plan_id=?",
            (operation.plan.plan_id,),
        ).fetchone()
        raw = json.loads(row[1])
        raw["attempt"] = True
        self._corrupt_and_reject(
            "UPDATE field_effect_actions SET document=? WHERE action_id=?",
            (json.dumps(raw), row[0]),
        )

    def test_existing_database_rejects_boolean_event_sequence(self) -> None:
        operation = self.plan(suffix="a")
        path = Path(self.temporary.name) / "effects.sqlite"
        self.repository.close()
        connection = sqlite3.connect(path)
        trigger = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name='field_effect_events_no_update'"
        ).fetchone()[0]
        connection.execute("DROP TRIGGER field_effect_events_no_update")
        row = connection.execute(
            "SELECT event_id,document FROM field_effect_events WHERE plan_id=?",
            (operation.plan.plan_id,),
        ).fetchone()
        raw = json.loads(row[1])
        raw["sequence"] = True
        connection.execute(
            "UPDATE field_effect_events SET document=? WHERE event_id=?",
            (json.dumps(raw), row[0]),
        )
        connection.execute(trigger)
        connection.commit()
        connection.close()
        with self.assertRaises(Exception):
            open_repository(path, create=False)
        self.repository = open_repository(Path(self.temporary.name) / "fresh.sqlite")
        self.service._repository = self.repository

    def _corrupt_and_reject(self, statement: str, parameters: tuple[object, ...]) -> None:
        path = Path(self.temporary.name) / "effects.sqlite"
        self.repository.close()
        connection = sqlite3.connect(path)
        connection.execute(statement, parameters)
        connection.commit()
        connection.close()
        with self.assertRaises(Exception):
            open_repository(path, create=False)
        self.repository = open_repository(Path(self.temporary.name) / "fresh.sqlite")
        self.service._repository = self.repository

    def test_authenticated_api_uses_the_same_service_and_blocks_anonymous_mutation(self) -> None:
        operation = self.plan(suffix="b")
        root = Path(self.temporary.name)
        password = root / "owner.hash"
        password.write_text(PasswordHasher().hash("correct horse"), encoding="utf-8")
        os.chmod(password, 0o600)
        app = create_transport_test_app(
            self.repository,
            ReviewWebConfiguration(
                canonical_origin="https://review.test",
                owner_password_hash_file=str(password),
                session_lifetime_seconds=600,
            ),
            clock=self.clock,
            field_application=self.service,
        )
        with TestClient(app, base_url="https://review.test") as client:
            route = f"/api/v1/field-effects/{operation.plan.plan_id}/apply"
            self.assertEqual(client.post(route, headers={"Origin": "https://review.test"}).status_code, 401)
            login = client.post(
                "/api/v1/session/login", json={"password": "correct horse"},
                headers={"Origin": "https://review.test"},
            )
            self.assertEqual(login.status_code, 200)
            response = client.post(route, headers={
                "Origin": "https://review.test",
                "X-CSRF-Token": login.json()["csrf_token"],
                "x-ml-build-identity": TEST_REACT_BUILD_IDENTITY,
            })
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["state"], "verified")
            status = client.get(f"/api/v1/field-effects/{operation.plan.plan_id}")
            self.assertEqual(status.json()["state"], "verified")

    def test_web_pause_and_resume_bound_persistence_failures(self) -> None:
        repository = self.repository

        class FailingControlRepository:
            def __getattr__(self, name: str):
                return getattr(repository, name)

            def set_field_effect_paused(self, paused: bool, changed_at: int) -> None:
                raise FieldEffectError("persistence")

        failing = FailingControlRepository()
        self.service._repository = failing
        root = Path(self.temporary.name)
        password = root / "failing-control-owner.hash"
        password.write_text(PasswordHasher().hash("correct horse"), encoding="utf-8")
        os.chmod(password, 0o600)
        app = create_transport_test_app(
            failing,
            ReviewWebConfiguration(
                canonical_origin="https://review.test",
                owner_password_hash_file=str(password),
                session_lifetime_seconds=600,
            ),
            clock=self.clock,
            field_application=self.service,
        )
        with TestClient(app, base_url="https://review.test") as client:
            login = client.post(
                "/api/v1/session/login", json={"password": "correct horse"},
                headers={"Origin": "https://review.test"},
            )
            self.assertEqual(login.status_code, 200)
            headers = {
                "Origin": "https://review.test",
                "X-CSRF-Token": login.json()["csrf_token"],
                "x-ml-build-identity": TEST_REACT_BUILD_IDENTITY,
            }
            for action in ("pause", "resume"):
                with self.subTest(action=action):
                    response = client.post(
                        f"/api/v1/field-effects/{action}", headers=headers
                    )
                    self.assertEqual(response.status_code, 503)
                    self.assertEqual(response.json(), {
                        "error": {"code": "persistence"},
                        "schema_version": "review-error-v1",
                    })

    def test_web_field_effect_and_review_reads_share_one_repository_lock(self) -> None:
        operation = self.plan(suffix="c")
        entered = threading.Event()
        release = threading.Event()
        library_entered = threading.Event()
        repository = self.repository

        class BlockingRepository:
            def __getattr__(self, name: str):
                return getattr(repository, name)

            def get_field_effect(self, plan_id: str):
                entered.set()
                if not release.wait(2):
                    raise RuntimeError("test release timeout")
                return repository.get_field_effect(plan_id)

            def read_review_library(self, search, limit, offset, attention):
                library_entered.set()
                return repository.read_review_library(search, limit, offset, attention)

        blocking = BlockingRepository()
        self.service._repository = blocking
        root = Path(self.temporary.name)
        password = root / "serialized-owner.hash"
        password.write_text(PasswordHasher().hash("correct horse"), encoding="utf-8")
        os.chmod(password, 0o600)
        app = create_transport_test_app(
            blocking,
            ReviewWebConfiguration(
                canonical_origin="https://review.test",
                owner_password_hash_file=str(password),
                session_lifetime_seconds=600,
            ),
            clock=self.clock,
            field_application=self.service,
        )
        responses: dict[str, Any] = {}
        with TestClient(app, base_url="https://review.test") as client:
            login = client.post(
                "/api/v1/session/login", json={"password": "correct horse"},
                headers={"Origin": "https://review.test"},
            )
            self.assertEqual(login.status_code, 200)

            status_thread = threading.Thread(
                target=lambda: responses.setdefault(
                    "status", client.get(
                        f"/api/v1/field-effects/{operation.plan.plan_id}"
                    )
                )
            )
            library_thread = threading.Thread(
                target=lambda: responses.setdefault(
                    "library", client.get("/api/v1/library")
                )
            )
            status_thread.start()
            self.assertTrue(entered.wait(1))
            library_thread.start()
            self.assertFalse(library_entered.wait(0.1))
            release.set()
            status_thread.join(2)
            library_thread.join(2)
            self.assertFalse(status_thread.is_alive())
            self.assertFalse(library_thread.is_alive())
            self.assertTrue(library_entered.is_set())
            self.assertEqual(responses["status"].status_code, 200)
            self.assertEqual(responses["library"].status_code, 200)


class FieldEffectDomainTests(unittest.TestCase):
    def test_service_tokens_accept_realistic_opaque_ids_but_remain_bounded(self) -> None:
        authority = EffectAuthority(
            "human_decision_" + "a" * 64, "review_case_" + "b" * 64,
            "c" * 64, "fake_service", "human", "candidate", None, None,
        )
        consent = EffectConsent.create(
            connection="primary", library="books", destination="title",
            envelope_identity="a" * 64, granted_at=1
        )

        def create(work_id: object, target_version: object) -> FieldEffectPlan:
            return FieldEffectPlan.create(
                connection="primary", library="books", work_id=work_id,
                destination="title", pre_value="Old", desired_value="New",
                target_fingerprint="c" * 64, target_version=target_version,
                dependency_digests=(DEPENDENCY,), authority=authority,
                consent=consent, capability_digest=CAPABILITY, created_at=1,
            )

        plan = create("65f12ab90d42c7e81934beef/+opaque", "d" * 64)
        self.assertEqual(plan.work_id, "65f12ab90d42c7e81934beef/+opaque")
        self.assertEqual(plan.target_version, "d" * 64)
        for work_id, target_version in (
            ("item\nheader", "v1"),
            ("item\x00suffix", "v1"),
            ("x" * 1025, "v1"),
            ("item-1", "v" * 1025),
            ("item-1", "   "),
            ("item-1", "\ud800"),
            (True, "v1"),
        ):
            with self.subTest(work_id=work_id, target_version=target_version):
                with self.assertRaises(Exception):
                    create(work_id, target_version)

    def test_series_is_one_atomic_destination_and_state_machine_rejects_skips(self) -> None:
        authority = EffectAuthority(
            "human_decision_" + "a" * 64, "review_case_" + "b" * 64,
            "c" * 64, "fake_service", "human", "candidate", None, None,
        )
        consent = EffectConsent.create(
            connection="primary", library="books", destination="series",
            envelope_identity="a" * 64, granted_at=1
        )
        plan = FieldEffectPlan.create(
            connection="primary", library="books", work_id="book-1",
            destination="series", pre_value=[{"name": "Old", "sequence": "1"}],
            desired_value=[{"name": "New", "sequence": "2"}],
            target_fingerprint="c" * 64, target_version="v1",
            dependency_digests=(DEPENDENCY,), authority=authority,
            consent=consent, capability_digest=CAPABILITY, created_at=1,
        )
        operation = FieldEffectOperation(plan, updated_at=1)
        with self.assertRaises(Exception):
            operation.transition("verified", at=2)

        for member, invalid in (
            ("attempt", True), ("updated_at", True),
            ("failure", False), ("claim_token", 1),
        ):
            with self.subTest(member=member):
                raw = operation.to_data()
                raw[member] = invalid
                with self.assertRaises(Exception):
                    FieldEffectOperation.from_json(json.dumps(raw))
        raw = operation.to_data()
        raw["plan"]["created_at"] = True
        with self.assertRaises(Exception):
            FieldEffectOperation.from_json(json.dumps(raw))
        raw = operation.to_data()
        raw["plan"]["consent"]["granted_at"] = True
        with self.assertRaises(Exception):
            FieldEffectOperation.from_json(json.dumps(raw))


if __name__ == "__main__":
    unittest.main()
