"""The original an automatic cover switch replaces is kept for Put back (#1363)."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import sqlite3
import stat
from pathlib import Path

import pytest

from mechanical_librarian.adapters.field_effect_fakes import PrivateCoverRecoveryStore
from mechanical_librarian.adapters.persistence.metadata_reports import (
    MetadataReportError,
    open_repository,
)
from mechanical_librarian.adapters.retained_covers import (
    RetainedCoverFull,
    RetainedCoverStore,
)
from mechanical_librarian.application.cover_retention import CoverRetention
from mechanical_librarian.application.field_effects import GuardedFieldApplication
from mechanical_librarian.domain.cover_automatic_decision import (
    CoverAutomaticDecision,
)
from mechanical_librarian.domain.field_effect import FieldEffectOperation
from mechanical_librarian.domain.readable_activity import failure_reason
from tests.support.field_effects import (
    CAPABILITY,
    DEPENDENCY,
    _authorize,
    _EffectEnvironment,
    _plan,
    envelope,
)
from tests.support.field_effects import (
    effects as effects,
)

OLD = b"old-cover"
OLD_DIGEST = hashlib.sha256(OLD).hexdigest()
NEW = "cover_" + "9" * 64


def _automatic_cover(suffix: str = "0") -> CoverAutomaticDecision:
    fixture = Path(__file__).parents[1] / "fixtures/cover-authority-v2.json"
    raw = json.loads(json.loads(fixture.read_text())["decisions"][0])
    arguments = {
        name: raw[name]
        for name in inspect.signature(CoverAutomaticDecision.create).parameters
    }
    arguments.update(
        rank_key=tuple(raw["rank_key"]),
        citation_ids=tuple(raw["citation_ids"]),
        policy_option="replace_if_strictly_better",
        artifact_id=NEW,
        candidate_id="alternative_" + suffix * 64,
    )
    return CoverAutomaticDecision.create(**arguments)


def _service(
    effects: _EffectEnvironment, store: RetainedCoverStore
) -> GuardedFieldApplication:
    return GuardedFieldApplication(
        effects.repository,
        effects.target,
        effects.backup,
        effects.capabilities,
        effects.authority,
        effects.dependencies,
        effects.consent,
        effects.recovery,
        effects.reevaluation,
        effects.clock,
        effects.preclaim,
        effects.claim_hook,
        effects.recovery_hook,
        cover_retention=CoverRetention(
            store, effects.repository, effects.recovery.read, effects.clock
        ),
    )


def _root(effects: _EffectEnvironment) -> Path:
    return Path(effects.temporary.name) / "retained-covers"


def _automatic_plan(
    effects: _EffectEnvironment,
    service: GuardedFieldApplication,
    *,
    pre: str | None = "cover_" + OLD_DIGEST,
    work_id: str = "book-1",
    suffix: str = "0",
) -> FieldEffectOperation:
    automatic = _automatic_cover(suffix)
    effects.authority.automatic_values[automatic.decision_id] = envelope("cover", NEW)
    _authorize(effects, automatic, work_id=work_id)
    result = service.plan(
        automatic,
        connection="primary",
        library="books",
        work_id=work_id,
        destination="cover",
        pre_value=pre,
        desired_value=envelope("cover", NEW),
        target_fingerprint=automatic.target_fingerprint,
        target_version="version-1",
        dependency_digests=(DEPENDENCY,),
        capability_digest=CAPABILITY,
    )
    assert result is not None
    assert result.plan.authority.origin == "automatic_policy"
    effects.target.seed(result.plan)
    return result


def _removals(effects: _EffectEnvironment) -> list[tuple[str, str, str, int]]:
    connection = sqlite3.connect(effects.database_path)
    try:
        return [
            tuple(row)
            for row in connection.execute(
                "SELECT plan_id,reason,state,attempt FROM retained_cover_removals"
            )
        ]
    finally:
        connection.close()


def test_a_verified_automatic_switch_keeps_the_original_privately(
    effects: _EffectEnvironment,
) -> None:
    store = RetainedCoverStore(_root(effects))
    service = _service(effects, store)
    planned = _automatic_plan(effects, service)

    assert service.apply(planned.plan.plan_id).state == "verified"

    kept = _root(effects) / f"{OLD_DIGEST}.retained"
    assert kept.read_bytes() == OLD
    assert stat.S_IMODE(kept.stat().st_mode) == 0o600
    assert stat.S_IMODE(_root(effects).stat().st_mode) == 0o700
    assert effects.repository.retained_cover_original("primary", "books", "book-1") == (
        OLD_DIGEST,
        planned.plan.plan_id,
        planned.plan.authority.decision_id,
    )
    assert _removals(effects) == []
    # The recovery copy is discharged as before; only the retained copy stays.
    assert not effects.recovery.exists(planned.plan.plan_id)


def test_an_owner_cover_decision_and_an_automatic_fill_retain_nothing(
    effects: _EffectEnvironment,
) -> None:
    store = RetainedCoverStore(_root(effects))
    service = _service(effects, store)
    owner = _plan(effects, "cover", "cover_" + OLD_DIGEST, NEW, "b")
    fill = _automatic_plan(effects, service, pre=None, work_id="book-2")

    assert service.apply(owner.plan.plan_id).state == "verified"
    assert service.apply(fill.plan.plan_id).state == "verified"
    assert list(_root(effects).iterdir()) == []
    assert (
        effects.repository.retained_cover_original("primary", "books", "book-1") is None
    )


def test_an_original_that_does_not_match_the_plan_refuses_the_write(
    effects: _EffectEnvironment,
) -> None:
    effects.recovery = PrivateCoverRecoveryStore(
        Path(effects.temporary.name) / "other-recovery", lambda _: b"changed-cover"
    )
    store = RetainedCoverStore(_root(effects))
    service = _service(effects, store)
    planned = _automatic_plan(effects, service)
    sent: list[str] = []
    effects.target.before_apply = lambda plan: sent.append(plan.plan_id)

    result = service.apply(planned.plan.plan_id)

    assert (result.state, result.failure) == ("stale", "cover_retention")
    assert sent == []
    assert list(_root(effects).iterdir()) == []
    assert (
        effects.repository.retained_cover_original("primary", "books", "book-1") is None
    )


def test_a_full_store_turns_the_switch_away_and_drops_nothing_older(
    effects: _EffectEnvironment,
) -> None:
    store = RetainedCoverStore(_root(effects), maximum_entries=1)
    older = b"an-older-original"
    store.keep(hashlib.sha256(older).hexdigest(), older)
    service = _service(effects, store)
    planned = _automatic_plan(effects, service)
    sent: list[str] = []
    effects.target.before_apply = lambda plan: sent.append(plan.plan_id)

    result = service.apply(planned.plan.plan_id)

    assert (result.state, result.failure) == ("stale", "cover_retention")
    assert sent == []
    # The binding preceded the refusal, so the stale plan owes a removal; it
    # finds nothing of its own to remove and leaves the older original alone.
    assert _removals(effects) == [(planned.plan.plan_id, "unverified", "pending", 0)]
    service.run_pending_cleanups()
    assert _removals(effects) == [(planned.plan.plan_id, "unverified", "completed", 1)]
    assert [path.name for path in _root(effects).iterdir()] == [
        f"{hashlib.sha256(older).hexdigest()}.retained"
    ]


def test_a_plan_that_ends_unverified_removes_its_original_through_the_obligation(
    effects: _EffectEnvironment,
) -> None:
    store = RetainedCoverStore(_root(effects))
    service = _service(effects, store)
    planned = _automatic_plan(effects, service)
    # Audiobookshelf changes after the original was kept and before the claim.
    effects.preclaim.callback = lambda plan: effects.target.drift(
        plan, "cover_" + "8" * 64
    )

    result = service.apply(planned.plan.plan_id)

    assert (result.state, result.failure) == ("stale", "target_changed")
    assert _removals(effects) == [(planned.plan.plan_id, "unverified", "pending", 0)]
    assert (_root(effects) / f"{OLD_DIGEST}.retained").exists()
    service.run_pending_cleanups()
    assert not (_root(effects) / f"{OLD_DIGEST}.retained").exists()
    assert _removals(effects) == [(planned.plan.plan_id, "unverified", "completed", 1)]
    assert (
        effects.repository.retained_cover_original("primary", "books", "book-1") is None
    )


def test_a_failed_removal_stays_pending_and_restart_recovery_retries_it(
    effects: _EffectEnvironment, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RetainedCoverStore(_root(effects))
    service = _service(effects, store)
    planned = _automatic_plan(effects, service)
    effects.preclaim.callback = lambda plan: effects.target.drift(
        plan, "cover_" + "8" * 64
    )
    original_remove = store.remove

    def failing_remove(digest: str) -> None:
        raise OSError("synthetic removal failure")

    monkeypatch.setattr(store, "remove", failing_remove)
    assert service.apply(planned.plan.plan_id).state == "stale"
    service.run_pending_cleanups()
    assert _removals(effects) == [(planned.plan.plan_id, "unverified", "pending", 1)]
    assert (_root(effects) / f"{OLD_DIGEST}.retained").exists()

    monkeypatch.setattr(store, "remove", original_remove)
    restarted = _service(effects, store)
    restarted.recover_startup()
    assert not (_root(effects) / f"{OLD_DIGEST}.retained").exists()
    assert _removals(effects) == [(planned.plan.plan_id, "unverified", "completed", 2)]


def test_an_original_another_binding_still_needs_is_not_removed(
    effects: _EffectEnvironment,
) -> None:
    store = RetainedCoverStore(_root(effects))
    service = _service(effects, store)
    kept = _automatic_plan(effects, service, work_id="book-1", suffix="1")
    assert service.apply(kept.plan.plan_id).state == "verified"
    ended = _automatic_plan(effects, service, work_id="book-2", suffix="2")
    effects.preclaim.callback = lambda plan: effects.target.drift(
        plan, "cover_" + "8" * 64
    )

    assert service.apply(ended.plan.plan_id).state == "stale"
    service.run_pending_cleanups()

    assert (_root(effects) / f"{OLD_DIGEST}.retained").read_bytes() == OLD
    assert _removals(effects) == [(ended.plan.plan_id, "unverified", "completed", 1)]
    assert effects.repository.retained_cover_original("primary", "books", "book-1") == (
        OLD_DIGEST,
        kept.plan.plan_id,
        kept.plan.authority.decision_id,
    )


def test_bindings_are_append_only_and_their_removals_are_reconciled_on_open(
    effects: _EffectEnvironment,
) -> None:
    store = RetainedCoverStore(_root(effects))
    service = _service(effects, store)
    planned = _automatic_plan(effects, service)
    effects.preclaim.callback = lambda plan: effects.target.drift(
        plan, "cover_" + "8" * 64
    )
    assert service.apply(planned.plan.plan_id).state == "stale"
    effects.repository.close()

    connection = sqlite3.connect(effects.database_path)
    try:
        for statement in (
            "UPDATE retained_cover_bindings SET byte_count=1",
            "DELETE FROM retained_cover_bindings",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                connection.execute(statement)
        # A stale plan whose removal obligation vanished is refused on open.
        connection.execute("DELETE FROM retained_cover_removals")
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(MetadataReportError):
        open_repository(effects.database_path, cross_thread=True)


def test_retention_failures_write_nothing_to_the_operational_streams(
    effects: _EffectEnvironment, capfd: pytest.CaptureFixture[str]
) -> None:
    store = RetainedCoverStore(_root(effects), maximum_entries=1)
    store.keep(hashlib.sha256(b"x").hexdigest(), b"x")
    service = _service(effects, store)
    planned = _automatic_plan(effects, service)

    assert service.apply(planned.plan.plan_id).failure == "cover_retention"
    service.run_pending_cleanups()

    captured = capfd.readouterr()
    assert captured.out == captured.err == ""


def test_the_retention_failure_reads_as_a_plain_reason() -> None:
    assert failure_reason("cover_retention") == (
        "The current cover could not be kept to put back, so it was not replaced"
    )


def test_the_store_refuses_bytes_that_do_not_match_their_digest(tmp_path: Path) -> None:
    root = tmp_path / "retained"
    store = RetainedCoverStore(root)
    with pytest.raises(ValueError):
        store.keep("0" * 64, OLD)
    with pytest.raises(ValueError):
        store.keep("not-a-digest", OLD)
    assert list(root.iterdir()) == []


def test_the_store_bounds_total_bytes_and_keeping_again_is_a_no_op(
    tmp_path: Path,
) -> None:
    store = RetainedCoverStore(tmp_path / "retained", maximum_bytes=len(OLD) + 1)
    store.keep(OLD_DIGEST, OLD)
    store.keep(OLD_DIGEST, OLD)
    with pytest.raises(RetainedCoverFull):
        store.keep(hashlib.sha256(b"another").hexdigest(), b"another")
    store.remove(OLD_DIGEST)
    store.remove(OLD_DIGEST)
    assert list((tmp_path / "retained").iterdir()) == []


def test_the_store_refuses_a_shared_directory(tmp_path: Path) -> None:
    root = tmp_path / "retained"
    root.mkdir(mode=0o755)
    os.chmod(root, 0o755)
    with pytest.raises(ValueError):
        RetainedCoverStore(root)
