from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock, patch

import pytest

from mechanical_librarian.adapters.operational_evidence import (
    LocalTimingStore,
    SupportBundle,
)
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.private_recovery import PrivateCoverRecoveryStore
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.operations import (
    MAX_SUPPORT_BYTES,
    MAX_TIMINGS,
    OperationalStatus,
    OperationsError,
    StageTimer,
    StageTiming,
    aggregate_incidents,
    support_preparation_incidents,
)
from mechanical_librarian.bootstrap import container
from mechanical_librarian.domain.field_effect import (
    EffectAuthority,
    EffectConsent,
    FieldEffectPlan,
)
from mechanical_librarian.infrastructure.first_boot import FirstBootMode


def _plan() -> FieldEffectPlan:
    authority = EffectAuthority(
        "human_decision_" + "a" * 64,
        "review_case_" + "b" * 64,
        "c" * 64,
        "fake_service",
        "human",
        "candidate",
        None,
        None,
    )
    consent = EffectConsent.create(
        connection="connection",
        library="library",
        destination="cover",
        envelope_identity="a" * 64,
        granted_at=1,
    )
    return FieldEffectPlan.create(
        connection="connection",
        library="library",
        work_id="work",
        destination="cover",
        pre_value=None,
        desired_value="cover_" + "d" * 64,
        target_fingerprint="c" * 64,
        target_version="version",
        dependency_digests=("e" * 64,),
        authority=authority,
        consent=consent,
        capability_digest="a" * 64,
        created_at=1,
    )


def test_deployment_contract_names_the_composed_guarded_target() -> None:
    document = (
        Path(__file__).parents[2] / "docs" / "operations" / "deployment.md"
    ).read_text(encoding="utf-8")
    for statement in (
        "normal container composes the supported Audiobookshelf guarded target",
        "authenticated, library-scoped\nActivity consent flow",
        "No fake target, direct database write or writable media\npath is composed.",
    ):
        assert statement in document
    assert "canary-only" not in document
    assert "unavailable in the normal container" not in document


def test_container_entrypoint_initializes_only_absent_exact_current_stores() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)

        def interrupt_before_kickoff(*_args: object, **_kwargs: object) -> None:
            repository = container.open_repository(root / "application.sqlite3")
            try:
                repository.setup_state_store().initialize()
            finally:
                repository.close()

        with (
            patch.object(container, "DATA", root),
            patch.object(container, "CONFIG", root / "config.json"),
            patch.object(container, "DATABASE", root / "application.sqlite3"),
            patch.object(container, "CATALOGUE", root / "catalogue.sqlite3"),
            patch.object(container, "ARTIFACTS", root / "artifacts"),
            patch.object(container.signal, "signal") as install_signal,
            patch.object(
                container,
                "run_review_server",
                side_effect=interrupt_before_kickoff,
            ) as serve,
        ):
            container.main()
            serve.assert_called_once_with(
                root / "config.json",
                root / "application.sqlite3",
                root / "catalogue.sqlite3",
                root / "artifacts",
                "0.0.0.0",
                8443,
                first_boot_mode=FirstBootMode.INITIALIZE_SETUP,
            )
            assert {value.name for value in root.iterdir()} == {"application.sqlite3"}
            serve.reset_mock(side_effect=True)
            container.main()
            serve.assert_called_once_with(
                root / "config.json",
                root / "application.sqlite3",
                root / "catalogue.sqlite3",
                root / "artifacts",
                "0.0.0.0",
                8443,
                first_boot_mode=FirstBootMode.RESUME_SETUP,
            )
            assert all(
                installed.args
                == (container.signal.SIGTERM, container._exit_on_container_stop)
                for installed in install_signal.call_args_list
            )
            assert install_signal.call_count == 2
        assert root.stat().st_mode & 0o777 == 0o700


def test_container_stop_handler_exits_successfully() -> None:
    with pytest.raises(SystemExit) as raised:
        container._exit_on_container_stop(container.signal.SIGTERM, None)
    assert raised.value.code == 0


def test_container_definition_keeps_one_non_root_process_volume_and_port() -> None:
    root = Path(__file__).parents[2]
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    compose = (root / "compose.yaml").read_text(encoding="utf-8")
    seccomp = json.loads(
        (root / "config" / "seccomp" / "contained-reviewer.json").read_text(
            encoding="utf-8"
        )
    )
    seccomp_bytes = (
        root / "config" / "seccomp" / "contained-reviewer.json"
    ).read_bytes()
    seccomp_license = (
        root / "config" / "seccomp" / "LICENSE.moby-profiles"
    ).read_bytes()
    assert "USER 65532:65532" in dockerfile
    assert (
        'ENTRYPOINT ["/opt/mechanical-librarian/bin/mechanical-librarian-container"]'
        in dockerfile
    )
    assert "uv sync --frozen --no-dev --no-editable" in dockerfile
    assert dockerfile.count("EXPOSE ") == 1
    assert "read_only: true" in compose
    assert "init: true" in compose
    assert "cap_drop:" in compose
    assert "mem_limit: 2g" in compose
    assert "memswap_limit: 2g" in compose
    assert "mem_limit: 1g" not in compose
    assert "memswap_limit: 1g" not in compose
    assert "seccomp=./config/seccomp/contained-reviewer.json" in compose
    assert seccomp["defaultAction"] == "SCMP_ACT_ERRNO"
    assert (
        hashlib.sha256(seccomp_bytes).hexdigest()
        == "be2ebd491e84aebc79551b718f6d6f5f748f5b3050fede615820e463d030e21c"
    )
    assert (
        hashlib.sha256(seccomp_license).hexdigest()
        == "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"
    )
    exact_rules = {
        (rule["names"][0], rule["args"][0]["valueTwo"])
        for rule in seccomp["syscalls"]
        if rule.get("comment", "").startswith("Allow only the exact")
        or rule.get("comment", "").startswith("Allow the same exact")
        or rule.get("comment", "").startswith("Allow Bubblewrap")
    }
    assert exact_rules == {
        ("clone", 1_040_318_464),
        ("clone", 2_114_060_288),
        ("unshare", 268_435_456),
    }
    assert any(
        rule["names"] == ["mount", "pivot_root", "umount2"]
        and rule["action"] == "SCMP_ACT_ALLOW"
        and rule.get("comment", "").startswith("Admit mount-namespace")
        for rule in seccomp["syscalls"]
    )
    assert compose.count("8443:8443") == 1
    assert compose.count("mechanical-librarian-data:/data") == 1
    assert "stop_grace_period: 610s" in compose


def test_container_bundles_the_exact_verified_reviewer_artifacts() -> None:
    dockerfile = (Path(__file__).parents[2] / "Dockerfile").read_text(encoding="utf-8")
    assert 'package_version="0.147.0-linux-x64"' in dockerfile
    assert 'package_version="0.147.0-linux-arm64"' in dockerfile
    assert (
        'expected_sha256="c969740cf8297e4c31905cd551efeb2c99af5080c12c236bdf825598b250139a"'
        in dockerfile
    )
    assert (
        'expected_sha256="4d09bf000597768dfa070b0e7c612f0fc258ac7fab364e570104ea625f918546"'
        in dockerfile
    )
    assert 'npm pack "@openai/codex@$package_version"' in dockerfile
    assert "--ignore-scripts --no-audit --no-fund" in dockerfile
    assert 'echo "$expected_sha256  /tmp/codex/$archive" | sha256sum -c -' in dockerfile
    assert 'test "$(/reviewer/bin/codex --version)" = "codex-cli 0.147.0"' in dockerfile
    assert "sha256sum bin/codex bin/bwrap > metadata/SHA256SUMS" in dockerfile
    assert (
        "sha256:d17f227e4df5da1600391338865ce0f3055211760a36688f816941d58232d8dc"
        in dockerfile
    )
    assert (
        "COPY --from=reviewer-build /reviewer /opt/mechanical-librarian/reviewer"
        in dockerfile
    )


def test_container_lifecycle_qualifies_both_init_backed_application_runs() -> None:
    root = Path(__file__).parents[2]
    qualifier = (root / "scripts/qualify_container_lifecycle.py").read_text(
        encoding="utf-8"
    )
    assert qualifier.count('"--init",') == 2


def test_status_is_not_ready_until_exact_current_snapshot_opens_and_drains() -> None:
    calls = []
    timings = LocalTimingStore(Path("unused"), enabled=False)
    # `append` returns `None`, so the `or` always yields the snapshot dict; the
    # call is there to record that the snapshot was taken.
    service = OperationalStatus(
        snapshot=lambda: calls.append(True) or {"signals": []},  # type: ignore[func-returns-value]  # reason: the record-then-yield idiom is deliberate; the discarded None never reaches an assertion
        timings=timings,
    )
    assert not service.ready()
    service.start()
    assert service.ready()
    assert service.status()["summary"] == {
        "automatic_recovery": 0,
        "healthy": True,
        "owner_action_required": 0,
    }
    service.begin_drain()
    assert not service.ready()
    assert service.status()["readiness"] == "draining"
    assert len(calls) >= 3


def test_common_root_causes_aggregate_but_currentness_remains_work_local() -> None:
    signals = [
        {
            "kind": "authentication_failure",
            "cause": "authentication",
            "scope": "connection",
            "connection": "primary",
            "library": None,
            "category": None,
            "work_id": "one",
            "field": None,
        },
        {
            "kind": "authentication_failure",
            "cause": "authentication",
            "scope": "connection",
            "connection": "primary",
            "library": None,
            "category": None,
            "work_id": "two",
            "field": None,
        },
        {
            "kind": "owner_action_required",
            "cause": "currentness_conflict",
            "scope": "field",
            "connection": "primary",
            "library": "books",
            "category": None,
            "work_id": "one",
            "field": "title",
        },
        {
            "kind": "owner_action_required",
            "cause": "currentness_conflict",
            "scope": "field",
            "connection": "primary",
            "library": "books",
            "category": None,
            "work_id": "two",
            "field": "title",
        },
    ]
    incidents = aggregate_incidents(signals)
    assert [value.count for value in incidents] == [2, 1, 1]
    assert {value.work_id for value in incidents} == {None, "one", "two"}


def test_long_shared_provider_prefixes_have_distinct_stable_incident_ids() -> None:
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
    assert tuple(value.incident_id for value in first) == tuple(
        value.incident_id for value in replay
    )
    assert len({value.incident_id for value in first}) == 2
    assert all(
        value.incident_id.startswith("incident:authentication_failure:")
        and len(value.incident_id) == 96
        for value in first
    )


def test_opt_in_timings_are_bounded_private_and_survive_reopen() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "private" / "timings.json"
        disabled = LocalTimingStore(path)
        disabled.append(StageTiming("apply", 12, "target", 1))
        assert not path.exists()
        store = LocalTimingStore(path, enabled=True)
        for index in range(MAX_TIMINGS + 5):
            store.append(StageTiming("verification", index, "none", index))
        values = LocalTimingStore(path, enabled=True).list()
        assert len(values) == MAX_TIMINGS
        assert values[0].duration_ms == 5
        assert path.stat().st_mode & 0o777 == 0o600
        assert "pre_value" not in path.read_text()
        path.with_suffix(".tmp").write_text("incomplete", encoding="utf-8")
        os.chmod(path.with_suffix(".tmp"), 0o600)
        store.append(StageTiming("apply", 1, "target", MAX_TIMINGS + 6))
        assert not path.with_suffix(".tmp").exists()


def test_timing_failure_cannot_change_measured_authority_and_private_roots_reject_links() -> (
    None
):
    class FailingTimings:
        def append(self, timing) -> None:
            raise OSError("synthetic disk failure")

        def list(self):
            return ()

    completed = []
    with StageTimer(
        FailingTimings(),
        monotonic_ns=iter((0, 2_000_000)).__next__,
        clock=lambda: 1,
    ).measure("apply", "target"):
        completed.append("durable")
    assert completed == ["durable"]

    with pytest.raises(RuntimeError, match="authority failure"):
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
        with pytest.raises(OperationsError):
            LocalTimingStore(linked / "timings.json", enabled=True).list()
        with pytest.raises(ValueError):
            PrivateCoverRecoveryStore(linked, lambda _: b"cover")


def test_support_preview_and_export_are_bounded_and_redacted_again() -> None:
    state = {
        "status": "ready",
        "secret": "do-not-export",
        "nested": {"private_path": "/owner/books", "code": "ok"},
    }
    bundle = SupportBundle((("runtime", lambda: state),))
    preview = bundle.preview()
    assert cast(int, preview["bytes"]) <= MAX_SUPPORT_BYTES
    assert preview["payload"]["sections"] == {
        "runtime": {"nested": {"code": "ok"}, "status": "ready"}
    }
    state["status"] = "/new/private/path"
    exported = bundle.export()
    assert b"do-not-export" not in exported
    assert b"/new/private/path" not in exported
    assert json.loads(exported)["sections"]["runtime"]["status"] == "[redacted]"


def test_support_export_retains_only_a_browse_storage_exception_class(
    tmp_path: Path,
) -> None:
    repository = open_repository(tmp_path / "application.sqlite3")
    try:
        repository._connection.execute("DROP TABLE review_cases")
        with pytest.raises(MetadataReportError, match="review library is unavailable"):
            repository.list_current_review_case_documents(None, 1, 0, None)
        exported = json.loads(
            SupportBundle(
                (
                    (
                        "browse_failures",
                        lambda: {
                            "exception_classes": repository.browse_unavailable_exception_classes()
                        },
                    ),
                )
            ).export()
        )
    finally:
        repository.close()
    assert exported["sections"] == {
        "browse_failures": {"exception_classes": ["OperationalError"]}
    }


def test_support_preparation_incidents_match_status_and_exclude_private_values() -> (
    None
):
    snapshot: dict[str, object] = {
        "signals": [
            {
                "attempt": 2,
                "code": "parser_refused",
                "diagnostic_reference": "prep-0123456789abcdef",
                "kind": "owner_action_required",
                "cause": "preparation_failure",
                "scope": "work",
                "stage": "extraction",
                "work_id": "private-item-id",
                "provider": "private-provider",
                "path": "/private/media/book.epub",
            },
            {
                "kind": "automatic_recovery",
                "cause": "in_progress",
                "scope": "work",
                "work_id": "retrying-private-item",
            },
        ]
    }
    timings = Mock()
    timings.list.return_value = ()
    status = OperationalStatus(snapshot=lambda: snapshot, timings=timings).status()
    bundle = SupportBundle(
        (("operational_incidents", lambda: support_preparation_incidents(snapshot)),)
    )

    preview = bundle.preview()
    exported = json.loads(bundle.export())
    status_incident = next(
        incident
        for incident in cast(list[dict[str, object]], status["incidents"])
        if incident["diagnostic_reference"] is not None
    )
    support_incident = preview["payload"]["sections"]["operational_incidents"][
        "incidents"
    ][0]
    assert support_incident == {
        key: status_incident[key]
        for key in ("attempt", "code", "diagnostic_reference", "stage")
    }
    assert (
        exported["sections"]["operational_incidents"]
        == preview["payload"]["sections"]["operational_incidents"]
    )
    encoded = json.dumps(exported)
    for private in ("private-item-id", "private-provider", "/private/media/book.epub"):
        assert private not in encoded


def test_support_rejects_oversize_and_depth() -> None:
    bundle = SupportBundle(
        (("runtime", lambda: {"safe": "x" * (MAX_SUPPORT_BYTES + 1)}),)
    )
    # Long strings redact before bounding.
    assert bundle.preview()["payload"]["sections"]["runtime"]["safe"] == "[redacted]"
    nested = value = cast(dict[str, Any], {})
    for _ in range(15):
        value["safe"] = {}
        value = value["safe"]
    with pytest.raises(OperationsError, match="support_too_deep"):
        SupportBundle((("runtime", lambda: nested),)).preview()


def test_cover_recovery_survives_reopen_and_cleanup_without_exporting_bytes() -> None:
    plan = _plan()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "recovery"
        first = PrivateCoverRecoveryStore(root, lambda _: b"private-cover")
        first.capture(plan)
        assert first.exists(plan.plan_id)
        second = PrivateCoverRecoveryStore(root, lambda _: b"changed")
        second.capture(plan)
        files = list(root.glob("*.recovery"))
        assert len(files) == 1
        assert files[0].read_bytes() == b"private-cover"
        assert files[0].stat().st_mode & 0o777 == 0o600
        second.remove(plan.plan_id)
        assert not second.exists(plan.plan_id)


def test_cover_recovery_contains_permission_capacity_and_write_failures() -> None:
    plan = _plan()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "recovery"
        store = PrivateCoverRecoveryStore(root, lambda _: b"x", maximum_entries=1)
        store.capture(plan)
        other = FieldEffectPlan.create(
            connection=plan.connection,
            library=plan.library,
            work_id="other-work",
            destination=plan.destination,
            pre_value=plan.pre_value,
            desired_value=plan.desired_value,
            target_fingerprint=plan.target_fingerprint,
            target_version=plan.target_version,
            dependency_digests=plan.dependency_digests,
            authority=plan.authority,
            consent=plan.consent,
            capability_digest=plan.capability_digest,
            created_at=plan.created_at,
        )
        with pytest.raises(ValueError, match="full"):
            store.capture(other)
        os.chmod(next(root.glob("*.recovery")), 0o644)
        with pytest.raises(ValueError, match="unavailable"):
            store.capture(plan)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "recovery"
        store = PrivateCoverRecoveryStore(root, lambda _: b"xx", maximum_bytes=1)
        with pytest.raises(ValueError, match="unavailable"):
            store.capture(plan)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "recovery"
        store = PrivateCoverRecoveryStore(root, lambda _: b"x")
        with patch(
            "mechanical_librarian.adapters.private_recovery.os.write",
            side_effect=OSError("synthetic disk full"),
        ):
            with pytest.raises(OSError):
                store.capture(plan)
        store.capture(plan)
        assert store.exists(plan.plan_id)
        os.chmod(root, 0o755)
        with pytest.raises(ValueError, match="unavailable"):
            store.exists(plan.plan_id)
