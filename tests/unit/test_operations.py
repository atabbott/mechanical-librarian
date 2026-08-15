from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from mechanical_librarian.adapters.operational_evidence import (
    LocalTimingStore,
    SupportBundle,
)
from mechanical_librarian.adapters.private_recovery import PrivateCoverRecoveryStore
from mechanical_librarian.application.operations import (
    MAX_SUPPORT_BYTES,
    MAX_TIMINGS,
    OperationalStatus,
    OperationsError,
    StageTimer,
    StageTiming,
    aggregate_incidents,
)
from mechanical_librarian.bootstrap import container
from mechanical_librarian.domain.field_effect import (
    EffectAuthority,
    EffectConsent,
    FieldEffectPlan,
)


def _plan() -> FieldEffectPlan:
    authority = EffectAuthority("human_decision_" + "a" * 64,
                                "review_case_" + "b" * 64, "c" * 64, "fake_service",
                                "human", "candidate", None, None)
    consent = EffectConsent.create(connection="connection", library="library",
                                   destination="cover", envelope_identity="a" * 64,
                                   granted_at=1)
    return FieldEffectPlan.create(
        connection="connection", library="library", work_id="work",
        destination="cover", pre_value=None, desired_value="cover_" + "d" * 64,
        target_fingerprint="c" * 64, target_version="version",
        dependency_digests=("e" * 64,), authority=authority, consent=consent,
        capability_digest="a" * 64, created_at=1,
    )


class OperationsTests(unittest.TestCase):
    def test_deployment_contract_names_the_composed_guarded_target(self) -> None:
        document = (
            Path(__file__).parents[2] / "docs" / "operations" / "deployment.md"
        ).read_text(encoding="utf-8")
        for statement in (
            "normal container composes the supported Audiobookshelf guarded target",
            "authenticated, library-scoped\nActivity consent flow",
            "No fake target, direct database write or writable media\npath is composed.",
        ):
            self.assertIn(statement, document)
        self.assertNotIn("canary-only", document)
        self.assertNotIn("unavailable in the normal container", document)

    def test_container_entrypoint_initializes_only_absent_exact_current_stores(self) -> None:
        repository = Mock()
        catalogue = Mock()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(container, "DATA", root), \
                 patch.object(container, "CONFIG", root / "config.json"), \
                 patch.object(container, "DATABASE", root / "application.sqlite3"), \
                 patch.object(container, "CATALOGUE", root / "catalogue.sqlite3"), \
                 patch.object(container, "ARTIFACTS", root / "artifacts"), \
                 patch.object(container, "open_repository", return_value=repository) as open_db, \
                 patch.object(container, "open_catalogue", return_value=catalogue) as open_cat, \
                 patch.object(container, "run_review_server") as serve:
                container.main()
            open_db.assert_called_once_with(root / "application.sqlite3", create=True,
                                            cross_thread=True)
            open_cat.assert_called_once_with(root / "catalogue.sqlite3", create=True)
            repository.close.assert_called_once_with()
            catalogue.close.assert_called_once_with()
            serve.assert_called_once_with(root / "config.json",
                                          root / "application.sqlite3",
                                          root / "catalogue.sqlite3",
                                          root / "artifacts", "0.0.0.0", 8443)
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)

    def test_container_definition_keeps_one_non_root_process_volume_and_port(self) -> None:
        root = Path(__file__).parents[2]
        dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
        compose = (root / "compose.yaml").read_text(encoding="utf-8")
        self.assertIn("USER 65532:65532", dockerfile)
        self.assertIn('ENTRYPOINT ["/opt/mechanical-librarian/bin/mechanical-librarian-container"]', dockerfile)
        self.assertIn("uv sync --frozen --no-dev --no-editable", dockerfile)
        self.assertEqual(dockerfile.count("EXPOSE "), 1)
        self.assertIn("read_only: true", compose)
        self.assertIn("cap_drop:", compose)
        self.assertEqual(compose.count("8443:8443"), 1)
        self.assertEqual(compose.count("mechanical-librarian-data:/data"), 1)
    def test_status_is_not_ready_until_exact_current_snapshot_opens_and_drains(self) -> None:
        calls = []
        timings = LocalTimingStore(Path("unused"), enabled=False)
        service = OperationalStatus(snapshot=lambda: calls.append(True) or {"signals": []},
                                    timings=timings)
        self.assertFalse(service.ready())
        service.start()
        self.assertTrue(service.ready())
        self.assertEqual(service.status()["summary"], {
            "automatic_recovery": 0, "healthy": True, "owner_action_required": 0,
        })
        service.begin_drain()
        self.assertFalse(service.ready())
        self.assertEqual(service.status()["readiness"], "draining")
        self.assertGreaterEqual(len(calls), 3)

    def test_common_root_causes_aggregate_but_currentness_remains_work_local(self) -> None:
        signals = [
            {"kind": "authentication_failure", "cause": "authentication",
             "scope": "connection", "connection": "primary", "library": None,
             "category": None, "work_id": "one", "field": None},
            {"kind": "authentication_failure", "cause": "authentication",
             "scope": "connection", "connection": "primary", "library": None,
             "category": None, "work_id": "two", "field": None},
            {"kind": "owner_action_required", "cause": "currentness_conflict",
             "scope": "field", "connection": "primary", "library": "books",
             "category": None, "work_id": "one", "field": "title"},
            {"kind": "owner_action_required", "cause": "currentness_conflict",
             "scope": "field", "connection": "primary", "library": "books",
             "category": None, "work_id": "two", "field": "title"},
        ]
        incidents = aggregate_incidents(signals)
        self.assertEqual([value.count for value in incidents], [2, 1, 1])
        self.assertEqual({value.work_id for value in incidents}, {None, "one", "two"})

    def test_long_shared_provider_prefixes_have_distinct_stable_incident_ids(self) -> None:
        shared = "provider-" + "a" * 54
        signals = [
            {
                "kind": "authentication_failure",
                "cause": "provider_authentication",
                "scope": "connection",
                "connection": shared + suffix,
                "library": None,
                "category": None,
                "work_id": None,
                "field": None,
            }
            for suffix in ("b", "c")
        ]
        first = aggregate_incidents(signals)
        replay = aggregate_incidents(signals)
        self.assertEqual(
            tuple(value.incident_id for value in first),
            tuple(value.incident_id for value in replay),
        )
        self.assertEqual(len({value.incident_id for value in first}), 2)
        self.assertTrue(all(
            value.incident_id.startswith("incident:authentication_failure:")
            and len(value.incident_id) == 96
            for value in first
        ))

    def test_opt_in_timings_are_bounded_private_and_survive_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private" / "timings.json"
            disabled = LocalTimingStore(path)
            disabled.append(StageTiming("apply", 12, "target", 1))
            self.assertFalse(path.exists())
            store = LocalTimingStore(path, enabled=True)
            for index in range(MAX_TIMINGS + 5):
                store.append(StageTiming("verification", index, "none", index))
            values = LocalTimingStore(path, enabled=True).list()
            self.assertEqual(len(values), MAX_TIMINGS)
            self.assertEqual(values[0].duration_ms, 5)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("pre_value", path.read_text())
            path.with_suffix(".tmp").write_text("incomplete", encoding="utf-8")
            os.chmod(path.with_suffix(".tmp"), 0o600)
            store.append(StageTiming("apply", 1, "target", MAX_TIMINGS + 6))
            self.assertFalse(path.with_suffix(".tmp").exists())

    def test_timing_failure_cannot_change_measured_authority_and_private_roots_reject_links(self) -> None:
        class FailingTimings:
            def append(self, timing) -> None:
                raise OSError("synthetic disk failure")
            def list(self):
                return ()

        completed = []
        with StageTimer(
            FailingTimings(), monotonic_ns=iter((0, 2_000_000)).__next__,
            clock=lambda: 1,
        ).measure("apply", "target"):
            completed.append("durable")
        self.assertEqual(completed, ["durable"])

        with self.assertRaisesRegex(RuntimeError, "authority failure"):
            with StageTimer(
                None, monotonic_ns=iter((0, 1_000_000)).__next__, clock=lambda: 1
            ).measure("verification", "target"):
                raise RuntimeError("authority failure")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real = root / "real"
            real.mkdir(mode=0o700)
            linked = root / "private"
            linked.symlink_to(real, target_is_directory=True)
            with self.assertRaises(OperationsError):
                LocalTimingStore(linked / "timings.json", enabled=True).list()
            with self.assertRaises(ValueError):
                PrivateCoverRecoveryStore(linked, lambda _: b"cover")

    def test_support_preview_and_export_are_bounded_and_redacted_again(self) -> None:
        state = {"status": "ready", "secret": "do-not-export",
                 "nested": {"private_path": "/owner/books", "code": "ok"}}
        bundle = SupportBundle((("runtime", lambda: state),))
        preview = bundle.preview()
        self.assertLessEqual(preview["bytes"], MAX_SUPPORT_BYTES)
        self.assertEqual(preview["payload"]["sections"], {
            "runtime": {"nested": {"code": "ok"}, "status": "ready"}
        })
        state["status"] = "/new/private/path"
        exported = bundle.export()
        self.assertNotIn(b"do-not-export", exported)
        self.assertNotIn(b"/new/private/path", exported)
        self.assertEqual(json.loads(exported)["sections"]["runtime"]["status"], "[redacted]")

    def test_support_rejects_oversize_and_depth(self) -> None:
        bundle = SupportBundle((("runtime", lambda: {"safe": "x" * (MAX_SUPPORT_BYTES + 1)}),))
        # Long strings redact before bounding.
        self.assertEqual(bundle.preview()["payload"]["sections"]["runtime"]["safe"], "[redacted]")
        nested = value = {}
        for _ in range(15):
            value["safe"] = {}
            value = value["safe"]
        with self.assertRaisesRegex(OperationsError, "support_too_deep"):
            SupportBundle((("runtime", lambda: nested),)).preview()

    def test_cover_recovery_survives_reopen_and_cleanup_without_exporting_bytes(self) -> None:
        plan = _plan()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "recovery"
            first = PrivateCoverRecoveryStore(root, lambda _: b"private-cover")
            first.capture(plan)
            self.assertTrue(first.exists(plan.plan_id))
            second = PrivateCoverRecoveryStore(root, lambda _: b"changed")
            second.capture(plan)
            files = list(root.glob("*.recovery"))
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].read_bytes(), b"private-cover")
            self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
            second.remove(plan.plan_id)
            self.assertFalse(second.exists(plan.plan_id))

    def test_cover_recovery_contains_permission_capacity_and_write_failures(self) -> None:
        plan = _plan()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "recovery"
            store = PrivateCoverRecoveryStore(root, lambda _: b"x", maximum_entries=1)
            store.capture(plan)
            other = FieldEffectPlan.create(
                connection=plan.connection, library=plan.library,
                work_id="other-work", destination=plan.destination,
                pre_value=plan.pre_value, desired_value=plan.desired_value,
                target_fingerprint=plan.target_fingerprint,
                target_version=plan.target_version,
                dependency_digests=plan.dependency_digests,
                authority=plan.authority, consent=plan.consent,
                capability_digest=plan.capability_digest,
                created_at=plan.created_at,
            )
            with self.assertRaisesRegex(ValueError, "full"):
                store.capture(other)
            os.chmod(next(root.glob("*.recovery")), 0o644)
            with self.assertRaisesRegex(ValueError, "unavailable"):
                store.capture(plan)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "recovery"
            store = PrivateCoverRecoveryStore(root, lambda _: b"xx", maximum_bytes=1)
            with self.assertRaisesRegex(ValueError, "unavailable"):
                store.capture(plan)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "recovery"
            store = PrivateCoverRecoveryStore(root, lambda _: b"x")
            with patch("mechanical_librarian.adapters.private_recovery.os.write",
                       side_effect=OSError("synthetic disk full")):
                with self.assertRaises(OSError):
                    store.capture(plan)
            store.capture(plan)
            self.assertTrue(store.exists(plan.plan_id))
            os.chmod(root, 0o755)
            with self.assertRaisesRegex(ValueError, "unavailable"):
                store.exists(plan.plan_id)


if __name__ == "__main__":
    unittest.main()
