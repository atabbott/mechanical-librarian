#!/usr/bin/env python3
"""Qualification-only installed probe for guarded checkpoint restart recovery."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mechanical_librarian.adapters.field_effect_fakes import (
    FakeBackupReadiness,
    FakeCapabilityObserver,
    FakeDependencyCurrentness,
    FakeForwardReevaluation,
    FakeTargetService,
)
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.private_recovery import PrivateCoverRecoveryStore
from mechanical_librarian.application.field_effects import (
    GuardedApplicationError,
    GuardedFieldApplication,
)
from mechanical_librarian.domain.field_effect import (
    EffectAuthority,
    EffectConsent,
    FieldEffectOperation,
    FieldEffectPlan,
)


def _service(repository, target, store, clock):
    current = type("Current", (), {"current": lambda self, value: True})()
    return GuardedFieldApplication(
        repository,
        target,
        FakeBackupReadiness(),
        FakeCapabilityObserver("e" * 64),
        current,
        FakeDependencyCurrentness(),
        current,
        store,
        FakeForwardReevaluation(),
        clock,
    )


def _plan(repository, clock, destination, suffix, pre="old", desired="new"):
    authority = EffectAuthority(
        "human_decision_" + suffix * 64,
        "review_case_" + "b" * 64,
        "c" * 64,
        "fake_service",
        "human",
        "candidate",
        None,
        None,
    )
    consent = EffectConsent.create(
        connection="primary",
        library="books",
        destination=destination,
        envelope_identity="a" * 64,
        granted_at=1,
    )
    value = FieldEffectPlan.create(
        connection="primary",
        library="books",
        work_id="work-" + suffix,
        destination=destination,
        pre_value=pre,
        desired_value=desired,
        target_fingerprint="c" * 64,
        target_version="version-1",
        dependency_digests=("d" * 64,),
        authority=authority,
        consent=consent,
        capability_digest="e" * 64,
        created_at=clock(),
    )
    repository.record_field_effect(
        FieldEffectOperation(value, updated_at=value.created_at)
    )
    return value


def _expect_rejected(service, plan_id, code):
    try:
        service.apply(plan_id)
    except GuardedApplicationError as error:
        if error.code != code:
            raise
    else:
        raise RuntimeError("guard admission unexpectedly succeeded")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("prepare", "recover"))
    parser.add_argument("--root", type=Path, default=Path("/data/guarded-qualification"))
    arguments = parser.parse_args()
    root = arguments.root
    database = root / "application.sqlite3"
    identifiers_path = root / "identifiers.json"
    recovery_root = root / "cover-recovery"
    clock_value = [100 if arguments.phase == "prepare" else 10_000]

    def clock():
        clock_value[0] += 1
        return clock_value[0]

    if arguments.phase == "prepare":
        root.mkdir(mode=0o700)
        if database.exists() or identifiers_path.exists():
            raise RuntimeError("probe state already exists")
        repository = open_repository(database, cross_thread=True)
        target = FakeTargetService()
        store = PrivateCoverRecoveryStore(
            recovery_root, lambda value: b"synthetic-cover"
        )
        service = _service(repository, target, store, clock)

        ready = _plan(repository, clock, "title", "0")
        claimed = _plan(repository, clock, "subtitle", "1")
        repository.claim_field_effect(
            claimed.plan_id, "planned", "1" * 64, clock()
        )
        verification = _plan(repository, clock, "authors", "9", ["old"], ["new"])
        repository.claim_field_effect(
            verification.plan_id, "planned", "9" * 64, clock()
        )
        applying = _plan(repository, clock, "publisher", "2")
        repository.claim_field_effect(
            applying.plan_id, "planned", "2" * 64, clock()
        )
        repository.transition_field_effect(
            applying.plan_id, "claimed", "applying", clock()
        )
        reconciling = _plan(repository, clock, "description", "3")
        repository.claim_field_effect(
            reconciling.plan_id, "planned", "3" * 64, clock()
        )
        repository.transition_field_effect(
            reconciling.plan_id, "claimed", "applying", clock()
        )
        repository.schedule_field_effect_reconciliation(
            reconciling.plan_id, "applying", clock()
        )
        cover = _plan(
            repository, clock, "cover", "4", None, "cover_" + "4" * 64
        )
        store.capture(cover)
        repository.claim_field_effect(
            cover.plan_id, "planned", "4" * 64, clock()
        )
        repository.transition_field_effect(
            cover.plan_id, "claimed", "applying", clock()
        )
        repository.transition_field_effect(
            cover.plan_id, "applying", "verified", clock()
        )

        paused = _plan(repository, clock, "language", "5", None, "en")
        target.seed(paused)
        repository.set_field_effect_paused(True, clock())
        _expect_rejected(service, paused.plan_id, "paused")
        repository.set_field_effect_paused(False, clock())
        revoked = _plan(repository, clock, "genres", "6", ["old"], ["fiction"])
        if repository.revoke_field_effect_authority(
            revoked.authority.decision_id, clock()
        ) != 1:
            raise RuntimeError("revocation failed")
        drained = _plan(repository, clock, "isbn", "7")
        target.seed(drained)
        service.begin_drain()
        _expect_rejected(service, drained.plan_id, "draining")
        identifiers = {
            name: value.plan_id
            for name, value in {
                "ready": ready,
                "claimed": claimed,
                "verification": verification,
                "applying": applying,
                "reconciling": reconciling,
                "cover": cover,
                "paused": paused,
                "revoked": revoked,
                "drained": drained,
            }.items()
        }
        identifiers_path.write_text(
            json.dumps(identifiers, sort_keys=True, separators=(",", ":")),
            encoding="ascii",
        )
        repository.close()
        return

    identifiers = json.loads(identifiers_path.read_text(encoding="ascii"))
    repository = open_repository(database, create=False, cross_thread=True)
    target = FakeTargetService()
    for name in ("claimed", "verification", "applying", "reconciling"):
        target.seed(repository.get_field_effect(identifiers[name]).plan)
    target.fail_read_after_apply_for.add(identifiers["verification"])
    store = PrivateCoverRecoveryStore(
        recovery_root,
        lambda value: (_ for _ in ()).throw(RuntimeError("unexpected reread")),
    )
    service = _service(repository, target, store, clock)
    startup = service.start()
    if startup != (4, 1, 1):
        raise RuntimeError(f"startup recovery count disagrees: {startup!r}")
    expected = {
        "ready": "planned",
        "claimed": "verified",
        "verification": "retryable",
        "applying": "retryable",
        "reconciling": "retryable",
        "cover": "verified",
        "paused": "planned",
        "revoked": "superseded",
        "drained": "planned",
    }
    actual = {
        name: service.status(plan_id).state
        for name, plan_id in identifiers.items()
    }
    verification = service.status(identifiers["verification"])
    if (actual != expected
            or verification.failure != "target_failure"
            or store.exists(identifiers["cover"])):
        raise RuntimeError("checkpoint recovery disagrees")
    repository.close()
    print(json.dumps({
        "checkpoints": actual,
        "schema": "guarded-checkpoint-restart-v1",
        "status": "passed",
    }, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
