from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from mechanical_librarian.adapters.field_effect_fakes import (
    FakeAuthorityCurrentness,
    FakeBackupReadiness,
    FakeCapabilityObserver,
    FakeDependencyCurrentness,
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
        self.preclaim = FakePreclaimHook()
        self.recovery = PrivateCoverRecoveryStore(root / "recovery", lambda _: b"old-cover")
        self.clock = Clock()
        self.service = GuardedFieldApplication(
            self.repository, self.target, self.backup,
            self.capabilities, self.authority,
            self.dependencies, self.recovery, self.clock, self.preclaim,
        )

    def tearDown(self) -> None:
        self.repository.close()
        self.temporary.cleanup()

    def plan(self, destination: str = "title", pre: object = "old",
             desired: object = "new", suffix: str = "a") -> FieldEffectOperation:
        selected = envelope(destination, desired)
        result = self.service.plan(
            decision(destination, selected, suffix), connection="primary",
            library="books", work_id="book-1", destination=destination,
            pre_value=pre, desired_value=selected, target_version="version-1",
            dependency_digests=(DEPENDENCY,), capability_digest=CAPABILITY,
        )
        assert result is not None
        self.target.seed(result.plan)
        return result

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
                result = self.service.plan(
                    decision(destination, selected, suffix), connection="primary",
                    library="books", work_id=f"book-{index}", destination=destination,
                    pre_value=None, desired_value=selected, target_version="version-1",
                    dependency_digests=(DEPENDENCY,), capability_digest=CAPABILITY,
                )
                self.assertEqual(result.plan.destination, destination)
        self.assertIsNone(self.service.plan(
            decision("title", envelope("title", "same"), "f"), connection="primary", library="books",
            work_id="same-book", destination="title", pre_value="same",
            desired_value=envelope("title", "same"), target_version="version-1",
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
        result = self.service.plan(
            automatic, connection="primary", library="books", work_id="auto-book",
            destination="title", pre_value="old", desired_value=envelope("title", "new"),
            target_version="version-1", dependency_digests=(DEPENDENCY,),
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
            target_version="version-1", dependency_digests=(DEPENDENCY,),
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
        self.assertEqual(self.service.apply(desired.plan.plan_id).state, "reconciling")
        self.assertEqual(self.service.reconcile(desired.plan.plan_id).state, "verified")

        pre = self.plan(destination="subtitle", suffix="6")
        self.target.ambiguous_next = "pre"
        self.assertEqual(self.service.apply(pre.plan.plan_id).state, "reconciling")
        self.assertEqual(self.service.reconcile(pre.plan.plan_id).state, "retryable")
        self.assertEqual(self.service.apply(pre.plan.plan_id).state, "verified")

        third = self.plan(destination="publisher", suffix="7")
        self.target.ambiguous_next = "third"
        self.service.apply(third.plan.plan_id)
        result = self.service.reconcile(third.plan.plan_id)
        self.assertEqual((result.state, result.failure), ("stale", "third_value"))

    def test_target_compare_and_set_rejects_a_race_without_overwrite(self) -> None:
        operation = self.plan(suffix="0")
        self.target.before_apply = lambda plan: self.target.drift(plan, "external")
        result = self.service.apply(operation.plan.plan_id)
        self.assertEqual((result.state, result.failure), ("stale", "target_changed"))
        self.assertEqual(self.target.read(operation.plan).value, "external")

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
        restarted.capture(cover.plan)
        recovery_file = next((Path(self.temporary.name) / "recovery").iterdir())
        os.chmod(recovery_file, 0o644)
        with self.assertRaises(ValueError):
            restarted.capture(cover.plan)
        os.chmod(recovery_file, 0o600)

    def test_exact_current_schema_rejects_drift(self) -> None:
        self.repository.close()
        import sqlite3
        path = Path(self.temporary.name) / "effects.sqlite"
        connection = sqlite3.connect(path)
        connection.execute("ALTER TABLE field_effects ADD COLUMN drift TEXT")
        connection.commit()
        connection.close()
        with self.assertRaises(Exception):
            open_repository(path, create=False)
        self.repository = open_repository(Path(self.temporary.name) / "fresh.sqlite")

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
        authority = EffectAuthority("human_decision_" + "a" * 64, "human", "candidate", None, None)
        plan = FieldEffectPlan.create(
            connection="primary", library="books", work_id="book-1",
            destination="series", pre_value=[{"name": "Old", "sequence": "1"}],
            desired_value=[{"name": "New", "sequence": "2"}], target_version="v1",
            dependency_digests=(DEPENDENCY,), authority=authority,
            capability_digest=CAPABILITY, created_at=1,
        )
        operation = FieldEffectOperation(plan, updated_at=1)
        with self.assertRaises(Exception):
            operation.transition("verified", at=2)


if __name__ == "__main__":
    unittest.main()
