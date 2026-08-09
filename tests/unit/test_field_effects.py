from __future__ import annotations

import os
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

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
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.field_effects import GuardedFieldApplication
from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.interfaces.web.app import create_review_app
from mechanical_librarian.domain.field_effect import (
    EffectAuthority,
    EffectConsent,
    FieldEffectOperation,
    FieldEffectPlan,
    SUPPORTED_DESTINATIONS,
)
from mechanical_librarian.domain.human_decision import HumanDecision
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


class FieldEffectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        os.chmod(root, 0o700)
        self.repository = open_repository(root / "effects.sqlite", cross_thread=True)
        self.target = FakeTargetService()
        self.authority = FakeAuthorityCurrentness()
        self.dependencies = FakeDependencyCurrentness()
        self.backup = FakeBackupReadiness()
        self.capabilities = FakeCapabilityObserver(CAPABILITY)
        self.consent = FakeConsentCurrentness()
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

    def test_current_review_lineage_replacement_wins_the_claim_race(self) -> None:
        selected = envelope("title", "new")
        source = decision("title", selected, "4")
        self.authority.bind(
            source, connection="primary", library="books", work_id="book-1",
            target_version="version-1", source="persisted_review_case",
        )
        self.consent.allow(connection="primary", library="books", destination="title")
        operation = self.service.plan(
            source, connection="primary", library="books", work_id="book-1",
            destination="title", pre_value="old", desired_value=selected,
            target_fingerprint="d" * 64, target_version="version-1",
            dependency_digests=(DEPENDENCY,), capability_digest=CAPABILITY,
        )
        assert operation is not None
        self.target.seed(operation.plan)
        connection = self.repository._connection
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "INSERT INTO review_cases(review_case_id,proposal_set_id,report_id,document) VALUES(?,?,?,?)",
            (source.review_case_id, "proposal_set_" + "1" * 64,
             "report_" + "2" * 64,
             json.dumps({"target_fingerprint": source.target_fingerprint})),
        )
        connection.execute(
            "INSERT INTO current_review_case_lineages(connection_name,library_name,item_id,proposal_set_id,report_id,review_case_id,transition_id,checkpoint,outcome) VALUES(?,?,?,?,?,?,?,?,?)",
            ("primary", "books", "book-1", "proposal_set_" + "1" * 64,
             "report_" + "2" * 64, source.review_case_id,
             "review_transition_" + "3" * 64, 1, "admitted"),
        )
        connection.execute(
            "INSERT INTO human_decisions(decision_id,review_case_id,proposal_id,target_fingerprint,destination,actor,outcome,selected_value_document,idempotency_digest,request_digest,occurred_at,document) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (source.decision_id, source.review_case_id, source.proposal_id,
             source.target_fingerprint, source.destination, source.actor,
             source.outcome, json.dumps(source.selected_value), source.idempotency_digest,
             source.request_digest, source.occurred_at, source.to_json()),
        )
        connection.execute("PRAGMA foreign_keys=ON")
        self.claim_hook.callback = lambda _: connection.execute(
            "UPDATE current_review_case_lineages SET review_case_id=NULL,outcome='retired' "
            "WHERE connection_name='primary' AND library_name='books' AND item_id='book-1'"
        )
        with self.assertRaisesRegex(Exception, "claim_conflict"):
            self.service.apply(operation.plan.plan_id)
        self.assertEqual(self.service.status(operation.plan.plan_id).state, "planned")
        self.assertEqual(self.target.read(operation.plan).value, "old")

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
        app = create_review_app(
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
            })
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["state"], "verified")
            status = client.get(f"/api/v1/field-effects/{operation.plan.plan_id}")
            self.assertEqual(status.json()["state"], "verified")


class FieldEffectDomainTests(unittest.TestCase):
    def test_series_is_one_atomic_destination_and_state_machine_rejects_skips(self) -> None:
        authority = EffectAuthority(
            "human_decision_" + "a" * 64, "review_case_" + "b" * 64,
            "c" * 64, "fake_service", "human", "candidate", None, None,
        )
        consent = EffectConsent.create(
            connection="primary", library="books", destination="series", granted_at=1
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
