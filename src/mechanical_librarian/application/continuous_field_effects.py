"""Continuous, fair activation around the guarded field-effect kernel."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Callable

from mechanical_librarian.application.field_effects import (
    AuthorityCurrentness,
    CapabilityObserver,
    GuardedApplicationError,
    GuardedFieldApplication,
)
from mechanical_librarian.application.operations import StageTimer
from mechanical_librarian.domain.automatic_decision import AutomaticDecision
from mechanical_librarian.domain.field_effect import (
    FieldEffectError,
    FieldEffectOperation,
    SUPPORTED_DESTINATIONS,
    project_decision_value,
)
from mechanical_librarian.domain.human_decision import HumanDecision


@dataclass(frozen=True, slots=True)
class _CapabilityProbe:
    work_id: str
    destination: str


class ContinuousGuardedApplication:
    """Compose persisted decisions into one instance-scoped mutation slot."""

    def __init__(
        self,
        repository,
        applications: dict[tuple[str, str], GuardedFieldApplication],
        capabilities: dict[tuple[str, str], CapabilityObserver],
        authority: AuthorityCurrentness,
        configured_libraries: tuple[tuple[str, str], ...],
        clock: Callable[[], int],
        request_scan: Callable[[str, str], object],
        *,
        polling_seconds: float = 2.0,
    ) -> None:
        if (not applications or set(applications) != set(configured_libraries)
                or set(capabilities) != set(configured_libraries)
                or not 0.1 <= polling_seconds <= 60.0):
            raise GuardedApplicationError("configuration")
        connections = {connection for connection, _ in configured_libraries}
        if len(connections) != 1:
            raise GuardedApplicationError("configuration")
        self._repository = repository
        self._applications = dict(applications)
        self._capabilities = dict(capabilities)
        self._authority = authority
        self._libraries = configured_libraries
        self._clock = clock
        self._request_scan = request_scan
        self._polling_seconds = polling_seconds
        self._slot = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_library: str | None = None
        self._last_failure: str | None = None
        self._schedule_cursor: tuple[int, str] | None = None
        self._capability_cache: dict[str, tuple[int, dict[str, object]]] = {}
        self._capability_lock = threading.Lock()
        self._serialize: Callable[[Callable[[], object]], object] = lambda call: call()

    def attach_serialization(
        self, serialize: Callable[[Callable[[], object]], object]
    ) -> None:
        self._serialize = serialize

    def attach_timing(self, timing: StageTimer) -> None:
        for application in self._applications.values():
            application.attach_timing(timing)

    def start(self) -> tuple[int, int, int]:
        self._stop.clear()
        for application in self._applications.values():
            application.resume_admission()
        recovered = actions = cleanups = 0
        seen_plans: set[str] = set()
        while True:
            plan_ids = self._repository.pending_field_effect_plan_ids(100)
            if not plan_ids:
                break
            if any(plan_id in seen_plans for plan_id in plan_ids):
                raise GuardedApplicationError("recovery")
            for plan_id in plan_ids:
                seen_plans.add(plan_id)
                self.apply(plan_id)
                recovered += 1
        seen_actions: set[str] = set()
        while True:
            plan_ids = self._repository.pending_field_effect_action_plan_ids(100)
            if not plan_ids:
                break
            if any(plan_id in seen_actions for plan_id in plan_ids):
                raise GuardedApplicationError("recovery")
            for plan_id in plan_ids:
                seen_actions.add(plan_id)
                self.reconcile(plan_id)
                actions += 1
        first = next(iter(self._applications.values()))
        while True:
            completed = first.run_pending_cleanups(limit=100)
            cleanups += completed
            if completed < 100:
                break
        self._thread = threading.Thread(
            target=self._run, name="guarded-field-application", daemon=True
        )
        self._thread.start()
        self._wake.set()
        return recovered, actions, cleanups

    def begin_drain(self) -> None:
        self._stop.set()
        self._wake.set()
        for application in self._applications.values():
            application.begin_drain()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5.0)
        self._thread = None

    def wake(self) -> None:
        self._wake.set()

    def enable(self, libraries: tuple[str, ...]) -> dict[str, object]:
        configured = tuple(library for _, library in self._libraries)
        if (type(libraries) is not tuple or not libraries
                or len(set(libraries)) != len(libraries)
                or any(library not in configured for library in libraries)):
            raise GuardedApplicationError("consent")
        if not self._repository.field_effect_backup_ready():
            raise GuardedApplicationError("backup")
        documents = self._repository.pending_field_effect_decision_documents(100)
        if not documents:
            raise GuardedApplicationError("not_ready")
        readiness = self._capability_readiness(libraries, force=True)
        if any(item["state"] != "allowed" for item in readiness):
            raise GuardedApplicationError("capability")
        connection = self._libraries[0][0]
        destinations = tuple(sorted(SUPPORTED_DESTINATIONS))
        self._repository.grant_field_effect_consents(
            connection, libraries, destinations, self._clock()
        )
        self.wake()
        return self.activity()

    def revoke(self, connection: str, library: str) -> dict[str, object]:
        if (connection, library) not in self._applications:
            raise GuardedApplicationError("consent")
        self._repository.revoke_library_field_effect_consents(
            connection, library, self._clock()
        )
        self.wake()
        return self.activity()

    def set_paused(self, paused: bool) -> None:
        next(iter(self._applications.values())).set_paused(paused)
        if not paused:
            self.wake()

    def activate_human_override(
        self, review_case_id: str, destination: str,
    ) -> None:
        self._repository.field_effect_manual_override(
            review_case_id, destination, self._clock()
        )
        self.wake()

    def return_to_automatic(
        self, review_case_id: str, destination: str,
    ) -> None:
        context = self._repository.clear_field_effect_manual_hold(
            review_case_id, destination, self._clock()
        )
        if context is None:
            raise GuardedApplicationError("manual_hold")
        self._request_scan(*context)

    def status(self, plan_id: str) -> FieldEffectOperation:
        return self._repository.get_field_effect(plan_id)

    def apply(self, plan_id: str) -> FieldEffectOperation:
        operation = self._repository.get_field_effect(plan_id)
        application = self._application(operation)
        with self._slot:
            return application.apply(plan_id)

    def reconcile(self, plan_id: str) -> FieldEffectOperation:
        operation = self._repository.get_field_effect(plan_id)
        application = self._application(operation)
        with self._slot:
            return application.reconcile(plan_id)

    def schedule(self, limit: int = 100) -> int:
        documents = self._repository.pending_field_effect_decision_documents(
            limit, self._schedule_cursor
        )
        if not documents and self._schedule_cursor is not None:
            self._schedule_cursor = None
            documents = self._repository.pending_field_effect_decision_documents(limit)
        planned = 0
        for document in documents:
            decision = self._decision(document)
            self._schedule_cursor = (decision.occurred_at, decision.decision_id)
            snapshot = self._authority.resolve(decision)
            key = (snapshot.connection, snapshot.library)
            application = self._applications.get(key)
            capability = self._capabilities.get(key)
            if application is None or capability is None:
                continue
            try:
                consent = self._repository.current_field_effect_consent(
                    snapshot.connection, snapshot.library, snapshot.destination
                )
            except FieldEffectError as error:
                if error.code == "consent":
                    continue
                raise
            del consent
            observed = capability.observe(  # type: ignore[arg-type]
                _CapabilityProbe(snapshot.work_id, snapshot.destination)
            )
            if observed.category != "allowed":
                scope = (
                    "connection"
                    if observed.category == "connection_unavailable"
                    else "destination"
                )
                self._repository.suspend_field_effect_capability(
                    scope=scope, connection=snapshot.connection,
                    destination=snapshot.destination, reason=observed.category,
                    changed_at=self._clock(),
                )
                continue
            operation = application.plan(
                decision,
                connection=snapshot.connection,
                library=snapshot.library,
                work_id=snapshot.work_id,
                destination=snapshot.destination,
                pre_value=snapshot.pre_value,
                desired_value=snapshot.decision_value,
                target_fingerprint=snapshot.target_fingerprint,
                target_version=snapshot.target_version,
                dependency_digests=(
                    snapshot.dependency_digests
                    or (snapshot.target_fingerprint,)
                ),
                capability_digest=observed.digest,
            )
            if operation is not None:
                planned += 1
        return planned

    def run_cycle(self) -> bool:
        self.schedule(100)
        selected = self._repository.next_field_effect_plan_id(self._last_library)
        if selected is None:
            return False
        plan_id, library = selected
        self.apply(plan_id)
        self._last_library = library
        return True

    def activity(self) -> dict[str, object]:
        data = self._repository.field_effect_activity_data(50)
        documents = self._repository.pending_field_effect_decision_documents(100)
        automatic = human = 0
        example: dict[str, object] | None = None
        for document in documents:
            decision = self._decision(document)
            if type(decision) is HumanDecision:
                human += 1
            else:
                automatic += 1
            if example is None:
                try:
                    snapshot = self._authority.resolve(decision)
                    example = {
                        "after": project_decision_value(
                            snapshot.destination, snapshot.decision_value
                        ),
                        "before": snapshot.pre_value,
                        "destination": snapshot.destination,
                        "library": snapshot.library,
                        "work_id": snapshot.work_id,
                    }
                except (FieldEffectError, GuardedApplicationError, ValueError):
                    pass
        enabled = []
        for connection, library in self._libraries:
            if all(self._consented(connection, library, destination)
                   for destination in SUPPORTED_DESTINATIONS):
                enabled.append({"connection": connection, "library": library})
        capabilities = self._capability_readiness(
            tuple(library for _, library in self._libraries), force=False
        )
        return {
            **data,
            "backup_ready": self._repository.field_effect_backup_ready(),
            "configured_libraries": [
                {"connection": connection, "library": library}
                for connection, library in self._libraries
            ],
            "capabilities": capabilities,
            "enabled_libraries": enabled,
            "example": example,
            "last_failure": self._last_failure,
            "prepared": {
                "automatic": automatic,
                "human": human,
                "total": automatic + human,
            },
            "schema_version": "continuous-field-activity-v1",
        }

    def _capability_readiness(
        self, libraries: tuple[str, ...], *, force: bool,
    ) -> list[dict[str, object]]:
        """Project least-authority readiness before consent can be granted."""
        now = self._clock()
        representatives: dict[str, str] = {}
        cursor: tuple[int, str] | None = None
        while len(representatives) < len(libraries):
            documents = self._repository.pending_field_effect_decision_documents(100, cursor)
            if not documents:
                break
            for document in documents:
                decision = self._decision(document)
                cursor = (decision.occurred_at, decision.decision_id)
                try:
                    snapshot = self._authority.resolve(decision)
                except (FieldEffectError, GuardedApplicationError, ValueError):
                    continue
                if snapshot.library in libraries:
                    representatives.setdefault(snapshot.library, snapshot.work_id)
        results: list[dict[str, object]] = []
        for connection, library in self._libraries:
            if library not in libraries:
                continue
            with self._capability_lock:
                cached = self._capability_cache.get(library)
            if not force and cached is not None and now - cached[0] <= 30:
                results.append(dict(cached[1]))
                continue
            work_id = representatives.get(library)
            state = "not_ready"
            if work_id is not None:
                state = "allowed"
                observer = self._capabilities[(connection, library)]
                for destination in sorted(SUPPORTED_DESTINATIONS):
                    observed = observer.observe(_CapabilityProbe(work_id, destination))  # type: ignore[arg-type]
                    if observed.category != "allowed":
                        state = observed.category
                        break
            result = {"connection": connection, "library": library, "state": state}
            with self._capability_lock:
                self._capability_cache[library] = (now, result)
            results.append(dict(result))
        return results

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self._polling_seconds)
            self._wake.clear()
            try:
                self._serialize(self._run_until_idle)
                self._last_failure = None
            except (FieldEffectError, GuardedApplicationError, ValueError) as error:
                self._last_failure = getattr(error, "code", "unavailable")

    def _run_until_idle(self) -> None:
        while not self._stop.is_set() and self.run_cycle():
            pass

    def _application(self, operation: FieldEffectOperation) -> GuardedFieldApplication:
        application = self._applications.get(
            (operation.plan.connection, operation.plan.library)
        )
        if application is None:
            raise GuardedApplicationError("configuration")
        return application

    def _consented(self, connection: str, library: str, destination: str) -> bool:
        try:
            self._repository.current_field_effect_consent(
                connection, library, destination
            )
            return True
        except FieldEffectError as error:
            if error.code == "consent":
                return False
            raise

    @staticmethod
    def _decision(document: str) -> HumanDecision | AutomaticDecision:
        raw = json.loads(document)
        if type(raw) is not dict:
            raise FieldEffectError("authority_binding")
        if raw.get("schema_version") == "human-decision-v1":
            return HumanDecision.from_json(document)
        if raw.get("schema_version") == "automatic-decision-v1":
            return AutomaticDecision.from_json(document)
        raise FieldEffectError("authority_binding")
