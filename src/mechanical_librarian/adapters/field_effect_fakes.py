"""Deterministic, credential-free adapters for guarded-application qualification."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import Callable

from mechanical_librarian.application.field_effects import (
    AuthoritySnapshot,
    TargetEffectResult,
    TargetObservation,
)
from mechanical_librarian.domain.field_effect import EffectAuthority, EffectConsent, FieldEffectPlan
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.automatic_decision import AutomaticDecision


class FakeTargetService:
    """A bounded fake target; it deliberately cannot contain service credentials."""

    def __init__(self) -> None:
        self._values: dict[tuple[str, str, str, str], object] = {}
        self._versions: dict[tuple[str, str, str, str], str] = {}
        self._effects: dict[str, TargetEffectResult] = {}
        self.fail_next = False
        self.ambiguous_next: str | None = None
        self.before_apply: Callable[[FieldEffectPlan], None] | None = None

    def seed(self, plan: FieldEffectPlan) -> None:
        key = self._key(plan)
        self._values[key] = plan.pre_value
        self._versions[key] = plan.target_version

    def read(self, plan: FieldEffectPlan) -> TargetObservation:
        key = self._key(plan)
        return TargetObservation(self._values.get(key), self._versions.get(key, "missing"))

    def apply(self, plan: FieldEffectPlan) -> TargetEffectResult:
        prior = self._effects.get(plan.idempotency_key)
        if prior is not None:
            return prior
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("synthetic target failure")
        if self.before_apply is not None:
            callback, self.before_apply = self.before_apply, None
            callback(plan)
        observed = self.read(plan)
        if observed.value != plan.pre_value or observed.version != plan.target_version:
            return TargetEffectResult("conflict")
        if self.ambiguous_next is not None:
            behavior, self.ambiguous_next = self.ambiguous_next, None
            if behavior == "desired":
                self._write(plan, plan.desired_value)
            elif behavior == "third":
                self._write(plan, "synthetic-third-value")
            result = TargetEffectResult("ambiguous")
            if behavior != "pre":
                self._effects[plan.idempotency_key] = result
            return result
        self._write(plan, plan.desired_value)
        result = TargetEffectResult("accepted")
        self._effects[plan.idempotency_key] = result
        return result

    def drift(self, plan: FieldEffectPlan, value: object) -> None:
        self._write(plan, value)

    def _write(self, plan: FieldEffectPlan, value: object) -> None:
        key = self._key(plan)
        self._values[key] = value
        self._versions[key] = hashlib.sha256(repr(value).encode()).hexdigest()[:32]

    @staticmethod
    def _key(plan: FieldEffectPlan) -> tuple[str, str, str, str]:
        return plan.connection, plan.library, plan.work_id, plan.destination


class FakeBackupReadiness:
    ready_value = True
    def ready(self, plan: FieldEffectPlan) -> bool:
        return self.ready_value


class FakeCapabilityObserver:
    def __init__(self, digest: str) -> None:
        self.digest = digest
    def observe(self, plan: FieldEffectPlan) -> str:
        return self.digest


class FakeAuthorityCurrentness:
    def __init__(self) -> None:
        self.revoked: set[str] = set()
        self.automatic_values: dict[str, object] = {}
        self.bindings: dict[str, AuthoritySnapshot] = {}
    def bind(self, decision: HumanDecision | AutomaticDecision, *, connection: str,
             library: str, work_id: str, target_version: str,
             source: str = "fake_service") -> None:
        if type(decision) is HumanDecision and decision.outcome == "accept":
            value = decision.selected_value
        elif type(decision) is AutomaticDecision and decision.outcome == "candidate":
            value = self.automatic_values[decision.decision_id]
        else:
            raise ValueError("decision has no effect value")
        self.bindings[decision.decision_id] = AuthoritySnapshot(
            connection, library, work_id, decision.destination,
            decision.review_case_id, decision.target_fingerprint,
            target_version, value, source,
        )
    def resolve(self, decision: HumanDecision | AutomaticDecision) -> AuthoritySnapshot:
        return self.bindings[decision.decision_id]
    def current(self, authority: EffectAuthority) -> bool:
        return authority.decision_id not in self.revoked


class FakeConsentCurrentness:
    def __init__(self) -> None:
        self.revoked: set[str] = set()
        self.allowed: dict[tuple[str, str, str, str], EffectConsent] = {}
    def allow(self, *, connection: str, library: str, destination: str) -> None:
        consent = EffectConsent.create(
            connection=connection, library=library, destination=destination,
            granted_at=1,
        )
        self.allowed[(connection, library, destination, "apply_field")] = consent
    def resolve(self, *, connection: str, library: str,
                destination: str) -> EffectConsent:
        return self.allowed[(connection, library, destination, "apply_field")]
    def current(self, consent: EffectConsent) -> bool:
        return (consent.consent_id not in self.revoked
                and self.allowed.get((consent.connection, consent.library,
                                      consent.destination, consent.action)) == consent)


class FakeDependencyCurrentness:
    current_value = True
    def current(self, digests: tuple[str, ...]) -> bool:
        return self.current_value


class FakePreclaimHook:
    def __init__(self) -> None:
        self.callback: Callable[[FieldEffectPlan], None] | None = None

    def __call__(self, plan: FieldEffectPlan) -> None:
        if self.callback is not None:
            callback, self.callback = self.callback, None
            callback(plan)


class FakeForwardReevaluation:
    def __init__(self) -> None:
        self.emitted: list[str] = []
        self.fail_next = False

    def emit(self, plan: FieldEffectPlan) -> None:
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("synthetic reevaluation failure")
        if plan.plan_id not in self.emitted:
            self.emitted.append(plan.plan_id)


class PrivateCoverRecoveryStore:
    """Owner-only, bounded raw bytes kept outside persisted/loggable documents."""

    def __init__(self, root: Path, reader, maximum_bytes: int = 8 * 1024 * 1024) -> None:
        self._root = root
        self._reader = reader
        self._maximum_bytes = maximum_bytes
        self.fail_remove_next = False
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(root, 0o700)

    def capture(self, plan: FieldEffectPlan) -> None:
        path = self._path(plan.plan_id)
        temporary = self._temporary_path(plan.plan_id)
        self._clean_incomplete(temporary)
        try:
            information = path.lstat()
        except FileNotFoundError:
            information = None
        if information is not None:
            if (not stat.S_ISREG(information.st_mode)
                    or stat.S_IMODE(information.st_mode) != 0o600
                    or information.st_uid != os.geteuid()
                    or information.st_size > self._maximum_bytes):
                if stat.S_ISREG(information.st_mode) and information.st_uid == os.geteuid():
                    path.unlink()
                    self._sync_root()
                raise ValueError("cover recovery unavailable")
            return
        value = self._reader(plan)
        if value is None:
            value = b"ABSENT\n"
        if type(value) is not bytes or len(value) > self._maximum_bytes:
            raise ValueError("cover recovery unavailable")
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0), 0o600
        )
        try:
            remaining = memoryview(value)
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    raise OSError("cover recovery unavailable")
                remaining = remaining[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
        self._sync_root()

    def remove(self, plan_id: str) -> None:
        if self.fail_remove_next:
            self.fail_remove_next = False
            raise OSError("synthetic cleanup failure")
        removed = False
        for path in (self._path(plan_id), self._temporary_path(plan_id)):
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            else:
                removed = True
        if removed:
            self._sync_root()

    def exists(self, plan_id: str) -> bool:
        return self._path(plan_id).exists()

    def _path(self, plan_id: str) -> Path:
        digest = plan_id.removeprefix("effect_plan_")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("invalid plan identity")
        return self._root / (digest + ".recovery")

    def _temporary_path(self, plan_id: str) -> Path:
        return self._path(plan_id).with_suffix(".recovery.tmp")

    def _clean_incomplete(self, path: Path) -> None:
        try:
            information = path.lstat()
        except FileNotFoundError:
            return
        if (not stat.S_ISREG(information.st_mode)
                or information.st_uid != os.geteuid()):
            raise ValueError("cover recovery unavailable")
        path.unlink()
        self._sync_root()

    def _sync_root(self) -> None:
        descriptor = os.open(self._root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
